# Project Overview

This is a single-directory suckless dmenu fork: a C99 X11 menu, a file-filtering
utility, and two POSIX shell launcher scripts. The source version is defined in
`config.mk`. Fuzzy matching is integrated and enabled by default in
`config.def.h`; `-F` selects the token-based matching path.

# Repository Map

- `dmenu.c`: argument parsing, stdin items, matching, keyboard/clipboard input,
  selection output, X11 window setup, and event loop.
- `drw.c`, `drw.h`: Xlib/Xft drawing, fonts, text measurement, and rendering.
- `util.c`, `util.h`: fatal errors and checked allocation; `arg.h`: stest options.
- `stest.c`: file predicates and directory enumeration used by `dmenu_path`.
- `dmenu_path`: executable-name discovery and its per-user cache.
- `dmenu_run`: asynchronous pipeline from executable discovery to menu to shell.
- `clipmenu-preview`: optional read-only POSIX helper for clipmenu 6 payloads;
  included in source distributions, but not installed by generic `make install`.
- `config.def.h`: distributed compile-time defaults; `config.h`: active local
  compile-time configuration; `config.mk`: compiler, linker, and install paths.
- `dmenu.1`, `stest.1`: command-line and interaction documentation.
- `patches/`: reference diffs, not build inputs. Presence does not mean applied;
  inspect live source before using a patch. Fuzzy highlighting and item counts
  from the stored diffs are not present in the current menu implementation.
- `openspec/`: planning configuration, specifications, and changes;
  `.omp/commands/` and `.omp/skills/`: installed OpenSpec workflows.

# Commands

Run commands from the repository root. There is no dependency-install target:
provide a C99 compiler, make, and development headers/libraries for Xlib,
Xinerama, Xft, Fontconfig, and FreeType. The linker also uses libm.
`config.mk` contains the platform-specific include/library paths and flags;
Xinerama is optional by disabling its two settings there.

| Purpose | Existing command | Notes |
| --- | --- | --- |
| Build | `make` | Builds `dmenu` and `stest`; uses existing `config.h`. |
| Clean build outputs | `make clean` | Removes binaries, objects, and the versioned release tarball; keeps `config.h`. |
| Install | `make install` | Uses `PREFIX` and `MANPREFIX`; distributed prefix is `/usr/local`, while local `config.mk` overrides may differ. |
| Staged install | `make install DESTDIR=/tmp/dmenu-stage PREFIX=/usr` | Choose a disposable staging directory; does not install into the host `/usr`. |
| Source archive | `make dist` | Runs clean, then packages the Makefile's explicit source list, not every repository file. |
| Shell syntax | `sh -n dmenu_path && sh -n dmenu_run` | Checks both launchers without executing them. |
| Helper regression | `sh -n clipmenu-preview && python3 tests/clipmenu-preview.py` | Synthetic fixtures only; never runs clipmenu, xsel or X clients. |
| Version smoke | `./dmenu -v` | Exits before opening an X display. |
| File-filter smoke | `./stest -f dmenu.c` | Prints `dmenu.c` and exits 0. |
| Interactive menu | `printf 'alpha\nbeta\n' \| ./dmenu` | Requires a working X display and fonts; not a headless check. |
| OpenSpec validation | `openspec validate --all --strict` | Validates specs/changes, not semantic accuracy of this guide or project configuration. |

No dev server, lint, formatter, standalone typecheck, automated test suite,
CI workflow, code generator, migration, container, or infrastructure-validation
command is defined in this checkout. Do not substitute invented targets.

# Architecture and Boundaries

- `dmenu` reads newline-separated items from stdin and emits selections or typed
  input to stdout. Diagnostics belong on stderr (`die()` exits 1).
- Return emits the selected item and exits 0; Shift-Return emits typed input;
  Ctrl-Return continues after output. Escape exits 1 without a selection.
  Preserve these pipeline contracts unless a change explicitly alters them.
- Matching maintains the doubly linked item lists used for selection and paging.
  Changes must account for both fuzzy and `-F` modes and `-i` case handling.
- `dmenu` itself does not execute selections. `dmenu_run` pipes output into
  `${SHELL:-"/bin/sh"}` in the background. Treat launcher smoke inputs as shell
  commands, not harmless menu labels; test with benign commands only.
- `dmenu_path` uses `${XDG_CACHE_HOME:-"$HOME/.cache"}/dmenu_run` and directory
  mtimes to refresh executable names from `PATH`. Isolate cache and PATH when
  exercising discovery so checks do not overwrite the user's cache.
- `-f` grabs the keyboard before consuming non-tty stdin: an input producer
  that never reaches EOF can hold the keyboard. Use finite input for checks.

# Configuration and Build Artifacts

Make copies `config.def.h` to `config.h` only when `config.h` is absent. Editing
the defaults does not update an existing local configuration, and `make clean`
does not fix that. Compare both headers when defaults gain fields; preserve
local customizations rather than blindly replacing `config.h`.

`config.h` is locally editable configuration, not an immutable generated client.
`dmenu`, `stest`, `*.o`, and release archives are build outputs, not source.
Pre-existing binaries are not evidence that the current source builds.
Installed manuals are generated by substituting VERSION into the `.1` sources.

# Testing and Definition of Done

For C changes, build against the intended configuration and exercise the changed
behavior. For matching/input/rendering changes, check an actual X11 menu,
including relevant flags, selection output, and cancellation; version output
alone does not verify UI behavior. Report explicitly if no display is available.
For launcher changes, also check shell syntax and discovery/execution with
isolated cache/PATH and benign inputs. For install changes, stage installation
and inspect binaries/scripts and manuals rather than overwriting host files.

The `add-text-preview` implementation is authorized through Checkpoint 5B:
groups 1–4 plus helper/integration tasks 5.1–5.3 only. Deployment, installation
and group 6 remain outside authorization.
`-P /absolute/helper` requires an explicit positive decimal `-l`. The executable
receives the selected label plus LF on stdin; argv/environment never carry it.
The single helper group uses nonblocking I/O, 40 ms debounce, a 1000 ms deadline
and 65536-byte cap. Overflow takes precedence over forced producer failure.
Exit releases the keyboard without waiting for group termination. Trusted
helpers must not deliberately escape the group; this is not a sandbox.

`clipmenu-preview` accepts no arguments and follows the existing label-plus-LF
stdin protocol. Cache lookup uses unset-only CM_DIR/XDG_RUNTIME_DIR/TMPDIR
defaults, `clipmenu.6.$USER` and the full label-plus-LF cksum filename. USER
allows only ASCII letters/digits/underscore/dot/hyphen, excluding empty, dot
and dot-dot. It requires a nonsymlink current-UID-owned private final directory
and readable regular nonsymlink payload, then streams bytes unchanged without
clipboard/cache writes or sensitive diagnostics. GNU stat and existing POSIX
tools are used. Ancestors/PATH/same-UID peers are trusted; checks are not atomic,
and streaming failures may emit a prefix. No locking or snapshot guarantee.
`tests/clipmenu-preview.py` verifies byte preservation and rejection boundaries,
including fixture contents/modes/ownership/mtime preservation. Its /tmp fallback
uses a unique synthetic USER and exclusive mkdir. Foreign ownership uses optional
fakeroot metadata simulation only for non-root runs; EUID zero skips it because
fakeroot may allow real privileged chown. Root also skips mode-000 unreadability.
Keep these coverage limits explicit. GUI smoke must use an owned
disposable X server, synthetic cache and disposable source build, never the
working desktop. The launcher regression alone runs the installed clipmenu
body with explicit Bash, bypassing its Guix wrapper, and an allowlisted PATH
containing fake xsel. Never run the wrapper, daemon or real xsel in this check.
`python3 tests/clipmenu-launcher.py --launcher /absolute/installed/clipmenu
--binary /absolute/disposable/dmenu --xvfb /absolute/Xvfb
--output /absolute/empty/results` owns its display and compares preview and
clipboard/PRIMARY restoration bytes against synthetic multiline fixtures.
README documents the exact CM_LAUNCHER invocation and optional installation
and rollback; these instructions are not deployment authorization.

The compile-time fixture is removed. Preserve the prior GUI evidence and use
executable synthetic test helpers instead. Build baseline and current binaries
in disposable source copies so local configuration, binaries and objects remain
untouched. Run `python3 tests/preview-checkpoint.py --baseline /absolute/baseline
--binary /absolute/new --output /tmp/screenshots --isolated-display` as one command,
with DISPLAY set only to an owned disposable Xvfb/Xephyr server. Tests use xdotool,
xwininfo and ImageMagick; never point them at real history or the working desktop.
Do not install this checkpoint; group-6 delivery gates remain pending.
The GUI harness requires an empty output directory, confirms at least 200 real
helper starts, and retains stderr on every outcome. Byte-exact collection uses
`tests/preview-boundary.c`, including the real private lifecycle functions;
build/run commands are in README. Its producer must be the uninstrumented
normal probe even when the parent is sanitized, because helper stderr is discarded.
Record leak-disabled ASan coverage separately from leak-enabled exit diagnostics.
`tests/preview-text.c` includes the production incremental text preparation and
uses deterministic metrics; `PREVIEW_TEXT_HELPER` must name the normal probe
even for sanitized parents. `tests/preview-text.py --binary /absolute/dmenu
--layout-probe /absolute/preview-text --clip-probe /absolute/preview-clip
--output /tmp/text-results --isolated-display` adds synthetic safe-text, wrapping,
font/clipping and pending-layout interaction checks on an owned X server.
`tests/preview-clip.c` compares real overwide-glyph pixels against independent
Xft clipping, ellipsis and blank references. These probes are test-only,
not production CLI options.
Alt-Up/Down scroll one prepared visual row, Alt-PageUp/PageDown one text
viewport, and Alt-Home/End to viewport boundaries. Only item identity changes
reset the offset; redraws and cursor-only edits retain it. Scrolling reuses the
prepared buffer/row ends without helper invocation or preparation. The existing
status footer reduces capacity by one when `-l` exceeds one. The explicitly
accepted `-l 1` Truncated exception remains status-only; use at least two rows
to read truncated content. Do not redesign that geometry as part of scrolling.
`tests/preview-scroll.py --binary /absolute/current/dmenu --baseline
/absolute/baseline/dmenu --output /tmp/scroll-results --isolated-display`
checks scrolling pixels and a baseline/input matrix on an owned display.
It needs an empty output directory and uses installed `xclip` only to own
synthetic PRIMARY data for paste; never run it on the working desktop.
Preparation handles at most 256 codepoints per event-loop turn, including tab
expansion, and yields after a 4 ms monotonic budget between glyphs. A single
synchronous Xft lookup cannot be interrupted by that budget.
`tests/preview-fonts.py --short --baseline-source /absolute/starting-copy
--corrected-source /absolute/current-copy --xvfb /absolute/Xvfb
--output /tmp/font-results` measures 1/8 KiB real-font fallback and keyboard
latency on its own disposable server; source copies must contain `config.h`.
The helper collection deadline and raw cap are unchanged. Display storage
is at most 4 * raw length + 1 bytes, with at most that many size_t row entries.
LF preserves empty/trailing rows; CR and unsafe controls become U+FFFD, and
tabs use four codepoint-column stops reset only by LF. Xft glyph ink is clipped
to the preview row; grapheme shaping and font coverage are not extended.
The reviewed fuzzy bounds fix is integrated independently of preview features.
`tests/fuzzymatch.c` includes production matching code and asserts unequal-score
ordering by default; `a a` is the original single-match ASan reproducer.
Its README build instructions require `make config.h` in a fresh disposable
checkout. Native probes keep leak detection enabled; GUI memory-safety runs
isolate the unrelated Fontconfig leak and retain separate leak-enabled results.


Keep CLI/help/manual and compile-time defaults consistent with behavior changes.
Update this guide when commands or these boundaries change. Use OpenSpec's
installed workflows for scoped planning and implementation; this guide is the
canonical project guide, not a duplicate `.omp/AGENTS.md`.
