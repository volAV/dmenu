# Reviewed fuzzymatch bounds-fix integration

## Scope and provenance

The user authorized integration of reviewed commit
`32fafa2ce7daf1a683e3039bbaf7a9007823e986` into the existing
`add-text-preview` working tree. Main HEAD remains the verified pre-preview
baseline `787050237a1b357e53efbec9ef5cc7870a51d041`; the reviewed commit was
not cherry-picked, amended, or committed again. No integrated file was staged.
No preview feature development, installation, Xmonad change, or real clipboard
interaction is part of this integration.

Before edits, the full checkout (including `.git`, index, tracked changes,
untracked/ignored files, local configuration, binaries, and existing evidence)
was archived to
`/tmp/dmenu-fuzzy-integration-mclmbvjk/checkout-before.tar.gz`.
Full staged/unstaged binary diffs, HEAD, the reviewed commit's exact patch,
checkout hashes, the original index, and external protected-file hashes are
retained beside that snapshot. Recovery should extract the archive into a
separate directory for inspection, not overwrite the live checkout.

Integration was first applied to a disposable trial copy. Its fresh-header
ASan/UBSan probe passed before the active source was edited. Production source
and regression-test bytes used for verification match the integrated files.

## Exact correction

The old reconstruction loop incremented `i`, then loaded `fuzzymatches[i]`
before evaluating the next iteration's bound. The final iteration therefore
read index `number_of_matches`, immediately after the allocation.

The only production change is:

```c
for (i = 0; i < number_of_matches &&
        (it = fuzzymatches[i]) && it->text; ++i)
        appenditem(it, &matches, &matchend);
```

Short-circuit evaluation checks the bound before every reconstruction-array
read. The positive-count branch remains unchanged; zero matches never enter
it, and a single match is appended once without reading index 1. Scoring,
qsort comparator/ties, list append semantics, matching flags, and all preview
subprocess/event-loop/rendering/clipboard code are unchanged. There is no new
production allocation or copying. `production-integration.diff` records the
complete two-line source correction against the saved preview source.

## Regression probe and documentation

`tests/fuzzymatch.c` integrates the reviewed include-production-code probe;
it does not replace either existing preview test. Its new no-argument case
asserts exact linked-list order for three unequal scores using query `ab` and
input order `axb`, `zzab`, `ab`:

- `ab`: `log(2) - 1`
- `zzab`: `log(4) - 1`
- `axb`: `log(2)`

Forward/backward links, endpoints, count bounds, and initial selection are also
checked. `a a` remains the minimal single-match reproducer. Optional `-i` and
`-F` use the real production matcher. The delta from the reviewed probe is in
`regression-test-integration.diff`.

README preview documentation is retained. Its standalone build instructions
now explicitly require `make config.h` from a fresh source checkout, retain
sanitizer options during compilation and linking, forbid disabling assertions,
and distinguish headless leak-enabled checks from GUI leak-disabled memory-safety
checks. The exact documented shell block was executed in a fresh disposable
source directory with no `config.h`; only the `/tmp/fuzzymatch-check` output
path was replaced by a unique disposable path. It exited 0, generated the
header, and passed both the score/order self-check and `a a`.
`readme-fresh-command.sh`, `readme-fresh-result.json`, and stdout/stderr retain
the actual command and result. AGENTS.md now points to this regression command
and the same sanitizer boundary.

## Headless verification

All builds and runs used disposable sources and synthetic labels. Exact commands,
environment settings, exit codes, outputs, compiler logs, and per-case results
are retained in `headless-artifacts.tar.gz` (`summary.json` inside the archive).
The verifier command was:

```sh
python3 /tmp/dmenu-fuzzy-integration-mclmbvjk/headless-check.py
```

The pre-fix source was extracted byte-for-byte from the recovery snapshot,
not reconstructed from memory. The minimal command `pre/fuzzy-asan a a`
exited 1 with an ASan 8-byte heap-buffer-overflow in `fuzzymatch()` at
`dmenu.c:643`, called through `match()` and the production-code probe. The
read is zero bytes after an 8-byte allocation. For this failing ASan reproducer
only, `UBSAN_OPTIONS=halt_on_error=0` allowed the reported UBSan error to continue
to ASan; the separate UBSan-only reproducer used `halt_on_error=1` and also
exited 1. Neither diagnostic was suppressed. See `original-minimal-{asan,ubsan}.stderr`.

Corrected probes were built with GCC, C99, `-pedantic -Wall -Wextra -g -O1
-fno-omit-frame-pointer`, using separate normal, `-fsanitize=address,undefined`,
and `-fsanitize=undefined` builds. Every corrected sanitizer run used
`ASAN_OPTIONS=detect_leaks=1` and `UBSAN_OPTIONS=halt_on_error=1`.

The 148-row behavioral matrix comprises 37 datasets in four flag combinations:
fuzzy, fuzzy `-i`, `-F`, and `-F -i`. It covers empty items, zero/one/multiple/all
matches, duplicate labels, empty queries, absent subsequences, mixed case,
token queries, 8000-byte queries, labels up to 64000 bytes, and 24 deterministic
seeded datasets. All 444 corrected runs across the three builds exited 0;
stdout indices, full-precision scores, label order, and exit status matched
the pre-fix normal build byte-for-byte. The pre-fix executable has undefined
behavior and is used only as an observed behavioral reference, not a memory-safe
oracle. The explicit independent expected-score/order assertions also passed.

The unchanged `tests/preview-boundary.c` completed eight reports with exit 0 in
each normal/ASan+UBSan/UBSan build, retaining leak detection. Sanitized parents
used the uninstrumented normal probe as `PREVIEW_TEST_HELPER`. Checks covered
65535/65536-byte exact success, both 65537-byte overflow terminal causes,
exit-7 precedence, discarded partial/exact-cap nonzero output, terminator and
canary preservation, and stdout EOF while alive timing out at 1001 ms in the
ASan run.

All before/after application and fuzzy-probe compiler stderr logs were identical
for each build mode: the same four pre-existing signedness warnings in
`calcoffsets`, `drawmenu`, and `setup`; no new warnings were introduced.
Headless checks alone are not GUI correctness evidence.

## Isolated X11 verification

The unchanged existing `tests/preview-checkpoint.py` ran against an owned
1280x900 Xvfb display allocated with `-displayfd`, never the working desktop.
The successful server used `-noreset`, `-nolisten tcp`, and file-backed stderr.
Five repeated ctypes XOpenDisplay/xdotool connection probes passed first.
All application builds used the copied original local `config.h`, which is
byte-identical to the main checkout; tested normal/ASan/UBSan `dmenu.c` bytes
also match the integrated main source.

| Accepted run | PASS reports | Exit | Distinct stress-helper starts |
| --- | ---: | ---: | ---: |
| Normal full original-baseline/current comparison | 90 | 0 | 201 |
| Normal helper-only fuzzy | 42 | 0 | 201 |
| Normal helper-only token (`-F`) | 42 | 0 | 201 |
| ASan+UBSan full corrected-baseline/current comparison | 90 | 0 | 201 |
| ASan+UBSan helper-only token | 42 | 0 | 201 |
| UBSan full corrected-baseline/current comparison | 90 | 0 | 201 |
| UBSan helper-only token | 42 | 0 | 201 |

Full normal comparison uses original pre-preview baseline `7870502...`.
Full sanitized comparisons deliberately use corrected pre-preview commit
`32fafa2...`: the original baseline still contains the independently reproduced
overflow, so it cannot be an accepted sanitized comparison executable.
Each full run includes fuzzy lifecycle checks. Actual command arrays,
environment and outcomes are in `gui-summary.json` and
`gui-support-artifacts.tar.gz`. The command form is:

```sh
DISPLAY=<owned-display> python3 tests/preview-checkpoint.py \
    --baseline /absolute/comparison/dmenu --binary /absolute/integrated/dmenu \
    --output /absolute/empty/results --isolated-display
```

Helper-only runs replace `--baseline ...` with `--helpers-only`; token coverage
adds `--token-mode`. Sanitized GUI builds retain compile/link sanitizer flags
and frame pointers. Accepted ASan+UBSan GUI runs use only
`ASAN_OPTIONS=detect_leaks=0` plus `UBSAN_OPTIONS=halt_on_error=1`; UBSan-only
runs use `UBSAN_OPTIONS=halt_on_error=1`. No address/undefined-behavior finding
is suppressed. The full original-baseline sanitizer suite is not claimed.

The full runs verify fuzzy/subsequence, token and case-insensitive matching;
selection/navigation, Return/Shift-Return/Ctrl-Return/Escape and output/status
contracts; no-preview horizontal/vertical pixel equality; split geometry and
embedding; and the existing adversarial helper lifecycle scenarios. These
include literal labels, privacy/descriptors, stale selections, 65535/65536/65537
bytes, failure/overflow precedence, delayed/blocked input/output, EOF while
alive, deadline fairness, cancel and window destruction. The source correction
did not require changing the existing harness or boundary probe.

Every run retained 200 active resource samples, 201 distinct stress-helper PIDs
and 210 group observations. The parent independently checked actual PID logs
and all indexed stderr files, rather than accepting report counts alone.
Full runs each captured 109 process stderr records; helper-only runs captured
55 each. All files exist and contain zero sanitizer diagnostics in the accepted
scoped runs. All stress groups were observed within their cancellation bounds;
no-preview sampling observed four FDs, no children and unchanged idle CPU ticks.
Normal full post-warmup RSS was 2386–2387 pages. ASan full RSS was 6874–7376
pages, within the declared 1024-page sanitizer quarantine allowance; this is
finite-workload evidence, not a claim about every future workload.

The literal-label preview and ASan fairness-timeout screenshots were visually
inspected: the unchanged selected label stays in the highlighted left pane,
the synthetic preview/status stays in the right pane, and the divider/input
geometry remains intact. Raw screenshots and measurements are retained in
the per-run `gui-*-artifacts.tar.gz` archives.

### Separate leak-enabled exits and original-baseline GUI diagnostic

Original baseline, corrected pre-preview baseline and integrated current were
also run to normal Return exits on the isolated server with
`ASAN_OPTIONS=detect_leaks=1` and `UBSAN_OPTIONS=halt_on_error=1`.
All three exited 1 and reported the same existing Fontconfig leak:
8839 bytes in 128 allocations. Their observed stdout was empty. These outcomes
are retained separately in `leak-repro-records.json` and the support archive;
they are not passing behavioral cases or leak-clean results.

The actual GUI `a`/one-item-`a` reproducer with leak detection disabled exits 1
on the original baseline's UBSan out-of-bounds load at `dmenu.c:303`; UBSan
halts before ASan in that GUI command. The corrected GUI emits exactly `a\n`,
exits 0 and has empty stderr. The independent pre-integration headless command
above retains the full ASan overflow stack, so ASan detection is not inferred
from the GUI UBSan result.

### Superseded infrastructure attempts

The first seven GUI invocations failed partially, not on a reported application
memory error: normal full reached 48 PASS before X connections stopped opening;
later invocations timed out at initial window discovery. Xvfb repeatedly reset
and wrote keymap warnings into an undrained subprocess stderr pipe. Recovery
removed this backpressure risk by using `-noreset` and regular-file stderr;
connection probes and all complete runs then passed without application or
harness edits. Initial commands, logs and partial reports remain segregated in
`superseded-gui-artifacts.tar.gz` and `superseded-gui-attempts.json`; they are
not counted as accepted coverage.

## Remaining scope limits

The known Fontconfig leak remains unfixed. Forced SIGKILL cannot perform a
final leak scan; leak-disabled GUI ASan success is not a leak-free claim.
No final preview text sanitization, wrapping, scrolling, clipmenu integration,
installation, real desktop/Xmonad or multiple-physical-monitor verification
was attempted. Trusted helper groups remain a cooperative containment
mechanism, not a sandbox for deliberately escaping processes. Automated
screenshots and finite workload measurements do not establish complete GUI
correctness on all fonts, themes, platforms or future workloads.

OpenSpec remains `add-text-preview`, with groups 1–3 at 10/21 and all eleven
remaining tasks unchecked. Only an integration evidence section was appended
to tasks.md; previous task text, checkboxes and historical evidence are retained.
This working-tree integration stops for review without staging, committing or
rewriting branch history.

## Final validation and preservation

`openspec validate --all --strict` exited 0 (one change passed, zero failed);
`openspec validate add-text-preview --strict` also exited 0. The final apply
instructions retain exactly the original task objects and progress:
21 total, 10 complete, 11 remaining. Validation outputs and command statuses
are retained in `validation-results.json` and `openspec-*.stdout/.stderr`.

The unstaged `git diff --check` exited 0. The staged check exited 2 for an
existing README Makefile-recipe example with spaces before its required tab.
That exact line is present in the saved pre-integration index diff. It was
not edited or staged away; this is a preserved pre-existing whitespace finding,
not a compiler warning or a new integration change.

The final preservation check passed: HEAD, raw index bytes and the complete
staged binary diff are unchanged. Of the original 115 checkout files, only
README, dmenu.c, AGENTS.md and an appended tasks.md evidence section changed;
the other 111 files, including local config, existing binaries/objects,
existing preview tests and all earlier evidence, retain their original bytes.
All 1560 hashed external protected files, including installed dmenu/launchers,
stest, Xmonad configuration and binaries, are unchanged.

`preservation.json` records exact hashes and staged/unstaged/untracked paths;
`git-status-final.txt` is the full final porcelain status, including all 135
untracked files (97 pre-existing plus 38 new regression/evidence files).
`integration-session.diff` records this session's focused changes against the
recovery snapshot, without conflating the existing staged preview delta.
The snapshot and temporary test workspaces remain outside the repository;
retained repository evidence contains only synthetic test data and diagnostics.
