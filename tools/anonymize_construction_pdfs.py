#!/usr/bin/env python3
"""Local-only construction PDF sanitizer.

No extracted document text or detected value is printed or written to the
review report. Source PDFs are opened read-only and outputs use neutral names.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import hmac
import importlib.metadata
import io
import json
import os
import platform
import re
import resource
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Required local PDF dependencies are missing. Install requirements-anonymizer.txt."
    ) from exc

try:
    import zxingcpp
except ImportError:  # pragma: no cover
    zxingcpp = None


DIRECT_PATTERNS: dict[str, re.Pattern[str]] = {
    # CAD/BIM save paths embedded in drawing revision footers. Deterministic
    # and high-value: one of these leaked both a drafter's OS username and the
    # real project city on a sheet where every other identifier had been
    # redacted. Too reliable a pattern to leave to fuzzy NER.
    "file_path": re.compile(
        # Consumes to end of line rather than to the first space: real save
        # paths contain spaces ("...\21109 Panama City MLK - New Building...")
        # and are frequently truncated mid-name by the footer, so stopping at
        # a space or requiring an extension leaves the project name exposed.
        r"(?i)(?:[A-Z]:[\\/]|\\\\[A-Z0-9._-]+[\\/])[^\n\"'<>|]{4,200}"
        r"|/(?:Users|home)/[A-Za-z0-9._-]+/[^\n\"'<>|]{2,200}"
    ),
    # The trailing TLD is optional because OCR frequently splits it onto a
    # separate line; a local-part plus @ plus domain fragment is still unsafe.
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]{2,}@[A-Z0-9.-]{2,}"),
    "url": re.compile(
        r"(?i)\b(?:https?://|www\.)[^\s<>()]+|"
        r"\b[A-Z0-9.-]+\.(?:com|org|net|gov|edu|us|co|io|biz)\b"
    ),
    "phone": re.compile(
        r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}"
        r"(?:\s*(?:x|ext\.?|extension)\s*\d+)?(?!\d)",
        re.I,
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
FIRM_RE = re.compile(
    r"(?i)\b(?:architects?|architecture|engineering|engineers?|design\s+(?:group|studio)|"
    r"consultants?|surveyors?|planners?|construction\s+management|contractors?)\b"
)
COPYRIGHT_RE = re.compile(r"(?i)(?:©|\bcopyright\b|all rights reserved)")
MAP_LABEL_RE = re.compile(r"(?i)\b(?:vicinity|location|site|area)\s+map\b")
SAFE_FILENAME_WORDS = {
    "drawing", "drawings", "plan", "plans", "spec", "specs", "specification",
    "specifications", "mep", "mechanical", "electrical", "plumbing", "architectural",
    "structural", "civil", "set", "bid", "permit", "construction", "documents", "pdf",
    "combined", "issued", "final", "volume", "vol",
}


class PageProcessingError(RuntimeError):
    def __init__(self, page_number: int, reason: str):
        self.page_number = page_number
        self.reason = reason
        super().__init__(f"page {page_number}: {reason}")


# Release-status vocabulary. Replaces the old single PASS/FAIL string and the
# HUMAN_VISUAL_REVIEW_REQUIRED constant everywhere in the report.
#
# A per-document report's release_status is the automated-gate result alone
# (AUTOMATED_PASS/FAIL): a document has no reviewer of its own, so it cannot
# know whether human review has happened. AUTOMATED_PASS is documented here as
# non-terminal — nothing downstream may treat it as safe to ship.
#
# An overall-run status additionally folds in review state via
# derive_release_status() below, and is the only place REVIEW_REQUIRED,
# REVIEW_INCOMPLETE, and RELEASED can appear.
RELEASE_STATUS_AUTOMATED_PASS = "AUTOMATED_PASS"
RELEASE_STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"
RELEASE_STATUS_REVIEW_INCOMPLETE = "REVIEW_INCOMPLETE"
RELEASE_STATUS_FAIL = "FAIL"
RELEASE_STATUS_RELEASED = "RELEASED"
RELEASE_STATUSES = frozenset({
    RELEASE_STATUS_AUTOMATED_PASS, RELEASE_STATUS_REVIEW_REQUIRED,
    RELEASE_STATUS_REVIEW_INCOMPLETE, RELEASE_STATUS_FAIL, RELEASE_STATUS_RELEASED,
})

TESSERACT_TIMEOUT_SECONDS = 120
GHOSTSCRIPT_TIMEOUT_SECONDS = 30 * 60


def automated_gate_status(checks: dict[str, bool]) -> str:
    """The automated-only verdict for one document: AUTOMATED_PASS or FAIL.

    Never terminal on its own — see the release-status vocabulary note above.
    """
    return RELEASE_STATUS_AUTOMATED_PASS if all(checks.values()) else RELEASE_STATUS_FAIL


def derive_release_status(automated_status: str, review: dict | None = None) -> str:
    """The outward release status for a run, folding automated results
    together with review state.

    `review` is None (review has not started) until a human records one via
    the manifest's review fields (populated outside this tool — no reviewer
    UI is in scope). Its `status` key, once present, is "complete" or
    "incomplete".
    """
    if automated_status != RELEASE_STATUS_AUTOMATED_PASS:
        return RELEASE_STATUS_FAIL
    if not review or not review.get("status"):
        return RELEASE_STATUS_REVIEW_REQUIRED
    if review["status"] != "complete":
        return RELEASE_STATUS_REVIEW_INCOMPLETE
    return RELEASE_STATUS_RELEASED


@dataclass(frozen=True)
class Line:
    block: int
    line: int
    text: str
    words: tuple[tuple, ...]
    rect: fitz.Rect


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    block: int
    paragraph: int
    line: int


@dataclass(frozen=True)
class Region:
    name: str
    category: str
    rect: tuple[float, float, float, float]
    pages: str | tuple[int, ...]

    def applies(self, page_number: int) -> bool:
        return self.pages == "all" or page_number in self.pages


@dataclass(frozen=True)
class Detection:
    """One located candidate, with enough provenance to audit and measure it.

    `text` is the matched string. It stays in memory for suppression decisions
    and is never serialized — the report carries keyed digests only.
    """

    rect: fitz.Rect
    category: str
    detector: str
    page: int = 0
    span: tuple[int, int] | None = None
    text: str | None = None
    score: float | None = None
    zone: str | None = None
    evidence: tuple[str, ...] = ()
    suppressed_by: str | None = None

    def bbox(self) -> tuple[float, float, float, float]:
        return (round(self.rect.x0, 2), round(self.rect.y0, 2),
                round(self.rect.x1, 2), round(self.rect.y1, 2))


TABLE_FRAGILE_CATEGORIES = frozenset({
    "street_address", "credentialed_person", "project_or_job_number",
    "parcel_or_lot", "permit_or_application", "labelled_identifier",
})


class LazyTableCells:
    """Table geometry for a page, resolved at most once and only if asked.

    `find_tables()` is the most expensive call in the per-page path — on
    large-format MEP sheets it dominates the run — and the great majority of
    pages carry no candidate whose category can be confused by table layout.
    Resolving on first demand skips it entirely on those pages.
    """

    __slots__ = ("_page", "_enabled", "_cells")

    def __init__(self, page: fitz.Page, enabled: bool):
        self._page = page
        self._enabled = enabled
        self._cells: list[fitz.Rect] | None = None

    def get(self) -> list[fitz.Rect]:
        if not self._enabled:
            return []
        if self._cells is None:
            self._cells = table_cell_rects(self._page)
        return self._cells


def crossings(
    category: str, rects: Sequence[fitz.Rect], cells: LazyTableCells | None,
) -> int:
    """Cell crossings, computed only for categories table layout can fool."""
    if cells is None or category not in TABLE_FRAGILE_CATEGORIES:
        return 0
    return cells_spanned(rects, cells.get())


def containing_line(text: str, start: int, end: int) -> str:
    """The single line a match sits on, for context-level suppression."""
    left = text.rfind("\n", 0, start) + 1
    right = text.find("\n", end)
    return text[left: right if right != -1 else len(text)]


def table_cell_rects(page: fitz.Page) -> list[fitz.Rect]:
    """Cell geometry for every table MuPDF can find on the page.

    Used to spot a match that runs across several cells of one row — the
    signature of a regex reading a panel schedule as prose. Table detection
    is best-effort: a failure here must never stop a page.
    """
    try:
        finder = page.find_tables()
    except Exception:
        return []
    cells: list[fitz.Rect] = []
    for table in getattr(finder, "tables", ()):
        for cell in getattr(table, "cells", ()) or ():
            if cell:
                rect = fitz.Rect(cell)
                if not rect.is_empty:
                    cells.append(rect)
    return cells


def cells_spanned(rects: Sequence[fitz.Rect], cells: Sequence[fitz.Rect]) -> int:
    """How many distinct table cells a match touches.

    Counted across every rect of the match, not per rect: one match yields one
    rect per line it crosses, and in a schedule each of those lines is a
    separate cell, so a per-rect maximum is always 1.
    """
    if not cells or not rects:
        return 0
    touched: set[int] = set()
    for rect in rects:
        for index, cell in enumerate(cells):
            if index in touched:
                continue
            overlap = fitz.Rect(rect) & cell
            if not overlap.is_empty and overlap.width > 1.0 and overlap.height > 1.0:
                touched.add(index)
    return len(touched)


def candidate_suppression(
    category: str,
    text: str,
    lexicons: Lexicons | None,
    cells_crossed: int = 0,
    context: str | None = None,
) -> str | None:
    """The shared suppression decision for detection, the post-flatten sweep,
    and verification.

    All three must agree. Suppressing a match at detection time while the
    verifier still flags it would fail every run on its own suppressed noise —
    the detection/verification asymmetry the audit called out.

    A denylist match is never suppressed: it is the authoritative
    sensitive-term list, and a real project number ("CCR-21109") is shaped
    exactly like the catalog numbers the lexicons rule out.
    """
    if lexicons is None or category == "denylist":
        return None
    # OCR commonly reads an architectural feet/inches dimension such as
    # 6'-9" ST or 6"9 ST as the street-address span "9 ST". The quote before
    # the numeric span is decisive construction notation, not an address.
    if category == "street_address" and context and re.search(
        r"\b\d{1,3}\s*['\"]\s*[-–—]?\s*\d{1,3}\s*(?:ST|RD|DR|LN|CT|CIR|PL)\b",
        context, re.I,
    ):
        return "structural:feet_inches_dimension"
    # A match running across two or more cells of one row is the regex
    # stitching separate schedule cells into a phrase — "1 20 A OUTDOOR
    # CONCESSION PAD 24 21 BASKETBALL COURT" read as a street address. A
    # genuine address in a title block sits inside a single cell, so the
    # multi-cell test keeps it.
    if cells_crossed >= 2 and category in TABLE_FRAGILE_CATEGORIES:
        return f"table_zone:multi_cell_{category}"
    reason = lexicons.suppression_reason(text)
    if reason:
        return reason
    # Overlapping detectors otherwise defeat span-level suppression: suppress
    # the email in an AIA copyright notice and the url pattern immediately
    # claims the same rect, so the line is still destroyed under a different
    # label. If the surrounding line is boilerplate, nothing located inside it
    # is a project identifier — and a denylist term in that line is still
    # redacted, because denylist matches are never suppressed.
    if context:
        phrase = lexicons.boilerplate_context(context)
        if phrase:
            return f"boilerplate_context:{phrase}"
    return None


DEFAULT_LEXICON_DIR = "config/lexicons"
DEFAULT_ALLOWLIST = "config/allowlist.shared.json"

LEXICON_CONTRACT_DEFINED_TERMS_FILENAME = "contract_defined_terms.json"
LEXICON_STRUCTURAL_PATTERNS_FILENAME = "structural_patterns.json"
LEXICON_BOILERPLATE_FILENAME = "boilerplate.json"
LEXICON_FILENAMES = (
    LEXICON_CONTRACT_DEFINED_TERMS_FILENAME,
    LEXICON_STRUCTURAL_PATTERNS_FILENAME,
    LEXICON_BOILERPLATE_FILENAME,
)


@dataclass(frozen=True)
class Lexicons:
    """Project-independent vocabulary shared by every detector layer.

    Suppression here answers "is this span a construction-document artifact
    rather than an identifier?" — a question that is the same on every project.
    It applies to detector *candidates* only. A denylist match is never
    suppressed: the denylist is the authoritative sensitive-term list, and a
    real project number legitimately looks like a catalog number.
    """

    defined_terms: frozenset[str] = frozenset()
    defined_strip_chars: str = ""
    defined_strip_affixes: tuple[str, ...] = ()
    allowlist: frozenset[str] = frozenset()
    allow_strip_chars: str = ""
    allow_strip_suffixes: tuple[str, ...] = ()
    structural: tuple[tuple[str, "re.Pattern[str]"], ...] = ()
    boilerplate: tuple[str, ...] = ()
    manufacturer_triggers: tuple[str, ...] = ()
    trigger_window: int = 120
    reject_rules: dict = field(default_factory=dict)
    section_header_re: "re.Pattern[str] | None" = None
    part_header_re: "re.Pattern[str] | None" = None
    section_end_re: "re.Pattern[str] | None" = None
    party_divisions: frozenset[str] = frozenset()
    product_part: str = "2"

    # PDF text mixes typographic and ASCII punctuation for the same phrase, so
    # every lexicon comparison folds them together first.
    _PUNCT_FOLD = str.maketrans({
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "“": '"', "”": '"', "„": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "―": "-", "−": "-",
        " ": " ", " ": " ", " ": " ",
    })

    @classmethod
    def _fold(cls, value: str) -> str:
        return value.translate(cls._PUNCT_FOLD).casefold()

    @staticmethod
    def _trim(value: str, chars: str) -> str:
        return re.sub(r"\s+", " ", value).strip(chars or None).strip()

    def _defined_key(self, span: str) -> str:
        trimmed = self._trim(span, self.defined_strip_chars)
        folded = self._fold(trimmed)
        for affix in self.defined_strip_affixes:
            folded_affix = self._fold(affix)
            if folded.endswith(folded_affix) and len(folded) > len(folded_affix) + 1:
                return folded[: -len(folded_affix)].strip(" '\"-").strip()
        return folded

    def _allow_key(self, span: str) -> str:
        trimmed = self._trim(span, self.allow_strip_chars)
        for suffix in self.allow_strip_suffixes:
            pattern = rf"(?i)[,\s]+{re.escape(suffix)}\.?$"
            trimmed = re.sub(pattern, "", trimmed).strip()
        return self._fold(trimmed)

    def suppression_reason(self, span: str) -> str | None:
        """Why this candidate is a document artifact, or None to keep it.

        The single suppression entry point for the regex, label, and NER
        layers, so a term ruled out for one is ruled out for all and the
        reason is reportable.
        """
        trimmed = re.sub(r"\s+", " ", span).strip()
        if not trimmed:
            return "empty"
        if self.defined_terms and self._defined_key(trimmed) in self.defined_terms:
            return f"contract_defined_term:{self._defined_key(trimmed)}"
        if self.allowlist and self._allow_key(trimmed) in self.allowlist:
            return f"shared_allowlist:{self._allow_key(trimmed)}"
        bare = trimmed.strip(" \t.,:;-—–'’\"()[]{}*#|_")
        for name, pattern in self.structural:
            if pattern.fullmatch(trimmed) or (bare and pattern.fullmatch(bare)):
                return f"structural:{name}"
        folded = self._fold(trimmed)
        for phrase in self.boilerplate:
            if phrase in folded:
                return f"boilerplate:{phrase}"
        return None

    def boilerplate_context(self, context: str) -> str | None:
        """The boilerplate phrase this surrounding text belongs to, if any."""
        folded = self._fold(re.sub(r"\s+", " ", context).strip())
        for phrase in self.boilerplate:
            if phrase in folded:
                return phrase
        return None

    def has_manufacturer_context(self, text: str, start: int, end: int) -> str | None:
        """A trigger phrase near the span marks it as a product reference.
        Returned as evidence for down-weighting, never as suppression — a real
        subcontractor can be named beside 'or approved equal'."""
        if not self.manufacturer_triggers:
            return None
        window = self._fold(text[max(0, start - self.trigger_window): end + self.trigger_window])
        for phrase in self.manufacturer_triggers:
            if phrase in window:
                return phrase
        return None

    def rejects_proposed_term(self, term: str) -> str | None:
        """Why --derive-denylist must not propose this term, or None.

        Stricter than suppression_reason: a proposed term becomes a global
        destructive redaction rule, so prose, boilerplate, and extraction
        garbage have to be caught here rather than discovered in the output.
        """
        rules = self.reject_rules
        if not rules:
            return None
        trimmed = re.sub(r"\s+", " ", term).strip()
        if not trimmed:
            return "empty"
        for glyph in rules.get("reject_if_contains_glyph", ()):
            if glyph in trimmed:
                return f"glyph:{glyph}"

        # A term that already IS a deterministic identifier — a full street
        # address, a city/state/zip, an email, a CAD save-path — is exempt from
        # the length and prose heuristics. Real addresses run long and real
        # paths run longer; only the shape rules are skipped, never the
        # boilerplate, defined-term, or structural checks below.
        if not self._is_identifier_shaped(trimmed, rules):
            reason = self._shape_rejection(trimmed, rules)
            if reason:
                return reason

        if rules.get("reject_if_matches_structural_pattern") or rules.get(
            "reject_if_matches_contract_defined_term"
        ):
            reason = self.suppression_reason(trimmed)
            if reason:
                return reason
        return None

    @staticmethod
    def _is_identifier_shaped(term: str, rules: dict) -> bool:
        if rules.get("skip_shape_rules_if_path_like") and re.search(
            r"[A-Za-z]:\\|\\\\|(?:/[A-Za-z0-9_.-]+){2,}|\.[A-Za-z0-9]{2,4}$", term
        ):
            return True
        # A compact letters-then-digits code is identifier-shaped: project and
        # job numbers ("CCR-21109") are digit-heavy and would otherwise fail
        # the alpha-ratio rule, and missing a project number in a proposal
        # generator costs far more than proposing a model number a human
        # clears in a second.
        if rules.get("skip_shape_rules_if_alnum_code") and re.fullmatch(
            r"[A-Za-z]{2,6}\s*[-_]?\s*\d{3,8}[A-Za-z0-9 -]*", term
        ):
            return True
        if not rules.get("skip_shape_rules_if_identifier_pattern"):
            return False
        return any(
            DIRECT_PATTERNS[name].search(term)
            for name in ("street_address", "city_state_zip", "email", "url", "phone", "po_box")
            if name in DIRECT_PATTERNS
        )

    def _shape_rejection(self, trimmed: str, rules: dict) -> str | None:
        mojibake = rules.get("mojibake_glyphs", "")
        if mojibake:
            ratio = sum(ch in mojibake for ch in trimmed) / len(trimmed)
            if ratio > rules.get("max_mojibake_ratio", 1.0):
                return "mojibake"
        tokens = re.findall(r"[^\s]+", trimmed)
        # \w+ rather than [A-Za-z0-9]+: an ASCII-only class shatters a
        # mojibake-substituted word ("RecreaƟon" -> "Recrea", "on") and the
        # fragments read as function words, rejecting a real project name.
        alnum_tokens = re.findall(r"\w+", trimmed)
        if len(tokens) > rules.get("max_tokens", 99):
            return f"too_many_tokens:{len(tokens)}"
        if sum(ch.isalnum() for ch in trimmed) < rules.get("min_alnum_chars", 0):
            return "too_few_alnum_chars"
        letters = sum(ch.isalpha() for ch in trimmed)
        if letters / len(trimmed) < rules.get("min_alpha_ratio", 0.0):
            return "low_alpha_ratio"
        if rules.get("reject_if_repeated_inner_punctuation") and re.search(
            r"\w[._]{2,}\w", trimmed
        ):
            return "garbage:repeated_punctuation"
        if rules.get("reject_if_internal_case_flip"):
            for token in tokens:
                # ASCII-only, so a mojibake-substituted real name ("MarƟn
                # Luther King Jr.") still proposes; Mc/Mac/De/O' names are
                # legitimate internal capitals.
                bare = token.strip(".,:;'\"()[]-")
                if len(bare) < 4 or not bare.isascii() or not bare.isalpha():
                    continue
                if re.match(r"(?:Mc|Mac|De|Di|La|Le|Van|Von|O)[A-Z]", bare):
                    continue
                if re.search(r"[a-z][A-Z]", bare):
                    return "garbage:internal_case_flip"
        if rules.get("reject_if_lowercase_sentence_fragment") and trimmed.endswith("."):
            first = next((t for t in alnum_tokens if t[0].isalpha()), "")
            if first and first.islower():
                return "prose:sentence_fragment"
        function_words = {w.casefold() for w in rules.get("prose_function_words", ())}
        if function_words:
            found = sum(1 for t in alnum_tokens if t.casefold() in function_words)
            if found >= rules.get("max_function_words", 2):
                return "prose:function_words"
            if found and len(alnum_tokens) >= rules.get("prose_token_threshold", 6):
                return "prose:long_with_function_word"
        return None


def _compile_lexicon_pattern(name: str, raw: str) -> "re.Pattern[str]":
    try:
        return re.compile(raw, re.IGNORECASE)
    except re.error as exc:
        raise SystemExit(f"Lexicon pattern '{name}' is not a valid regular expression") from exc


def load_lexicons(
    directory: Path, allowlist_path: Path | None = None,
) -> Lexicons:
    """Load the shared lexicons. A missing directory yields empty lexicons —
    no suppression, which is the pre-lexicon behaviour and never less safe. A
    present-but-malformed file is fatal, because silently losing a suppression
    rule would be reported as a clean run."""
    if not directory.is_dir():
        return Lexicons()

    def read(name: str) -> dict:
        path = directory / name
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Lexicon file '{name}' is missing or invalid") from exc
        if not isinstance(payload, dict):
            raise SystemExit(f"Lexicon file '{name}' must contain an object")
        return payload

    defined = read(LEXICON_CONTRACT_DEFINED_TERMS_FILENAME)
    structural_payload = read(LEXICON_STRUCTURAL_PATTERNS_FILENAME)
    boiler = read(LEXICON_BOILERPLATE_FILENAME)

    defined_strip_chars = defined.get("strip_chars", "")
    defined_affixes = tuple(defined.get("strip_affixes", ()))
    scratch = Lexicons(
        defined_strip_chars=defined_strip_chars, defined_strip_affixes=defined_affixes,
    )
    defined_terms = frozenset(
        scratch._defined_key(term) for term in defined.get("terms", ()) if isinstance(term, str)
    )

    allow_terms: set[str] = set()
    allow_payload: dict = {}
    if allowlist_path and allowlist_path.is_file():
        try:
            allow_payload = json.loads(allowlist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit("The shared allowlist is missing or invalid") from exc
    allow_scratch = Lexicons(
        allow_strip_chars=allow_payload.get("strip_chars", ""),
        allow_strip_suffixes=tuple(allow_payload.get("strip_suffixes", ())),
    )
    for entry in allow_payload.get("entries", ()):
        if not isinstance(entry, dict):
            continue
        for key, values in entry.items():
            if key in {"confirmed_on", "source", "notes"} or not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value.strip():
                    allow_terms.add(allow_scratch._allow_key(value))

    structural = tuple(
        (item["name"], _compile_lexicon_pattern(item["name"], item["pattern"]))
        for item in structural_payload.get("suppress_if_span_matches", ())
        if isinstance(item, dict) and item.get("name") and item.get("pattern")
    )
    triggers_block = structural_payload.get("manufacturer_context_triggers", {})
    zone = structural_payload.get("csi_zone_markers", {})

    def zone_pattern(key: str) -> "re.Pattern[str] | None":
        raw = zone.get(key)
        return _compile_lexicon_pattern(key, raw) if isinstance(raw, str) else None

    return Lexicons(
        defined_terms=defined_terms,
        defined_strip_chars=defined_strip_chars,
        defined_strip_affixes=defined_affixes,
        allowlist=frozenset(allow_terms),
        allow_strip_chars=allow_payload.get("strip_chars", ""),
        allow_strip_suffixes=tuple(allow_payload.get("strip_suffixes", ())),
        structural=structural,
        boilerplate=tuple(
            Lexicons._fold(phrase)
            for phrase in boiler.get("phrases", ())
            if isinstance(phrase, str) and phrase.strip()
        ),
        manufacturer_triggers=tuple(
            Lexicons._fold(phrase)
            for phrase in triggers_block.get("phrases", ())
            if isinstance(phrase, str) and phrase.strip()
        ),
        trigger_window=int(triggers_block.get("window_chars", 120)),
        reject_rules=boiler.get("reject_proposed_term_if", {}),
        section_header_re=zone_pattern("section_header"),
        part_header_re=zone_pattern("part_header"),
        section_end_re=zone_pattern("section_end"),
        party_divisions=frozenset(zone.get("party_divisions", ())),
        product_part=str(zone.get("product_part", "2")),
    )


DEFAULT_NER_LABELS: tuple[str, ...] = (
    "person name", "company name", "organization",
    "project name", "street address", "city",
)


@dataclass(frozen=True)
class NerSettings:
    """Report-only NER review layer. Findings are triage candidates for the
    human reviewer; they never change the automated verdict."""

    enabled: bool = False
    model_dir: str = "models/gliner_multi_pii-v1"
    labels: tuple[str, ...] = DEFAULT_NER_LABELS
    threshold: float = 0.5
    max_findings: int = 500


@dataclass(frozen=True)
class ResourceLimits:
    """Process-wide ceilings for one CLI invocation (Phase 5 operational scope).

    RLIMIT_AS/RLIMIT_CPU are process-wide, not per-document, because the whole
    run's document batch shares one Python process (see orchestrate_run). The
    disk ceiling has no OS-level rlimit equivalent for a directory, so it's
    enforced by explicit polling instead.
    """

    max_memory_bytes: int = 4 * 1024**3
    max_cpu_seconds: int = 3600
    max_staging_disk_bytes: int = 8 * 1024**3
    resource_check_every_pages: int = 25
    retention_days: int = 30


@dataclass
class Settings:
    ocr_dpi: int = 300
    verification_ocr_dpi: int = 300
    barcode_dpi: int = 120
    barcode_max_dimension: int = 2400
    min_vector_text_chars: int = 20
    raster_image_area_ratio: float = 0.02
    progress_every_pages: int = 100
    tesseract_executable: str = "tesseract"
    ghostscript_executable: str = "gs"
    detect_barcodes: bool = True
    redact_repeated_margin_images: bool = True
    repeated_image_min_pages: int = 3
    regions: list[Region] = field(default_factory=list)
    ner: NerSettings = field(default_factory=NerSettings)
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)


class DenylistMatcher:
    """One compiled search for every local denylist phrase."""

    # Hyphen-like characters, including the Unicode dash block.
    _DASHES = r"\-‐-―−"
    _SPLIT = re.compile(rf"([\s{_DASHES}]+)")

    @classmethod
    def _term_pattern(cls, term: str) -> str:
        """One term, tolerant of how PDF text actually renders it.

        The same identifier appears as "CCR-21109", "CCR - 21109", and
        "CCR\\n21109" across one document. Where the term has a hyphen the
        separator is optional, so all three match. Where the term has a space
        at least one separator is still required, so "Owner Holdings" never
        matches "OwnerHoldings" — a run-together match is a different string,
        not a rendering variant.
        """
        pieces: list[str] = []
        for index, chunk in enumerate(cls._SPLIT.split(term)):
            if not chunk:
                continue
            if index % 2:
                dashed = any(ch in "-‐‑‒–—―−" for ch in chunk)
                pieces.append(rf"[\s{cls._DASHES}]{'*' if dashed else '+'}")
            elif chunk.endswith(".") and len(chunk) > 1:
                # A trailing abbreviation period is optional: title blocks
                # write both "King Jr. Recreation" and "KING JR RECREATION".
                pieces.append(re.escape(chunk[:-1]) + r"\.?")
            else:
                pieces.append(re.escape(chunk))
        return "".join(pieces)

    def __init__(self, terms: Iterable[str]):
        self.terms = tuple(sorted(set(terms), key=lambda value: (-len(value), value.casefold())))
        alternatives = "|".join(self._term_pattern(term) for term in self.terms)
        self.pattern = re.compile(
            rf"(?i)(?<![A-Z0-9])(?:{alternatives})(?![A-Z0-9])"
        )

    def finditer(self, text: str):
        return self.pattern.finditer(text)

    def count(self, text: str) -> int:
        return sum(1 for _ in self.finditer(text))


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t:;,-")


def masked_shape(value: str, limit: int = 80) -> str:
    """Audit masking convention: uppercase→A, lowercase→a, digit→9. No
    original letter or digit survives; punctuation and layout are kept."""
    masked = "".join(
        "A" if ch.isalpha() and ch.isupper()
        else "a" if ch.isalpha()
        else "9" if ch.isdigit()
        else ch
        for ch in value
    )
    return masked if len(masked) <= limit else masked[:limit] + "…"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def keyed_digest(key: bytes, value: str) -> str:
    """HMAC-SHA256 of value under a per-run key. Correlates repeated
    occurrences of the same value within one run's report without being
    reproducible across runs or reversible without the key."""
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


# Environment variables git uses to pin discovery to a specific repository,
# overriding the normal cwd-based upward search. A caller running under a
# git hook (e.g. this project's own pre-push hook, invoked from a linked
# worktree) inherits these pointing at *that* repository; left in place,
# they'd redirect code_identity()'s git calls away from repo_root to
# whichever repo the ambient hook was invoked for.
_GIT_DISCOVERY_OVERRIDE_VARS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
)


def _hermetic_git_env() -> dict[str, str]:
    """A copy of the current environment safe to hand to a git subprocess
    that must discover its repository strictly from the given cwd."""
    return {
        key: value for key, value in os.environ.items()
        if key not in _GIT_DISCOVERY_OVERRIDE_VARS
    }


def code_identity(script_path: Path, repo_root: Path, *, timeout: float = 5.0) -> dict:
    """Identify the exact code that ran. build_digest_sha256 is the ground
    truth (always present, catches uncommitted edits); commit/commit_dirty are
    best-effort human-readable provenance, populated only when git is
    available. Never reads or returns file content, only hashes and git
    identifiers."""
    script_path = script_path.resolve()
    build_digest = sha256_file(script_path)
    commit: str | None = None
    dirty: bool | None = None
    env = _hermetic_git_env()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=repo_root, env=env,
            capture_output=True, text=True, timeout=timeout,
        )
        if (
            head.returncode == 0 and toplevel.returncode == 0
            and Path(toplevel.stdout.strip()).resolve() == repo_root.resolve()
        ):
            commit = head.stdout.strip()
            diff = subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--", str(script_path)],
                cwd=repo_root, env=env, timeout=timeout,
            )
            # Anything other than a clean 0/1 (git error, ambiguous pathspec)
            # is unknown, not "dirty".
            dirty = {0: False, 1: True}.get(diff.returncode)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"commit": commit, "commit_dirty": dirty, "build_digest_sha256": build_digest}


def build_fingerprint(
    *, script_path: Path, repo_root: Path, denylist_path: Path | None,
    project_metadata_path: Path | None, config_path: Path,
    allowlist_path: Path | None, lexicon_dir: Path,
) -> dict:
    """Hashes of the exact code, denylist, project metadata, config,
    allowlist, and lexicons used to produce a report. Never reads a file's
    content into the return value or a log, only its hash."""

    def hash_if_present(path: Path | None) -> str | None:
        return sha256_file(path) if path and path.is_file() else None

    return {
        "code": code_identity(script_path, repo_root),
        "denylist_sha256": hash_if_present(denylist_path),
        "project_metadata_sha256": hash_if_present(project_metadata_path),
        "config_sha256": sha256_file(config_path),
        "allowlist_sha256": hash_if_present(allowlist_path),
        "lexicon_sha256": {
            name: sha256_file(lexicon_dir / name)
            for name in LEXICON_FILENAMES
            if (lexicon_dir / name).is_file()
        },
    }


def redactable_phrase(value: str, *, allow_short: bool = False) -> bool:
    value = normalized(value)
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    minimum = 2 if allow_short else 4
    if len(value) < minimum or len(value) > 180 or not tokens:
        return False
    if not allow_short and len(tokens) == 1 and len(tokens[0]) < 7:
        return False
    return any(ch.isalpha() for ch in value) or (allow_short and any(ch.isdigit() for ch in value))


def filename_phrase(path: Path) -> str | None:
    tokens = [t for t in re.split(r"[-_\s]+", path.stem) if t]
    kept = [t for t in tokens if t.casefold() not in SAFE_FILENAME_WORDS]
    phrase = normalized(" ".join(kept))
    return phrase if redactable_phrase(phrase) else None


def lines_from_page(page: fitz.Page) -> list[Line]:
    groups: dict[tuple[int, int], list[tuple]] = collections.defaultdict(list)
    # Content-stream word order preserves the reading direction of rotated text.
    # Coordinate sorting reverses many 90-degree title-block lines.
    for word in page.get_text("words", sort=False):
        groups[(int(word[5]), int(word[6]))].append(word)
    result: list[Line] = []
    for (block, line_no), words in groups.items():
        text = " ".join(str(word[4]) for word in words)
        rect = fitz.Rect(words[0][:4])
        for word in words[1:]:
            rect.include_rect(fitz.Rect(word[:4]))
        result.append(Line(block, line_no, text, tuple(words), rect))
    result.sort(key=lambda line: (line.rect.y0, line.rect.x0))
    return result


@dataclass(frozen=True)
class TextBlock:
    """Lines of one layout block joined with newlines, plus the character
    span each line occupies, so cross-line matches map back to rectangles."""

    text: str
    segments: tuple[tuple[int, int, Line], ...]


def text_blocks(lines: Sequence[Line]) -> list[TextBlock]:
    grouped: dict[int, list[Line]] = collections.defaultdict(list)
    for line in lines:
        grouped[line.block].append(line)
    blocks: list[TextBlock] = []
    for _, block_lines in sorted(grouped.items()):
        block_lines.sort(key=lambda line: line.line)
        segments: list[tuple[int, int, Line]] = []
        cursor = 0
        for line in block_lines:
            segments.append((cursor, cursor + len(line.text), line))
            cursor += len(line.text) + 1
        blocks.append(TextBlock("\n".join(line.text for line in block_lines), tuple(segments)))
    return blocks


def rects_for_block_span(block: TextBlock, start: int, end: int) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    for segment_start, segment_end, line in block.segments:
        if segment_end <= start or segment_start >= end:
            continue
        rects.append(rect_for_span(
            line, max(0, start - segment_start), min(len(line.text), end - segment_start),
        ))
    return rects


def page_text_block(lines: Sequence[Line]) -> TextBlock:
    """One TextBlock spanning every line on the page, in MuPDF reading order.

    Ordered by (block, line) — the content-stream order `get_text()` itself
    uses — NOT by the (y0, x0) geometry order `lines_from_page` returns.
    Geometric sorting interleaves unrelated lines on multi-column and rotated
    layouts: on a drawing title block it put "CCR ARCHITECTURE &" next to
    "AS BEING NECESSARY TO PRODUCE" instead of "INTERIORS", so the firm name
    was invisible to a page-level scan even though `get_text()` shows the two
    halves adjacent.
    """
    ordered = sorted(lines, key=lambda line: (line.block, line.line))
    segments: list[tuple[int, int, Line]] = []
    cursor = 0
    for line in ordered:
        segments.append((cursor, cursor + len(line.text), line))
        cursor += len(line.text) + 1
    return TextBlock("\n".join(line.text for line in ordered), tuple(segments))


def block_matches(
    blocks: Sequence[TextBlock],
    denylist: DenylistMatcher,
    page_block: TextBlock | None = None,
) -> Iterator[tuple[str, TextBlock, "re.Match[str]"]]:
    """Every direct-pattern and denylist match. The single match source shared
    by detection, the post-flatten sweep, and verification, so all three
    always see the same text stream.

    The two detector families get different scopes on purpose:

    Direct patterns stay block-scoped. They are shape heuristics, and letting
    them span blocks stitches unrelated fragments into phantom addresses —
    the false-positive class the cross-block regression test pins down.

    The denylist scans the whole page when `page_block` is supplied. A
    denylist term is an exact string already established as sensitive, so
    matching it across a block boundary carries none of that risk. Without
    this, a title block that splits "CCR Architecture &" from "Interiors"
    into separate blocks hides the architect's name from detection AND from
    verification, and the run reports PASS with the name still on the page.
    """
    for block in blocks:
        for category, pattern in DIRECT_PATTERNS.items():
            for match in pattern.finditer(block.text):
                yield category, block, match
        if page_block is None:
            for match in denylist.finditer(block.text):
                yield "denylist", block, match
    if page_block is not None:
        for match in denylist.finditer(page_block.text):
            yield "denylist", page_block, match


def following_value_lines(lines: Sequence[Line], index: int) -> Iterator[Line]:
    """Value lines that belong to a label-only line. Shared by denylist
    derivation and detection so both see the same label/value pairs."""
    label_line = lines[index]
    label_width = max(1.0, label_line.rect.width)
    for following in lines[index + 1:]:
        if following.rect.y0 - label_line.rect.y1 > 120:
            break
        aligned = (
            abs(following.rect.x0 - label_line.rect.x0) <= max(36.0, label_width * 0.5)
            or not (following.rect & label_line.rect).is_empty
        )
        if not aligned:
            continue
        candidate = normalized(following.text)
        if (
            redactable_phrase(candidate, allow_short=True)
            and len(candidate.split()) <= 10
            and not LABEL_RE.match(candidate)
        ):
            yield following


def rect_for_span(line: Line, start: int, end: int) -> fitz.Rect:
    cursor = 0
    selected: list[fitz.Rect] = []
    for index, word in enumerate(line.words):
        token = str(word[4])
        token_end = cursor + len(token)
        if token_end > start and cursor < end:
            selected.append(fitz.Rect(word[:4]))
        cursor = token_end + (1 if index < len(line.words) - 1 else 0)
    if not selected:
        return fitz.Rect(line.rect)
    rect = selected[0]
    for other in selected[1:]:
        rect.include_rect(other)
    return rect


def padded(rect: fitz.Rect, page_rect: fitz.Rect, points: float = 1.5) -> fitz.Rect:
    return fitz.Rect(rect.x0 - points, rect.y0 - points, rect.x1 + points, rect.y1 + points) & page_rect


def load_settings(path: Path) -> Settings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("Sanitizer configuration could not be read") from exc
    settings = Settings(
        ocr_dpi=int(raw.get("ocr_dpi", 300)),
        verification_ocr_dpi=int(raw.get("verification_ocr_dpi", 300)),
        barcode_dpi=int(raw.get("barcode_dpi", 120)),
        barcode_max_dimension=int(raw.get("barcode_max_dimension", 2400)),
        min_vector_text_chars=int(raw.get("min_vector_text_chars", 20)),
        raster_image_area_ratio=float(raw.get("raster_image_area_ratio", 0.02)),
        progress_every_pages=int(raw.get("progress_every_pages", 100)),
        tesseract_executable=str(raw.get("tesseract_executable", "tesseract")),
        ghostscript_executable=str(raw.get("ghostscript_executable", "gs")),
        detect_barcodes=bool(raw.get("detect_barcodes", True)),
        redact_repeated_margin_images=bool(raw.get("redact_repeated_margin_images", True)),
        repeated_image_min_pages=int(raw.get("repeated_image_min_pages", 3)),
    )
    if (
        not 150 <= settings.ocr_dpi <= 600
        or not 150 <= settings.verification_ocr_dpi <= 600
        or not 72 <= settings.barcode_dpi <= 300
        or not 1200 <= settings.barcode_max_dimension <= 6000
    ):
        raise SystemExit("Configured raster resolution is outside the safe supported range")
    ner_raw = raw.get("ner", {})
    if not isinstance(ner_raw, dict):
        raise SystemExit("Sanitizer configuration could not be read")
    ner_labels = tuple(
        normalized(str(value)) for value in ner_raw.get("labels", DEFAULT_NER_LABELS)
        if normalized(str(value))
    )
    settings.ner = NerSettings(
        enabled=bool(ner_raw.get("enabled", False)),
        model_dir=str(ner_raw.get("model_dir", NerSettings.model_dir)),
        labels=ner_labels or DEFAULT_NER_LABELS,
        threshold=float(ner_raw.get("threshold", NerSettings.threshold)),
        max_findings=int(ner_raw.get("max_findings", NerSettings.max_findings)),
    )
    if not 0.05 <= settings.ner.threshold <= 1.0 or not 1 <= settings.ner.max_findings <= 10000:
        raise SystemExit("Configured NER review settings are outside the supported range")
    limits_raw = raw.get("resource_limits", {})
    if not isinstance(limits_raw, dict):
        raise SystemExit("Sanitizer configuration could not be read")
    settings.resource_limits = ResourceLimits(
        max_memory_bytes=int(limits_raw.get("max_memory_bytes", ResourceLimits.max_memory_bytes)),
        max_cpu_seconds=int(limits_raw.get("max_cpu_seconds", ResourceLimits.max_cpu_seconds)),
        max_staging_disk_bytes=int(
            limits_raw.get("max_staging_disk_bytes", ResourceLimits.max_staging_disk_bytes)
        ),
        resource_check_every_pages=int(
            limits_raw.get("resource_check_every_pages", ResourceLimits.resource_check_every_pages)
        ),
        retention_days=int(limits_raw.get("retention_days", ResourceLimits.retention_days)),
    )
    if (
        settings.resource_limits.max_memory_bytes <= 0
        or settings.resource_limits.max_cpu_seconds <= 0
        or settings.resource_limits.max_staging_disk_bytes <= 0
        or settings.resource_limits.resource_check_every_pages <= 0
        or settings.resource_limits.retention_days <= 0
    ):
        raise SystemExit("Configured resource limits are outside the supported range")
    for item in raw.get("regions", []):
        if not item.get("enabled", True):
            continue
        rect = tuple(float(value) for value in item["rect"])
        if len(rect) != 4 or not (0 <= rect[0] < rect[2] <= 1 and 0 <= rect[1] < rect[3] <= 1):
            raise SystemExit("A configured rectangle is invalid")
        pages_raw = item.get("pages", "all")
        pages: str | tuple[int, ...] = "all" if pages_raw == "all" else tuple(int(p) for p in pages_raw)
        settings.regions.append(Region(
            name=str(item.get("name", "configured-region")),
            category=str(item.get("category", "configured_region")),
            rect=rect, pages=pages,
        ))
    return settings


def load_denylist(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("The local denylist is missing or invalid; derive or supply it before processing") from exc
    values: set[str] = set()
    for entries in payload.values():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, str) and redactable_phrase(entry, allow_short=True):
                    values.add(normalized(entry))
    if not values:
        raise SystemExit("The local denylist contains no usable terms")
    return values


PROJECT_METADATA_FIELDS: tuple[str, ...] = (
    "project_name", "project_number", "project_address", "site_address",
    "owner", "architect", "engineers", "contractors", "consultants",
    "personnel", "other_identifiers",
)


def load_project_metadata(path: Path) -> set[str]:
    """Seed the denylist from what the project team already knows.

    The primary seeding path. Project name, number, address, and party names
    are known before the PDF is ever opened — they are in the contract and the
    PM system — and taking them from a form is both more accurate and more
    complete than scraping them back out of the document.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("The project metadata file is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise SystemExit("The project metadata file must contain an object")
    terms: set[str] = set()
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        values = value if isinstance(value, list) else [value]
        for entry in values:
            if isinstance(entry, str) and redactable_phrase(entry, allow_short=True):
                terms.add(normalized(entry))
    if not terms:
        raise SystemExit("The project metadata file yielded no usable identifiers")
    return terms


def derive_term_candidates(
    doc: fitz.Document, source: Path, lexicons: Lexicons,
) -> dict[str, dict]:
    """Propose denylist candidates found in the document, with provenance.

    Proposals only. Writing these straight into a live denylist is what
    produced 160 terms of which 136 were boilerplate, prose, product names, or
    extraction garbage — each one a global destructive redaction rule. Every
    candidate is filtered through the shared lexicons and carries the reason
    it was proposed plus its occurrence count, so a human can confirm the list
    in one pass.
    """
    proposals: dict[str, dict] = {}

    def propose(value: str, why: str) -> None:
        term = normalized(value)
        # "Architect: Cohen Carnaggio Reynolds, Inc." only ever matches where
        # the label shares a line with the value, so it silently misses every
        # other occurrence of the firm. Propose the value alone. The trailing
        # space after the colon is required so a Windows path ("C:\Users\...")
        # is never mistaken for a labelled value.
        for _ in range(2):
            matched = LABEL_VALUE_RE.match(term) or re.match(
                r"^[A-Za-z][A-Za-z/&'. -]{1,38}:\s+(\S.*)$", term
            )
            if not matched:
                break
            stripped = normalized(matched.group(1))
            if not redactable_phrase(stripped, allow_short=True):
                break
            term = stripped
        if not redactable_phrase(term, allow_short=True):
            return
        if len(re.findall(r"\w+", term)) < 2 or sum(ch.isalnum() for ch in term) < 7:
            return
        rejected = lexicons.rejects_proposed_term(term)
        if rejected:
            return
        record = proposals.setdefault(term, {"reasons": set(), "occurrences": 0})
        record["reasons"].add(why)
        record["occurrences"] += 1

    name_term = filename_phrase(source)
    if name_term:
        propose(name_term, "source_filename")
    for page in doc:
        lines = lines_from_page(page)
        for index, line in enumerate(lines):
            text = normalized(line.text)
            matched = LABEL_VALUE_RE.match(text)
            if matched:
                # Propose the value alone, never "Architect: <firm>". The
                # compound only ever matches where the label happens to sit on
                # the same line, so it silently misses every other occurrence.
                propose(matched.group(1), "labelled_value")
            elif LABEL_RE.match(text):
                for following in following_value_lines(lines, index):
                    propose(following.text, "label_following_line")
            organization_marker = re.search(
                r"(?i)\b(?:LLC|LLP|LP|Inc\.?|Ltd\.?|Corporation|Company|Associates|"
                r"Architects|Engineers|Consultants|Studio|Group)\b", text,
            )
            if FIRM_RE.search(text) and organization_marker and len(text.split()) <= 10:
                propose(text, "firm_name_pattern")
    return {
        term: {"reasons": sorted(record["reasons"]), "occurrences": record["occurrences"]}
        for term, record in proposals.items()
    }


def write_denylist_candidates(
    sources: Sequence[Path], path: Path, lexicons: Lexicons,
) -> int:
    """Write proposals for human confirmation. Never writes a live denylist."""
    merged: dict[str, dict] = {}
    for source in sources:
        try:
            doc = fitz.open(source)
            found = derive_term_candidates(doc, source, lexicons)
            doc.close()
        except Exception as exc:
            raise SystemExit(
                "A source document could not be inspected while proposing denylist candidates"
            ) from exc
        for term, record in found.items():
            existing = merged.setdefault(term, {"reasons": [], "occurrences": 0})
            existing["reasons"] = sorted(set(existing["reasons"]) | set(record["reasons"]))
            existing["occurrences"] += record["occurrences"]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_about": {
            "status": "UNCONFIRMED CANDIDATES - not used by any run",
            "instructions": (
                "Review each candidate and copy the genuine project identifiers into the "
                "'terms' array of the denylist. Prefer seeding the denylist from project "
                "intake metadata (--project-metadata); this scrape only catches what the "
                "form missed."
            ),
            "candidate_count": len(merged),
        },
        "candidates": [
            {"term": term, **merged[term]}
            for term in sorted(merged, key=str.casefold)
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return len(merged)


def repeated_margin_images(
    doc: fitz.Document, minimum_pages: int, complexity_threshold: int = 1000,
) -> tuple[dict[int, list[fitz.Rect]], set[int]]:
    placements: dict[int, list[tuple[int, fitz.Rect, float]]] = collections.defaultdict(list)
    complex_pages: set[int] = set()
    for page_index, page in enumerate(doc):
        page_area = page.rect.width * page.rect.height
        seen: set[tuple[int, tuple[float, float, float, float]]] = set()
        # get_image_info resolves every displayed image in one page traversal;
        # get_images + get_image_rects repeatedly walks nested form resources.
        image_info = page.get_image_info(hashes=False, xrefs=True)
        if len(image_info) > complexity_threshold:
            complex_pages.add(page_index + 1)
            continue
        for info in image_info:
            xref = int(info.get("xref", 0))
            if not xref:
                continue
            rect = fitz.Rect(info["bbox"])
            key = (xref, tuple(round(value, 2) for value in rect))
            if key in seen:
                continue
            seen.add(key)
            ratio = rect.width * rect.height / page_area if page_area else 1.0
            placements[xref].append((page_index, rect, ratio))
    result: dict[int, list[fitz.Rect]] = collections.defaultdict(list)
    for items in placements.values():
        if len({item[0] for item in items}) < minimum_pages:
            continue
        for page_index, rect, ratio in items:
            page_rect = doc[page_index].rect
            in_margin = (
                rect.y1 <= page_rect.height * 0.20
                or rect.y0 >= page_rect.height * 0.80
                or rect.x0 >= page_rect.width * 0.80
            )
            if in_margin and 0.00001 <= ratio <= 0.06:
                result[page_index].append(rect)
    return result, complex_pages


def page_raster_ratio(page: fitz.Page) -> float:
    """Fraction of the page area covered by displayed raster images."""
    page_area = page.rect.width * page.rect.height
    if not page_area:
        return 0.0
    # Same one-traversal idiom repeated_margin_images() uses, rather than
    # get_images()/get_image_rects() repeatedly walking nested form resources.
    covered = 0.0
    for info in page.get_image_info(hashes=False, xrefs=True):
        rect = fitz.Rect(info["bbox"])
        covered += rect.width * rect.height
    return covered / page_area


def page_needs_raster_pass(text_length: int, raster_ratio: float, settings: Settings) -> bool:
    """Replaces the flat min_text_chars threshold. True if the page has too
    little vector text to trust on its own, OR carries enough embedded
    raster-image area that sensitive content could be hiding in pixels the
    vector path never inspects — the mixed-page case a flat character count
    always missed."""
    return (
        text_length < settings.min_vector_text_chars
        or raster_ratio >= settings.raster_image_area_ratio
    )


def configured_page_rects(settings: Settings, page: fitz.Page, page_number: int) -> list[tuple[fitz.Rect, str]]:
    result: list[tuple[fitz.Rect, str]] = []
    for region in settings.regions:
        if region.applies(page_number):
            x0, y0, x1, y1 = region.rect
            result.append((fitz.Rect(
                page.rect.x0 + page.rect.width * x0,
                page.rect.y0 + page.rect.height * y0,
                page.rect.x0 + page.rect.width * x1,
                page.rect.y0 + page.rect.height * y1,
            ), region.category))
    return result


def line_detections(
    lines: Sequence[Line],
    denylist: DenylistMatcher,
    lexicons: Lexicons | None = None,
    page_number: int = 0,
    zone: str | None = None,
    cells: LazyTableCells | None = None,
) -> list[Detection]:
    detections: list[Detection] = []
    blocks = text_blocks(lines)
    page_block = page_text_block(lines)
    # Direct patterns and the denylist scan block-level text so identifiers
    # split across a line break (label/value pairs, a name with its
    # credential on the next line, wrapped addresses) stay visible. The
    # verifier consumes the identical block_matches stream on the output.
    for category, block, match in block_matches(blocks, denylist, page_block):
        matched = match.group()
        rects = rects_for_block_span(block, match.start(), match.end())
        crossed = crossings(category, rects, cells)
        suppressed = candidate_suppression(
            category, matched, lexicons, crossed,
            containing_line(block.text, match.start(), match.end()),
        )
        evidence: tuple[str, ...] = ()
        if lexicons is not None:
            trigger = lexicons.has_manufacturer_context(block.text, match.start(), match.end())
            if trigger:
                evidence = (f"manufacturer_trigger:{trigger}",)
        for rect in rects:
            detections.append(Detection(
                rect=rect, category=category,
                detector="denylist" if category == "denylist" else "pattern",
                page=page_number, span=(match.start(), match.end()), text=matched,
                zone=zone, evidence=evidence, suppressed_by=suppressed,
            ))
    for index, line in enumerate(lines):
        label_match = LABEL_VALUE_RE.match(line.text)
        if label_match:
            value = label_match.group(1)
            detections.append(Detection(
                rect=rect_for_span(line, label_match.start(1), label_match.end(1)),
                category="labelled_identifier", detector="label", page=page_number,
                span=(label_match.start(1), label_match.end(1)), text=value, zone=zone,
                suppressed_by=candidate_suppression(
                    "labelled_identifier", value, lexicons, context=line.text,
                ),
            ))
        elif LABEL_RE.match(normalized(line.text)):
            # A label alone on its line labels the following lines; redact
            # them with the same rules the denylist derivation trusts.
            for following in following_value_lines(lines, index):
                detections.append(Detection(
                    rect=fitz.Rect(following.rect), category="labelled_identifier",
                    detector="label", page=page_number, text=following.text, zone=zone,
                    suppressed_by=candidate_suppression(
                        "labelled_identifier", following.text, lexicons,
                        context=following.text,
                    ),
                ))
    return detections


def csi_zone_for_page(text: str, lexicons: Lexicons | None, carried: str | None) -> str | None:
    """Track CSI Division/Section/Part down the document.

    In CSI three-part format, PART 2 - PRODUCTS is definitionally where
    manufacturers are named, and Division 00 (Procurement and Contracting
    Requirements) is where the real parties are. The zone travels with every
    finding so a reviewer can sort a firm named in a product spec away from
    one named in the contract front matter. It is evidence, never suppression:
    a genuine subcontractor can be named in PART 2.
    """
    if lexicons is None or lexicons.section_header_re is None:
        return carried
    division, part = (carried.split("/") + [""])[:2] if carried else ("", "")
    for line in text.splitlines():
        section = lexicons.section_header_re.match(line)
        if section:
            digits = re.sub(r"\D", "", section.group(1))
            division = digits[:2] if len(digits) >= 2 else division
            part = ""
            continue
        if lexicons.part_header_re is not None:
            part_match = lexicons.part_header_re.match(line)
            if part_match:
                part = part_match.group(2).upper()
    if not division and not part:
        return carried
    if division in lexicons.party_divisions:
        return f"{division}/{part}|contracting"
    if part == "PRODUCTS":
        return f"{division}/{part}|products"
    return f"{division}/{part}"


def geographic_rects(page: fitz.Page, lines: Sequence[Line]) -> list[tuple[fitz.Rect, str]]:
    results: list[tuple[fitz.Rect, str]] = []
    map_lines = [line for line in lines if MAP_LABEL_RE.search(line.text)]
    if not map_lines:
        return results
    image_rects = [fitz.Rect(info["bbox"]) for info in page.get_image_info(hashes=False, xrefs=False)]
    for line in map_lines:
        nearby = [rect for rect in image_rects if (rect | line.rect).get_area() <= page.rect.get_area() * 0.45]
        if nearby:
            chosen = min(nearby, key=lambda rect: (rect | line.rect).get_area())
            results.append((chosen | line.rect, "geographic_map"))
        else:
            width = page.rect.width * 0.38
            height = page.rect.height * 0.38
            cx = (line.rect.x0 + line.rect.x1) / 2
            cy = (line.rect.y0 + line.rect.y1) / 2
            results.append((fitz.Rect(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2) & page.rect, "geographic_map"))
    return results


def page_image(page: fitz.Page, dpi: int, max_dimension: int | None = None) -> tuple[Image.Image, float]:
    scale = dpi / 72.0
    if max_dimension:
        largest = max(page.rect.width, page.rect.height) * scale
        if largest > max_dimension:
            scale *= max_dimension / largest
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), scale


def barcode_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    if zxingcpp is None:
        raise RuntimeError("local barcode decoder is unavailable")
    boxes: list[tuple[int, int, int, int]] = []
    # Grayscale plus a single native-resolution pass is substantially faster
    # on dense specification pages. Page rendering supplies the target scale,
    # so ZXing's recursive downscale passes are unnecessary.
    for result in zxingcpp.read_barcodes(
        image.convert("L"), try_rotate=True, try_downscale=False,
    ):
        position = result.position
        points = [position.top_left, position.top_right, position.bottom_right, position.bottom_left]
        xs = [int(point.x) for point in points]
        ys = [int(point.y) for point in points]
        boxes.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1))
    return boxes


def page_barcode_rects(page: fitz.Page, settings: Settings) -> list[tuple[fitz.Rect, str]]:
    if not settings.detect_barcodes:
        return []
    try:
        image, scale = page_image(page, settings.barcode_dpi, settings.barcode_max_dimension)
        boxes = barcode_boxes(image)
    except Exception as exc:
        raise PageProcessingError(page.number + 1, "barcode inspection failed") from exc
    return [(fitz.Rect(x0 / scale, y0 / scale, x1 / scale, y1 / scale), "barcode_or_qr") for x0, y0, x1, y1 in boxes]


def run_tesseract_tsv(image: Image.Image, settings: Settings, temp_dir: Path) -> list[OcrWord]:
    executable = shutil.which(settings.tesseract_executable)
    if executable is None:
        raise RuntimeError("local OCR executable is unavailable")
    image_path = temp_dir / "page_image.png"
    image.save(image_path, format="PNG", dpi=(settings.ocr_dpi, settings.ocr_dpi))
    try:
        completed = subprocess.run(
            [executable, str(image_path), "stdout", "-l", "eng", "--psm", "11", "tsv"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            timeout=TESSERACT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"local OCR exceeded {TESSERACT_TIMEOUT_SECONDS}s timeout") from exc
    if completed.returncode != 0:
        raise RuntimeError("local OCR failed")
    words: list[OcrWord] = []
    try:
        # Tesseract TSV is delimiter-separated but does not CSV-escape OCR text.
        # A recognized quote glyph must remain a literal one-character field,
        # not open a quoted field that consumes subsequent rows.
        reader = csv.DictReader(
            io.StringIO(completed.stdout.decode("utf-8", errors="replace")),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )
        for row in reader:
            text = normalized(row.get("text", ""))
            if not text:
                continue
            words.append(OcrWord(
                text=text,
                left=int(row["left"]), top=int(row["top"]), width=int(row["width"]), height=int(row["height"]),
                block=int(row["block_num"]), paragraph=int(row["par_num"]), line=int(row["line_num"]),
            ))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("local OCR returned malformed coordinates") from exc
    return words


def ocr_lines(words: Sequence[OcrWord]) -> list[tuple[str, list[OcrWord]]]:
    groups: dict[tuple[int, int, int], list[OcrWord]] = collections.defaultdict(list)
    for word in words:
        groups[(word.block, word.paragraph, word.line)].append(word)
    result: list[tuple[str, list[OcrWord]]] = []
    for grouped in groups.values():
        grouped.sort(key=lambda word: word.left)
        result.append((" ".join(word.text for word in grouped), grouped))
    # Tesseract runs with --psm 11 ("sparse text... no particular order"), so
    # unlike lines_from_page() this has no inherent top-to-bottom guarantee —
    # required by following_value_ocr_lines()'s "next line(s) below" rule.
    result.sort(key=lambda item: (min(word.top for word in item[1]), min(word.left for word in item[1])))
    return result


def following_value_ocr_lines(
    ordered_lines: Sequence[tuple[str, list[OcrWord]]], index: int, scale: float,
) -> Iterator[tuple[str, list[OcrWord]]]:
    """Value lines that belong to a label-only OCR line — the pixel-space
    analog of following_value_lines() for the raster path. `scale` converts
    the vector path's 120-point vertical-gap cutoff into pixels (pixels per
    point == settings.ocr_dpi / 72, the same ratio page_image() returns)."""
    _, label_words = ordered_lines[index]
    label_bottom = max(word.top + word.height for word in label_words)
    label_left = min(word.left for word in label_words)
    label_right = max(word.left + word.width for word in label_words)
    label_width = max(1, label_right - label_left)
    for following_text, following_words in ordered_lines[index + 1:]:
        following_top = min(word.top for word in following_words)
        if following_top - label_bottom > 120 * scale:
            break
        following_left = min(word.left for word in following_words)
        following_right = max(word.left + word.width for word in following_words)
        aligned = (
            abs(following_left - label_left) <= max(36 * scale, label_width * 0.5)
            or min(label_right, following_right) > max(label_left, following_left)
        )
        if not aligned:
            continue
        candidate = normalized(following_text)
        if (
            redactable_phrase(candidate, allow_short=True)
            and len(candidate.split()) <= 10
            and not LABEL_RE.match(candidate)
        ):
            yield following_text, following_words


def ocr_span_box(words: Sequence[OcrWord], text: str, start: int, end: int) -> tuple[int, int, int, int]:
    cursor = 0
    chosen: list[OcrWord] = []
    for index, word in enumerate(words):
        token_end = cursor + len(word.text)
        if token_end > start and cursor < end:
            chosen.append(word)
        cursor = token_end + (1 if index < len(words) - 1 else 0)
    if not chosen:
        chosen = list(words)
    return (
        min(word.left for word in chosen), min(word.top for word in chosen),
        max(word.left + word.width for word in chosen), max(word.top + word.height for word in chosen),
    )


@dataclass(frozen=True)
class OcrParagraph:
    text: str
    segments: tuple[tuple[int, int, tuple[OcrWord, ...]], ...]


def ocr_paragraphs(words: Sequence[OcrWord]) -> list[OcrParagraph]:
    grouped: dict[tuple[int, int], dict[int, list[OcrWord]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for word in words:
        grouped[(word.block, word.paragraph)][word.line].append(word)
    paragraphs: list[OcrParagraph] = []
    for _, line_map in sorted(grouped.items()):
        segments: list[tuple[int, int, tuple[OcrWord, ...]]] = []
        parts: list[str] = []
        cursor = 0
        for _, line_words in sorted(line_map.items()):
            line_words.sort(key=lambda word: word.left)
            text = " ".join(word.text for word in line_words)
            segments.append((cursor, cursor + len(text), tuple(line_words)))
            parts.append(text)
            cursor += len(text) + 1
        paragraphs.append(OcrParagraph("\n".join(parts), tuple(segments)))
    return paragraphs


def ocr_boxes_for_span(paragraph: OcrParagraph, start: int, end: int) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    for segment_start, segment_end, line_words in paragraph.segments:
        if segment_end <= start or segment_start >= end:
            continue
        boxes.append(ocr_span_box(
            line_words, paragraph.text[segment_start:segment_end],
            max(0, start - segment_start),
            min(segment_end - segment_start, end - segment_start),
        ))
    return boxes


def ocr_detection_boxes(
    words: Sequence[OcrWord],
    denylist: DenylistMatcher,
    lexicons: Lexicons | None = None,
    scale: float = 1.0,
    suppressed_counts: collections.Counter[str] | None = None,
    suppressed_categories: collections.Counter[str] | None = None,
) -> list[tuple[tuple[int, int, int, int], str]]:
    """Raster-path parity with line_detections(): same lexicon suppression
    and label-following-line lookahead, so both paths enforce one policy.

    Table-cell-crossing suppression is deliberately not threaded in: a
    rasterized page has no vector table geometry (find_tables()) for a match
    to cross, so line_detections()'s crossings() check has nothing to
    operate on here.
    """
    # Paragraph-level scanning mirrors the vector path's block-level
    # scanning, so the raster fallback has the same cross-line recall.
    results: list[tuple[tuple[int, int, int, int], str]] = []
    for paragraph in ocr_paragraphs(words):
        for category, pattern in DIRECT_PATTERNS.items():
            for match in pattern.finditer(paragraph.text):
                matched = match.group()
                suppressed = candidate_suppression(
                    category, matched, lexicons,
                    context=containing_line(paragraph.text, match.start(), match.end()),
                )
                if suppressed:
                    if suppressed_counts is not None:
                        suppressed_counts[suppressed.split(":")[0]] += 1
                    if suppressed_categories is not None:
                        suppressed_categories[category] += 1
                    continue
                for box in ocr_boxes_for_span(paragraph, match.start(), match.end()):
                    results.append((box, category))
        for denylist_match in denylist.finditer(paragraph.text):
            matched = denylist_match.group()
            suppressed = candidate_suppression(
                "denylist", matched, lexicons,
                context=containing_line(paragraph.text, denylist_match.start(), denylist_match.end()),
            )
            if suppressed:
                if suppressed_counts is not None:
                    suppressed_counts[suppressed.split(":")[0]] += 1
                if suppressed_categories is not None:
                    suppressed_categories["denylist"] += 1
                continue
            for box in ocr_boxes_for_span(paragraph, denylist_match.start(), denylist_match.end()):
                results.append((box, "denylist"))
        for segment_start, segment_end, line_words in paragraph.segments:
            text = paragraph.text[segment_start:segment_end]
            match = LABEL_VALUE_RE.match(text)
            if match:
                value = match.group(1)
                suppressed = candidate_suppression("labelled_identifier", value, lexicons, context=text)
                if suppressed:
                    if suppressed_counts is not None:
                        suppressed_counts[suppressed.split(":")[0]] += 1
                    if suppressed_categories is not None:
                        suppressed_categories["labelled_identifier"] += 1
                    continue
                results.append((ocr_span_box(line_words, text, match.start(1), match.end(1)), "labelled_identifier"))
    # A label alone on its line labels the following lines — the raster-path
    # analog of line_detections()'s LABEL_RE branch.
    ordered_lines = ocr_lines(words)
    for index, (text, _line_words) in enumerate(ordered_lines):
        if not LABEL_RE.match(normalized(text)):
            continue
        for value_text, value_words in following_value_ocr_lines(ordered_lines, index, scale):
            suppressed = candidate_suppression("labelled_identifier", value_text, lexicons, context=value_text)
            if suppressed:
                if suppressed_counts is not None:
                    suppressed_counts[suppressed.split(":")[0]] += 1
                if suppressed_categories is not None:
                    suppressed_categories["labelled_identifier"] += 1
                continue
            box = (
                min(word.left for word in value_words), min(word.top for word in value_words),
                max(word.left + word.width for word in value_words),
                max(word.top + word.height for word in value_words),
            )
            results.append((box, "labelled_identifier"))
    return results


def ocr_box_to_page_rect(
    page: fitz.Page, box: tuple[int, int, int, int], scale: float,
) -> fitz.Rect:
    """Map rendered-pixel OCR coordinates back to unrotated PDF space."""
    x0, y0, x1, y1 = box
    rendered = fitz.Rect(x0 / scale, y0 / scale, x1 / scale, y1 / scale)
    return rendered * page.derotation_matrix if page.rotation else rendered


def apply_image_boxes(image: Image.Image, boxes: Iterable[tuple[int, int, int, int]]) -> None:
    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in boxes:
        # Include the quiet zone and enough finder/guard area that an
        # error-correcting decoder cannot reconstruct a partially covered code.
        pad = max(12, int(max(x1 - x0, y1 - y0) * 0.18))
        draw.rectangle((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)), fill="black")


def raster_page_pdf(
    page: fitz.Page,
    settings: Settings,
    denylist: DenylistMatcher,
    temp_dir: Path,
    categories: set[str],
    lexicons: Lexicons | None = None,
    suppressed_counts: collections.Counter[str] | None = None,
    suppressed_categories: collections.Counter[str] | None = None,
) -> tuple[bytes, str | None]:
    try:
        image, scale = page_image(page, settings.ocr_dpi)
        first_words = run_tesseract_tsv(image, settings, temp_dir)
        detections = ocr_detection_boxes(
            first_words, denylist, lexicons, scale, suppressed_counts, suppressed_categories,
        )
        boxes = [box for box, category in detections]
        categories.update(category for box, category in detections)
        if settings.detect_barcodes:
            barcode_hits = barcode_boxes(image)
            boxes.extend(barcode_hits)
            if barcode_hits:
                categories.add("barcode_or_qr")
        for region in settings.regions:
            if region.applies(page.number + 1):
                x0, y0, x1, y1 = region.rect
                boxes.append((int(x0 * image.width), int(y0 * image.height), int(x1 * image.width), int(y1 * image.height)))
                categories.add(region.category)
        apply_image_boxes(image, boxes)
        if settings.detect_barcodes:
            for _ in range(3):
                residual_codes = barcode_boxes(image)
                if not residual_codes:
                    break
                apply_image_boxes(image, residual_codes)
                categories.add("barcode_or_qr")
            else:
                raise RuntimeError("residual barcode detected after raster redaction")
        sanitized_words = run_tesseract_tsv(image, settings, temp_dir)
        # Internal safety verification, not a second detection pass whose
        # suppressions need independent audit-counting — no counters passed,
        # mirroring how the vector path's own second pass
        # (sweep_flattened_output) keeps a separate sweep_suppressed counter
        # rather than sharing the first pass's.
        failure_reason: str | None = None
        if ocr_detection_boxes(sanitized_words, denylist, lexicons, scale):
            # Fails only this page rather than aborting the whole run (see
            # the PageProcessingError re-raise below and the caller in
            # sanitize_document). Blacken the entire rendered page rather
            # than trust the specific boxes this safety check found, and
            # skip reinserting the OCR text layer below — invisible-but-
            # copyable text over pixels that could not be confirmed clean
            # would defeat the point of failing closed.
            apply_image_boxes(image, [(0, 0, image.width, image.height)])
            failure_reason = "residual identifier detected after raster redaction"
        output = fitz.open()
        out_page = output.new_page(width=page.rect.width, height=page.rect.height)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", dpi=(settings.ocr_dpi, settings.ocr_dpi), optimize=True)
        out_page.insert_image(out_page.rect, stream=buffer.getvalue())
        if failure_reason is None:
            for text, line_words in ocr_lines(sanitized_words):
                left = min(word.left for word in line_words) / scale
                top = min(word.top for word in line_words) / scale
                right = max(word.left + word.width for word in line_words) / scale
                bottom = max(word.top + word.height for word in line_words) / scale
                rect = fitz.Rect(left, top, right, bottom)
                font_size = max(3.0, rect.height * 0.82)
                # A baseline insertion always creates the sanitized text item and
                # keeps each OCR line searchable as a phrase.
                out_page.insert_text(
                    (rect.x0, max(rect.y0 + font_size, rect.y1 - 0.5)),
                    text, fontsize=font_size, fontname="helv", render_mode=3, overlay=True,
                )
        output.set_metadata({})
        data = output.tobytes(garbage=4, clean=True, deflate=True)
        output.close()
        return data, failure_reason
    except PageProcessingError:
        raise
    except Exception as exc:
        raise PageProcessingError(page.number + 1, "raster sanitization or OCR failed") from exc


def remove_interactive_content(doc: fitz.Document) -> None:
    for page in doc:
        # Annotation linked lists can be malformed or invalidated by applied
        # redactions. Disconnect the page-level array atomically; garbage=4 on
        # save then removes every annotation, link, popup, and widget object.
        doc.xref_set_key(page.xref, "Annots", "[]")
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "AcroForm", "null")
    doc.xref_set_key(catalog, "OpenAction", "null")
    doc.xref_set_key(catalog, "AA", "null")
    for attachment in list(doc.embfile_names()):
        doc.embfile_del(attachment)
    doc.set_toc([])
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except ValueError:
        pass


def reconcile_page_geometry(reference: fitz.Document, candidate: fitz.Document) -> set[int]:
    """Restore rotation flags Ghostscript changed when that is lossless, and
    return the page numbers whose displayed geometry still differs.

    A pure /Rotate flag change (identical MediaBox) is reversed in place; any
    other geometry difference is reported so the caller can rebuild the page
    from the cleaned source instead of shipping altered geometry."""
    mismatched: set[int] = set()
    for index in range(min(len(reference), len(candidate))):
        ref_page = reference[index]
        out_page = candidate[index]
        ref_size = (round(ref_page.rect.width, 3), round(ref_page.rect.height, 3))
        if (round(out_page.rect.width, 3), round(out_page.rect.height, 3)) == ref_size:
            continue
        same_mediabox = all(
            round(out_value, 3) == round(ref_value, 3)
            for out_value, ref_value in zip(tuple(out_page.mediabox), tuple(ref_page.mediabox))
        )
        if same_mediabox and out_page.rotation != ref_page.rotation:
            out_page.set_rotation(ref_page.rotation)
        if (round(out_page.rect.width, 3), round(out_page.rect.height, 3)) != ref_size:
            mismatched.add(index + 1)
    return mismatched


def flatten_with_ghostscript(source: Path, destination: Path, settings: Settings) -> set[int] | None:
    """Flatten visible content locally while retaining vector text.

    Ghostscript may copy an unused OCProperties catalog even after flattening.
    We verify that no page drawing item still belongs to a layer, remove that
    catalog, and garbage-collect the unreachable definitions.

    AutoRotatePages is disabled: pdfwrite's default re-orients pages whose
    text is predominantly rotated (rotated tables/schedules), swapping page
    dimensions. Geometry is additionally reconciled against the source, and
    unreconcilable pages are returned for the raster fallback.
    """
    executable = shutil.which(settings.ghostscript_executable)
    if executable is None:
        return None
    intermediate = destination.with_name("ghostscript_intermediate.pdf")
    try:
        completed = subprocess.run(
            [
                executable, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER",
                "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.7",
                "-dAutoRotatePages=/None",
                "-dPreserveAnnots=false", f"-sOutputFile={intermediate}", str(source),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            timeout=GHOSTSCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        # A hang here must fail the whole document in a controlled way, not
        # silently fall back to the (equally slow, on a pathological input)
        # per-page raster path the "unavailable executable" case below uses.
        raise PageProcessingError(
            0, f"ghostscript flatten exceeded {GHOSTSCRIPT_TIMEOUT_SECONDS}s timeout",
        ) from exc
    if completed.returncode != 0 or not intermediate.is_file():
        return None
    try:
        doc = fitz.open(intermediate)
        unsafe_layer_pages = {
            page_index + 1
            for page_index, page in enumerate(doc)
            if any(item[2] for item in page.get_bboxlog(layers=True) if len(item) > 2)
        }
        reference = fitz.open(source)
        unsafe_layer_pages |= reconcile_page_geometry(reference, doc)
        reference.close()
        catalog = doc.pdf_catalog()
        doc.xref_set_key(catalog, "OCProperties", "null")
        remove_interactive_content(doc)
        doc.scrub(
            attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
            javascript=True, metadata=True, redactions=True, remove_links=True,
            reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True,
        )
        doc.save(destination, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True)
        doc.close()
        check = fitz.open(destination)
        safe = not check.get_ocgs()
        check.close()
        return unsafe_layer_pages if safe else None
    except Exception:
        return None


NER_CHUNK_MAX_CHARS = 1000
NER_MIN_ALNUM_CHARS = 12
NER_RECASE_UPPER_RATIO = 0.70


def normalize_case_for_ner(text: str) -> str:
    """Re-case predominantly ALL-CAPS text (drawing/spec style) so the NER
    model sees natural-looking casing. Every character maps to exactly one
    character, so entity offsets remain valid in the original text."""
    alpha = [ch for ch in text if ch.isalpha()]
    if not alpha:
        return text
    if sum(ch.isupper() for ch in alpha) / len(alpha) <= NER_RECASE_UPPER_RATIO:
        return text
    result: list[str] = []
    previous_alnum = False
    for ch in text:
        if ch.isalpha():
            candidate = ch.lower() if previous_alnum else ch.upper()
            # A case transform that changes length would break offset
            # alignment with the original block text; keep the original.
            result.append(candidate if len(candidate) == 1 else ch)
        else:
            result.append(ch)
        previous_alnum = ch.isalnum()
    return "".join(result)


def ner_chunks(text: str, max_chars: int = NER_CHUNK_MAX_CHARS) -> Iterator[tuple[int, str]]:
    """Split block text into chunks of at most max_chars at line boundaries,
    yielding (offset, chunk). A single line longer than max_chars is yielded
    whole rather than split mid-line."""
    if not text:
        return
    chunk_start = 0
    chunk_end = 0
    position = 0
    for line in text.split("\n"):
        line_start = position
        line_end = position + len(line)
        position = line_end + 1
        if chunk_end > chunk_start and line_end - chunk_start > max_chars:
            yield chunk_start, text[chunk_start:chunk_end]
            chunk_start = line_start
        chunk_end = line_end
    yield chunk_start, text[chunk_start:chunk_end]


class NerDetector:
    """Report-only named-entity scan over block-level text. The predict
    callable is injectable so the pipeline is testable without the model
    runtime: predict(texts, labels, threshold) returns one list of
    {start, end, label, score} entity dicts per input text."""

    def __init__(
        self,
        predict,
        labels: Sequence[str],
        threshold: float,
        model_name: str,
        max_findings: int = 500,
    ):
        self.predict = predict
        self.labels = tuple(labels)
        self.threshold = float(threshold)
        self.model_name = model_name
        self.max_findings = int(max_findings)

    def block_findings(self, blocks: Sequence[TextBlock]) -> Iterator[tuple[TextBlock, int, int, str, float]]:
        """Yield (block, start, end, label, score) with offsets into the
        original block text."""
        batch: list[str] = []
        origins: list[tuple[TextBlock, int]] = []
        for block in blocks:
            for offset, chunk in ner_chunks(block.text):
                if sum(ch.isalnum() for ch in chunk) < NER_MIN_ALNUM_CHARS:
                    continue
                batch.append(normalize_case_for_ner(chunk))
                origins.append((block, offset))
        if not batch:
            return
        for (block, offset), entities in zip(origins, self.predict(batch, list(self.labels), self.threshold)):
            for entity in entities:
                start = int(entity["start"]) + offset
                end = int(entity["end"]) + offset
                if start < 0 or end <= start or end > len(block.text):
                    continue
                yield block, start, end, str(entity["label"]), float(entity.get("score", 0.0))


def load_gliner_detector(settings: NerSettings) -> NerDetector:
    """Load the local zero-shot NER model. Offline operation is enforced
    in-process before the runtime is imported: a missing model can only stop
    the run, never trigger a download from a machine holding source PDFs."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    model_dir = Path(settings.model_dir)
    if not model_dir.is_dir():
        raise SystemExit(
            "NER review is enabled but the local model directory is missing; "
            "see ANONYMIZATION.md for the one-time offline model install"
        )
    try:
        from gliner import GLiNER
        model = GLiNER.from_pretrained(str(model_dir), local_files_only=True)
    except Exception as exc:
        raise SystemExit(
            "NER review is enabled but the local NER runtime failed to load; "
            "install requirements-anonymizer-ner.txt and verify the model directory"
        ) from exc

    def predict(texts: list[str], labels: list[str], threshold: float):
        return model.batch_predict_entities(texts, labels, threshold=threshold)

    return NerDetector(
        predict, settings.labels, settings.threshold, model_dir.name, settings.max_findings,
    )


def sweep_flattened_output(
    doc: fitz.Document,
    matcher: DenylistMatcher,
    page_categories: dict[int, set[str]],
    lexicons: Lexicons | None = None,
) -> tuple[collections.Counter[str], collections.Counter[str]]:
    """Second detection pass over the assembled output document.

    Flattening rewrites content streams, so text that never extracts
    contiguously from the source can extract contiguously afterwards. The
    sweep runs the exact verifier match stream (block_matches) on the final
    text and destructively redacts every hit, so verification scans a stream
    a detector has already swept. Scope is deliberately the verifier's
    detector set only — label heuristics on already-redacted pages would
    over-redact surviving technical text without changing the verdict."""
    counts: collections.Counter[str] = collections.Counter()
    suppressed: collections.Counter[str] = collections.Counter()
    for page_index in range(len(doc)):
        page = doc[page_index]
        try:
            seen: set[tuple[int, int, int, int]] = set()
            cells = LazyTableCells(page, lexicons is not None)
            sweep_lines = lines_from_page(page)
            for category, block, match in block_matches(
                text_blocks(sweep_lines), matcher, page_text_block(sweep_lines),
            ):
                spans = rects_for_block_span(block, match.start(), match.end())
                crossed = crossings(category, spans, cells)
                reason = candidate_suppression(
                    category, match.group(), lexicons, crossed,
                    containing_line(block.text, match.start(), match.end()),
                )
                if reason:
                    suppressed[reason.split(":")[0]] += 1
                    continue
                for rect in spans:
                    rect = padded(rect, page.rect)
                    key = tuple(round(value * 2) for value in rect)
                    if rect.is_empty or key in seen:
                        continue
                    seen.add(key)
                    page.add_redact_annot(rect, fill=(0, 0, 0), cross_out=False)
                    counts[category] += 1
                    page_categories[page_index + 1].add(category)
            if seen:
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_PIXELS,
                    graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=fitz.PDF_REDACT_TEXT_REMOVE,
                )
        except Exception as exc:
            raise PageProcessingError(page_index + 1, "post-flatten sweep failed") from exc
    return counts, suppressed


TRIAGE_LIMIT = 200


def render_residual_crop(
    page: fitz.Page, rects: Sequence[fitz.Rect], path: Path, dpi: int = 150,
) -> Path | None:
    """Render the residual region with context for local reviewer triage.
    The crop contains original page pixels: it stays on this machine and is
    NDA-sensitive material until reviewed and deleted."""
    if not rects:
        return None
    try:
        region = fitz.Rect(rects[0])
        for rect in rects[1:]:
            region.include_rect(rect)
        context = fitz.Rect(
            region.x0 - 48, region.y0 - 30, region.x1 + 48, region.y1 + 30,
        ) & page.rect
        if context.is_empty:
            return None
        scale = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=context, colorspace=fitz.csRGB, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        draw = ImageDraw.Draw(image)
        for rect in rects:
            draw.rectangle(
                (
                    (rect.x0 - context.x0) * scale, (rect.y0 - context.y0) * scale,
                    (rect.x1 - context.x0) * scale, (rect.y1 - context.y0) * scale,
                ),
                outline=(214, 40, 40), width=2,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        return path
    except Exception:
        # Triage rendering must never change the verification verdict.
        return None


def process_peak_rss_bytes() -> int:
    """Best-effort process peak RSS for verifier profiling.

    macOS reports bytes and Linux reports KiB. The sanitizer is local-only,
    so recording the host process high-water mark is more useful than a
    Python-only allocator metric that misses MuPDF, Pillow, and Tesseract.
    """
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if platform.system() == "Darwin" else peak * 1024


def _handle_cpu_limit_signal(signum: int, frame) -> None:  # noqa: ARG001 - signal handler signature
    """Converts a hard RLIMIT_CPU breach (SIGXCPU) into the same controlled
    failure path as any other page-processing error, instead of the default
    action (terminating the process)."""
    raise PageProcessingError(0, "CPU time ceiling exceeded")


def apply_process_resource_limits(limits: ResourceLimits) -> None:
    """Best-effort OS-level ceilings for the whole run's process.

    One Python process handles every document in a run (see orchestrate_run),
    so these limits are process-wide, not per-document. Each limit is applied
    independently: RLIMIT_AS/RLIMIT_RSS enforcement is inconsistent across
    platforms (notably unenforced on macOS), so a platform that rejects one
    limit still gets the others rather than none at all.
    """
    for name in ("RLIMIT_AS", "RLIMIT_RSS"):
        limit_id = getattr(resource, name, None)
        if limit_id is None:
            continue
        try:
            resource.setrlimit(limit_id, (limits.max_memory_bytes, limits.max_memory_bytes))
        except (ValueError, OSError):
            continue
        break
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.max_cpu_seconds, limits.max_cpu_seconds))
    except (ValueError, OSError, AttributeError):
        pass
    try:
        signal.signal(signal.SIGXCPU, _handle_cpu_limit_signal)
    except (ValueError, OSError, AttributeError):
        pass


def check_resource_ceilings(staging_dir: Path, limits: ResourceLimits, page_number: int = 0) -> None:
    """Polled ceiling check for memory and staging-disk usage.

    Called at page boundaries during processing (see sanitize_document). A
    breach raises the same PageProcessingError the Tesseract/Ghostscript
    timeouts already raise, so it reaches the existing FAIL-building path in
    orchestrate_run unchanged. Disk usage has no OS-level rlimit equivalent
    for a directory, so it's enforced here rather than via setrlimit.
    """
    peak_rss = process_peak_rss_bytes()
    if peak_rss > limits.max_memory_bytes:
        raise PageProcessingError(page_number, "memory ceiling exceeded")
    staging_bytes = sum(
        entry.stat().st_size for entry in staging_dir.rglob("*") if entry.is_file()
    )
    if staging_bytes > limits.max_staging_disk_bytes:
        raise PageProcessingError(page_number, "staging disk usage ceiling exceeded")


def rendered_ocr_verification(
    doc: fitz.Document,
    denylist: DenylistMatcher,
    lexicons: Lexicons | None,
    settings: Settings,
    temp_dir: Path,
) -> dict:
    """Render and OCR every final output page at verification_ocr_dpi.

    This deliberately runs after output creation on every page, including
    searchable and mixed pages. Matching reuses the unified raster/vector
    policy from ticket 03; an OCR error is a failed gate, never a silent skip.
    """
    pages: list[dict] = []
    totals: collections.Counter[str] = collections.Counter()
    profile: dict[str, dict[str, float | int]] = {}
    for page_index, page in enumerate(doc):
        started = time.perf_counter()
        text_length = len(normalized(page.get_text("text")))
        raster_ratio = page_raster_ratio(page)
        if text_length >= settings.min_vector_text_chars and raster_ratio >= settings.raster_image_area_ratio:
            page_type = "mixed"
        elif text_length >= settings.min_vector_text_chars:
            page_type = "searchable"
        else:
            page_type = "raster"
        suppressed: collections.Counter[str] = collections.Counter()
        suppressed_categories: collections.Counter[str] = collections.Counter()
        match_counts: collections.Counter[str] = collections.Counter()
        status = "clean"
        error: str | None = None
        try:
            image, scale = page_image(page, settings.verification_ocr_dpi)
            words = run_tesseract_tsv(image, settings, temp_dir)
            detections = ocr_detection_boxes(
                words, denylist, lexicons, scale, suppressed, suppressed_categories,
            )
            match_counts.update(category for _box, category in detections)
            if detections:
                status = "unresolved"
        except Exception:
            status = "error"
            error = "rendered-page OCR verification failed"
        elapsed = round(time.perf_counter() - started, 6)
        peak_rss = process_peak_rss_bytes()
        totals[status] += 1
        page_result = {
            "page": page_index + 1,
            "page_type": page_type,
            "status": status,
            "unresolved_match_counts": dict(sorted(match_counts.items())),
            "suppressed_by_rule": dict(sorted(suppressed.items())),
            "elapsed_seconds": elapsed,
            "process_peak_rss_bytes": peak_rss,
        }
        if error:
            page_result["error"] = error
        pages.append(page_result)
        bucket = profile.setdefault(page_type, {
            "pages": 0, "wall_seconds": 0.0, "peak_rss_bytes": 0,
        })
        bucket["pages"] = int(bucket["pages"]) + 1
        bucket["wall_seconds"] = round(float(bucket["wall_seconds"]) + elapsed, 6)
        bucket["peak_rss_bytes"] = max(int(bucket["peak_rss_bytes"]), peak_rss)
    return {
        "dpi": settings.verification_ocr_dpi,
        "pages": pages,
        "status_counts": dict(sorted(totals.items())),
        "profile_by_page_type": profile,
    }


def remediate_rendered_output(
    destination: Path,
    page_numbers: set[int],
    denylist: DenylistMatcher,
    lexicons: Lexicons | None,
    settings: Settings,
) -> dict[int, list[str]]:
    """Redact final-surface OCR matches once, then let verify_output recheck.

    Flattening can make outlined glyphs OCR-visible even when source OCR
    missed them. This pass is bounded to pages the verifier marked unresolved
    and never turns an OCR error into success.
    """
    if not page_numbers:
        return {}
    document = fitz.open(destination)
    remediated: dict[int, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="render_remediate_", dir=destination.parent) as temp_name:
        temp_dir = Path(temp_name)
        for page_number in sorted(page_numbers):
            page = document[page_number - 1]
            image, scale = page_image(page, settings.verification_ocr_dpi)
            words = run_tesseract_tsv(image, settings, temp_dir)
            detections = ocr_detection_boxes(
                words, denylist, lexicons, scale,
                collections.Counter(), collections.Counter(),
            )
            categories: set[str] = set()
            seen: set[tuple[int, int, int, int]] = set()
            for box, category in detections:
                rect = ocr_box_to_page_rect(page, box, scale)
                boundary = page.cropbox if page.rotation else page.rect
                rect = fitz.Rect(
                    rect.x0 - 1.5, rect.y0 - 1.5, rect.x1 + 1.5, rect.y1 + 1.5,
                ) & boundary
                key = tuple(round(value * 2) for value in rect)
                if rect.is_empty or key in seen:
                    continue
                seen.add(key)
                categories.add(category)
                page.add_redact_annot(rect, fill=(0, 0, 0), cross_out=False)
            if seen:
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_PIXELS,
                    graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=fitz.PDF_REDACT_TEXT_REMOVE,
                )
                remediated[page_number] = sorted(categories)
        if remediated:
            remove_interactive_content(document)
            document.scrub(
                attached_files=True, clean_pages=True, embedded_files=True, hidden_text=False,
                javascript=True, metadata=True, redactions=True, remove_links=True,
                reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True,
            )
            rewritten = temp_dir / "remediated.pdf"
            document.save(
                rewritten, garbage=4, clean=True, deflate=True,
                deflate_images=True, deflate_fonts=True,
            )
            document.close()
            os.replace(rewritten, destination)
        else:
            document.close()
    return remediated


def verify_output(
    destination: Path,
    source_sizes: Sequence[tuple[float, float]],
    denylist: DenylistMatcher,
    raster_pages: set[int],
    triage_dir: Path,
    run_key: bytes,
    ner_detector: NerDetector | None = None,
    lexicons: Lexicons | None = None,
    raster_page_failures: Sequence[dict] = (),
    settings: Settings | None = None,
) -> dict:
    settings = settings or Settings()
    doc = fitz.open(destination)
    residual_counts: collections.Counter[str] = collections.Counter()
    verifier_suppressed: collections.Counter[str] = collections.Counter()
    residual_denylist_hits = 0
    residuals: list[dict] = []
    residuals_truncated = 0
    ner_label_counts: collections.Counter[str] = collections.Counter()
    ner_suppressed: collections.Counter[str] = collections.Counter()
    ner_forms: dict[tuple[str, str], dict] = {}
    ner_forms_truncated: set[tuple[str, str]] = set()
    zone: str | None = None
    size_mismatch_pages: list[int] = []
    blank_render_pages: list[int] = []
    interactive_pages: list[int] = []
    with tempfile.TemporaryDirectory(prefix="render_verify_", dir=destination.parent) as verify_temp:
        rendered_verification = rendered_ocr_verification(
            doc, denylist, lexicons, settings, Path(verify_temp),
        )
    if triage_dir.exists():
        shutil.rmtree(triage_dir)
    for page_index, page in enumerate(doc):
        page_lines = lines_from_page(page)
        blocks = text_blocks(page_lines)
        zone = csi_zone_for_page("\n".join(line.text for line in page_lines), lexicons, zone)
        # The verifier consumes the same block-level match stream as
        # detection, so any residual is something detection could have seen —
        # not an artifact of whole-page text joining unrelated blocks.
        cells = LazyTableCells(page, lexicons is not None)
        for category, block, match in block_matches(blocks, denylist, page_text_block(page_lines)):
            # Detection, the post-flatten sweep, and verification share one
            # suppression decision. Flagging here what detection deliberately
            # left alone would fail every run on its own suppressed noise.
            match_rects = rects_for_block_span(block, match.start(), match.end())
            crossed = crossings(category, match_rects, cells)
            reason = candidate_suppression(
                category, match.group(), lexicons, crossed,
                containing_line(block.text, match.start(), match.end()),
            )
            if reason:
                verifier_suppressed[reason.split(":")[0]] += 1
                continue
            if category == "denylist":
                residual_denylist_hits += 1
            else:
                residual_counts[category] += 1
            if len(residuals) >= TRIAGE_LIMIT:
                residuals_truncated += 1
                continue
            entry = {
                "page": page_index + 1,
                "category": category,
                "digest": keyed_digest(run_key, match.group()),
            }
            crop_name = f"residual_{len(residuals) + 1:04d}_page{page_index + 1:04d}_{category}.png"
            crop_path = render_residual_crop(
                page, rects_for_block_span(block, match.start(), match.end()),
                triage_dir / crop_name,
            )
            if crop_path is not None:
                try:
                    entry["crop"] = str(crop_path.relative_to(destination.parent))
                except ValueError:
                    entry["crop"] = str(crop_path)
            residuals.append(entry)
        if ner_detector is not None:
            # Report-only: everything rule-based redaction caught is already
            # gone from this output, so every hit here is a net-new candidate
            # for the human reviewer. Never contributes to any check.
            page_hits: dict[tuple[int, int, int], tuple[float, str, TextBlock, int, int]] = {}
            for block, start, end, label, score in ner_detector.block_findings(blocks):
                key = (id(block), start, end)
                kept = page_hits.get(key)
                if kept is None or score > kept[0]:
                    page_hits[key] = (score, label, block, start, end)
            for score, label, block, start, end in page_hits.values():
                text = block.text[start:end]
                reason = candidate_suppression(
                    label, text, lexicons, context=containing_line(block.text, start, end),
                )
                if reason:
                    ner_suppressed[reason.split(":")[0]] += 1
                    continue
                ner_label_counts[label] += 1
                # Deduplicate by surface form across the whole document. The
                # findings are not distinct decisions: recurring boilerplate
                # dominates the raw count, and an occurrence-capped list
                # exhausts its budget on the first pages while the long tail —
                # where an unlisted party name actually hides — is never shown
                # to the reviewer. One entry per (label, form) makes the cap
                # span the document and makes each entry one decision.
                form_key = (label, re.sub(r"\s+", " ", text).strip().casefold())
                record = ner_forms.get(form_key)
                if record is None:
                    if len(ner_forms) >= ner_detector.max_findings:
                        ner_forms_truncated.add(form_key)
                        continue
                    label_slug = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_") or "entity"
                    record = {
                        "label": label,
                        "digest": keyed_digest(run_key, text),
                        "occurrences": 0,
                        "pages": [],
                        "score_max": 0.0,
                        "zone": zone,
                        "evidence": sorted(set(
                            e for e in (
                                lexicons.has_manufacturer_context(block.text, start, end)
                                if lexicons is not None else None,
                            ) if e
                        )),
                    }
                    crop_path = render_residual_crop(
                        page, rects_for_block_span(block, start, end),
                        triage_dir / "ner" / (
                            f"ner_{len(ner_forms) + 1:04d}_page{page_index + 1:04d}_{label_slug}.png"
                        ),
                    )
                    if crop_path is not None:
                        try:
                            record["crop"] = str(crop_path.relative_to(destination.parent))
                        except ValueError:
                            record["crop"] = str(crop_path)
                    ner_forms[form_key] = record
                record["occurrences"] += 1
                record["score_max"] = max(record["score_max"], round(score, 3))
                if len(record["pages"]) < 5 and (page_index + 1) not in record["pages"]:
                    record["pages"].append(page_index + 1)
        actual = (round(page.rect.width, 3), round(page.rect.height, 3))
        if page_index >= len(source_sizes) or actual != source_sizes[page_index]:
            size_mismatch_pages.append(page_index + 1)
        if page.first_annot or page.first_widget or page.get_links():
            interactive_pages.append(page_index + 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(0.10, 0.10), colorspace=fitz.csGRAY, alpha=False)
        if not pix.samples or min(pix.samples) == max(pix.samples):
            blank_render_pages.append(page_index + 1)
    identifying_metadata_keys = (
        "title", "author", "subject", "keywords", "creator", "producer",
        "creationDate", "modDate", "trapped",
    )
    metadata_empty = not any(doc.metadata.get(key) for key in identifying_metadata_keys)
    checks = {
        "page_count_preserved": len(doc) == len(source_sizes),
        "page_sizes_preserved": not size_mismatch_pages,
        "render_smoke_test": not blank_render_pages,
        "direct_identifier_scan": sum(residual_counts.values()) == 0,
        "denylist_scan": residual_denylist_hits == 0,
        "metadata_empty": metadata_empty,
        "annotations_links_forms_empty": not interactive_pages,
        "bookmarks_empty": not doc.get_toc(simple=True),
        "attachments_empty": not doc.embfile_names(),
        "optional_content_groups_empty": not doc.get_ocgs(),
        "raster_page_verification": not raster_page_failures,
        "rendered_page_ocr": all(
            page["status"] == "clean" for page in rendered_verification["pages"]
        ),
    }
    doc.close()
    result = {
        "checks": checks,
        "residual_match_counts": dict(sorted(residual_counts.items())),
        "residual_denylist_hits": residual_denylist_hits,
        "verifier_suppressed_by_rule": dict(sorted(verifier_suppressed.items())),
        "residuals": residuals,
        "residuals_truncated": residuals_truncated,
        "size_mismatch_pages": size_mismatch_pages,
        "blank_render_pages": blank_render_pages,
        "interactive_pages": interactive_pages,
        "rasterized_pages": sorted(raster_pages),
        "raster_page_failures": list(raster_page_failures),
        "rendered_ocr_verification": rendered_verification,
        "release_status": automated_gate_status(checks),
    }
    if ner_detector is not None:
        distinct_by_label: collections.Counter[str] = collections.Counter()
        for label, _form in ner_forms:
            distinct_by_label[label] += 1
        # Sort key uses the casefolded surface form (form_key[1]), not the
        # digest: the digest is keyed by a fresh random secret each run, so
        # sorting by it would make tie order vary run to run for the same
        # document even though nothing about the document changed.
        findings = [
            record for _key, record in sorted(
                ner_forms.items(),
                key=lambda item: (-item[1]["occurrences"], item[1]["label"], item[0][1]),
            )
        ]
        result["ner_review"] = {
            "enabled": True,
            "mode": "report_only",
            "model": ner_detector.model_name,
            "labels": list(ner_detector.labels),
            "threshold": ner_detector.threshold,
            # Total occurrences per label, unchanged in meaning.
            "finding_counts": dict(sorted(ner_label_counts.items())),
            # Distinct surface forms per label — the number of decisions a
            # reviewer actually has to make.
            "distinct_form_counts": dict(sorted(distinct_by_label.items())),
            "suppressed_by_rule": dict(sorted(ner_suppressed.items())),
            "findings": findings,
            "findings_truncated": len(ner_forms_truncated),
        }
    return result


def sanitize_document(
    source: Path,
    destination: Path,
    document_id: str,
    denylist: set[str],
    settings: Settings,
    temp_root: Path,
    run_key: bytes,
    ner_detector: NerDetector | None = None,
    lexicons: Lexicons | None = None,
) -> dict:
    matcher = DenylistMatcher(denylist)
    try:
        source_doc = fitz.open(source)
    except Exception as exc:
        raise SystemExit(f"{document_id}: source PDF could not be opened") from exc
    if source_doc.is_encrypted:
        # Checked immediately after opening, before any page-dependent
        # operation. This tool never attempts decryption and never accepts a
        # password argument — detection only, always a controlled FAIL.
        page_count = len(source_doc)
        source_doc.close()
        return {
            "document_id": document_id,
            "source_sha256": sha256_file(source),
            "pages": page_count,
            "release_status": RELEASE_STATUS_FAIL,
            "fail_reason": (
                "source PDF is encrypted; this tool never attempts decryption "
                "or accepts a password, so an encrypted input cannot be processed"
            ),
        }
    source_sizes = [(round(page.rect.width, 3), round(page.rect.height, 3)) for page in source_doc]
    if settings.redact_repeated_margin_images:
        margin_images, image_complexity_pages = repeated_margin_images(source_doc, settings.repeated_image_min_pages)
    else:
        margin_images, image_complexity_pages = {}, set()
    page_categories: dict[int, set[str]] = collections.defaultdict(set)
    raster_required: set[int] = set(image_complexity_pages)
    raster_page_failures: list[dict] = []
    rule_counts: collections.Counter[str] = collections.Counter()
    suppressed_counts: collections.Counter[str] = collections.Counter()
    suppressed_categories: collections.Counter[str] = collections.Counter()
    zone: str | None = None

    # Render/OCR every source page before choosing the vector or raster
    # remediation path. This catches identifiers encoded as outlines or other
    # visible graphics that text extraction cannot see. OCR boxes are mapped
    # back to page coordinates and redacted alongside vector detections;
    # pages already requiring raster reconstruction are independently handled
    # by raster_page_pdf(). An OCR failure is fail-closed.
    source_rendered_pages: list[dict] = []
    source_rendered_profile: dict[str, dict[str, float | int]] = {}
    source_rendered_detections: dict[int, list[tuple[fitz.Rect, str]]] = collections.defaultdict(list)
    with tempfile.TemporaryDirectory(prefix="source_render_scan_", dir=temp_root) as source_scan_temp:
        for page_index, page in enumerate(source_doc):
            page_number = page_index + 1
            started = time.perf_counter()
            text_length = len(normalized(page.get_text("text")))
            raster_ratio = page_raster_ratio(page)
            if text_length >= settings.min_vector_text_chars and raster_ratio >= settings.raster_image_area_ratio:
                page_type = "mixed"
            elif text_length >= settings.min_vector_text_chars:
                page_type = "searchable"
            else:
                page_type = "raster"
            try:
                image, scale = page_image(page, settings.verification_ocr_dpi)
                words = run_tesseract_tsv(image, settings, Path(source_scan_temp))
                detections = ocr_detection_boxes(
                    words, matcher, lexicons, scale,
                    collections.Counter(), collections.Counter(),
                )
            except Exception as exc:
                source_doc.close()
                raise PageProcessingError(
                    page_number, "source rendered-page OCR inspection failed",
                ) from exc
            match_counts: collections.Counter[str] = collections.Counter(
                category for _box, category in detections
            )
            source_rendered_detections[page_number].extend(
                (ocr_box_to_page_rect(page, box, scale), category)
                for box, category in detections
            )
            elapsed = round(time.perf_counter() - started, 6)
            peak_rss = process_peak_rss_bytes()
            source_rendered_pages.append({
                "page": page_number,
                "page_type": page_type,
                "status": "remediation_required" if detections else "clean",
                "match_counts": dict(sorted(match_counts.items())),
                "elapsed_seconds": elapsed,
                "process_peak_rss_bytes": peak_rss,
            })
            bucket = source_rendered_profile.setdefault(page_type, {
                "pages": 0, "wall_seconds": 0.0, "peak_rss_bytes": 0,
            })
            bucket["pages"] = int(bucket["pages"]) + 1
            bucket["wall_seconds"] = round(float(bucket["wall_seconds"]) + elapsed, 6)
            bucket["peak_rss_bytes"] = max(int(bucket["peak_rss_bytes"]), peak_rss)

    for page_index, page in enumerate(source_doc):
        page_number = page_index + 1
        try:
            if page_number in raster_required:
                continue
            lines = lines_from_page(page)
            text_length = len(normalized(" ".join(line.text for line in lines)))
            if page_needs_raster_pass(text_length, page_raster_ratio(page), settings):
                raster_required.add(page_number)
                continue
            zone = csi_zone_for_page(
                "\n".join(line.text for line in lines), lexicons, zone,
            )
            cells = LazyTableCells(page, lexicons is not None)
            detections = line_detections(lines, matcher, lexicons, page_number, zone, cells)
            detections.extend(
                Detection(rect, category, "source_rendered_ocr", page_number, zone=zone)
                for rect, category in source_rendered_detections.get(page_number, [])
            )
            detections.extend(
                Detection(rect, category, "geographic", page_number, zone=zone)
                for rect, category in geographic_rects(page, lines)
            )
            detections.extend(
                Detection(rect, category, "region", page_number, zone=zone)
                for rect, category in configured_page_rects(settings, page, page_number)
            )
            detections.extend(
                Detection(rect, category, "barcode", page_number, zone=zone)
                for rect, category in page_barcode_rects(page, settings)
            )
            detections.extend(
                Detection(rect, "repeated_margin_image", "image", page_number, zone=zone)
                for rect in margin_images.get(page_index, [])
            )
            seen: set[tuple[int, int, int, int]] = set()
            for detection in detections:
                if detection.suppressed_by:
                    # Counted and reported, never silently dropped, so a
                    # suppression rule can be audited and reverted.
                    suppressed_counts[detection.suppressed_by.split(":")[0]] += 1
                    suppressed_categories[detection.category] += 1
                    continue
                rect = padded(detection.rect, page.rect)
                key = tuple(round(value * 2) for value in rect)
                if rect.is_empty or key in seen:
                    continue
                seen.add(key)
                page.add_redact_annot(rect, fill=(0, 0, 0), cross_out=False)
                page_categories[page_number].add(detection.category)
                rule_counts[detection.category] += 1
            if seen:
                page.apply_redactions(
                    images=fitz.PDF_REDACT_IMAGE_PIXELS,
                    graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                    text=fitz.PDF_REDACT_TEXT_REMOVE,
                )
        except PageProcessingError:
            source_doc.close()
            raise
        except Exception as exc:
            source_doc.close()
            raise PageProcessingError(page_number, "inspection or redaction failed") from exc
        if settings.progress_every_pages and page_number % settings.progress_every_pages == 0:
            print(f"{document_id}: sanitized {page_number}/{len(source_doc)} pages", file=sys.stderr)
        if (
            settings.resource_limits.resource_check_every_pages
            and page_number % settings.resource_limits.resource_check_every_pages == 0
        ):
            check_resource_ceilings(destination.parent, settings.resource_limits, page_number)

    remove_interactive_content(source_doc)
    try:
        source_doc.scrub(
            attached_files=True, clean_pages=True, embedded_files=True, hidden_text=True,
            javascript=True, metadata=True, redactions=True, remove_links=True,
            reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True,
        )
    except Exception as exc:
        source_doc.close()
        raise SystemExit(f"{document_id}: hidden-content scrubbing failed") from exc

    with tempfile.TemporaryDirectory(prefix="pdf_sanitizer_", dir=temp_root) as temp_name:
        temp_dir = Path(temp_name)
        cleaned_path = temp_dir / "cleaned_source.pdf"
        # This is a local staging file. Garbage level 3 removes unreachable
        # redacted objects without the expensive duplicate-stream comparison;
        # image/font recompression is deferred to Ghostscript and final save.
        source_doc.save(cleaned_path, garbage=3, clean=True, deflate=True)
        source_doc.close()
        cleaned = fitz.open(cleaned_path)
        ghostscript_path = temp_dir / "vector_flattened.pdf"
        unsafe_ghostscript_pages = flatten_with_ghostscript(cleaned_path, ghostscript_path, settings)
        ghostscript_ok = unsafe_ghostscript_pages is not None
        vector_flattened = fitz.open(ghostscript_path) if ghostscript_ok else None
        if vector_flattened is not None and len(vector_flattened) != len(cleaned):
            vector_flattened.close()
            vector_flattened = None
            ghostscript_ok = False
        if unsafe_ghostscript_pages:
            raster_required.update(unsafe_ghostscript_pages)
        output = fitz.open()
        for page_index in range(len(cleaned)):
            page_number = page_index + 1
            page = cleaned[page_index]
            if page_number not in raster_required:
                if vector_flattened is not None:
                    output.insert_pdf(vector_flattened, from_page=page_index, to_page=page_index)
                    continue
                try:
                    flattened = fitz.open("pdf", cleaned.convert_to_pdf(from_page=page_index, to_page=page_index))
                    if flattened.get_ocgs():
                        raise RuntimeError("layer flattening incomplete")
                    output.insert_pdf(flattened)
                    flattened.close()
                    continue
                except Exception:
                    raster_required.add(page_number)
            page_pdf, failure_reason = raster_page_pdf(
                page, settings, matcher, temp_dir, page_categories[page_number],
                lexicons, suppressed_counts, suppressed_categories,
            )
            if failure_reason is not None:
                raster_page_failures.append({"page": page_number, "reason": failure_reason})
            raster_doc = fitz.open("pdf", page_pdf)
            output.insert_pdf(raster_doc)
            raster_doc.close()
            if settings.progress_every_pages and page_number % settings.progress_every_pages == 0:
                print(f"{document_id}: rebuilt {page_number}/{len(cleaned)} pages", file=sys.stderr)
            if (
                settings.resource_limits.resource_check_every_pages
                and page_number % settings.resource_limits.resource_check_every_pages == 0
            ):
                check_resource_ceilings(destination.parent, settings.resource_limits, page_number)
        if vector_flattened is not None:
            vector_flattened.close()
        cleaned.close()
        try:
            sweep_counts, sweep_suppressed = sweep_flattened_output(
                output, matcher, page_categories, lexicons,
            )
            suppressed_counts.update(sweep_suppressed)
        except PageProcessingError:
            output.close()
            raise
        remove_interactive_content(output)
        output.scrub(
            attached_files=True, clean_pages=True, embedded_files=True, hidden_text=False,
            javascript=True, metadata=True, redactions=True, remove_links=True,
            reset_fields=True, reset_responses=True, thumbnails=True, xml_metadata=True,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        output.save(destination, garbage=4, clean=True, deflate=True, deflate_images=True, deflate_fonts=True)
        output.close()

    verification = verify_output(
        destination, source_sizes, matcher, raster_required,
        destination.parent / "triage" / document_id,
        run_key,
        ner_detector=ner_detector, lexicons=lexicons,
        raster_page_failures=raster_page_failures,
        settings=settings,
    )
    unresolved_rendered_pages = {
        page["page"] for page in verification["rendered_ocr_verification"]["pages"]
        if page["status"] == "unresolved"
    }
    rendered_output_remediation = remediate_rendered_output(
        destination, unresolved_rendered_pages, matcher, lexicons, settings,
    )
    if rendered_output_remediation:
        for page_number, categories in rendered_output_remediation.items():
            page_categories[page_number].update(categories)
        verification = verify_output(
            destination, source_sizes, matcher, raster_required,
            destination.parent / "triage" / document_id,
            run_key,
            ner_detector=ner_detector, lexicons=lexicons,
            raster_page_failures=raster_page_failures,
            settings=settings,
        )
    # A value that only surfaced in final-output rendered OCR is exactly the
    # finding ticket 05 requires to block AUTOMATED_PASS. Auto-patching it and
    # re-verifying clean must not erase that: force this document to FAIL so a
    # human always reviews the patched pages, regardless of how clean the
    # second verification pass looks.
    verification["checks"]["no_render_remediation_required"] = not rendered_output_remediation
    verification["release_status"] = automated_gate_status(verification["checks"])
    return {
        "document_id": document_id,
        "source_sha256": sha256_file(source),
        "output_sha256": sha256_file(destination),
        "pages": len(source_sizes),
        "redaction_counts": dict(sorted(rule_counts.items())),
        "post_flatten_redactions": dict(sorted(sweep_counts.items())),
        # Candidates ruled out by the shared lexicons, by rule and by category.
        # Reported rather than dropped so any suppression can be audited.
        "suppressed_by_rule": dict(sorted(suppressed_counts.items())),
        "suppressed_by_category": dict(sorted(suppressed_categories.items())),
        "vector_flatten_backend": "ghostscript" if ghostscript_ok else "mupdf_per_page_with_ocr_fallback",
        "page_redactions": [
            {"page": page, "categories": sorted(categories)}
            for page, categories in sorted(page_categories.items()) if categories
        ],
        "source_rendered_ocr_detection": {
            "dpi": settings.verification_ocr_dpi,
            "pages": source_rendered_pages,
            "profile_by_page_type": source_rendered_profile,
        },
        "rendered_output_remediation": [
            {"page": page, "categories": categories}
            for page, categories in sorted(rendered_output_remediation.items())
        ],
        **verification,
    }


def inputs_from_args(values: Sequence[str]) -> Iterator[Path]:
    for value in values:
        path = Path(value)
        if path.is_dir():
            yield from sorted(p for p in path.iterdir() if p.suffix.casefold() == ".pdf")
        else:
            yield path


def build_run_payload(reports: list[dict], fingerprint: dict, ner_enabled: bool) -> dict:
    all_automated_checks_pass = all(
        report["release_status"] == RELEASE_STATUS_AUTOMATED_PASS for report in reports
    )
    payload = {
        "schema_version": 4,
        "documents": reports,
        "all_automated_checks_pass": all_automated_checks_pass,
        "release_status": derive_release_status(
            RELEASE_STATUS_AUTOMATED_PASS if all_automated_checks_pass else RELEASE_STATUS_FAIL,
        ),
        "fingerprint": fingerprint,
        "notes": [
            "The report contains counts, categories, page numbers, keyed digests, and hashes only. Digests are keyed by a random per-run secret that is never written to disk: values correlate within this report but not across runs, and cannot be reversed without the key.",
            "Residual triage crops under the output triage/ directory contain original page regions; treat them as NDA material and delete after review.",
            "Automated checks do not guarantee NDA compliance.",
            "An NDA-authorized person must visually review every page locally before any AI submission.",
        ],
    }
    if ner_enabled:
        payload["notes"].append(
            "NER review findings are report-only candidates for human triage; they never change automated checks."
        )
    return payload


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def generate_run_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def command_version(command: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            list(command), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0] if completed.returncode == 0 and text else None


def runtime_versions(settings: Settings, ner_detector: NerDetector | None) -> dict:
    def package(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    model_dir = Path(settings.ner.model_dir)
    return {
        "python": platform.python_version(),
        "pymupdf": getattr(fitz, "VersionBind", package("PyMuPDF")),
        "mupdf": getattr(fitz, "VersionFitz", None),
        "pillow": package("Pillow"),
        "zxing_cpp": package("zxing-cpp"),
        "reportlab": package("reportlab"),
        "tesseract": command_version([settings.tesseract_executable, "--version"]),
        "ghostscript": command_version([settings.ghostscript_executable, "--version"]),
        "ner_model": {
            "enabled": ner_detector is not None,
            "name": ner_detector.model_name if ner_detector is not None else model_dir.name,
            "available_locally": model_dir.is_dir(),
        },
    }


def manifest_document(report: dict, run_dir: Path) -> dict:
    document_id = report.get("document_id")
    output_path = run_dir / f"{document_id}.pdf" if document_id else None
    output_exists = bool(output_path and output_path.is_file())
    return {
        "document_id": document_id,
        "source_sha256": report.get("source_sha256"),
        "output": {
            "path": output_path.name if output_exists and output_path is not None else None,
            "sha256": report.get("output_sha256") if output_exists else None,
        },
        "pages": report.get("pages", 0),
        "processing_seconds": report.get("processing_seconds"),
        "release_status": report.get("release_status", RELEASE_STATUS_FAIL),
        "automated_gate_results": report.get("checks", {}),
        "statistics": {
            "redaction_counts": report.get("redaction_counts", {}),
            "post_flatten_redactions": report.get("post_flatten_redactions", {}),
            "rasterized_pages": report.get("rasterized_pages", []),
            "rendered_output_remediation": report.get("rendered_output_remediation", []),
            "source_rendered_ocr_profile": report.get("source_rendered_ocr_detection", {}).get(
                "profile_by_page_type", {}
            ),
            "rendered_ocr_profile": report.get("rendered_ocr_verification", {}).get(
                "profile_by_page_type", {}
            ),
        },
        **({"failure": report["fail_reason"]} if report.get("fail_reason") else {}),
    }


def build_manifest(
    *, run_id: str, started_at: str, completed_at: str, payload: dict,
    fingerprint: dict, versions: dict, run_dir: Path,
) -> dict:
    documents = [manifest_document(report, run_dir) for report in payload["documents"]]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "release_status": payload["release_status"],
        "fingerprint": fingerprint,
        "code_identity": fingerprint["code"],
        "runtime_versions": versions,
        "documents": documents,
        "statistics": {
            "documents": len(documents),
            "pages": sum(int(item.get("pages") or 0) for item in documents),
            "rasterized_pages": sum(
                len(item["statistics"]["rasterized_pages"]) for item in documents
            ),
        },
        "automated_gate_results": {
            "all_automated_checks_pass": payload["all_automated_checks_pass"],
            "documents": {
                item["document_id"]: item["automated_gate_results"] for item in documents
                if item["document_id"]
            },
        },
        "review": {
            "status": "not_started",
            "reviewer": None,
            "completed_at": None,
        },
    }


def review_summary(run_id: str, payload: dict, manifest: dict) -> str:
    lines = [
        f"# Sanitization review — {run_id}", "",
        f"Release status: **{payload['release_status']}**", "",
        "This run is not safe to release until an NDA-authorized reviewer completes local visual review and records it in `manifest.json`.",
        "", "## Documents", "",
    ]
    for document in manifest["documents"]:
        lines.append(
            f"- `{document['document_id']}`: {document['release_status']}; "
            f"{document['pages']} page(s); output `{document['output']['path'] or 'none'}`"
        )
    lines.extend(["", "## Review record", "", "- Status: not_started", "- Reviewer: —", "- Completed: —", ""])
    return "\n".join(lines)


def write_private_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def orchestrate_run(
    *, sources: Sequence[Path], output_root: Path, output_index_start: int,
    denylist: set[str], settings: Settings, temp_root: Path,
    denylist_path: Path | None, project_metadata_path: Path | None,
    config_path: Path, allowlist_path: Path | None, lexicon_dir: Path,
    lexicons: Lexicons | None, ner_detector: NerDetector | None,
    script_path: Path = Path(__file__), repo_root: Path | None = None,
) -> tuple[Path, dict]:
    """Build a complete run in a private temp directory, then publish once.

    The final run directory is created only by the atomic rename. Processing
    failures are converted into a FAIL document record before publication.
    """
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_root, 0o700)
    run_key = secrets.token_bytes(32)
    run_id = generate_run_id()
    final_dir = output_root / run_id
    while final_dir.exists():
        run_id = generate_run_id()
        final_dir = output_root / run_id
    staging_dir = Path(tempfile.mkdtemp(prefix=f".{run_id}.tmp-", dir=output_root))
    os.chmod(staging_dir, 0o700)
    started_at = utc_timestamp()
    fingerprint = build_fingerprint(
        script_path=script_path,
        repo_root=repo_root or script_path.resolve().parent.parent,
        denylist_path=denylist_path,
        project_metadata_path=project_metadata_path,
        config_path=config_path,
        allowlist_path=allowlist_path,
        lexicon_dir=lexicon_dir,
    )
    reports: list[dict] = []
    active_id: str | None = None
    active_source: Path | None = None
    try:
        for index, source in enumerate(sources, output_index_start):
            active_id = f"sanitized_document_{index:02d}"
            active_source = source
            destination = staging_dir / f"{active_id}.pdf"
            print(f"{active_id}: processing input {len(reports) + 1}/{len(sources)}", file=sys.stderr)
            started = time.perf_counter()
            report = sanitize_document(
                source, destination, active_id, denylist, settings, temp_root, run_key,
                ner_detector=ner_detector, lexicons=lexicons,
            )
            report["processing_seconds"] = round(time.perf_counter() - started, 6)
            reports.append(report)
    except (Exception, SystemExit) as exc:
        if isinstance(exc, PageProcessingError):
            reason = f"processing stopped at page {exc.page_number}: {exc.reason}"
        elif isinstance(exc, SystemExit) and str(exc):
            reason = str(exc)
        else:
            reason = "processing failed before the run completed"
        reports.append({
            "document_id": active_id or f"sanitized_document_{output_index_start:02d}",
            "source_sha256": sha256_file(active_source) if active_source and active_source.is_file() else None,
            "pages": 0,
            "checks": {"processing_completed": False},
            "release_status": RELEASE_STATUS_FAIL,
            "fail_reason": reason,
        })
    payload = build_run_payload(reports, fingerprint, ner_detector is not None)
    completed_at = utc_timestamp()
    versions = runtime_versions(settings, ner_detector)
    manifest = build_manifest(
        run_id=run_id, started_at=started_at, completed_at=completed_at,
        payload=payload, fingerprint=fingerprint, versions=versions, run_dir=staging_dir,
    )
    write_private_text(staging_dir / "report.json", json.dumps(payload, indent=2) + "\n")
    write_private_text(staging_dir / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    write_private_text(staging_dir / "review-summary.md", review_summary(run_id, payload, manifest))
    os.replace(staging_dir, final_dir)
    if payload["all_automated_checks_pass"]:
        # Confidential residual crops are only needed for triage on a run
        # that still requires review or investigation; a fully clean run has
        # nothing left to review, so the NDA-sensitive material is deleted
        # rather than left to accumulate (issue #8's "light version" scope
        # explicitly excludes cleanup on failure/crash). A real deletion
        # failure should surface loudly rather than leave NDA material
        # behind unnoticed, so this doesn't swallow errors the way
        # ignore_errors=True would.
        triage_dir = final_dir / "triage"
        if triage_dir.exists():
            shutil.rmtree(triage_dir)
    return final_dir, payload


def prune_expired_runs(
    output_root: Path, retention_days: int, *, now: dt.datetime | None = None,
) -> list[Path]:
    """Delete published run directories older than retention_days.

    An explicit maintenance step (see tools/prune_runs.py), never run
    automatically on every invocation. Directories whose name starts with
    "." are a staging dir from a run that never finished publishing (crashed
    before its atomic rename in orchestrate_run) and are left untouched —
    startup recovery for abandoned runs is explicitly out of scope.
    """
    if not output_root.is_dir():
        return []
    cutoff = (now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=retention_days)
    removed: list[Path] = []
    for entry in sorted(output_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        mtime = dt.datetime.fromtimestamp(entry.stat().st_mtime, dt.timezone.utc)
        if mtime < cutoff:
            shutil.rmtree(entry)
            removed.append(entry)
    return removed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanitize construction PDFs entirely on the local machine")
    parser.add_argument("inputs", nargs="+", help="PDF files or directories")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/runs"),
        help="root directory under which a unique immutable run directory is published",
    )
    parser.add_argument("--config", type=Path, default=Path("config/sanitizer.json"))
    parser.add_argument("--denylist", type=Path, default=Path("config/denylist.local.json"))
    parser.add_argument(
        "--project-metadata", type=Path, default=None,
        help="project intake JSON (name, number, address, parties, personnel); the "
             "primary way to seed the denylist, merged with --denylist when both are given",
    )
    parser.add_argument("--lexicons", type=Path, default=Path(DEFAULT_LEXICON_DIR))
    parser.add_argument("--allowlist", type=Path, default=Path(DEFAULT_ALLOWLIST))
    parser.add_argument(
        "--candidates", type=Path, default=Path("config/denylist.candidates.json"),
        help="where --propose-denylist writes its unconfirmed candidates",
    )
    parser.add_argument(
        "--propose-denylist", "--derive-denylist", dest="propose_denylist", action="store_true",
        help="scan the sources and write unconfirmed denylist candidates for human review, "
             "then exit without processing. Never writes a live denylist.",
    )
    parser.add_argument("--output-index-start", type=int, default=1, help="neutral output number for the first supplied input")
    parser.add_argument(
        "--ner-report", action="store_true",
        help="run the local NER review layer for this run (report-only; requires the local model)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sources = list(inputs_from_args(args.inputs))
    if not sources:
        raise SystemExit("No PDF inputs found")
    for source in sources:
        if not source.is_file() or source.suffix.casefold() != ".pdf":
            raise SystemExit("An input is not a readable PDF file")
    settings = load_settings(args.config)
    apply_process_resource_limits(settings.resource_limits)
    if settings.detect_barcodes and zxingcpp is None:
        raise SystemExit("The local barcode decoder dependency is missing")
    lexicons = load_lexicons(args.lexicons, args.allowlist)
    if args.propose_denylist:
        count = write_denylist_candidates(sources, args.candidates, lexicons)
        print(
            f"Wrote {count} unconfirmed denylist candidates to {args.candidates} "
            "(values not displayed). Review them, copy the genuine identifiers into the "
            "denylist, then re-run without --propose-denylist.",
            file=sys.stderr,
        )
        return 0
    denylist: set[str] = set()
    if args.project_metadata:
        denylist |= load_project_metadata(args.project_metadata)
    denylist_file_present = args.denylist.is_file()
    if denylist_file_present or not denylist:
        denylist |= load_denylist(args.denylist)
    if not denylist:
        raise SystemExit("No denylist terms are available; supply --denylist or --project-metadata")
    ner_detector: NerDetector | None = None
    if settings.ner.enabled or args.ner_report:
        # Loaded before page 1: if NER review is requested but unavailable,
        # failing fast beats a report that silently omits the review layer.
        ner_detector = load_gliner_detector(settings.ner)
        print("NER review layer enabled (report-only; never changes automated checks)", file=sys.stderr)
    temp_root = Path("tmp/pdfs")
    temp_root.mkdir(parents=True, exist_ok=True)
    run_dir, payload = orchestrate_run(
        sources=sources,
        output_root=args.output_dir,
        output_index_start=args.output_index_start,
        denylist=denylist,
        settings=settings,
        temp_root=temp_root,
        denylist_path=args.denylist if denylist_file_present else None,
        project_metadata_path=args.project_metadata,
        config_path=args.config,
        allowlist_path=args.allowlist,
        lexicon_dir=args.lexicons,
        lexicons=lexicons,
        ner_detector=ner_detector,
    )
    print(json.dumps({
        "run_directory": str(run_dir),
        "documents": len(payload["documents"]),
        "all_automated_checks_pass": payload["all_automated_checks_pass"],
        "release_status": payload["release_status"],
    }), file=sys.stderr)
    return 0 if payload["all_automated_checks_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
