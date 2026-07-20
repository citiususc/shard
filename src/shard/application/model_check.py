"""Check whether user-configured inference models are reachable."""

def validate_model(payload):
    """Lightweight availability check before a custom model is added in the UI."""
    provider = str(payload.get("provider") or "").strip().lower()
    model = str(payload.get("model") or "").strip()
    role = str(payload.get("role") or "chat").strip().lower()

    if not model:
        return {"ok": False, "message": "Enter a model id first."}
    if provider not in {"databricks", "huggingface"}:
        return {"ok": False, "message": "Choose remote or local inference first."}

    if provider == "databricks":
        import httpx
        from shard.inference.context import get_databricks_base_url, get_databricks_token
        from shard.inference.databricks import normalize_model_id

        model = normalize_model_id(model)
        token = get_databricks_token()
        base_url = get_databricks_base_url()
        if not token or not base_url:
            return {
                "ok": False,
                "message": "Remote inference is not configured on this deployment.",
            }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if role == "embedding":
            url = f"{base_url}/embeddings"
            body = {"model": model, "input": ["ping"]}
        else:
            url = f"{base_url}/chat/completions"
            body = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }

        try:
            res = httpx.post(url, headers=headers, json=body, timeout=20)
        except Exception as exc:
            return {"ok": False, "message": f"Could not reach remote inference: {exc}"}

        if 200 <= res.status_code < 300:
            return {"ok": True, "message": f"Model '{model}' is available."}
        detail = res.text[:500]
        return {
            "ok": False,
            "message": f"Remote inference rejected '{model}' ({res.status_code}): {detail}",
        }

    # Hugging Face: check repository visibility/access without downloading weights.
    try:
        from huggingface_hub import model_info
        from shard.inference.context import get_hf_token

        info = model_info(model, token=get_hf_token() or None)
        pipeline = getattr(info, "pipeline_tag", None)
        suffix = f" ({pipeline})" if pipeline else ""
        return {"ok": True, "message": f"Local model '{model}' is available{suffix}."}
    except Exception as exc:
        return {"ok": False, "message": f"Local model '{model}' is not available: {exc}"}
