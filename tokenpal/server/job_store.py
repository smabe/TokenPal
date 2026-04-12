"""Training job state persistence — JSON files on disk."""

from __future__ import annotations

import abc
import json
import logging
import os
import tempfile
from pathlib import Path

from tokenpal.server.models import TrainingJob, TrainingStatus

log = logging.getLogger(__name__)

_ACTIVE_STATUSES = {
    TrainingStatus.QUEUED,
    TrainingStatus.FETCHING,
    TrainingStatus.PREPARING,
    TrainingStatus.TRAINING,
    TrainingStatus.MERGING,
    TrainingStatus.REGISTERING,
}


class AbstractJobStore(abc.ABC):
    @abc.abstractmethod
    def put(self, job: TrainingJob) -> None: ...

    @abc.abstractmethod
    def get(self, job_id: str) -> TrainingJob | None: ...

    @abc.abstractmethod
    def get_active(self) -> TrainingJob | None: ...

    @abc.abstractmethod
    def list_recent(self, limit: int = 20) -> list[TrainingJob]: ...


class JsonFileJobStore(AbstractJobStore):
    """Persist jobs as individual JSON files. Survives restarts, human-debuggable."""

    def __init__(self, jobs_dir: Path) -> None:
        self._dir = jobs_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def put(self, job: TrainingJob) -> None:
        path = self._dir / f"{job.job_id}.json"
        data = job.model_dump_json(indent=2)
        # Atomic write: temp file + rename
        fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, job_id: str) -> TrainingJob | None:
        path = self._dir / f"{job_id}.json"
        if not path.exists():
            return None
        job = self._get_from_path(path)
        if job is None:
            log.warning("Corrupt job file: %s", path)
        return job

    def get_active(self) -> TrainingJob | None:
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            job = self._get_from_path(path)
            if job and job.status in _ACTIVE_STATUSES:
                return job
        return None

    def list_recent(self, limit: int = 20) -> list[TrainingJob]:
        jobs: list[TrainingJob] = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            job = self._get_from_path(path)
            if job:
                jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def _get_from_path(self, path: Path) -> TrainingJob | None:
        try:
            return TrainingJob.model_validate_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            return None

    def recover_stale_jobs(self) -> None:
        """Mark any 'running' jobs as failed on startup (server crashed during training)."""
        for path in self._dir.glob("*.json"):
            job = self._get_from_path(path)
            if job and job.status in _ACTIVE_STATUSES:
                log.warning(
                    "Recovering stale job %s (was %s). Server likely crashed during training.",
                    job.job_id, job.status.value,
                )
                job.status = TrainingStatus.FAILED
                job.error = "Server restarted during training"
                job.error_hint = (
                    "The server process exited while this job was running. "
                    "Re-run the training command to retry."
                )
                self.put(job)
