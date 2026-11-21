# Spec Delta

## ADDED Requirements

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

## MODIFIED Requirements

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
