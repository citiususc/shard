#!/usr/bin/env python3
"""Reproducible rule-target resolver checks for non-generation validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(ROOT / "text2shacl_core"))

from find_relevant_terms import embedding_status, prepare_embeddings  # noqa: E402
from model_loader import get_chat_llm  # noqa: E402
from parse_ontology import parse_ontology  # noqa: E402
from rule_target_resolver import resolve_template  # noqa: E402
from runtime_config import inference_config  # noqa: E402


SEMANTIC_TARGETS = {
    "BR-011": [("ex:serialNumber", 0.91)],
    "BR-012": [("ex:dueDate", 0.90), ("ex:MaintenanceTask", 0.80)],
    "RINF-001": [("era:maximumPermittedSpeed", 0.91), ("era:RunningTrack", 0.80)],
    "RINF-002": [("era:contactLineSystemType", 0.90), ("era:RunningTrack", 0.78), ("era:ContactLineSystem", 0.76)],
    "RINF-003": [("era:trainDetectionSystemType", 0.90), ("era:RunningTrack", 0.78), ("era:TrainDetectionSystem", 0.76)],
}

LLM_TARGETS = {
    "BR-013": ["ex:Inspection", "ex:inspectionDate", "ex:inspectsAsset", "ex:Asset"],
    "RINF-006": ["era:gsmRVersion", "era:RunningTrack"],
}


def injected_semantic_ranker(rule, ontology_terms, _payload, _top_k):
    """Deterministic semantic-ranker stand-in for exercising cascade control flow."""
    requested = SEMANTIC_TARGETS.get(rule.number, [])
    out = []
    for target, score in requested:
        out.append({
            "target": target,
            "score": score,
            "reasons": ["injected semantic similarity for resolver cascade test"],
        })
    return out


def injected_llm(payload):
    """Deterministic LLM stand-in: can only select from the provided target list."""
    allowed = set(payload.get("allowed_targets") or [])
    return [target for target in LLM_TARGETS.get(payload.get("rule_number"), []) if target in allowed]


def inference_config_from_env():
    """Build the same non-global config shape that the UI sends to services."""
    provider = os.environ.get("BR2SHACL_PROVIDER", "databricks").strip().lower()
    return {
        "provider": provider,
        "databricks": {
            "base_url": os.environ.get("DATABRICKS_BASE_URL", "").strip().rstrip("/"),
            "token": os.environ.get("DATABRICKS_TOKEN", "").strip(),
        },
        "huggingface": {
            "token": os.environ.get("HF_TOKEN", "").strip(),
        },
    }


def real_payload_options():
    """Options needed by the real semantic ranker."""
    return {
        "provider": os.environ.get("BR2SHACL_PROVIDER", "databricks").strip().lower(),
        "embedding_model": os.environ.get("BR2SHACL_EMBEDDING_MODEL", "qwen3-embedding-0-6b").strip(),
        "inference_config": inference_config_from_env(),
        "strict_semantic": True,
    }


def _parse_llm_selection(content: str):
    text = str(content or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise ValueError(f"LLM fallback did not return a JSON array: {text[:500]}")
        parsed = json.loads(match.group(0))

    if isinstance(parsed, dict):
        parsed = parsed.get("targets") or parsed.get("selected_targets") or []
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise ValueError(f"LLM fallback returned unsupported JSON: {parsed!r}")
    return [str(item) for item in parsed]


def real_llm_fallback(payload):
    """Ask the configured chat model to choose targets from the allowed list."""
    model_id = os.environ.get("BR2SHACL_LLM_MODEL", "gemma-3-12b").strip()
    candidates = payload.get("allowed_candidates") or [
        {"target": target} for target in payload.get("allowed_targets") or []
    ]
    candidate_lines = "\n".join(
        "- {target} | type={type} | label={label} | domain={domain} | range={range} | score={score} | reasons={reasons}".format(
            target=item.get("target", ""),
            type=item.get("type", ""),
            label=item.get("label", ""),
            domain=item.get("domain", ""),
            range=item.get("range", ""),
            score=item.get("score", ""),
            reasons="; ".join(item.get("reasons") or []),
        )
        for item in candidates
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You resolve a business rule to ontology targets. "
                "Choose only from the provided target IRIs. "
                "Return a JSON array of selected target strings and nothing else. "
                "Return [] if none are justified."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Rule number: {payload.get('rule_number', '')}\n"
                f"Title: {payload.get('title', '')}\n"
                f"Rule text:\n{payload.get('text', '')}\n\n"
                f"Allowed candidates:\n{candidate_lines}\n"
            ),
        },
    ]
    model = get_chat_llm(model_id, kind="resolver", temperature=0.0, max_new_tokens=500)
    result = model.invoke(messages)
    return _parse_llm_selection(getattr(result, "content", result))


def wait_for_embeddings(payload, *, timeout_seconds: int = 900, poll_seconds: float = 2.0):
    """Synchronously prepare ontology embeddings for CLI evaluation."""
    ontology_content = payload["ontology_content"]
    payload["ontology_hash"] = hashlib.sha1(ontology_content.encode("utf-8")).hexdigest()
    ontology = parse_ontology(payload["ontology_filename"], ontology_content)
    terms = ontology.get("entities") or []
    semantic_payload = {
        **payload,
        "ontology_terms": terms,
    }

    started = time.monotonic()
    last_line = ""
    config = payload.get("inference_config") or {}
    with inference_config(config):
        result = prepare_embeddings(semantic_payload)
        status = result.get("status", "unknown")
        if status in {"disabled", "error", "cancelled"}:
            raise RuntimeError(result.get("message") or f"Embedding preparation failed: {result}")

        while True:
            current = embedding_status(semantic_payload)
            status = current.get("status", "unknown")
            completed = current.get("completed", 0)
            total = current.get("total", len(terms))
            message = current.get("message", "")
            line = f"embeddings {status}: {completed}/{total} — {message}"
            if line != last_line:
                print(line, flush=True)
                last_line = line

            if status == "ready":
                return current
            if status in {"disabled", "error", "cancelled"}:
                raise RuntimeError(message or f"Embedding preparation failed: {current}")
            if time.monotonic() - started > timeout_seconds:
                raise TimeoutError(
                    f"Timed out waiting for ontology embeddings after {timeout_seconds}s. "
                    f"Last status: {current}"
                )
            time.sleep(poll_seconds)


def _split_rule_ids(value: str):
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def print_semantic_debug(result, *, rule_ids, top_k):
    if not rule_ids or top_k <= 0:
        return
    for row in result["rules"]:
        if row["rule_number"] not in rule_ids:
            continue
        semantic = (row.get("signal_candidates") or {}).get("semantic") or []
        print(f"         {row['rule_number']} semantic raw top-{top_k}:")
        if not semantic:
            print("           (no semantic candidates)")
            continue
        for idx, item in enumerate(semantic[:top_k], start=1):
            reasons = "; ".join(item.get("reasons") or [])
            print(
                "           {idx}. score={score:.3f} target={target} type={type} "
                "label={label} reason={reason}".format(
                    idx=idx,
                    score=float(item.get("score", 0)),
                    target=item.get("target", ""),
                    type=item.get("type", ""),
                    label=item.get("label", ""),
                    reason=reasons,
                )
            )


def run_case(
    label,
    ontology_path,
    guide_path,
    *,
    injected=False,
    real=False,
    wait_embeddings=False,
    timeout_seconds=900,
    semantic_threshold=None,
    llm_fallback=False,
    debug_rule_ids=None,
    debug_semantic_top_k=0,
):
    payload = {
        "ontology_content": Path(ontology_path).read_text(),
        "ontology_filename": Path(ontology_path).name,
        "guide_content": Path(guide_path).read_text(),
        "guide_filename": Path(guide_path).name,
        "top_k": 50,
    }
    if real:
        payload.update(real_payload_options())
    if semantic_threshold is not None:
        payload["semantic_threshold"] = float(semantic_threshold)
    if real and wait_embeddings:
        wait_for_embeddings(payload, timeout_seconds=timeout_seconds)
    llm = None
    if injected:
        llm = injected_llm
    elif real and llm_fallback:
        llm = real_llm_fallback
    if real:
        with inference_config(payload.get("inference_config") or {}):
            result = resolve_template(
                payload,
                semantic_ranker=injected_semantic_ranker if injected else None,
                llm=llm,
            )
    else:
        result = resolve_template(
            payload,
            semantic_ranker=injected_semantic_ranker if injected else None,
            llm=llm,
        )
    print(f"\n## {label}")
    print(result["summary"])
    for row in result["rules"]:
        details = ", ".join(f"{item['target']}({item['type']})" for item in row["target_details"])
        print(f"{row['rule_number']:>8} | {row['resolved_by']:<8} | {row['confidence']} | {details}")
        if row["candidates"]:
            print(f"         reason: {row['candidates'][0]['reasons'][0]}")
    print_semantic_debug(result, rule_ids=debug_rule_ids or set(), top_k=debug_semantic_top_k)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("real", "injected", "all"),
        default="all",
        help="real uses rank_semantic; injected uses deterministic test doubles.",
    )
    parser.add_argument(
        "--case",
        choices=("asset", "era", "all"),
        default="all",
        help="Fixture set to run.",
    )
    parser.add_argument(
        "--wait-embeddings",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="In real mode, block until ontology embeddings are ready before resolving.",
    )
    parser.add_argument(
        "--embedding-timeout",
        type=int,
        default=900,
        help="Maximum seconds to wait for ontology embeddings in real mode.",
    )
    parser.add_argument(
        "--semantic-threshold",
        type=float,
        default=float(os.environ.get("BR2SHACL_SEMANTIC_THRESHOLD", "0.74")),
        help="Semantic confidence threshold. Default: 0.74.",
    )
    parser.add_argument(
        "--llm-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="In real mode, call the configured chat model when semantic+label do not resolve.",
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("BR2SHACL_LLM_MODEL", "gemma-3-12b"),
        help="Chat model for real LLM fallback. Also exported as BR2SHACL_LLM_MODEL.",
    )
    parser.add_argument(
        "--debug-rules",
        default="BR-011,BR-012,BR-013,RINF-001,RINF-006",
        help="Comma-separated rule ids for raw semantic top-k diagnostics.",
    )
    parser.add_argument(
        "--debug-semantic-top-k",
        type=int,
        default=5,
        help="Print raw semantic candidates for --debug-rules.",
    )
    args = parser.parse_args()
    os.environ["BR2SHACL_LLM_MODEL"] = args.llm_model

    cases = [
        ("asset md", "asset", "test/asset_maintenance_ontology.ttl", "test/business_rules_example.md"),
        ("era-rinf md", "era", "test/era_rinf_subset_ontology.ttl", "test/era_rinf_business_rules_example.md"),
    ]
    debug_rule_ids = _split_rule_ids(args.debug_rules)
    try:
        for label, case_name, ontology, guide in cases:
            if args.case != "all" and args.case != case_name:
                continue
            if args.mode in {"real", "all"}:
                run_case(
                    f"{label} / real rank_semantic",
                    ontology,
                    guide,
                    real=True,
                    wait_embeddings=args.wait_embeddings,
                    timeout_seconds=args.embedding_timeout,
                    semantic_threshold=args.semantic_threshold,
                    llm_fallback=args.llm_fallback,
                    debug_rule_ids=debug_rule_ids,
                    debug_semantic_top_k=args.debug_semantic_top_k,
                )
            if args.mode in {"injected", "all"}:
                run_case(
                    f"{label} / injected semantic+llm",
                    ontology,
                    guide,
                    injected=True,
                    semantic_threshold=args.semantic_threshold,
                    debug_rule_ids=debug_rule_ids,
                    debug_semantic_top_k=args.debug_semantic_top_k,
                )
    except Exception as exc:
        raise SystemExit(f"ERROR: {exc}") from exc


if __name__ == "__main__":
    main()
