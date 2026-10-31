# Checkpoint 4A: safe text preparation and soft wrapping

## Scope and provenance

- Worktree: `/home/avo/Projects/private/dmenu-preview-next`; branch: `dev/add-text-preview-next`.
- Starting and current HEAD: `34b0888632fca8100531f938dff6e59c070db683`. No commit, index staging or installation was performed.
- The worktree was created only after inspecting worktrees, branch names, the destination and the approved branch SHA. `make config.h` generated distributed defaults; no private configuration was copied.
- No-preview comparison baseline: corrected pre-preview commit `32fafa2ce7daf1a683e3039bbaf7a9007823e986`, built from `git archive` in a disposable directory. The approved asynchronous checkpoint was built separately as an additional reference.
- Original checkout root files and index retain their recorded hashes; the approved checkpoint branch is unchanged. See `preservation.json`. Builds and runtime artifacts stayed in `/tmp/dmenu-preview-4a-tttbbkv1`; only the requested default `config.h` was generated in the development worktree.
- Only OpenSpec task 4.1 was newly marked complete: **11/21**. Scrolling, additional bindings, clipmenu, deployment, installation, Xmonad and real clipboard/history access remain excluded. Earlier evidence is unchanged.

## Exact source changes

`source.diff` is the complete unified source/documentation diff against the approved checkpoint. `git apply --check` succeeded against its disposable source copy. `source-files.json` records additions, removals and SHA256 for each of the nine changed/new source files:

- `dmenu.c`: file-local bounded UTF-8 preparation, row offsets, incremental scheduling, cancellation and initial-row rendering.
- `drw.c`, `drw.h`: a narrow `drw_text_clip` entry point sharing the existing renderer; no new layout library.
- `tests/preview-text.c`: production-include byte/layout/lifecycle checks and a test-only delayed-metric menu mode.
- `tests/preview-text.py`: synthetic isolated-X11 text, confinement, font and pending-layout interaction checks.
- `README`, `dmenu.1`, `AGENTS.md`: implemented text rules, bounds, limitations and commands.
- `openspec/changes/add-text-preview/tasks.md`: authorization boundary, task 4.1 and checkpoint evidence.

The remaining additions are evidence files in this directory; `files.json` lists them exactly. The generated untracked `config.h` is byte-identical to `config.def.h`, not a source change. The reviewed `fuzzymatch()` bounds correction remains unchanged.

## Text and wrapping design

Raw helper bytes are never passed to the string renderer. An explicit-length UTF-8 scanner accepts valid 1–4-byte scalar encodings, rejects overlong encodings, surrogates, values above U+10FFFF and incomplete sequences, and consumes one byte for each malformed replacement. It never reads beyond the retained byte count.

LF closes a row, including empty and trailing rows. CR is replaced independently, including in CRLF; it does not move the cursor. Invalid bytes, NUL, ESC, remaining C0/C1 controls and DEL become U+FFFD. ANSI-like suffixes remain literal text, not style or escape instructions. Tabs expand to four **codepoint-column** stops, reset only by explicit LF, not soft wrapping. This is not a terminal-cell model for proportional fonts or CJK.

Each sanitized scalar is measured once with existing font metrics, not by repeatedly measuring a growing prefix. Rows soft-wrap at scalar boundaries using the available pane width after left padding. Spaces remain text; no linguistic word-breaking algorithm is introduced. A glyph wider than the pane is consumed and its ink is clipped. Zero-advance scalars still consume work and bytes. LF delimiters are stored as internal NUL separators; wrapped rows share a byte boundary. Temporary row terminators are applied only to the sanitized buffer during drawing.

The full-width input, 40/60 body split, list highlighting, divider and stable footer remain unchanged. Only initial visible rows are drawn; with `-l 1`, Ready uses the one body row. Xft ink is clipped both horizontally and vertically to each preview row, including fallback fonts and overwide glyphs. Ordinary `drw_text` calls retain the unclipped existing path.

## Ownership and bounds

One request owns the existing raw buffer plus, while preparing/ready, a display buffer and row-end array. Selection changes and failure discard preparation; exit frees it after issuing the existing prompt keyboard ungrab. No preview buffers are allocated without `-P`.

| Storage | Bound | Maximum on this 64-bit build |
| --- | --- | ---: |
| Raw collection | 65536 bytes plus existing terminator | 65537 bytes |
| Sanitized display | `4 * retained_raw_bytes + 1` | 262145 bytes |
| Row ends | at most `4 * retained_raw_bytes + 1` `size_t` entries | 2097160 bytes |
| Combined text buffers | sum of these three bounds | 2424842 bytes |

These are text-buffer bounds, not a total-process RSS limit; existing item strings, X11 state and font caches are separate. Allocation multiplications are checked before allocation. Row widths are clamped and compared by subtraction rather than unbounded cumulative pixel sums. Maximum tab expansion and maximum LF/trailing-row cases are exercised under sanitizers.

## Incremental work and lifecycle

`previewprepare_step()` processes at most 256 work units per event-loop turn. Both decoded input scalars and expanded tab spaces count, so tab expansion cannot evade the budget. Preparation runs once per `previewwait()` call, after bounded X-event servicing. Pending work sets a zero poll timeout; completion/cancellation stops those wakeups. Partial layouts are never published: drawing keeps Loading until the complete current layout is ready.

Raw collection state is distinct from preparation state. The existing collection/process-completion path clears the helper deadline before layout starts. Preparation never converts an already successful/truncated collection into Timeout. Selection reconciliation cancels the old preparation synchronously, so no obsolete layout can publish.

The subprocess protocol, 40 ms debounce, 1000 ms deadline, 65536-byte raw cap, overflow precedence, generation ownership, one child-group slot, nonblocking teardown/reaping and prompt keyboard release are unchanged. No worker, thread, generic job framework or subprocess rewrite was introduced.

## Executed verification

All GUI runs used owned disposable Xvfb servers with `-displayfd`, `-noreset`, file-backed stderr and no TCP listener. DISPLAY was set only for those owned servers. Helpers and captured content were synthetic.

| Check | Normal | UBSan | ASan + UBSan |
| --- | --- | --- | --- |
| Final clean warning-enabled C99 build | pass | pass | pass |
| Existing full no-preview baseline / lifecycle GUI suite | 90 PASS | 90 PASS | 90 PASS |
| Existing token-mode helper / resource suite | 42 PASS | 42 PASS | 42 PASS |
| Native byte-exact collection / precedence checks | 8 PASS | 8 PASS | 8 PASS |
| Native safe-text / actual late-layout lifecycle | pass | pass | pass |
| New isolated safe-text GUI / confinement checks | pass | pass | pass |

- Final builds retain exactly the same four pre-existing application signedness warnings; no new warning remains. Compiler, flags, commands and complete build/probe logs are archived.
- Native checks cover ASCII multiline/blank/trailing rows, accented Latin/Cyrillic/Greek/CJK/emoji/combining UTF-8, malformed/overlong/surrogate/out-of-range encodings, incomplete tails, all byte values, NUL/C0/C1/DEL/CR/ESC and ANSI-like sequences, four-column tabs across wraps, narrow/zero widths, zero-advance and overwide glyphs, long lines, empty input, maximum display expansion, cancellation and per-turn progress. Scanner-only test allocations have exact lengths and no string terminator.
- A real uninstrumented helper wrote 4098 bytes with a valid three-byte scalar split across the production 4096-byte read boundary. Its preparation, deliberately slowed in the test metric callback, completed at **1714 / 1717 / 1729 ms** after launch, respectively, without timeout. Production `previewstep()` continued to run between preparation chunks.
- A separate test-only menu probe uses actual Xft metrics with a small delay. GUI checks observe helper completion and reaping plus a pixel-exact Loading footer before injecting input, proving cancellation occurs during preparation rather than debounce. Escape-to-process-exit was **48 / 17 / 17 ms** including driver overhead. Selection-to-new-content observation was **184 / 181 / 185 ms**, including debounce, helper startup, screenshot capture and comparison; these are not single-key handler latencies. The `-l 1` check compares exact Ready pixels to the normal first text row, not merely to a nonblank Loading message.
- Each full and each token lifecycle run confirmed 201 real stress-helper starts, 200 active resource samples and 210 timely cooperative-group observations. No live/zombie group members remained in those observations. Raw FD/RSS samples and stderr records are retained, rather than inferred from injected key counts.
- Each build retained 172 stderr records across the final full, token and new text GUI runs; none contains sanitizer diagnostics. Existing exact 65536-byte success and 65537-byte overflow/forced-failure precedence checks passed.
- Fuzzy expected-order and original single-match regression probes passed; a 52-case fuzzy/token/case/Unicode/long-query matrix produced 156 identical cross-build outputs with native leak detection enabled.
- No-preview screenshots, selection stdout and status matched the corrected pre-preview baseline. Existing idle resource/CPU checks passed. Version and `stest -f dmenu.c` smoke contracts also matched.
- Additional actual-font smoke checks used a disposable two-font configuration: `Liberation Mono:size=8` plus `DejaVu Sans:size=48`. Oversized fallback emoji ink remained inside 16-pixel rows, including an 80-pixel-wide embedding parent where the glyph exceeded the pane width. Input, list, footer and an intentional blank row were pixel-identical to a safe-text reference; Return still emitted `alpha`.
- Strict `openspec validate --all --strict` passed; refreshed apply instructions report task 4.1 done and 11/21 overall.

### Sanitizer limits

Native probes used `ASAN_OPTIONS=detect_leaks=1` and `UBSAN_OPTIONS=halt_on_error=1`; their helper producer was the normal uninstrumented probe because the production protocol discards helper stderr. GUI ASan used `detect_leaks=0` to isolate memory-safety checks from the known unrelated Fontconfig leak. **No leak-free GUI claim is made.** Historical leak-enabled diagnostics (8839 bytes / 128 allocations) remain preserved under the earlier evidence directories; the known failure was not rerun merely to reconfirm it. Forced-kill cleanup also cannot perform a final leak scan.

Valgrind was unavailable. OpenBSD runtime, Xmonad and physical multi-monitor checks were not performed or claimed.

## Screenshots and raw evidence

Visually inspected synthetic captures:

- `unicode-multiline.png`: explicit multiline/blank rows, accented Latin, Cyrillic, Greek, CJK, emoji and combining text.
- `malformed-crlf-wrapped.png`: inert replacement characters, CRLF handling and long text.
- `narrow-oversized-font.png`: real Unicode/codepoint wrapping in a narrow pane.
- `font-clipped.png`, `font-narrow-clipped.png`: oversized fallback ink confinement, including an overwide glyph.

`lifecycle-artifacts.tar.gz`, `token-lifecycle-artifacts.tar.gz` and `text-artifacts.tar.gz` retain screenshots, helper records, stderr and resource observations. `commands-and-builds.tar.gz` retains the executed disposable orchestration, native/build logs, custom font header and provenance. Machine-readable outcomes and timings are in `summary.json`.

The executed orchestration commands were `python3 /tmp/dmenu-preview-4a-tttbbkv1/verify.py build`, `.../verify.py native`, `SKIP_TEXT=1 .../verify.py gui`, `TOKEN_ONLY=1 SKIP_TEXT=1 .../verify.py gui` and `TEXT_ONLY=1 .../verify.py gui`. The driver owns Xvfb startup/cleanup. The standalone public test commands are documented in README. The context-mode RPC timed out during the initial reference-GUI run; its completed 90-report log was recovered, but final accepted runs used the finite shell driver with explicit exit checking instead.

## Remaining limitations and stop boundary

Wrapping is by Unicode scalar, not grapheme cluster. Combining sequences, variation selectors and emoji ZWJ sequences may be split across visual rows. Complex shaping, bidi layout and missing glyph coverage remain limited by existing Xft/font behavior. Very large fallback glyphs are clipped rather than increasing row height. There is no new word-breaking or rich-text subsystem.

Only the initial visible portion is displayed. Scrolling and its bindings, scroll persistence, mouse interaction, clipmenu/cache lookup, additional display configuration and deployment are not implemented. Work stops at Checkpoint 4A for separate review.
