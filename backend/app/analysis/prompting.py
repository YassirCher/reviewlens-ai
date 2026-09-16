from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.analysis.registry import AgentSpec
from app.runtime.contracts import canonical_json_hash


@dataclass(frozen=True)
class PromptEnvelope:
    system: str
    user: str
    prompt_hash: str


def build_prompt_envelope(
    spec: AgentSpec,
    *,
    task_instruction: str,
    task_input: dict[str, Any],
    context_manifest_id: str | None,
    rendered_context: str,
    correction: dict[str, Any] | None = None,
) -> PromptEnvelope:
    schema = spec.output_model.model_json_schema()
    system = "\n\n".join(
        (
            spec.persisted_payload()["system_prompt"],
            "ROLE OBJECTIVE\n" + spec.purpose,
            "PROHIBITED BEHAVIOR\n- " + "\n- ".join(spec.prohibited_behaviors),
            "STRICT OUTPUT SCHEMA\n" + json.dumps(schema, sort_keys=True, separators=(",", ":")),
            "TOOL CONTRACT\nAllowed immutable tools: " + (", ".join(spec.tool_keys) or "none"),
        )
    )
    trusted = {
        "task_instruction": task_instruction,
        "task_input": task_input,
        "context_manifest_id": context_manifest_id,
    }
    if correction:
        trusted["correction"] = correction
    user = (
        "<trusted-task>\n"
        + json.dumps(trusted, sort_keys=True, separators=(",", ":"), default=str)
        + "\n</trusted-task>\n"
        + "<untrusted-context>\n"
        + rendered_context
        + "\n</untrusted-context>"
    )
    return PromptEnvelope(
        system=system,
        user=user,
        prompt_hash=canonical_json_hash({"system": system, "user": user}),
    )
