"""Pydantic request/response models for the TokenPal server API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class TrainingStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    PREPARING = "preparing"
    TRAINING = "training"
    MERGING = "merging"
    REGISTERING = "registering"
    COMPLETE = "complete"
    FAILED = "failed"


class TrainRequest(BaseModel):
    wiki: str = Field(
        ..., min_length=1, pattern=r"^[a-zA-Z0-9-]+$",
        examples=["adventure-time"],
    )
    character: str = Field(
        ..., min_length=1, pattern=r"^[a-zA-Z0-9 _.'-]+$",
        examples=["BMO"],
    )
    base_model: str = "google/gemma-2-2b-it"


class TrainResponse(BaseModel):
    job_id: str
    status: TrainingStatus


class TrainingJob(BaseModel):
    job_id: str
    status: TrainingStatus
    wiki: str
    character: str
    base_model: str
    progress: list[str] = Field(default_factory=list)
    error: str | None = None
    error_hint: str | None = None
    model_name: str | None = None


class ServerInfo(BaseModel):
    server_version: str
    api_version: int = 1
    ollama_healthy: bool
    ollama_url: str
    active_training_job: str | None = None
    hf_token_set: bool = False


class ModelInfo(BaseModel):
    name: str
    size: int | None = None
    modified_at: str | None = None


class PullRequest(BaseModel):
    model: str = Field(
        ..., min_length=1, pattern=r"^[a-zA-Z0-9_.-]+(:[a-zA-Z0-9_.-]+)?$",
    )
