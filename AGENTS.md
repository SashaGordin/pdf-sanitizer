## Imported Claude Cowork project instructions

## Local-only assets (not in git)

These are deliberately gitignored. A fresh clone will not have them:

- **`models/gliner_multi_pii-v1/`** (~1.1 GB) — GLiNER PII weights, referenced by
  `config/sanitizer.json` and `tools/anonymize_construction_pdfs.py`. Download
  once via the commands in [ANONYMIZATION.md](./ANONYMIZATION.md#L124).
- **Input PDFs** (`*.pdf` at the repo root) — real client construction documents
  containing the PII the sanitizer strips. Drop them in the repo root and pass
  the path as a CLI arg; never commit them.
- **`output/`, `tmp/`** — generated artifacts and intermediate working dirs.

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, used verbatim as label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
