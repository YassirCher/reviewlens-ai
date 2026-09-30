from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.analysis.registry import UNIVERSAL_POLICY, AgentSpec
from app.analysis.audit import CitedAuditorInput
from app.analysis.review import VideoExtraction
from app.analysis.synthesis import AtomicBuyingSynthesis, BuyingSynthesis, QuoteSynthesisInput, SourceBoundBuyingSynthesis
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
    combined = spec.output_model is VideoExtraction
    schema_in_response = combined or spec.output_model in {BuyingSynthesis, AtomicBuyingSynthesis, SourceBoundBuyingSynthesis} or "CITATION AUDIT:" in spec.role_prompt
    output_contract = (
        "STRICT OUTPUT SCHEMA\nReturn one object according to the supplied strict response_format JSON schema."
        if schema_in_response else
        "STRICT OUTPUT SCHEMA\n" + json.dumps(spec.output_model.model_json_schema(), sort_keys=True, separators=(",", ":"))
    )
    system = "\n\n".join(
        (
            f"{UNIVERSAL_POLICY}\n\n{spec.role_prompt}",
            "ROLE OBJECTIVE\n" + spec.purpose,
            "PROHIBITED BEHAVIOR\n- " + "\n- ".join(spec.prohibited_behaviors),
            output_contract,
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
    if spec.input_model in {QuoteSynthesisInput, CitedAuditorInput} and rendered_context == "<no-authorized-context />":
        # These successors carry their complete authorized evidence in task_input.
        # An absent extra retrieval packet must not imply that evidence is absent.
        rendered_context = ""
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
