"""Training routes — submit jobs and poll status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from tokenpal.server.models import TrainingJob, TrainRequest, TrainResponse
from tokenpal.server.worker import submit_training_job

router = APIRouter()


@router.post("/train", status_code=202)
async def start_training(req: TrainRequest, request: Request) -> TrainResponse:
    """Submit a training job. Returns 202 with job ID.

    Raises 409 if a training job is already running.
    """
    store = request.app.state.job_store

    try:
        job = await submit_training_job(
            wiki=req.wiki,
            character=req.character,
            base_model=req.base_model,
            store=store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return TrainResponse(job_id=job.job_id, status=job.status)


@router.get("/train/{job_id}")
async def get_training_status(job_id: str, request: Request) -> TrainingJob:
    """Poll the status of a training job."""
    store = request.app.state.job_store
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job
