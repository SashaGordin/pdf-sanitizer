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

**Status:** done

**GitHub issue:** https://github.com/SashaGordin/pdf-sanitizer/issues/37

- [x] Launching the built executable with no arguments and no terminal
      opens a native file-picker restricted to PDF files. Confirmed
      2026-08-10 by the operator: double-clicked `dist/corpus-labeler` in
      Finder, no terminal involved.
- [x] Selecting a real corpus PDF opens the same real-page labeling UI
      ticket 04 built — no reimplementation, no visible JSON/export
      schema, editable/removable labeled-item list. Confirmed against a
      real 203-page document (`21-project-manual.pdf`), labeling three
      items across two different pages.
- [x] The doc ID used for the exported filename is derived automatically
      from the picked file's name; nothing prompts the client to type one.
- [x] Clicking "Export" writes a correctly-schemed JSON file into a
      `labeled-output/` folder created next to the running executable —
      verified directly (no GUI) by exercising the packaged export path
      with a synthetic payload and asserting on the written file.
- [x] The pre-existing operator CLI invocation
      (`corpus_labeler.py <path> --doc-id <id>`, exporting to
      `.scratch/corpus/labels/`) is unchanged, and ticket 04's existing
      tests for it still pass.
- [x] The executable is actually built for the client's confirmed OS (macOS,
      confirmed 2026-08-10), and a manual smoke pass completes without a
      terminal or any file editing: the operator double-clicked
      `dist/corpus-labeler` in Finder, picked a real 203-page document
      (`21-project-manual.pdf`), labeled three items across two pages, and
      exported — `labeled-output/21-project-manual.json` landed next to the
      executable with a correctly-schemed payload (real bbox/scale/category
      values, doc ID `21-project-manual` derived from the filename).
- [x] A plain-language instructions file accompanies the executable,
      covering the one-time OS security warning and how to return the
      exported labels.

## Comments

Step 2 (packaged-launch mode) landed in `tools/corpus_labeler.py`:
`pdf_path` is now optional; omitting it opens a `tkinter.filedialog`
restricted to PDFs (`pick_pdf_via_file_dialog()`), and the pdf-path/output-dir
selection was factored into a pure `resolve_input_and_output()` so it's
testable without a real file-picker or a running server. Packaged-mode
default export dir is `running_executable_dir() / "labeled-output"`
(`sys.executable`'s parent when `sys.frozen`, else cwd). The operator's
explicit `<path> --doc-id <id>` invocation and its
`.scratch/corpus/labels/` default are untouched — all 25 pre-existing tests
plus 8 new ones (`ResolveInputAndOutputTest`, `RunningExecutableDirTest`)
pass.

Also added, ahead of step 3: `requirements-corpus-labeler.txt` (trimmed to
PyMuPDF + Pillow, per this ticket's dependency-set decision) and
`tools/build_corpus_labeler_executable.py`, a PyInstaller build script that
creates a throwaway build venv and bundles `corpus_labeler.html` +
`anonymize_construction_pdfs.py` as data files (needed since both are loaded
via `importlib`/`Path(__file__)` at runtime, not a static `import`
PyInstaller's analysis would otherwise catch). Also drafted
`tools/CORPUS_LABELER_INSTRUCTIONS.md` (step 5) covering Gatekeeper/
SmartScreen and the return-the-labels step for both Windows and Mac, since
the client's OS wasn't yet confirmed at drafting time.

Client OS confirmed 2026-08-10: macOS. Ran
`tools/build_corpus_labeler_executable.py` on the operator's Mac and
produced `dist/corpus-labeler` (ad-hoc-signed arm64 Mach-O). Debugging the
build surfaced two real gaps in the naive "bundle it as a data file" plan,
both now fixed in the build script:

1. **Missing hidden imports.** Because `anonymize_construction_pdfs.py` is
   loaded via `importlib.util.spec_from_file_location` rather than a static
   `import`, PyInstaller's analysis never sees *its* imports at all — not
   just the ML ones we're deliberately excluding, but its ordinary stdlib
   ones too. First symptom: `ModuleNotFoundError: No module named 'uuid'`
   at runtime. Fixed by explicitly listing every one of that module's
   top-level imports as `--hidden-import` (`ANONYMIZER_MODULE_HIDDEN_IMPORTS`
   in the build script). Once `uuid` was added, a second, non-obvious
   failure appeared one line later: `from PIL import Image, ImageDraw`
   inside that same module failed, because `ImageDraw` is a second PIL
   submodule PyInstaller's `PIL` hook never learned to collect (only
   `PIL.Image`, from `corpus_labeler.py`'s own static import, got bundled
   automatically). Fixed the same way, by hidden-importing `PIL.ImageDraw`.
2. **~30s cold-start recompile.** Once imports resolved, every single
   launch took ~30 seconds before the server came up, because PyInstaller's
   onefile mode extracts to a brand-new temp directory on each run, so a
   `.py`-source sibling module never benefits from bytecode caching across
   launches — `anonymize_construction_pdfs.py` (3,700+ lines) was being
   parsed and compiled from scratch every time. For a client double-clicking
   this once per document, a 30-second silent "is this frozen?" window was a
   real risk. Fixed by precompiling that module to `.pyc` at build time
   (`py_compile.compile()`, same build-venv interpreter so the bytecode
   magic number matches) and bundling the `.pyc` instead of the `.py`;
   `corpus_labeler.py`'s `MODULE_PATH` now prefers a sibling `.pyc` if
   present, falling back to `.py` unchanged for the operator/dev/test path.
   Cold start is now ~5-6s (ordinary onefile extraction overhead).

Smoke-tested the resulting binary two ways. First, scripted (this
environment has no interactive GUI to click through a real file-picker
dialog): invoked with the same `<path> --doc-id <id> --output-dir <dir>`
arguments the operator CLI uses, confirmed `/api/session` returns the real
PDF's page count, and posted a synthetic export payload to `/api/export` —
a correctly-schemed JSON landed on disk. Second, the operator ran the actual
manual pass 2026-08-10: double-clicked `dist/corpus-labeler` in Finder with
no arguments, the native file-picker opened, picked a real 203-page
document (`21-project-manual.pdf`, not a fixture), labeled three items
across two different pages, and exported. The resulting
`labeled-output/21-project-manual.json` matches the schema
`validate_export_payload()` expects (real bbox coordinates, `scale`,
`category`/`sensitivity`/`disposition` per item) and the doc ID was
correctly derived from the filename with no prompt to type one. All
acceptance-checklist items are now closed.

GitHub issue mirror filed and closed as issue #37 (see PR #25's pattern for
tickets 01-07). Still open, outside this ticket's scope: the distribution
step itself (sending the client the packaged executable and collecting
labels back) — an operator action.
