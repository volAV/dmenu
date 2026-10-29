#!/usr/bin/env python3
"""Compare no-preview behavior and exercise the real async preview-helper protocol."""
import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TIMEOUT = 5
HELPER_SOURCE = r'''#!{python}
import hashlib, json, os, subprocess, sys, time
mode = os.environ.get("PREVIEW_MODE", "content")
def extra_fds():
    result = []
    for fd in os.listdir("/proc/self/fd"):
        if fd not in ("0", "1", "2"):
            try:
                os.readlink("/proc/self/fd/" + fd)
                result.append(fd)
            except FileNotFoundError:
                pass
    return result
line = sys.stdin.buffer.readline() if mode not in ("no-read", "partial-input", "early-success") else b""
label = line[:-1].decode("utf-8", "replace") if line.endswith(b"\n") else line.decode("utf-8", "replace")
stdin_line_ends_lf = line.endswith(b"\n")
stdin_eof = sys.stdin.buffer.read(1) == b"" if mode not in ("no-read", "partial-input", "early-success") else False
env_digest = hashlib.sha256(json.dumps(sorted(os.environ.items()), separators=(",", ":")).encode()).hexdigest()
record = {{"argv": sys.argv, "label": label, "stdin_line_ends_lf": stdin_line_ends_lf, "stdin_eof": stdin_eof, "environment": sorted(os.environ), "environment_digest": env_digest, "label_in_environment": any(label and label in value for value in os.environ.values()), "extra_fds": extra_fds(), "stderr_target": os.readlink("/proc/self/fd/2"), "pid": os.getpid(), "pgid": os.getpgrp(), "started_monotonic": time.monotonic()}}
with open(os.environ["PREVIEW_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.flush()
os.write(2, b"synthetic stderr must be discarded\n")
if mode == "no-read":
    time.sleep(2)
elif mode == "partial-input":
    sys.stdin.buffer.read(1)
    os.close(0)
    sys.stdout.write("partial input\n")
    sys.exit(7)
elif mode == "early-success":
    os.close(0)
    sys.stdout.write("early success\n")
elif mode == "delay-output":
    time.sleep(.5)
    sys.stdout.write("delayed\n")
elif mode == "delay-exit":
    sys.stdout.write("finished\n")
    sys.stdout.flush()
    time.sleep(.2)
elif mode == "stale":
    time.sleep(float(os.environ.get("PREVIEW_DELAY", ".25")))
    sys.stdout.write({{"blue": "Unicode λ雪🙂\n", "red": "red preview\n", "green": "green preview\n"}}.get(label, label))
elif mode == "close-stdout":
    sys.stdout.write("closed\n")
    sys.stdout.flush()
    os.close(1)
    time.sleep(2)
elif mode == "unlimited":
    while True:
        os.write(1, b"x" * 4096)
elif mode == "continuous":
    while True:
        os.write(1, b"bounded preview traffic\n")
        time.sleep(.04)
elif mode == "nonzero":
    sys.stdout.write("discard me\n")
    sys.exit(7)
elif mode == "empty":
    pass
elif mode == "truncate":
    sys.stdout.buffer.write(b"x" * int(os.environ["PREVIEW_BYTES"]))
    sys.stdout.buffer.flush()
    sys.exit(int(os.environ.get("PREVIEW_EXIT", "0")))
elif mode == "descendant-pipe":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    sys.stdout.write("leader\n")
elif mode == "descendant-exit":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    sys.stdout.write("leader\n")
    sys.stdout.flush()
elif mode == "content":
    outputs = {{
        "alpha": "Alpha preview\nsecond line\n\nparagraph\n",
        "βeta": "Unicode λ雪🙂\n",
        "white space;$(touch nope)": "Literal label\n",
        "No preview": "No preview\n",
        "Unavailable": "Unavailable\n",
        "Timed out": "Timed out\n",
        "Truncated": "Truncated\n",
        "Loading": "Loading\n",
        "row-label": "first line\nsecond line\n",
        "first line": "first line\n",
        "red": "red preview\n",
        "green": "Green preview\nsecond line\n\nparagraph\n",
        "blue": "Unicode λ雪🙂\n",
        "x": "x preview\n",
    }}
    sys.stdout.write(outputs.get(label, "selected: " + label + "\n"))

'''

RESULTS = []
FIXTURE_LOGS = set()
DIAGNOSTIC_ERRORS = []
DIAGNOSTICS_DIR = None
REPORT_DIR = None
GROUP_OBSERVATIONS = []
class CheckFailure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def tool(name):
    path = shutil.which(name)
    require(path is not None, f"required tool not found: {name}")
    return path


def run(args, timeout=TIMEOUT, **kwargs):
    try:
        return subprocess.run(args, timeout=timeout, check=False,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise CheckFailure(f"timed out after {timeout}s: {' '.join(map(str, args))}") from exc

def run_instrumented(args, input, env):
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    try:
        stdout, stderr = process.communicate(input=input, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        stdout, stderr = process.communicate()
        persist_diagnostics(process.pid, args, process.returncode, True, stderr)
        raise CheckFailure(f"timed out after {TIMEOUT}s: {' '.join(map(str, args))}") from exc
    persist_diagnostics(process.pid, args, process.returncode, False, stderr)
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)


def report(name, passed, detail):
    line = f"{'PASS' if passed else 'FAIL'} {name}: {detail}"
    RESULTS.append(line)
    print(line, flush=True)
    if not passed:
        raise CheckFailure(line)


def check_diagnostics():
    require(not DIAGNOSTIC_ERRORS, "sanitizer diagnostics found: " + "; ".join(DIAGNOSTIC_ERRORS))

def persist_diagnostics(pid, command, status, forced, stderr):
    if DIAGNOSTICS_DIR is not None:
        DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"{pid}-{time.time_ns()}-{Path(command[0]).name}"
        (DIAGNOSTICS_DIR / f"{stem}.stderr").write_text(stderr, encoding="utf-8")
        with (DIAGNOSTICS_DIR / "processes.jsonl").open("a", encoding="utf-8") as index:
            index.write(json.dumps({"pid": pid, "command": command, "status": status,
                                    "forced_cleanup": forced, "stderr": f"{stem}.stderr"}) + "\n")
    if re.search(r"AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|"
                 r"runtime error:|heap-use-after-free|ERROR: (?:ASan|LSan|UBSan)",
                 stderr, re.I):
        DIAGNOSTIC_ERRORS.append(f"pid {pid}: {stderr.strip()}")





def make_helper(path):
    path.write_text(HELPER_SOURCE.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o700)


def helper_env(base, log, mode="content", **extra):
    FIXTURE_LOGS.add(str(log))
    return {**base, "PREVIEW_LOG": str(log), "PREVIEW_MODE": mode,
            **{key: str(value) for key, value in extra.items()}}


def read_log(path):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines.pop()  # The helper may still be writing its final JSONL record.
    return [json.loads(line) for line in lines]
def cleanup_helper_group(env):
    log = env.get("PREVIEW_LOG")
    if not log or log not in FIXTURE_LOGS:
        return
    records = read_log(Path(log))
    private_log = b"PREVIEW_LOG=" + os.fsencode(log)
    for pgid in {record["pgid"] for record in records}:
        if pgid <= 1 or pgid == os.getpgrp():
            continue
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) != pgid:
                    continue
                if private_log not in (entry / "environ").read_bytes().split(bytes([0])):
                    continue
                current = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(current[2]) == pgid:
                    os.killpg(pgid, signal.SIGKILL)
                    break
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
                pass


def observe_group(pgid, cancellation_started):
    """Observe only within two seconds of the conservative cancellation/completion bound."""
    deadline = cancellation_started + 2
    observer_started = time.monotonic()
    require(observer_started <= deadline,
            f"process-group observation started after its two-second bound: pgid={pgid}, "
            f"started={observer_started:.6f}, deadline={deadline:.6f}")
    while True:
        live, zombies = [], []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid:
                    (zombies if fields[0] == "Z" else live).append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
                pass
        observed = time.monotonic()
        require(observed <= deadline,
                f"infrastructure failure: process scan finished after its anchored deadline: pgid={pgid}")
        if not live or observed >= deadline:
            GROUP_OBSERVATIONS.append({"pgid": pgid, "cancellation_origin_monotonic": cancellation_started,
                                       "deadline_monotonic": deadline, "observed_monotonic": observed,
                                       "observation_elapsed": observed - cancellation_started,
                                       "live": live, "zombies": zombies})
            return live, zombies
        time.sleep(.02)


class Menu:
    def __init__(self, executable, args, data, env, inherited_fd=None):
        self.stdout = tempfile.TemporaryFile()
        self.stderr = tempfile.TemporaryFile()
        self.input = tempfile.TemporaryFile()
        self.input.write(data)
        self.input.seek(0)
        self.command = [executable, *args]
        self.process = subprocess.Popen(self.command, stdin=self.input,
                                        stdout=self.stdout, stderr=self.stderr, env=env,
                                        start_new_session=True,
                                        pass_fds=() if inherited_fd is None else (inherited_fd,))
        self.env = env
        self.window = None
    def wait_window(self):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise CheckFailure(f"menu exited before window map: {self.stderr_text()}")
            result = run([tool("xdotool"), "search", "--onlyvisible", "--class", "dmenu"], timeout=2)
            windows = result.stdout.split()
            if result.returncode == 0 and windows:
                self.window = windows[-1]
                return self.window
            time.sleep(.05)
        raise CheckFailure("menu window did not appear")

    def key(self, *keys):
        if keys:
            # Injection start conservatively precedes application cancellation.
            self.last_key_started = time.monotonic()
            try:
                result = run([tool("xdotool"), "key", "--clearmodifiers", *keys])
            except CheckFailure as error:
                raise CheckFailure(f"infrastructure failure: keyboard injector: {error}") from error
            self.last_key_delivered = time.monotonic()
            require(result.returncode == 0,
                    f"infrastructure failure: keyboard injection failed: {result.stderr}")
            time.sleep(.05)

    def info(self):
        result = run([tool("xwininfo"), "-id", self.window])
        require(result.returncode == 0, f"xwininfo failed: {result.stderr}")
        values = {}
        for name in ("Absolute upper-left X", "Absolute upper-left Y", "Width", "Height"):
            match = re.search(rf"^\s*{re.escape(name)}:\s+(\d+)", result.stdout, re.M)
            require(match is not None, f"missing {name} in xwininfo")
            values[name] = int(match.group(1))
        return values

    def screenshot(self, path):
        result = run([tool("import"), "-window", self.window, str(path)])
        require(result.returncode == 0 and path.is_file(), f"screenshot failed: {result.stderr}")

    def stdout_bytes(self):
        self.stdout.seek(0)
        return self.stdout.read()

    def stderr_text(self):
        self.stderr.seek(0)
        return self.stderr.read().decode(errors="replace")

    def finish(self, key):
        self.key(key)
        try:
            status = self.process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise CheckFailure(f"menu did not exit after {key}") from exc
        return self.stdout_bytes(), status, self.stderr_text()

    def close(self):
        forced = self.process.poll() is None
        if forced:
            self.process.kill()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        status = self.process.poll()
        stderr = self.stderr_text()
        persist_diagnostics(self.process.pid, self.command, status, forced, stderr)
        for file in (self.input, self.stdout, self.stderr):
            file.close()
        cleanup_helper_group(self.env)


def invoke(executable, args, data, env, inherited_fd=None):
    menu = Menu(executable, args, data, env, inherited_fd)
    try:
        menu.wait_window()
        return menu
    except BaseException:
        menu.close()
        raise


def pixel(path, x, y):
    result = run([tool("convert"), str(path), "-format", f"%[pixel:p{{{x},{y}}}]", "info:"])
    require(result.returncode == 0, f"pixel sampling failed: {result.stderr}")
    return result.stdout.strip()


def region_signature(path, x, y, width, height):
    """Measure rendered pixels, excluding the input, item highlight and divider."""
    result = run([tool("convert"), str(path), "-crop", f"{width}x{height}+{x}+{y}",
                  "+repage", "-format", "%[fx:standard_deviation] %#", "info:"])
    require(result.returncode == 0, f"preview pixel inspection failed: {result.stderr}")
    deviation, signature = result.stdout.split()
    return float(deviation), signature


def parent_window(width, height):
    xlib = ctypes.CDLL("libX11.so.6")
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    display = xlib.XOpenDisplay(None)
    require(display, "libX11 could not open DISPLAY for embedded-parent case")
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
                                         ctypes.c_uint, ctypes.c_ulong, ctypes.c_ulong]
    xlib.XCreateSimpleWindow.restype = ctypes.c_ulong
    xlib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xlib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    xlib.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    root = xlib.XDefaultRootWindow(display)
    window = xlib.XCreateSimpleWindow(display, root, 5, 5, width, height, 0, 0, 0)
    xlib.XMapWindow(display, window)
    xlib.XSync(display, 0)
    return xlib, display, window

def ximage_signature(window, area):
    class XImageHeader(ctypes.Structure):
        _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int),
                    ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
                    ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                    ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
                    ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
                    ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                    ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
                    ("blue_mask", ctypes.c_ulong), ("obdata", ctypes.c_void_p)]
    xlib = ctypes.CDLL("libX11.so.6")
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
    xlib.XGetImage.restype = ctypes.c_void_p
    xlib.XDestroyImage.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = xlib.XOpenDisplay(None)
    require(display, "XGetImage could not open DISPLAY")
    x, y, width, height = area
    image = xlib.XGetImage(display, int(window), x, y, width, height, ctypes.c_ulong(-1).value, 2)
    require(image, "XGetImage failed")
    try:
        header = ctypes.cast(image, ctypes.POINTER(XImageHeader)).contents
        data = ctypes.string_at(header.data, header.bytes_per_line * header.height)
        return hashlib.sha256(data).hexdigest()
    finally:
        xlib.XDestroyImage(image)
        xlib.XCloseDisplay(display)

EXPOSE_TRAFFIC_SOURCE = r'''
import ctypes, json, os, sys, time
class Expose(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                ("window", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("width", ctypes.c_int), ("height", ctypes.c_int), ("count", ctypes.c_int)]
class Event(ctypes.Union):
    _fields_ = [("expose", Expose), ("pad", ctypes.c_long * 24)]
class XError(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]
x = ctypes.CDLL("libX11.so.6")
x.XOpenDisplay.restype = ctypes.c_void_p
x.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                         ctypes.c_long, ctypes.c_void_p]
x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
x.XCloseDisplay.argtypes = [ctypes.c_void_p]
x.XSetErrorHandler.argtypes = [ctypes.c_void_p]
d = x.XOpenDisplay(None)
if not d:
    raise SystemExit("XOpenDisplay failed")
bad_window, unexpected = [False], []
@ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XError))
def on_error(_display, event):
    if event.contents.error_code == 3 and event.contents.resourceid == window:
        bad_window[0] = True
    else:
        unexpected.append(event.contents.error_code)
    return 0
x.XSetErrorHandler(ctypes.cast(on_error, ctypes.c_void_p))
window, stop, result, ready = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
started, count, sync_rtts, probe_started, first_ack = time.monotonic(), 0, [], None, None
try:
    while count < 300 and time.monotonic() - started < 3:
        probe_after_stop = False
        if os.path.exists(stop):
            with open(stop, encoding="utf-8") as control:
                probe_after_stop = control.read() == "probe"
        if os.path.exists(stop) and not probe_after_stop:
            break
        if probe_after_stop:
            probe_started = probe_started or time.monotonic()
            if time.monotonic() - probe_started >= 1:
                break
        event = Event()
        event.expose = Expose(12, 0, 1, d, window, 0, 0, 1, 1, 0)
        if not x.XSendEvent(d, window, 0, 1 << 15, ctypes.byref(event)):
            raise RuntimeError("XSendEvent returned failure")
        count += 1
        if count == 1 or count % 10 == 0 or probe_after_stop:
            before = time.monotonic()
            x.XSync(d, 0)
            rtt = time.monotonic() - before
            sync_rtts.append(rtt)
            if count == 1:
                first_ack = time.monotonic()
                with open(ready + ".pending", "w", encoding="utf-8") as f:
                    json.dump({"first_event_ack_monotonic": first_ack,
                               "first_xsync_rtt_seconds": rtt}, f)
                os.replace(ready + ".pending", ready)
        if unexpected:
            raise RuntimeError("unexpected X11 error codes: " + repr(unexpected))
        if bad_window[0]:
            if not probe_after_stop:
                with open(stop, encoding="utf-8") as control:
                    if control.read() != "probe":
                        raise RuntimeError("unexpected BadWindow before the requested teardown probe")
            break
        time.sleep(.01)
    if not bad_window[0]:
        x.XSync(d, 0)
    if unexpected:
        raise RuntimeError("unexpected X11 error codes: " + repr(unexpected))
finally:
    x.XCloseDisplay(d)
with open(result, "w", encoding="utf-8") as f:
    json.dump({"count": count, "started_monotonic": started,
               "stopped_monotonic": time.monotonic(),
               "first_event_ack_monotonic": first_ack,
               "first_xsync_rtt_seconds": sync_rtts[0] if sync_rtts else None,
               "max_xsync_rtt_seconds": max(sync_rtts, default=0),
               "expected_badwindow_after_destroy": bad_window[0]}, f)
'''


def start_expose_traffic(window, output, name):
    stop = output / f"{name}.stop"
    result = output / f"{name}.json"
    ready = output / f"{name}.ready.json"
    process = subprocess.Popen([sys.executable, "-c", EXPOSE_TRAFFIC_SOURCE,
                                str(window), str(stop), str(result), str(ready)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 1
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.005)
        require(ready.is_file() and process.poll() is None, "Expose generator did not become ready")
        handshake = json.loads(ready.read_text(encoding="utf-8"))
        require(handshake.get("first_xsync_rtt_seconds") is not None and
                handshake["first_xsync_rtt_seconds"] < .1,
                f"invalid or delayed Expose-generator handshake: {handshake}")
        return process, stop, result
    except (CheckFailure, OSError, ValueError) as error:
        stop.write_text("", encoding="utf-8")
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        raise CheckFailure(f"infrastructure failure: Expose handshake: {error}; "
                           f"generator diagnostics: {stderr or stdout}") from error


def stop_expose_traffic(process, stop, result, probe_badwindow=False):
    if probe_badwindow:
        require(stop.is_file() and stop.read_text(encoding="utf-8") == "probe",
                "infrastructure failure: window-destruction probe was not armed before Escape")
    else:
        stop.write_text("", encoding="utf-8")
    try:
        stdout, stderr = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise CheckFailure(f"infrastructure failure: X11 event generator exceeded its hard stop: {stderr or stdout}")
    require(process.returncode == 0 and result.is_file(),
            f"X11 event generator infrastructure failure: status={process.returncode}, stderr={stderr}")
    stats = json.loads(result.read_text(encoding="utf-8"))
    require(stats["count"] > 0 and
            (not probe_badwindow or stats["expected_badwindow_after_destroy"]),
            f"X11 event generator sent no events or missed required BadWindow probe: {stats}")
    require(stats["max_xsync_rtt_seconds"] < .1,
            f"infrastructure failure: X server round-trip delay prevents a fairness measurement: {stats}")
    return stats


def protocol_cases(binary, helper, output, base_env, token_mode=False):
    menu_options = ["-F"] if token_mode else []
    env_log = output / "protocol.jsonl"
    reference = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"No preview\n",
                       helper_env(base_env, env_log))
    try:
        time.sleep(.15)
        reference_image = output / "status-reference.png"
        reference.screenshot(reference_image)
        geometry = reference.info()
    finally:
        reference.close()
    row = geometry["Height"] // 6
    pane = (geometry["Width"] - 1) * 2 // 5 + 1
    reference_blank = region_signature(reference_image, pane, row * 5,
                                       geometry["Width"] - pane, row)[1]
    word_signatures = {}
    for word in ("No preview", "Unavailable", "Timed out", "Truncated", "Loading"):
        ref = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], (word + "\n").encode(),
                     helper_env(base_env, env_log))
        try:
            image = output / f"status-word-{word.replace(' ', '-')}.png"
            time.sleep(.15)
            ref.screenshot(image)
            word_signatures[word] = region_signature(image, pane, row, geometry["Width"] - pane, row)[1]
        finally:
            ref.close()

    statuses = [("empty", "No preview", {}), ("nonzero", "Unavailable", {}),
                ("no-read", "Timed out", {}), ("partial-input", "Unavailable", {}),
                ("close-stdout", "Timed out", {}), ("descendant-pipe", "Timed out", {}),
                ("descendant-exit", "", {}), ("early-success", "", {}),
                ("unlimited", "Truncated", {})]
    for index, (mode, word, extra) in enumerate(statuses):
        log = output / f"{mode}.jsonl"
        large = (b"target-" + b"q" * 100000 + b"\n") if mode in ("no-read", "partial-input", "early-success") else b"target\n"
        menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], large,
                      helper_env(base_env, log, mode, **extra))
        try:
            image = output / f"status-{index}-{mode}.png"
            time.sleep(1.15 if word == "Timed out" or mode == "unlimited" else .15)
            menu.screenshot(image)
            actual = region_signature(image, pane, row * 5, geometry["Width"] - pane, row)[1]
            expected = word_signatures[word] if word else reference_blank
            report(f"helper-status-{mode}", actual == expected,
                   f"footer matches {word or 'successful blank state'}; screenshot={image}")
            visible, _ = region_signature(image, pane, row, geometry["Width"] - pane, row * 4)
            require((visible > 0) == (not word or word == "Truncated"),
                    f"{mode} incorrectly published/discarded partial output")
            require(not menu.stderr_text(), f"{mode} leaked helper stderr into menu diagnostics")
            if mode == "no-read":
                records = read_log(log)
                require(records and len(records[0]["argv"]) == 1 and
                        Path(records[0]["argv"][0]) == helper,
                        f"helper argv was not executable-only: {records}")
            if mode in ("descendant-pipe", "descendant-exit"):
                records = read_log(log)
                require(records, f"{mode} helper did not record its process group")
                pgid = records[-1]["pgid"]
                live, zombies = observe_group(pgid, records[-1]["started_monotonic"])
                require(not live, f"{mode} process group {pgid} still has live members: {live}")
                report(f"helper-group-termination-{mode}", True,
                       f"no live group members after observation; zombies={zombies}")
        finally:
            menu.close()

    for size, exit_status in ((65535, 0), (65536, 0), (65537, 0), (65537, 7), (65536, 7)):
        log = output / f"bytes-{size}-exit-{exit_status}.jsonl"
        menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"target\n",
                      helper_env(base_env, log, "truncate", PREVIEW_BYTES=size, PREVIEW_EXIT=exit_status))
        try:
            image = output / f"status-bytes-{size}-exit-{exit_status}.png"
            time.sleep(.15)
            menu.screenshot(image)
            actual = region_signature(image, pane, row * 5, geometry["Width"] - pane, row)[1]
            expected = (word_signatures["Truncated"] if size > 65536 else
                        word_signatures["Unavailable"] if exit_status else reference_blank)
            require(actual == expected,
                    f"{size}-byte output with exit {exit_status} has incorrect terminal state")
            report(f"helper-output-{size}-bytes-exit-{exit_status}", True,
                   f"terminal-state precedence verified; screenshot={image}")
        finally:
            menu.close()
    first_line_ref = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "1"],
                            b"first line\n", helper_env(base_env, output / "one-row-first.jsonl"))
    try:
        time.sleep(.15)
        one_geometry = first_line_ref.info()
        one_row = one_geometry["Height"] // 2
        pane_x = (one_geometry["Width"] - 1) * 2 // 5 + 1
        first_image = output / "one-row-first.png"
        first_line_ref.screenshot(first_image)
        first_pixels = region_signature(first_image, pane_x, one_row,
                                        one_geometry["Width"] - pane_x, one_row)[1]
    finally:
        first_line_ref.close()
    multiline = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "1"],
                       b"row-label\n", helper_env(base_env, output / "one-row-multiline.jsonl"))
    try:
        time.sleep(.15)
        image = output / "one-row-multiline.png"
        multiline.screenshot(image)
        actual = region_signature(image, pane_x, one_row,
                                  one_geometry["Width"] - pane_x, one_row)[1]
        require(multiline.info()["Height"] == one_geometry["Height"] and actual == first_pixels,
                "single-row Ready preview did not clip to its first content line")
        report("helper-single-row-ready", True, f"first line rendered; screenshot={image}")
    finally:
        multiline.close()
    status_ref = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "1"],
                        b"No preview\n", helper_env(base_env, output / "one-row-status-ref.jsonl"))
    try:
        time.sleep(.15)
        image = output / "one-row-status-reference.png"
        status_ref.screenshot(image)
        status_pixels = region_signature(image, pane_x, one_row,
                                         one_geometry["Width"] - pane_x, one_row)[1]
    finally:
        status_ref.close()
    empty_one = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "1"],
                       b"target\n", helper_env(base_env, output / "one-row-empty.jsonl", "empty"))
    try:
        time.sleep(.15)
        image = output / "one-row-empty.png"
        empty_one.screenshot(image)
        actual = region_signature(image, pane_x, one_row,
                                  one_geometry["Width"] - pane_x, one_row)[1]
        require(actual == status_pixels, "single-row empty state did not display only No preview")
        report("helper-single-row-status", True, f"No preview status rendered; screenshot={image}")
    finally:
        empty_one.close()

    # A missing, non-executable, and non-shebang helper must be ordinary Unavailable states.
    for name, content, executable in (("missing", None, False), ("nonexec", "pass\n", False),
                                       ("no-shebang", "printf 'forbidden shell fallback\\n'\nexit 0\n", True)):
        candidate = output / f"helper-{name}"
        if content is not None:
            candidate.write_text(content, encoding="utf-8")
            candidate.chmod(0o700 if executable else 0o600)
        badenv = helper_env(base_env, output / f"{name}.jsonl")
        menu = invoke(str(binary), ["-P", str(candidate), "-l", "5"], b"target\n", badenv)
        try:
            time.sleep(.15)
            image = output / f"status-{name}.png"
            menu.screenshot(image)
            actual = region_signature(image, pane, row * 5, geometry["Width"] - pane, row)[1]
            require(actual == word_signatures["Unavailable"],
                    f"{name} executable did not render Unavailable status")
            report(f"helper-{name}-failure", True, f"footer=Unavailable; screenshot={image}")
        finally:
            menu.close()

    # Direct-exec protocol preserves the selected bytes, passes no argv, and does not export it.
    private = " \tCafé white space;$(touch nope)  "
    private_log = output / "privacy.jsonl"
    inherited_fd = os.open(output / "inherited-descriptor", os.O_CREAT | os.O_RDWR, 0o600)
    menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], (private + "\n").encode(),
                  helper_env(base_env, private_log), inherited_fd=inherited_fd)
    try:
        time.sleep(.15)
        records = read_log(private_log)
        require(records and records[-1]["argv"] == [str(helper)] and records[-1]["label"] == private,
                f"helper argv/stdin protocol mismatch: {records}")
        require(records[-1]["stdin_line_ends_lf"] and records[-1]["stdin_eof"],
                f"helper stdin did not contain one LF-terminated label followed by EOF: {records[-1]}")
        require(not records[-1]["label_in_environment"] and records[-1]["extra_fds"] == [],
                f"selected content leaked or an unexpected fd was inherited: {records[-1]}")
        expected_env = helper_env(base_env, private_log)
        expected_digest = hashlib.sha256(json.dumps(sorted(expected_env.items()),
                                                    separators=(",", ":")).encode()).hexdigest()
        require(records[-1]["environment_digest"] == expected_digest and
                records[-1]["stderr_target"] == "/dev/null",
                f"helper environment or stderr inheritance mismatch: {records[-1]}")
        image = output / "literal-label-preview.png"
        menu.screenshot(image)
        visible, _ = region_signature(image, pane, row, geometry["Width"] - pane, row * 4)
        report("helper-argv-env-privacy", visible > 0,
               f"argv is executable-only; exact label arrived on LF-terminated stdin then EOF; screenshot={image}")
        actual = menu.finish("Return")[:2]
        require(actual == ((private + "\n").encode(), 0),
                f"literal selection stdout/status changed: {actual}; diagnostics: {menu.stderr_text()}")
    finally:
        menu.close()
        os.close(inherited_fd)
    long_label = "L" * 100000
    log = output / "long-input.jsonl"
    menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"],
                  (long_label + "\n").encode(), helper_env(base_env, log, "empty"))
    try:
        time.sleep(.2)
        records = read_log(log)
        require(records and records[-1]["label"] == long_label and records[-1]["stdin_eof"],
                "partial writes lost bytes from a label larger than pipe capacity")
        report("helper-large-stdin-delivery", True, "100000 original label bytes plus LF and EOF received")
    finally:
        menu.close()
    for mode in ("delay-output", "delay-exit"):
        log = output / f"{mode}.jsonl"
        menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"target\n",
                      helper_env(base_env, log, mode))
        try:
            if mode == "delay-output":
                loading_image = output / "status-loading.png"
                time.sleep(.12)
                menu.screenshot(loading_image)
                loading = region_signature(loading_image, pane, row * 5,
                                           geometry["Width"] - pane, row)[1]
                require(loading == word_signatures["Loading"],
                        f"pending helper did not render Loading footer: {loading}")
                report("helper-status-loading", True,
                       f"footer matches rendered Loading; screenshot={loading_image}")
                time.sleep(.5)
            else:
                loading_image = output / "delay-exit-before-completion.png"
                time.sleep(.08)
                menu.screenshot(loading_image)
                pending, _ = region_signature(loading_image, pane, row, geometry["Width"] - pane, row * 4)
                require(pending == 0, "stdout was published before delayed process exit")
                time.sleep(.3)
            image = output / f"success-{mode}.png"
            menu.screenshot(image)
            nonblank, _ = region_signature(image, pane, row, geometry["Width"] - pane, row * 4)
            require(nonblank > 0, f"{mode} did not publish successful helper output")
            report(f"helper-{mode}", True, f"successful completion rendered; screenshot={image}")
        finally:
            menu.close()

    rapid_log = output / "rapid-selection.jsonl"
    menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"red\ngreen\nblue\n",
                  helper_env(base_env, rapid_log))
    try:
        run([tool("xdotool"), "key", "--delay", "1", "Down", "Down"])
        time.sleep(.25)
        records = read_log(rapid_log)
        require(records and records[-1]["label"] == "blue",
                f"rapid selection left stale helper result: {records}")
        require(len(records) <= 2, f"40ms debounce started too many rapid helper requests: {len(records)}")
        report("helper-rapid-selection-debounce", True,
               f"{len(records)} request(s); latest label={records[-1]['label']!r}")
    finally:
        menu.close()
    area = (pane, row, geometry["Width"] - pane, row * 4)
    footer_area = (pane, row * 5, geometry["Width"] - pane, row)
    blue = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"blue\n",
                  helper_env(base_env, output / "blue-reference.jsonl"))
    try:
        time.sleep(.15)
        blue_image = output / "blue-reference.png"
        blue.screenshot(blue_image)
        blue_signature = ximage_signature(blue.window, area)
        blue_footer_signature = ximage_signature(blue.window, footer_area)
    finally:
        blue.close()
    green_ref = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"green\n",
                       helper_env(base_env, output / "green-reference.jsonl", "stale", PREVIEW_DELAY=.15))
    try:
        time.sleep(.3)
        forbidden_green = ximage_signature(green_ref.window, area)
    finally:
        green_ref.close()
    red_ref = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"red\n",
                     helper_env(base_env, output / "red-reference.jsonl"))
    try:
        time.sleep(.25)
        red_image = output / "red-reference.png"
        red_ref.screenshot(red_image)
        require(region_signature(red_image, *area)[0] > 0,
                "forbidden superseded-content reference did not render")
        forbidden_red = ximage_signature(red_ref.window, area)
    finally:
        red_ref.close()
    stale_log = output / "stale-selection.jsonl"
    menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"red\ngreen\nblue\n",
                  helper_env(base_env, stale_log, "stale", PREVIEW_DELAY=.8))
    try:
        deadline = time.monotonic() + 2
        while not read_log(stale_log) and time.monotonic() < deadline:
            time.sleep(.005)
        records = read_log(stale_log)
        require(records and records[0]["label"] == "red",
                "stale-output test did not observe old helper startup before superseding it")
        selection_pixel = (1, row + row // 2, 1, 1)
        initial_timestamp = time.monotonic()
        initial_highlight = ximage_signature(menu.window, selection_pixel)
        initial_signature = ximage_signature(menu.window, area)
        initial_footer = ximage_signature(menu.window, footer_area)
        samples = [{"monotonic": initial_timestamp, "highlight": initial_highlight,
                    "signature": initial_signature, "footer_signature": initial_footer}]
        injection_started = time.monotonic()
        injector = subprocess.Popen([tool("xdotool"), "key", "--clearmodifiers",
                                     "--delay", "1", "Down", "Down"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        old_would_complete = records[0]["started_monotonic"] + .8
        superseded_at = intermediate_signature = newest_started = output_seen_at = None
        delayed_current_alive = False
        sampling_deadline = injection_started + 1.5
        while time.monotonic() < sampling_deadline:
            records = read_log(stale_log)
            timestamp = time.monotonic()
            highlight = ximage_signature(menu.window, selection_pixel)
            signature = ximage_signature(menu.window, area)
            footer_signature = ximage_signature(menu.window, footer_area)
            newest = records[-1] if len(records) >= 2 else None
            newest_started = newest["started_monotonic"] if newest else None
            newest_alive = bool(newest and Path("/proc", str(newest["pid"])).exists())
            samples.append({"monotonic": timestamp, "highlight": highlight, "signature": signature,
                            "footer_signature": footer_signature, "newest_helper_alive": newest_alive})
            if superseded_at is None and highlight != initial_highlight:
                superseded_at, intermediate_signature = timestamp, signature
            if (superseded_at is not None and newest_started is not None and
                    timestamp >= newest_started + .35 and signature != blue_signature and newest_alive):
                delayed_current_alive = True
            if signature == blue_signature:
                output_seen_at = timestamp
            if (superseded_at is not None and output_seen_at is not None and
                    timestamp >= old_would_complete):
                break
            time.sleep(.02)
        try:
            injector_stdout, injector_stderr = injector.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            injector.kill()
            injector_stdout, injector_stderr = injector.communicate()
            raise CheckFailure("infrastructure failure: stale-case xdotool injector exceeded timeout")
        require(injector.returncode == 0,
                f"infrastructure failure: stale-case xdotool injector failed: {injector_stderr or injector_stdout}")
        require(superseded_at is not None and output_seen_at is not None and
                len(records) >= 2 and records[-1]["label"] == "blue",
                f"selection transition, old deadline, or newest output not observed: records={records}")
        nav_elapsed = superseded_at - injection_started
        sample_gaps = [samples[index]["monotonic"] - samples[index - 1]["monotonic"]
                       for index in range(1, len(samples))]
        max_sample_gap = max(sample_gaps, default=0)
        sample_path = output / "stale-intermediate-samples.json"
        sample_path.write_text(json.dumps({"samples": samples, "injection_started_monotonic": injection_started,
                                           "selection_superseded_monotonic": superseded_at,
                                           "old_helper_would_complete_monotonic": old_would_complete,
                                           "newest_helper_started_monotonic": records[-1]["started_monotonic"],
                                           "newest_output_seen_monotonic": output_seen_at,
                                           "reference_signatures": {"loading_body": initial_signature,
                                                                    "loading_footer": initial_footer,
                                                                    "blue_body": blue_signature,
                                                                    "blue_footer": blue_footer_signature,
                                                                    "forbidden_red": forbidden_red,
                                                                    "forbidden_green": forbidden_green},
                                           "requested_sleep_interval_seconds": .02,
                                           "max_observed_sample_gap_seconds": max_sample_gap,
                                           "sampling_ceiling": "Transient content shorter than an observed sample gap may be missed."},
                                          indent=2) + "\n", encoding="utf-8")
        after_supersede = [sample for sample in samples if sample["monotonic"] >= superseded_at]
        require(after_supersede and intermediate_signature == initial_signature and
                delayed_current_alive and output_seen_at >= old_would_complete and
                all(sample["signature"] in (initial_signature, blue_signature) and
                    sample["signature"] not in (forbidden_red, forbidden_green) and
                    sample["footer_signature"] in (initial_footer, blue_footer_signature)
                    for sample in after_supersede),
                f"superseded content observed or stale interval was not sampled through new output: {sample_path}")
        image = output / "stale-selection.png"
        menu.screenshot(image)
        current = region_signature(image, *area)[1]
        expected = region_signature(blue_image, *area)[1]
        require(current == expected,
                f"newest blue helper output not rendered after stale interval: records={records}")
        report("helper-stale-selection-discard", True,
               f"old start observed; newest output rendered; navigation={nav_elapsed:.3f}s; "
               f"samples={len(samples)} max-gap={max_sample_gap:.3f}s; transients shorter may be missed; "
               f"evidence={sample_path}")
    finally:
        menu.close()

    timeout_log = output / "fairness-timeout.jsonl"
    timeout_menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"],
                          b"red\ngreen\n", helper_env(base_env, timeout_log, "continuous"))
    traffic = stop = result = None
    try:
        deadline = time.monotonic() + 2
        while not read_log(timeout_log) and time.monotonic() < deadline:
            time.sleep(.005)
        timeout_records = read_log(timeout_log)
        require(timeout_records, "continuous timeout helper did not actually start")
        footer_area = (pane, row * 5, geometry["Width"] - pane, row)
        loading_footer = ximage_signature(timeout_menu.window, footer_area)
        traffic, stop, result = start_expose_traffic(timeout_menu.window, output, "fairness-timeout-events")
        timeout_samples = []
        timeout_sampled_at = None
        deadline = time.monotonic() + 1.8
        while time.monotonic() < deadline:
            timestamp = time.monotonic()
            signature = ximage_signature(timeout_menu.window, footer_area)
            timeout_samples.append({"monotonic": timestamp, "signature": signature})
            if signature != loading_footer:
                timeout_sampled_at = timestamp
                break
            time.sleep(.02)
        require(timeout_sampled_at is not None, "timeout status did not change under Expose traffic")
        timeout_elapsed = timeout_sampled_at - timeout_records[0]["started_monotonic"]
        timeout_image = output / "fairness-timeout.png"
        timeout_menu.screenshot(timeout_image)
        timeout_state = region_signature(timeout_image, pane, row * 5,
                                         geometry["Width"] - pane, row)[1]
        traffic_stats = stop_expose_traffic(traffic, stop, result)
        traffic = None
        require(timeout_state == word_signatures["Timed out"] and .8 <= timeout_elapsed <= 1.3,
                f"deadline was not serviced during Expose traffic: state={timeout_state}, "
                f"elapsed={timeout_elapsed:.3f}s after Python helper-start timestamp")
        timeout_gaps = [timeout_samples[index]["monotonic"] - timeout_samples[index - 1]["monotonic"]
                        for index in range(1, len(timeout_samples))]
        (output / "fairness-timeout-evidence.json").write_text(
            json.dumps({"helper_started_monotonic": timeout_records[0]["started_monotonic"],
                        "timeout_sample_monotonic": timeout_sampled_at, "elapsed": timeout_elapsed,
                        "startup_uncertainty": "Python timestamp is post-exec; application deadline starts before exec.",
                        "samples": timeout_samples,
                        "max_observed_sample_gap_seconds": max(timeout_gaps, default=0),
                        "traffic": traffic_stats, "timeout_signature": timeout_state}, indent=2) + "\n",
            encoding="utf-8")
        report("helper-deadline-under-x11-traffic", True,
               f"Timed out at {timeout_elapsed:.3f}s with {traffic_stats['count']} bounded-rate Expose events; "
               f"first/max XSync RTT={traffic_stats['first_xsync_rtt_seconds']:.6f}/"
               f"{traffic_stats['max_xsync_rtt_seconds']:.6f}s")
    finally:
        try:
            if traffic is not None:
                stop_expose_traffic(traffic, stop, result)
        finally:
            timeout_menu.close()

    cancel_log = output / "fairness-cancel.jsonl"
    cancel_menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"],
                         b"red\ngreen\n", helper_env(base_env, cancel_log, "continuous"))
    traffic = stop = result = None
    try:
        deadline = time.monotonic() + 2
        while not read_log(cancel_log) and time.monotonic() < deadline:
            time.sleep(.005)
        require(read_log(cancel_log), "continuous cancellation helper did not start")
        traffic, stop, result = start_expose_traffic(cancel_menu.window, output, "fairness-cancel-events")
        navigation_started = time.monotonic()
        cancel_menu.key("Down")
        navigation_elapsed = time.monotonic() - navigation_started
        navigation_dispatch = cancel_menu.last_key_delivered - cancel_menu.last_key_started
        require(navigation_dispatch < .2,
                f"infrastructure failure: navigation injector took {navigation_dispatch:.3f}s")
        deadline = time.monotonic() + .5
        while len(read_log(cancel_log)) < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        cancel_records = read_log(cancel_log)
        require(len(cancel_records) >= 2 and cancel_records[-1]["label"] == "green",
                f"navigation did not start next helper under Expose traffic: {cancel_records}")
        next_helper_start_latency = cancel_records[-1]["started_monotonic"] - navigation_started
        # Arm the expected window destruction before Escape, without a partial control-file read.
        pending = stop.with_suffix(".pending")
        pending.write_text("probe", encoding="utf-8")
        pending.replace(stop)
        cancellation_started = time.monotonic()
        actual = cancel_menu.finish("Escape")[:2]
        cancellation_elapsed = time.monotonic() - cancellation_started
        cancellation_dispatch = cancel_menu.last_key_delivered - cancel_menu.last_key_started
        require(cancellation_dispatch < .2,
                f"infrastructure failure: Escape injector took {cancellation_dispatch:.3f}s")
        traffic_stats = stop_expose_traffic(traffic, stop, result, probe_badwindow=True)
        traffic = None
        require(actual == (b"", 1) and navigation_elapsed < .5 and
                next_helper_start_latency < .5 and cancellation_elapsed < .5,
                f"active cancellation/navigation stalled under X11 traffic: "
                f"stdout/status={actual}, navigation={navigation_elapsed:.3f}s, "
                f"next-helper={next_helper_start_latency:.3f}s, cancel={cancellation_elapsed:.3f}s")
        (output / "fairness-cancel-evidence.json").write_text(
            json.dumps({"navigation_started_monotonic": navigation_started,
                        "next_helper_started_monotonic": cancel_records[-1]["started_monotonic"],
                        "navigation_elapsed": navigation_elapsed,
                        "navigation_injector_elapsed": navigation_dispatch,
                        "next_helper_start_latency": next_helper_start_latency,
                        "cancellation_started_monotonic": cancellation_started,
                        "cancellation_elapsed": cancellation_elapsed,
                        "escape_injector_elapsed": cancellation_dispatch,
                        "traffic": traffic_stats}, indent=2) + "\n", encoding="utf-8")
        report("helper-cancellation-under-x11-traffic", True,
               f"next label helper and Escape completed under {traffic_stats['count']} Expose events; "
               f"navigation={navigation_elapsed:.3f}s next-start={next_helper_start_latency:.3f}s "
               f"cancellation={cancellation_elapsed:.3f}s; expected BadWindow handled; "
               f"first/max XSync RTT={traffic_stats['first_xsync_rtt_seconds']:.6f}/"
               f"{traffic_stats['max_xsync_rtt_seconds']:.6f}s")
    finally:
        try:
            if traffic is not None:
                stop_expose_traffic(traffic, stop, result)
        finally:
            cancel_menu.close()

    log = output / "zero-matches-running.jsonl"
    menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"],
                  b"q" * 100000 + b"\n", helper_env(base_env, log, "no-read"))
    try:
        time.sleep(.15)
        records = read_log(log)
        require(records, "blocked-stdin helper never started")
        start = time.monotonic()
        menu.key("z")
        image = output / "zero-matches-running.png"
        menu.screenshot(image)
        elapsed = time.monotonic() - start
        blank, _ = region_signature(image, pane, row, geometry["Width"] - pane, row * 5)
        require(blank == 0 and elapsed < .25, f"blocked-stdin filtering failed: {blank}, {elapsed}")
        for record in records:
            live, zombies = observe_group(record["pgid"], start)
            require(not live, f"zero matches left helper group alive: {live}")
        require(menu.finish("Return")[:2] == (b"z\n", 0),
                "zero matches changed typed-input stdout")
        report("helper-blocked-stdin-zero-matches", True,
               f"100000-byte blocked input cancelled, pane cleared, latency={elapsed:.3f}s")
    finally:
        menu.close()


    for key, expected, status in (("Return", b"red\n", 0),
                                  ("shift+Return", b"r\n", 0),
                                  ("ctrl+Return", b"red\ngreen\n", 0),
                                  ("Escape", b"", 1)):
        log = output / f"running-key-{key}.jsonl"
        menu = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"],
                      b"red\ngreen\nblue\n", helper_env(base_env, log, "no-read"))
        try:
            time.sleep(.15)
            require(read_log(log), "running-helper output-key test never launched a helper")
            if key == "shift+Return":
                menu.key("r")
            start = time.monotonic()
            if key == "ctrl+Return":
                menu.key(key)
                require(menu.process.poll() is None, "Ctrl-Return unexpectedly exited")
                menu.key("Down")
                time.sleep(.12)
                final = "Return"
            else:
                final = key
            actual = menu.finish(final)[:2]
            elapsed = time.monotonic() - start
            require(actual == (expected, status) and elapsed < (.6 if key == "ctrl+Return" else .25),
                    f"{key} blocked or changed output: {actual}, elapsed={elapsed}")
            require(not menu.stderr_text(), "helper stderr leaked on output/cancel")
            for record in read_log(log):
                live, zombies = observe_group(record["pgid"], start)
                require(not live, f"{key} left a live helper group: {live}")
            report(f"helper-running-key-{key}", True,
                   f"stdout/status preserved; elapsed={elapsed:.3f}s; groups terminated")
        finally:
            menu.close()
    released = invoke(str(binary), [], b"keyboard-released\n", base_env)
    try:
        baseline_fd_count = len(list((Path("/proc") / str(released.process.pid) / "fd").iterdir()))
        actual = released.finish("Return")[:2]
        require(actual == (b"keyboard-released\n", 0),
                f"keyboard grab was not released after output-key completion: {actual}")
        report("helper-keyboard-grab-release", True, "a new menu accepted input after output-key completion")
    finally:
        released.close()

    repeated = invoke(str(binary), [*menu_options, "-P", str(helper), "-l", "5"], b"red\ngreen\n",
                      helper_env(base_env, output / "repeated.jsonl", "no-read"))
    try:
        rss, fds, active_children, settled_children = [], [], [], []
        proc = Path("/proc") / str(repeated.process.pid)
        log = output / "repeated.jsonl"
        children_path = proc / "task" / str(repeated.process.pid) / "children"
        deadline = time.monotonic() + 2
        while not read_log(log) and time.monotonic() < deadline:
            time.sleep(.005)
        records = read_log(log)
        require(records, "repeated-request baseline helper did not actually start")
        previous_record = records[-1]
        previous = previous_record["pid"]
        initial_count = len(records)
        repeated_observed_groups = []
        for index in range(200):
            cancel_bound = time.monotonic()
            repeated.key("Down" if index % 2 == 0 else "Up")
            expected_count = initial_count + index + 1
            deadline = cancel_bound + 2
            while len(read_log(log)) < expected_count and time.monotonic() < deadline:
                time.sleep(.005)
            records = read_log(log)
            require(len(records) >= expected_count,
                    f"repeated helper startup missing: expected {expected_count}, got {len(records)}")
            record = records[expected_count - 1]
            if previous is not None:
                while Path("/proc", str(previous)).exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                require(not Path("/proc", str(previous)).exists(),
                        f"old direct helper PID {previous} was not reaped while menu remained alive")
                live, zombies = observe_group(previous_record["pgid"], cancel_bound)
                require(not live, f"repeated request left group {previous_record['pgid']} alive: {live}")
                repeated_observed_groups.append(previous_record["pgid"])
            previous_record = record
            previous = record["pid"]
            active_children.append(children_path.read_text().split())
            rss.append(int((proc / "statm").read_text().split()[1]))
            fds.append(len(list((proc / "fd").iterdir())))
            require(active_children[-1] == [str(previous)] and
                    Path("/proc", str(previous), "stat").read_text().rsplit(")", 1)[1].split()[0] != "Z",
                    f"resource sample did not contain the confirmed live helper {previous}")
        cancel_bound = time.monotonic()
        repeated.key("z")
        deadline = cancel_bound + 2
        while children_path.read_text().strip() and time.monotonic() < deadline:
            time.sleep(.005)
        settled_children = children_path.read_text().split()
        settled_rss = int((proc / "statm").read_text().split()[1])
        settled_fds = len(list((proc / "fd").iterdir()))
        require(not settled_children and not Path("/proc", str(previous)).exists(),
                f"settled helper was not reaped: children={settled_children}, pid={previous}")
        live, zombies = observe_group(previous_record["pgid"], cancel_bound)
        require(not live, f"final repeated request left group {previous_record['pgid']} alive: {live}")
        repeated_observed_groups.append(previous_record["pgid"])
        records = read_log(log)
        unique_pids = {record["pid"] for record in records}
        require(len(records) >= 200 and len(unique_pids) >= 200,
                f"explicit minimum 200 unique helper starts not met: {len(records)} records, "
                f"{len(unique_pids)} unique PIDs")
        # Treat the first ten active samples as startup/quarantine warmup; retain them in raw evidence.
        warmup_count = 10
        maps = (proc / "maps").read_text(errors="replace")
        asan = "libasan" in maps or "libclang_rt.asan" in maps
        rss_budget_pages = 1024 if asan else 64
        measured_rss = rss[warmup_count:] + [settled_rss]
        measured_fds = fds + [settled_fds]
        require(max(measured_rss) - min(measured_rss) <= rss_budget_pages and
                settled_fds == baseline_fd_count and
                max(measured_fds) <= baseline_fd_count + 2 and
                max(measured_fds) - min(measured_fds) <= 2 and
                all(len(children) <= 1 for children in active_children),
                f"repeated helper resources exceeded post-warmup budget={rss_budget_pages} RSS pages: "
                f"rss={rss}, fds={fds}, active={active_children}, settled={settled_children}")
        resource_evidence = {"warmup_samples": warmup_count, "asan_runtime_detected": asan,
                             "no_preview_fd_baseline": baseline_fd_count,
                             "rss_budget_pages": rss_budget_pages, "rss_pages": rss,
                             "fd_counts": fds, "active_child_pids": active_children,
                             "settled_rss_pages": settled_rss, "settled_fd_count": settled_fds,
                             "settled_children": settled_children,
                             "helper_pids": [record["pid"] for record in records]}
        (output / "repeated-resource-samples.json").write_text(
            json.dumps(resource_evidence, indent=2) + "\n", encoding="utf-8")
        report("helper-repeated-request-resources", True,
               f"unique_starts={len(records)} active_samples={len(active_children)} warmup={warmup_count} "
               f"asan={asan} RSS_budget={rss_budget_pages}pages post-warmup="
               f"{min(measured_rss)}..{max(measured_rss)} fd_count={min(measured_fds)}..{max(measured_fds)}; "
               "raw samples=repeated-resource-samples.json")
        actual = repeated.finish("Escape")[:2]
        require(actual == (b"", 1), f"repeated cancellation changed Escape contract: {actual}")
        records = read_log(output / "repeated.jsonl")
        observed = set(repeated_observed_groups)
        require(len(records) >= 200 and len(observed) == len(records),
                f"not every repeated helper group was observed at cancellation: "
                f"starts={len(records)}, timely_groups={len(observed)}")
        report("helper-repeated-group-cleanup", True,
               f"{len(records)} recorded starts; every group observed within its cancellation bound")
    finally:
        repeated.close()
    # Repeated no-preview sampling reports observable process state without claiming an absence proof.
    no_preview = invoke(str(binary), [], b"steady\n", base_env)
    try:
        rss, fd_counts, child_samples, cpu_ticks = [], [], [], []
        for _ in range(12):
            proc = Path("/proc") / str(no_preview.process.pid)
            rss.append(int((proc / "statm").read_text().split()[1]))
            fd_counts.append(len(list((proc / "fd").iterdir())))
            child_samples.append((proc / "task" / str(no_preview.process.pid) / "children").read_text().strip())
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            cpu_ticks.append(int(stat[11]) + int(stat[12]))
            time.sleep(.05)
        require(not any(child_samples), f"no-preview process had child processes: {child_samples}")
        require(max(rss) - min(rss) <= 64 and max(fd_counts) - min(fd_counts) <= 1 and
                max(cpu_ticks) - min(cpu_ticks) <= 2,
                f"no-preview process grew or used excess idle CPU: rss={rss}, fds={fd_counts}, cpu_ticks={cpu_ticks}")
        report("no-preview-process-sampling", True,
               f"samples=12 rss_pages={min(rss)}..{max(rss)} fd_count={min(fd_counts)}..{max(fd_counts)} cpu_ticks={cpu_ticks[0]}..{cpu_ticks[-1]} children=none observed")
    finally:
        no_preview.close()

    # Destroying an embedding parent while a helper is blocked must not strand the menu.
    xlib, display, parent = parent_window(520, 150)
    destroyed = Menu(str(binary), [*menu_options, "-P", str(helper), "-l", "5", "-w", hex(parent)],
                     b"target\n", helper_env(base_env, output / "destroy-window.jsonl", "no-read"))
    try:
        destroyed.wait_window()
        startup_deadline = time.monotonic() + TIMEOUT
        while not read_log(output / "destroy-window.jsonl") and time.monotonic() < startup_deadline:
            require(destroyed.process.poll() is None,
                    f"menu exited before destruction-test helper startup: {destroyed.stderr_text()}")
            time.sleep(.005)
        require(read_log(output / "destroy-window.jsonl"),
                "destruction test did not observe a helper start before destroying its parent")
        destroy_started = time.monotonic()
        xlib.XDestroyWindow(display, parent)
        xlib.XSync(display, 0)
        try:
            code = destroyed.process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise CheckFailure("destroyed parent left menu alive during a hanging helper") from exc
        report("helper-window-destruction", True,
               f"menu exited with status {code} after parent destruction")
        records = read_log(output / "destroy-window.jsonl")
        require(records, "destroyed-window helper never started")
        pgid = records[0]["pgid"]
        live, zombies = observe_group(pgid, destroy_started)
        require(not live, f"window destruction left helper group {pgid} alive: {live}")
        report("helper-window-destruction-group", True, f"no live group members; zombies={zombies}")
    finally:
        destroyed.close()
        xlib.XCloseDisplay(display)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helpers-only", "--helper-only", dest="helper_only", action="store_true",
                        help="run async-helper cases without the unrelated no-preview comparison suite")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--token-mode", action="store_true",
                        help="add -F to helper cases, avoiding fuzzy-match sanitizer regressions")
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="directory for per-process stderr and cleanup records (default: OUTPUT/diagnostics)")
    parser.add_argument("--isolated-display", action="store_true",
                        help="confirm DISPLAY is a disposable isolated X server, never a user's display")
    args = parser.parse_args()
    require(args.isolated_display, "refusing X11 interaction without --isolated-display confirmation")
    require(os.environ.get("DISPLAY"), "DISPLAY is unset; start an isolated X server")
    paths = [("binary", args.binary)] + ([] if args.baseline is None else [("baseline", args.baseline)])
    require(args.helper_only or args.baseline is not None,
            "--baseline is required unless --helper-only is selected")
    require(not args.token_mode or args.helper_only,
            "--token-mode is only valid with --helper-only")
    for label, path in paths:
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                f"{label} must be an executable absolute path: {path}")
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    require(not any(args.output.iterdir()),
            f"output directory must be empty to avoid stale test artifacts: {args.output}")
    global DIAGNOSTICS_DIR, REPORT_DIR
    DIAGNOSTICS_DIR = (args.diagnostics_dir or (args.output / "diagnostics")).resolve()
    REPORT_DIR = args.output
    for name in ("xdotool", "xwininfo", "import", "convert"):
        tool(name)
    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    helper = args.output / "preview-helper.py"
    helper_log = args.output / "helper.jsonl"
    make_helper(helper)
    preview_env = helper_env(env, helper_log)

    # Invalid preview CLI must fail before display access or a keyboard grab.
    invalid = [
        ["-P"], ["-P", "relative", "-l", "12"], ["-P", "/synthetic"],
        ["-P", "/synthetic", "-l", "0"], ["-P", "/synthetic", "-l", "-1"],
        ["-P", "/synthetic", "-l", "12x"],
        ["-P", "/synthetic", "-l", "4294967296"],
        ["-P", "/synthetic", "-l", "-18446744073709551615"],
    ]
    for options in invalid:
        result = run_instrumented([str(args.binary), *options], input="alpha\n",
                                  env={**os.environ, "DISPLAY": ""})
        require(result.returncode == 1 and not result.stdout and result.stderr.startswith("usage:"),
                f"invalid preview CLI did not fail before opening display: {options}")
    report("preview-cli-validation", True, f"{len(invalid)} invalid invocations rejected before display access")
    if args.helper_only:
        protocol_cases(args.binary, helper, args.output, env, args.token_mode)
        check_diagnostics()
        return 0

    ordinary = b"alpha\nAlpine\nbeta\ngamma\ndelta\nepsilon\n" + b"long-label-" + b"x" * 160 + b"\n"
    comparisons = [
        ("horizontal-empty-enter", [], ordinary, [], "Return", b"alpha\n", 0),
        ("horizontal-down-enter", [], ordinary, ["Down", "Down"], "Return", b"beta\n", 0),
        ("horizontal-escape", [], ordinary, ["Down"], "Escape", b"", 1),
        ("horizontal-fuzzy-filter", [], ordinary, ["a", "l", "p", "h", "a"], "Return", b"alpha\n", 0),
        ("vertical-navigation", ["-l", "4"], ordinary, ["Down", "Down"], "Return", b"beta\n", 0),
        ("vertical-no-fuzzy-filter", ["-l", "4", "-F"], ordinary, ["a", "l", "p", "h", "a"], "Return", b"alpha\n", 0),
        ("case-insensitive-filter", ["-l", "4", "-i"], ordinary, ["A", "L", "P", "H", "A"], "Return", b"alpha\n", 0),
        ("fuzzy-subsequence", ["-l", "4"], ordinary, ["a", "p", "h", "a"], "Return", b"alpha\n", 0),
        ("token-rejects-subsequence", ["-l", "4", "-F"], ordinary, ["a", "p", "h", "a"], "Return", b"apha\n", 0),
        ("token-case-insensitive", ["-l", "4", "-F", "-i"], ordinary, ["A", "L", "P", "H", "A"], "Return", b"alpha\n", 0),
        ("empty-list-escape", ["-l", "3"], b"", [], "Escape", b"", 1),
        ("short-list-enter", ["-l", "8"], b"one\n", [], "Return", b"one\n", 0),
        ("explicit-monitor", ["-l", "4", "-m", "0"], ordinary, ["Down"], "Return", b"Alpine\n", 0),
        ("vertical-page-selection", ["-l", "2"], ordinary, ["Next"], "Return", b"beta\n", 0),
        ("vertical-home-selection", ["-l", "2"], ordinary, ["Down", "Home"], "Return", b"alpha\n", 0),
        ("long-label-enter", [], ordinary, ["End"], "Return",
         b"long-label-" + b"x" * 160 + b"\n", 0),
    ]
    for name, options, data, keys, final, expected, expected_status in comparisons:
        results = []
        appearance = []
        for executable in (args.baseline, args.binary):
            menu = invoke(str(executable), options, data, env)
            try:
                menu.key(*keys)
                if name in ("horizontal-empty-enter", "vertical-navigation"):
                    image = args.output / f"{name}-{len(results)}.png"
                    menu.screenshot(image)
                    appearance.append(image)
                results.append(menu.finish(final)[:2])
            finally:
                menu.close()
        equal = results[0] == results[1]
        correct = results[0] == (expected, expected_status)
        report(name, equal and correct,
               f"baseline={results[0]!r}, binary={results[1]!r}, expected={(expected, expected_status)!r}")
        if appearance:
            difference = run([tool("convert"), *map(str, appearance), "-compose",
                              "difference", "-composite", "-format", "%[fx:maxima]", "info:"])
            require(difference.returncode == 0 and float(difference.stdout) == 0,
                    f"no-preview appearance changed: {appearance}")
            report(name + "-appearance", True, "baseline/new screenshots are pixel-identical")

    fixture_data = b"red\ngreen\nblue\n"
    for name, options, keys, final, expected, status in [
        ("preview-enter", [], ["Down"], "Return", b"green\n", 0),
        ("preview-escape", [], [], "Escape", b"", 1),
        ("preview-no-match-enter", [], ["z"], "Return", b"z\n", 0),
        ("preview-fuzzy-subsequence", [], ["g", "n"], "Return", b"green\n", 0),
        ("preview-token-subsequence", ["-F"], ["g", "n"], "Return", b"gn\n", 0),
        ("preview-case-insensitive", ["-i"], ["R", "E", "D"], "Return", b"red\n", 0),
        ("preview-tab", [], ["Down", "Tab"], "Return", b"green\n", 0),
        ("preview-ctrl-return", [], ["ctrl+Return", "Down"], "Return", b"red\ngreen\n", 0),
        ("preview-shift-return", [], ["g", "n"], "shift+Return", b"gn\n", 0),
        ("preview-long-label", [], ["End"], "Return", b"x" * 200 + b"\n", 0),
    ]:
        data = fixture_data if name != "preview-long-label" else fixture_data + b"x" * 200 + b"\n"
        menu = invoke(str(args.binary), ["-P", str(helper), "-l", "5", *options], data, preview_env)
        try:
            menu.key(*keys)
            actual = menu.finish(final)[:2]
            report(name, actual == (expected, status), f"stdout/status={actual!r}")
        finally:
            menu.close()
    preview_cases = [("preview-geometry", ["-l", "5", "-m", "0"], fixture_data),
                     ("preview-empty-matches", ["-l", "5"], fixture_data),
                     ("preview-empty-input", ["-l", "5"], b""),
                     ("preview-one-item", ["-l", "5"], b"red\n"),
                     ("preview-bottom", ["-b", "-l", "5"], fixture_data),
                     ("preview-clamped-rows", ["-l", "1000"], fixture_data),
                     ("preview-max-rows", ["-l", "4294967295"], fixture_data),
                     ("preview-long-prompt", ["-l", "5", "-p", "P" * 300], fixture_data)]
    screenshot_paths = []
    for name, options, data in preview_cases:
        menu = invoke(str(args.binary), ["-P", str(helper), *options], data, preview_env)
        try:
            before = args.output / f"{name}-before.png"
            time.sleep(.15)
            menu.screenshot(before)
            geometry = menu.info()
            if name == "preview-empty-matches":
                menu.key("z")
                time.sleep(.1)
            elif name == "preview-bottom":
                root = run([tool("xwininfo"), "-root"])
                match = re.search(r"Height:\s+(\d+)", root.stdout)
                require(match is not None, "cannot read root screen height")
                require(geometry["Absolute upper-left Y"] + geometry["Height"] == int(match.group(1)),
                        f"-b menu is not bottom-positioned: {geometry}")
            elif name in ("preview-clamped-rows", "preview-max-rows"):
                row_height = preview_height // 6
                root = run([tool("xwininfo"), "-root"])
                screen_height = int(re.search(r"Height:\s+(\d+)", root.stdout).group(1))
                require(row_height > 0 and geometry["Height"] <= screen_height,
                        "large requested row count was not clamped to the screen")
            after = args.output / f"{name}-after.png"
            menu.screenshot(after)
            after_geometry = menu.info()
            require(geometry["Height"] == after_geometry["Height"], "preview body height changed during filtering")
            if name == "preview-geometry":
                preview_height = geometry["Height"]
            screenshot_paths.extend((before, after))
            if name == "preview-empty-matches":
                width, height = geometry["Width"], geometry["Height"]
                row_height = height // 6
                pane_x = (width - 1) * 2 // 5 + 1
                visible, _ = region_signature(before, pane_x, row_height,
                                              width - pane_x, height - 2 * row_height)
                cleared, _ = region_signature(after, pane_x, row_height,
                                              width - pane_x, height - 2 * row_height)
                report("preview-zero-matches-clear", visible > 0 and cleared == 0,
                       f"right-pane pixel deviation {visible} -> {cleared}")
            if name == "preview-geometry":
                height = geometry["Height"]
                require(height > 20 and height % 6 == 0, f"unexpected input/body height: {geometry}")
                width = geometry["Width"]
                require(width >= 300, f"display too narrow for pane geometry assertion: {width}")
                menu.key("Down")
                time.sleep(.15)
                menu.screenshot(after)
                y = height // 6 + (height // 6) // 2
                first = pixel(before, 1, y)
                second = pixel(after, 1, y)
                report("preview-selection-pixel",
                       first == "srgb(0,85,119)" and second == "srgb(34,34,34)",
                       f"sample (1,{y}) changed {first} -> {second}; screenshots: {before}, {after}")
                require(pixel(after, 1, y + height // 6) == "srgb(0,85,119)",
                        "selected highlight did not move to the second item")
                divider_x = (width - 1) * 2 // 5
                row_y = height // 2
                left, divider, right = (pixel(before, x, row_y)
                                         for x in (divider_x - 1, divider_x, divider_x + 1))
                ratio = divider_x / width
                report("preview-divider-ratio",
                       .32 <= ratio <= .48 and (left != divider or divider != right),
                       f"boundary x≈{divider_x}/{width} ({ratio:.2f}); pixels {left}, {divider}, {right}")
                pane_x = divider_x + 1
                row_height = height // 6
                region = (pane_x, row_height, width - pane_x, height - row_height)
                short, short_signature = region_signature(before, *region)
                multiline, multiline_signature = region_signature(after, *region)
                report("preview-visible-text", short > 0 and multiline > 0,
                       f"right-pane pixel deviations: short={short}, multiline={multiline}")
                report("preview-selection-content", short_signature != multiline_signature,
                       "navigation changes rendered pixels strictly inside the right pane")
                blank, _ = region_signature(after, pane_x, 3 * row_height,
                                             width - pane_x, row_height)
                paragraph, _ = region_signature(after, pane_x, 4 * row_height,
                                                 width - pane_x, row_height)
                report("preview-multiline-blank-line", blank == 0 and paragraph > 0,
                       f"blank row deviation={blank}, following paragraph={paragraph}")
                menu.key("Down")
                time.sleep(.15)
                unicode_image = args.output / "preview-unicode.png"
                menu.screenshot(unicode_image)
                screenshot_paths.append(unicode_image)
                unicode, unicode_signature = region_signature(unicode_image, *region)
                report("preview-unicode-content",
                       unicode > 0 and unicode_signature not in (short_signature, multiline_signature),
                       f"third selection renders a distinct nonblank Unicode preview: {unicode_image}")
                menu.key("Up")
                time.sleep(.15)
                menu.screenshot(after)
                _, restored_signature = region_signature(after, *region)
                report("preview-return-content", restored_signature == multiline_signature,
                       "returning to the previous selection restores its rendered preview")
            report(name, True, f"geometry={geometry}; screenshots: {before}, {after}")
        finally:
            menu.close()

    menu = invoke(str(args.binary), ["-l", "5"], fixture_data, env)
    try:
        clamped_height = menu.info()["Height"]
        report("preview-requested-rows", preview_height > clamped_height,
               f"preview height={preview_height}, no-preview short-list height={clamped_height}")
    finally:
        menu.close()

    # Use a real X11 parent to ensure requested rows clamp to its available height.
    xlib, display, parent = parent_window(520, 150)
    try:
        menu = invoke(str(args.binary), ["-P", str(helper), "-l", "100", "-w", hex(parent)], fixture_data, preview_env)
        try:
            geometry = menu.info()
            require(geometry["Width"] == 520 and geometry["Height"] <= 150,
                    f"embedded parent constraints ignored: {geometry}")
            screenshot = args.output / "preview-embedded-parent.png"
            menu.screenshot(screenshot)
            screenshot_paths.append(screenshot)
            report("embedded-parent-clamp", True, f"geometry={geometry}; screenshot: {screenshot}")
            menu.close()
            menu = invoke(str(args.binary),
                          ["-P", str(helper), "-b", "-l", "100", "-w", hex(parent)],
                          fixture_data, preview_env)
            bottom = menu.info()
            require(bottom["Absolute upper-left Y"] + bottom["Height"] == 155,
                    f"embedded bottom placement ignored parent bounds: {bottom}")
            report("embedded-parent-bottom", True, f"geometry={bottom}")
        finally:
            menu.close()
    finally:
        xlib.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        xlib.XDestroyWindow(display, parent)
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XCloseDisplay(display)
    # An undersized parent must fail instead of producing unreadable panes.
    xlib, display, parent = parent_window(20, 20)
    try:
        menu = Menu(str(args.binary), ["-P", str(helper), "-l", "3", "-w", hex(parent)],
                    fixture_data, preview_env)
        try:
            try:
                status = menu.process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired as exc:
                raise CheckFailure("impossible embedded dimensions did not fail promptly") from exc
            require(status != 0, "impossible embedded dimensions unexpectedly opened a menu")
            report("impossible-parent-dimensions", True,
                   f"fixture exited {status}: {menu.stderr_text().strip()}")
        finally:
            menu.close()
    finally:
        xlib.XDestroyWindow(display, parent)
        xlib.XCloseDisplay(display)

    protocol_cases(args.binary, helper, args.output, env)
    screenshot_paths.extend(str(path) for path in sorted(args.output.glob("status-*.png")))
    check_diagnostics()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (CheckFailure, OSError, ValueError) as error:
        RESULTS.append(f"FAIL: {error}")
        print(f"FAIL: {error}", file=sys.stderr)
        if DIAGNOSTIC_ERRORS:
            print("SANITIZER DIAGNOSTICS: " + "; ".join(DIAGNOSTIC_ERRORS), file=sys.stderr)
        sys.exit(1)
    finally:
        if REPORT_DIR is not None:
            (REPORT_DIR / "report.txt").write_text("\n".join(RESULTS) + "\n", encoding="utf-8")
            (REPORT_DIR / "results.json").write_text(json.dumps(RESULTS, indent=2) + "\n",
                                                       encoding="utf-8")
            (REPORT_DIR / "group-observations.json").write_text(
                json.dumps(GROUP_OBSERVATIONS, indent=2) + "\n", encoding="utf-8")
