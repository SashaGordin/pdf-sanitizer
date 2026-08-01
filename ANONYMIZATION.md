# Local construction-plan PDF sanitizer

This repository contains a deterministic sanitizer that runs entirely on the
local machine. It does not call an AI model, web API, cloud OCR service,
telemetry service, or remote logger. Never run it in an online notebook or
upload source PDFs to install dependencies.

## Install

Tesseract is required for scanned pages and secure raster fallbacks:

```bash
brew install tesseract
brew install ghostscript
python3 -m venv .venv-anonymizer
.venv-anonymizer/bin/python -m pip install -r requirements-anonymizer.txt
```

## Configure and run

### The denylist is the actuator, not a detector

The denylist is what turns "this term is established as sensitive" into "every
occurrence of it is destroyed, exactly, with correct coordinates." It is the
point every discovery path converges on: regexes, the NER review layer, and a
human reviewer all *propose*, and the denylist *acts*. Getting a term onto it
is worth far more than any detector improvement — on the reference specs, 36
terms produce ~2,490 exact redactions, including a project number that appears
in the footer of 1,002 of 1,475 pages.

The vocabulary splits into two halves that scale in opposite directions:

| | `config/denylist.local.json` | `config/allowlist.shared.json` |
|---|---|---|
| Contents | This project's identifiers | Manufacturers, standards bodies, brands |
| Size | ~20–40 terms | Grows without bound |
| Lifetime | Per project | Forever, across every project |
| Source | Project intake | Confirmed review decisions |
| Git | Ignored, mode 0600, NDA material | Tracked; no project information |

Per-project work stays small because you already have the data. The expensive
half is built once and every project makes the next one quieter.

**Denylist precedence is absolute:** a term in both files is redacted. That
matters when a manufacturer is genuinely a party on a particular job.

### 1. Seed from project intake (primary path)

Copy `config/project-metadata.example.json` to
`config/project-metadata.local.json` and fill in the project name, number,
address, owner, architect, engineers, contractors, consultants, and personnel.
All of it is known before the PDF is opened. List every spelling variant that
appears in the documents — full legal name, short form, all-caps title-block
form, abbreviation — because matching is exact (whitespace-tolerant and
case-insensitive), not fuzzy.

```bash
.venv-anonymizer/bin/python tools/anonymize_construction_pdfs.py \
  source-file-1.pdf source-file-2.pdf \
  --project-metadata config/project-metadata.local.json
```

`--project-metadata` and `--denylist` merge when both are supplied.

### 2. Catch what intake missed (proposal pass)

`--propose-denylist` scans the sources for labelled fields and firm-name
patterns, filters them through the shared lexicons, and writes *unconfirmed
candidates* to `config/denylist.candidates.json` with mode `0600`. It writes
no live denylist and processes nothing:

```bash
.venv-anonymizer/bin/python tools/anonymize_construction_pdfs.py \
  source-file-1.pdf source-file-2.pdf --propose-denylist
```

Each candidate carries why it was proposed and how many times it occurred.
Copy the genuine identifiers into the denylist and re-run without the flag.

This flag was previously `--derive-denylist` and wrote straight into the live
denylist. That produced 160 terms of which 136 were contract boilerplate, spec
prose, product names, or extraction garbage — each one a global destructive
redaction rule that silently removed technical content while the run still
reported PASS. The old name still works as an alias, with the new behaviour.

### 3. Shared lexicons

`config/lexicons/` holds the project-independent vocabulary every detector
layer consults through one suppression entry point:

- `contract_defined_terms.json` — AIA/EJCDC defined terms (`Owner`,
  `Contractor`, `Bidder`). Suppressed only when the *entire* span equals the
  bare term, so `City of Springfield` survives while `City` does not.
- `structural_patterns.json` — CSI MasterFormat codes, standards designations,
  dimension callouts, panel-schedule cells, letter-spaced title-block labels.
  Also holds the manufacturer trigger phrases and the CSI Division/Section/Part
  zone markers.
- `boilerplate.json` — recurring legal and drawing boilerplate, plus the rules
  that stop the proposal pass from suggesting prose or garbage.

Suppression applies to detector *candidates* only. It never overrides a
denylist match, and it never removes text by itself.

Outputs are written as `output/pdf/sanitized_document_01.pdf`, and so on. The
local JSON review report contains only hashes, counts, page numbers, redaction
categories, and masked residual shapes (letters become A/a, digits become 9).

> Report hygiene caveat: `masked_shape` is a strict one-to-one character map,
> so a report held *alongside its output PDF* can have distinctive long spans
> reconstructed by matching shapes against the output text. Store the report
> separately from the PDF if it is shared.

Edit `config/sanitizer.json` to enable normalized `[x0, y0, x1, y1]`
rectangles for known cover, title-block, revision, seal, logo, or approval
areas. Coordinates range from 0 to 1 relative to the page.

## Optional NER review layer (report-only)

A local zero-shot NER model (GLiNER) can flag *unlisted* names, firms,
projects, and places that no regex, label, or denylist term covers. It is
report-only: findings never change redactions or the automated verdict — they
are triage candidates for the human reviewer.

One-time install (the model is downloaded once, like a Tesseract language
pack; the sanitizer itself never accesses the network and refuses to run NER
without the local model directory):

```bash
.venv-anonymizer/bin/python -m pip install -r requirements-anonymizer-ner.txt
.venv-anonymizer/bin/hf download urchade/gliner_multi_pii-v1 \
  --local-dir models/gliner_multi_pii-v1
# The GLiNER repo ships only the weights; its base-model tokenizer must be
# cached once while online (a few MB into ~/.cache/huggingface), because the
# sanitizer blocks all network access when it loads the model:
.venv-anonymizer/bin/python -c \
  "from gliner import GLiNER; GLiNER.from_pretrained('models/gliner_multi_pii-v1'); print('cached')"
```

Enable per run with `--ner-report`, or persistently via the `ner` block in
`config/sanitizer.json`. The report gains a per-document `ner_review` section.

**Findings are deduplicated by surface form.** A raw run on the reference specs
produced 11,923 findings, but those are not 11,923 decisions — recurring
boilerplate dominates the count, and an occurrence-capped list burned its
entire budget in the first 97 of 1,475 pages while the long tail, where an
unlisted party name actually hides, was never shown to anyone. Each entry is
now one `(label, surface form)` pair carrying `occurrences`, the pages it
appears on, `score_max`, its CSI `zone`, and any manufacturer-context
`evidence`. `finding_counts` still reports total occurrences per label;
`distinct_form_counts` reports the number of decisions. `max_findings` now caps
distinct forms, so the cap spans the whole document.

Note what this layer can and cannot do. GLiNER answers a *type* question —
"is this span an organization?" — and it answers correctly: Cooper Lighting is
an organization. The NDA asks a *role* question: is this a party to the
project, or a manufacturer named in a materials spec? Nothing in the span
distinguishes them, only its role in the document, and score does not separate
the two (measured: noise at 0.94–0.99, genuine catches at 0.68). Threshold
tuning is therefore not a useful lever. Treat this layer as a proposal
generator feeding the denylist, never as a redaction detector.

CPU inference on a 1,475-page document takes tens of minutes.

## Security model

- Direct identifiers are located from text coordinates using deterministic
  regular expressions, labelled fields, and the project denylist.
- The two detector families get different text scopes on purpose. Direct
  patterns are shape heuristics and stay **block-scoped**, because letting
  them span blocks stitches unrelated fragments into phantom addresses. The
  denylist is **page-scoped**: its terms are exact strings already established
  as sensitive, so matching one across a block boundary carries none of that
  risk. Block-scoping the denylist previously hid the architect's name on
  every sheet of a drawing set — the title block split
  `CCR Architecture &` and `Interiors` into separate MuPDF blocks, so neither
  detection nor verification could see the phrase, and the run reported PASS
  with the name still on the page.
- The page-level scan is assembled in MuPDF's own `(block, line)`
  content-stream order, not in `(y0, x0)` geometry order. Geometric sorting
  interleaves unrelated lines on multi-column and rotated layouts — it put
  `CCR ARCHITECTURE &` beside `AS BEING NECESSARY TO PRODUCE` instead of
  `INTERIORS`, and `James T.` beside `by` instead of `Vickers` — so a
  page-scoped scan over that order still missed most of what it was added to
  catch. `PageScopeTest` pins this down.
- Denylist matching tolerates how PDF text actually renders an identifier.
  The same string appears as `CCR-21109`, `CCR - 21109`, and `CCR` + newline +
  `21109` across one document, and `King Jr. Recreation` also appears as
  `KING JR RECREATION`. Separators around a hyphen are optional and a trailing
  abbreviation period is optional; a space still requires at least one
  separator, so `Owner Holdings` never matches `OwnerHoldings`.
- Candidates from the regex, label, and NER layers pass through one shared
  suppression decision built from `config/lexicons/` and the shared allowlist.
  Detection, the post-flatten sweep, and verification all consult it, so the
  three can never disagree — flagging in verification what detection
  deliberately left alone would fail every run on its own suppressed noise.
  Denylist matches are never suppressed.
- Suppression also considers the line a candidate sits on. Overlapping
  detectors otherwise defeat it: suppress the email in an AIA copyright notice
  and the URL pattern immediately claims the same rectangle.
- A match crossing two or more cells of a detected table is treated as the
  regex stitching separate schedule cells into a phrase. A genuine address in
  a title block occupies one cell and is unaffected.
- Every suppressed candidate is counted and attributed in the report
  (`suppressed_by_rule`, `suppressed_by_category`), never silently dropped, so
  a suppression rule can be audited and reverted.
- QR codes and common 1D/2D barcodes are located with the local ZXing engine.
- Repeated small margin images are treated as likely logos and redacted.
- Vicinity/location/site map areas are removed when their labels are detected.
- Text redactions use MuPDF's destructive redaction engine.
- Metadata, XMP, bookmarks, JavaScript, annotations, links, form fields,
  attachments, thumbnails, hidden text, and response data are scrubbed.
- A page is routed to the raster path if it has too little vector text to
  trust on its own (`min_vector_text_chars`, default 20) OR its embedded
  raster images cover at least `raster_image_area_ratio` of the page (default
  2%) — a mixed page (ordinary vector technical text plus a full-page or
  large embedded image) is no longer classified as pure "searchable" and
  skipped; its image content is inspected the same way a fully scanned page
  is. This replaces a flat vector-character-count threshold that a mixed
  page could slip past uninspected.
- Every searchable page is locally vector-flattened with Ghostscript to remove
  optional-content layers while retaining text. MuPDF is the secondary vector
  backend; if both safe flattening paths fail, the page is rasterized locally.
- Scanned/rasterized pages are OCRed locally, redacted as images, OCRed again,
  and rebuilt using only the sanitized second OCR pass as the text layer. The
  raster OCR pass applies the same lexicon suppression and label-following
  lookahead as the vector path, so both paths enforce one policy.
- After flattening, the assembled output is scanned and redacted a second
  time (post-flatten sweep): flattening rewrites content streams, so text can
  extract differently afterwards, and verification must only ever see a text
  stream a detector has already swept.
- A failed page stops the run and reports only its page number and failure
  category — except a suppressible false positive surviving the raster
  path's post-redaction safety check, which fails only that page
  (`raster_page_failures` in the report; the whole rendered page is blackened
  and its OCR text layer is not reinserted) rather than aborting the run.
- Temporary directories use neutral names under `tmp/pdfs/` and are deleted
  automatically.

## Tests

```bash
.venv-anonymizer/bin/python -m unittest discover -s tests -v
```

The synthetic suite covers searchable and image-only PDFs, contact details,
addresses, labelled party fields, project numbers, title/approval regions,
rotated and invisible text, text under shapes, metadata, bookmarks,
annotations, links, forms, attachments, barcode/QR removal, sanitized OCR
layers, content-stream recoverability, technical-text preservation, safe
reports, fail-closed page errors, lexicon scoping, denylist seeding, and the
detection/verification suppression symmetry.

## Scoring changes against a golden set

```bash
.venv-anonymizer/bin/python tools/eval_sanitizer.py            # summary
.venv-anonymizer/bin/python tools/eval_sanitizer.py --verbose  # per entry
```

`tests/golden/mlk_labels.json` labels strings verified to occur in the
reference corpus as `party`, `manufacturer`, `boilerplate`, `structural`,
`garbage`, or `hard_negative`. The harness reports three numbers, and any
tuning change should be judged by their delta rather than by a spot check:

| Metric | Meaning |
|---|---|
| **RECALL** | Of the identifiers that must be redacted, how many are. A miss is a breach. This gates release. |
| **OVER-REDACTION** | Of the content that must survive, how much is destroyed. This is technical content lost from the deliverable. |
| **NOISE HANDLED** | Of the content that must survive, how much is kept out of the review queue. This is reviewer minutes. |

Both detector suppression and the derivation proposal rules are scored, since
a term can reach the pipeline either way and the correct answer differs. The
`GoldenSetTest` cases in the unit suite enforce recall at 100%,
over-redaction at 0%, and noise handling above 90%, so a regression fails the
build rather than surfacing during a review.

The reference corpus (MLK Recreation Center, Panama City FL) is public, which
is why real strings are committed there. **Never add strings from an
NDA-protected project to the golden set.**

## Independent leak check (run this before every release)

```bash
.venv-anonymizer/bin/python tools/verify_output_text.py output/pdf/sanitized_document_*.pdf
```

**A PASS in the review report is not evidence of a clean output.** The
verifier can only flag what the detectors can see, so a blind spot shared by
detection and verification is invisible to both. That is not hypothetical: a
title block that split `CCR Architecture &` from `Interiors` into separate
MuPDF blocks kept the architect's name on all 43 sheets of a drawing set while
the report showed PASS with zero residuals.

`tools/verify_output_text.py` deliberately shares none of the pipeline's
plumbing. It reads raw `page.get_text()`, flattens whitespace across the whole
page, and looks for every denylist term with the loosest reasonable separator
tolerance. It will surface things the pipeline is right to ignore — that is
the point of an independent check. Any hit means open the page and look.

## Residual triage

When automated checks fail, the report lists each residual with its page,
category, and masked shape, and `output/pdf/triage/<document_id>/` contains a
cropped PNG of the match region with the matched lines outlined in red. Crops
are rendered locally and contain original page content: treat them as
NDA-protected source material, never upload or share them, and delete the
triage directory after review. At most 200 residuals are detailed per
document; the report's `residuals_truncated` field counts any remainder.

## Mandatory human release gate

Automated checks do not guarantee NDA compliance. An NDA-authorized reviewer
must inspect every output page locally at readable zoom before the document is
submitted to any AI system. The reviewer must confirm that no project, site,
party, person, firm, logo, seal, signature, code, map, or identifier remains;
that technical content is legible; and that the reviewed file hash matches the
report. A failed or incomplete visual review means the PDF must not be released.

Release checklist:

1. `release_status` is `AUTOMATED_PASS` and `residuals` is empty.
2. `tools/verify_output_text.py` reports no known identifier in the output.
3. `tools/eval_sanitizer.py` shows recall 100% and over-redaction 0%.
4. The `ner_review` queue has been triaged and anything genuine has been added
   to the denylist and the document re-run.
5. Full visual review at readable zoom, by an NDA-authorized reviewer.
6. The reviewed file's SHA-256 matches `output_sha256` in the report.

Steps 1–4 are necessary and jointly insufficient. Step 5 is the gate.
