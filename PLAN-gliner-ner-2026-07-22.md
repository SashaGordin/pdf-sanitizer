# Plan: Local NER review layer — GLiNER, report-only (audit next-step #4)

The structural recall hole: an unlisted person or firm mentioned mid-paragraph
without a label is caught by nothing — no regex, no denylist term, no label
heuristic. Audit remedy: zero-shot NER (GLiNER, Apache-2.0, CPU) in
*report-only* mode first, measure the false-positive burden on the real specs,
then decide whether to promote findings to redaction.

## Design

**Where it runs: on the sanitized output, inside verification.** Anything the
rules already redact is gone from the output, so every NER hit on the output
is exactly a net-new candidate the rules missed — no overlap bookkeeping
against detections. Findings land in a new per-document `ner_review` report
section and reuse the existing triage-crop machinery (`triage/<doc_id>/ner/`).
Report-only is a hard contract: `automated_checks` and every existing check
key are computed exactly as today; a run with 500 NER findings still PASSes.

**Offline loading is enforced, not assumed.** The model is loaded from a local
directory only (`local_files_only=True`) after setting `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1` in-process, so a missing model can never trigger a
download from a machine holding source PDFs. The model directory is
pre-downloaded once (documented in ANONYMIZATION.md) like Tesseract language
packs. If NER is enabled and the import or model load fails, the run stops
before page 1 — a silent skip would report false confidence.

**Injectable engine.** `NerDetector` wraps a `predict(texts, labels,
threshold) -> [[{start, end, label, score}]]` callable. Production wires it to
GLiNER's `batch_predict_entities`; tests inject a stub — the full pipeline is
testable without torch. `gliner` stays out of the core requirements
(torch is a ~1 GB dependency); it gets `requirements-anonymizer-ner.txt`.

**Case normalization without offset drift.** Drawing/spec text is
predominantly ALL-CAPS, which degrades NER recall. Blocks whose alphabetic
content is >70% uppercase are re-cased character-by-character (lowercase all,
re-uppercase word-initial letters); every transform keeps 1 char → 1 char, so
entity spans map back to the original block offsets and then through the
existing `rects_for_block_span` → crop path. Acronym distortion (ASTM→Astm)
is acceptable: findings are review candidates, not redactions.

**Chunking.** GLiNER has a short effective window. Block text is fed in
chunks of ≤ 1,000 chars, split at line boundaries (never mid-line), each chunk
carrying its offset into the block. Chunks under 12 alphanumeric chars are
skipped. Pages are batched per `batch_predict` call.

**Report contract.** `ner_review`: `{enabled, model, labels, threshold,
mode: "report_only", finding_counts (per label), findings: [{page, label,
score, shape, crop}], findings_truncated}` — shapes use the same masking
convention as residuals (A/a/9), crops documented as NDA material. Cap:
`max_findings` (default 500) per document, same truncation-counter pattern
as residuals. Findings deduped on (page, span); overlapping labels keep the
highest score.

## Config

`config/sanitizer.json` gains:

```json
"ner": {
  "enabled": false,
  "model_dir": "models/gliner_multi_pii-v1",
  "labels": ["person name", "company name", "organization",
             "project name", "street address", "city"],
  "threshold": 0.5,
  "max_findings": 500
}
```

Disabled by default; `--ner-report` on the CLI enables it for one run without
editing config. Labels target the recall hole (names/orgs/projects/places) —
emails, phones, and numbers already belong to the deterministic patterns.

## Changes

- `tools/anonymize_construction_pdfs.py`: `NerSettings` in `Settings` +
  `load_settings`; `normalize_case_for_ner()`, `ner_chunks()`, `NerDetector`,
  `load_gliner_detector()`; `verify_output()` gains an optional detector and
  emits `ner_review`; `sanitize_document()`/`main()` plumbing; `--ner-report`.
- `config/sanitizer.json`: `ner` block (disabled).
- `requirements-anonymizer-ner.txt`: `gliner`.
- `ANONYMIZATION.md`: offline model download/install, report section, security
  model note (NER runs locally, report-only, never gates release).

## Tests (fail first, then pass)

- Case normalization: length always preserved (property-style over samples),
  mixed-case blocks untouched, ALL-CAPS re-cased.
- Chunking: line-boundary splits, offsets reassemble the block text.
- Stub-engine end-to-end: sanitize a PDF whose output retains an unlisted
  synthetic firm name; stub flags it; verdict stays PASS; finding carries
  page, label, masked shape (no original letters/digits), crop file exists;
  dedupe and `max_findings` truncation behave.
- Disabled path: no `ner_review` key, no behavior change; full suite green.
- Report hygiene: serialized report with findings contains no entity text.

## Verification on real data (Sasha's machine)

1. `pip install -r requirements-anonymizer-ner.txt` into `.venv-anonymizer`;
   download `urchade/gliner_multi_pii-v1` once into `models/`.
2. After the pending clean re-run of both docs, re-run the specs with
   `--ner-report` and read `finding_counts`: that number *is* the measured
   false-positive burden the audit asked for. Spot-check crops, then decide
   promote-to-redact vs. tune labels/threshold.

## Risks / accepted tradeoffs

- GLiNER on 1,475 pages CPU-only is slow (est. 1–3 s/page → tens of minutes).
  Acceptable for a report pass; it is opt-in and off the redaction path.
- Zero-shot labels will flag spec-language noise (manufacturer names in
  product callouts are *supposed* to survive). That is the point of
  report-only: measure before trusting.
- Report-only means the release gate still rests on the human reviewer; the
  NER section gives the reviewer a second signal, not a verdict.

## Result (2026-07-22)

Implemented as designed. 4 new tests (case normalization, chunking,
stub-engine end-to-end report-only run, dedupe/truncation + disabled path);
full suite 17/17 green (includes the post-flatten-sweep test added in the
parallel session). CLI verified: `--ner-report` without the local model fails
fast before page 1 with the install pointer; a normal run is byte-for-byte
unaffected (no `ner_review` key, PASS unchanged). Real-model report pass on
the specs document remains for Sasha's machine (steps above).

## Triage result (2026-07-31)

Real-model `--ner-report` pass run on both documents:
- Specs (1,475 pages): 11,923 findings (`organization: 8527, person name: 1684,
  project name: 916, street address: 253, company name: 475, city: 68`); 500
  saved with crops, 11,423 truncated — the global cap exhausts by page ~97, so
  saved crops sample only the first 6.6% of the document.
- MEP (43 pages, first NER run for this doc): 685 findings (`organization: 472,
  project name: 143, person name: 28, city: 24, street address: 12, company
  name: 6`); 500 saved, 185 truncated.

Directly reviewed ~45 crops (this test corpus — MLK Recreation Center — is
public, so a direct visual pass was fine for this build/test session; on real
NDA documents this step stays reviewer-only per the mandatory human release
gate).

**Noise dominates every label**, not just `organization`. Confirmed sources:
manufacturer/product/technique brand names in schedules (Cooper Lighting,
Lithonia, Siemens, Miro Industries, Geopier, Jay R Smith, KESCO — the last two
are person-shaped brand names, defeating any name-pattern heuristic); legal/
copyright boilerplate (repeated AIA/"The American Institute of Architects"
notices); generic capitalized spec words mislabeled as entities (Bidder,
Owner's, Contractor, City, Project:, Communications, DDC System, Logs of
Boring); CSI MasterFormat section codes misread as street addresses
(`32 13 13`); form-template blank labels (`Street Address:`). Confidence score
does not separate noise from real hits — noise scored as high as 0.94–0.99,
genuine hits as low as 0.68 — so threshold tuning is not an effective lever
here.

**Genuine recall gains, proving the layer's value:** an internal project code
missed by the denylist (`CCR Project 21109`, specs p1); a real adjacent street
name on a page where the city was already redacted but the street wasn't
(`East 14th Street`, specs p52); a likely real subcontractor/engineering firm
name in geotechnical addendum text (`Magnum Engineering`, specs p69); and the
standout catch — a raw CAD file-save path embedded in a drawing revision
footer (`C:\Users\raheal\Documents\Panama City MLK - V...`) leaking both the
drafter's OS username and the real project city (MEP p8). Every one of the 6
labels produced at least one real catch in this sample, so none should be
dropped.

**One ambiguous case for a human call:** a full firm name + city/state
("COHEN CARNAGGIO REYNOLDS", "Birmingham, Alabama 35233") survives in
cleartext in the sanitized specs output — could be an intentionally-preserved
architect-of-record disclosure (common on stamped drawings) or a denylist gap.
Not resolved here.

**Config decision: `ner.threshold` and `ner.labels` left unchanged.** Score
doesn't discriminate noise from signal at this sample size, and every label
contributed a real find, so narrowing either would trade away recall without
reliably cutting noise. Report-only remains correct for every label —
precision is far too low anywhere to auto-redact.

**Recommendations for later (not implemented this pass):**
1. `max_findings` (500, global across the whole run) exhausts before page 100
   of a 1,475-page document — coverage from this cap is misleading for large
   docs. Worth raising substantially or switching to a per-label cap so
   low-volume/high-value labels (`city`, `street address`) aren't crowded out
   by `organization` volume. Cost is more crop I/O, not more inference time.
2. The CAD file-path save-string pattern (`C:\Users\<name>\...`) is a
   *reliable, deterministic* pattern, not something that should depend on
   fuzzy NER — worth a dedicated regex in the rules-based detector (precise,
   unlike NER), given it directly leaked a username and the project city here.
3. Sasha to decide on the ambiguous architect-firm/city-state case above.
