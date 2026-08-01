# Plan: Post-flatten sweep (defect e — transformation asymmetry)

Found by the first full re-run after fixes (a)–(c): MEP FAILs with 45
denylist residuals (one firm-name shape on nearly every sheet), specs FAILs
with 1 wrapped address-shaped prose clause (p1427).

## Diagnosis (verified on MEP page 1, counts only)

Ghostscript rewrites content streams: 380 source text blocks become 317
after flattening, and a denylist phrase that is never contiguous in any
*source* block becomes contiguous in the *flattened* text (8 denylist hits
before GS, 9 after — measured pre-redaction). Detection (source) and
verification (output) run identical code on different text. Defect (b)'s
unification fixed the code asymmetry; this is document asymmetry.

## Fix

`sweep_flattened_output(doc, matcher, page_categories)`: after the output
document is fully assembled (GS/MuPDF flatten + raster rebuilds), re-run the
exact verifier match stream — `block_matches` over
`lines_from_page → text_blocks` — on every page and destructively redact
every hit, then scrub and save. Scope is deliberately the verifier's detector
set only (patterns + denylist), not the label heuristics: the sweep's
contract is "verification finds nothing the sweep didn't already see", and
label lookahead on already-redacted title blocks would over-redact
surviving technical text. Sweep counts land in the report as
`post_flatten_redactions`; swept categories join `page_redactions`.
A sweep failure fails closed as a `PageProcessingError` with the page number.

The verifier still runs afterwards on the saved file, unchanged — if
redaction itself re-groups text into a new match (rare), that still FAILs
honestly with triage output.

## Tests (fail first, then pass)

Deterministic reproduction without depending on GS internals: monkeypatch
`flatten_with_ghostscript` with a stub that copies the cleaned document and
*injects* a denylist term as new text — exactly "text the verifier will see
that source-time detection never saw". Current code: term survives, FAIL.
With sweep: term redacted, PASS, `post_flatten_redactions` shows the
denylist hit, output text clean. Existing 12 tests stay green.

## Real-data verification

- MEP pages 1–3 through the pipeline with the real denylist: FAIL today →
  PASS with sweep (counts only).
- Specs pages 1425–1430 slice: FAIL today (the p1427 residual) → PASS.
- Then Sasha re-runs both full documents locally.

## Risks / accepted tradeoffs

- The sweep redacts flattened-text matches that may be prose artifacts of
  consolidation (the specs p1427 clause) — over-redaction by design,
  consistent with the NDA posture; the redaction is logged per page.
- One extra text pass over the document (~1–2 min on 1,475 pages).
