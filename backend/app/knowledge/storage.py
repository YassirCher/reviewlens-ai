from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings, settings

REQUIRED_FRONTMATTER = frozenset(
    {
        "id",
        "workspace_id",
        "run_id",
        "type",
        "title",
        "status",
        "version",
        "source_uri",
        "source_language",
        "trust_level",
        "confidence",
        "tags",
        "created_at",
        "updated_at",
        "content_hash",
    }
)
SAFE_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


class MarkdownValidationError(ValueError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_body(body: str) -> str:
    value = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise MarkdownValidationError("node body cannot be blank")
    return value + "\n"


def body_hash(body: str) -> str:
    return f"sha256:{sha256_text(normalize_body(body))}"


def canonical_json_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_text(payload)


def _yaml_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def render_markdown(frontmatter: dict[str, Any], body: str) -> str:
    missing = REQUIRED_FRONTMATTER - frontmatter.keys()
    if missing:
        raise MarkdownValidationError(f"missing frontmatter fields: {', '.join(sorted(missing))}")
    lines = ["---"]
    lines.extend(f"{key}: {_yaml_scalar(value)}" for key, value in frontmatter.items())
    lines.extend(("---", "", normalize_body(body).rstrip("\n"), ""))
    return "\n".join(lines)


def parse_markdown(value: str) -> tuple[dict[str, Any], str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise MarkdownValidationError("Markdown node must start with YAML frontmatter")
    try:
        raw_frontmatter, raw_body = normalized[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise MarkdownValidationError("Markdown frontmatter is not terminated") from exc
    frontmatter: dict[str, Any] = {}
    for line_number, line in enumerate(raw_frontmatter.splitlines(), start=2):
        if not line or ":" not in line:
            raise MarkdownValidationError(f"invalid frontmatter line {line_number}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if not SAFE_SEGMENT.fullmatch(key):
            raise MarkdownValidationError(f"invalid frontmatter key at line {line_number}")
        if key in frontmatter:
            raise MarkdownValidationError(f"duplicate frontmatter key: {key}")
        try:
            frontmatter[key] = json.loads(raw_value.strip())
        except json.JSONDecodeError as exc:
            raise MarkdownValidationError(f"frontmatter value for {key} must be JSON-compatible YAML") from exc
    missing = REQUIRED_FRONTMATTER - frontmatter.keys()
    if missing:
        raise MarkdownValidationError(f"missing frontmatter fields: {', '.join(sorted(missing))}")
    body = normalize_body(raw_body)
    if frontmatter["content_hash"] != body_hash(body):
        raise MarkdownValidationError("Markdown content_hash does not match the body")
    for key in ("id", "workspace_id", "run_id"):
        try:
            uuid.UUID(str(frontmatter[key]))
        except (TypeError, ValueError) as exc:
            raise MarkdownValidationError(f"frontmatter {key} must be a UUID") from exc
    for key in ("created_at", "updated_at"):
        try:
            timestamp = datetime.fromisoformat(str(frontmatter[key]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise MarkdownValidationError(f"frontmatter {key} must be ISO-8601") from exc
        if timestamp.tzinfo is None:
            raise MarkdownValidationError(f"frontmatter {key} must include a timezone")
    return frontmatter, body


def workspace_root(workspace_id: uuid.UUID, config: Settings = settings) -> Path:
    root = Path(config.node_storage_root).resolve()
    candidate = (root / str(workspace_id)).resolve()
    if candidate.parent != root:
        raise MarkdownValidationError("workspace path escaped NODE_STORAGE_ROOT")
    return candidate


def version_relative_path(node_type: str, node_id: uuid.UUID, version_number: int) -> Path:
    if not SAFE_SEGMENT.fullmatch(node_type):
        raise MarkdownValidationError("unsafe node type path segment")
    if version_number < 1:
        raise MarkdownValidationError("node version must be positive")
    suffix = "" if version_number == 1 else f".v{version_number:06d}"
    return Path("nodes") / node_type / f"{node_id}{suffix}.md"


def resolve_body_path(root: Path, relative_path: str | Path) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise MarkdownValidationError("node body path must be workspace-relative")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise MarkdownValidationError("node body path escaped the workspace") from exc
    return candidate


def atomic_write(root: Path, relative_path: Path, content: str) -> Path:
    destination = resolve_body_path(root, relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = root / "temporary"
    temporary_root.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix="node-", suffix=".tmp", dir=temporary_root)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return destination


def utc_iso(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise MarkdownValidationError("timestamps must be timezone-aware")
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
