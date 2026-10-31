#!/usr/bin/env python3
"""Check rendered text previews only on an explicitly owned X display."""
import argparse
import ctypes
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True)


HELPER = r'''#!{python}
import os, sys, time
label = sys.stdin.buffer.readline().strip()
with open(os.environ["PREVIEW_LOG"], "a", encoding="ascii") as log:
    log.write(label.decode("ascii") + "\n")
if label == b"unicode":
    data = ("ASCII line\n\nLatin café; Кириллица; Ελληνικά; 中文; 😀; e\u0301\nend\n").encode()
    if os.environ.get("PREVIEW_ALT"):
        data = b"alternate rendered contents\n"
elif label == b"malformed":
    data = b"bad:\xff\ncontrols:\x00\x1b\r\x01\x7f\xc2\x80\nCRLF:\r\nnext\nlong:" + b"W" * 500
elif label == b"large":
    data = b"\t" * 16000
else:
    data = b"LATEST PREVIEW\n"
if os.environ.get("PREVIEW_LOADING_REF"):
    data = b"Loading\n"
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
with open(os.environ["PREVIEW_LOG"], "a", encoding="ascii") as log:
    log.write("done:" + label.decode("ascii") + "\n")
'''


class Menu:
    def __init__(self, binary, helper, labels, env, options, output, lines=6, launch_prefix=()):
        self.out = tempfile.TemporaryFile()
        self.err = tempfile.TemporaryFile()
        self.data = tempfile.TemporaryFile()
        self.data.write(labels)
        self.data.seek(0)
        self.proc = subprocess.Popen([binary, *launch_prefix, "-P", str(helper), "-l", str(lines), *options],
                                     stdin=self.data, stdout=self.out, stderr=self.err, env=env,
                                     start_new_session=True)
        self.output = output
        self.window = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            found = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", "dmenu"],
                                   text=True, capture_output=True)
            if found.returncode == 0 and found.stdout.split():
                self.window = found.stdout.split()[-1]
                return
            if self.proc.poll() is not None:
                error = self.stderr()
                self.close()
                raise RuntimeError("menu exited before mapping: " + error)
            time.sleep(.03)
        self.close()
        raise RuntimeError("menu did not map")

    def key(self, *keys):
        started = time.monotonic()
        run(["xdotool", "key", "--clearmodifiers", *keys])
        time.sleep(.08)
        return time.monotonic() - started

    def shot(self, path):
        run(["import", "-window", self.window, str(path)])

    def geometry(self):
        text = run(["xwininfo", "-id", self.window]).stdout
        return {key: int(re.search(rf"^\s*{re.escape(key)}:\s+(\d+)", text, re.M)[1])
                for key in ("Width", "Height")}

    def stderr(self):
        self.err.seek(0)
        return self.err.read().decode(errors="replace")

    def close(self):
        forced = self.proc.poll() is None
        if forced:
            self.proc.kill()
            self.proc.wait(timeout=2)
        error = self.stderr()
        (self.output / f"menu-{self.proc.pid}.stderr").write_text(error, encoding="utf-8")
        for stream in (self.out, self.err, self.data):
            stream.close()
        need(not re.search(r"AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:", error),
             f"sanitizer diagnostic from menu {self.proc.pid}: {error}")


def signature(path, crop):
    x, y, width, height = crop
    value = run(["convert", str(path), "-crop", f"{width}x{height}+{x}+{y}", "+repage",
                 "-format", "%[fx:standard_deviation] %#", "info:"]).stdout
    deviation, digest = value.split()
    return float(deviation), digest


def wait_image(menu, path, crop, timeout=4, expected=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        menu.shot(path)
        deviation, digest = signature(path, crop)
        if deviation > 0 and (expected is None or digest == expected):
            return
        time.sleep(.04)
    raise RuntimeError(f"preview pixels did not appear before timeout: {path}")


def exercise_pending_layout(probe, helper, output, log, loading_digest, latest_digest, escape=False):
    env = {**os.environ, "LC_ALL": "C.UTF-8", "PREVIEW_LOG": str(log)}
    menu = Menu(str(probe), helper, b"large\nlatest\n", env,
                ["-F", "-fn", "monospace:size=12"], output,
                launch_prefix=("--menu",))
    try:
        geometry = menu.geometry()
        row = geometry["Height"] // 7
        pane = (geometry["Width"] * 2 // 5 + 2, row,
                geometry["Width"] * 3 // 5 - 3, 5 * row)
        wait_helper_done(log, "large")
        deadline = time.monotonic() + 2
        children = Path(f"/proc/{menu.proc.pid}/task/{menu.proc.pid}/children")
        while children.read_text().strip() and time.monotonic() < deadline:
            time.sleep(.01)
        need(not children.read_text().strip(), "completed helper was not reaped during preparation")
        loading = output / ("pending-escape.png" if escape else "pending-cancel.png")
        menu.shot(loading)
        footer = (pane[0], 6 * row, pane[2], row)
        need(signature(loading, pane)[0] == 0 and
             signature(loading, footer)[1] == loading_digest,
             "completed helper did not remain Loading during deliberately slow preparation")
        if escape:
            started = time.monotonic()
            run(["xdotool", "key", "--clearmodifiers", "Escape"])
            status = menu.proc.wait(timeout=2)
            elapsed = time.monotonic() - started
            need(status == 1 and elapsed < .35,
                 f"Escape during pending preparation was slow: status={status}, {elapsed:.3f}s")
        else:
            started = time.monotonic()
            run(["xdotool", "key", "--clearmodifiers", "Down"])
            latest = output / "latest-after-pending-cancel.png"
            wait_image(menu, latest, pane)
            elapsed = time.monotonic() - started
            need(elapsed < .35 and signature(latest, pane)[1] == latest_digest,
                 f"selection stalled or published stale text during pending preparation: {elapsed:.3f}s")
            menu.key("Escape")
            need(menu.proc.wait(timeout=2) == 1, "Escape after pending-layout cancellation failed")
        print(f"PASS pending-layout {'Escape' if escape else 'selection'}: {elapsed:.3f}s including driver observation")
    finally:
        menu.close()


def wait_helper_done(log, label, timeout=5):
    target = f"done:{label}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and target in log.read_text(encoding="ascii").splitlines():
            return
        time.sleep(.02)
    raise RuntimeError(f"synthetic helper did not finish {label!r}")


def parent_window(width, height):
    xlib = ctypes.CDLL("libX11.so.6")
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    display = xlib.XOpenDisplay(None)
    need(display, "cannot open isolated DISPLAY for embedded narrow-font check")
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                         ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
    xlib.XCreateSimpleWindow.restype = ctypes.c_ulong
    xlib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    root = xlib.XDefaultRootWindow(display)
    parent = xlib.XCreateSimpleWindow(display, root, 5, 5, width, height, 0, 0, 0)
    xlib.XMapWindow(display, parent)
    xlib.XSync(display, 0)
    return xlib, display, parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--layout-probe", required=True, type=Path,
                        help="native preview-text probe built with --menu's slow Xft metrics")
    parser.add_argument("--clip-probe", required=True, type=Path,
                        help="real-Xft preview-clip content oracle")
    parser.add_argument("--isolated-display", action="store_true",
                        help="confirm DISPLAY names a disposable X server owned by this test")
    args = parser.parse_args()
    need(args.isolated_display and os.environ.get("DISPLAY"),
         "requires --isolated-display and DISPLAY set to an owned disposable X server")
    need(args.binary.is_absolute() and args.binary.is_file() and os.access(args.binary, os.X_OK),
         "--binary must be an executable absolute path")
    need(args.layout_probe.is_absolute() and args.layout_probe.is_file() and
         os.access(args.layout_probe, os.X_OK), "--layout-probe must be executable and absolute")
    need(args.clip_probe.is_absolute() and args.clip_probe.is_file() and
         os.access(args.clip_probe, os.X_OK), "--clip-probe must be executable and absolute")
    for command in ("xdotool", "xwininfo", "import", "convert"):
        need(shutil.which(command), f"missing required tool: {command}")
    args.output.mkdir(parents=True, exist_ok=True)
    need(not any(args.output.iterdir()), "--output must be empty")
    args.output = args.output.resolve()
    clip_output = args.output / "clipping"
    clip_output.mkdir()
    result = subprocess.run([str(args.clip_probe), str(clip_output)],
                            text=True, capture_output=True)
    (clip_output / "probe.stdout").write_text(result.stdout, encoding="utf-8")
    (clip_output / "probe.stderr").write_text(result.stderr, encoding="utf-8")
    for ppm in clip_output.glob("*.ppm"):
        run(["convert", str(ppm), str(ppm.with_suffix(".png"))])
    need(result.returncode == 0, f"overwide glyph content check failed: {result.stderr}")
    need(not re.search(r"AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:",
                       result.stderr), "sanitizer diagnostic from clipping probe")
    print("PASS overwide glyph content: " + result.stdout.strip())
    helper = args.output / "preview-text-helper.py"
    helper.write_text(HELPER.format(python=os.sys.executable), encoding="utf-8")
    helper.chmod(0o700)
    log = args.output / "helper.log"
    env = {**os.environ, "LC_ALL": "C.UTF-8", "PREVIEW_LOG": str(log)}
    labels = b"unicode\nmalformed\nlarge\nlatest\n"
    prefix = ["-F", "-fn", "monospace:size=12"]
    menu = Menu(str(args.binary), helper, labels, env, prefix, args.output)
    try:
        geometry = menu.geometry()
        row = geometry["Height"] // 7
        need(row > 0 and geometry["Height"] == 7 * row, "unexpected menu/footer row geometry")
        pane = (geometry["Width"] * 2 // 5 + 2, row,
                geometry["Width"] * 3 // 5 - 3, 5 * row)
        unicode = args.output / "unicode-multiline.png"
        wait_image(menu, unicode, pane)
        menu.key("Down")
        malformed = args.output / "malformed-crlf-wrapped.png"
        wait_image(menu, malformed, pane)
        menu.key("Down", "Down")
        latest = args.output / "latest-after-churn.png"
        wait_image(menu, latest, pane)
        menu.key("Escape")
        status = menu.proc.wait(timeout=2)
        need(status == 1, f"Escape after selection churn returned {status}")
    finally:
        menu.close()
    reference = Menu(str(args.binary), helper, labels,
                     {**env, "PREVIEW_LOADING_REF": "1"}, prefix, args.output)
    try:
        reference_image = args.output / "loading-reference.png"
        wait_image(reference, reference_image, pane)
        loading_digest = signature(reference_image, (pane[0], row, pane[2], row))[1]
    finally:
        reference.close()
    latest_digest = signature(latest, pane)[1]
    exercise_pending_layout(args.layout_probe, helper, args.output,
                            args.output / "pending-cancel.log", loading_digest, latest_digest)
    exercise_pending_layout(args.layout_probe, helper, args.output,
                            args.output / "pending-escape.log", loading_digest, latest_digest, escape=True)

    # Ready-state footer stays identical; only pixels inside the right preview body may change.
    alternate = Menu(str(args.binary), helper, labels,
                     {**env, "PREVIEW_ALT": "1"}, prefix, args.output)
    try:
        alternate_image = args.output / "alternate-text.png"
        wait_image(alternate, alternate_image, pane)
        protected = ((0, 0, geometry["Width"], row),
                     (0, row, geometry["Width"] * 2 // 5, 5 * row),
                     (0, 6 * row, geometry["Width"], row))
        for region in protected:
            need(signature(unicode, region)[1] == signature(alternate_image, region)[1],
                 "preview ink escaped into input, left pane, or footer")
    finally:
        alternate.close()

    # Real Xft clipping and wrapping with a narrow embedded pane and oversized font.
    xlib, display, parent = parent_window(320, 700)
    try:
        narrow = Menu(str(args.binary), helper, labels, env,
                      ["-F", "-fn", "monospace:size=24", "-w", hex(parent)], args.output)
        try:
            narrow_geometry = narrow.geometry()
            need(narrow_geometry["Width"] == 320 and narrow_geometry["Height"] <= 700,
                 f"narrow embedded geometry escaped parent bounds: {narrow_geometry}")
            narrow_row = narrow_geometry["Height"] // 7
            narrow_pane = (narrow_geometry["Width"] * 2 // 5 + 2, narrow_row,
                           narrow_geometry["Width"] * 3 // 5 - 3, 5 * narrow_row)
            shot = args.output / "narrow-oversized-font.png"
            wait_image(narrow, shot, narrow_pane)
            need(signature(shot, narrow_pane)[0] > 0,
                 "narrow oversized-font preview is blank")
        finally:
            narrow.close()
        narrow_alt = Menu(str(args.binary), helper, labels,
                          {**env, "PREVIEW_ALT": "1"},
                          ["-F", "-fn", "monospace:size=24", "-w", hex(parent)], args.output)
        try:
            alt_shot = args.output / "narrow-oversized-font-alt.png"
            wait_image(narrow_alt, alt_shot, narrow_pane)
            protected = ((0, 0, 320, narrow_row),
                         (0, narrow_row, 128, 5 * narrow_row),
                         (0, 6 * narrow_row, 320, narrow_row))
            for region in protected:
                need(signature(shot, region)[1] == signature(alt_shot, region)[1],
                     "oversized font ink escaped preview pane into protected pixels")
        finally:
            narrow_alt.close()
    finally:
        xlib.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XDestroyWindow(display, parent)
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XCloseDisplay(display)

    # The initial Ready preview has one body row for -l 1.
    single = Menu(str(args.binary), helper, b"unicode\n", env,
                  ["-F", "-fn", "monospace:size=12"], args.output, lines=1)
    try:
        one_geometry = single.geometry()
        one_row = one_geometry["Height"] // 2
        one_pane = (one_geometry["Width"] * 2 // 5 + 2, one_row,
                    one_geometry["Width"] * 3 // 5 - 3, one_row)
        one_image = args.output / "one-row-ready.png"
        first_row = signature(unicode, (pane[0], row, pane[2], row))[1]
        wait_image(single, one_image, one_pane, expected=first_row)
        need(signature(one_image, one_pane)[1] == first_row,
             "one-row Ready content differs from the normal first text row")
    finally:
        single.close()
    print(f"PASS preview text GUI screenshots: {', '.join(p.name for p in args.output.glob('*.png'))}")


if __name__ == "__main__":
    main()
