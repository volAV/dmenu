# clipmenu-preview Specification

## Purpose
Resolve installed clipmenu 6.2.0-1.7c34ace display labels to original cached text without changing clipboard capture or restoration.

## Requirements

### Requirement: Compatible label-to-cache mapping
The standalone helper SHALL read one original label from stdin, preserving whitespace and backslashes. It SHALL resolve the launcher's cache base using `CM_DIR`, otherwise `XDG_RUNTIME_DIR`, otherwise `TMPDIR`, otherwise `/tmp`, with unset-variable semantics. It SHALL use `clipmenu.6.$USER` and the complete POSIX `cksum` result for label plus one newline, including CRC, space, and byte count, as the filename.

#### Scenario: Exact checksum filename
- **WHEN** a synthetic label contains leading/trailing spaces, quotes, backslashes, shell syntax or non-ASCII bytes
- **THEN** the helper opens the same filename as installed clipmenu's `cksum <<< "$chosen_line"`, without trimming the label or executing its contents

#### Scenario: Environment overrides
- **WHEN** an isolated fixture varies each cache-base environment variable, including explicitly empty versus unset values
- **THEN** resolution matches installed clipmenu's launcher semantics rather than using `$HOME/.cache` or changing the daemon configuration

### Requirement: Original payload bytes
The helper SHALL stream the cached payload as data without command substitution, line flattening, newline insertion or decoding. The menu's documented output cap and display sanitization SHALL apply only to preview presentation; selection restoration SHALL continue using clipmenu's original lookup and payload.

#### Scenario: Multiline and trailing newlines
- **WHEN** a fixture label describes a payload containing blank lines, UTF-8, a long line and trailing newlines
- **THEN** helper stdout is byte-identical to the fixture payload, while dmenu stdout remains the exact original display label followed by its normal newline

#### Scenario: Duplicate label or collision
- **WHEN** several records share a label or different labels resolve to the same existing checksum filename
- **THEN** the helper uses the current file that installed clipmenu would use, without inventing disambiguation or migrating history

### Requirement: Missing data and constrained reads
The helper SHALL fail without payload or sensitive diagnostics for missing, unreadable, symlinked or non-regular payloads, invalid user path components, and unsafe cache directories. It SHALL operate only in a caller-trusted, current-user-owned private cache directory, reject a symlinked final cache directory, and never modify cache files, history, locks or selections.

#### Scenario: Cache vacuum or unsafe entry
- **WHEN** a fixture payload is removed before lookup, is a FIFO or symlink, or its final cache directory is not private and owned by the current user
- **THEN** the helper returns nonzero and no clipboard bytes, and dmenu keeps a stable unavailable pane without hanging or changing selection

### Requirement: Integration without clipmenu changes
The documented integration SHALL use existing `CM_LAUNCHER` and forwarded CLI arguments, without changing clipmenu, clipmenud, history, existing launcher scripts or Xmonad configuration. The helper SHALL remain optional to invoke and outside the generic menu's clipboard-independent implementation, while being included in normal installation.

#### Scenario: Forwarded preview invocation
- **WHEN** clipmenu is invoked with `CM_LAUNCHER=/path/to/new/dmenu` and `-i -l 12 -p Clipboard -P /path/to/clipmenu-preview`
- **THEN** the launcher receives those arguments after clipmenu's initial line-count argument, preview mode uses the final value 12, and Enter returns the unchanged selected label for existing restoration

#### Scenario: Independent restoration check
- **WHEN** the installed clipmenu launcher is exercised in a disposable synthetic environment with a fake clipboard sink and the new menu
- **THEN** the sink receives the original multiline payload for clipboard and primary, no real history or clipboard is touched, and failure/cancellation retain installed clipmenu's own exit behavior

#### Scenario: Bare helper forwarding
- **WHEN** the installed launcher's source is inspected read-only for `clipmenu -i -p Clipboard -P clipmenu-preview -l 12`
- **THEN** it forwards the literal helper name and final line-count argument after its initial line count, without requiring a launcher modification

### Requirement: Generic helper installation and removal
Normal `make install` SHALL install clipmenu-preview unchanged with mode 0755 at `$(DESTDIR)$(PREFIX)/bin/clipmenu-preview`, using the existing executable-directory conventions. `make uninstall` SHALL remove that same path. Existing executable and manual mappings SHALL remain unchanged; unrelated files SHALL remain untouched. `make dist` SHALL continue including the executable helper once.

#### Scenario: Staged nondefault prefix
- **WHEN** a disposable build is installed with a private DESTDIR and a nondefault PREFIX
- **THEN** the helper bytes match the source, its mode is 0755, and all existing executables and substituted manuals occupy their expected staged locations without host writes

#### Scenario: Uninstall with unrelated file
- **WHEN** the staged installation includes an unrelated sentinel and uninstall uses the same DESTDIR, PREFIX and MANPREFIX
- **THEN** all intended installed executables, helper and manuals are removed while the sentinel remains unchanged

#### Scenario: Source distribution
- **WHEN** make dist runs only in a disposable source copy
- **THEN** the archive contains exactly one executable clipmenu-preview with source-identical bytes

### Requirement: Documented user-local PATH integration
Documentation SHALL describe normal install/uninstall, PREFIX and DESTDIR, both supported helper forms, and `make install PREFIX="$HOME/.local"` as a separately authorized user-local example. It SHALL state that the installation bin directory must be in PATH inherited by Xmonad and launched clipmenu/dmenu, without claiming automatic visibility or authorizing deployment.

#### Scenario: Intended Xmonad invocation
- **WHEN** an independently authorized installation provides the intended dmenu and helper and its bin directory is present in the inherited trusted PATH
- **THEN** `clipmenu -i -p Clipboard -P clipmenu-preview -l 12` selects the helper without an absolute path or changes to clipboard history, daemon or checksum protocol
