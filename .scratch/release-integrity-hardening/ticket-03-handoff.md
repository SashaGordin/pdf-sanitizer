# Handoff — Ticket 03 implementation (raster/vector parity, mixed-page, architect-of-record)

Status: plan approved, environment set up, **implementation not yet started**.
Written so a fresh session/context can pick this up without the prior
conversation history.

## Where things stand right now

- Worktree already created and checked out:
  `/Users/Sasha/Dev/clockwork/Taylor-Construction-worktrees/ticket-03`
  on branch `ticket-03-raster-vector-parity`, forked from `main` at commit
  `e656eaa` ("Close out ticket 01: verify five-status vocabulary and
  fail-closed handling"). Ticket 01 is `Status: done`; ticket 02 is still
  `ready-for-agent` (untouched, being worked in a separate
  `ticket-02` worktree — not your concern here).
- Venv created and deps installed in that worktree:
  `.venv-anonymizer/bin/python` has `fitz`, `PIL`, `reportlab`, `zxingcpp`
  importable. `tesseract` (5.5.2) and `gs` are on `PATH` via Homebrew.
- **Open loose end, check this first**: running
  `.venv-anonymizer/bin/python -m unittest discover -s tests -q` in the
  fresh worktree printed `The local denylist is missing or invalid; derive
  or supply it before processing` and the run was not confirmed clean.
  `config/denylist.local.json` is gitignored (NDA-material convention, see
  `AGENTS.md`'s "Local-only assets" section) so a fresh worktree checkout
  won't have it — but the unit tests call `sanitizer.sanitize_document(...)`
  directly with an in-memory `FAKE_TERMS` set (see
  `tests/test_anonymize_construction_pdfs.py`'s `SanitizerTests.run_sanitizer`),
  they should **not** need `config/denylist.local.json` on disk at all. Before
  writing any new code, figure out why that message appeared — likely just
  needs `-v` output / a clean re-run to see whether it's a real failure or a
  red herring (e.g. a stray `print`/import-time check, or the message came
  from a different, unrelated command run earlier in the same shell). Do not
  copy the *actual* project's `config/denylist.local.json` from the main
  checkout into this worktree as a "fix" without understanding why it's being
  requested — the test suite is designed not to need it.
- No source code has been modified yet. `tools/anonymize_construction_pdfs.py`
  is 2421 lines, `tests/test_anonymize_construction_pdfs.py` is 1041 lines —
  matches what's described below exactly (confirmed after the ticket-01
  closeout commit landed).
- A task list exists in this session (TaskCreate/TaskUpdate) with 6 tasks;
  task 1 ("Set up isolated worktree and venv") is `in_progress` and should be
  marked `completed` once the denylist loose end above is resolved. Tasks
  2–6 below are still `pending`. A fresh session won't see this task list —
  recreate one if useful, or just work through the plan directly.

## The task

Implement ticket 03 end-to-end using the **implement** skill, following the
plan below and its checklist. Full ticket file:
`.scratch/release-integrity-hardening/issues/03-raster-vector-parity-and-mixed-page-remediation.md`
(all 6 checkboxes currently unchecked, `Status: ready-for-agent`). Required
reading before starting, if not already fresh in context: `AGENTS.md`,
`ANONYMIZATION.md`, `.scratch/release-integrity-hardening/spec.md`
(especially "Implementation Decisions" and "Testing Decisions"), and the
ticket file itself.

Use `Specs-MLK-Recreation-Center.pdf` / `MEP-Drawings-MLK-Recreation-Center.pdf`
(repo root) plus `tests/golden/mlk_labels.json` as needed for parity/regression
testing, and extend `tests/test_anonymize_construction_pdfs.py` following its
existing synthetic-PDF-in-memory pattern for the new synthetic cases (mixed
page, suppressible raster false positive, architect-of-record stamp).

When done: check off every box in the ticket file and set its `Status:` line
to `done`. Run `.venv-anonymizer/bin/python -m unittest discover -s tests -q`
and `.venv-anonymizer/bin/python tools/eval_sanitizer.py`, then commit on
this branch (**no** `Co-Authored-By: Claude` line — this user's global git
preference). Report the branch name and a summary of what was built.

---

## Approved plan

### Context

This repo is a local PDF anonymization tool for construction documents. A
current hardening cycle (`.scratch/release-integrity-hardening/spec.md`) is
closing three related, previously-measured defects, all confirmed by direct
reads of `tools/anonymize_construction_pdfs.py` as it stood at plan time
(line numbers below are anchors from that read; re-anchor by function name
if they've drifted — they should not have, since the file's line count is
unchanged since then):

1. **`ocr_detection_boxes()` (line 1483) has no lexicons, no
   `candidate_suppression()`, and no label-alone-line lookahead**, while the
   vector path's `line_detections()` (line 1229) has all three. Its only
   caller, `raster_page_pdf()` (line 1512), treats *any* OCR hit after
   redaction as a fatal residual — including exactly the kind of noise
   (`"Owner"`, a CSI MasterFormat code, a manufacturer name) the other three
   surfaces are configured to ignore — and that `RuntimeError` becomes a
   `PageProcessingError` with **no containment anywhere**: it propagates out
   of `sanitize_document()`, out of `main()`'s `except PageProcessingError`
   (line 2386), aborting the entire multi-document batch, discarding every
   already-processed report, and never writing the report JSON (exit code 3).
2. **`min_text_chars = 20`** (`Settings`, line 714; used at line 2146) is a
   flat character-count threshold blind to embedded images — a page with 20
   characters of vector text and a full-page sensitive scan is classified
   "searchable" and never OCRed.
3. **Architect-of-record** (firm name + city/state) must be redacted by
   default per `CONTEXT.md`'s resolved policy, not treated as an assumed
   intentional disclosure. The word "architect" is already in the vector
   path's `LABEL_NAME` alternation (~line 102), so `Architect: <firm>` already
   works there — the real project's actual leak (documented in
   `config/denylist-classification.local.json`'s `open_question` note) was
   the firm's full legal name and city/state surviving in cleartext, now
   patched only as a per-project, git-ignored `denylist.local.json` entry.
   Ticket 03 needs a synthetic test proving the *generic* mechanism (not a
   per-run denylist string) catches this — which only becomes possible once
   the raster path can run label-following logic at all, tying this item to
   item 1.

Two implementation designs were explored in depth during planning (a
minimal-footprint tuple-based patch, and a fully unified `Detection`-based
model per `PRODUCTION-READINESS-PLAN.md`'s Phase 2 recommendation). The
unified design is materially larger (new dataclasses, surgical per-image
OCR + affine-transform coordinate remapping, a `tempfile.TemporaryDirectory`
hoist across all of `sanitize_document`) to buy a capability — direct
in-place pixel redaction of a single embedded image, as opposed to
rasterizing the whole page — that **no checkbox actually requires** (item 4
only requires embedded-image content be "proven redacted," and this
project's own documented philosophy is to prefer rasterizing a whole page
over a low-confidence targeted redaction — OCR-only discovery is
definitionally that low-confidence case). Per this repo's own engineering
norms (small, justified diffs; no speculative abstraction), **this plan
takes the minimal-footprint design as the base**, adopting only one
correction the unified design got right and the minimal one did not:
per-page failure containment must be scoped narrowly (see item 4 below) or
it silently breaks two existing, currently-passing regression tests
(`test_fails_closed_with_page_number_when_ocr_is_unavailable`,
`test_tesseract_timeout_fails_closed_per_page` — both call
`sanitize_document` directly via `run_sanitizer()` and assert
`PageProcessingError` still propagates for those failure modes). Confirmed
directly by reading both tests.

### Approach

#### 1. Fix `ocr_lines()`'s latent ordering gap (prerequisite for item 2)

`following_value_lines()` (vector path, line 922) relies on
`lines_from_page()` returning lines sorted top-to-bottom. `ocr_lines()`
(line 1416) has no such guarantee — Tesseract runs with `--psm 11` ("sparse
text... no particular order"), so a page-order-dependent "value line(s)
below this label" rule would be fragile without an explicit sort. Add one
line to `ocr_lines()` sorting its result by `(min(top), min(left))` before
returning. Pure bugfix: its one existing caller (`raster_page_pdf`'s
OCR-text-reinsertion loop) inserts each line independently and doesn't
depend on order, so no existing test's behavior changes.

#### 2. Bring `ocr_detection_boxes()` to parity

New signature (return shape unchanged — still `list[tuple[box, category]]`,
consistent with its one caller):
```python
def ocr_detection_boxes(
    words: Sequence[OcrWord],
    denylist: DenylistMatcher,
    lexicons: Lexicons | None = None,
    scale: float = 1.0,
    suppressed_counts: collections.Counter[str] | None = None,
    suppressed_categories: collections.Counter[str] | None = None,
) -> list[tuple[tuple[int, int, int, int], str]]:
```
- For every `DIRECT_PATTERNS` match and every denylist match (existing
  per-paragraph loops), call `candidate_suppression(category, matched_text,
  lexicons, context=containing_line(paragraph.text, start, end))` before
  appending to results; on suppression, increment
  `suppressed_counts[reason.split(":")[0]]` and
  `suppressed_categories[category]` (when the counters were passed) and skip
  the box — mirrors `line_detections()`'s identical per-match suppression
  call, and satisfies ANONYMIZATION.md's *"every suppressed candidate is
  counted and attributed... never silently dropped"* invariant, which is
  part of the shared suppression contract this ticket must extend to the
  raster path, not an optional nicety.
- Same-line `LABEL_VALUE_RE` match (existing per-segment loop): same
  suppression treatment, category `"labelled_identifier"`.
- **New**: iterate `ocr_lines(words)` (now order-fixed); for any line
  matching `LABEL_RE` alone, look ahead via a new
  `following_value_ocr_lines(ordered_lines, index, scale)` helper — the
  pixel-space analog of `following_value_lines()` (line 922): same "next up
  to 3 lines, `redactable_phrase`, ≤10 words, not itself a label" rule, same
  120-point vertical-gap cutoff except converted through `scale` (pixels per
  point = `settings.ocr_dpi / 72`, the exact ratio `raster_page_pdf` already
  computes via `image, scale = page_image(page, settings.ocr_dpi)` and
  already uses to convert pixel coordinates back to page points). Each
  yielded value line goes through `candidate_suppression("labelled_identifier",
  value, lexicons, context=value)` exactly like its vector-path counterpart.
- Table-cell-crossing suppression (`cells`/`LazyTableCells`) is **not**
  threaded in: a rasterized page has no vector table geometry to cross, and
  the vector-specific `crossings()` check has nothing to operate on here.
  Document this scope boundary in a short comment.

Thread `lexicons` and `scale` through `raster_page_pdf()`'s (line 1512) two
`ocr_detection_boxes` calls (both the pre-redaction detection call and the
post-redaction residual self-check), plus the new suppression counters
**only on the first (pre-redaction) call** — the residual self-check is an
internal safety verification, not a second detection pass whose suppressions
need independent audit-counting (the vector path doesn't double-count either;
its analogous second pass, `sweep_flattened_output`, has its own separate
`sweep_suppressed` counter merged once via `suppressed_counts.update(...)`
in `sanitize_document`). `raster_page_pdf()` gains a `lexicons: Lexicons |
None = None` parameter and, for the counters, accepts and forwards two
`Counter`-typed parameters exactly the way it already accepts and mutates
`categories: set[str]` in place today — no new pattern, just one more
mutable output parameter. Its one caller (`sanitize_document`'s second
rebuild loop, ~line 2248) passes `lexicons` (already a `sanitize_document`
parameter, currently never threaded to this call) and the document's
`suppressed_counts`/`suppressed_categories` counters (already local
variables in `sanitize_document`, currently only fed by the first/vector
loop).

#### 3. Replace `min_text_chars` with an image-ratio-aware rule

Rename `Settings.min_text_chars: int = 20` (line 714) to
`min_vector_text_chars: int = 20` (same meaning: "essentially no
extractable vector text at all" — still needed standalone for a page with
neither text nor a qualifying image, e.g. outlined/non-extractable glyphs,
which is explicitly out of this ticket's scope). Add
`raster_image_area_ratio: float = 0.02` (2%): deliberately low, since a
typical 1.5"×1.5" architect's seal on a letter page is already ~2.4% of page
area, and the project's fail-closed philosophy favors erring toward more
raster passes over a possible miss.

New function, placed beside `repeated_margin_images()` (line 1174), reusing
its exact `page.get_image_info(hashes=False, xrefs=True)` /
`rect.width*rect.height/page_area` idiom:
```python
def page_raster_ratio(page: fitz.Page) -> float:
    """Fraction of the page area covered by displayed raster images."""
```
and:
```python
def page_needs_raster_pass(text_length: int, raster_ratio: float, settings: Settings) -> bool:
    """Replaces the flat min_text_chars threshold. True if the page has too
    little vector text to trust on its own, OR carries enough embedded
    raster-image area that sensitive content could be hiding in pixels the
    vector path never inspects — the mixed-page case a flat character count
    always missed."""
```

Call site (`sanitize_document`'s first loop, line 2146) changes from the
single `len(...) < settings.min_text_chars` comparison to:
```python
lines = lines_from_page(page)
text_length = len(normalized(" ".join(line.text for line in lines)))
if page_needs_raster_pass(text_length, page_raster_ratio(page), settings):
    raster_required.add(page_number)
    continue
```
Same shape, same `try/except` coverage as today — a page that now qualifies
as "mixed" is routed into the existing `raster_required` set and rebuilt by
the (now parity-upgraded) `raster_page_pdf()` in the second loop exactly
like any other raster page; no new rebuild code path is needed, since
`page_image()`/`get_pixmap()` already flattens the *entire* page (vector
text and embedded images alike) into one bitmap that the upgraded
`ocr_detection_boxes()` now scans with full suppression/label-following
parity. This directly satisfies checklist item 4 ("its embedded-image
content is proven redacted") without new redaction machinery.

Update `load_settings()` (~line 959: replace the `min_text_chars=` parse
line with `min_vector_text_chars=` and add `raster_image_area_ratio=
float(raw.get("raster_image_area_ratio", 0.02))`) and
`config/sanitizer.json` (rename the key, add the new one). **Required
accompanying fix in the same change**: `tests/test_anonymize_construction_pdfs.py`'s
`SanitizerTests.setUp()` (line ~193) constructs
`sanitizer.Settings(..., min_text_chars=20, ...)` — this becomes a
`TypeError` the moment the field is renamed; update the keyword there.

Document the new rule in `ANONYMIZATION.md`'s "Security model" bullet list
(where the existing `min_text_chars`-adjacent behavior — vector flattening,
raster fallback — is already documented), satisfying the checklist's
"documented replacement rule" wording literally.

#### 4. Contain a raster-path residual to one page — scoped narrowly

**Only** the post-redaction "unresolved identifier residual" check inside
`raster_page_pdf()` changes from raise-and-abort to contain-and-continue.
Every other failure mode in that function — missing/broken Tesseract
executable, a Tesseract subprocess timeout, the barcode-residual-after-3-tries
check, any other exception — **keeps raising `PageProcessingError` exactly as
today**. This scoping is required, not optional: a blanket catch around the
whole `raster_page_pdf()` call would silently swallow the two scenarios
`test_fails_closed_with_page_number_when_ocr_is_unavailable` and
`test_tesseract_timeout_fails_closed_per_page` currently assert *do* raise
out of `sanitize_document` (verified by reading both tests directly — they
call `run_sanitizer`/`sanitize_document` on a single document and assert
`assertRaises(sanitizer.PageProcessingError)`), and
`test_ghostscript_timeout_fails_closed_without_hanging` must also keep
raising unmodified (unrelated code path, page number `0`).

Change `raster_page_pdf()`'s contract for this one case: instead of
`raise RuntimeError("residual identifier detected after raster redaction")`,
when the second (post-redaction) `ocr_detection_boxes(...)` call returns any
hit:
```python
apply_image_boxes(image, [(0, 0, image.width, image.height)])  # blacken the whole rendered page
failure_reason = "residual identifier detected after raster redaction"
```
and **skip** the loop that re-inserts the sanitized OCR text layer via
`out_page.insert_text(..., render_mode=3, ...)` for this page — reinserting
invisible-but-copyable text over a page whose pixels could not be confirmed
clean would defeat the whole point of the fail-closed branch. Build the
one-page output PDF exactly as today (still `out_page.insert_image(...)` of
the now-fully-black image), and change the function's return type to
`tuple[bytes, str | None]` (`data, failure_reason`), with `failure_reason`
`None` on the normal/success path. All *other* exception paths in the
function are unaffected and continue to raise `PageProcessingError` as
today.

Caller (`sanitize_document`'s second loop, ~line 2248):
```python
page_pdf, failure_reason = raster_page_pdf(
    page, settings, matcher, temp_dir, page_categories[page_number],
    lexicons, suppressed_counts, suppressed_categories,
)
output.insert_pdf(fitz.open("pdf", page_pdf))
if failure_reason is not None:
    raster_page_failures.append({"page": page_number, "reason": failure_reason})
```
`raster_page_failures: list[dict]` is a new local, accumulated across the
loop. **No change needed in `main()`** — because `sanitize_document` no
longer raises for this specific case, `main()`'s existing per-document loop
(line 2377) naturally proceeds to the next document; the report gets
written; `all_automated_checks_pass`/`derive_release_status` correctly
reflect the affected document's `FAIL` without the batch aborting. This is
exactly "the run does not abort," achieved by removing the one source of
the abort rather than adding a second containment layer in `main()`.

Report/gate wiring — reuse the existing generic
`automated_gate_status(checks)` (`all(checks.values())`, line 160), so
`checks` and `release_status` can never disagree: `verify_output()` (line
1899) gains a `raster_page_failures: Sequence[dict] = ()` parameter, adds
`"raster_page_verification": not raster_page_failures` to its `checks` dict
(line ~2044), and returns `"raster_page_failures": list(raster_page_failures)`
in its result dict. `sanitize_document`'s call to `verify_output` passes the
`raster_page_failures` list through, and `sanitize_document`'s own returned
dict includes `"raster_page_failures": raster_page_failures` alongside the
existing `**verification` splice, following the same `{"page": N, ...}`
dict-list idiom already used by `residuals`/`page_redactions`.

**Also in scope for this step** (named explicitly in `spec.md`'s
Implementation Decisions): remove the hard-coded
`"raster_ocr_from_sanitized_images_only": True` line from `verify_output()`'s
`checks` dict (~line 2055) — not replaced by a separate measurement, per the
spec's own reasoning that this cycle's rewritten detection makes the
property true by construction rather than needing to assert it.

#### 5. Architect-of-record synthetic test

A **mixed** page (ordinary vector technical text, e.g. `"TECHNICAL: ASTM
A36 structural steel"`, comfortably above `min_vector_text_chars`, plus one
embedded raster image sized well above the 2% ratio threshold — e.g. a
300×260pt placement on a letter page). Build the image the same way
`create_scanned_pdf` already does (small `reportlab` canvas → `fitz`
`get_pixmap()` → PIL `Image` → `page.insert_image(rect, stream=...)`),
containing three baked-in lines:
```
ARCHITECT OF RECORD
Fakename Architecture Group
Someplace, ZZ 00000
```
("ARCHITECT OF RECORD" alone on its own line specifically exercises the
*new* label-alone/following-lines lookahead, not the same-line
`LABEL_VALUE_RE` form the vector path already handled before this ticket.
`"ZZ"` is a deliberately non-real state abbreviation so the `city_state_zip`
direct-pattern regex doesn't independently fire, isolating the proof to the
label-following mechanism.) Run through `sanitizer.sanitize_document(...,
lexicons=sanitizer.load_lexicons(REPO_LEXICONS, REPO_ALLOWLIST))` with the
existing `FAKE_TERMS` denylist, which contains **none** of "Fakename
Architecture Group"/"Someplace" — proving the generic mechanism, not a
per-run denylist entry, does the work.

Assert: `report["rasterized_pages"]` includes the fixture's page (proves the
new mixed-page classification routed it, not the vector path); `report["release_status"]
== sanitizer.RELEASE_STATUS_AUTOMATED_PASS`; the firm name/city (case-folded)
are absent from both `output[0].get_text("text")` and the raw output bytes
(`destination.read_bytes()`, matching the existing raw-bytes-check
convention at line 230); `"ASTM A36"` still survives.

#### 6. New unit tests (`tests/test_anonymize_construction_pdfs.py`)

Follow existing conventions exactly: module-level fixture builders,
`REPO_LEXICONS`/`REPO_ALLOWLIST` constants (line 650-651) for real-lexicon
tests, direct `sanitizer.sanitize_document(...)` calls, assertions on the
returned report dict and re-opened output PDF text/bytes — never on
internal call counts (per `spec.md`'s testing guidance).

- **Parity test**: build the same content two ways — plain vector
  (`canvas.Canvas` + `drawString`) and scanned (`create_scanned_pdf`-style)
  — using a suppressible structural fragment already validated end-to-end by
  the existing `SuppressionSymmetryTest` (e.g. `"3 CIR"`, suppressed via the
  `panel_circuit_reference` structural-lexicon rule) plus a genuine
  identifier that must not survive on either path. Run both through
  `sanitize_document(..., lexicons=sanitizer.load_lexicons(REPO_LEXICONS,
  REPO_ALLOWLIST))`. Assert both reach `AUTOMATED_PASS`, both preserve the
  suppressible fragment, both redact the genuine identifier.
- **Per-page containment test**: a 2-page scanned document (generalize
  `create_scanned_pdf` to accept per-page line lists, or add a small sibling
  helper). Mock `sanitizer.run_tesseract_tsv` (or `ocr_detection_boxes`
  directly, whichever gives a cleaner deterministic seam) so page 2's
  post-redaction residual check reports a hit while page 1 processes
  normally. Assert `run_sanitizer(...)` does **not** raise; `report["raster_page_failures"]
  == [{"page": 2, "reason": "residual identifier detected after raster redaction"}]`;
  `report["release_status"] != sanitizer.RELEASE_STATUS_AUTOMATED_PASS`; page 1's
  content is correctly redacted/preserved (proving the *other* page
  processed normally, not just that nothing crashed). Also re-run the three
  existing tests named in item 4 unmodified and confirm they still pass
  unchanged.
- **Mixed-page embedded-image test**: vector text + an embedded image
  containing a `FAKE_TERMS` phrase (e.g. `"Fictional Owner Holdings"`), run
  with the standard denylist. Assert the page appears in
  `report["rasterized_pages"]` and the term is absent from output
  text/bytes — the exact case `spec.md`'s Testing Decisions name.
- **Classification unit test**: direct test of `page_needs_raster_pass()`
  (no PDF needed): low text + no image → `True`; ample text + image ratio
  below threshold → `False`; ample text + image ratio at/above threshold →
  `True`. Plus `sanitizer.Settings(min_text_chars=20)` now raises
  `TypeError` — proves the old field is actually gone.
- **Architect-of-record test**: exactly as designed in item 5.

### Sequencing

1. `ocr_lines()` sort fix (item 1) — verify full suite unchanged.
2. `ocr_detection_boxes()` parity + `following_value_ocr_lines()` +
   threading `lexicons`/`scale`/counters through `raster_page_pdf()` and its
   caller (item 2) — verify full suite unchanged (no existing test passes
   `lexicons=` on a scanned-page run, so behavior is unchanged until new
   tests exercise it). Add the parity test.
3. `min_text_chars` → `min_vector_text_chars` + `raster_image_area_ratio` +
   `page_raster_ratio`/`page_needs_raster_pass` + call-site update +
   `load_settings`/`config/sanitizer.json` + the required `SanitizerTests.setUp`
   rename (item 3) — verify full suite. Add the classification test and the
   mixed-page embedded-image test.
4. Per-page containment: `raster_page_pdf()`'s return-tuple contract,
   blacken-and-skip-text-layer on residual, caller wiring,
   `verify_output()`'s new parameter/check, removal of the hard-coded
   `raster_ocr_from_sanitized_images_only` (item 4) — verify full suite,
   especially the three pre-existing raster-failure tests named above stay
   green. Add the containment test.
5. Architect-of-record test (item 5/6) — integration proof exercising all of
   the above together.
6. Document the new classification rule in `ANONYMIZATION.md`.
7. Check off all six boxes and set `Status: done` in
   `.scratch/release-integrity-hardening/issues/03-raster-vector-parity-and-mixed-page-remediation.md`.
8. Full regression: `.venv-anonymizer/bin/python -m unittest discover -s tests -q`
   then `.venv-anonymizer/bin/python tools/eval_sanitizer.py` (must show
   recall 100%, over-redaction 0%, matching the release gate in
   `ANONYMIZATION.md`).
9. Commit on the `ticket-03-raster-vector-parity` branch (no
   `Co-Authored-By: Claude` line, per this user's global git-commit
   preference).

### Verification

- `.venv-anonymizer/bin/python -m unittest discover -s tests -q` — full
  suite green, including all new tests and the three pre-existing raster
  fail-closed tests unmodified.
- `.venv-anonymizer/bin/python tools/eval_sanitizer.py` — RECALL 100%,
  OVER-REDACTION 0% (the release gate), unaffected by this change since it
  operates purely on policy-vocabulary matching, not PDF processing.
- Manually inspect one run's report JSON for the new `raster_page_failures`
  field shape and the removed `raster_ocr_from_sanitized_images_only` key.
