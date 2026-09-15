#!/usr/bin/env python3
"""Validate the ReviewLens V2 context vault without third-party packages."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_DIR = ROOT / "context"
CANVAS_PATH = CONTEXT_DIR / "ReviewLens V2 Context Map.canvas"

REQUIRED_FRONTMATTER_FIELDS = {
    "id",
    "title",
    "type",
    "status",
    "domain",
    "tags",
    "depends_on",
    "related",
    "read_when",
}

OBSOLETE_CONTEXT_STEMS = {
    "00_PROJECT_OVERVIEW",
    "01_SCOPE_AND_REQUIREMENTS",
    "02_USER_FLOW",
    "03_ARCHITECTURE",
    "04_VIDEO_SELECTION",
    "05_TRANSCRIPT_STRATEGY",
    "06_COMMENT_ANALYSIS",
    "07_ANALYSIS_SCHEMA",
    "08_SCORING_SYSTEM",
    "09_AGGREGATION_RULES",
    "10_AI_PROMPTS",
    "11_API_CONTRACTS",
    "12_DATA_MODELS",
    "13_COST_AND_QUOTA_STRATEGY",
    "14_FAILURE_HANDLING",
    "15_SECURITY",
    "16_UI_SPEC",
    "17_ROADMAP",
    "18_ACCEPTANCE_CRITERIA",
    "19_DECISIONS",
    "20_AGENT_BUILD_INSTRUCTIONS",
    "21_IMPLEMENTATION_NOTES",
    "22_DESIGN_REFERENCES",
}

STALE_PROVIDER_TERM = re.compile(
    r"openrouter/free|free[- ]tier|direct[- ]provider|direct\s+(?:xai|openai)"
    r"|xai_api_key|xai_model|openai_api_key|openai_model"
    r"|ai_primary_provider|ai_aggregator_provider",
    re.IGNORECASE,
)
LEGACY_QUALIFIER = re.compile(
    r"\b(?:legacy|deprecated|ignored|remove|removed|no|not|never|cannot|prohibited|only)\b"
    r"|\b(?:may|must)\s+not\b",
    re.IGNORECASE,
)

FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---(?:\n|\Z)", re.DOTALL)
FRONTMATTER_KEY_RE = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):", re.MULTILINE)
WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


class ContextAudit:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.documents = self._documentation_files()
        self.vault_files = self._vault_files()
        self.file_index = self._build_file_index()

    @staticmethod
    def _documentation_files() -> list[Path]:
        root_docs = [
            ROOT / "README.md",
            ROOT / "ARCHITECTURE.md",
            ROOT / "AGENTS.md",
            ROOT / "REBUILD_PHASES.md",
        ]
        return sorted(
            [path for path in root_docs if path.is_file()]
            + list(CONTEXT_DIR.rglob("*.md")),
            key=lambda path: path.as_posix().casefold(),
        )

    @staticmethod
    def _vault_files() -> list[Path]:
        root_notes = list(ROOT.glob("*.md"))
        return sorted(
            root_notes + [path for path in CONTEXT_DIR.rglob("*") if path.is_file()],
            key=lambda path: path.as_posix().casefold(),
        )

    def _build_file_index(self) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = defaultdict(list)
        for path in self.vault_files:
            index[path.name.casefold()].append(path)
            index[path.stem.casefold()].append(path)
        return index

    @staticmethod
    def _display(path: Path) -> str:
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return str(path)

    def error(self, path: Path, message: str) -> None:
        self.errors.append(f"{self._display(path)}: {message}")

    def read(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.error(path, f"cannot read UTF-8 text: {exc}")
            return ""

    def check_numbered_notes(self) -> list[Path]:
        numbered_notes = sorted(
            path
            for path in CONTEXT_DIR.glob("*.md")
            if re.fullmatch(r"\d{2}_.+\.md", path.name)
        )
        expected_numbers = {f"{number:02d}" for number in range(28)}
        notes_by_number: dict[str, list[Path]] = defaultdict(list)
        seen_ids: dict[str, Path] = {}

        for path in numbered_notes:
            notes_by_number[path.name[:2]].append(path)

        for number in sorted(expected_numbers):
            matches = notes_by_number.get(number, [])
            if not matches:
                self.error(CONTEXT_DIR, f"missing numbered Target V2 note {number}")
            elif len(matches) > 1:
                names = ", ".join(match.name for match in matches)
                self.error(CONTEXT_DIR, f"multiple numbered notes use {number}: {names}")

        unexpected = sorted(set(notes_by_number) - expected_numbers)
        for number in unexpected:
            self.error(CONTEXT_DIR, f"unexpected numbered note prefix {number}")

        if len(numbered_notes) != 28:
            self.error(
                CONTEXT_DIR,
                f"expected exactly 28 numbered Target V2 notes, found {len(numbered_notes)}",
            )

        for path in numbered_notes:
            text = self.read(path)
            match = FRONTMATTER_RE.match(text)
            if not match:
                self.error(path, "missing or malformed opening YAML frontmatter")
                continue

            frontmatter = match.group("body")
            fields = {
                key_match.group("key") for key_match in FRONTMATTER_KEY_RE.finditer(frontmatter)
            }
            for field in sorted(REQUIRED_FRONTMATTER_FIELDS - fields):
                self.error(path, f"frontmatter is missing required field '{field}'")

            expected_id = f"RL-V2-{path.name[:2]}"
            actual_id = self._frontmatter_scalar(frontmatter, "id")
            if actual_id != expected_id:
                self.error(path, f"frontmatter id must be '{expected_id}', found {actual_id!r}")
            elif actual_id in seen_ids:
                self.error(
                    path,
                    f"frontmatter id duplicates {self._display(seen_ids[actual_id])}",
                )
            else:
                seen_ids[actual_id] = path

            status = self._frontmatter_scalar(frontmatter, "status")
            if status != "target-v2":
                self.error(path, f"frontmatter status must be 'target-v2', found {status!r}")

            if not re.search(r"^\*\*Status:\s*Target V2\*\*\s*$", text, re.MULTILINE):
                self.error(path, "missing visible '**Status: Target V2**' marker")

            self._check_stale_provider_language(path, text)

        return numbered_notes

    @staticmethod
    def _frontmatter_scalar(frontmatter: str, key: str) -> str | None:
        match = re.search(rf"^{re.escape(key)}:\s*(.*?)\s*$", frontmatter, re.MULTILINE)
        if not match:
            return None
        value = match.group(1).strip()
        return value.strip('"\'') if value else None

    def _check_stale_provider_language(self, path: Path, text: str) -> None:
        for line_number, line in enumerate(text.splitlines(), start=1):
            if STALE_PROVIDER_TERM.search(line) and not LEGACY_QUALIFIER.search(line):
                self.error(
                    path,
                    "line "
                    f"{line_number} mentions a free-tier/direct-provider term without an "
                    "explicit legacy, removal, prohibition, or deprecation qualifier",
                )

    def check_obsolete_context_names(self) -> None:
        for path in self.documents:
            text = self.read(path)
            for obsolete_stem in sorted(OBSOLETE_CONTEXT_STEMS):
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(obsolete_stem)}(?:\.md)?", text):
                    self.error(path, f"references obsolete context note '{obsolete_stem}'")

    def check_links(self) -> None:
        for path in self.documents:
            text = self.read(path)
            for match in WIKI_LINK_RE.finditer(text):
                self._check_wiki_link(path, match.group(1))
            for match in MARKDOWN_LINK_RE.finditer(text):
                self._check_markdown_link(path, match.group(1))

    def _check_wiki_link(self, source: Path, raw_target: str) -> None:
        target = raw_target.split("|", maxsplit=1)[0].strip()
        file_part, separator, heading = target.partition("#")
        candidates = self._resolve_wiki_file(source, file_part.strip())
        if not candidates:
            self.error(source, f"unresolved wiki link '[[{raw_target}]]'")
            return
        if len(candidates) > 1:
            locations = ", ".join(self._display(path) for path in candidates)
            self.error(source, f"ambiguous wiki link '[[{raw_target}]]' resolves to {locations}")
            return
        if separator and heading.strip() and not self._heading_exists(candidates[0], heading):
            self.error(source, f"wiki link '[[{raw_target}]]' targets a missing heading")

    def _resolve_wiki_file(self, source: Path, file_part: str) -> list[Path]:
        if not file_part:
            return [source]

        normalized = unquote(file_part.replace("\\", "/"))
        relative_path = Path(normalized)
        exact_candidates = [source.parent / relative_path, ROOT / relative_path]
        if not relative_path.suffix:
            exact_candidates.extend(
                [
                    (source.parent / relative_path).with_suffix(".md"),
                    (ROOT / relative_path).with_suffix(".md"),
                ]
            )

        existing = self._unique_paths(path for path in exact_candidates if path.is_file())
        if existing:
            return existing

        lookup_key = relative_path.name.casefold()
        indexed = self.file_index.get(lookup_key, [])
        if not indexed and relative_path.suffix:
            indexed = self.file_index.get(relative_path.stem.casefold(), [])
        return self._unique_paths(indexed)

    def _check_markdown_link(self, source: Path, raw_target: str) -> None:
        target = raw_target.strip()
        if target.startswith("<") and ">" in target:
            target = target[1 : target.index(">")]
        else:
            target = target.split(maxsplit=1)[0]

        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            return

        decoded_path = unquote(parsed.path)
        destination = source if not decoded_path else (source.parent / decoded_path).resolve()
        try:
            destination.relative_to(ROOT)
        except ValueError:
            self.error(source, f"local Markdown link escapes the repository: '{raw_target}'")
            return

        if not destination.exists():
            self.error(source, f"unresolved local Markdown link '{raw_target}'")
            return
        if parsed.fragment and destination.is_file():
            if not self._heading_exists(destination, unquote(parsed.fragment)):
                self.error(source, f"Markdown link '{raw_target}' targets a missing heading")

    def _heading_exists(self, path: Path, requested_heading: str) -> bool:
        if path.suffix.casefold() != ".md":
            return False
        headings = [match.group(1) for match in HEADING_RE.finditer(self.read(path))]
        requested_text = self._normalize_heading(requested_heading)
        requested_slug = self._markdown_slug(requested_heading)
        return any(
            self._normalize_heading(heading) == requested_text
            or self._markdown_slug(heading) == requested_slug
            for heading in headings
        )

    @staticmethod
    def _normalize_heading(value: str) -> str:
        value = re.sub(r"[`*_~]", "", value)
        return " ".join(value.split()).casefold()

    @classmethod
    def _markdown_slug(cls, value: str) -> str:
        value = cls._normalize_heading(value)
        value = re.sub(r"[^\w\s-]", "", value)
        return re.sub(r"[-\s]+", "-", value).strip("-")

    @staticmethod
    def _unique_paths(paths: Iterable[Path]) -> list[Path]:
        unique: dict[str, Path] = {}
        for path in paths:
            resolved = path.resolve()
            unique[str(resolved).casefold()] = resolved
        return sorted(unique.values(), key=lambda path: path.as_posix().casefold())

    def check_canvas(self, numbered_notes: list[Path]) -> None:
        try:
            canvas = json.loads(self.read(CANVAS_PATH))
        except json.JSONDecodeError as exc:
            self.error(CANVAS_PATH, f"invalid JSON: {exc}")
            return

        nodes = canvas.get("nodes")
        edges = canvas.get("edges")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            self.error(CANVAS_PATH, "top-level 'nodes' and 'edges' must be lists")
            return

        node_ids: set[str] = set()
        file_counts: dict[str, int] = defaultdict(int)
        for node in nodes:
            if not isinstance(node, dict):
                self.error(CANVAS_PATH, "every Canvas node must be an object")
                continue
            node_id = node.get("id")
            if not isinstance(node_id, str) or not node_id:
                self.error(CANVAS_PATH, "every Canvas node must have a non-empty string id")
            elif node_id in node_ids:
                self.error(CANVAS_PATH, f"duplicate Canvas node id '{node_id}'")
            else:
                node_ids.add(node_id)

            if node.get("type") == "file":
                file_value = node.get("file")
                if not isinstance(file_value, str) or not file_value:
                    self.error(CANVAS_PATH, f"file node '{node_id}' has no file path")
                    continue
                normalized = Path(file_value.replace("\\", "/")).as_posix()
                file_counts[normalized.casefold()] += 1
                if not (ROOT / normalized).is_file():
                    self.error(CANVAS_PATH, f"file node '{node_id}' targets missing '{file_value}'")

        edge_ids: set[str] = set()
        for edge in edges:
            if not isinstance(edge, dict):
                self.error(CANVAS_PATH, "every Canvas edge must be an object")
                continue
            edge_id = edge.get("id")
            if not isinstance(edge_id, str) or not edge_id:
                self.error(CANVAS_PATH, "every Canvas edge must have a non-empty string id")
            elif edge_id in edge_ids:
                self.error(CANVAS_PATH, f"duplicate Canvas edge id '{edge_id}'")
            else:
                edge_ids.add(edge_id)
            for endpoint in ("fromNode", "toNode"):
                if edge.get(endpoint) not in node_ids:
                    self.error(
                        CANVAS_PATH,
                        f"edge '{edge_id}' references missing {endpoint} '{edge.get(endpoint)}'",
                    )

        expected_canvas_files = [
            path.relative_to(ROOT).as_posix() for path in numbered_notes
        ] + [
            f"context/codebase/{number:02d}_{name}.md"
            for number, name in (
                (0, "CODEBASE_MAP"),
                (1, "FRONTEND_CODE_MAP"),
                (2, "BACKEND_CODE_MAP"),
                (3, "INFRASTRUCTURE_CODE_MAP"),
                (4, "CONTRACT_TRACEABILITY"),
            )
        ]
        for expected_file in expected_canvas_files:
            count = file_counts.get(expected_file.casefold(), 0)
            if count != 1:
                self.error(
                    CANVAS_PATH,
                    f"expected exactly one file node for '{expected_file}', found {count}",
                )

    def check_code_maps(self) -> None:
        expected_maps = {
            "00_CODEBASE_MAP.md",
            "01_FRONTEND_CODE_MAP.md",
            "02_BACKEND_CODE_MAP.md",
            "03_INFRASTRUCTURE_CODE_MAP.md",
            "04_CONTRACT_TRACEABILITY.md",
        }
        actual_maps = {path.name for path in (CONTEXT_DIR / "codebase").glob("*.md")}
        for missing in sorted(expected_maps - actual_maps):
            self.error(CONTEXT_DIR / "codebase", f"missing required code map '{missing}'")
        for unexpected in sorted(actual_maps - expected_maps):
            self.error(CONTEXT_DIR / "codebase", f"unexpected code map '{unexpected}'")

    def check_repository_boundary(self) -> None:
        requirements = {
            ROOT / "README.md": (
                "legacy v1 proof of concept",
                "target v2",
                "only the v2 phase 1 platform foundation is implemented",
                "v1 public flow remains the default",
                "context/00_index_and_project_overview.md",
                "context/codebase/00_codebase_map.md",
            ),
            ROOT / "ARCHITECTURE.md": (
                "legacy v1",
                "target v2",
                "only its phase 1 platform foundation is implemented",
                "still-default v1 product flow",
                "context/03_system_architecture.md",
            ),
        }
        for path, phrases in requirements.items():
            text = self.read(path).casefold()
            for phrase in phrases:
                if phrase not in text:
                    self.error(path, f"missing required V1/V2 boundary phrase '{phrase}'")

        roadmap_path = CONTEXT_DIR / "24_MIGRATION_AND_ROADMAP.md"
        roadmap = self.read(roadmap_path)
        if re.search(r"^##\s+Phase\s+\d+", roadmap, re.IGNORECASE | re.MULTILINE):
            self.error(roadmap_path, "defines numbered phases outside the single controller")
        if "single execution controller" not in roadmap.casefold():
            self.error(roadmap_path, "does not defer phase sequencing to the single controller")
        if "../REBUILD_PHASES.md" not in roadmap:
            self.error(roadmap_path, "does not link to REBUILD_PHASES.md")

    def run(self) -> int:
        numbered_notes = self.check_numbered_notes()
        self.check_obsolete_context_names()
        self.check_links()
        self.check_canvas(numbered_notes)
        self.check_code_maps()
        self.check_repository_boundary()

        unique_errors = sorted(set(self.errors), key=str.casefold)
        if unique_errors:
            print(f"Context audit failed with {len(unique_errors)} error(s):", file=sys.stderr)
            for message in unique_errors:
                print(f"- {message}", file=sys.stderr)
            return 1

        print(
            "Context audit passed: 28 Target V2 notes, documentation links, "
            "Canvas coverage, code maps, stale-term policy, and V1/V2 boundaries are valid."
        )
        return 0


def main() -> int:
    return ContextAudit().run()


if __name__ == "__main__":
    raise SystemExit(main())
