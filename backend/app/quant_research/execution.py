"""统一的正式研究运行/恢复写侧接口。

adapter 只选择 :class:`StartRun` 或 :class:`ResumeRun`。调用是同步长任务，调用方拥有
数据库 Session 生命周期；停止信号只在阶段安全点观察。固定阶段、checkpoint、归档
提升与身份校验仍由现有 canonical runner 拥有。生产身份来自部署环境和 Alembic，
``code_commit``、``schema_revision``、容量覆盖及硬中断仅是 ``test_mode`` 测试 seam。
稳定失败通过 ``RequestRejected`` 或带 ``retryable`` 的 ``RunFailed`` 暴露。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .runner import (
    ResearchRunResult,
    ResearchStopRequested,
    ResumeIdentityError,
    ResumeIntegrityError,
    _resume_quant_research_pipeline,
    _start_quant_research_pipeline,
)
from .run_config import validate_run_config
from .snapshot import SnapshotCapacityPolicy
from .strategy_registry import resolve_strategy_definition


@dataclass(frozen=True)
class StartRun:
    config: dict[str, Any]
    formal_research_id: str | None = None
    orchestration_attempt_id: str | None = None


@dataclass(frozen=True)
class ResumeRun:
    """恢复请求只携带 run_id；冻结身份必须从 registry 和 checkpoint 读回。"""

    run_id: str


@dataclass(frozen=True)
class ExecutionRuntime:
    registry_db: Session
    output_root: Path
    code_commit: str | None = None
    schema_revision: str | None = None
    test_mode: bool = False
    capacity_policy: SnapshotCapacityPolicy | None = None
    interrupt_after_stage: str | None = None


@dataclass(frozen=True)
class SucceededRun:
    run_id: str
    archive_ref: Path
    reproducibility_key: str
    result_fingerprint: str
    manifest: dict[str, Any]

    @property
    def path(self) -> Path:
        """兼容旧 adapter 的工件路径名称。"""

        return self.archive_ref


@dataclass(frozen=True)
class InterruptedRun:
    run_id: str
    last_stage: str | None
    checkpoint_ref: Path
    reason: str


class RequestRejected(RuntimeError):
    def __init__(self, category: str, cause: Exception):
        super().__init__(str(cause))
        self.category = category
        self.cause = cause


class RunFailed(RuntimeError):
    def __init__(self, category: str, cause: Exception, *, retryable: bool):
        super().__init__(str(cause))
        self.category = category
        self.cause = cause
        self.retryable = retryable


ExecutionRequest = StartRun | ResumeRun
ExecutionOutcome = SucceededRun | InterruptedRun


def execute(
    runtime: ExecutionRuntime,
    request: ExecutionRequest,
    stop_signal: Callable[[], bool] | None = None,
) -> ExecutionOutcome:
    """执行唯一 pipeline；adapter 不得提供恢复身份覆盖项。"""

    if isinstance(request, StartRun):
        try:
            normalized = validate_run_config(request.config)
        except (TypeError, ValueError) as exc:
            raise RequestRejected("request", exc) from exc
        try:
            resolve_strategy_definition(normalized)
        except (TypeError, ValueError) as exc:
            raise RequestRejected("request", exc) from exc

    try:
        if isinstance(request, ResumeRun):
            result = _resume_quant_research_pipeline(
                runtime.registry_db,
                request.run_id,
                runtime.output_root,
                code_commit=runtime.code_commit,
                schema_revision=runtime.schema_revision,
                test_mode=runtime.test_mode,
                capacity_policy=runtime.capacity_policy,
                interrupt_after_stage=runtime.interrupt_after_stage,
                should_stop=stop_signal,
            )
        elif isinstance(request, StartRun):
            result = _start_quant_research_pipeline(
                runtime.registry_db,
                request.config,
                runtime.output_root,
                code_commit=runtime.code_commit,
                schema_revision=runtime.schema_revision,
                test_mode=runtime.test_mode,
                capacity_policy=runtime.capacity_policy,
                interrupt_after_stage=runtime.interrupt_after_stage,
                formal_research_id=request.formal_research_id,
                orchestration_attempt_id=request.orchestration_attempt_id,
                should_stop=stop_signal,
            )
        else:
            raise TypeError(f"不支持的执行请求：{type(request).__name__}")
    except ResumeIdentityError as exc:
        raise RequestRejected("identity", exc) from exc
    except ResumeIntegrityError as exc:
        raise RunFailed("integrity", exc, retryable=False) from exc
    except ResearchStopRequested as exc:
        return _interrupted_outcome(runtime, exc)
    except Exception as exc:
        retryable = isinstance(
            exc,
            (OperationalError, TimeoutError, ConnectionError),
        )
        category = "infrastructure" if retryable else "execution"
        raise RunFailed(category, exc, retryable=retryable) from exc
    return _succeeded(result)


def _succeeded(result: ResearchRunResult) -> SucceededRun:
    return SucceededRun(
        run_id=result.run_id,
        archive_ref=result.path,
        reproducibility_key=str(result.manifest["reproducibilityKey"]),
        result_fingerprint=str(result.manifest["resultFingerprint"]),
        manifest=result.manifest,
    )


def _interrupted_outcome(
    runtime: ExecutionRuntime,
    interruption: ResearchStopRequested,
) -> InterruptedRun:
    from ..models import ResearchRun

    run_id = interruption.run_id
    working = interruption.checkpoint_root
    if run_id is None or working is None:
        raise RunFailed(
            "integrity",
            RuntimeError("停止异常缺少本次研究运行身份或 checkpoint 目录"),
            retryable=False,
        )
    run = runtime.registry_db.get(ResearchRun, run_id)
    if run is None or run.status != "interrupted":
        raise RunFailed(
            "integrity",
            RuntimeError("停止后无法按本次身份读回 interrupted 研究运行"),
            retryable=False,
        )
    runs_root = Path(runtime.output_root) / "runs"
    allowed_roots = {runs_root / f".{run_id}.tmp", runs_root / run_id}
    if working not in allowed_roots:
        raise RunFailed(
            "integrity",
            RuntimeError("停止异常中的 checkpoint 目录不属于本次研究运行"),
            retryable=False,
        )
    index_path = working / "checkpoints" / "index.json"
    recovery_path = working / "checkpoints" / "recovery.json"
    try:
        completed = json.loads(index_path.read_text(encoding="utf-8"))["completed"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RunFailed("integrity", exc, retryable=False) from exc
    return InterruptedRun(
        run_id=run_id,
        last_stage=completed[-1] if completed else None,
        checkpoint_ref=recovery_path,
        reason=str(interruption),
    )
