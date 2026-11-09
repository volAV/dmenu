#!/usr/bin/env python3
"""Exercise installed clipmenu restoration through real dmenu on owned Xvfb."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import shutil
import stat
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
USER = "syntheticUSER"
LABELS = [b'quoted "; $(touch "$FIXTURE_OUT/forbidden-touch")', "UTF-8 café λ".encode()]
PAYLOADS = ['first line\n\nquoted "; $(not-a-command) café\n'.encode(),
            "雪だるま\n\nend\n\n".encode()]


def snapshot(root):
    result = {}
    for path in [root, *root.rglob("*")]:
        info = path.lstat()
        value = (info.st_mode, info.st_uid, info.st_gid, info.st_mtime_ns)
        if stat.S_ISREG(info.st_mode):
            value += (path.read_bytes(),)
        elif stat.S_ISLNK(info.st_mode):
            value += (os.readlink(path),)
        result[str(path.relative_to(root))] = value
    return result


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def command(argv, env, timeout=5):
    return subprocess.run(argv, env=env, capture_output=True, timeout=timeout)


def stop(proc):
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--xvfb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name, path in (("launcher", args.launcher), ("binary", args.binary), ("xvfb", args.xvfb)):
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                f"--{name} must be an executable absolute path")
    require(args.output.is_absolute(), "--output must be an absolute path")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not any(args.output.iterdir()), "--output must be an empty directory")
    args.output = args.output.resolve()
    tools = ("bash", "sort", "cut", "awk", "cksum", "cat", "stat", "id", "xdotool", "xwininfo")
    paths = {tool: shutil.which(tool) for tool in tools}
    require(all(paths.values()), "missing required launcher or X11 utility")
    summary = {"operations": [], "cases": []}
    xvfb = None
    menus = []
    with tempfile.TemporaryDirectory(prefix="clipmenu-launcher-") as temporary:
        base = Path(temporary)
        home, cachebase, runtime, pathdir = (base / n for n in ("home", "cache", "runtime", "path"))
        for directory in (home, cachebase, runtime, pathdir):
            directory.mkdir(mode=0o700)
        cachedir = cachebase / f"clipmenu.6.{USER}"
        cachedir.mkdir(mode=0o700)
        index = cachedir / "line_cache"
        index.write_bytes(b"2 " + LABELS[0] + b"\n1 " + LABELS[1] + b"\n")
        for label, payload in zip(LABELS, PAYLOADS):
            checksum = subprocess.run([paths["bash"], "-c", 'IFS= read -r s; cksum <<< "$s"'],
                                      input=label + b"\n", capture_output=True, check=True,
                                      env={"PATH": os.path.dirname(paths["cksum"]), "HOME": str(home), "USER": USER}).stdout.decode().strip()
            (cachedir / checksum).write_bytes(payload)
        for number, payload in enumerate(PAYLOADS):
            (args.output / f"expected-{number}.bin").write_bytes(payload)
        xsel_log = args.output / "xsel.jsonl"
        xsel = base / "fake-xsel"
        xsel.write_text("#!/usr/bin/python3\nimport json,os,sys\n"
                        "if sys.argv[1:] not in [['--logfile','/dev/null','-i','--clipboard'],['--logfile','/dev/null','-i','--primary']]: sys.exit(91)\n"
                        "data=sys.stdin.buffer.read()\nroot=os.environ['FIXTURE_OUT']\n"
                        "n=len([p for p in os.listdir(root) if p.startswith('xsel-') and p.endswith('.bin')])\n"
                        "open(os.path.join(root,f'xsel-{n}.bin'),'wb').write(data)\n"
                        "open(os.path.join(root,'xsel.jsonl'),'a').write(json.dumps(sys.argv[1:])+'\\n')\n")
        xsel.chmod(0o700)
        (pathdir / "xsel").symlink_to(xsel)
        (pathdir / "touch").write_text("#!/usr/bin/python3\nimport pathlib,sys\npathlib.Path(sys.argv[1]).touch()\n")
        (pathdir / "touch").chmod(0o700)
        helper_log = args.output / "helper.jsonl"
        helper = args.output / "helper-probe.py"
        helper.write_text(f"#!{os.sys.executable}\nimport json,subprocess,sys\nlabel=sys.stdin.buffer.read()\n"
                          f"p=subprocess.run([{str(ROOT / 'clipmenu-preview')!r}],input=label,capture_output=True)\n"
                          f"open({str(helper_log)!r},'ab').write(json.dumps({{'argv':sys.argv[1:],'label':label.hex(),'payload':p.stdout.hex(),'status':p.returncode}}).encode()+b'\\n')\n"
                          "sys.stdout.buffer.write(p.stdout)\nsys.exit(p.returncode)\n")
        helper.chmod(0o700)
        for tool, source in paths.items():
            (pathdir / tool).symlink_to(source)
        env = {"PATH": str(pathdir), "HOME": str(home), "USER": USER, "CM_DIR": str(cachebase),
               "CM_HISTLENGTH": "8", "CM_LAUNCHER": str(args.binary), "CM_OUTPUT_CLIP": "1",
               "XDG_RUNTIME_DIR": str(runtime), "TMPDIR": str(base), "LC_ALL": "C.UTF-8",
               "FIXTURE_OUT": str(args.output)}
        before_cache = snapshot(cachebase)
        xvfb_err = (args.output / "xvfb.stderr").open("wb")
        try:
            xvfb = subprocess.Popen([str(args.xvfb), "-displayfd", "1", "-nolisten", "tcp",
                                     "-noreset", "-extension", "GLX", "-screen", "0", "1024x768x24"],
                                    stdout=subprocess.PIPE, stderr=xvfb_err,
                                    env={"PATH": str(pathdir), "HOME": str(home)},
                                    start_new_session=True)
            require(select.select([xvfb.stdout], [], [], 5)[0], "Xvfb startup timed out")
            display = xvfb.stdout.readline().decode().strip()
            require(display.isdigit() and xvfb.poll() is None, "owned Xvfb failed to allocate display")
            env["DISPLAY"] = ":" + display
            env["XAUTHORITY"] = str(base / "no-authority")
            summary["operations"].append("started owned Xvfb with -displayfd -nolisten tcp -noreset")
            cases = (("documented-12", ["-i", "-p", "Clipboard", "-P",
                                       str(ROOT / "clipmenu-preview"), "-l", "12"], 12, None, 0),
                     ("explicit-12", ["-i", "-p", "Clipboard", "-P", str(helper), "-l", "12"], 12, None, 0),
                     ("default-8", ["-i", "-p", "Clipboard", "-P", str(helper)], 8, "Down", 1))
            for name, forwarded, rows, key, selected in cases:
                stdout_path, stderr_path = args.output / f"{name}.stdout", args.output / f"{name}.stderr"
                with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                    proc = subprocess.Popen([paths["bash"], str(args.launcher), *forwarded], stdin=subprocess.DEVNULL,
                                            stdout=out, stderr=err, env=env, cwd=base,
                                            start_new_session=True)
                    menus.append(proc)
                    deadline, window = time.monotonic() + 5, b""
                    while time.monotonic() < deadline and proc.poll() is None:
                        found = command(["xdotool", "search", "--onlyvisible", "--class", "dmenu"], env)
                        if found.returncode == 0 and found.stdout.strip():
                            window = found.stdout.splitlines()[0]
                            break
                        time.sleep(0.03)
                    require(window, f"{name}: dmenu window did not appear")
                    geometry = command(["xwininfo", "-id", window.decode()], env).stdout.decode()
                    height = int(re.search(r"Height: (\d+)", geometry).group(1))
                    if key:
                        command(["xdotool", "key", key], env)
                    if name == "documented-12":
                        time.sleep(0.2)
                    else:
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            records = [json.loads(line) for line in helper_log.read_text().splitlines()] if helper_log.exists() else []
                            if records and bytes.fromhex(records[-1]["label"]) == LABELS[selected] + b"\n":
                                break
                            time.sleep(0.02)
                        else:
                            raise RuntimeError(f"{name}: selected helper did not finish")
                    command(["xdotool", "key", "Return"], env)
                    proc.wait(timeout=5)
                    menus.remove(proc)
                require(proc.returncode == 0, f"{name}: launcher exited {proc.returncode}")
                require(stdout_path.read_bytes() == PAYLOADS[selected],
                        f"{name}: optional CM_OUTPUT_CLIP returned the wrong bytes")
                require(stderr_path.read_bytes() == b"", f"{name}: unexpected launcher diagnostics")
                summary["cases"].append({"name": name, "rows": rows, "height": height, "selection": selected})
            heights = [case["height"] for case in summary["cases"]]
            require(heights[0] == heights[1] and heights[1] * 9 == heights[2] * 13,
                    "effective body geometry is not 12 rows versus default 8")
            records = [json.loads(line) for line in helper_log.read_text().splitlines()]
            require({bytes.fromhex(r["label"]) for r in records} == {label + b"\n" for label in LABELS},
                    "both selected labels must reach the real helper")
            for record in records:
                selected = [label + b"\n" for label in LABELS].index(bytes.fromhex(record["label"]))
                require(record["argv"] == [], "helper received arguments")
                require(bytes.fromhex(record["payload"]) == PAYLOADS[selected] and record["status"] == 0,
                        "real helper returned bytes different from cached payload")
            argv_log = [json.loads(line) for line in xsel_log.read_text().splitlines()]
            require(argv_log == [["--logfile", "/dev/null", "-i", "--clipboard"],
                                 ["--logfile", "/dev/null", "-i", "--primary"]] * 3,
                    f"unexpected fake xsel arguments: {argv_log}")
            streams = [(args.output / f"xsel-{n}.bin").read_bytes() for n in range(6)]
            expected = [PAYLOADS[case["selection"]] for case in summary["cases"] for _ in range(2)]
            require(streams == expected, "clipboard/primary restoration differs from original payload bytes")
            require(snapshot(cachebase) == before_cache, "launcher/helper modified cache fixtures")
            require(not (args.output / "forbidden-touch").exists(), "literal label executed as shell")
            summary["operations"] += ["checked actual helper stdin/stdout and no-argument protocol",
                                       "checked fake xsel argv and exact clipboard/primary payload streams"]
            summary["payloads"] = [{"bytes": len(p), "sha256": digest(p)} for p in PAYLOADS]
            summary["restored_streams"] = [{"bytes": len(p), "sha256": digest(p)} for p in streams]
            summary["cache"] = {"index_hex": index.read_bytes().hex(), "entries": sorted(p.name for p in cachedir.iterdir())}
            (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            print(json.dumps(summary, indent=2))
        finally:
            for proc in menus:
                stop(proc)
            stop(xvfb)
            xvfb_err.close()


if __name__ == "__main__":
    main()
