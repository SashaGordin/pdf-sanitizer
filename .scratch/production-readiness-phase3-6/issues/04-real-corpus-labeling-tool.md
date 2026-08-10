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

**Status:** done

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/21

- [x] Launching the tool against a real corpus PDF path renders that
      document's actual pages (not a placeholder) for labeling.
- [x] Drawing a box and tagging it (category, sensitivity, disposition,
      optional note) adds it to the visible running list; items are
      individually editable and removable.
- [x] No raw JSON or export-schema is visible anywhere on the working screen.
- [x] Clicking "Export" writes `.scratch/corpus/labels/<doc-id>.json` to disk
      with the correct schema (bbox, category, sensitivity, disposition,
      note per item) — verified without a browser, by calling the server's
      write endpoint directly with a synthetic payload and asserting on the
      written file's contents.
- [x] A manual/headless-browser smoke pass (same style as issue #11's
      verification) confirms the interactive labeling flow still works
      end-to-end against a real document.

## Comments

Implementation landed as `tools/corpus_labeler.py` + `tools/corpus_labeler.html`
(plus `tests/test_corpus_labeler.py`, 25 new tests). Renders full pages via
`page_image()` (the same PyMuPDF path `render_residual_crop()` uses), fixes
the prototype's delete-only gap with real in-place item editing, and adds the
`http.server` launcher: `GET /`, `GET /api/session`, `GET /api/page.png`,
`GET /api/page-meta`, `POST /api/export`. Export writes
`.scratch/corpus/labels/<doc-id>.json` via a validate-then-atomic-rename
`write_label_export()`, matching the ticket's stated testable seam. Server
refuses to bind outside `127.0.0.1`/`localhost` per ADR-0001.

The end-to-end smoke pass (last checkbox) was run as a scripted HTTP
walkthrough against a synthetic multi-page PDF, driving every route the
browser UI drives (session bootstrap, per-page PNG/meta fetch confirming
distinct real page content, export write) and visually inspecting a
rendered page PNG to confirm it shows real document text, not a canvas
placeholder — not a literal mouse-driven or headless-browser run, since no
browser-automation tooling (Playwright/Selenium) exists in this repo and
adding a browser-test stack for one ticket was judged disproportionate (see
plan discussion). The interactive drag/edit/delete JS itself was validated
by direct code inspection against the HITL-approved prototype interaction
model, not by an automated browser test.

Code review (Standards + Spec axes) surfaced one real finding: on-screen
export-failure text echoed the validator's raw message, naming internal
schema fields (e.g. `items[0].category must be one of [...]`) — fixed by
showing a generic failure message and routing the detail to
`console.error` instead, closing the "no schema visible on screen"
criterion for the error path too.
