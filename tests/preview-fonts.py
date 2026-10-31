#!/usr/bin/env python3
"""Compare real-font preparation and input responsiveness on an owned Xvfb."""
import argparse
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import time
from pathlib import Path


def need(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_helpers():
    path = Path(__file__).with_name("preview-text.py")
    spec = importlib.util.spec_from_file_location("preview_text_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_probe(source, output):
    shutil.copyfile(Path(__file__).with_name("preview-text.c"),
                    source / "tests" / "preview-text.c")
    command = ["cc", "-std=c99", "-pedantic", "-Wall", "-Os", "-finstrument-functions",
               "-I/usr/include/freetype2", "-D_DEFAULT_SOURCE", "-D_BSD_SOURCE",
               "-D_XOPEN_SOURCE=700", "-D_POSIX_C_SOURCE=200809L", '-DVERSION="5.4"',
               "-DXINERAMA", "-o", str(output), "tests/preview-text.c", "drw.c", "util.c",
               "-lX11", "-lXinerama", "-lfontconfig", "-lXft", "-lm"]
    result = subprocess.run(command, cwd=source, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"probe build failed ({result.returncode}): {result.stderr}")


def input_bytes(size):
    result = bytearray()
    scalar = 0x1000
    while len(result) + 3 <= size:
        if 0xd800 <= scalar <= 0xdfff:
            scalar = 0xe000
        result.extend(chr(scalar).encode("utf-8"))
        scalar += 1
    result.extend(b" " * (size - len(result)))
    return bytes(result)


def samples(values):
    ordered = sorted(values)
    return {"n": len(values), "median": statistics.median(values),
            "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
            "max": max(values)}


def read_records(path):
    measurements, steps = [], []
    for line in path.read_text().splitlines():
        fields = line.split(",")
        if fields[0] == "M" and len(fields) == 5 and fields[3] == "1":
            measurements.append({"wall_ns": int(fields[1]), "cpu_ns": int(fields[2]),
                                 "width": int(fields[4])})
        elif fields[0] == "S":
            steps.append({"wall_ns": int(fields[1]), "cpu_ns": int(fields[2]),
                          "work": int(fields[3]), "pending": bool(int(fields[4]))})
    return measurements, steps


def run_case(helpers, binary, helper, rawfile, directory, action, size, sample):
    profile = directory / f"{binary.stem}-{size}-{action}-{sample}.jsonl"
    inherited_env = {key: value for key, value in os.environ.items()
                     if key not in ("PREVIEW_TEXT_HELPER_CHILD", "PREVIEW_TEXT_HELPER",
                                    "XAUTHORITY")}
    env = {**inherited_env, "DISPLAY": os.environ["DISPLAY"], "LC_ALL": "C.UTF-8",
           "PREVIEW_PROFILE": str(profile), "PREVIEW_DATA": str(rawfile)}
    menu = helpers.Menu(str(binary), helper, b"target\n", env,
                        ["-F", "-fn", "monospace:size=12"], directory,
                        launch_prefix=("--profile",))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if profile.is_file() and profile.stat().st_size:
                active = any(len(parts := line.split(b",")) == 5 and
                             parts[0] == b"M" and parts[3] == b"1"
                             for line in profile.read_bytes().splitlines())
                if active:
                    break
            need(menu.proc.poll() is None, f"menu exited before active preparation: {menu.stderr()}")
            time.sleep(0.001)
        else:
            raise RuntimeError("no real Xft measurement observed while preparation was pending")
        records, steps = read_records(profile)
        need(records, "active preparation marker lacked a real Xft measurement")
        injection_started = time.monotonic_ns()
        subprocess.run(["xdotool", "key", "--clearmodifiers", action], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        injection_delivered = time.monotonic_ns()
        status = menu.proc.wait(timeout=20)
        exited = time.monotonic_ns()
        # Normal exit flushes buffered per-measurement records after the active marker.
        measurements, all_steps = read_records(profile)
        need(status == (1 if action == "Escape" else 0),
             f"unexpected {action} exit status {status}: {menu.stderr()}")
        need(all_steps and all(step["work"] <= 256 for step in all_steps),
             "step instrumentation missing or work cap exceeded")
        need(all(step["pending"] for step in all_steps[:-1]),
             "step records did not show preparation pending until completion")
        return {"source": binary.stem, "raw_bytes": size, "action": action, "sample": sample,
                "escape_or_return_wall_ns": exited - injection_started,
                "injector_wall_ns": injection_delivered - injection_started,
                "measurements": measurements, "steps": all_steps,
                "prep_wall_ns": sum(step["wall_ns"] for step in all_steps),
                "prep_cpu_ns": sum(step["cpu_ns"] for step in all_steps),
                "metric_wall": samples([record["wall_ns"] for record in measurements]),
                "metric_cpu": samples([record["cpu_ns"] for record in measurements]),
                "step_wall": samples([step["wall_ns"] for step in all_steps]),
                "step_cpu": samples([step["cpu_ns"] for step in all_steps])}
    finally:
        menu.close()


def run_complete(helpers, binary, helper, rawfile, directory, size):
    profile = directory / f"{binary.stem}-{size}-complete.jsonl"
    inherited_env = {key: value for key, value in os.environ.items()
                     if key not in ("PREVIEW_TEXT_HELPER_CHILD", "PREVIEW_TEXT_HELPER",
                                    "XAUTHORITY")}
    env = {**inherited_env, "DISPLAY": os.environ["DISPLAY"], "LC_ALL": "C.UTF-8",
           "PREVIEW_PROFILE": str(profile), "PREVIEW_DATA": str(rawfile)}
    menu = helpers.Menu(str(binary), helper, b"target\n", env,
                        ["-F", "-fn", "monospace:size=12"], directory,
                        launch_prefix=("--profile",))
    try:
        started = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if profile.is_file() and profile.stat().st_size:
                if started is None:
                    lines = profile.read_bytes().splitlines()
                    if any(len(parts := line.split(b",")) == 5 and
                           parts[0] == b"M" and parts[3] == b"1" for line in lines):
                        started = time.monotonic_ns()
                if started is not None:
                    with profile.open("rb") as stream:
                        stream.seek(max(0, profile.stat().st_size - 256))
                        tail = stream.read().splitlines()
                    if tail and tail[-1].startswith(b"S,") and tail[-1].endswith(b",0"):
                        completed = time.monotonic_ns()
                        break
            need(menu.proc.poll() is None,
                 f"menu exited before full preparation: {menu.stderr()}")
            time.sleep(0.005)
        else:
            raise RuntimeError(f"full preparation timed out for {binary.stem}, {size} bytes")
        records, steps = read_records(profile)
        need(started is not None and steps and not steps[-1]["pending"] and
             all(step["work"] <= 256 for step in steps),
             "full preparation instrumentation exceeded the unit budget")
        subprocess.run(["xdotool", "key", "--clearmodifiers", "Escape"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        need(menu.proc.wait(timeout=5) == 1, "Escape failed after complete preparation")
        return {"source": binary.stem, "raw_bytes": size, "measurements": records, "steps": steps,
                "prep_wall_ns": completed - started,
                "prep_step_wall_ns": sum(step["wall_ns"] for step in steps),
                "prep_cpu_ns": sum(step["cpu_ns"] for step in steps),
                "metric_wall": samples([record["wall_ns"] for record in records]),
                "metric_cpu": samples([record["cpu_ns"] for record in records]),
                "step_wall": samples([step["wall_ns"] for step in steps]),
                "step_cpu": samples([step["cpu_ns"] for step in steps])}
    finally:
        menu.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", required=True, type=Path)
    parser.add_argument("--corrected-source", required=True, type=Path)
    parser.add_argument("--xvfb", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--short", action="store_true",
                        help="measure only 1 KiB and 8 KiB inputs")
    args = parser.parse_args()
    need(args.repeats >= 3, "at least three repeats are required")
    sizes = (1024, 8192) if args.short else (1024, 8192, 32768, 65536)
    need(args.xvfb.is_absolute() and args.xvfb.is_file() and os.access(args.xvfb, os.X_OK),
         "--xvfb must name the owned executable Xvfb")
    need(not args.output.exists() or not any(args.output.iterdir()), "--output must be empty")
    args.output.mkdir(parents=True, exist_ok=True)
    for command in ("xdotool",):
        need(shutil.which(command), f"missing required tool: {command}")
    helpers = load_helpers()
    binaries = []
    for name, source in (("starting-4a", args.baseline_source), ("corrected", args.corrected_source)):
        source = source.resolve()
        need((source / "dmenu.c").is_file() and (source / "config.h").is_file(),
             f"source tree lacks dmenu.c/config.h: {source}")
        binary = args.output / f"preview-text-{name}"
        compile_probe(source, binary)
        binaries.append(binary)

    display_read, display_fd = os.pipe()
    xvfb_stderr = (args.output / "xvfb.stderr").open("wb")
    xvfb = subprocess.Popen([str(args.xvfb), "-displayfd", str(display_fd), "-noreset",
                             "-screen", "0", "1024x768x24"], pass_fds=(display_fd,),
                            stdout=subprocess.DEVNULL, stderr=xvfb_stderr)
    os.close(display_fd)
    try:
        display_number = os.read(display_read, 32).decode("ascii").strip()
        need(display_number.isdigit() and xvfb.poll() is None, "owned Xvfb failed to allocate display")
    finally:
        os.close(display_read)
    os.environ["DISPLAY"] = ":" + display_number
    os.environ["LC_ALL"] = "C.UTF-8"
    helper = args.output / "font-helper.py"
    helper.write_text("#!/usr/bin/env python3\nimport os,sys\nsys.stdin.buffer.readline()\nsys.stdout.buffer.write(open(os.environ['PREVIEW_DATA'],'rb').read())\n")
    helper.chmod(0o700)
    rows, complete_rows = [], []
    try:
        for size in sizes:
            rawfile = args.output / f"input-{size}.bin"
            rawfile.write_bytes(input_bytes(size))
            for binary in binaries:
                for action in ("Escape", "Return"):
                    for sample in range(args.repeats):
                        result = run_case(helpers, binary, helper, rawfile, args.output,
                                          action, size, sample)
                        rows.append(result)
                        print(json.dumps({key: value for key, value in result.items()
                                          if key not in ("measurements", "steps")}), flush=True)
                complete = run_complete(helpers, binary, helper, rawfile, args.output, size)
                complete_rows.append(complete)
                print(json.dumps({key: value for key, value in complete.items()
                                  if key not in ("measurements", "steps")}), flush=True)
        (args.output / "samples.json").write_text(json.dumps(rows, indent=2) + "\n")
        (args.output / "complete-preparation.json").write_text(
            json.dumps(complete_rows, indent=2) + "\n")
        corrected = [row for row in rows if row["source"] == "preview-text-corrected"]
        need(all(row["escape_or_return_wall_ns"] < 100_000_000 for row in corrected),
             "corrected Escape/Return latency exceeded 100 ms")
        need(all(row["metric_wall"]["max"] < 50_000_000 for row in rows + complete_rows),
             "individual synchronous Xft measurement exceeded 50 ms; investigate drw.c")
        full_by_key = {(row["raw_bytes"], row["source"]): row for row in complete_rows}
        for size in sizes:
            old = full_by_key[size, "preview-text-starting-4a"]["measurements"]
            new = full_by_key[size, "preview-text-corrected"]["measurements"]
            need([entry["width"] for entry in old] == [entry["width"] for entry in new],
                 f"real Xft glyph widths changed for {size}-byte input")
        print(f"Saved raw per-step and per-measurement samples under {args.output}")
    finally:
        xvfb.terminate()
        xvfb.wait(timeout=5)
        xvfb_stderr.close()


if __name__ == "__main__":
    main()
