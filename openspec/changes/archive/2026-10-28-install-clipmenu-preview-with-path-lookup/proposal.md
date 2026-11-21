# Proposal

## Why

Normal installation omits the shipped clipmenu-preview adapter, and dmenu requires an absolute helper path. Installing the adapter alongside dmenu and allowing literal executable names enables `clipmenu -i -p Clipboard -P clipmenu-preview -l 12` without embedding a checkout path.

## What Changes

- Install and uninstall clipmenu-preview using the existing executable directory, mode 0755, and DESTDIR/PREFIX mapping; retain existing executables, manuals and source packaging.
- Accept a nonempty bare helper name and resolve it through inherited PATH before direct execution, while preserving absolute paths and rejecting relative paths containing slashes.
- Preserve the stdin label-plus-LF protocol, preview lifecycle, failure states, selection output and no-preview behavior; never introduce a shell fallback.
- Document installation, removal, user-local PREFIX selection, inherited Xmonad PATH requirements and trusted-path limitations.
- Verify packaging in disposable source copies and CLI behavior with synthetic helpers on an owned disposable display.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `clipmenu-preview`: Generic adapter installation/removal and PATH-based integration without changing clipmenu or its checksum protocol.
- `text-preview`: Opt-in accepts bare executable names as well as absolute helper paths; deterministic PATH lookup and direct-execution failure semantics.

## Impact

Implementation targets are Makefile and the helper validation/execution boundary in dmenu.c, with focused regression coverage and updates to README, AGENTS.md and dmenu.1. No new dependencies, matching, geometry, cache, clipboard or asynchronous lifecycle changes are intended.

Baseline is branch `dev/add-text-preview-next`, HEAD `00de293f59913b3e22ca049c6e1b07fdf290dfc7`, with no tracked or staged modifications. Pre-existing untracked `.omp/`, config.h, binaries and objects remain user-owned. Development and isolated verification only are authorized: no host installation, active-checkout cleaning, working DISPLAY, real clipboard/history, Xmonad modifications, original-repository writes, commits or archive changes.
