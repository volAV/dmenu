# Spec Delta

## MODIFIED Requirements

### Requirement: Explicit opt-in and unchanged default mode
The menu SHALL enable previews only with `-P` followed by an absolute executable path or a nonempty bare executable name without slashes. Without `-P`, it SHALL retain existing CLI behavior, layout, matching, output, and resource characteristics, without starting helpers or allocating preview buffers.

#### Scenario: Existing invocations
- **WHEN** dmenu runs without `-P` in horizontal or vertical mode, including `-F`, `-i`, `-b`, `-m`, and `-w`
- **THEN** its visible layout, navigation, selection output and exit status match the pre-change build, with no preview descriptors, child processes, or periodic wakeups

#### Scenario: Invalid preview invocation
- **WHEN** `-P` has no argument, an empty argument, a nonabsolute path containing a slash, or the effective `-l` value is zero or absent
- **THEN** dmenu reports a content-free usage error on stderr and exits 1 before grabbing the keyboard

## ADDED Requirements

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
