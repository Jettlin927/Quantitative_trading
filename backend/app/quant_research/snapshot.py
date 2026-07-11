from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from ..models import (
    DataQualityRun,
    DataSnapshot,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    TradeCalendar,
)
from .artifacts import (
    ArtifactIntegrityError,
    atomic_write_json,
    verify_csv_artifact,
    write_canonical_csv_gz,
)
from .run_config import canonical_sha256, validate_run_config


GIB = 1024**3
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ETF_SNAPSHOT_TABLES = {
    "universe",
    "trade_calendars",
    "funds",
    "fund_daily_bars",
    "fund_adjust_factors",
    "indices",
    "index_daily_bars",
}


class SnapshotError(RuntimeError):
    pass


class SnapshotCapacityError(SnapshotError):
    pass


class SnapshotIntegrityError(SnapshotError):
    pass


@dataclass(frozen=True)
class SnapshotCapacityPolicy:
    min_remaining_bytes: int = 5 * GIB
    estimate_multiplier: int = 2


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_id: str
    path: Path
    manifest: dict[str, Any]
    reused: bool


@dataclass(frozen=True)
class TableSlice:
    name: str
    columns: tuple[str, ...]
    natural_key: tuple[str, ...]
    statement: Select[Any]


def freeze_input_snapshot(
    registry_db: Session,
    config: dict[str, Any],
    snapshot_root: Path,
    *,
    capacity_policy: SnapshotCapacityPolicy | None = None,
    statement_timeout_ms: int = 30_000,
) -> SnapshotResult:
    normalized = validate_run_config(config)
    quality_run = validate_quality_gate(registry_db, normalized)
    snapshot_root = Path(snapshot_root).expanduser().resolve()
    snapshot_root.mkdir(parents=True, exist_ok=True)
    capacity = capacity_policy or SnapshotCapacityPolicy()
    temporary = snapshot_root / f".building-{uuid4()}"
    inputs_dir = temporary / "inputs"
    inputs_dir.mkdir(parents=True)
    registered_snapshot_id: str | None = None

    try:
        table_artifacts: dict[str, dict[str, Any]] = {}
        row_counts: dict[str, int] = {}
        with Session(bind=registry_db.get_bind(), autoflush=False, expire_on_commit=False) as source_db:
            with source_db.begin():
                _configure_snapshot_transaction(source_db, statement_timeout_ms)
                slices = _build_etf_slices(normalized)
                estimates = {
                    item.name: int(
                        source_db.scalar(
                            select(func.count()).select_from(item.statement.order_by(None).subquery())
                        )
                        or 0
                    )
                    for item in slices
                }
                estimates["universe"] = len(normalized["universe"]["members"])
                _enforce_capacity(snapshot_root, slices, estimates, capacity)

                universe_rows = ({"ts_code": code} for code in normalized["universe"]["members"])
                universe_artifact = write_canonical_csv_gz(
                    inputs_dir / "universe.csv.gz",
                    columns=("ts_code",),
                    rows=universe_rows,
                    natural_key=("ts_code",),
                )
                universe_artifact["filename"] = "inputs/universe.csv.gz"
                table_artifacts["universe"] = universe_artifact
                row_counts["universe"] = universe_artifact["rowCount"]

                for item in slices:
                    result = source_db.execute(item.statement.execution_options(stream_results=True, yield_per=1000))
                    artifact = write_canonical_csv_gz(
                        inputs_dir / f"{item.name}.csv.gz",
                        columns=item.columns,
                        rows=result.mappings(),
                        natural_key=item.natural_key,
                    )
                    if artifact["rowCount"] == 0:
                        raise SnapshotError(f"冻结切片缺少必需数据：{item.name}")
                    artifact["filename"] = f"inputs/{item.name}.csv.gz"
                    table_artifacts[item.name] = artifact
                    row_counts[item.name] = artifact["rowCount"]

                source_cutoff = source_db.scalar(select(func.now()))
                transaction_contract = {
                    "dialect": source_db.get_bind().dialect.name,
                    "isolation": "REPEATABLE READ" if source_db.get_bind().dialect.name == "postgresql" else "test",
                    "readOnly": source_db.get_bind().dialect.name == "postgresql",
                }

        snapshot_identity = {
            "scope": normalized["scope"],
            "warmupStart": normalized["warmupStart"],
            "startDate": normalized["startDate"],
            "endDate": normalized["endDate"],
            "benchmark": normalized["benchmark"],
            "universeHash": normalized["universe"]["universeHash"],
            "tableArtifacts": {
                name: {
                    "contentSha256": artifact["contentSha256"],
                    "columns": artifact["columns"],
                    "naturalKey": artifact["naturalKey"],
                    "rowCount": artifact["rowCount"],
                }
                for name, artifact in sorted(table_artifacts.items())
            },
        }
        snapshot_id = canonical_sha256(snapshot_identity)
        registered_snapshot_id = snapshot_id
        final_path = snapshot_root / snapshot_id
        manifest = {
            "schemaVersion": 1,
            "snapshotId": snapshot_id,
            "qualityRunId": quality_run.id,
            "scope": normalized["scope"],
            "warmupStart": normalized["warmupStart"],
            "startDate": normalized["startDate"],
            "endDate": normalized["endDate"],
            "benchmark": normalized["benchmark"],
            "universeHash": normalized["universe"]["universeHash"],
            "universeSourceArtifact": {
                "format": normalized["universe"]["sourceArtifact"]["format"],
                "sha256": normalized["universe"]["sourceArtifact"]["sha256"],
            },
            "transaction": transaction_contract,
            "tableArtifacts": table_artifacts,
            "rowCounts": row_counts,
        }
        atomic_write_json(temporary / "snapshot.json", manifest)
        _fsync_tree(temporary)

        existing = registry_db.get(DataSnapshot, snapshot_id)
        if existing is not None:
            if existing.status != "complete":
                raise SnapshotIntegrityError(
                    f"同内容 snapshot registry 不是 complete：{snapshot_id}, status={existing.status}"
                )
            shutil.rmtree(temporary)
            existing_path = Path(existing.artifact_root).expanduser()
            if not existing_path.is_absolute():
                existing_path = existing_path.resolve()
            try:
                actual_manifest = verify_snapshot(existing_path)
                _verify_registry_snapshot(existing, actual_manifest, existing_path)
            except Exception:
                existing.status = "failed"
                registry_db.commit()
                raise
            return SnapshotResult(
                snapshot_id=snapshot_id,
                path=existing_path,
                manifest=actual_manifest,
                reused=True,
            )

        registry_db.add(
            DataSnapshot(
                snapshot_id=snapshot_id,
                quality_run_id=quality_run.id,
                scope=normalized["scope"],
                start_date=date.fromisoformat(normalized["warmupStart"]),
                end_date=date.fromisoformat(normalized["endDate"]),
                universe_hash=normalized["universe"]["universeHash"],
                artifact_root=str(final_path),
                table_artifacts=table_artifacts,
                row_counts=row_counts,
                source_cutoff=_as_utc(source_cutoff),
                status="building",
            )
        )
        registry_db.commit()
        _atomic_promote(temporary, final_path)
        verify_snapshot(final_path)
        snapshot_row = registry_db.get(DataSnapshot, snapshot_id)
        snapshot_row.status = "complete"
        registry_db.commit()
        return SnapshotResult(snapshot_id=snapshot_id, path=final_path, manifest=manifest, reused=False)
    except Exception:
        registry_db.rollback()
        if registered_snapshot_id:
            failed = registry_db.get(DataSnapshot, registered_snapshot_id)
            if failed is not None and failed.status != "complete":
                failed.status = "failed"
                registry_db.commit()
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def verify_snapshot(path: Path) -> dict[str, Any]:
    path = Path(path)
    manifest_path = path / "snapshot.json"
    if not manifest_path.is_file():
        raise SnapshotIntegrityError(f"snapshot.json 不存在：{path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("snapshotId") != path.name:
            raise SnapshotIntegrityError("snapshot 目录名与 snapshotId 不一致")
        verify_snapshot_identity(manifest)
        for artifact in manifest["tableArtifacts"].values():
            artifact_path = path / artifact["filename"]
            verify_csv_artifact(artifact_path, artifact)
    except ArtifactIntegrityError as exc:
        raise SnapshotIntegrityError(str(exc)) from exc
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        raise SnapshotIntegrityError(f"snapshot manifest 无效：{path}") from exc
    return manifest


def verify_snapshot_identity(manifest: dict[str, Any]) -> None:
    required = {
        "snapshotId",
        "scope",
        "warmupStart",
        "startDate",
        "endDate",
        "benchmark",
        "universeHash",
        "tableArtifacts",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise SnapshotIntegrityError(f"snapshot identity 缺少字段：{', '.join(missing)}")
    artifacts = manifest["tableArtifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != ETF_SNAPSHOT_TABLES:
        raise SnapshotIntegrityError("snapshot tableArtifacts 不是完整 ETF 输入集")
    for name, artifact in artifacts.items():
        _validate_table_artifact(name, artifact)
    if "rowCounts" in manifest:
        expected_counts = {name: artifact["rowCount"] for name, artifact in artifacts.items()}
        if manifest["rowCounts"] != expected_counts:
            raise SnapshotIntegrityError("snapshot rowCounts 与 tableArtifacts 不一致")
    identity = {
        "scope": manifest["scope"],
        "warmupStart": manifest["warmupStart"],
        "startDate": manifest["startDate"],
        "endDate": manifest["endDate"],
        "benchmark": manifest["benchmark"],
        "universeHash": manifest["universeHash"],
        "tableArtifacts": {
            name: {
                "contentSha256": artifact["contentSha256"],
                "columns": artifact["columns"],
                "naturalKey": artifact["naturalKey"],
                "rowCount": artifact["rowCount"],
            }
            for name, artifact in sorted(manifest["tableArtifacts"].items())
        },
    }
    if canonical_sha256(identity) != manifest["snapshotId"]:
        raise SnapshotIntegrityError("snapshotId 与 canonical 输入身份不一致")


def materialize_snapshot_inputs(snapshot: SnapshotResult, destination: Path) -> dict[str, dict[str, Any]]:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for artifact in snapshot.manifest["tableArtifacts"].values():
        source = snapshot.path / artifact["filename"]
        target = destination / Path(artifact["filename"]).name
        shutil.copyfile(source, target)
        with target.open("rb") as handle:
            os.fsync(handle.fileno())
    _fsync_tree(destination)
    return snapshot.manifest["tableArtifacts"]


def verify_materialized_inputs(
    inputs_dir: Path,
    table_artifacts: dict[str, dict[str, Any]],
) -> None:
    try:
        if set(table_artifacts) != ETF_SNAPSHOT_TABLES:
            raise SnapshotIntegrityError("冻结输入表集不完整")
        for name, artifact in table_artifacts.items():
            _validate_table_artifact(name, artifact)
            verify_csv_artifact(Path(inputs_dir) / Path(artifact["filename"]).name, artifact)
    except ArtifactIntegrityError as exc:
        raise SnapshotIntegrityError(str(exc)) from exc


def validate_quality_gate(registry_db: Session, config: dict[str, Any]) -> DataQualityRun:
    run = registry_db.get(DataQualityRun, config["qualityRunId"])
    if run is None:
        raise SnapshotError("qualityRunId 不存在")
    if run.status not in {"ready", "ready_with_warnings"}:
        raise SnapshotError(f"质量门禁未通过：{run.status}")
    if run.finished_at is None:
        raise SnapshotError("质量运行尚未完成")
    if (run.summary or {}).get("status") != run.status:
        raise SnapshotError("质量运行 summary.status 与 registry status 不一致")
    if (run.summary or {}).get("blockers") or (run.summary or {}).get("failedRules"):
        raise SnapshotError("质量运行仍包含 blocker 或 failed rule")
    if run.scope != config["scope"]:
        raise SnapshotError("质量运行 scope 与研究配置不一致")
    if run.start_date > date.fromisoformat(config["warmupStart"]) or run.end_date < date.fromisoformat(config["endDate"]):
        raise SnapshotError("质量运行日期未覆盖 warmupStart 到 endDate")
    quality_config = run.config or {}
    if quality_config.get("universeHash") != run.universe_hash:
        raise SnapshotError("质量运行 registry universe_hash 与 config 不一致")
    if quality_config.get("universeSourceVerified") is not True:
        raise SnapshotError("质量运行 universe 来源未验证")
    if quality_config.get("universeSourceIssue") not in {None, ""}:
        raise SnapshotError("质量运行 universe 来源存在未解决问题")
    if (
        quality_config.get("universeSourceSha256")
        != config["universe"]["sourceArtifact"]["sha256"]
    ):
        raise SnapshotError("质量运行 universe 来源 SHA 与研究来源工件不一致")
    quality_members = sorted(
        {
            str(value).strip().upper()
            for value in quality_config.get("universe", [])
            if str(value).strip()
        }
    )
    if quality_members != config["universe"]["members"]:
        raise SnapshotError("质量运行 universe 成员与研究来源工件不一致")
    if quality_config.get("universeType") != config["universe"]["mode"]:
        raise SnapshotError("质量运行 universe 类型与研究来源工件不一致")
    if quality_config.get("universeAsOfDate") != config["universe"]["asOfDate"]:
        raise SnapshotError("质量运行 universe 日期与研究来源工件不一致")
    benchmark = (run.summary or {}).get("benchmark") or (run.config or {}).get("benchmark")
    if benchmark != config["benchmark"]:
        raise SnapshotError("质量运行 benchmark 与研究配置不一致")
    warning_ids = {
        item.get("ruleId") if isinstance(item, dict) else str(item)
        for item in (run.summary or {}).get("warnings", [])
    }
    unexpected = sorted(warning_ids - set(config["allowedWarnings"]))
    if unexpected:
        raise SnapshotError(f"质量 warning 未在白名单：{', '.join(unexpected)}")
    return run


def _validate_table_artifact(name: str, artifact: Any) -> None:
    if not isinstance(artifact, dict):
        raise SnapshotIntegrityError(f"snapshot artifact 无效：{name}")
    expected_filename = f"inputs/{name}.csv.gz"
    columns = artifact.get("columns")
    natural_key = artifact.get("naturalKey")
    row_count = artifact.get("rowCount")
    if artifact.get("filename") != expected_filename:
        raise SnapshotIntegrityError(f"snapshot artifact 文件名非 canonical：{name}")
    if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)):
        raise SnapshotIntegrityError(f"snapshot artifact columns 无效：{name}")
    if not isinstance(natural_key, list) or not natural_key or not set(natural_key).issubset(columns):
        raise SnapshotIntegrityError(f"snapshot artifact naturalKey 无效：{name}")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise SnapshotIntegrityError(f"snapshot artifact rowCount 无效：{name}")
    if not SHA256_PATTERN.fullmatch(str(artifact.get("contentSha256") or "")):
        raise SnapshotIntegrityError(f"snapshot artifact content hash 无效：{name}")
    if not SHA256_PATTERN.fullmatch(str(artifact.get("fileSha256") or "")):
        raise SnapshotIntegrityError(f"snapshot artifact file hash 无效：{name}")
    if (
        artifact.get("nullValue") != r"\N"
        or artifact.get("compression") != "gzip"
        or artifact.get("gzipMtime") != 0
    ):
        raise SnapshotIntegrityError(f"snapshot artifact canonical 压缩合同无效：{name}")


def _verify_registry_snapshot(row: DataSnapshot, manifest: dict[str, Any], final_path: Path) -> None:
    expected = {
        "qualityRunId": row.quality_run_id,
        "scope": row.scope,
        "warmupStart": row.start_date.isoformat(),
        "endDate": row.end_date.isoformat(),
        "universeHash": row.universe_hash,
        "tableArtifacts": row.table_artifacts,
        "rowCounts": row.row_counts,
    }
    actual = {key: manifest.get(key) for key in expected}
    stored_path = Path(row.artifact_root).expanduser()
    if not stored_path.is_absolute():
        stored_path = stored_path.resolve()
    if actual != expected or stored_path != final_path:
        raise SnapshotIntegrityError("snapshot 磁盘 manifest 与 registry 不一致")


def _configure_snapshot_transaction(db: Session, statement_timeout_ms: int) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    timeout = int(statement_timeout_ms)
    if not 500 <= timeout <= 60_000:
        raise ValueError("statement_timeout_ms 必须在 500 到 60000 之间")
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
    db.execute(text("SET TRANSACTION READ ONLY"))
    db.execute(text(f"SET LOCAL statement_timeout = '{timeout}ms'"))


def _build_etf_slices(config: dict[str, Any]) -> tuple[TableSlice, ...]:
    if config["scope"] != "etf_time_series":
        raise SnapshotError("Phase 3 正式快照仅开放 etf_time_series")
    members = tuple(config["universe"]["members"])
    benchmark = config["benchmark"]
    start = date.fromisoformat(config["warmupStart"])
    end = date.fromisoformat(config["endDate"])
    exchange = str(config["executionPolicy"].get("calendarExchange", "SSE")).upper()

    def columns(model: Any, names: tuple[str, ...]) -> list[Any]:
        return [getattr(model, name).label(name) for name in names]

    calendar_columns = ("exchange", "cal_date", "is_open")
    fund_columns = ("ts_code", "name", "market", "fund_type", "management", "custodian", "list_date")
    bar_columns = (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change_amount",
        "pct_chg",
        "vol",
        "amount",
    )
    factor_columns = ("ts_code", "trade_date", "adj_factor")
    index_columns = ("ts_code", "name", "market", "publisher", "category", "base_date", "list_date")
    return (
        TableSlice(
            "trade_calendars",
            calendar_columns,
            ("exchange", "cal_date"),
            select(*columns(TradeCalendar, calendar_columns))
            .where(
                TradeCalendar.exchange == exchange,
                TradeCalendar.cal_date >= start,
                TradeCalendar.cal_date <= end,
            )
            .order_by(TradeCalendar.exchange, TradeCalendar.cal_date),
        ),
        TableSlice(
            "funds",
            fund_columns,
            ("ts_code",),
            select(*columns(Fund, fund_columns))
            .where(Fund.ts_code.in_(members))
            .order_by(Fund.ts_code),
        ),
        TableSlice(
            "fund_daily_bars",
            bar_columns,
            ("ts_code", "trade_date"),
            select(*columns(FundDailyBar, bar_columns))
            .where(
                FundDailyBar.ts_code.in_(members),
                FundDailyBar.trade_date >= start,
                FundDailyBar.trade_date <= end,
            )
            .order_by(FundDailyBar.ts_code, FundDailyBar.trade_date),
        ),
        TableSlice(
            "fund_adjust_factors",
            factor_columns,
            ("ts_code", "trade_date"),
            select(*columns(FundAdjustFactor, factor_columns))
            .where(
                FundAdjustFactor.ts_code.in_(members),
                FundAdjustFactor.trade_date >= start,
                FundAdjustFactor.trade_date <= end,
            )
            .order_by(FundAdjustFactor.ts_code, FundAdjustFactor.trade_date),
        ),
        TableSlice(
            "indices",
            index_columns,
            ("ts_code",),
            select(*columns(Index, index_columns))
            .where(Index.ts_code == benchmark)
            .order_by(Index.ts_code),
        ),
        TableSlice(
            "index_daily_bars",
            bar_columns,
            ("ts_code", "trade_date"),
            select(*columns(IndexDailyBar, bar_columns))
            .where(
                IndexDailyBar.ts_code == benchmark,
                IndexDailyBar.trade_date >= start,
                IndexDailyBar.trade_date <= end,
            )
            .order_by(IndexDailyBar.ts_code, IndexDailyBar.trade_date),
        ),
    )


def _enforce_capacity(
    root: Path,
    slices: Iterable[TableSlice],
    counts: dict[str, int],
    policy: SnapshotCapacityPolicy,
) -> None:
    column_counts = {item.name: len(item.columns) for item in slices}
    column_counts["universe"] = 1
    estimated_bytes = sum(
        max(1, row_count) * (32 + column_counts[name] * 32)
        for name, row_count in counts.items()
    )
    free_bytes = shutil.disk_usage(root).free
    required_remaining = max(policy.min_remaining_bytes, policy.estimate_multiplier * estimated_bytes)
    if free_bytes - estimated_bytes <= required_remaining:
        candidates = sorted(
            (
                item
                for item in root.iterdir()
                if item.is_dir() and not item.name.startswith(".")
            ),
            key=lambda item: item.stat().st_mtime,
        )[:20]
        names = ", ".join(item.name for item in candidates) or "<none>"
        raise SnapshotCapacityError(
            "快照容量门禁 blocked："
            f"estimated={estimated_bytes}, free={free_bytes}, required_remaining={required_remaining}, "
            f"old_snapshot_candidates={names}；不会自动删除。"
        )


def _atomic_promote(temporary: Path, final_path: Path) -> None:
    if final_path.exists():
        raise SnapshotIntegrityError(f"快照完成目录已存在但 registry 未登记 complete：{final_path}")
    os.replace(temporary, final_path)
    descriptor = os.open(final_path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(root: Path) -> None:
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)
