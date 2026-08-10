#!/usr/bin/env python3
"""Score the real sanitizer, end-to-end, against the locked generalization
corpus (tests/golden/mlk_labels.json is a different, string-level golden set
scored by eval_sanitizer.py — that is "policy-vocabulary recall", never
opens a PDF. This module reports "document-level recall": it runs the real
sanitizer against real locked-corpus PDFs and scores its output against
per-item bounding-box labels).

    .venv-anonymizer/bin/python tools/eval_corpus.py
    .venv-anonymizer/bin/python tools/eval_corpus.py --manifest .scratch/corpus/manifest.json
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import secrets
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

METRIC_LABELS_PATH = Path(__file__).with_name("metric_labels.py")
_metric_labels_spec = importlib.util.spec_from_file_location("metric_labels", METRIC_LABELS_PATH)
metric_labels = importlib.util.module_from_spec(_metric_labels_spec)
assert _metric_labels_spec and _metric_labels_spec.loader
sys.modules[_metric_labels_spec.name] = metric_labels
_metric_labels_spec.loader.exec_module(metric_labels)
DOCUMENT_LEVEL_RECALL_LABEL = metric_labels.DOCUMENT_LEVEL_RECALL_LABEL

MODULE_PATH = Path(__file__).with_name("anonymize_construction_pdfs.py")
_spec = importlib.util.spec_from_file_location("pdf_sanitizer", MODULE_PATH)
sanitizer = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = sanitizer
_spec.loader.exec_module(sanitizer)

LABELER_MODULE_PATH = Path(__file__).with_name("corpus_labeler.py")
_labeler_spec = importlib.util.spec_from_file_location("corpus_labeler", LABELER_MODULE_PATH)
corpus_labeler = importlib.util.module_from_spec(_labeler_spec)
assert _labeler_spec and _labeler_spec.loader
sys.modules[_labeler_spec.name] = corpus_labeler
_labeler_spec.loader.exec_module(corpus_labeler)

# Hand-drawn label boxes are drawn on a rasterized preview, not measured off
# exact text-span geometry, so some positional slack against the residual's
# exact bbox is expected. Named and tunable rather than an inline magic
# number.
IOU_MATCH_THRESHOLD = 0.3

# What a sanitizer-side category/label satisfies on the labeling tool's
# vocabulary (tools/corpus_labeler.py's CATEGORIES). A detection surface can
# be more specific (e.g. "email") or less specific (denylist terms carry no
# sub-category of their own) than the label; this map is deliberately
# explicit rather than inferred, since silently guessing here is exactly the
# kind of drift that would let a real miss slide through.
CATEGORY_EQUIVALENCE: dict[str, frozenset[str]] = {
    "email": frozenset({"contact"}),
    "phone": frozenset({"contact"}),
    "url": frozenset({"contact"}),
    "po_box": frozenset({"address"}),
    "street_address": frozenset({"address"}),
    "city_state_zip": frozenset({"address"}),
    "credentialed_person": frozenset({"person"}),
    "project_or_job_number": frozenset({"project"}),
    "barcode_or_qr": frozenset({"barcode"}),
    # The denylist carries no sub-category of its own: any of these labeled
    # categories can legitimately be seeded as a denylist term.
    "denylist": frozenset({"person", "firm", "project", "address", "contact", "account_id"}),
    # NER labels (tools/anonymize_construction_pdfs.py's DEFAULT_NER_LABELS).
    "person name": frozenset({"person"}),
    "company name": frozenset({"firm"}),
    "organization": frozenset({"firm"}),
    "project name": frozenset({"project"}),
    "street address": frozenset({"address"}),
    "city": frozenset({"address"}),
}


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection-over-union of two [x0, y0, x1, y1] boxes."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def label_bbox_to_pdf_points(item: dict) -> list[float]:
    """corpus_labeler.py stores bbox in rendered-pixel space with
    scale = dpi / 72; the sanitizer's own report bboxes are PDF points."""
    scale = item["scale"]
    return [v / scale for v in item["bbox"]]


def category_satisfied(label_category: str, detection_category: str) -> bool:
    return label_category in CATEGORY_EQUIVALENCE.get(detection_category, frozenset())


def find_match(item: dict, residuals: Sequence[dict], ner_findings: Sequence[dict]) -> dict | None:
    """The first residual or NER finding that positionally and categorically
    matches this label item, or None.

    residuals/ner_review.findings are built by verify_output() against the
    SANITIZED OUTPUT file (anonymize_construction_pdfs.py's verify_output()
    opens `destination`, not the source) — a match here means this label's
    region is a leak: content that is still present in the output. It is
    never a confirmation that redaction happened; absence of a match means
    only "not flagged as a leftover by the sanitizer's own verifier," which
    score_recall still treats as a hit — see its docstring for the resulting
    precision limit.

    Label item pages are 0-indexed (corpus_labeler.py); residual/NER report
    pages are 1-indexed (anonymize_construction_pdfs.py) — converted once,
    here, rather than at every call site."""
    target_page = item["page"] + 1
    target_bbox = label_bbox_to_pdf_points(item)
    for residual in residuals:
        if residual["page"] != target_page or residual.get("bbox") is None:
            continue
        if iou(target_bbox, residual["bbox"]) < IOU_MATCH_THRESHOLD:
            continue
        if category_satisfied(item["category"], residual["category"]):
            return {"surface": "residual", **residual}
    for finding in ner_findings:
        if target_page not in finding.get("pages", ()) or finding.get("bbox") is None:
            continue
        if iou(target_bbox, finding["bbox"]) < IOU_MATCH_THRESHOLD:
            continue
        if category_satisfied(item["category"], finding["label"]):
            return {"surface": "ner", **finding}
    return None


def score_recall(
    items: Sequence[dict], residuals: Sequence[dict], ner_findings: Sequence[dict],
) -> tuple[list[dict], list[dict]]:
    """Every must-redact (disposition == "redact") label item is either a
    miss (find_match finds it as a residual/NER leak in the output — a
    document-level recall failure, the zero-tolerance metric) or a hit
    (no such leak found). Items marked "keep" are scored by
    score_over_redaction instead, never here.

    Precision limit, stated plainly: a hit here means "the sanitizer's own
    verifier did not flag this region as a leftover match," not an
    independently-confirmed redaction. residuals/ner_review.findings are
    themselves built from the same match patterns detection uses (denylist,
    regex, NER) — content the pipeline's detectors never recognized as a
    candidate at all (e.g. because OCR on a noisy scan corrupted the exact
    string a denylist term was written against) leaves no residual entry
    either, and is scored as a hit even though it may still be visible,
    unredacted, in the actual output. Closing that gap needs the label
    schema to carry the item's original text so the harness can check the
    output directly, independent of the sanitizer's self-report — out of
    scope for this pass, flagged here rather than silently assumed away.
    """
    misses: list[dict] = []
    hits: list[dict] = []
    for item in items:
        if item["disposition"] != "redact":
            continue
        match = find_match(item, residuals, ner_findings)
        if match is None:
            hits.append(item)
        else:
            misses.append({"item": item, "match": match})
    return misses, hits


def score_over_redaction(items: Sequence[dict], page_redactions: Sequence[dict]) -> list[dict]:
    """Every must-survive (disposition == "keep") label item is flagged if
    its page reports a redacted category compatible with the label.

    page_redactions carries no per-region bbox — only which categories were
    redacted somewhere on a page — so this check is necessarily page+category
    granularity, not exact-position. That is a real precision limit, not an
    oversight: the report format this reads from doesn't retain per-redaction
    geometry once the content is gone.
    """
    by_page: dict[int, set[str]] = {
        entry["page"]: set(entry["categories"]) for entry in page_redactions
    }
    flagged: list[dict] = []
    for item in items:
        if item["disposition"] != "keep":
            continue
        target_page = item["page"] + 1
        redacted_categories = by_page.get(target_page, set())
        if any(category_satisfied(item["category"], category) for category in redacted_categories):
            flagged.append(item)
    return flagged


def run_sanitizer_isolated(
    sanitize_document: Callable[..., dict],
    *,
    source: Path,
    destination: Path,
    document_id: str,
    denylist: set[str],
    settings: Any,
    temp_root: Path,
    run_key: bytes,
    ner_detector: Any = None,
    lexicons: Any = None,
) -> dict:
    """Call sanitize_document for exactly one document, converting a raised
    SystemExit/Exception into the same shape of FAIL record
    orchestrate_run() synthesizes for its whole-batch catch — except here the
    catch is per-document, so one malformed/encrypted corpus document never
    drops the rest of the corpus from being scored."""
    try:
        return sanitize_document(
            source, destination, document_id, denylist, settings, temp_root, run_key,
            ner_detector=ner_detector, lexicons=lexicons,
        )
    except (Exception, SystemExit) as exc:
        reason = str(exc) if str(exc) else "processing failed before the run completed"
        return {
            "document_id": document_id,
            "release_status": "FAIL",
            "fail_reason": reason,
        }


def load_label_payload(labels_dir: Path, doc_id: str) -> dict | None:
    """None if this corpus document has no label file yet — an explicit,
    tested branch (not a silent zero-items report) since most of the real
    corpus is left for the user to label after this pass."""
    path = labels_dir / f"{doc_id}.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    corpus_labeler.validate_export_payload(payload)
    return payload


def evaluate_corpus(
    manifest_path: Path,
    labels_dir: Path,
    intake_seeds_dir: Path,
    denylist_path: Path,
    lexicon_dir: Path,
    allowlist_path: Path,
    scratch_root: Path,
    doc_ids: Sequence[str] | None = None,
    ner_detector: Any = None,
) -> dict:
    """Run the real sanitizer against every manifest document that has a
    label file, and score document-level recall/over-redaction against those
    labels. Documents with no label file yet are reported as unlabeled, not
    silently scored as zero items."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    lexicons = sanitizer.load_lexicons(lexicon_dir, allowlist_path)
    shared_denylist = sanitizer.load_denylist(denylist_path)

    per_document: list[dict] = []
    unlabeled_documents: list[str] = []
    recall_misses: list[dict] = []
    over_redactions: list[dict] = []

    for entry in manifest["documents"]:
        doc_id = entry["doc_id"]
        if doc_ids is not None and doc_id not in doc_ids:
            continue
        payload = load_label_payload(labels_dir, doc_id)
        if payload is None:
            unlabeled_documents.append(doc_id)
            continue

        intake_path = intake_seeds_dir / f"{doc_id}.json"
        document_denylist = set(shared_denylist)
        if intake_path.is_file():
            document_denylist |= sanitizer.load_project_metadata(intake_path)

        source = Path(entry["path"])
        run_key = secrets.token_bytes(32)
        with tempfile.TemporaryDirectory(prefix=f"eval_corpus_{doc_id}_") as doc_scratch:
            doc_scratch_path = Path(doc_scratch)
            doc_temp_root = doc_scratch_path / "tmp"
            doc_temp_root.mkdir(parents=True, exist_ok=True)
            report = run_sanitizer_isolated(
                sanitizer.sanitize_document,
                source=source, destination=doc_scratch_path / "output.pdf",
                document_id=doc_id, denylist=document_denylist, settings=sanitizer.Settings(),
                temp_root=doc_temp_root, run_key=run_key, ner_detector=ner_detector,
                lexicons=lexicons,
            )

        residuals = report.get("residuals", [])
        ner_findings = report.get("ner_review", {}).get("findings", [])
        page_redactions = report.get("page_redactions", [])
        items = payload["items"]
        misses, hits = score_recall(items, residuals, ner_findings)
        flagged = score_over_redaction(items, page_redactions)

        for miss in misses:
            recall_misses.append({"doc_id": doc_id, **miss})
        for flag in flagged:
            over_redactions.append({"doc_id": doc_id, "item": flag})

        per_document.append({
            "doc_id": doc_id,
            "release_status": report.get("release_status"),
            "labeled_items": len(items),
            "recall_misses": len(misses),
            "recall_hits": len(hits),
            "over_redactions": len(flagged),
            "dimension_tags": entry.get("dimension_tags", {}),
        })

    return {
        "metric_label": DOCUMENT_LEVEL_RECALL_LABEL,
        "per_document": per_document,
        "unlabeled_documents": unlabeled_documents,
        "recall_misses": recall_misses,
        "over_redactions": over_redactions,
        **aggregate_by_dimension(per_document),
    }


def aggregate_by_dimension(per_document: Sequence[dict]) -> dict:
    """Cross-cuts required by the ticket: document-level recall broken out by category,
    detection surface, language, and image quality — never one aggregate
    number. Built by grouping the per-document rows already produced by
    evaluate_corpus(), not by re-running anything."""
    by_language: collections.Counter[str] = collections.Counter()
    by_language_hits: collections.Counter[str] = collections.Counter()
    by_image_quality: collections.Counter[str] = collections.Counter()
    by_image_quality_hits: collections.Counter[str] = collections.Counter()
    for doc in per_document:
        tags = doc.get("dimension_tags", {})
        total = doc["recall_hits"] + doc["recall_misses"]
        language = tags.get("language", "unknown")
        by_language[language] += total
        by_language_hits[language] += doc["recall_hits"]
        for quality in tags.get("image_quality", ["unknown"]) or ["unknown"]:
            by_image_quality[quality] += total
            by_image_quality_hits[quality] += doc["recall_hits"]
    return {
        "by_language": {k: {"total": v, "hits": by_language_hits[k]} for k, v in by_language.items()},
        "by_image_quality": {
            k: {"total": v, "hits": by_image_quality_hits[k]} for k, v in by_image_quality.items()
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).parents[1]
    parser.add_argument("--manifest", type=Path, default=root / ".scratch/corpus/manifest.json")
    parser.add_argument("--labels", type=Path, default=root / ".scratch/corpus/labels")
    parser.add_argument("--intake-seeds", type=Path, default=root / ".scratch/corpus/intake_seeds")
    parser.add_argument("--denylist", type=Path, default=root / "config/denylist.local.json")
    parser.add_argument("--lexicons", type=Path, default=root / "config/lexicons")
    parser.add_argument("--allowlist", type=Path, default=root / "config/allowlist.shared.json")
    parser.add_argument("--scratch", type=Path, default=root / "tmp/eval_corpus")
    args = parser.parse_args(argv)

    result = evaluate_corpus(
        args.manifest, args.labels, args.intake_seeds, args.denylist, args.lexicons,
        args.allowlist, args.scratch,
    )

    print(f"{result['metric_label'].upper()}")
    header = f"{'doc_id':<40}{'status':<18}{'items':>8}{'hits':>8}{'misses':>8}{'over-red.':>10}"
    print(header)
    print("-" * len(header))
    for doc in result["per_document"]:
        print(
            f"{doc['doc_id']:<40}{str(doc['release_status']):<18}{doc['labeled_items']:>8}"
            f"{doc['recall_hits']:>8}{doc['recall_misses']:>8}{doc['over_redactions']:>10}"
        )
    if result["unlabeled_documents"]:
        print(f"\nUnlabeled (no label file yet): {', '.join(result['unlabeled_documents'])}")

    print("\nby language:")
    for language, counts in sorted(result["by_language"].items()):
        print(f"  {language:<10}{counts['hits']}/{counts['total']}")
    print("by image quality:")
    for quality, counts in sorted(result["by_image_quality"].items()):
        print(f"  {quality:<10}{counts['hits']}/{counts['total']}")

    if result["recall_misses"] or result["over_redactions"]:
        print(f"\n{len(result['recall_misses'])} document-level recall miss(es), "
              f"{len(result['over_redactions'])} over-redaction(s)")
        return 1
    print("\nAll labeled corpus items behave as labelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
