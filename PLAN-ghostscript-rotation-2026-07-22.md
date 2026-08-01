# Plan: Fix Ghostscript page re-orientation (audit defect c)

Audit next-step #3. Specs pages 74/288 come out 792×612 instead of 612×792
after the Ghostscript flatten; the fidelity check rightly fails them.

## Diagnosis

Ghostscript's pdfwrite defaults `AutoRotatePages` to `/PageByPage`: any page
whose text is predominantly rotated (a rotated table or schedule on a
portrait sheet — exactly what construction specs contain) gets re-oriented so
the text reads "upright", swapping the page dimensions. The sanitizer never
asked for this and the verifier correctly refuses it.

## Fix — two layers

1. **Root cause:** add `-dAutoRotatePages=/None` to the Ghostscript command in
   `flatten_with_ghostscript()`. GS then leaves page orientation alone.
2. **Safety net:** new `reconcile_page_geometry(reference, candidate)` runs
   after flattening, before save:
   - A page whose displayed size already matches the source is untouched.
   - If the MediaBox is unchanged but GS altered only the `/Rotate` flag, the
     source rotation is restored — a pure, lossless flag reversal.
   - Any page whose displayed geometry still differs is returned and routed
     into the existing unsafe-pages set → the raster fallback rebuilds it
     from the *cleaned source page*, which preserves geometry by
     construction. Fail toward the safe path, never ship altered geometry.

## Tests (fail first, then pass)

- Integration: a portrait page whose text is entirely rotated 90° (pure
  technical content, nothing redactable) must survive the pipeline with
  `automated_checks` PASS, no size mismatches, `rasterized_pages` empty
  (i.e. fixed at the GS layer, not by fallback), and its text intact.
  Today this page comes back landscape and FAILs.
- Unit: `reconcile_page_geometry` restores a rotation-flag-only change and
  reports a genuinely resized page as unreconcilable.

## Verification on real data

Extract the two real failing pages (73 and 287, 0-based) from the source
specs into a temp file; show the old GS flags swap their dimensions and the
fixed flatten preserves them. Dimensions only — no content read or printed.

## Risks / accepted tradeoffs

- `/None` changes GS behavior for every page, but "do not re-orient" is
  strictly closer to the tool's fidelity contract; pages that legitimately
  carry `/Rotate` flags keep them (the safety net double-checks).
- The net rasterizes a page in the worst case — losing vector text on that
  page but never geometry or content. Report shows it in `rasterized_pages`.

After this lands: re-run both real documents end-to-end (audit's gate for
new features) — specs should now reach PASS or produce a triaged residual
list from the defect-(b) tooling.
