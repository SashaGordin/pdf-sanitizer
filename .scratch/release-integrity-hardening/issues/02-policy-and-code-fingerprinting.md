# 02 — Policy & code fingerprinting

**What to build:** Every report is stamped with a fingerprint: hashes of the
sanitizer code itself (commit SHA when available, else a computed build
digest), the denylist, project metadata, configuration, allowlist, and each
lexicon file used to produce it. Two runs against byte-identical inputs,
code, and policy produce identical fingerprints; changing any one of those
inputs visibly changes the corresponding hash.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Report/manifest include a fingerprint block with per-input hashes
      (denylist, project-metadata, config, allowlist, each lexicon file) plus
      a code identity (git commit SHA, or a computed build digest when no
      commit is available).
- [ ] Running the sanitizer twice against unchanged inputs/code/policy yields
      an identical fingerprint block.
- [ ] Changing the denylist, a lexicon file, or the config changes only the
      corresponding hash(es) in the fingerprint, leaving the rest unchanged.
- [ ] Fingerprint computation never reads or logs raw sensitive values
      (hashes and version identifiers only).
- [ ] Tests assert the fingerprint's hashes match the actual on-disk
      denylist/config/lexicon file contents at the time of the run.
