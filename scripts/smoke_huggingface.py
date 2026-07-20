#!/usr/bin/env python3
"""Exercise the local Hugging Face inference backends with tiny models.

This is a networked smoke test on its first run and an offline cache test on
subsequent runs. The default models are intentionally too small to assess output
quality; they only verify provider routing, model loading, chat inference and
the embedding interface used by the application.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat-model", default="sshleifer/tiny-gpt2")
    parser.add_argument(
        "--embedding-model",
        default="hf-internal-testing/tiny-random-bert",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Reject network access and require both models to be cached.",
    )
    return parser.parse_args()


def vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def main() -> int:
    args = parse_args()
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"

    import torch
    from langchain_core.messages import HumanMessage
    from shard.inference import (
        get_chat_llm,
        get_embedding_function,
    )
    from shard.inference.context import inference_config

    config = {"provider": "huggingface", "huggingface": {}}
    started = time.monotonic()

    with inference_config(config):
        chat = get_chat_llm(
            args.chat_model,
            kind="local-smoke",
            temperature=0.0,
            max_new_tokens=4,
        )
        response = chat.invoke([HumanMessage(content="Local inference smoke test")])
        content = str(getattr(response, "content", "")).strip()
        if not content:
            raise RuntimeError("The local chat backend returned an empty response.")

        embeddings = get_embedding_function(args.embedding_model)
        documents = embeddings.embed_documents([
            "An asset has a maintenance deadline.",
            "A railway track has a maximum speed.",
        ])
        query = embeddings.embed_query("maintenance task deadline")

    if len(documents) != 2 or not query:
        raise RuntimeError("The local embedding backend returned incomplete vectors.")
    if any(len(vector) != len(query) for vector in documents):
        raise RuntimeError("Document and query embedding dimensions do not match.")
    if not all(math.isfinite(value) for vector in [*documents, query] for value in vector):
        raise RuntimeError("The local embedding backend returned non-finite values.")
    print(f"device: {'cuda' if torch.cuda.is_available() else 'cpu'}")
    print(f"chat: ok ({args.chat_model}; {len(content)} output characters)")
    print(
        f"embeddings: ok ({args.embedding_model}; "
        f"2 documents; dimension {len(query)}; query norm {vector_norm(query):.4f})"
    )
    print(f"offline: {'yes' if args.offline else 'no'}")
    print(f"elapsed: {time.monotonic() - started:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
