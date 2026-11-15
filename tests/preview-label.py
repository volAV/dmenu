#!/usr/bin/env python3
"""Run focused preview-label pixel and menu regressions on an owned Xvfb."""
import argparse
import importlib.util
import os
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, timeout=5, env=None):
    try:
        return subprocess.run(args, timeout=timeout, text=True, capture_output=True, env=env)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"timed out after {timeout}s: {' '.join(map(str, args))}") from exc


def wait_log(path, expected):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_bytes() == expected:
            return
        time.sleep(.03)
    raise RuntimeError("synthetic preview did not receive the complete label")


def screenshot_match(first, second):
    result = run(["convert", str(first), str(second), "-compose", "difference",
                  "-composite", "-format", "%[fx:maxima]", "info:"])
    require(result.returncode == 0 and float(result.stdout) == 0,
            f"no-preview screenshot mismatch: {first.name} vs {second.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xvfb", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    for name, path in (("xvfb", args.xvfb), ("binary", args.binary),
                       ("baseline", args.baseline), ("probe", args.probe)):
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                f"--{name} must be an executable absolute path")
    require(args.output.is_absolute(), "--output must be an absolute path")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not any(args.output.iterdir()), "--output must be an empty directory")
    args.output = args.output.resolve()
    for command in ("xdotool", "xwininfo", "import", "convert"):
        require(shutil.which(command), f"missing required tool: {command}")

    xvfb_err = (args.output / "xvfb.stderr").open("wb")
    xvfb = subprocess.Popen([str(args.xvfb), "-displayfd", "1", "-screen", "0",
                             "1024x768x24", "-nolisten", "tcp", "-noreset"],
                            stdout=subprocess.PIPE, stderr=xvfb_err, start_new_session=True)
    menu_module = None
    menus = []
    try:
        ready, _, _ = select.select([xvfb.stdout], [], [], 5)
        require(ready, "Xvfb did not allocate a display")
        display = xvfb.stdout.readline().decode("ascii").strip()
        require(display.isdigit(), "Xvfb returned an invalid display number")
        env = {**os.environ, "DISPLAY": f":{display}", "LC_ALL": "C.UTF-8"}
        # Shared screenshot/key helpers inherit only this owned display.
        os.environ["DISPLAY"] = env["DISPLAY"]

        pixel = run([str(args.probe)], timeout=10, env=env)
        (args.output / "pixel-probe.stdout").write_text(pixel.stdout, encoding="utf-8")
        (args.output / "pixel-probe.stderr").write_text(pixel.stderr, encoding="utf-8")
        require(pixel.returncode == 0, "native pixel oracle failed: " + pixel.stderr)

        spec = importlib.util.spec_from_file_location(
            "preview_scroll", Path(__file__).with_name("preview-scroll.py"))
        menu_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(menu_module)
        helper = args.output / "synthetic-preview.py"
        helper.write_text(
            "#!" + os.sys.executable + "\n"
            "import os, sys\n"
            "label = sys.stdin.buffer.readline()\n"
            "open(os.environ['PREVIEW_LABEL_LOG'], 'wb').write(label)\n"
            "sys.stdout.buffer.write(b'A' * 300 + b'\\nsecond helper line\\nthird line')\n"
            "sys.stdout.buffer.flush()\n", encoding="utf-8")
        helper.chmod(0o700)
        label = b"A long first line with enough content to overflow ordinary label width (3 lines)\n"
        log = args.output / "helper-stdin.bin"
        menu_env = {**env, "PREVIEW_LABEL_LOG": str(log)}
        menu = menu_module.Menu(args.binary, ["-l", "3", "-P", str(helper)],
                                label, menu_env, args.output)
        menus.append(menu)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not log.exists():
            time.sleep(.03)
        wait_log(log, label)
        menu.screenshot(args.output / "preview-long-label.png")
        menu.key("Return")
        require(menu.proc.wait(timeout=5) == 0 and menu.stdout() == label,
                "preview menu changed the complete selected label on stdout")
        menu.close(args.output, "preview-label")
        menus.remove(menu)

        for index, text in enumerate((b"short label\n", b"short (2 lines)\n", label)):
            images = []
            outputs = []
            for executable in (args.baseline, args.binary):
                menu = menu_module.Menu(executable, ["-l", "2"], text, env, args.output)
                menus.append(menu)
                menu.screenshot(args.output / f"no-preview-{index}-{len(images)}.png")
                images.append(args.output / f"no-preview-{index}-{len(images)}.png")
                menu.key("Return")
                require(menu.proc.wait(timeout=5) == 0, "no-preview menu exited abnormally")
                outputs.append(menu.stdout())
                menu.close(args.output, f"no-preview-{index}-{len(images)}")
                menus.remove(menu)
            require(outputs == [text, text], "no-preview selected stdout changed")
            screenshot_match(*images)

        print(f"PASS preview label pixel, identity, and no-preview checks; evidence: {args.output}")
    except BaseException:
        print(f"FAIL preview label regression; inspect evidence: {args.output}")
        raise
    finally:
        for menu in menus:
            menu.close(args.output, f"cleanup-{menu.proc.pid}")
        xvfb.terminate()
        try:
            xvfb.wait(timeout=2)
        except subprocess.TimeoutExpired:
            xvfb.kill()
            xvfb.wait(timeout=2)
        xvfb_err.close()


if __name__ == "__main__":
    main()
