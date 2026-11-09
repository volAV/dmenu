#!/usr/bin/env python3
"""Synthetic lookup checks only: never run clipmenu, xsel or an X client."""
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import uuid

HELPER = Path(__file__).resolve().parents[1] / "clipmenu-preview"
USER = "checkpoint5a-" + uuid.uuid4().hex
ENV = {k: v for k, v in os.environ.items()
       if k not in ("CM_DIR", "XDG_RUNTIME_DIR", "TMPDIR", "USER")}
ENV["USER"] = USER
REPORTS = 0


def report(message):
    global REPORTS
    REPORTS += 1
    print("PASS", message)


def checksum(label):
    data = label + b"\n"
    reference = subprocess.run(
        ["bash", "-c", 'IFS= read -r chosen_line; cksum <<< "$chosen_line"'],
        input=data, capture_output=True, check=True).stdout
    direct = subprocess.run(["cksum"], input=data, capture_output=True,
                            check=True).stdout
    assert direct == reference
    return reference.decode("ascii").rstrip("\n")


def snapshot(roots):
    result = {}
    for root in roots:
        for p in [root, *root.rglob("*")]:
            s = p.lstat()
            value = (s.st_mode, s.st_uid, s.st_gid, s.st_mtime_ns)
            if stat.S_ISREG(s.st_mode):
                try:
                    value += (p.read_bytes(),)
                except PermissionError:
                    value += (None,)  # Mode/owner/mtime still checked for unreadable fixtures.
            elif stat.S_ISLNK(s.st_mode):
                value += (os.readlink(p),)
            result[str(p)] = value
    return result


def check(label, env, expected=None, args=(), prefix=()):
    before = snapshot(ROOTS)
    p = subprocess.run([*prefix, str(HELPER), *args], input=label,
                       cwd=WORK, env={**ENV, **env}, capture_output=True, timeout=3)
    assert p.stderr == b"", (p.returncode, p.stderr)
    if expected is None:
        assert p.returncode != 0 and p.stdout == b"", (p.returncode, p.stdout)
    else:
        assert p.returncode == 0 and p.stdout == expected, p.returncode
    assert snapshot(ROOTS) == before, "helper changed fixture contents or metadata"


assert HELPER.read_bytes().startswith(b"#!/bin/sh\n")
assert os.access(HELPER, os.X_OK)
subprocess.run(["sh", "-n", str(HELPER)], check=True)
report("POSIX shell syntax and executable permissions")

# mkdir is exclusive: never reuse or remove an existing /tmp cache path.
fallback = Path("/tmp") / ("clipmenu.6." + USER)
fallback.mkdir(mode=0o700)
try:
    with tempfile.TemporaryDirectory(prefix="clipmenu-preview-test-") as directory:
        WORK = Path(directory)
        ROOTS = [WORK, fallback]
        bases = {key: WORK / key for key in ("CM_DIR", "XDG_RUNTIME_DIR", "TMPDIR")}
        caches = {}
        label = b"synthetic environment label"
        name = checksum(label)
        for key, base in bases.items():
            base.mkdir()
            cache = base / ("clipmenu.6." + USER)
            cache.mkdir(mode=0o700)
            (cache / name).write_bytes(key.encode())
            caches[key] = cache
        (fallback / name).write_bytes(b"fallback")
        values = {key: str(base) for key, base in bases.items()}
        for env, expected in [
            (values, b"CM_DIR"),
            ({k: values[k] for k in ("XDG_RUNTIME_DIR", "TMPDIR")}, b"XDG_RUNTIME_DIR"),
            ({"TMPDIR": values["TMPDIR"]}, b"TMPDIR"),
            ({}, b"fallback"),
            ({**values, "CM_DIR": ""}, None),
            ({"XDG_RUNTIME_DIR": "", "TMPDIR": values["TMPDIR"]}, None),
            ({"TMPDIR": ""}, None),
        ]:
            # Empty bases mean /clipmenu.6.$USER, not a lower-priority base.
            assert not os.path.lexists(Path("/") / ("clipmenu.6." + USER))
            check(label + b"\n", env, expected)
        report("unset-only precedence, explicit empty bases, exclusive synthetic /tmp fallback")

        cache = caches["CM_DIR"]
        env = {"CM_DIR": str(bases["CM_DIR"])}
        labels = [b"  leading and trailing  ", b"back\\slash\\",
                  b"$(touch injected); `touch injected` 'quotes' \"double\"",
                  "caf\u00e9 \u65e5\u672c\u8a9e".encode(), b"", b"-n", b"x" * 100000]
        payload = b"first\n\n" + "caf\u00e9".encode() + b"\n" + bytes(range(256)) + b"z" * 70000 + b"\n\n\n"
        for label in labels:
            file = cache / checksum(label)
            file.write_bytes(payload)
            check(label + b"\n", env, payload)
            file.unlink()
        report("Bash here-string checksum, literal labels, binary/multiline/trailing bytes, no helper cap")

        label = b"filesystem fixture"
        file = cache / checksum(label)
        check(label + b"\n", env)
        file.write_bytes(payload)
        file.unlink()
        check(label + b"\n", env)
        check(label, env)  # Protocol requires LF, not an unterminated label.
        check(label + b"\n", env, args=("unexpected",))
        for user in ("", ".", "..", "../escape", "a/b", "space user", "a\n", "$(id)", "\u00e9"):
            check(label + b"\n", {**env, "USER": user})
        missing_user = dict(ENV)
        del missing_user["USER"]
        before = snapshot(ROOTS)
        p = subprocess.run([str(HELPER)], input=label + b"\n", cwd=WORK,
                           env={**missing_user, **env}, capture_output=True, timeout=3)
        assert p.returncode != 0 and p.stdout == p.stderr == b""
        assert snapshot(ROOTS) == before
        report("missing/vacuumed entries, LF/no-argument protocol, invalid/unset USER")

        target = WORK / "target"
        target.write_bytes(payload)
        for destination in (target, WORK / "absent"):
            file.symlink_to(destination)
            check(label + b"\n", env)
            file.unlink()
        os.mkfifo(file)
        check(label + b"\n", env)
        file.unlink()
        file.mkdir()
        check(label + b"\n", env)
        file.rmdir()
        with socket.socket(socket.AF_UNIX) as sock:
            previous = Path.cwd()
            try:
                os.chdir(cache)  # AF_UNIX limits the textual address length.
                sock.bind(file.name)
            finally:
                os.chdir(previous)
            check(label + b"\n", env)
        file.unlink()
        file.write_bytes(payload)
        file.chmod(0)
        if os.geteuid() != 0:
            check(label + b"\n", env)
        else:
            print("SKIP unreadable payload: root can read mode 000")
        file.chmod(0o600)
        report("symlink/dangling payloads, FIFO, directory and socket; unreadability checked unless skipped")

        for mode in (0o701, 0o710, 0o750, 0o770, 0o777):
            cache.chmod(mode)
            check(label + b"\n", env)
        for mode in (0o700, 0o500, 0o1700):
            cache.chmod(mode)
            check(label + b"\n", env, payload)
        cache.chmod(0o700)
        other = WORK / "other"
        other.mkdir(mode=0o700)
        (other / file.name).write_bytes(payload)
        file.unlink()
        (cache / name).unlink()
        cache.rmdir()
        cache.symlink_to(other, target_is_directory=True)
        check(label + b"\n", env)
        cache.unlink()
        cache.symlink_to(WORK / "absent", target_is_directory=True)
        check(label + b"\n", env)
        cache.unlink()
        cache.write_bytes(b"not a directory")
        check(label + b"\n", env)
        cache.unlink()
        cache.mkdir(mode=0o700)
        (cache / file.name).write_bytes(payload)
        report("private directory modes, final-directory symlinks and non-directory rejection")

        if os.geteuid() == 0:
            print("SKIP foreign ownership: running as root; fakeroot may perform real privileged chown")
        elif shutil.which("fakeroot"):
            # Non-root only: fakeroot cannot grant privileged kernel chown.
            check(label + b"\n", env, prefix=(
                "fakeroot", "sh", "-c", 'chown 1 "$1" && exec "$2"',
                "ownership-fixture", str(cache)))
            report("foreign directory ownership rejected (fakeroot metadata simulation)")
        else:
            print("SKIP foreign ownership: fakeroot unavailable; no privileged chown attempted")
        report("every helper invocation preserves fixture bytes, modes, ownership and mtimes")
finally:
    shutil.rmtree(fallback)
print(f"{REPORTS} reports passed")
