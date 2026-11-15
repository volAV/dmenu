# Proposal

## Why

Clipboard display labels hide multiline text and long entries, making keyboard selection ambiguous. An opt-in text preview lets this dmenu fork display original content while leaving clipmenu, its daemon, history, and restoration protocol unchanged.

## What Changes

- Add `-P /absolute/path/to/helper` for an optional two-column text preview below the existing search input: approximately 40% items and 60% preview.
- Execute a trusted helper directly, without shell evaluation, passing the unchanged selected label through stdin; receive bounded UTF-8 text through stdout. Keep helper work asynchronous, cancellable, and time-limited.
- Refresh on actual selected-item changes, including filtering; retain selection highlighting and existing output/exit contracts. Add preview-only keyboard scrolling and stable empty/error states.
- Supply a separate, optional clipmenu-specific helper that follows the installed 6.2.0-1.7c34ace cache lookup rather than adding clipboard knowledge to dmenu.
- Document limits, security boundaries, independent X11 verification, and safe staged installation/rollback. Preserve the existing fuzzy matching implementation and non-preview fast path.
- Deliver small, independently verified increments: prove split-pane geometry with synthetic text and isolated X11 screenshot/navigation evidence before adding helper processes. This checkpoint does not replace final acceptance. Stop for review before substantially expanding event-loop/process-management complexity.
- This change is planning only until separately approved. No application source, installation, real clipboard data, daemon, or Xmonad configuration changes are authorized by this proposal session.

## Capabilities

### New Capabilities

- `text-preview`: Opt-in layout, helper protocol/lifecycle, scrolling, safe text rendering, and unchanged menu contracts.
- `clipmenu-preview`: Read-only mapping from original clipmenu display labels to cached original text, independent of selection restoration.

### Modified Capabilities

None. The initialized OpenSpec root has no existing capability specifications.

## Impact

Expected implementation surface: `dmenu.c`, `dmenu.1`, a small standalone `clipmenu-preview` helper and its usage documentation; `Makefile` only for explicit packaging of new deliverables. Reuse `drw.c`/`drw.h` drawing APIs, changing them only if a narrowly necessary bounded-text primitive cannot remain local. No new libraries, fzy/fzf dependency, worker framework, or default configuration fields are planned.

No changes to `stest`, `dmenu_path`, `dmenu_run`, clipmenu/clipmenud, clipboard history, or Xmonad. Existing customized `config.h` and the local `config.mk` prefix remain intact. The helper is not installed automatically as part of the existing generic dmenu install target. Public additions are `-P`, its documented stdin/stdout protocol, and preview-only key bindings; no existing CLI option or pipeline behavior is removed.
