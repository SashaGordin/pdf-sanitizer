# 01 — Report keyed-digest fix

**What to build:** Replace `masked_shape()` as the default value in every
report field that currently carries it (`residuals` entries,
`ner_review.findings` entries) with an HMAC-SHA256 digest keyed by a random
256-bit value generated fresh per run. The key lives only in memory for the
duration of the run — never written to the report or manifest — so the
digest correlates repeated occurrences of the same value within one run's
report, but is neither reproducible across runs nor guessable without the
run's key. `masked_shape()` itself is not deleted: it remains available only
for the reviewer-local in-tool crop view (per `PRODUCTION-READINESS-PLAN.md`
Phase 5), and must not appear anywhere in the report or manifest that
actually leaves the machine alongside the sanitized output.

**Blocked by:** none.

**Status:** ready-for-agent

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/18

- [ ] Every `residuals` and `ner_review.findings` entry in the report uses
      the new keyed digest by default; `masked_shape()`'s output no longer
      appears in the written report or manifest.
- [ ] Two occurrences of the same underlying value within one run's report
      produce the same digest (within-run correlation preserved).
- [ ] The same underlying value processed in two separate runs produces two
      different digests (no cross-run reproducibility).
- [ ] The per-run HMAC key is never written to disk in the report, manifest,
      or any other artifact that travels with the output.
- [ ] A test proves that, given only the report and the sanitized PDF (no
      key), the digest cannot be used to reconstruct or narrow down the
      original value the way a masked-shape table could.
- [ ] `masked_shape()` remains callable and is exercised only by the
      reviewer-local crop-view code path, with a test asserting it does not
      appear in the serialized report/manifest.

## Comments
