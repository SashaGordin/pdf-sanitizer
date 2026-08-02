#!/usr/bin/env python3
"""Independently verify an existing sanitizer run against current policy.

This command accepts one atomically-published run directory. It validates
artifact hashes and the recorded policy/code fingerprint, then scans both
independently-extracted text and an independently-rendered/OCR'd view of every
page. It is local-only and read-only.

Independence is a safety property: do not import or reuse extraction,
rendering, OCR, or matching helpers from anonymize_construction_pdfs.py. A
shared blind spot would defeat the reason this second verifier exists.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    import fitz
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyMuPDF is required. Install requirements-anonymizer.txt.") from exc


LEXICON_FILENAMES = (
    "contract_defined_terms.json", "structural_patterns.json", "boilerplate.json",
)
DASHES = r"\-‐-―−"
DIRECT_PATTERNS: dict[str, re.Pattern[str]] = {
    "file_path": re.compile(
        r"(?i)(?:[A-Z]:[\\/]|\\\\[A-Z0-9._-]+[\\/])[^\n\"'<>|]{4,200}"
        r"|/(?:Users|home)/[A-Za-z0-9._-]+/[^\n\"'<>|]{2,200}"
    ),
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]{2,}@[A-Z0-9.-]{2,}"),
    "url": re.compile(
        r"(?i)\b(?:https?://|www\.)[^\s<>()]+|"
        r"\b[A-Z0-9.-]+\.(?:com|org|net|gov|edu|us|co|io|biz)\b"
    ),
    "phone": re.compile(
        r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}"
        r"(?:\s*(?:x|ext\.?|extension)\s*\d+)?(?!\d)", re.I,
    ),
    "po_box": re.compile(r"(?i)\bP\.?\s*O\.?\s+Box\s+\d+[A-Z-]*\b"),
    "street_address": re.compile(
        r"(?i)\b\d{1,7}[A-Z]?(?:[- ]\d{1,7})?\s+"
        r"(?:[A-Z0-9.'#-]+\s+){0,8}"
        r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|"
        r"Court|Ct|Circle|Cir|Highway|Hwy|Parkway|Pkwy|Way|Place|Pl|"
        r"Terrace|Ter|Trail|Trl)\.?\b"
    ),
    "city_state_zip": re.compile(
        r"(?i)\b[A-Z][A-Z .'-]{1,40},?\s+"
        r"(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|"
        r"MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|"
        r"RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\s+\d{5}(?:-\d{4})?\b"
    ),
    "coordinate": re.compile(
        r"(?i)(?<!\d)[+-]?\d{1,3}\.\d{4,}\s*[,;/]\s*[+-]?\d{1,3}\.\d{4,}(?!\d)|"
        r"\b\d{1,3}\s*[°]\s*\d{1,2}\s*['′]\s*\d{1,2}(?:\.\d+)?\s*[\"″]?\s*[NSEW]\b"
    ),
    "parcel_or_lot": re.compile(
        r"(?i)\b(?:APN|parcel|assessor(?:'s)? parcel|lot|legal description)\b"
        r"\s*(?:number|no\.?|#)?\s*[:#-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b"
    ),
    "permit_or_application": re.compile(
        r"(?i)\b(?:permit|application|license|licence|registration|certificate)"
        r"\s*(?:number|no\.?|#)\s*[:#-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b"
    ),
    "project_or_job_number": re.compile(
        r"(?i)\b(?:project|job|commission|contract)\s*(?:number|no\.?|#)"
        r"\s*[:#-]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b"
    ),
    "credentialed_person": re.compile(
        r"\b(?:[A-Z][A-Za-z'-]+\s+){1,4}[A-Z][A-Za-z'-]+,?\s+"
        r"(?:P\.?E\.?|AIA|R\.?A\.?|NCARB|LEED(?:\s+AP)?|PMP|CCM)\b"
    ),
}
LABEL_NAME = (
    r"owner|client|tenant|architect(?: of record)?|engineer|engineering|designer|design firm|"
    r"consultant|contractor|construction manager|developer|vendor|prepared by|"
    r"submitted by|drawn by|checked by|approved by|contact|project(?: name)?|"
    r"property(?: name)?|project location|site address|address|facility|"
    r"jurisdiction|city|agency|permit(?: (?:number|no\.?|num\.?|#))?|"
    r"application(?: (?:number|no\.?|num\.?|#))?|"
    r"project(?: (?:number|no\.?|num\.?|#))?|job(?: (?:number|no\.?|num\.?|#))?|"
    r"parcel(?: (?:number|no\.?|num\.?|#))?|APN|lot(?: (?:number|no\.?|num\.?|#))?|"
    r"legal description|copyright|signature|seal"
)
LABEL_RE = re.compile(rf"(?i)^\s*(?:{LABEL_NAME})\s*(?::|-)?\s*$")
LABEL_VALUE_RE = re.compile(rf"(?i)^\s*(?:{LABEL_NAME})\s*[:|-]\s*(.+)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_identity(script_path: Path, repo_root: Path) -> dict:
    commit: str | None = None
    dirty: bool | None = None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        if (
            head.returncode == 0 and top.returncode == 0
            and Path(top.stdout.strip()).resolve() == repo_root.resolve()
        ):
            commit = head.stdout.strip()
            diff = subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--", str(script_path.resolve())],
                cwd=repo_root, timeout=5,
            )
            dirty = {0: False, 1: True}.get(diff.returncode)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "commit": commit,
        "commit_dirty": dirty,
        "build_digest_sha256": sha256_file(script_path),
    }


def current_fingerprint(
    *, sanitizer_script: Path, repo_root: Path, denylist: Path | None,
    project_metadata: Path | None, config: Path, allowlist: Path | None,
    lexicons: Path,
) -> dict:
    def optional_hash(path: Path | None) -> str | None:
        return sha256_file(path) if path and path.is_file() else None

    return {
        "code": code_identity(sanitizer_script, repo_root),
        "denylist_sha256": optional_hash(denylist),
        "project_metadata_sha256": optional_hash(project_metadata),
        "config_sha256": sha256_file(config),
        "allowlist_sha256": optional_hash(allowlist),
        "lexicon_sha256": {
            name: sha256_file(lexicons / name)
            for name in LEXICON_FILENAMES if (lexicons / name).is_file()
        },
    }


def collect_strings(node: object) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if not key.startswith("_"):
                yield from collect_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from collect_strings(value)
    elif isinstance(node, str):
        yield node


def load_terms(*paths: Path | None) -> list[str]:
    terms: set[str] = set()
    for path in paths:
        if path is None or not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        terms.update(value.strip() for value in collect_strings(payload) if len(value.strip()) >= 3)
    return sorted(terms, key=lambda value: (-len(value), value.casefold()))


def term_pattern(term: str) -> str:
    pieces = [re.escape(part) for part in re.split(rf"[\s{DASHES}]+", term) if part]
    return rf"[\s{DASHES}.,]*".join(pieces)


@dataclass(frozen=True)
class IndependentLine:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    group: tuple[int, int] | None = None


@dataclass(frozen=True)
class IndependentPolicy:
    denylist: re.Pattern[str]
    defined_terms: frozenset[str]
    defined_strip: str
    defined_affixes: tuple[str, ...]
    allowlist: frozenset[str]
    allow_strip: str
    allow_suffixes: tuple[str, ...]
    structural: tuple[tuple[str, re.Pattern[str]], ...]
    boilerplate: tuple[str, ...]

    @classmethod
    def load(cls, denylist_path: Path | None, project_metadata: Path | None,
             lexicon_dir: Path, allowlist_path: Path | None) -> "IndependentPolicy":
        terms = load_terms(denylist_path, project_metadata)
        alternatives = "|".join(term_pattern(term) for term in terms) or r"(?!)"
        denylist_re = re.compile(rf"(?i)(?<![A-Z0-9])(?:{alternatives})(?![A-Z0-9])")
        defined = json.loads((lexicon_dir / LEXICON_FILENAMES[0]).read_text(encoding="utf-8"))
        structural = json.loads((lexicon_dir / LEXICON_FILENAMES[1]).read_text(encoding="utf-8"))
        boilerplate = json.loads((lexicon_dir / LEXICON_FILENAMES[2]).read_text(encoding="utf-8"))
        allow = json.loads(allowlist_path.read_text(encoding="utf-8")) if allowlist_path and allowlist_path.is_file() else {}
        entries = allow.get("entries", []) if isinstance(allow, dict) else []
        allow_terms: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("standards_bodies", "manufacturers", "entries", "terms"):
                allow_terms.update(
                    value.strip().casefold() for value in collect_strings(entry.get(key, []))
                    if value.strip()
                )
        return cls(
            denylist=denylist_re,
            defined_terms=frozenset(str(value).casefold() for value in defined.get("terms", [])),
            defined_strip=str(defined.get("strip_chars", "")),
            defined_affixes=tuple(str(value).casefold() for value in defined.get("strip_affixes", [])),
            allowlist=frozenset(allow_terms),
            allow_strip=str(allow.get("strip_chars", "")),
            allow_suffixes=tuple(str(value).casefold() for value in allow.get("strip_suffixes", [])),
            structural=tuple(
                (str(item["name"]), re.compile(str(item["pattern"]), re.I))
                for item in structural.get("suppress_if_span_matches", [])
            ),
            boilerplate=tuple(str(value).casefold() for value in boilerplate.get("phrases", [])),
        )

    def suppressed(self, category: str, value: str, context: str) -> bool:
        if category == "denylist":
            return False
        folded = re.sub(r"\s+", " ", value).strip().casefold()
        if category == "street_address" and re.search(
            r"\b\d{1,3}\s*['\"]\s*[-–—]?\s*\d{1,3}\s*(?:ST|RD|DR|LN|CT|CIR|PL)\b",
            context, re.I,
        ):
            return True
        if category == "street_address" and re.match(r"^\s*\d+\.\d+\s+", context):
            return True
        trimmed_defined = folded.strip(self.defined_strip or None)
        if trimmed_defined in self.defined_terms:
            return True
        for affix in self.defined_affixes:
            if trimmed_defined.endswith(affix) and trimmed_defined[:-len(affix)].strip() in self.defined_terms:
                return True
        trimmed_allow = folded.strip(self.allow_strip or None)
        candidates = {trimmed_allow}
        for suffix in self.allow_suffixes:
            suffix_folded = suffix.casefold().strip(" .")
            if trimmed_allow.rstrip(".").endswith(" " + suffix_folded):
                candidates.add(trimmed_allow.rstrip(".")[:-(len(suffix_folded) + 1)].rstrip())
        if candidates & self.allowlist:
            return True
        bare = folded.strip(" \t.,:;-—–'’\"()[]{}*#|_")
        if any(
            pattern.fullmatch(folded) or (bare and pattern.fullmatch(bare))
            for _name, pattern in self.structural
        ):
            return True
        context_folded = context.casefold()
        return any(phrase in folded or phrase in context_folded for phrase in self.boilerplate)

    @staticmethod
    def aligned(first: IndependentLine, second: IndependentLine, units_per_point: float) -> bool:
        width = max(1.0, first.right - first.left)
        return (
            abs(second.left - first.left) <= max(36 * units_per_point, width * 0.5)
            or min(first.right, second.right) > max(first.left, second.left)
        )

    def scan_lines(
        self, lines: Sequence[IndependentLine], units_per_point: float = 1.0,
    ) -> collections.Counter[str]:
        hits: collections.Counter[str] = collections.Counter()
        ordered = sorted(lines, key=lambda line: (line.top, line.left))
        normalized_lines = [
            IndependentLine(
                re.sub(r"\s+", " ", line.text).strip(),
                line.left, line.top, line.right, line.bottom, line.group,
            ) for line in ordered if line.text.strip()
        ]
        denylist_spans: set[tuple[int, int]] = set()
        grouped: dict[tuple[int, int] | tuple[str, int], list[tuple[int, IndependentLine]]] = collections.defaultdict(list)
        for index, line in enumerate(normalized_lines):
            if self.denylist.search(line.text):
                denylist_spans.add((index, index))
            grouped[line.group if line.group is not None else ("line", index)].append((index, line))
        for members in grouped.values():
            paragraph = "\n".join(line.text for _index, line in members)
            if self.denylist.search(paragraph):
                denylist_spans.add((members[0][0], members[-1][0]))
        if denylist_spans:
            hits["denylist"] = len(denylist_spans)
        for line in normalized_lines:
            for category, pattern in DIRECT_PATTERNS.items():
                for match in pattern.finditer(line.text):
                    if not self.suppressed(category, match.group(), line.text):
                        hits[category] += 1
            labelled = LABEL_VALUE_RE.match(line.text)
            if labelled and not self.suppressed("labelled_identifier", labelled.group(1), line.text):
                hits["labelled_identifier"] += 1
        for index, line in enumerate(normalized_lines):
            if not LABEL_RE.match(line.text):
                continue
            for following in normalized_lines[index + 1:]:
                if following.top - line.bottom > 72 * units_per_point:
                    break
                if not self.aligned(line, following, units_per_point):
                    continue
                if LABEL_RE.match(following.text):
                    continue
                if (
                    label_value_candidate(line.text, following.text)
                    and len(following.text.split()) <= 10
                    and not self.suppressed(
                        "labelled_identifier", following.text, following.text,
                    )
                ):
                    hits["labelled_identifier"] += 1
                    break
        return hits

    def scan(self, text: str) -> collections.Counter[str]:
        """Text-only convenience seam for tests; production uses geometry."""
        lines = [
            IndependentLine(value, 0, index * 20, 100, index * 20 + 12)
            for index, value in enumerate(text.splitlines()) if value.strip()
        ]
        return self.scan_lines(lines)


def redactable_phrase(value: str, *, allow_short: bool = False) -> bool:
    value = re.sub(r"\s+", " ", value).strip(" \t:;,-")
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    minimum = 2 if allow_short else 4
    if len(value) < minimum or len(value) > 180 or not tokens:
        return False
    if not allow_short and len(tokens) == 1 and len(tokens[0]) < 7:
        return False
    return any(char.isalpha() for char in value) or (
        allow_short and any(char.isdigit() for char in value)
    )


def label_value_candidate(label: str, value: str) -> bool:
    """Reject drawing/table codes while retaining numeric identifier values."""
    if not redactable_phrase(value, allow_short=True):
        return False
    numeric_label = re.search(
        r"(?i)\b(?:permit|application|project|job|parcel|APN|lot)\b",
        label,
    )
    if numeric_label:
        return True
    if re.search(r"\d\s*['\"ø]|['\"ø]\s*\d", value):
        return False
    return sum(char.isalpha() for char in value) >= 4


def masked_shape(value: str) -> str:
    return "".join(
        "A" if char.isupper() else "a" if char.isalpha() else "9" if char.isdigit() else char
        for char in value
    )[:80]


def independent_extracted_lines(page: fitz.Page) -> list[IndependentLine]:
    """Independent extraction from MuPDF's raw dictionary structure."""
    result: list[IndependentLine] = []
    payload = page.get_text("dict", sort=False)
    for block_index, block in enumerate(payload.get("blocks", [])):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text or not spans:
                continue
            x0, y0, x1, y1 = line["bbox"]
            result.append(IndependentLine(text, x0, y0, x1, y1, (block_index, 0)))
    return result


def independent_rendered_lines(
    page: fitz.Page, tesseract: str, dpi: int, temp_dir: Path,
) -> list[IndependentLine]:
    """Separate renderer/OCR invocation; never call pipeline page_image or OCR helpers."""
    executable = shutil.which(tesseract)
    if executable is None:
        raise RuntimeError("local OCR executable is unavailable")
    pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), colorspace=fitz.csRGB, alpha=False)
    image_path = temp_dir / f"page-{page.number + 1:05d}.png"
    pixmap.save(image_path)
    try:
        completed = subprocess.run(
            [executable, str(image_path), "stdout", "-l", "eng", "--psm", "11", "tsv"],
            capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("independent rendered OCR timed out") from exc
    if completed.returncode != 0:
        raise RuntimeError("independent rendered OCR failed")
    words: list[tuple[str, int, int, int, int]] = []
    reader = csv.DictReader(
        io.StringIO(completed.stdout.decode("utf-8", errors="replace")),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    )
    try:
        for row in reader:
            text = re.sub(r"\s+", " ", row.get("text", "")).strip()
            if not text:
                continue
            words.append((
                text, int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"]),
            ))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("independent rendered OCR returned malformed coordinates") from exc
    # PSM 11 can reuse block/paragraph/line IDs for distant sparse glyphs on
    # large drawings. Derive rows from geometry instead of trusting those IDs.
    rows: list[list[tuple[str, int, int, int, int]]] = []
    row_centers: list[float] = []
    for word in sorted(words, key=lambda item: (item[2] + item[4] / 2, item[1])):
        center = word[2] + word[4] / 2
        if not rows or abs(center - row_centers[-1]) > max(8.0, word[4] * 0.6):
            rows.append([word])
            row_centers.append(center)
        else:
            rows[-1].append(word)
            row_centers[-1] = sum(item[2] + item[4] / 2 for item in rows[-1]) / len(rows[-1])
    lines: list[IndependentLine] = []
    for row_index, row in enumerate(rows):
        row.sort(key=lambda word: word[1])
        # A drawing can contain unrelated text at the same Y coordinate across
        # the whole sheet. Split those visual rows at material horizontal gaps.
        segments: list[list[tuple[str, int, int, int, int]]] = []
        for word in row:
            if not segments:
                segments.append([word])
                continue
            previous = segments[-1][-1]
            gap = word[1] - (previous[1] + previous[3])
            threshold = max(100.0, max(previous[4], word[4]) * 4.0)
            if gap > threshold:
                segments.append([word])
            else:
                segments[-1].append(word)
        for segment_index, segment in enumerate(segments):
            lines.append(IndependentLine(
                " ".join(word[0] for word in segment),
                min(word[1] for word in segment), min(word[2] for word in segment),
                max(word[1] + word[3] for word in segment),
                max(word[2] + word[4] for word in segment),
                (row_index, segment_index),
            ))
    return lines


def verify_pdf(pdf_path: Path, policy: IndependentPolicy, tesseract: str, dpi: int) -> dict:
    extracted: collections.Counter[str] = collections.Counter()
    rendered: collections.Counter[str] = collections.Counter()
    page_results: list[dict] = []
    errors = 0
    doc = fitz.open(pdf_path)
    with tempfile.TemporaryDirectory(prefix="verify_existing_") as temp_name:
        temp_dir = Path(temp_name)
        for page_index, page in enumerate(doc):
            extracted_hits = policy.scan_lines(independent_extracted_lines(page))
            extracted.update(extracted_hits)
            try:
                rendered_hits = policy.scan_lines(
                    independent_rendered_lines(page, tesseract, dpi, temp_dir), dpi / 72.0,
                )
                rendered.update(rendered_hits)
                status = "clean" if not rendered_hits and not extracted_hits else "unresolved"
            except Exception:
                rendered_hits = collections.Counter()
                errors += 1
                status = "error"
            page_results.append({
                "page": page_index + 1,
                "status": status,
                "extracted_match_counts": dict(sorted(extracted_hits.items())),
                "rendered_match_counts": dict(sorted(rendered_hits.items())),
            })
    pages = len(doc)
    doc.close()
    return {
        "pages": pages,
        "extracted_match_counts": dict(sorted(extracted.items())),
        "rendered_match_counts": dict(sorted(rendered.items())),
        "page_results": page_results,
        "ocr_errors": errors,
        "release_status": "AUTOMATED_PASS" if not extracted and not rendered and not errors else "FAIL",
    }


def verify_run(
    run_dir: Path, *, denylist: Path | None, project_metadata: Path | None,
    config: Path, lexicons: Path, allowlist: Path | None, sanitizer_script: Path,
    tesseract: str = "tesseract", dpi: int = 300,
) -> dict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError("run directory has no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repo_root = sanitizer_script.resolve().parent.parent
    fingerprint = current_fingerprint(
        sanitizer_script=sanitizer_script, repo_root=repo_root, denylist=denylist,
        project_metadata=project_metadata, config=config, allowlist=allowlist,
        lexicons=lexicons,
    )
    fingerprint_status = "current" if fingerprint == manifest.get("fingerprint") else "stale"
    policy = IndependentPolicy.load(denylist, project_metadata, lexicons, allowlist)
    documents: list[dict] = []
    for recorded in manifest.get("documents", []):
        output = recorded.get("output", {})
        relative = output.get("path")
        pdf_path = run_dir / relative if relative else None
        if pdf_path is None or not pdf_path.is_file():
            documents.append({
                "document_id": recorded.get("document_id"),
                "artifact_status": "missing",
                "release_status": "FAIL",
            })
            continue
        artifact_status = (
            "current" if sha256_file(pdf_path) == output.get("sha256") else "hash_mismatch"
        )
        verification = verify_pdf(pdf_path, policy, tesseract, dpi)
        if artifact_status != "current" or fingerprint_status != "current":
            verification["release_status"] = "FAIL"
        documents.append({
            "document_id": recorded.get("document_id"),
            "artifact_status": artifact_status,
            **verification,
        })
    clean = bool(documents) and all(
        item.get("release_status") == "AUTOMATED_PASS" for item in documents
    )
    return {
        "run_id": manifest.get("run_id"),
        "fingerprint_status": fingerprint_status,
        "documents": documents,
        "release_status": "AUTOMATED_PASS" if clean and fingerprint_status == "current" else "FAIL",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--denylist", type=Path, default=root / "config/denylist.local.json")
    parser.add_argument("--project-metadata", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=root / "config/sanitizer.json")
    parser.add_argument("--lexicons", type=Path, default=root / "config/lexicons")
    parser.add_argument("--allowlist", type=Path, default=root / "config/allowlist.shared.json")
    parser.add_argument("--tesseract", default="tesseract")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        result = verify_run(
            args.run_directory, denylist=args.denylist,
            project_metadata=args.project_metadata, config=args.config,
            lexicons=args.lexicons, allowlist=args.allowlist,
            sanitizer_script=root / "tools/anonymize_construction_pdfs.py",
            tesseract=args.tesseract, dpi=args.dpi,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"verify-existing failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["release_status"] == "AUTOMATED_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
