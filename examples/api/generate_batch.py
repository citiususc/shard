#!/usr/bin/env python3
"""Generate consolidated SHACL shapes through the SHARD REST API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASSET_EXAMPLE = ROOT / "examples" / "asset-maintenance"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8768/api/v1")
    parser.add_argument("--ontology", type=Path, default=ASSET_EXAMPLE / "ontology.ttl")
    parser.add_argument("--batch", type=Path, default=ASSET_EXAMPLE / "business-rules.md")
    parser.add_argument("--context", type=Path, default=ASSET_EXAMPLE / "generation-context.md")
    parser.add_argument("--profile", action="append", type=Path, default=[])
    parser.add_argument("--provider", choices=("databricks", "huggingface"), default="databricks")
    parser.add_argument(
        "--astrea-mode",
        choices=("none", "evidence", "merge", "evidence-and-merge"),
        default="none",
    )
    parser.add_argument(
        "--merge-strategy",
        choices=("generated-priority", "restrictive"),
        default="generated-priority",
    )
    parser.add_argument("--output", type=Path, default=Path("shard-batch-shapes.ttl"))
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
    profiles = [
        {"name": path.name, "content": path.read_text(encoding="utf-8")}
        for path in args.profile
    ]
    payload = {
        "ontology": {
            "filename": args.ontology.name,
            "content": args.ontology.read_text(encoding="utf-8"),
        },
        "batch": {
            "filename": args.batch.name,
            "content": args.batch.read_text(encoding="utf-8"),
        },
        "inference": inference_config(args.provider),
        "generation": {
            "domain_context": args.context.read_text(encoding="utf-8") if args.context else "",
        },
        "resolver": {
            "semantic_threshold": 0.60,
            "semantic_target_margin": 0.16,
            "semantic_max_targets": 4,
            "llm_fallback": True,
            "wait_embeddings": True,
            "embedding_timeout": args.timeout,
        },
        "validation": {"profiles": profiles},
        "astrea": {
            "mode": args.astrea_mode,
            "merge_strategy": args.merge_strategy,
            "failure_policy": "continue",
        },
    }
    request = urllib.request.Request(
        f"{args.api_url.rstrip('/')}/workflows/batch-to-shapes",
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
    unresolved = [
        (item.get("rule") or {}).get("number")
        for item in result.get("unresolved_rules") or []
    ]
    print(json.dumps({
        "request_id": result.get("request_id"),
        "summary": result.get("summary") or {},
        "unresolved_rules": unresolved,
        "astrea": result.get("astrea") or {},
        "merged": result.get("merge") is not None,
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
