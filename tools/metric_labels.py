"""Shared recall-metric labels.

tools/eval_sanitizer.py and tools/eval_corpus.py report two different
things that are easy to conflate because they're both called "recall":

  POLICY_VOCABULARY_RECALL_LABEL -- eval_sanitizer.py. A pure string check
      against tests/golden/mlk_labels.json. Never opens a PDF.
  DOCUMENT_LEVEL_RECALL_LABEL     -- eval_corpus.py. Runs the real sanitizer
      end-to-end against the locked corpus and scores its actual output.

Every place either module prints or writes the word "recall", it must use
one of these two constants, never a bare "recall" — see
tests/test_metric_naming.py, which scans both files' source for exactly
that.
"""

from __future__ import annotations

POLICY_VOCABULARY_RECALL_LABEL = "policy-vocabulary recall"
DOCUMENT_LEVEL_RECALL_LABEL = "document-level recall"
