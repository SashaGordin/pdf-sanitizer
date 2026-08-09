## Project phase: development vs. production (read this first)

**Right now: development phase.** The documents used to build and test this
tool (e.g. the MLK Recreation Center corpus) are public information — see the
`_about` note in `tests/golden/mlk_labels.json`. It is fine for Claude to
read, open, and inspect these files directly, including their full content.

**Production phase: no cloud access, anywhere, once this.** When this system
is complete, finalized, and actually used to process real client files, the
user switches to legitimate, NDA-protected, non-public documents. From that
point on:

- **The tool itself must run fully offline.** This is already the design
  intent per `ANONYMIZATION.md` (the sanitizer refuses to run NER without a
  local model directory, and never accesses the network at runtime) — that
  property must hold for the whole pipeline, not just the NER step, and must
  not regress.
- **Claude/Claude Code must not read real client file content.** Once real
  client PDFs, extracted text, or sanitizer output are in play, do not `Read`
  their content, paste it into a conversation, or otherwise send it to any
  cloud API (Claude or otherwise) — that defeats the purpose of the tool,
  which exists specifically to keep this data from leaving the machine.
  Filenames, file sizes, config, code, and test/golden-fixture data (already
  public) remain fine to inspect at any time.
- If asked to debug an issue that only reproduces on a real client document,
  say so explicitly and ask the user how they want to proceed (e.g. a
  redacted/synthetic repro) rather than reading the file.

If it's unclear which phase the project is in, ask before opening any file
that isn't clearly public/synthetic (config, code, and docs are always safe).

## Imported Claude Cowork project instructions

## Local-only assets (not in git)

These are deliberately gitignored. A fresh clone will not have them:

- **`models/gliner_multi_pii-v1/`** (~1.1 GB) — GLiNER PII weights, referenced by
  `config/sanitizer.json` and `tools/anonymize_construction_pdfs.py`. Download
  once via the commands in [ANONYMIZATION.md](./ANONYMIZATION.md#L124).
- **Input PDFs** — real client construction documents containing the PII the
  sanitizer strips. Drop them in the repo root and pass the path as a CLI arg;
  never commit them. Note the `*.pdf` ignore rule applies in *every* directory,
  not just the root, so a PDF added anywhere is untracked by default. Use
  `git add -f` if you ever need a specific one (e.g. a small redacted fixture).
- **`output/`, `tmp/`** — generated artifacts and intermediate working dirs.

## Local dev setup

- **Pre-push hook.** `tools/git-hooks/pre-push` runs the full unit suite and
  the golden-set eval, blocking `git push` on either's failure. `core.hooksPath`
  is a local repo setting, not itself versioned, so a fresh clone needs one
  command to activate it: `git config core.hooksPath tools/git-hooks`.
- **Dependency lockfiles.** `requirements-anonymizer.lock.txt` and
  `requirements-anonymizer-ner.lock.txt` are hash-pinned lockfiles generated
  from the range-specified `requirements-anonymizer.txt`/
  `requirements-anonymizer-ner.txt`. For a reproducible install:
  `.venv-anonymizer/bin/python -m pip install --require-hashes -r requirements-anonymizer.lock.txt -r requirements-anonymizer-ner.lock.txt`.
  The unpinned `.txt` files remain the source of truth for version ranges;
  regenerate a lockfile after changing its range spec with
  `.venv-anonymizer/bin/python -m pip install pip-tools` once, then
  `.venv-anonymizer/bin/python -m piptools compile --generate-hashes --allow-unsafe --output-file=<name>.lock.txt <name>.txt`
  (also documented as a comment at the top of each `.txt` file).

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/<feature-slug>/` in this repo. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, used verbatim as label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
