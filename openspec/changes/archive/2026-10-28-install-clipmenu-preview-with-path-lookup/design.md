# Design

## Context

See proposal.md for motivation and the two delta specifications for behavior. Makefile currently copies four executables to `$(DESTDIR)$(PREFIX)/bin`, sets each mode explicitly, and already includes clipmenu-preview in dist. config.mk defaults PREFIX to /usr/local and MANPREFIX to `$(PREFIX)/share/man`.

`main()` currently requires an absolute previewcmd before XOpenDisplay. `previewstart()` creates the process group and pipes, redirects stderr, closes inherited descriptors, then calls execv and exits 127 on failure. Its parent already maps ordinary failed execution to Unavailable. This is the narrow execution boundary; selection identity, preview preparation and rendering need no changes.

Read-only inspection of `/home/avo/.guix-profile/bin/clipmenu` confirms initial `launcher_args=(-l "${CM_HISTLENGTH}")`, direct `"$CM_LAUNCHER" "${launcher_args[@]}" "$@"` forwarding, and `cksum <<< "$chosen_line"`. Its Guix wrapper prepends tool directories to inherited PATH. No launcher execution, cache reads or selection access is necessary to establish forwarding.

## Goals / Non-Goals

**Goals:** Keep installation additive and helper lookup literal, deterministic and bounded by the existing helper execution deadline. Reuse direct execv and ordinary execution-failure handling.

**Non-Goals:** No geometry, fonts, keyboard-grab lifetime, matching, launcher, cache, checksum, async lifecycle or compile-time configuration redesign. Do not reopen the archived change, deploy, commit or archive this change.

## Decisions

1. Add clipmenu-preview to the existing install copy list, explicit chmod 755 recipe and uninstall list. Preserve dist's single existing entry and all other mappings. Do not change config.mk, introduce a helper-only target or force a user-local prefix.
2. Relax CLI validation only for nonempty names containing no slash. Retain absolute paths unchanged and the positive decimal -l check. Reject `./helper`, `dir/helper` and empty strings before opening X.
3. Perform bare-name lookup in the already-forked helper child immediately before direct execution. Parse inherited PATH in order without mutating the environment; stat/access candidates to select the first regular executable file, following symlinks as normal executable lookup does. Empty components mean the inherited working directory; relative components remain relative; unset PATH has no implicit default. Allocate only for lookup in preview children with checked length arithmetic; absolute execution retains its existing path. Child-local storage needs no new parent state or cleanup. Lookup failure exits 127; keep the original previewcmd nonnull in the parent so failure cannot disable preview mode.
4. Execute the selected candidate with execv and only argv[0]; never execvp, shell construction or ENOEXEC fallback. Stop after the selected candidate's execution failure rather than searching for another program. Parent request deadline and group cancellation already cover the child lookup/execution lifetime. Filesystem access can block the child, but not the X11 event loop.
5. Reuse the existing owned-Xvfb startup and screenshot patterns in a compact focused regression, without running clipmenu or historical matrices. Use synthetic interpreter-backed helpers, a private controlled PATH and owned working directory. Keep packaging verification in disposable copies so dist's clean prerequisite cannot touch active build products.

## Risks / Trade-offs

- [PATH may select attacker-controlled code, especially through empty/relative components] -> Document trust in every searched directory, the inherited working directory and helper code; show an explicit trusted installation bin directory in the inherited Xmonad environment. Do not claim ~/.local/bin is automatically available.
- [Candidate changes between checks and execv] -> Disclose non-atomic trusted-filesystem lookup; execv failure remains Unavailable. No sandbox or stronger identity guarantee is added.
- [First executable file has invalid format or interpreter] -> Fail Unavailable without shell fallback or later-candidate retry; test an executable headerless payload with a side-effect sentinel.
- [Installation replaces existing files and uninstall does not restore backups] -> Document separately authorized backups and rollback, and the full target's removal semantics. Staged tests keep an unrelated sentinel and verify all existing artifacts.
- [Baseline or local state changes before apply] -> Recheck branch, HEAD, status and index at apply; adapt to user-owned changes without resetting or cleaning.

## Migration Plan

Development uses disposable source copies, private staging roots and owned displays only. Update README, AGENTS.md and dmenu.1 to remove obsolete helper-install exclusions and document `make install PREFIX="$HOME/.local"`, matching uninstall, staging with DESTDIR, both -P forms, and `clipmenu -i -p Clipboard -P clipmenu-preview -l 12`. This command also requires the intended dmenu to be selected by CM_LAUNCHER or the launcher's inherited PATH.

After implementation verification, use the installed OpenSpec sync workflow to reconcile the canonical specifications without modifying or archiving the accepted prior change or archiving this change. Actual installation, Xmonad environment changes and rollback execution remain separately unauthorized. Leave implementation and artifacts unstaged for independent review.
