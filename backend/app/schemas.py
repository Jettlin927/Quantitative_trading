from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class DataQualityRunRequest(BaseModel):
    scope: Literal["etf_time_series"]
    start_date: date
    end_date: date
    universe: list[str] = Field(default_factory=list, max_length=5000)
    universe_type: Literal["explicit_snapshot", "static_current"] = "explicit_snapshot"
    universe_source: str | None = Field(default=None, max_length=200)
    universe_as_of_date: date | None = None
    required_datasets: list[str] = Field(default_factory=list, max_length=20)
    benchmark: str | None = None
    statement_timeout_ms: int = Field(default=30_000, ge=500, le=60_000)
    code_commit: str | None = Field(default=None, max_length=64)

    @field_validator("universe")
    @classmethod
    def normalize_universe(cls, value: list[str]) -> list[str]:
        return sorted({item.strip().upper() for item in value if item.strip()})

    @field_validator("required_datasets")
    @classmethod
    def normalize_required_datasets(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    @field_validator("benchmark")
    @classmethod
    def normalize_benchmark(cls, value: str | None) -> str | None:
        return value.strip().upper() if value and value.strip() else None

    @model_validator(mode="after")
    def validate_date_range(self) -> "DataQualityRunRequest":
        if self.start_date > self.end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if not self.universe:
            raise ValueError("universe 必须包含至少一个有效代码")
        return self
