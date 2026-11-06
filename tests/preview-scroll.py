#!/usr/bin/env python3
"""Exercise preview scrolling and input compatibility on an explicitly isolated X11 display."""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

TIMEOUT = 5
HELPER = r'''#!{python}
import json, os, sys
label = sys.stdin.buffer.readline().decode("utf-8", "replace").rstrip("\n")
with open(os.environ["PREVIEW_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(label) + "\n")
if label == "empty":
    data = b""
elif label == "wrapped":
    data = ("WRAP-" + "abcdefghij" * 12 + "\n\nblank-after-wrap\nlast\n").encode()
elif label == "trailing":
    data = b"tail-one\n\ntail-three\n"
elif label == "large":
    data = b"".join((f"ROW-{i:04d}-" + chr(65 + i % 26) * 38 + "\n").encode() for i in range(1365))
elif label in ("alpha", "alpine", "beta", "gamma"):
    data = (f"{label.upper()} PREVIEW\nSelected label: {label}\n").encode()
else:
    data = b"".join((f"ROW-{i:04d}-" + chr(65 + i % 26) * 18 + "\n").encode() for i in range(37))
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
with open(os.environ["PREVIEW_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps("done:" + label) + "\n")
'''


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, timeout=TIMEOUT):
    try:
        return subprocess.run(args, timeout=timeout, text=True, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out after {timeout}s: {' '.join(map(str, args))}") from exc


class Menu:
    def __init__(self, binary, args, data, env, output):
        self.out = tempfile.TemporaryFile()
        self.err = tempfile.TemporaryFile()
        self.source = tempfile.TemporaryFile()
        self.source.write(data)
        self.source.seek(0)
        self.command = [str(binary), *args]
        self.proc = subprocess.Popen(self.command, stdin=self.source, stdout=self.out,
                                     stderr=self.err, env=env, start_new_session=True)
        self.window = None
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            found = run(["xdotool", "search", "--onlyvisible", "--class", "dmenu"], 2)
            if found.returncode == 0 and found.stdout.split():
                self.window = found.stdout.split()[-1]
                return
            if self.proc.poll() is not None:
                error = self.stderr()
                self.close(output, "map-failure")
                raise RuntimeError("menu exited before mapping: " + error)
            time.sleep(.03)
        self.close(output, "map-timeout")
        raise RuntimeError("menu did not map")

    def key(self, *keys):
        result = run(["xdotool", "key", "--clearmodifiers", *keys], 2)
        require(result.returncode == 0, "key injection failed: " + result.stderr)
        time.sleep(.09)

    def type(self, text):
        result = run(["xdotool", "type", "--clearmodifiers", "--delay", "1", text], 3)
        require(result.returncode == 0, "typing failed: " + result.stderr)
        time.sleep(.09)

    def screenshot(self, path):
        result = run(["import", "-window", self.window, str(path)], 3)
        require(result.returncode == 0, "screenshot failed: " + result.stderr)

    def geometry(self):
        info = run(["xwininfo", "-id", self.window])
        require(info.returncode == 0, "xwininfo failed: " + info.stderr)
        return {name: int(re.search(rf"^\s*{name}:\s+(\d+)", info.stdout, re.M)[1])
                for name in ("Width", "Height")}

    def stderr(self):
        self.err.seek(0)
        return self.err.read().decode(errors="replace")

    def stdout(self):
        self.out.seek(0)
        return self.out.read()

    def close(self, output, name):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=2)
        error = self.stderr()
        (output / f"{name}-{self.proc.pid}.stderr").write_text(self.stderr(), encoding="utf-8")
        for stream in (self.out, self.err, self.source):
            stream.close()
        require(not re.search(r"AddressSanitizer|LeakSanitizer|runtime error:", error),
                f"sanitizer diagnostic from {self.proc.pid}: {error}")


def image_signature(path, crop):
    x, y, width, height = crop
    result = run(["convert", str(path), "-crop", f"{width}x{height}+{x}+{y}", "+repage",
                  "-format", "%[fx:standard_deviation] %#", "info:"])
    require(result.returncode == 0, "pixel comparison failed: " + result.stderr)
    deviation, digest = result.stdout.split()
    return float(deviation), digest


def log_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def helper_starts(path):
    return [entry for entry in log_records(path) if not entry.startswith("done:")]


def wait_helper_done(path, label, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "done:" + label in log_records(path):
            return
        time.sleep(.02)
    raise RuntimeError(f"synthetic helper did not finish {label!r}")


def wait_latest_helper(path, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        starts = helper_starts(path)
        if starts and "done:" + starts[-1] in log_records(path):
            return starts[-1]
        time.sleep(.02)
    raise RuntimeError("synthetic preview helper did not finish the latest selection")


def capture(menu, path, pane):
    menu.screenshot(path)
    deviation, signature = image_signature(path, pane)
    return deviation, signature


def wait_preview(menu, path, pane, expected):
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        deviation, digest = capture(menu, path, pane)
        if deviation > 0 and digest == expected:
            return
        time.sleep(.02)
    raise RuntimeError(f"rendered preview did not match expected pixels: {path}")


def row_signatures(path, crop, count):
    x, y, width, height = crop
    row_height = height // count
    return [image_signature(path, (x, y + i * row_height, width, row_height))[1]
            for i in range(count)]


def row_deviations(path, crop, count):
    x, y, width, height = crop
    row_height = height // count
    return [image_signature(path, (x, y + i * row_height, width, row_height))[0]
            for i in range(count)]


def wait_ready(menu, output, lines=6):
    geometry = menu.geometry()
    row = geometry["Height"] // (lines + 1)
    footer = (geometry["Width"] * 2 // 5 + 2, row * lines,
              geometry["Width"] * 3 // 5 - 3, row)
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        path = output / "ready.png"
        menu.screenshot(path)
        if image_signature(path, footer)[0] == 0:
            return
        time.sleep(.02)
    raise RuntimeError("preview layout did not reach Ready")


def require_one_row_shift(before, after, pane, count):
    old_rows = row_signatures(before, pane, count)
    new_rows = row_signatures(after, pane, count)
    require(old_rows[1:] == new_rows[:-1],
            "Alt-Down did not advance exactly one prepared visual row")


def scroll_cases(binary, helper, output, env):
    labels = b"scroll\nempty\nwrapped\ntrailing\nlarge\n"
    menu = Menu(binary, ["-F", "-l", "6", "-fn", "monospace:size=12", "-P", str(helper)], labels, env, output)
    try:
        geometry = menu.geometry()
        row = geometry["Height"] // 7
        pane = (geometry["Width"] * 2 // 5 + 2, row, geometry["Width"] * 3 // 5 - 3, row * 5)
        wait_helper_done(Path(env["PREVIEW_LOG"]), "scroll")
        wait_ready(menu, output)
        initial = output / "scroll-initial.png"
        require(capture(menu, initial, pane)[0] > 0, "initial prepared rows are blank")
        initial_count = len(helper_starts(Path(env["PREVIEW_LOG"])))
        menu.key("alt+Down")
        down = output / "scroll-down.png"
        down_sig = capture(menu, down, pane)[1]
        require_one_row_shift(initial, down, pane, 5)
        menu.key("alt+Up")
        require(capture(menu, output / "scroll-up-restored.png", pane)[1] == image_signature(initial, pane)[1],
                "Alt-Up did not restore the initial viewport")
        menu.key("alt+Next")
        page = output / "scroll-page.png"
        page_sig = capture(menu, page, pane)[1]
        menu.key("alt+Prior")
        require(capture(menu, output / "scroll-page-up.png", pane)[1] ==
                image_signature(initial, pane)[1], "Alt-Prior did not restore one viewport")
        menu.key("alt+Home")
        menu.key(*(["alt+Down"] * 5))
        page_lines = output / "scroll-page-lines.png"
        menu.screenshot(page_lines)
        require(image_signature(page_lines, pane)[1] == page_sig,
                "Alt-Next did not advance exactly one preview capacity")
        menu.key("alt+Home")
        menu.key(*(["alt+Next"] * 7))
        partial_page = output / "scroll-final-partial-page.png"
        menu.screenshot(partial_page)
        menu.key("alt+End")
        final_page = output / "scroll-final-end.png"
        menu.screenshot(final_page)
        require(image_signature(partial_page, pane)[1] == image_signature(final_page, pane)[1],
                "page-down did not clamp its final partial page to the lower bound")
        menu.key("alt+Home", "alt+Next", "alt+End")
        end = output / "scroll-end.png"
        end_sig = capture(menu, end, pane)[1]
        menu.key("alt+Down")
        require(capture(menu, output / "scroll-end-bound.png", pane)[1] == end_sig,
                "Alt-End did not clamp to the lower bound")
        menu.key("alt+Home")
        home_sig = capture(menu, output / "scroll-home.png", pane)[1]
        menu.key("alt+Up")
        require(capture(menu, output / "scroll-home-bound.png", pane)[1] == home_sig,
                "Alt-Home did not clamp to the upper bound")
        require(down_sig != image_signature(initial, pane)[1], "Alt-Down did not change preview pixels")
        count = len(helper_starts(Path(env["PREVIEW_LOG"])))
        menu.key("alt+Down")
        scrolled_redraw = output / "scroll-before-cursor.png"
        scrolled_signature = capture(menu, scrolled_redraw, pane)[1]
        count_after_scroll = len(helper_starts(Path(env["PREVIEW_LOG"])))
        menu.key("Left")
        cursor_redraw = output / "scroll-after-cursor.png"
        cursor_signature = capture(menu, cursor_redraw, pane)[1]
        count_after_cursor = len(helper_starts(Path(env["PREVIEW_LOG"])))
        require(cursor_signature == scrolled_signature,
                "cursor-only redraw reset the preview scroll offset")
        require(count_after_scroll == count_after_cursor == count == initial_count,
                "scroll/cursor-only redraw restarted the helper")
        require(menu.stdout() == b"", "scrolling emitted stdout")
        menu.key("Down")
        wait_helper_done(Path(env["PREVIEW_LOG"]), "empty")
        after_nav = capture(menu, output / "scroll-selection-reset.png", pane)[1]
        require(after_nav != home_sig, "new selection did not display its initial preview")
        menu.key("Up")
        wait_ready(menu, output)
        require(capture(menu, output / "scroll-new-result-reset.png", pane)[1] == home_sig,
                "returning to an item retained its prior scroll on the new result")
        menu.key("Down")
        menu.key("Return")
        require(menu.proc.wait(timeout=TIMEOUT) == 0 and menu.stdout() == b"empty\n",
                "Enter after navigation changed selected-label output")
    finally:
        menu.close(output, "scroll")

    for label in ("empty", "wrapped", "trailing"):
        fixture_env = {**env, "PREVIEW_LOG": str(output / f"{label}.jsonl")}
        fixture = Menu(binary, ["-F", "-l", "6", "-fn", "monospace:size=12", "-P", str(helper)],
                       f"{label}\n".encode(), fixture_env, output)
        try:
            wait_helper_done(Path(fixture_env["PREVIEW_LOG"]), label)
            if label != "empty":
                wait_ready(fixture, output)
            geo = fixture.geometry()
            r = geo["Height"] // 7
            fixture_pane = (geo["Width"] * 2 // 5 + 2, r, geo["Width"] * 3 // 5 - 3, r * 5)
            shot = output / f"scroll-{label}.png"
            deviation, before = capture(fixture, shot, fixture_pane)
            require((deviation == 0) if label == "empty" else (deviation > 0),
                    f"unexpected {label} preview pixels: deviation={deviation}")
            if label in ("wrapped", "trailing"):
                rows = row_deviations(shot, fixture_pane, 5)
                require(any(value == 0 for value in rows) and any(value > 0 for value in rows),
                        f"{label} preview did not preserve both blank and text rows: {rows}")
            fixture.key("alt+End", "alt+Down", "alt+Home", "alt+Up")
            after_bounds = output / f"scroll-{label}-bounds.png"
            fixture.screenshot(after_bounds)
            require(image_signature(after_bounds, fixture_pane)[1] == before,
                    f"scroll bounds changed {label} content after returning Home")
            if label == "wrapped":
                fixture.key("alt+Down")
                after = output / "scroll-wrapped-down.png"
                fixture.screenshot(after)
                require(image_signature(after, fixture_pane)[1] != before,
                        "wrapped preview did not move by a visual row")
        finally:
            fixture.close(output, f"scroll-{label}")

    large_env = {**env, "PREVIEW_LOG": str(output / "large.jsonl")}
    large = Menu(binary, ["-F", "-l", "6", "-fn", "monospace:size=12", "-P", str(helper)],
                 b"large\n", large_env, output)
    try:
        wait_helper_done(Path(large_env["PREVIEW_LOG"]), "large")
        wait_ready(large, output)
        require(len(helper_starts(Path(large_env["PREVIEW_LOG"]))) == 1,
                "large preview unexpectedly restarted")
        geometry = large.geometry()
        row = geometry["Height"] // 7
        pane = (geometry["Width"] * 2 // 5 + 2, row,
                geometry["Width"] * 3 // 5 - 3, row * 5)
        initial = capture(large, output / "scroll-large-initial.png", pane)[1]
        # Independent line-scroll oracle for the expected five-row page movement.
        large.key(*(["alt+Down"] * 5))
        expected = capture(large, output / "scroll-large-expected.png", pane)[1]
        require(expected != initial, "large preview did not move after five visual rows")
        large.key("alt+Home")
        wait_preview(large, output / "scroll-large-restored.png", pane, initial)
        start = time.monotonic()
        result = run(["xdotool", "key", "--clearmodifiers", "alt+Next"], 2)
        require(result.returncode == 0, "large-preview key injection failed: " + result.stderr)
        wait_preview(large, output / "scroll-large.png", pane, expected)
        elapsed = time.monotonic() - start
        require(elapsed < 1.0, f"visual page-scroll observation was slow: {elapsed:.3f}s")
        (output / "large-scroll-measurement.json").write_text(json.dumps({
            "elapsed_seconds": elapsed, "bound_seconds": 1.0,
            "includes": "key driver, screenshot capture and pixel comparison",
            "initial_digest": initial, "expected_digest": expected,
        }, indent=2) + "\n", encoding="utf-8")
        large.key("Escape")
        require(large.proc.wait(timeout=TIMEOUT) == 1 and large.stdout() == b"",
                "Escape after large-preview scrolling changed output/status")
        print(f"PASS prepared 65520-byte visual page scroll: {elapsed:.3f}s "
              "(includes key driver, screenshot and pixel comparison; bound 1.0s)")
    finally:
        large.close(output, "scroll-large")



def no_preview_comparison(binary, baseline, output):
    images = []
    results = []
    for executable, index in ((baseline, 0), (binary, 1)):
        menu = Menu(executable, ["-l", "5"], b"alpha\nalpine\nbeta\ngamma\n", os.environ.copy(), output)
        try:
            menu.key("Down")
            menu.screenshot(output / f"no-preview-{index}.png")
            images.append(output / f"no-preview-{index}.png")
            menu.key("Return")
            require(menu.proc.wait(timeout=TIMEOUT) == 0, "no-preview comparison exited abnormally")
            results.append(menu.stdout())
        finally:
            menu.close(output, f"no-preview-{index}")
    require(results == [b"alpine\n", b"alpine\n"], f"no-preview stdout changed: {results}")
    difference = run(["convert", *map(str, images), "-compose", "difference",
                      "-composite", "-format", "%[fx:maxima]", "info:"])
    require(difference.returncode == 0 and float(difference.stdout) == 0,
            "no-preview baseline/current screenshots differ")


def compatibility_references(binary, helper, output, env):
    references = {}
    for label in ("alpha", "alpine", "beta", "gamma"):
        reference_env = {**env, "PREVIEW_LOG": str(output / f"reference-{label}.jsonl")}
        menu = Menu(binary, ["-l", "2", "-P", str(helper)],
                    (label + "\n").encode(), reference_env, output)
        try:
            wait_helper_done(Path(reference_env["PREVIEW_LOG"]), label)
            wait_ready(menu, output, lines=2)
            geometry = menu.geometry()
            row = geometry["Height"] // 3
            pane = (geometry["Width"] * 2 // 5 + 2, row,
                    geometry["Width"] * 3 // 5 - 3, row * 2)
            deviation, digest = capture(menu, output / f"reference-{label}.png", pane)
            require(deviation > 0, f"reference preview was never rendered: {label}")
            references[label] = digest
        finally:
            menu.close(output, f"reference-{label}")
    require(len(set(references.values())) == 4,
            "label-specific reference previews are not visually distinguishable")
    (output / "reference-digests.json").write_text(
        json.dumps(references, indent=2) + "\n", encoding="utf-8")
    return pane, references

def compatibility(binary, baseline, helper, output, env):
    items = b"alpha\nalpine\nbeta\ngamma\n"
    pane, references = compatibility_references(binary, helper, output, env)
    matrix = [
        (opts, keys) for opts in ([], ["-F"], ["-i"], ["-F", "-i"])
        for keys in ([], ["Up"], ["Down"], ["ctrl+n"], ["ctrl+p"], ["Next"], ["Prior"], ["Home"], ["End"],
                     ["b"], ["b", "BackSpace"], ["End", "BackSpace"], ["Down", "Tab"],
                     ["ctrl+alt+Down"], ["alt+l"], ["Down", "alt+h"],
                     ["alt+j"], ["End", "alt+k"], ["End", "alt+g"], ["alt+G"],
                     ["al", "ctrl+y"])
    ]
    clipboard = subprocess.Popen(["xclip", "-quiet", "-selection", "primary", "-i"],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, start_new_session=True)
    try:
        clipboard.stdin.write(b"pha\n")
        clipboard.stdin.close()
        clipboard.stdin = None
        time.sleep(.05)
        require(clipboard.poll() is None, "synthetic clipboard owner exited before paste tests")
        for index, (opts, keys) in enumerate(matrix):
            outputs = []
            selected = []
            case_env = {**env, "PREVIEW_LOG": str(output / f"matrix-{index}.jsonl")}
            for executable, preview in ((baseline, False), (binary, True)):
                args = [*opts, "-l", "2"]
                if preview:
                    args += ["-P", str(helper)]
                menu = Menu(executable, args, items, case_env if preview else os.environ.copy(), output)
                try:
                    for key in keys:
                        if key in ("al", "b"):
                            menu.type(key)
                        else:
                            menu.key(key)
                    if preview:
                        expected_label = outputs[0].decode("utf-8").rstrip("\n")
                        wait_helper_done(Path(case_env["PREVIEW_LOG"]), expected_label)
                        wait_ready(menu, output, lines=2)
                        wait_preview(menu, output / f"matrix-{index}-preview.png", pane,
                                     references[expected_label])
                        selected.append(wait_latest_helper(Path(case_env["PREVIEW_LOG"])))
                    menu.key("Return")
                    require(menu.proc.wait(timeout=TIMEOUT) == 0,
                            f"compatibility case exited abnormally: {opts} {keys}")
                    outputs.append(menu.stdout())
                finally:
                    menu.close(output, f"matrix-{index}-{int(preview)}")
            require(outputs[0] == outputs[1],
                    f"baseline/current stdout differs for {opts} {keys}: {outputs}")
            require(selected and selected[0] == outputs[1].decode("utf-8").rstrip("\n"),
                    f"preview identity does not match selected output for {opts} {keys}: {selected}, {outputs}")
    finally:
        if clipboard.poll() is None:
            clipboard.terminate()
        _, clipboard_stderr = clipboard.communicate(timeout=2)
        (output / "synthetic-selection.stderr").write_bytes(clipboard_stderr or b"")
    print(f"PASS {len(matrix)} baseline/current cases with rendered label-specific preview identity")

    menu = Menu(binary, ["-F", "-l", "5", "-P", str(helper)], items, env, output)
    try:
        geo = menu.geometry()
        row = geo["Height"] // 6
        pane = (geo["Width"] * 2 // 5 + 2, row, geo["Width"] * 3 // 5 - 3, row * 4)
        menu.type("z")
        empty = output / "no-match-clear.png"
        menu.screenshot(empty)
        require(image_signature(empty, pane)[0] == 0, "no-match filtering did not clear preview pixels")
        menu.key("Return")
        require(menu.proc.wait(timeout=TIMEOUT) == 0 and menu.stdout() == b"z\n",
                "no-match Enter output changed")
    finally:
        menu.close(output, "no-match")

    for key, expected, status in (("Return", b"alpha\n", 0), ("Escape", b"", 1)):
        menu = Menu(binary, ["-F", "-l", "5", "-P", str(helper)], items, env, output)
        try:
            menu.key(key)
            require(menu.proc.wait(timeout=TIMEOUT) == status and menu.stdout() == expected,
                    f"{key} output/status changed")
        finally:
            menu.close(output, f"exit-{key.lower()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--isolated-display", action="store_true")
    args = parser.parse_args()
    require(args.isolated_display and os.environ.get("DISPLAY"),
            "requires --isolated-display and DISPLAY set by the parent-owned Xvfb")
    for name, path in (("binary", args.binary), ("baseline", args.baseline)):
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                f"--{name} must be an executable absolute path")
    require(args.output.is_absolute(), "--output must be an absolute path")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not any(args.output.iterdir()), "--output must be an empty directory")
    args.output = args.output.resolve()
    for command in ("xdotool", "xwininfo", "import", "convert", "xclip"):
        require(shutil.which(command), f"missing required tool: {command}")
    helper = args.output / "synthetic-preview.py"
    helper.write_text(HELPER.replace("{python}", os.sys.executable), encoding="utf-8")
    helper.chmod(0o700)
    env = {**os.environ, "LC_ALL": "C.UTF-8", "PREVIEW_LOG": str(args.output / "helper.jsonl")}
    try:
        no_preview_comparison(args.binary, args.baseline, args.output)
        scroll_cases(args.binary, helper, args.output, env)
        compatibility(args.binary, args.baseline, helper, args.output, env)
        print(f"PASS preview scrolling regression; screenshots and per-menu stderr: {args.output}")
    except BaseException:
        print(f"FAIL preview scrolling regression; inspect screenshots and stderr: {args.output}")
        raise


if __name__ == "__main__":
    main()
