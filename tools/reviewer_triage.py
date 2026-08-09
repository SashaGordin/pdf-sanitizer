#!/usr/bin/env python3
"""Real human-reviewer tool for a run's flagged findings (issue #7, ticket 06).

Serves an inbox-style page — findings list left, real crop plus a
plain-language guess right — and writes each disposition to that run's
decisions.json, keyed to the document's output hash from manifest.json.
A separate ops-only view previews (never applies) what a disposition would
add to the denylist or a lexicon.

Ported from the throwaway, never-merged prototype on
origin/worktree-prototype-reviewer-triage (tools/prototype_reviewer_triage.py
and tools/prototype_reviewer_triage_web.html), whose shape was validated
through three rounds of human-in-the-loop review (issue #7).

    .venv-anonymizer/bin/python tools/reviewer_triage.py output/runs/<run_id>
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from pathlib import Path

DISPOSITIONS = frozenset({"sensitive", "safe", "duplicate", "escalate"})

# Guards the read-modify-write-replace sequence in record_disposition: the
# HTTP server is threaded, and two racing POSTs to the same decisions.json
# must not interleave.
_DECISIONS_LOCK = threading.Lock()

# Category (regex detector) or NER label -> a plain-language hint. Never the
# raw internal name — a reviewer with zero codebase context should never see
# "denylist" or "street_address", only what it looks like.
PLAIN_GUESS: dict[str, str] = {
    "denylist": "matches something we've flagged before",
    "street_address": "looks like an address",
    "city_state_zip": "looks like an address",
    "po_box": "looks like an address",
    "email": "looks like an email address",
    "phone": "looks like a phone number",
    "url": "looks like a web address",
    "file_path": "looks like a file path",
    "coordinate": "looks like a map coordinate",
    "parcel_or_lot": "looks like a parcel or lot number",
    "permit_or_application": "looks like a permit or application number",
    "project_or_job_number": "looks like a project or job number",
    "credentialed_person": "looks like a person's name with a professional title",
    "labelled_identifier": "looks like an identifier next to a form label",
    "person name": "looks like a person's name",
    "company name": "looks like a company name",
    "organization": "looks like a company name",
    "project name": "looks like a project name",
    "street address": "looks like an address",
    "city": "looks like a city name",
}
GENERIC_GUESS = "flagged item"


def load_findings(report: dict) -> list[dict]:
    """Flatten a run's report.json into one finding shape per candidate.

    Ported from tools/prototype_reviewer_triage.py (validated over three
    HITL rounds, issue #7) — same id scheme (document_id first, colon-
    delimited) and field names, unchanged.
    """
    findings: list[dict] = []
    for doc in report.get("documents", []):
        document_id = doc.get("document_id")
        for residual in doc.get("residuals", []):
            findings.append({
                "document_id": document_id,
                "source": "residual",
                "id": f"{document_id}:residual:{residual['page']}:{residual['category']}",
                "category": residual["category"],
                "shape": residual["shape"],
                "pages": [residual["page"]],
                "occurrences": 1,
                "score_max": None,
                "crop": residual.get("crop"),
            })
        for finding in doc.get("ner_review", {}).get("findings", []):
            findings.append({
                "document_id": document_id,
                "source": "ner",
                "id": f"{document_id}:ner:{finding['label']}:{finding['shape']}",
                "category": finding["label"],
                "shape": finding["shape"],
                "pages": finding.get("pages", []),
                "occurrences": finding.get("occurrences", 1),
                "score_max": finding.get("score_max"),
                "crop": finding.get("crop"),
            })
    return findings


def build_reviewer_payload(findings: list[dict], decisions: dict, *, run_dir: Path) -> list[dict]:
    """Build the reviewer-facing view of every finding.

    Deliberately excludes category/label/shape/occurrences/score_max/
    evidence/zone — internal detail a reviewer with zero codebase context
    must never see (ticket 06 criterion 1). The real crop image already
    shows what was found; a text "shape" is redundant now that there's a
    real picture to look at, not just a placeholder.
    """
    payload = []
    for finding in findings:
        crop = finding.get("crop")
        crop_url = None
        if crop and (run_dir / crop).is_file():
            crop_url = f"/crops?id={finding['id']}"
        item = {
            "id": finding["id"],
            "plain_guess": PLAIN_GUESS.get(finding["category"], GENERIC_GUESS),
            "pages": finding["pages"],
            "crop_url": crop_url,
        }
        decision = decisions.get(finding["id"])
        if decision is not None:
            item["disposition"] = decision["disposition"]
            item["note"] = decision["note"]
        payload.append(item)
    return payload


def load_decisions(decisions_path: Path) -> dict:
    """Return the finding_id -> decision dict, {} if none recorded yet."""
    if not decisions_path.is_file():
        return {}
    return json.loads(decisions_path.read_text(encoding="utf-8")).get("decisions", {})


def _output_sha256_for_document(document_id: str, manifest: dict) -> str:
    for document in manifest.get("documents", []):
        if document.get("document_id") == document_id:
            sha256 = document.get("output", {}).get("sha256")
            if sha256:
                return sha256
    raise ValueError(f"no output hash found for document {document_id!r} in manifest.json")


def record_disposition(finding_id: str, disposition: str, note: str, decisions_path: Path) -> dict:
    """Record one reviewer disposition, keyed to the document's output hash.

    Upserts by finding_id — a reviewer can change their mind, and the
    latest call wins rather than accumulating duplicate entries. Writes
    atomically (temp file + os.replace) so a crash or a racing request
    never leaves decisions.json half-written.
    """
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition {disposition!r}; must be one of {sorted(DISPOSITIONS)}")
    document_id = finding_id.split(":", 1)[0]
    manifest_path = decisions_path.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_sha256 = _output_sha256_for_document(document_id, manifest)

    entry = {
        "document_id": document_id,
        "output_sha256": output_sha256,
        "disposition": disposition,
        "note": note,
        "decided_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    with _DECISIONS_LOCK:
        if decisions_path.is_file():
            on_disk = json.loads(decisions_path.read_text(encoding="utf-8"))
        else:
            on_disk = {"run_id": manifest.get("run_id"), "decisions": {}}
        on_disk["decisions"][finding_id] = entry
        fd, tmp_name = tempfile.mkstemp(dir=decisions_path.parent, prefix=".decisions.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(on_disk, indent=2) + "\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, decisions_path)
    return entry


def build_promotion_preview(findings_by_id: dict, decisions: dict) -> dict:
    """Preview what each disposition would add to policy — never applies it.

    Ported from tools/prototype_reviewer_triage.py's summarize_promotions():
    "duplicate" ("we've already flagged this") previews a denylist addition;
    "safe" ("fine to show") previews a scoped lexicon rule. "sensitive" and
    "escalate" propose nothing. A human still has to act on this preview —
    nothing here writes to config/denylist.local.json or any lexicon file.

    Reads finding["shape"], which is masked_shape() output today. If ticket
    01 (report keyed-digest fix) later replaces that field with an opaque
    digest, this preview's hint text degrades silently — revisit then.
    """
    denylist_additions = []
    lexicon_proposals = []
    for finding_id, decision in decisions.items():
        finding = findings_by_id.get(finding_id)
        if finding is None:
            continue
        candidate = {"finding_id": finding_id, "category": finding["category"], "shape": finding["shape"]}
        if decision["disposition"] == "duplicate":
            denylist_additions.append(candidate)
        elif decision["disposition"] == "safe":
            lexicon_proposals.append(candidate)
    return {"denylist_additions": denylist_additions, "lexicon_proposals": lexicon_proposals}


REVIEWER_HTML = Path(__file__).parent / "reviewer_triage.html"
OPS_HTML = Path(__file__).parent / "reviewer_triage_ops.html"


def _make_handler(
    *, run_dir: Path, findings_by_id: dict, decisions_path: Path,
) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass  # keep CLI/test output quiet

        def _send_json(self, status: int, payload) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, content_type: str) -> None:
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/":
                self._send_file(REVIEWER_HTML, "text/html; charset=utf-8")
            elif parsed.path == "/ops":
                self._send_file(OPS_HTML, "text/html; charset=utf-8")
            elif parsed.path == "/api/findings":
                decisions = load_decisions(decisions_path)
                payload = build_reviewer_payload(list(findings_by_id.values()), decisions, run_dir=run_dir)
                self._send_json(200, payload)
            elif parsed.path == "/api/ops/preview":
                decisions = load_decisions(decisions_path)
                self._send_json(200, build_promotion_preview(findings_by_id, decisions))
            elif parsed.path == "/crops":
                # Crop path is looked up server-side from findings_by_id, never
                # parsed out of the client-supplied path — sidesteps directory
                # traversal by construction rather than by guarding against it.
                finding_id = urllib.parse.parse_qs(parsed.query).get("id", [None])[0]
                finding = findings_by_id.get(finding_id) if finding_id else None
                crop = finding.get("crop") if finding else None
                crop_path = (run_dir / crop) if crop else None
                if crop_path is None or not crop_path.is_file():
                    self._send_json(404, {"error": "crop not found"})
                else:
                    self._send_file(crop_path, "image/png")
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/api/decisions":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "malformed JSON body"})
                return
            finding_id = body.get("finding_id")
            if finding_id not in findings_by_id:
                self._send_json(404, {"error": f"unknown finding_id {finding_id!r}"})
                return
            try:
                entry = record_disposition(finding_id, body.get("disposition"), body.get("note", ""), decisions_path)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(200, entry)

    return Handler


def make_server(
    host: str, port: int, *, run_dir: Path, findings_by_id: dict, decisions_path: Path,
) -> http.server.ThreadingHTTPServer:
    handler = _make_handler(run_dir=run_dir, findings_by_id=findings_by_id, decisions_path=decisions_path)
    return http.server.ThreadingHTTPServer((host, port), handler)


def load_report(run_directory: Path) -> dict:
    """Load a run's report.json, requiring manifest.json to sit beside it.

    Matches tools/verify_output_text.py's convention: raise rather than
    print-and-return, so main() has one place that turns a bad run
    directory into a controlled exit rather than an inline check.
    """
    if not (run_directory / "manifest.json").is_file():
        raise ValueError(f"{run_directory}: no manifest.json")
    return json.loads((run_directory / "report.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    try:
        report = load_report(args.run_directory)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"reviewer-triage failed: {exc}", file=sys.stderr)
        return 2

    findings_by_id = {finding["id"]: finding for finding in load_findings(report)}
    decisions_path = args.run_directory / "decisions.json"

    server = make_server(
        args.host, args.port, run_dir=args.run_directory,
        findings_by_id=findings_by_id, decisions_path=decisions_path,
    )
    url = f"http://{args.host}:{server.server_address[1]}/"
    print(f"Serving reviewer triage at {url} (ops view at {url}ops)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
