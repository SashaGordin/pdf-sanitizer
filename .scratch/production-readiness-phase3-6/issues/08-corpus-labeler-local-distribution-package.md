# 08 — Package the corpus labeler for a trusted external annotator

**What to build:** Ticket 07 needs independent ground-truth annotations from
a real client, but ticket 04's labeling tool (`tools/corpus_labeler.py`)
currently assumes an operator running it from a terminal inside this repo
checkout. The actual annotator is one specific, trusted, non-technical
person — not the operator, and not on the operator's machine — so the tool
needs to reach them as something they can run with zero setup.

Decided (2026-08-09, grilling session with the operator):

- **Packaging:** a standalone executable (PyInstaller), not a zip + venv +
  terminal workflow. No Python install, no command line.
- **Invocation:** double-clicking the executable opens a native file-picker
  (`tkinter.filedialog`, part of the standard library — no new dependency)
  restricted to PDF files, instead of requiring a `--doc-id`/path command-line
  argument. The doc ID is derived automatically from the picked file's name
  (the corpus's existing filenames already match their manifest doc IDs,
  e.g. `ca_dgs_atascadero_book1.pdf`) — the client is never asked to type
  one. After picking, the existing labeling flow (real rendered pages,
  category/sensitivity/disposition/note tagging, editable list, no visible
  JSON — all of ticket 04's existing behavior, unchanged) proceeds exactly
  as it does today.
- **Bundled dependencies:** only what `corpus_labeler.py` actually needs at
  import time — PyMuPDF and Pillow (it loads `anonymize_construction_pdfs.py`
  as a sibling module for shared constants, but that module has no top-level
  NER/ML import either, so `torch`/`transformers` are never pulled in). A
  trimmed dependency set, not the full `requirements-anonymizer*.txt`.
- **Export location:** when running as a packaged executable, exported
  labels write to a `labeled-output/` folder created next to the running
  executable, instead of the operator-workflow default
  (`.scratch/corpus/labels/<doc-id>.json`, which assumes a repo checkout
  that won't exist on the client's machine). The existing operator-facing
  CLI invocation and its default export path are unchanged.
- **Distribution:** the operator emails the client a link to a shared
  folder (Google Drive/Dropbox/WeTransfer — not raw email attachments,
  since major providers commonly block executable attachments outright,
  and the corpus documents themselves are large — some real corpus PDFs run
  several hundred pages) containing the executable, a short plain-language
  instructions file, and the still-unlabeled real corpus documents (7 of
  the 8 real documents; `ph_quezon_city_health_center` is already labeled).
  This distribution step itself is a manual action by the operator, not
  part of this ticket's acceptance criteria.
- **Getting labels back:** the exported JSON files in `labeled-output/` are
  small (a few KB each, even for all 7 documents combined) — no size or
  attachment-blocking concerns apply, so the client emails them back as
  plain attachments.
- **ADR-0001:** no addendum. Packaging a copy of the tool for one named,
  trusted individual to run locally on their own machine was judged, this
  session, to already sit within ADR-0001's "local CLI, not a network
  service" scope — nobody is submitting files to a server the operator
  runs, and this isn't the tool being offered as a service to arbitrary
  users.

**Sequence:**

1. Confirm the client's operating system (operator, outside this ticket) —
   PyInstaller builds are platform-specific, so this gates which platform
   the executable in step 3 is built for.
2. Add a packaged-launch mode to `tools/corpus_labeler.py`: no arguments →
   `tkinter` file-picker (PDF filter) → doc ID derived from the filename
   stem → export target defaults to `labeled-output/` next to the running
   executable. The existing `<path> --doc-id <id>` CLI invocation and its
   `.scratch/corpus/labels/` export default must keep working unchanged for
   the operator's own workflow.
3. Build the PyInstaller executable for the client's confirmed OS, bundling
   `corpus_labeler.py`, `corpus_labeler.html`, and only PyMuPDF + Pillow (no
   NER/ML dependencies).
4. Smoke-test the built executable with no terminal involved: double-click,
   pick a real corpus PDF, label at least one item, export, confirm a
   correctly-schemed JSON lands in `labeled-output/` next to the
   executable.
5. Write a short plain-language instructions file to accompany the
   executable, covering: what to click, the one-time "unverified
   developer" OS warning (Gatekeeper/SmartScreen) and that it's expected
   and safe to click through, and how to send the `labeled-output/` files
   back once done.

Out of scope for this ticket: actually sending the link to the client,
and copying whatever labels come back into `.scratch/corpus/labels/` in
this repo (a manual step, once real annotations exist, that directly
unblocks ticket 07's steps 2–3).

**Blocked by:** none (execution of step 3 needs the client's OS, per step 1
— an operator action, not another ticket).

**Status:** ready-for-agent

**GitHub issue:** not yet filed

- [ ] Launching the built executable with no arguments and no terminal
      opens a native file-picker restricted to PDF files.
- [ ] Selecting a real corpus PDF opens the same real-page labeling UI
      ticket 04 built — no reimplementation, no visible JSON/export
      schema, editable/removable labeled-item list.
- [ ] The doc ID used for the exported filename is derived automatically
      from the picked file's name; nothing prompts the client to type one.
- [ ] Clicking "Export" writes a correctly-schemed JSON file into a
      `labeled-output/` folder created next to the running executable —
      verified directly (no GUI) by exercising the packaged export path
      with a synthetic payload and asserting on the written file.
- [ ] The pre-existing operator CLI invocation
      (`corpus_labeler.py <path> --doc-id <id>`, exporting to
      `.scratch/corpus/labels/`) is unchanged, and ticket 04's existing
      tests for it still pass.
- [ ] The executable is actually built for the client's confirmed OS, and a
      manual smoke pass (double-click, pick a file, label an item, export)
      completes without a terminal or any file editing.
- [ ] A plain-language instructions file accompanies the executable,
      covering the one-time OS security warning and how to return the
      exported labels.

## Comments
