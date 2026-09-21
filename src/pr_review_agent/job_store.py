"""Job Store (docs/specs/memory-context.md — Session state).

PoC implementation: in-memory, process-lifetime storage. A production
deployment would back this with SQLite/Redis as noted in the spec; the
interface here is small enough to swap without touching the orchestrator.
"""
from __future__ import annotations

import threading
import uuid

from .models import Job, JobStatus


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, pr_ref: str) -> Job:
        job = Job(job_id=str(uuid.uuid4()), pr_ref=pr_ref, status=JobStatus.QUEUED)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job: Job) -> None:
        job.touch()
        with self._lock:
            self._jobs[job.job_id] = job

    def set_status(self, job_id: str, status: JobStatus, error: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = status
            if error is not None:
                job.error = error
            job.touch()
