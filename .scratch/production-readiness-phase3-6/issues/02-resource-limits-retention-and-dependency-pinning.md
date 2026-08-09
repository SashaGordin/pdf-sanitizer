# 02 — Resource limits, retention/cleanup, and dependency pinning

**What to build:** Three related Phase 5 operational gaps, scoped exactly to
issue #8's "apply now, light version" list:

1. **Resource limits.** Wrap the per-document worker with `resource.setrlimit`
   (`RLIMIT_AS`/`RLIMIT_RSS` where the platform supports it, `RLIMIT_CPU`),
   plus an explicit disk-usage ceiling check on the per-run staging
   directory, polled during processing. Any breach produces the same
   controlled `FAIL` result shape the existing Tesseract/Ghostscript timeout
   handling already produces — no new failure taxonomy.
2. **Cleanup and retention.** On a run's successful completion only (not on
   failure or crash — startup recovery for abandoned runs is explicitly out
   of scope), delete that run's staging directory and triage-crop directory.
   Separately, add a configurable retention window (default: a stated number
   of days) that prunes entire run directories older than the window, run as
   an explicit maintenance step.
3. **Dependency pinning.** Generate a hash-pinned lockfile (e.g.
   `pip-compile --generate-hashes`) from the existing
   `requirements-anonymizer.txt` and `requirements-anonymizer-ner.txt` range
   specs. Check the lockfile in and document the regeneration command in
   `AGENTS.md` or the requirements files themselves. No SBOM or
   vulnerability-scanning program.

**Blocked by:** none.

**Status:** todo

- [ ] A synthetic test that forces the memory/CPU limit past its ceiling
      (mocked) proves the run fails closed with a controlled `FAIL`, not a
      crash or hang.
- [ ] A synthetic test that fills the staging directory past the disk
      ceiling proves the same controlled `FAIL` behavior.
- [ ] After a successful run, that run's staging directory and triage-crop
      directory no longer exist on disk.
- [ ] A failed or interrupted run's staging/triage directories are left
      untouched (cleanup is success-only by design).
- [ ] Running the retention-pruning step against a set of run directories
      with mixed ages deletes only those older than the configured window,
      leaving newer ones untouched.
- [ ] A hash-pinned lockfile exists, is checked into the repo, and installing
      from it produces the exact versions the lockfile names.
- [ ] The lockfile regeneration command is documented and, when run against
      the current range-specified requirements files, reproduces a lockfile
      with no unexpected drift.

## Comments
