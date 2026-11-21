#!/usr/bin/env python3
"""Exercise literal preview-helper PATH lookup on an owned Xvfb."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import time


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def same_pixels(first, second, crop=None):
    args = ["convert", str(first), str(second)]
    if crop:
        args += ["-crop", crop, "+repage"]
    result = subprocess.run([*args, "-compose", "difference", "-composite",
                             "-format", "%[fx:maxima]", "info:"],
                            text=True, capture_output=True, timeout=5)
    require(result.returncode == 0, f"pixel comparison failed: {result.stderr}")
    return float(result.stdout) == 0


def screenshot_match(first, second):
    require(same_pixels(first, second),
            f"screenshots differ: {first.name} vs {second.name}")


def write_helper(path, output, log=None, status=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    code = "#!" + os.sys.executable + "\nimport json, os, sys\n"
    code += "data=sys.stdin.buffer.read()\n"
    if log:
        code += ("open(" + repr(str(log)) + ", 'wb').write(json.dumps("
                 "{'argv': sys.argv[1:], 'stdin': data.hex(), "
                 "'env': dict(os.environ)}).encode())\n")
    code += f"sys.stdout.buffer.write({output!r})\nsys.exit({status})\n"
    path.write_text(code, encoding="utf-8")
    path.chmod(0o700)


def save_stderr(menu, output, name):
    (output / f"{name}.stderr").write_text(menu.stderr(), encoding="utf-8")


def main():
    require(Path(os.sys.executable).is_absolute(), "Python interpreter path must be absolute")
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "baseline", "xvfb", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    for name in ("binary", "baseline", "xvfb"):
        path = getattr(args, name)
        require(path.is_absolute() and path.is_file() and os.access(path, os.X_OK),
                f"--{name} must be an executable absolute path")
    require(args.output.is_absolute(), "--output must be an absolute path")
    args.output.mkdir(parents=True, exist_ok=True)
    require(not any(args.output.iterdir()), "--output must be empty")
    args.output = args.output.resolve()
    tools = {name: shutil.which(name) for name in ("xdotool", "import", "convert")}
    require(all(tools.values()), "missing required X11 or screenshot utility")

    spec = importlib.util.spec_from_file_location(
        "preview_scroll", Path(__file__).with_name("preview-scroll.py"))
    preview = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview)
    output = args.output
    base = output / "fixture"
    base.mkdir(mode=0o700)
    label = b'quoted "; $(printf label > forbidden-label-side-effect) caf\xc3\xa9\n'
    rendered = b"Synthetic preview PATH lookup reference\n"
    menus = []
    xvfb = None
    xvfb_err = (output / "xvfb.stderr").open("wb")
    try:
        xvfb = subprocess.Popen([str(args.xvfb), "-displayfd", "1", "-screen", "0",
                                 "1024x768x24", "-nolisten", "tcp", "-noreset",
                                 "-extension", "GLX"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=xvfb_err,
                                env={"PATH": os.pathsep.join(sorted({str(Path(p).parent) for p in tools.values()})),
                                     "HOME": str(base)}, start_new_session=True)
        require(select.select([xvfb.stdout], [], [], 5)[0], "owned Xvfb startup timed out")
        display = xvfb.stdout.readline().decode("ascii").strip()
        require(display.isdigit() and xvfb.poll() is None, "owned Xvfb failed to allocate display")
        display_env = {"DISPLAY": ":" + display, "LC_ALL": "C.UTF-8"}
        os.environ["DISPLAY"] = display_env["DISPLAY"]

        def menu(binary, name, helper, path, keys=("Return",), expected=label):
            env = {**display_env}
            if path is not None:
                env["PATH"] = path
            old_cwd = os.getcwd()
            os.chdir(base)
            try:
                instance = preview.Menu(binary, ["-P", str(helper), "-l", "2"],
                                        label, env, output)
            finally:
                os.chdir(old_cwd)
            menus.append(instance)
            time.sleep(.2)
            instance.screenshot(output / f"{name}.png")
            for key in keys:
                instance.key(key)
            instance.proc.wait(timeout=5)
            save_stderr(instance, output, name)
            require(instance.proc.returncode == (0 if "Return" in keys else 1),
                    f"{name}: unexpected exit status {instance.proc.returncode}")
            if "Return" in keys:
                require(instance.stdout() == expected, f"{name}: selected bytes changed")
            else:
                require(instance.stdout() == b"", f"{name}: Escape emitted selection")
            instance.close(output, name)
            menus.remove(instance)
            return output / f"{name}.png"

        def fixture(name, output_bytes=rendered, status=0, log=True):
            file = base / name
            logfile = output / (name.replace("/", "_") + ".json") if log else None
            write_helper(file, output_bytes, logfile, status)
            return file, logfile

        # Absolute paths bypass both a hostile PATH candidate and unset PATH.
        absolute, absolute_log = fixture("absolute-helper", log=True)
        shadow = base / "shadow"
        shadow.mkdir()
        write_helper(shadow / absolute.name, b"wrong shadow helper\n")
        reference_image = menu(args.binary, "absolute-unset-path", absolute, None)
        menu(args.binary, "absolute-shadowed-path", absolute, str(shadow))
        screenshot_match(reference_image, output / "absolute-shadowed-path.png")
        unset_image = menu(args.binary, "unset-path", "absolute-helper", None, keys=("Escape",))
        unavailable = unset_image
        # Compare the text row only: status/footer differences cannot prove text rendered.
        dimensions = subprocess.run(["convert", str(reference_image), "-format", "%w %h", "info:"],
                                    capture_output=True, text=True, check=True).stdout
        width, height = map(int, dimensions.split())
        x = (width - 1) * 2 // 5 + 1
        row = height // 3  # Search row, one text row, one status row at -l 2.
        require(not same_pixels(reference_image, unavailable, f"{width-x}x{row}+{x}+{row}"),
                "successful stdout did not produce visible preview text")
        require(json.loads(absolute_log.read_text())["stdin"] == label.hex(),
                "absolute helper did not receive exact selected label plus LF")
        record = json.loads(absolute_log.read_text())
        require(record["argv"] == [], "helper received arguments")
        require(record["env"] == {**display_env, "PATH": str(shadow)},
                "helper received unexpected environment variables or label data")
        name = "ordered-preview"
        first, first_log = fixture("first/" + name)
        second, _ = fixture("second/" + name, output_bytes=b"wrong later candidate\n", log=False)
        missing = base / "missing"
        missing.mkdir()
        directory = base / "directory"
        directory.mkdir()
        nonexec = base / "nonexec"
        (directory / name).mkdir()
        nonexec.mkdir()
        (nonexec / name).write_text("not executable\n", encoding="utf-8")
        selected_path = os.pathsep.join(map(str, (missing, directory, nonexec, first.parent, second.parent)))
        selected_image = menu(args.binary, "ordered-path", name, selected_path)
        screenshot_match(reference_image, selected_image)
        require(json.loads(first_log.read_text())["stdin"] == label.hex(),
                "first executable PATH candidate did not receive exact label")

        # Relative components and empty components are resolved from cwd.
        cwd = base / "cwd"
        (cwd / "rel").mkdir(parents=True)
        rel, rel_log = fixture("cwd/rel/relative-preview")
        current, current_log = fixture("cwd/current-preview")
        def in_cwd(binary, name, helper, path):
            env = {"DISPLAY": display_env["DISPLAY"], "LC_ALL": "C.UTF-8", "PATH": path}
            instance = preview.Menu(binary, ["-P", str(helper), "-l", "2"], label, env, output)
            menus.append(instance)
            time.sleep(.2)
            instance.screenshot(output / f"{name}.png")
            screenshot_match(reference_image, output / f"{name}.png")
            instance.key("Return")
            instance.proc.wait(timeout=5)
            save_stderr(instance, output, name)
            require(instance.proc.returncode == 0, f"{name}: menu failed")
            require(instance.stdout() == label, f"{name}: selected bytes changed")
            instance.close(output, name)
            menus.remove(instance)
        old_cwd = os.getcwd()
        os.chdir(cwd)
        try:
            in_cwd(args.binary, "relative-component", "relative-preview", "rel")
            in_cwd(args.binary, "empty-component", "current-preview", ":")
            in_cwd(args.binary, "empty-path", "current-preview", "")
        finally:
            os.chdir(old_cwd)
        require(json.loads(rel_log.read_text())["stdin"] == label.hex(), "relative PATH lookup failed")
        require(json.loads(current_log.read_text())["stdin"] == label.hex(), "empty PATH lookup failed")
        nonexec_only = menu(args.binary, "all-nonexecutable",
                            name, str(nonexec), keys=("Escape",))
        screenshot_match(unavailable, nonexec_only)

        # No PATH means no bare-name search; PATH unset differs from empty PATH.
        missing_image = menu(args.binary, "missing-helper", "absent-preview",
                             str(missing), keys=("Return",))
        screenshot_match(unavailable, missing_image)
        failure, _ = fixture("nonzero-preview", output_bytes=b"partial must not render\n", status=7, log=False)
        failure_image = menu(args.binary, "nonzero-helper", failure, str(base), keys=("Escape",))
        screenshot_match(unavailable, failure_image)
        headerless = base / "headerless"
        sentinel = output / "headerless-side-effect"
        headerless.mkdir()
        (headerless / "enoexec").write_text(
            "printf shell-fallback > " + shlex.quote(str(sentinel)) + "\n", encoding="utf-8")
        (headerless / "enoexec").chmod(0o700)
        later, _ = fixture("later/enoexec", output_bytes=b"later candidate ran\n", log=False)
        enoexec_image = menu(args.binary, "enoexec-no-fallback", "enoexec",
                             os.pathsep.join((str(headerless), str(later.parent))), keys=("Escape",))
        screenshot_match(unavailable, enoexec_image)
        require(not sentinel.exists(), "headerless helper was interpreted by a shell")

        # Metacharacters are part of the filename, not shell syntax.
        literal_name = "literal ; $(printf literal > forbidden-literal-side-effect)"
        literal, _ = fixture("literal/" + literal_name)
        literal_image = menu(args.binary, "literal-metacharacter", literal_name,
                             str(literal.parent), keys=("Return",))
        screenshot_match(reference_image, literal_image)
        require(not (base / "forbidden-label-side-effect").exists(),
                "selected label was interpreted as shell syntax")
        require(not (base / "forbidden-literal-side-effect").exists(),
                "metacharacter helper name executed shell syntax")
        require(not sentinel.exists(), "headerless helper or metacharacter executed shell syntax")

        # Reject invalid -P/-l before any X display connection.
        invalid = (("empty-helper", ["-P", "", "-l", "2"]),
                   ("relative-helper", ["-P", "dir/helper", "-l", "2"]),
                   ("dot-relative-helper", ["-P", "./helper", "-l", "2"]),
                   ("missing-helper-argument", ["-P"]),
                   ("missing-lines", ["-P", "helper"]),
                   ("invalid-lines", ["-P", "helper", "-l", "0"]))
        for name, options in invalid:
            proc = subprocess.run([str(args.binary), *options], input=label, capture_output=True,
                                  env={"LC_ALL": "C.UTF-8"}, cwd=base, timeout=5)
            (output / f"{name}.stderr").write_bytes(proc.stderr)
            require(proc.returncode == 1 and proc.stdout == b"" and proc.stderr.startswith(b"usage:"),
                    f"{name}: invocation was not rejected before display")

        # No-preview output and pixels remain baseline-identical in horizontal and vertical modes.
        for index, options in enumerate(([], ["-l", "2", "-F", "-i"])):
            images, outputs = [], []
            for binary, suffix in ((args.baseline, "baseline"), (args.binary, "current")):
                instance = preview.Menu(binary, options, label, display_env, output)
                menus.append(instance)
                image = output / f"no-preview-{index}-{suffix}.png"
                instance.screenshot(image)
                images.append(image)
                instance.key("Return")
                instance.proc.wait(timeout=5)
                save_stderr(instance, output, image.stem)
                require(instance.proc.returncode == 0, "no-preview menu exited abnormally")
                outputs.append(instance.stdout())
                instance.close(output, image.stem)
                menus.remove(instance)
            require(outputs == [label, label], "no-preview selected output changed")
            screenshot_match(*images)
        require(xvfb.poll() is None, "owned Xvfb exited unexpectedly")
        print(f"PASS preview PATH lookup regression; screenshots and stderr: {output}")
    except BaseException:
        print(f"FAIL preview PATH lookup regression; inspect evidence: {output}")
        raise
    finally:
        for instance in menus:
            instance.close(output, f"cleanup-{instance.proc.pid}")
        if xvfb and xvfb.poll() is None:
            xvfb.terminate()
            try:
                xvfb.wait(timeout=2)
            except subprocess.TimeoutExpired:
                xvfb.kill()
                xvfb.wait(timeout=2)
        xvfb_err.close()


if __name__ == "__main__":
    main()
