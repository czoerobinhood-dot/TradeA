from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


@dataclass(slots=True)
class Candidate:
    code: str
    name: str
    hot_rank: int | None = None
    concepts: list[str] = field(default_factory=list)
    concept_rank: int | None = None
    source_tags: list[str] = field(default_factory=list)
    calibration_role: str | None = None
    history: pd.DataFrame | None = None
    fund_flow: pd.DataFrame | None = None
    fund_snapshot: pd.DataFrame | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceTemplate:
    code: str
    name: str
    signal_date: str
    forward_return: float
    path: list[float]
    kind: str = "historical_start"
    timeframe: str = "daily"


@dataclass(slots=True)
class ScanIssue:
    scope: str
    message: str
    code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"scope": self.scope, "code": self.code, "message": self.message}


@dataclass(slots=True)
class ScanOutcome:
    status: str
    started_at: datetime
    finished_at: datetime
    candidates: list[Candidate]
    templates: list[ReferenceTemplate]
    issues: list[ScanIssue]
    source_summary: dict[str, Any]
