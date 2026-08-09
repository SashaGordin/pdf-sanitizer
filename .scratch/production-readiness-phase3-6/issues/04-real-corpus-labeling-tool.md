# 04 — Real corpus-labeling tool

**What to build:** Productionize the corpus-labeling tool whose shape was
validated through two rounds of HITL prototyping (issue #11): a static HTML
page — draw a bounding box on a rendered PDF page, tag it inline with
category / sensitivity decision / expected disposition / optional note, with
a running editable/removable list of labeled items, and no visible JSON/
export-schema preview on the working screen — plus a small Python
`http.server`-based local launcher (matching the existing `tools/*.py`
launcher convention).

Two things change from the throwaway prototype (`tools/prototype_corpus_labeler.*`
on the unmerged `origin/worktree-prototype-corpus-labeler` branch, not present
on `main`):

1. The page shown is a **real rendered page from an actual corpus document**
   (reuse `render_residual_crop()` / the existing PyMuPDF rendering path from
   `tools/anonymize_construction_pdfs.py`), not a synthetic placeholder page.
2. The **"Export" action writes directly to disk** via the local server —
   `POST` the labeled-items payload to an endpoint that writes it to
   `.scratch/corpus/labels/<doc-id>.json` — instead of only triggering a
   browser download the operator has to move by hand.

The server binds to `localhost` only, launched by the operator on their own
machine — this does not change ADR-0001's "not a network service" status.

**Blocked by:** none.

**Status:** ready-for-agent

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/21

- [ ] Launching the tool against a real corpus PDF path renders that
      document's actual pages (not a placeholder) for labeling.
- [ ] Drawing a box and tagging it (category, sensitivity, disposition,
      optional note) adds it to the visible running list; items are
      individually editable and removable.
- [ ] No raw JSON or export-schema is visible anywhere on the working screen.
- [ ] Clicking "Export" writes `.scratch/corpus/labels/<doc-id>.json` to disk
      with the correct schema (bbox, category, sensitivity, disposition,
      note per item) — verified without a browser, by calling the server's
      write endpoint directly with a synthetic payload and asserting on the
      written file's contents.
- [ ] A manual/headless-browser smoke pass (same style as issue #11's
      verification) confirms the interactive labeling flow still works
      end-to-end against a real document.

## Comments
