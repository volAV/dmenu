#!/usr/bin/env python3
"""Exercise install, uninstall, and dist in a disposable source copy."""
import argparse
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "LICENSE", "Makefile", "README", "arg.h", "config.def.h", "config.mk",
    "dmenu.1", "drw.h", "util.h", "dmenu_path", "dmenu_run",
    "stest.1", "clipmenu-preview", "drw.c", "dmenu.c", "stest.c", "util.c",
)
EXECUTABLES = ("dmenu", "dmenu_path", "dmenu_run", "stest", "clipmenu-preview")
MANUALS = ("dmenu.1", "stest.1")


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def make(source, *args):
    result = subprocess.run(["make", "-C", str(source), *args],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"make {' '.join(args)} failed:\n{result.stdout}{result.stderr}")


def check_install(source, stage, prefix, manprefix, version, explicit_manprefix):
    args = ["install", f"DESTDIR={stage}", f"PREFIX={prefix}"]
    if explicit_manprefix:
        args.append(f"MANPREFIX={manprefix}")
    make(source, *args)

    bindir = stage / prefix.lstrip("/") / "bin"
    mandir = stage / manprefix.lstrip("/") / "man1"
    for name in EXECUTABLES:
        installed = bindir / name
        expected = (source / name).read_bytes()
        require(installed.read_bytes() == expected, f"installed bytes differ: {installed}")
        require(stat.S_IMODE(installed.stat().st_mode) == 0o755,
                f"installed mode is not 0755: {installed}")
    for name in MANUALS:
        installed = mandir / name
        expected = (source / name).read_bytes().replace(b"VERSION", version.encode())
        require(installed.read_bytes() == expected, f"manual substitution differs: {installed}")
        require(stat.S_IMODE(installed.stat().st_mode) == 0o644,
                f"manual mode is not 0644: {installed}")

    sentinel = stage / "unrelated-sentinel"
    sentinel.write_bytes(b"keep me\n")
    intended = {bindir / name for name in EXECUTABLES}
    intended.update(mandir / name for name in MANUALS)
    make(source, "uninstall", f"DESTDIR={stage}", f"PREFIX={prefix}",
         f"MANPREFIX={manprefix}")
    require(sentinel.read_bytes() == b"keep me\n", "uninstall changed unrelated sentinel")
    require(all(not path.exists() for path in intended), "uninstall left an installed target")
    remaining = [path for path in stage.rglob("*") if path.is_file()]
    require(remaining == [sentinel], f"uninstall removed or left unexpected files: {remaining}")
    print(f"PASS install/uninstall: PREFIX={prefix}, MANPREFIX={manprefix}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT,
                        help="project source directory (default: repository root)")
    args = parser.parse_args()
    source = args.source.resolve()
    require((source / "Makefile").is_file(), f"no Makefile in source: {source}")
    with tempfile.TemporaryDirectory(prefix="dmenu-preview-install-") as temporary:
        base = Path(temporary)
        copy = base / "source"
        copy.mkdir()
        for name in SOURCES:
            original = source / name
            require(original.is_file(), f"required source file missing: {original}")
            shutil.copy2(original, copy / name)
        configuration = source / "config.h"
        if not configuration.exists():
            configuration = source / "config.def.h"
        shutil.copy2(configuration, copy / "config.h")
        require((copy / "config.h").read_bytes() == configuration.read_bytes(),
                "configuration was not preserved in the copy")

        make(copy, "all")
        version_match = re.search(r"^VERSION\s*=\s*(\S+)\s*$",
                                  (copy / "config.mk").read_text(), re.M)
        require(version_match is not None, "could not read VERSION from config.mk")
        version = version_match.group(1)

        prefix = "/opt/dmenu-preview-test"
        check_install(copy, base / "stage-default-man", prefix,
                      f"{prefix}/share/man", version, False)
        check_install(copy, base / "stage-override-man", prefix,
                      "/opt/dmenu-preview-man-test", version, True)

        make(copy, "dist")
        archive = copy / f"dmenu-{version}.tar.gz"
        with tarfile.open(archive, "r:gz") as bundle:
            helpers = [member for member in bundle.getmembers()
                       if member.name.rsplit("/", 1)[-1] == "clipmenu-preview"]
            require(len(helpers) == 1, f"archive contains {len(helpers)} clipmenu-preview entries")
            helper = helpers[0]
            require(stat.S_IMODE(helper.mode) == 0o755,
                    f"archive helper mode is not 0755: {oct(helper.mode)}")
            extracted = bundle.extractfile(helper)
            require(extracted is not None and extracted.read() == (copy / "clipmenu-preview").read_bytes(),
                    "archive helper bytes differ from source")
        print("PASS dist: one executable clipmenu-preview with source-identical bytes")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, tarfile.TarError) as error:
        raise SystemExit(f"FAIL preview install regression: {error}")
