from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

USERNAME_PATTERN = r"^[A-Za-z0-9_.@-]+$"
INCIDENT_STATUSES = ("NEW", "INVESTIGATING", "RESOLVED")


class LoginPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class SearchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    q: str = Field(min_length=1, max_length=128)


class PaginationQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=10, ge=1, le=20)


class UploadMetadata(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class AlertFeedbackPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    alert_id: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=32)
    reason: str = Field(default="", max_length=500)
    user_id: str = Field(default="dashboard", min_length=1, max_length=100, pattern=USERNAME_PATTERN)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        allowed = {"true_positive", "false_positive", "false_negative"}
        if value not in allowed:
            raise ValueError(f"label must be one of: {', '.join(sorted(allowed))}")
        return value


class AlertIncidentUpdatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    alert_id: int = Field(ge=1)
    incident_status: str = Field(min_length=1, max_length=32)
    incident_owner: str = Field(default="", max_length=100)
    incident_notes: str = Field(default="", max_length=1000)
    user_id: str = Field(default="dashboard", min_length=1, max_length=100, pattern=USERNAME_PATTERN)

    @field_validator("incident_status")
    @classmethod
    def validate_incident_status(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in INCIDENT_STATUSES:
            raise ValueError(f"incident_status must be one of: {', '.join(INCIDENT_STATUSES)}")
        return normalized


def validate_model(model_cls: type[BaseModel], payload: dict[str, Any]) -> BaseModel:
    return model_cls.model_validate(payload)


def first_validation_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    return f"{location}: {error.get('msg', 'invalid input')}" if location else error.get("msg", "invalid input")


def validate_upload_filename(filename: str, allowed_extensions: set[str] | None = None) -> str:
    allowed_extensions = allowed_extensions or {"json", "log", "csv"}
    metadata = UploadMetadata.model_validate({"filename": filename})
    suffix = Path(metadata.filename).suffix.lower().lstrip(".")
    if not suffix or suffix not in allowed_extensions:
        raise ValueError("Extensao de ficheiro nao permitida")
    return metadata.filename


def upload_size_limit_bytes() -> int:
    return int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024
