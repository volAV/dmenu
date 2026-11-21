# text-preview Specification

## Purpose
Provide optional keyboard-driven text previews without changing dmenu selection, matching, or pipeline semantics.

## Requirements

### Requirement: Explicit opt-in and unchanged default mode
The menu SHALL enable previews only with `-P` followed by an absolute executable path or a nonempty bare executable name without slashes. Without `-P`, it SHALL retain existing CLI behavior, layout, matching, output, and resource characteristics, without starting helpers or allocating preview buffers.

#### Scenario: Existing invocations
- **WHEN** dmenu runs without `-P` in horizontal or vertical mode, including `-F`, `-i`, `-b`, `-m`, and `-w`
- **THEN** its visible layout, navigation, selection output and exit status match the pre-change build, with no preview descriptors, child processes, or periodic wakeups

#### Scenario: Invalid preview invocation
- **WHEN** `-P` has no argument, an empty argument, a nonabsolute path containing a slash, or the effective `-l` value is zero or absent
- **THEN** dmenu reports a content-free usage error on stderr and exits 1 before grabbing the keyboard

### Requirement: Stable split geometry
In preview mode the menu SHALL retain a full-width search row above approximately 40% item and 60% preview columns. It SHALL retain selected-item highlighting and keep body dimensions independent of selection, match count, helper state, and preview length.

#### Scenario: Empty or short list
- **WHEN** `-P /helper -l 12` receives zero, one, or many items, or filtering removes all matches
- **THEN** the requested body remains 12 rows where screen space permits, neither pane collapses, and no selection means a cleared preview without a helper request

#### Scenario: Constrained geometry
- **WHEN** the menu is placed at the bottom, on a selected monitor, or inside a small embedding window
- **THEN** both panes and input remain inside the available geometry with at least one readable body row, or the menu exits 1 with a content-free geometry error if that is impossible

### Requirement: Preview-list multiline count visibility
Only in preview-enabled lists, overflowing labels ending in ` (N lines)`
SHALL preserve the suffix at the right padded edge when it fits, shortening
only the preceding text with an ellipsis. `N` SHALL be canonical ASCII decimal
greater than one, without a leading zero. Fitting and unrecognized labels
SHALL retain normal rendering. Highlighting, colors, padding, geometry and
pane proportions SHALL remain unchanged.

#### Scenario: Long first line of a multiline clip
- **WHEN** a complete label has a long first-line prefix and terminal ` (3 lines)` that ordinary rendering would hide
- **THEN** the list shows shortened prefix text and the complete count when it fits, with all shortened-run ink clipped inside the item region
- **AND** the original complete label remains unchanged for selection, search, helper stdin, checksum lookup and stdout, independently of preview payload line lengths

#### Scenario: Insufficient width
- **WHEN** the padded item cannot hold both prefix text and the count, or cannot hold the count itself
- **THEN** the count takes priority, any remaining prefix area contains clipped text/ellipsis, and the count is clipped safely if necessary; a padding-only row may be blank

#### Scenario: Compatibility
- **WHEN** labels are short, lack the exact recognized suffix, or dmenu runs without `-P`
- **THEN** they retain the existing rendering path, and no new label cache, helper protocol, matching rule or output transformation is introduced

### Requirement: Original-label helper protocol
The menu SHALL directly execute the configured program with no additional arguments, write the unchanged selected label plus one newline to stdin, then close stdin. Stdout SHALL be preview bytes only; stderr SHALL not be propagated or logged. Empty successful output SHALL mean no preview. Ordinary nonzero exit SHALL mean unavailable; a menu-forced exit after detected overflow SHALL retain the truncated result.

#### Scenario: Literal sensitive label
- **WHEN** the selected label contains spaces, quotes, shell metacharacters, or UTF-8
- **THEN** the helper receives those exact bytes through stdin, no label is placed in process arguments or environment, and neither the label nor preview is evaluated as shell code or written to logs

#### Scenario: Failure or missing executable
- **WHEN** the helper cannot execute or exits nonzero after writing data without detected output overflow
- **THEN** the preview shows a fixed unavailable state rather than partial output, while search, selection, Enter and Escape remain usable

### Requirement: Selection-consistent updates
The menu SHALL request previews for initial selection and subsequent actual selected-item changes, including filtering and pasting. It SHALL discard superseded results, clear old content immediately on selection change, and reset preview scroll position. Cursor-only edits or redraws that retain selection SHALL not rerun a completed helper.

#### Scenario: All selection paths
- **WHEN** Up/Down, Ctrl-n/p, PageUp/PageDown, Home/End, Tab, typed search or pasted search changes the selected item
- **THEN** the right pane eventually displays only that item's preview and never a late result from another item

#### Scenario: Matching variants
- **WHEN** the same input is exercised with fuzzy matching, `-F`, and each mode with `-i`
- **THEN** match ordering and selection-reset behavior match the pre-change build, and the preview tracks the resulting selection rather than changing the matching algorithm

### Requirement: Responsive bounded helper work
Helper work SHALL not block X11 event handling. Requests SHALL launch after 40 ms selection stability. The 1000 ms monotonic deadline from launch SHALL cover execution, stdin delivery, stdout collection and process completion, not text preparation. Retained output SHALL be limited to 65536 bytes. There SHALL be one active request slot; replacement SHALL wait for nonblocking reaping of the cancelled direct child, not instantaneous disappearance of its signalled group.

#### Scenario: Slow reader or hanging writer
- **WHEN** a helper does not read stdin, closes stdout but stays alive, or never exits, without first triggering overflow
- **THEN** typing, navigation, redraw and Escape are serviced without waiting for helper I/O or exit, and at the deadline the menu marks the request timed out and sends SIGKILL to its group without blocking for termination

#### Scenario: Rapid navigation and bounded output
- **WHEN** selection changes repeatedly or output exceeds 65536 bytes
- **THEN** obsolete work is cancelled, only the latest stable selection launches next, retained output never exceeds the limit, and a bounded readable prefix is marked truncated rather than silently presented as complete

### Requirement: Terminal result precedence
Detected output beyond 65536 bytes SHALL retain a bounded prefix marked truncated, even if the producer subsequently exits nonzero or is killed by the menu. That forced status SHALL NOT replace truncation with unavailable or timeout. Exactly 65536 bytes followed by EOF and successful process completion within the helper deadline SHALL NOT be marked truncated. Superseded selections SHALL still discard their results.

#### Scenario: Overflow followed by forced termination
- **WHEN** a producer writes at least 65537 bytes, the menu observes overflow and sends SIGKILL, and later observes a signalled or nonzero exit
- **THEN** the current selection retains a readable prefix of at most 65536 bytes with Truncated state, not Unavailable or Timed out

#### Scenario: Exact-limit success
- **WHEN** a helper writes exactly 65536 bytes, closes stdout and exits 0 before its 1000 ms deadline
- **THEN** its preview is successful without a truncation indicator

#### Scenario: Ordinary failure below the cap
- **WHEN** a helper writes fewer than 65536 bytes and exits nonzero without any observed overflow
- **THEN** its output is discarded and the preview is unavailable, not truncated

### Requirement: Separately bounded text preparation
After a successful or truncated collection result, text sanitization and layout SHALL use separate per-event-loop work budgets, processing at most 256 codepoints per turn before returning to event handling. Text preparation SHALL NOT consume or extend the helper's 1000 ms deadline, block on process completion, or turn a completed result into a timeout.

#### Scenario: Layout continues after helper completion
- **WHEN** a helper completes within its deadline but the bounded text preparation extends beyond 1000 ms from launch
- **THEN** input and cancellation remain serviceable between work turns, the current result eventually renders without a helper timeout, and changing selection cancels obsolete preparation

### Requirement: Safe multiline text rendering
The preview SHALL preserve newline structure and empty lines, soft-wrap long lines at UTF-8 boundaries using the active font metrics, and make all retained text accessible by vertical scrolling. It SHALL expand tabs to four-column stops and replace invalid UTF-8, NUL, remaining C0/C1 controls and DEL with visible replacement characters; it SHALL not interpret terminal escapes or markup.

#### Scenario: Mixed untrusted text
- **WHEN** output contains accented text, CJK, emoji, blank lines, tabs, CR, ESC sequences, embedded NUL or malformed UTF-8
- **THEN** line structure is retained, unsafe controls are rendered inertly, processing makes forward progress, and nothing draws outside the preview pane or changes the selected label

#### Scenario: Extremely long line
- **WHEN** a single line exceeds the output limit, including a multibyte sequence split at the byte cap
- **THEN** wrapping and scrolling remain bounded, the incomplete tail is safely replaced or omitted, and truncation is visibly indicated

#### Scenario: Single-row truncated geometry
- **WHEN** a truncated result is displayed with `-l 1`
- **THEN** the existing single status row SHALL remain Truncated without a text viewport; this user-approved Checkpoint 4B exception requires at least two body rows to read truncated text, while successful single-row previews SHALL remain scrollable

### Requirement: Independent keyboard scrolling
In preview mode Alt-Up/Down SHALL scroll one visual row, Alt-PageUp/PageDown SHALL scroll one viewport, and Alt-Home/End SHALL move to preview boundaries. Scrolling SHALL clamp to available rows without changing selection, search text, cursor, or stdout. Existing unmodified navigation and existing Alt-letter bindings SHALL remain unchanged.

#### Scenario: Scroll and select
- **WHEN** the user scrolls beyond either end, then selects another item
- **THEN** scrolling clamps at the boundary and the new preview starts at its first row without moving the item selection during scrolling

### Requirement: Preserve output and cancellation contracts
Preview content SHALL never replace the selected label or typed text on stdout. Return SHALL emit the selected label and exit 0; Shift-Return SHALL emit input and exit 0; Ctrl-Return SHALL emit and continue; Escape SHALL exit 1 without output. With no selection, Return SHALL retain existing typed-input behavior.

#### Scenario: Output while helper runs
- **WHEN** Return, Shift-Return, Ctrl-Return, or Escape is pressed while a helper is delayed or failing
- **THEN** output and status follow the existing contract without waiting for preview completion, and ordinary exits release the keyboard grab promptly

### Requirement: Resource lifecycle and privacy
On cancellation, completion, destruction or exit, the menu SHALL close preview fds, discard buffers and send SIGKILL to its owned cooperative group as needed. Exit SHALL release the keyboard promptly without waiting for termination; reaping while running SHALL be nonblocking. Tests SHALL allow 2000 ms from SIGKILL dispatch for eventual cooperative-group termination, not instant disappearance. Escaping descendants SHALL be outside containment guarantees; content temporary files SHALL NOT be used.

#### Scenario: Repeated cancellations and exit
- **WHEN** an isolated test repeatedly changes selection and exits with helpers active, including a helper with a child inheriting stdout
- **THEN** keyboard release and menu exit do not wait for helper disappearance, cooperative group members terminate within 2000 ms of SIGKILL dispatch under normal test scheduling, fd/child accounting stabilizes rather than accumulating, and diagnostics contain no labels or preview text; tests do not claim containment of descendants intentionally leaving the group

### Requirement: Predictable literal helper lookup
For each helper request, a bare name SHALL select the first executable regular-file candidate in inherited PATH order. Colon-separated empty components SHALL mean the current directory; relative components SHALL be relative to the inherited working directory. Unset PATH SHALL provide no candidates; empty PATH SHALL search only the current directory. Absolute helper paths SHALL bypass PATH unchanged.

#### Scenario: Ordered search and non-executable candidate
- **WHEN** controlled PATH contains missing, directory or non-executable candidates before an executable regular file of the requested name
- **THEN** those candidates are skipped and the first executable regular-file candidate receives the unchanged selected label plus LF on stdin, with no extra arguments or label environment variables

#### Scenario: Empty and relative components
- **WHEN** PATH has empty components or relative directories and synthetic helpers reside in the corresponding working-directory locations
- **THEN** lookup follows that component order without implicitly adding other directories

#### Scenario: Unset and empty PATH
- **WHEN** PATH is unset or explicitly empty
- **THEN** unset PATH yields Unavailable for a bare helper, while empty PATH considers only the inherited working directory

#### Scenario: Absolute compatibility
- **WHEN** an absolute helper path is supplied with PATH unset or containing another helper of the same basename
- **THEN** the configured absolute helper executes as before and its stdout is displayed as preview data

### Requirement: Direct execution without shell fallback
Helper names SHALL be literal filenames, never shell commands. The selected candidate SHALL execute directly without ENOEXEC-to-shell fallback or retrying later PATH candidates after execution fails. Missing candidates and failed execution SHALL use the existing Unavailable preview behavior. PATH directories, the inherited working directory when searched, and helpers SHALL be explicitly trusted; lookup SHALL NOT be described as a sandbox or atomic guarantee.

#### Scenario: Executable without interpreter header
- **WHEN** the first executable candidate is a text file without a valid interpreter header
- **THEN** it is not evaluated by a shell, no later candidate runs, and the pane becomes Unavailable while Enter and Escape remain usable

#### Scenario: Metacharacters remain literal
- **WHEN** a helper name contains spaces, semicolons or command-substitution syntax
- **THEN** only an exact matching executable filename can run; no syntax is evaluated or side effect produced by interpreting the name

#### Scenario: Missing or non-executable helper
- **WHEN** no executable regular-file candidate exists
- **THEN** the menu retains preview mode and shows Unavailable for the selected item without sensitive diagnostics, partial output or changed selection/exit contracts

#### Scenario: Lifecycle and protocol compatibility
- **WHEN** lookup succeeds or fails during synthetic preview interaction
- **THEN** label identity, label-plus-LF stdin, no-label argv/environment, nonblocking collection, process groups, cancellation, 40 ms debounce, 1000 ms deadline, 65536-byte cap and terminal-state precedence retain their existing contracts
