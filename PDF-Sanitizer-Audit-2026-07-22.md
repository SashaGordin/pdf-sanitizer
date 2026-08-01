# PDF Sanitizer Audit — 2026-07-22

Audit of the local construction-PDF de-identification tool produced in the prior session. Reviewed: all code, tests, configs, docs, and run reports. Source PDFs were sampled for structure only (page counts, text/image stats, 2 pages of dimensions); no document content was processed or quoted. Residual detections were analyzed in masked form (letters→A, digits→9).

## What exists

| Item | Size | Status |
|---|---|---|
| `tools/anonymize_construction_pdfs.py` | 973 lines | Full pipeline: detection → redaction → scrub → flatten → OCR fallback → verification → JSON report |
| `tests/test_anonymize_construction_pdfs.py` | 233 lines, 5 tests | Synthetic PDFs covering searchable, scanned, rotated/hidden text, regions, fail-closed, report hygiene |
| `config/sanitizer.json` | — | DPI, barcode, region config (example regions disabled) |
| `config/denylist.local.json` | 160 derived terms | Git-ignored, mode 0600 |
| `ANONYMIZATION.md` | 85 lines | Install, security model, mandatory human release gate |
| `output/pdf/` | 3 PDFs + 1 report | Both real docs were processed; **specs run FAILED automated checks** |
| `tmp/pdfs/vendor/` | 66 MB | Vendored macOS PyMuPDF 1.28 + zxing-cpp wheels (git-ignored) |
| `AGENTS.md` | 1 line | Empty stub |

Dependencies: PyMuPDF, Pillow, zxing-cpp, reportlab (tests only), plus external Tesseract and Ghostscript. Fully offline — no network calls anywhere in the code. Confirmed.

Source documents (structure only): MEP drawings = 43 large-format sheets (3024×2160 pt), specs = 1,475 letter pages. Both are fully text-based with optional-content layers, unencrypted; no scanned pages in my samples, so the OCR path is nearly unused on this corpus (1 page rasterized in the specs run).

## 1. Completion estimate: ~65% of a production-ready v1

| Stage | State | Done | Notes |
|---|---|---|---|
| PDF parsing / text extraction | working | 90% | Rotation-aware line grouping; solid |
| Rule/denylist detection | built, gaps proven | 70% | Fails on multi-line label/value and name/credential splits (see §2) |
| NER for unlisted names/orgs | not started | 5% | Only a `Name, PE/AIA` regex + denylist; nothing catches an unlisted company or person |
| Redaction engine | working | 90% | MuPDF destructive redaction, image pixels, line-art removal |
| Metadata/hidden-content scrub | working | 95% | XMP, annots, forms, JS, attachments, TOC, thumbnails, layers — thorough |
| Layer flattening | built-with-bug | 75% | Ghostscript primary + MuPDF + raster fallback; **rotates pages 74/288** (612×792 → 792×612) |
| OCR raster fallback | built-but-untested-on-real-scans | 80% | Redact → re-OCR → rebuild with sanitized text layer is a strong design; eng-only |
| Barcode/QR removal | working | 90% | zxing-cpp, iterative re-scan until clean, quiet-zone padding; 192 removed in specs run |
| Logo/map/region heuristics | working, crude | 70% | 1,286 repeated-margin-image redactions; map removal is label-guess-based |
| Verification | built, not trustworthy yet | 60% | Fails closed (good) but conflates its own false positives with true leaks; no triage tooling |
| Batch/CLI | working | 75% | Multi-file, dirs, neutral naming; sequential; per-doc report naming is manual |
| Tests/docs | good for synthetic | 65% | No real-document regression fixtures; the 4 bugs found below have no failing tests |

Weighted by effort, that lands around 65%. The number is honest but flattering: everything easy is done and done well; the remaining 35% is the hard part — recall on real documents and a verification story a reviewer can trust. The current state of the real run is FAIL: 38 residual direct-identifier matches and 2 page-geometry mismatches in `sanitization_review_report_02.json`.

## 2. Architecture assessment

### Efficiency: fine, not a problem

Per-page streaming, one compiled denylist alternation, single-pass `get_image_info`, barcode scan at capped 72 DPI, OCR only when a page has <20 chars of text. File timestamps bound the 1,475-page specs run at ≤ ~36 minutes (~1.5 s/page worst case) on the prior machine — well inside "reasonable." Memory is page-at-a-time. Nothing here will take hours per file. Two minor notes: batch is sequential (a per-document worker pool is an easy 4–8× when batches grow), and the 160-term regex alternation is fine but would want Aho-Corasick if the denylist grows to thousands. Output size can inflate (MEP: 12 MB → 23.7 MB) because pixel redaction re-encodes images — cosmetic, not blocking.

### Reliability: four concrete defects, found from the real run

**(a) Cross-line detection gap — the serious one.** Detection scans line-by-line, so anything split across a line break is invisible to it: `PROJECT NO.:` on one line and the number on the next, a person's name with `AIA`/`PE` on the following line, an address wrapping. The label→next-line case is handled in denylist *derivation* but not in *detection*. The masked residuals prove this is happening on the real specs: e.g. page 122 `AAAAAAA AA:` newline `99999`, pages 3/4/814/815 name-then-credential patterns. Some of these 38 residuals are verifier false positives (see b), but several are shaped exactly like real leaks.

**(b) Detection and verification are asymmetric.** The verifier scans whole-page text where regexes match across newlines; detection scans single lines. So the verifier flags things detection was never able to see (true gaps) *and* things that are just spec prose wrapping across lines (false positives, e.g. most of the 22 `street_address` hits that look like numbered technical clauses). Both should run over the same normalized text stream, and the report needs a triage mechanism — right now a FAIL gives a reviewer no way to distinguish leak from noise without manually hunting 38 locations in 1,475 pages.

**(c) Ghostscript rotation bug.** Pages 74 and 288 come out 792×612 instead of 612×792 — GS's pdfwrite re-orienting rotated pages. Content is preserved but the fidelity check rightly fails. Likely fix: handle `/Rotate` explicitly or normalize before comparison.

**(d) Images on text-rich pages are never inspected.** The OCR path only triggers on near-empty pages. A photo, scanned letterhead, or signature image embedded in an otherwise texty page is untouched unless it's a repeated margin logo, a barcode, or near a "vicinity map" label. For NDA purposes this is a real hole: one embedded site photo with a sign in it sails through.

Edge-case scorecard against your checklist: scanned pages ✔ (designed, synthetic-tested, unproven on real scans); mixed text+image ✖ (defect d); tables ✔ enough for redaction; headers/footers ✔ (regions + repeated-image + denylist); metadata/XMP ✔ thorough; embedded objects/attachments ✔; encrypted PDFs ~ (fails closed with a generic error, no explicit handling); multi-column ✔ for detection; non-Latin ✖ (eng-only OCR, `[A-Z]`-anchored regexes, US-state patterns — undocumented limitation); false negatives ✖ — this is the structural weakness: recall for *unlisted* names and firms depends entirely on the denylist and label patterns. An NDA party mentioned mid-paragraph without a label, not in the denylist, is not caught by anything. False-positive control is actually good (deliberate filters against propagating short terms, with a comment explaining why). Verification exists and fails closed ✔ but isn't yet a tool a reviewer can act on ✖. The documented mandatory human release gate is the right call and well-written.

### Verdict on direction

Keep this architecture. The shape — deterministic rules + project denylist + destructive MuPDF redaction + layer flattening + sanitized-OCR rebuild + fail-closed verification + human gate — is correct for an NDA tool, and the implementation quality is well above what the "90-minute session" framing suggests (the barcode re-scan loop, the redact→re-OCR→rebuild raster path, and the report hygiene are all things mature tools get wrong). Nothing here justifies a rewrite or a pivot to a document-processing framework. What it needs is (1) the four defects fixed, (2) a local NER layer to cover unlisted-name recall, and (3) one licensing decision — see below.

## 3. OSS landscape: what to adopt, what to skip

**Licensing flag first: PyMuPDF and Ghostscript are both AGPL-3.0** (Artifex sells commercial licenses, typically $10k–50k/yr). For a tool used *internally* — run by your own people on your own machines, output PDFs shared — AGPL imposes no obligations; you're fine as-is. If you ever distribute the tool to clients or expose it as a network service, you'd need commercial licenses or a rebuild on permissive components. Decide which world you're in before investing further; everything else in the stack is permissive (zxing-cpp Apache-2.0, Pillow MIT-HPND, Tesseract Apache-2.0, reportlab BSD, test-only).

| Candidate | License | Fit | Recommendation |
|---|---|---|---|
| **PyMuPDF 1.28** (in use) | AGPL-3.0 / commercial | Only Python library with true destructive redaction + extraction + rendering in one | **Keep.** No permissive-licensed equivalent exists; replacing it means rebuilding the core |
| pikepdf 10.x | MPL-2.0 | Low-level qpdf wrapper; no coordinate text extraction, no redaction engine | Skip — only relevant as part of a costly AGPL-avoidance rebuild |
| pdfplumber | MIT | Extraction only, slower, no redaction | Skip |
| poppler tools | GPL-2 | CLI extraction | Skip |
| **Ghostscript** (in use) | AGPL-3.0 | Layer flattening | Keep (same license posture as PyMuPDF anyway); fix the rotation flag |
| **Tesseract 5.5** (in use) | Apache-2.0 | Still the standard local OCR | Keep; add language packs if non-English docs appear |
| **Presidio 2.2.x** | MIT | Analyzer framework: spaCy/transformers NER + regex recognizers + scoring, fully offline | **Adopt as a detector plugin** — its PERSON/ORG NER via spaCy is the missing recall layer; its built-in US-PII recognizers overlap with your regexes but its recognizer registry is a clean way to host both |
| spaCy `en_core_web_lg` | MIT | Fast CPU NER | The default engine under Presidio. Caveat: recall degrades on ALL-CAPS drawing text — normalize case before NER |
| **GLiNER** (e.g. `urchade/gliner_multi_pii-v1`, knowledgator PII models) | Apache-2.0 | Zero-shot NER, CPU-capable (~300M params); you can ask it directly for "company name", "person name", "project name" | **Strongest candidate for this corpus** — construction title blocks aren't newswire, and zero-shot labels beat spaCy's fixed types. Slower than spaCy; run per-page, batch lines |
| docling 2.100 / unstructured.io | MIT / Apache-2.0 | Layout parsing for RAG pipelines | Skip — they extract, they don't redact; wrong tool category |
| Dangerzone | — | Rasterizes everything to pixels | Skip as pipeline; your raster fallback already does this *better* (it keeps a sanitized text layer) |
| pdf-redact-tools, CoverUP, PDF-Redactor | various | Small/unmaintained/subsets of what's built | Skip |

Net: no OSS tool replaces this pipeline — the custom glue (construction-specific labels, denylist derivation, fail-closed verification, report hygiene) *is* the product. The one genuine gap OSS fills is NER-based recall, via GLiNER (first choice) or Presidio+spaCy (more framework, more US-PII plumbing), both offline and permissively licensed.

## 4. Next steps, prioritized

1. **Fix cross-line detection (defect a).** Scan block/paragraph-level text with `\s+`-tolerant matching, and port the label→following-line value logic from `derive_terms` into `line_detections`. This alone addresses most of the 38 real-run residuals. Add regression tests built from the masked residual shapes.
2. **Unify detection and verification text normalization (defect b), and add triage output.** Verifier should emit, per residual: page, category, masked shape, and a cropped page-region PNG rendered locally — so a reviewer can clear false positives in minutes instead of hunting through 1,475 pages.
3. **Fix the Ghostscript page-rotation swap (defect c)** and re-run the specs document; the goal is a clean PASS on both real documents before any new features.
4. **Add a local NER detector for unlisted names/orgs.** Start with GLiNER in report-only mode on the specs document, measure the false-positive burden, then promote to redaction. Case-normalize before inference. This closes the biggest NDA recall hole.
5. **Inspect embedded images on text pages (defect d):** OCR images above a size threshold and scan the result with the same detectors; redact the image region on any hit.
6. **Decide the licensing posture** (internal-only vs. distributed). Internal-only: document it in ANONYMIZATION.md and stop worrying. Distributed: budget for Artifex commercial licenses — a permissive rebuild is not worth it.
7. Smaller, in order: per-document report files (the `_02` report suggests manual renaming); explicit encrypted-PDF message; document the English/Latin-only scope; regenerate the MEP report so both docs have current PASS evidence; delete the stray `output/pdf/anonymous-document-001.pdf` from the earlier naming scheme; parallelize per-document only when batch sizes demand it.

---

*Method note: completion percentages are judgment weighted by stage effort; defects (a)–(d) were verified programmatically against `sanitization_review_report_02.json` and the sanitized output (masked pattern shapes only — no document content was read or reproduced).*
