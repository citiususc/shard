#!/usr/bin/env python3
"""Generate one SHACL document through the SHARD REST API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ONTOLOGY = ROOT / "examples" / "asset-maintenance" / "ontology.ttl"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8768/api/v1")
    parser.add_argument("--ontology", type=Path, default=DEFAULT_ONTOLOGY)
    parser.add_argument(
        "--rule",
        default="Every asset must have exactly one asset identifier.",
    )
    parser.add_argument("--number", default="BR-API-001")
    parser.add_argument("--title", default="Asset identifier")
    parser.add_argument("--provider", choices=("databricks", "huggingface"), default="databricks")
    parser.add_argument("--output", type=Path, default=Path("shard-rule-shape.ttl"))
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def inference_config(provider: str):
    if provider == "huggingface":
        return {
            "provider": provider,
            "generation_model": os.environ.get("SHARD_LLM_MODEL", "HuggingFaceTB/SmolLM2-135M-Instruct"),
            "embedding_model": os.environ.get("SHARD_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            "temperature": 0.2,
            "huggingface": {"token": os.environ.get("HF_TOKEN", "")},
        }

    base_url = os.environ.get("DATABRICKS_BASE_URL", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not base_url or not token:
        raise SystemExit("DATABRICKS_BASE_URL and DATABRICKS_TOKEN are required.")
    return {
        "provider": provider,
        "generation_model": os.environ.get("SHARD_LLM_MODEL", "gemma-3-12b"),
        "embedding_model": os.environ.get("SHARD_EMBEDDING_MODEL", "qwen3-embedding-0-6b"),
        "temperature": 0.2,
        "databricks": {"base_url": base_url, "token": token},
    }


def main():
    args = parse_args()
    payload = {
        "ontology": {
            "filename": args.ontology.name,
            "content": args.ontology.read_text(encoding="utf-8"),
        },
        "rule": {
            "number": args.number,
            "title": args.title,
            "text": args.rule,
        },
        "inference": inference_config(args.provider),
        "resolver": {
            "semantic_threshold": 0.60,
            "semantic_target_margin": 0.16,
            "semantic_max_targets": 4,
            "llm_fallback": True,
            "wait_embeddings": True,
            "embedding_timeout": args.timeout,
        },
        "astrea": {"mode": "none"},
    }
    request = urllib.request.Request(
        f"{args.api_url.rstrip('/')}/workflows/rule-to-shape",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"SHARD returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach SHARD at {args.api_url}: {exc.reason}") from exc

    args.output.write_text(result.get("final_shape_document", ""), encoding="utf-8")
    resolution = result.get("rule") or {}
    print(json.dumps({
        "request_id": result.get("request_id"),
        "resolved_by": resolution.get("resolved_by"),
        "resolution_score": resolution.get("resolution_score"),
        "score_kind": resolution.get("score_kind"),
        "targets": resolution.get("selected_targets") or [],
        "unresolved": result.get("unresolved"),
        "summary": result.get("summary") or {},
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
