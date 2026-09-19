from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings, settings
from app.db.models import EmbeddingPolicyVersion, ModelPolicyVersion
from app.llmops.catalog import current_endpoints, search_models
from app.llmops.contracts import EmbeddingPolicyDocument, ModelPolicyDocument
from app.runtime.contracts import canonical_json_hash


class PolicyCompatibilityError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _eligible_endpoint(endpoint: dict[str, Any], policy: ModelPolicyDocument) -> bool:
    return not endpoint_eligibility_reasons(endpoint, policy)


def endpoint_eligibility_reasons(endpoint: dict[str, Any], policy: ModelPolicyDocument) -> list[str]:
    routing = policy.provider
    reasons: list[str] = []
    if endpoint["provider_slug"] == "unknown":
        reasons.append("provider_unknown")
    if endpoint.get("status") in {"unavailable", "offline", "disabled"}:
        reasons.append("endpoint_unavailable")
    if routing.mode == "restricted" and endpoint["provider_slug"] not in routing.only:
        reasons.append("provider_not_allowed")
    if policy.compatibility_mode == "strict" and "response_format" not in endpoint["supported_parameters"]:
        reasons.append("strict_json_unsupported")
    required_capacity = policy.minimum_context_tokens + policy.max_completion_tokens
    if endpoint["context_length"] is not None and endpoint["context_length"] < required_capacity:
        reasons.append("context_too_small")
    if endpoint["max_completion_tokens"] is not None and endpoint["max_completion_tokens"] < policy.max_completion_tokens:
        reasons.append("completion_too_small")
    if routing.quantizations and endpoint["quantization"] not in routing.quantizations:
        reasons.append("quantization_not_allowed")
    if routing.data_collection == "deny" and endpoint["privacy"].get("data_collection") in {True, "allow"}:
        reasons.append("privacy_policy_conflict")
    for price_kind, ceiling_per_million in routing.max_price.items():
        raw_price = endpoint["pricing"].get(price_kind)
        if raw_price is None:
            reasons.append(f"{price_kind}_price_missing")
            continue
        try:
            actual_per_million = Decimal(str(raw_price)) * Decimal(1_000_000)
        except (InvalidOperation, TypeError, ValueError):
            reasons.append(f"{price_kind}_price_invalid")
            continue
        if not actual_per_million.is_finite() or actual_per_million > ceiling_per_million:
            reasons.append(f"{price_kind}_price_over_cap")
    return reasons


def validate_model_policy(
    db: Session,
    policy: ModelPolicyDocument,
    *,
    acknowledge_stale: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    errors: list[str] = []
    catalog = search_models(
        db,
        model_kind="chat",
        acknowledge_stale=acknowledge_stale,
        config=config,
    )
    if catalog["status"] == "missing":
        errors.append("chat model catalog is missing")
    elif catalog["stale"] and not acknowledge_stale:
        errors.append("chat model catalog is stale and was not acknowledged")
    available = {item["slug"]: item for item in catalog["models"]}
    eligible: dict[str, list[str]] = {}
    for slug in policy.models:
        model = available.get(slug)
        if model is None:
            errors.append(f"model is unavailable: {slug}")
            continue
        if "text" not in model["output_modalities"]:
            errors.append(f"model does not produce text: {slug}")
        endpoints = current_endpoints(db, slug, config)
        if endpoints["status"] == "missing":
            errors.append(f"endpoint snapshot is missing: {slug}")
            continue
        if endpoints["stale"] and not acknowledge_stale:
            errors.append(f"endpoint snapshot is stale: {slug}")
            continue
        routes = [item["provider_slug"] for item in endpoints["endpoints"] if _eligible_endpoint(item, policy)]
        if not routes:
            errors.append(f"no eligible endpoint remains: {slug}")
        else:
            eligible[slug] = routes
    if errors:
        raise PolicyCompatibilityError(errors)
    return {"valid": True, "stale_acknowledged": bool(catalog["stale"]), "eligible_routes": eligible}


def validate_embedding_policy(
    db: Session,
    policy: EmbeddingPolicyDocument,
    *,
    acknowledge_stale: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    catalog = search_models(
        db,
        model_kind="embedding",
        acknowledge_stale=acknowledge_stale,
        config=config,
    )
    errors: list[str] = []
    if catalog["status"] == "missing":
        errors.append("embedding model catalog is missing")
    elif catalog["stale"] and not acknowledge_stale:
        errors.append("embedding model catalog is stale and was not acknowledged")
    model = next((item for item in catalog["models"] if item["slug"] == policy.model), None)
    if model is None:
        errors.append(f"embedding model is unavailable: {policy.model}")
    elif "embeddings" not in model["output_modalities"]:
        errors.append(f"model does not produce embeddings: {policy.model}")
    elif policy.dimensions is not None:
        if "dimensions" not in model["supported_parameters"]:
            errors.append(f"embedding model does not support requested dimensions: {policy.model}")
        advertised_dimensions = (
            model["top_provider"].get("max_dimensions")
            or model["architecture"].get("max_dimensions")
            or model["architecture"].get("dimensions")
        )
        if isinstance(advertised_dimensions, int) and policy.dimensions > advertised_dimensions:
            errors.append(f"embedding dimensions exceed model capacity: {policy.model}")
    if errors:
        raise PolicyCompatibilityError(errors)
    return {"valid": True, "stale_acknowledged": bool(catalog["stale"]), "model": policy.model}


def publish_model_policy(
    db: Session,
    version: ModelPolicyVersion,
    *,
    acknowledge_stale: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    if version.lifecycle != "draft":
        raise ValueError("only draft model policies can be published")
    policy = ModelPolicyDocument.model_validate(version.policy)
    expected_hash = canonical_json_hash(policy.model_dump(mode="json"))
    if version.content_hash != expected_hash:
        raise ValueError("model policy content hash does not match its validated document")
    validation = validate_model_policy(db, policy, acknowledge_stale=acknowledge_stale, config=config)
    version.lifecycle = "published"
    version.published_at = datetime.now(timezone.utc)
    return validation


def publish_embedding_policy(
    db: Session,
    version: EmbeddingPolicyVersion,
    *,
    acknowledge_stale: bool = False,
    config: Settings = settings,
) -> dict[str, Any]:
    if version.lifecycle != "draft":
        raise ValueError("only draft embedding policies can be published")
    policy = EmbeddingPolicyDocument(
        model=version.model_slug or "",
        provider=version.provider_policy,
        dimensions=version.dimensions,
    )
    validation = validate_embedding_policy(db, policy, acknowledge_stale=acknowledge_stale, config=config)
    version.lifecycle = "published"
    version.published_at = datetime.now(timezone.utc)
    return validation
