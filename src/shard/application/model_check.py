"""Check whether user-configured inference models are reachable."""

from shard.deployment.operational import operational_settings

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
                "error_code": "provider_unavailable",
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
            settings = operational_settings()
            res = httpx.post(
                url,
                headers=headers,
                json=body,
                timeout=httpx.Timeout(
                    connect=settings.http_connect_timeout_seconds,
                    read=settings.model_timeout_seconds,
                    write=settings.http_read_timeout_seconds,
                    pool=settings.http_connect_timeout_seconds,
                ),
            )
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error_code": "provider_timeout",
                "message": "Remote inference did not respond before the timeout.",
            }
        except httpx.HTTPError:
            return {
                "ok": False,
                "error_code": "provider_unavailable",
                "message": "Remote inference could not be reached.",
            }

        if 200 <= res.status_code < 300:
            return {"ok": True, "message": f"Model '{model}' is available."}
        error_code = "model_unavailable"
        if res.status_code in {401, 403}:
            error_code = "provider_authentication_failed"
        elif res.status_code == 429:
            error_code = "rate_limited"
        elif res.status_code >= 500:
            error_code = "provider_unavailable"
        return {
            "ok": False,
            "error_code": error_code,
            "upstream_status": res.status_code,
            "message": f"Remote inference could not use model '{model}'.",
        }

    # Hugging Face: check repository visibility/access without downloading weights.
    try:
        from huggingface_hub import model_info
        from shard.inference.context import get_hf_token

        info = model_info(model, token=get_hf_token() or None)
        pipeline = getattr(info, "pipeline_tag", None)
        suffix = f" ({pipeline})" if pipeline else ""
        return {"ok": True, "message": f"Local model '{model}' is available{suffix}."}
    except Exception:
        return {
            "ok": False,
            "error_code": "model_unavailable",
            "message": f"Local model '{model}' is not available.",
        }
