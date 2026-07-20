"""Inspect and explicitly populate the local model snapshot cache."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict

from shard.inference.context import get_hf_token


MODEL_IGNORE_PATTERNS = (
        "*.msgpack",
    "*.h5",
    "flax_model*",
    "tf_model*",
    "original/*",
)

ProgressCallback = Callable[[Dict[str, Any]], None]
_DOWNLOAD_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class LocalModelNotDownloaded(RuntimeError):
    """Raised when local inference is requested before explicit download."""


def _model_id(value: Any) -> str:
    model_id = str(value or "").strip()
    if not model_id or "/" not in model_id:
        raise ValueError("A repository-style local model id is required.")
    return model_id


def _download_lock(model_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _DOWNLOAD_LOCKS.setdefault(model_id, threading.Lock())


def cached_model_path(model_id: str) -> str:
    """Return a complete cached snapshot without performing network access."""
    from huggingface_hub import snapshot_download

    normalized = _model_id(model_id)
    try:
        return str(snapshot_download(
            repo_id=normalized,
            token=get_hf_token() or None,
            ignore_patterns=list(MODEL_IGNORE_PATTERNS),
            local_files_only=True,
        ))
    except Exception as exc:
        raise LocalModelNotDownloaded(
            f"Local model '{normalized}' has not been downloaded."
        ) from exc


def local_model_status(model_id: str) -> Dict[str, Any]:
    """Report whether a complete model snapshot is available locally."""
    normalized = _model_id(model_id)
    try:
        cached_model_path(normalized)
    except LocalModelNotDownloaded:
        return {
            "model": normalized,
            "downloaded": False,
            "status": "not-downloaded",
            "message": "Not downloaded locally.",
        }
    return {
        "model": normalized,
        "downloaded": True,
        "status": "ready",
        "message": "Downloaded locally.",
    }


def _progress_class(callback: ProgressCallback):
    from tqdm.auto import tqdm

    class SnapshotProgress(tqdm):
        """Forward the aggregate snapshot file counter to an SSE callback."""

        def __init__(self, *args, **kwargs):
            self._shard_track = str(kwargs.get("desc") or "").startswith("Fetching")
            super().__init__(*args, **kwargs)
            self._shard_track = self._shard_track or str(self.desc or "").startswith("Fetching")

        def update(self, n=1):
            displayed = super().update(n)
            if self._shard_track and self.total:
                current = min(float(self.n), float(self.total))
                callback({
                    "type": "progress",
                    "current": int(current),
                    "total": int(self.total),
                    "percent": round((current / float(self.total)) * 100, 1),
                    "message": f"Downloaded {int(current)} of {int(self.total)} files.",
                })
            return displayed

    return SnapshotProgress


def download_local_model(
    model_id: str,
    callback: ProgressCallback,
) -> Dict[str, Any]:
    """Download one model snapshot after the caller has obtained user consent."""
    from huggingface_hub import snapshot_download

    normalized = _model_id(model_id)
    callback({
        "type": "status",
        "status": "checking",
        "model": normalized,
        "percent": 0,
        "message": "Checking the local model cache.",
    })
    lock = _download_lock(normalized)
    if not lock.acquire(blocking=False):
        callback({
            "type": "status",
            "status": "waiting",
            "model": normalized,
            "percent": 0,
            "message": "Waiting for an existing download of this model.",
        })
        lock.acquire()
    try:
        cached = local_model_status(normalized)
        if cached["downloaded"]:
            result = {**cached, "type": "done", "percent": 100}
            callback(result)
            return result

        token = get_hf_token() or None
        files = snapshot_download(
            repo_id=normalized,
            token=token,
            ignore_patterns=list(MODEL_IGNORE_PATTERNS),
            dry_run=True,
        )
        pending = [item for item in files if getattr(item, "will_download", True)]
        total_bytes = sum(int(getattr(item, "file_size", 0) or 0) for item in pending)
        callback({
            "type": "start",
            "status": "downloading",
            "model": normalized,
            "current": 0,
            "total": len(pending),
            "total_bytes": total_bytes,
            "percent": 0,
            "message": f"Downloading {len(pending)} model file(s).",
        })
        snapshot_path = snapshot_download(
            repo_id=normalized,
            token=token,
            ignore_patterns=list(MODEL_IGNORE_PATTERNS),
            tqdm_class=_progress_class(callback),
        )
        if not Path(snapshot_path).is_dir():
            raise RuntimeError("The model download did not produce a snapshot directory.")
        result = {
            "type": "done",
            "model": normalized,
            "downloaded": True,
            "status": "ready",
            "percent": 100,
            "message": "Downloaded locally.",
        }
        callback(result)
        return result
    finally:
        lock.release()
