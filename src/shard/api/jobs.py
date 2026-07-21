"""Thread-safe in-memory jobs used by long-running local SHARD operations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import BoundedSemaphore, Event, RLock, Thread, Timer
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from shard.api.errors import CapacityExceeded, ConflictingJobState, ResourceNotFound
from shard.api.operational import OperationalSettings, operational_settings


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRegistry:
    """Store bounded process-local jobs with cooperative cancellation."""

    def __init__(
        self,
        *,
        max_completed: int = 100,
        settings: Optional[OperationalSettings] = None,
    ):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
        self._max_completed = max_completed
        self._settings = settings or operational_settings()
        self._job_slots = BoundedSemaphore(self._settings.max_concurrent_jobs)
        self._download_slots = BoundedSemaphore(
            self._settings.max_concurrent_model_downloads
        )

    def create(
        self,
        kind: str,
        worker: Callable[[str, Event, Callable[..., None]], Any],
        *,
        message: str,
    ) -> Dict[str, Any]:
        job_id = uuid4().hex
        created_at = _now()
        cancel_event = Event()
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 0.0,
            "message": message,
            "created_at": created_at,
            "updated_at": created_at,
            "error": None,
            "result": None,
            "cancel_event": cancel_event,
        }
        with self._lock:
            active = sum(
                1 for item in self._jobs.values()
                if item["status"] in ACTIVE_STATUSES
            )
            capacity = (
                self._settings.max_concurrent_jobs
                + self._settings.max_queued_jobs
            )
            if active >= capacity:
                raise CapacityExceeded(
                    "The asynchronous job queue is temporarily full.",
                    {
                        "limit": capacity,
                        "resource": "asynchronous_jobs",
                    },
                )
            self._jobs[job_id] = job
            self._prune()

        thread = Thread(
            target=self._run,
            args=(job_id, worker),
            daemon=True,
            name=f"shard-job-{kind}-{job_id[:8]}",
        )
        job["thread"] = thread
        thread.start()
        return self.get(job_id)

    def _run(self, job_id: str, worker) -> None:
        cancel_event = self._jobs[job_id]["cancel_event"]
        acquired_job = False
        acquired_download = False
        timer: Optional[Timer] = None
        try:
            acquired_job = self._acquire_slot(self._job_slots, cancel_event)
            if not acquired_job:
                return
            if self._jobs[job_id]["kind"] == "local-model-download":
                acquired_download = self._acquire_slot(
                    self._download_slots, cancel_event
                )
                if not acquired_download:
                    return
            with self._lock:
                if self._jobs[job_id]["status"] == "cancelled":
                    return
            self.update(job_id, status="running", message="Job is running.")
            timer = Timer(
                self._settings.job_max_runtime_seconds,
                self._expire,
                args=(job_id,),
            )
            timer.daemon = True
            timer.start()
            result = worker(
                job_id,
                cancel_event,
                lambda **values: self._progress_update(job_id, **values),
            )
            current = self.get(job_id)
            if current["status"] not in TERMINAL_STATUSES:
                self.update(
                    job_id,
                    status=("cancelled" if cancel_event.is_set() else "completed"),
                    progress=(current["progress"] if cancel_event.is_set() else 1.0),
                    message=("Job was cancelled." if cancel_event.is_set() else "Job completed."),
                    **({} if cancel_event.is_set() else {"result": result}),
                )
        except Exception:
            current = self.get(job_id)
            if current["status"] not in TERMINAL_STATUSES:
                self.update(
                    job_id,
                    status="failed",
                    message="Job failed.",
                    error={
                        "code": "JOB_EXECUTION_FAILED",
                        "message": "Job execution failed.",
                    },
                )
        finally:
            if timer is not None:
                timer.cancel()
            if acquired_download:
                self._download_slots.release()
            if acquired_job:
                self._job_slots.release()

    @staticmethod
    def _acquire_slot(semaphore: BoundedSemaphore, cancel_event: Event) -> bool:
        while not cancel_event.is_set():
            if semaphore.acquire(timeout=0.1):
                return True
        return False

    def _progress_update(self, job_id: str, **values: Any) -> Dict[str, Any]:
        with self._lock:
            if self._jobs[job_id]["status"] in TERMINAL_STATUSES:
                return self._public(self._jobs[job_id])
        return self.update(job_id, **values)

    def _expire(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] in TERMINAL_STATUSES:
                return
            job["cancel_event"].set()
            job.update({
                "status": "failed",
                "message": "Job exceeded its maximum runtime.",
                "updated_at": _now(),
                "error": {
                    "code": "JOB_RUNTIME_TIMEOUT",
                    "message": "Job exceeded its configured maximum runtime.",
                },
            })

    def update(self, job_id: str, **values: Any) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ResourceNotFound(
                    f"Job '{job_id}' was not found.",
                    {"job_id": job_id},
                    code="JOB_NOT_FOUND",
                )
            if "progress" in values:
                values["progress"] = max(0.0, min(1.0, float(values["progress"])))
            job.update(values)
            job["updated_at"] = _now()
            return self._public(job)

    def get(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ResourceNotFound(
                    f"Job '{job_id}' was not found.",
                    {"job_id": job_id},
                    code="JOB_NOT_FOUND",
                )
            return self._public(job)

    def cancel(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ResourceNotFound(
                    f"Job '{job_id}' was not found.",
                    {"job_id": job_id},
                    code="JOB_NOT_FOUND",
                )
            if job["status"] in TERMINAL_STATUSES:
                code = (
                    "JOB_ALREADY_COMPLETED"
                    if job["status"] == "completed"
                    else "JOB_ALREADY_TERMINAL"
                )
                raise ConflictingJobState(
                    f"Job '{job_id}' is already {job['status']}.",
                    {"job_id": job_id, "status": job["status"]},
                    code=code,
                )
            job["cancel_event"].set()
            job["status"] = "cancelled"
            job["message"] = "Cancellation requested."
            job["updated_at"] = _now()
            return self._public(job)

    def _public(self, job: Dict[str, Any]) -> Dict[str, Any]:
        return deepcopy({
            key: value
            for key, value in job.items()
            if key not in {"cancel_event", "thread", "kind", "result"}
        })

    def _prune(self) -> None:
        terminal = [
            job for job in self._jobs.values() if job["status"] in TERMINAL_STATUSES
        ]
        terminal.sort(key=lambda item: item["updated_at"], reverse=True)
        for job in terminal[self._max_completed:]:
            self._jobs.pop(job["job_id"], None)


JOBS = JobRegistry()
