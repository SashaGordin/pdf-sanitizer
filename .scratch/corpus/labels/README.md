# Corpus labels

One `<doc-id>.json` per labeled document, in `tools/corpus_labeler.py`'s
export schema (bbox, category, sensitivity, disposition per item).

## Status

- `ph_quezon_city_health_center.json` — labeled (this pass), a
  representative spread across the document's 30 pages, produced through
  the real `tools/corpus_labeler.py` browser tool, not hand-authored JSON.
- All other real documents in `../manifest.json` (`ca_dgs_25_277693_book1`,
  `ca_dgs_25_277693_plans`, `miami_dade_irp151_specs`,
  `ca_dgs_atascadero_book1`, `il_cdb_stevenson_yard`, `ph_rdo14_techspecs`,
  `corvallis_construction_specs`) are **not yet labeled**. Labeling them is
  left for the user to do with the same tool:

      .venv-anonymizer/bin/python tools/corpus_labeler.py .scratch/corpus/documents/<file>.pdf --doc-id <doc-id>

Until a document has a label file here, `tools/eval_corpus.py` reports it
under "unlabeled" rather than silently scoring it as zero items — see
`evaluate_corpus()`'s `unlabeled_documents` list.

The three synthetic documents (`encrypted_spec`, `malformed_spec`,
`non_english_spec`) are scored at the document level (expected
`release_status`), not through per-item labels here — see
`tests/fixtures/corpus/synthetic/README.md`.
