from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ArtifactWriteResult(BaseModel):
    output_path: Path
    item_count: int
    source_path: Path | None = None
    source_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AuditArtifactResult(BaseModel):
    output_path: Path
    audit_status: str
    issue_count: int
    warning_count: int
    item_count: int
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
