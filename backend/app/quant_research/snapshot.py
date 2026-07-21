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

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from ..models import (
    DataQualityResult,
    DataQualityRun,
    DataSnapshot,
    Fund,
    FundAdjustFactor,
    FundDailyBar,
    Index,
    IndexDailyBar,
    IndustryClassification,
    IndustryMember,
    StockAdjustFactor,
    StockDailyBar,
    StockLimitPrice,
    StockListing,
    StockSuspendEvent,
    TradeCalendar,
)
from .artifacts import (
    ArtifactIntegrityError,
    atomic_write_json,
    verify_csv_artifact,
    write_canonical_csv_gz,
)
from .run_config import canonical_sha256, validate_run_config
from .universe import (
    IndustryLevelMembershipResolution,
    IndustryMembershipResolution,
    resolve_industry_level_membership,
    resolve_industry_membership,
)


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
A_SHARE_SNAPSHOT_TABLES = {
    "universe",
    "trade_calendars",
    "stock_listings",
    "stock_daily_bars",
    "stock_adjust_factors",
    "stock_limit_prices",
    "stock_suspend_events",
    "industry_members",
    "indices",
    "index_daily_bars",
}
A_SHARE_INDUSTRY_LEVEL_SNAPSHOT_TABLES = A_SHARE_SNAPSHOT_TABLES | {
    "industry_classifications"
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
    max_universe_date_pairs: int = 5_000_000
    max_total_rows: int = 20_000_000


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
    allow_empty: bool = False


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
                membership: (
                    IndustryMembershipResolution
                    | IndustryLevelMembershipResolution
                    | None
                ) = None
                if normalized["scope"] == "a_share_cross_section":
                    if normalized["universe"]["mode"] == "industry_membership":
                        membership = resolve_industry_membership(
                            source_db,
                            normalized["universe"]["sourceKey"],
                            date.fromisoformat(normalized["warmupStart"]),
                            date.fromisoformat(normalized["endDate"]),
                        )
                    else:
                        membership = resolve_industry_level_membership(
                            source_db,
                            normalized["universe"]["classificationSource"],
                            normalized["universe"]["classificationLevel"],
                            date.fromisoformat(normalized["warmupStart"]),
                            date.fromisoformat(normalized["endDate"]),
                        )
                    _validate_snapshot_membership_identity(quality_run, membership)
                    universe_hash = membership.universe_hash
                    if isinstance(membership, IndustryLevelMembershipResolution):
                        universe_columns = (
                            "trade_date",
                            "ts_code",
                            "industry_index_code",
                        )
                        universe_key = universe_columns
                        universe_source_artifact = {
                            "format": "database_industry_level_membership_v1",
                            "source": "industry_classifications+industry_members",
                            "classificationSource": membership.classification_source,
                            "classificationLevel": membership.classification_level,
                            "classificationSha256": membership.classification_sha256,
                            "memberSha256": membership.member_sha256,
                            "memberCount": len(membership.records),
                            "uniqueMemberCount": len(membership.symbols),
                        }
                    else:
                        universe_columns = ("trade_date", "ts_code")
                        universe_key = ("trade_date", "ts_code")
                        universe_source_artifact = {
                            "format": "database_industry_membership_v1",
                            "source": "industry_members",
                            "sourceKey": membership.source_key,
                            "memberSha256": membership.member_sha256,
                            "memberCount": len(membership.records),
                            "uniqueMemberCount": len(membership.symbols),
                        }
                    universe_rows: Iterable[dict[str, object]] = membership.rows()
                    universe_pair_count = len(membership.records)
                else:
                    universe_hash = normalized["universe"]["universeHash"]
                    universe_columns = ("ts_code",)
                    universe_key = ("ts_code",)
                    universe_rows = (
                        {"ts_code": code}
                        for code in normalized["universe"]["members"]
                    )
                    universe_pair_count = len(normalized["universe"]["members"])
                    universe_source_artifact = {
                        "format": normalized["universe"]["sourceArtifact"]["format"],
                        "sha256": normalized["universe"]["sourceArtifact"]["sha256"],
                    }
                slices = _build_snapshot_slices(normalized, membership)
                estimates = {
                    item.name: int(
                        source_db.scalar(
                            select(func.count()).select_from(item.statement.order_by(None).subquery())
                        )
                        or 0
                    )
                    for item in slices
                }
                estimates["universe"] = universe_pair_count
                _enforce_capacity(
                    snapshot_root,
                    slices,
                    estimates,
                    capacity,
                    universe_pair_count=universe_pair_count,
                )

                universe_artifact = write_canonical_csv_gz(
                    inputs_dir / "universe.csv.gz",
                    columns=universe_columns,
                    rows=universe_rows,
                    natural_key=universe_key,
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
                    if artifact["rowCount"] == 0 and not item.allow_empty:
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
            "universeHash": universe_hash,
            "transaction": transaction_contract,
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
            "universeHash": universe_hash,
            "universeSourceArtifact": universe_source_artifact,
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
                universe_hash=universe_hash,
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
        "transaction",
        "tableArtifacts",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise SnapshotIntegrityError(f"snapshot identity 缺少字段：{', '.join(missing)}")
    artifacts = manifest["tableArtifacts"]
    expected_tables = {
        "etf_time_series": ETF_SNAPSHOT_TABLES,
        "a_share_cross_section": (
            A_SHARE_INDUSTRY_LEVEL_SNAPSHOT_TABLES
            if (manifest.get("universeSourceArtifact") or {}).get("format")
            == "database_industry_level_membership_v1"
            else A_SHARE_SNAPSHOT_TABLES
        ),
    }.get(manifest.get("scope"))
    if (
        expected_tables is None
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected_tables
    ):
        raise SnapshotIntegrityError("snapshot tableArtifacts 与 scope 必需输入集不一致")
    for name, artifact in artifacts.items():
        _validate_table_artifact(name, artifact)
    _validate_transaction_contract(manifest["transaction"])
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
        "transaction": manifest["transaction"],
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
        if frozenset(table_artifacts) not in {
            frozenset(ETF_SNAPSHOT_TABLES),
            frozenset(A_SHARE_SNAPSHOT_TABLES),
            frozenset(A_SHARE_INDUSTRY_LEVEL_SNAPSHOT_TABLES),
        }:
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
    if run.finished_at is None:
        raise SnapshotError("质量运行尚未完成")
    results = list(
        registry_db.scalars(
            select(DataQualityResult)
            .where(DataQualityResult.run_id == run.id)
            .order_by(DataQualityResult.rule_id, DataQualityResult.table_name)
        ).all()
    )
    _validate_quality_result_summary(run, results)
    if run.status not in {"ready", "ready_with_warnings"}:
        raise SnapshotError(f"质量门禁未通过：{run.status}")
    if run.scope != config["scope"]:
        raise SnapshotError("质量运行 scope 与研究配置不一致")
    warmup_start = date.fromisoformat(config["warmupStart"])
    end_date = date.fromisoformat(config["endDate"])
    if config["universe"]["mode"] in {
        "industry_membership",
        "industry_level_membership",
    }:
        if run.start_date != warmup_start or run.end_date != end_date:
            raise SnapshotError("A 股质量运行日期必须精确等于 warmupStart 到 endDate")
    elif run.start_date > warmup_start or run.end_date < end_date:
        raise SnapshotError("质量运行日期未覆盖 warmupStart 到 endDate")
    quality_config = run.config or {}
    if quality_config.get("universeHash") != run.universe_hash:
        raise SnapshotError("质量运行 registry universe_hash 与 config 不一致")
    if quality_config.get("universeSourceVerified") is not True:
        raise SnapshotError("质量运行 universe 来源未验证")
    if quality_config.get("universeSourceIssue") not in {None, ""}:
        raise SnapshotError("质量运行 universe 来源存在未解决问题")
    if quality_config.get("universeType") != config["universe"]["mode"]:
        raise SnapshotError("质量运行 universe 类型与研究来源工件不一致")
    if config["universe"]["mode"] == "industry_membership":
        if (
            quality_config.get("universeSource") != "industry_members"
            or quality_config.get("universeSourceKey") != config["universe"]["sourceKey"]
            or quality_config.get("universeAsOfDate") is not None
            or quality_config.get("universeSourceSha256")
            != quality_config.get("universeMemberSha256")
            or not SHA256_PATTERN.fullmatch(
                str(quality_config.get("universeMemberSha256") or "")
            )
            or int(quality_config.get("universeMemberCount") or 0) <= 0
            or int(quality_config.get("universeUniqueMemberCount") or 0) <= 0
        ):
            raise SnapshotError("质量运行 industry_membership 身份不完整")
    elif config["universe"]["mode"] == "industry_level_membership":
        if (
            quality_config.get("universeSource")
            != "industry_classifications+industry_members"
            or quality_config.get("universeClassificationSource")
            != config["universe"]["classificationSource"]
            or quality_config.get("universeClassificationLevel")
            != config["universe"]["classificationLevel"]
            or quality_config.get("universeAsOfDate") is not None
            or quality_config.get("universeSourceSha256")
            != quality_config.get("universeClassificationSha256")
            or not SHA256_PATTERN.fullmatch(
                str(quality_config.get("universeClassificationSha256") or "")
            )
            or not SHA256_PATTERN.fullmatch(
                str(quality_config.get("universeMemberSha256") or "")
            )
            or int(quality_config.get("universeMemberCount") or 0) <= 0
            or int(quality_config.get("universeUniqueMemberCount") or 0) <= 0
        ):
            raise SnapshotError("质量运行 industry_level_membership 身份不完整")
    else:
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


def _validate_quality_result_summary(
    run: DataQualityRun,
    results: list[DataQualityResult],
) -> None:
    if not results:
        raise SnapshotError("质量运行缺少持久化明细，拒绝打开 snapshot gate")
    counts = {
        status: sum(result.status == status for result in results)
        for status in ("passed", "warning", "blocked", "failed")
    }
    derived_status = (
        "failed"
        if counts["failed"]
        else "blocked"
        if counts["blocked"]
        else "ready_with_warnings"
        if counts["warning"]
        else "ready"
    )
    summary = run.summary or {}
    if not isinstance(summary, dict):
        raise SnapshotError("质量运行 summary 不是 JSON object")
    expected_counts = {
        "resultCount": len(results),
        "passedCount": counts["passed"],
        "warningCount": counts["warning"],
        "blockerCount": counts["blocked"],
        "failedCount": counts["failed"],
    }
    expected_references = {
        "warnings": sorted(
            f"{result.rule_id}:{result.table_name}"
            for result in results
            if result.status == "warning"
        ),
        "blockers": sorted(
            f"{result.rule_id}:{result.table_name}"
            for result in results
            if result.status == "blocked"
        ),
        "failedRules": sorted(
            f"{result.rule_id}:{result.table_name}"
            for result in results
            if result.status == "failed"
        ),
    }
    references_match = all(
        isinstance(summary.get(name), list)
        and all(isinstance(item, str) for item in summary[name])
        and sorted(summary[name]) == expected
        for name, expected in expected_references.items()
    )
    if (
        run.status != derived_status
        or summary.get("status") != derived_status
        or any(
            type(summary.get(name)) is not int or summary[name] != value
            for name, value in expected_counts.items()
        )
        or not references_match
    ):
        raise SnapshotError("质量运行 registry/summary 与持久化明细不一致")
    if counts["blocked"] or counts["failed"]:
        raise SnapshotError("质量运行持久化明细仍包含 blocked 或 failed")


def _validate_transaction_contract(transaction: Any) -> None:
    if not isinstance(transaction, dict) or set(transaction) != {
        "dialect",
        "isolation",
        "readOnly",
    }:
        raise SnapshotIntegrityError("snapshot transaction 合同字段无效")
    dialect = transaction["dialect"]
    if dialect == "postgresql":
        valid = transaction["isolation"] == "REPEATABLE READ" and transaction["readOnly"] is True
    elif dialect == "sqlite":
        valid = transaction["isolation"] == "test" and transaction["readOnly"] is False
    else:
        valid = False
    if not valid:
        raise SnapshotIntegrityError("snapshot transaction dialect/isolation/readOnly 无效")


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
        "snapshotId": row.snapshot_id,
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


def _validate_snapshot_membership_identity(
    quality_run: DataQualityRun,
    membership: IndustryMembershipResolution | IndustryLevelMembershipResolution,
) -> None:
    quality_config = quality_run.config or {}
    if isinstance(membership, IndustryLevelMembershipResolution):
        expected = {
            "universeSource": "industry_classifications+industry_members",
            "universeClassificationSource": membership.classification_source,
            "universeClassificationLevel": membership.classification_level,
            "universeSourceSha256": membership.classification_sha256,
            "universeClassificationSha256": membership.classification_sha256,
            "universeMemberSha256": membership.member_sha256,
            "universeMemberCount": len(membership.records),
            "universeUniqueMemberCount": len(membership.symbols),
            "universeHash": membership.universe_hash,
        }
    else:
        expected = {
            "universeSource": "industry_members",
            "universeSourceKey": membership.source_key,
            "universeSourceSha256": membership.member_sha256,
            "universeMemberSha256": membership.member_sha256,
            "universeMemberCount": len(membership.records),
            "universeUniqueMemberCount": len(membership.symbols),
            "universeHash": membership.universe_hash,
        }
    actual = {key: quality_config.get(key) for key in expected}
    if actual != expected or quality_run.universe_hash != membership.universe_hash:
        raise SnapshotError("历史成员已变化，旧质量运行 universe 身份不可用于 snapshot")


def _build_snapshot_slices(
    config: dict[str, Any],
    membership: IndustryMembershipResolution | IndustryLevelMembershipResolution | None,
) -> tuple[TableSlice, ...]:
    if config["scope"] == "etf_time_series":
        if membership is not None:
            raise SnapshotError("ETF snapshot 不接受行业成员解析结果")
        return _build_etf_slices(config)
    if config["scope"] == "a_share_cross_section" and membership is not None:
        return _build_a_share_slices(config, membership)
    raise SnapshotError("snapshot scope 或 universe 解析结果无效")


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
    index_columns = (
        "ts_code",
        "name",
        "market",
        "publisher",
        "category",
        "base_date",
        "list_date",
    )
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


def _build_a_share_slices(
    config: dict[str, Any],
    membership: IndustryMembershipResolution | IndustryLevelMembershipResolution,
) -> tuple[TableSlice, ...]:
    members = membership.symbols
    benchmark = config["benchmark"]
    start = date.fromisoformat(config["warmupStart"])
    end = date.fromisoformat(config["endDate"])
    exchange = str(config["executionPolicy"].get("calendarExchange", "SSE")).upper()

    def columns(model: Any, names: tuple[str, ...]) -> list[Any]:
        return [getattr(model, name).label(name) for name in names]

    calendar_columns = ("exchange", "cal_date", "is_open")
    listing_columns = (
        "ts_code",
        "symbol",
        "name",
        "exchange",
        "list_status",
        "list_date",
        "delist_date",
    )
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
    limit_columns = ("ts_code", "trade_date", "pre_close", "up_limit", "down_limit")
    suspend_columns = ("ts_code", "trade_date", "suspend_type", "suspend_timing")
    membership_columns = (
        "index_code",
        "con_code",
        "con_name",
        "in_date",
        "out_date",
        "is_new",
    )
    classification_columns = (
        "index_code",
        "industry_name",
        "level",
        "industry_code",
        "parent_code",
        "src",
    )
    if isinstance(membership, IndustryLevelMembershipResolution):
        industry_codes = tuple(sorted({record[2] for record in membership.records}))
    else:
        industry_codes = (membership.source_key,)
    index_columns = (
        "ts_code",
        "name",
        "market",
        "publisher",
        "category",
        "base_date",
        "list_date",
    )
    slices = (
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
            "stock_listings",
            listing_columns,
            ("ts_code",),
            select(*columns(StockListing, listing_columns))
            .where(StockListing.ts_code.in_(members))
            .order_by(StockListing.ts_code),
        ),
        TableSlice(
            "stock_daily_bars",
            bar_columns,
            ("ts_code", "trade_date"),
            select(*columns(StockDailyBar, bar_columns))
            .where(
                StockDailyBar.ts_code.in_(members),
                StockDailyBar.trade_date >= start,
                StockDailyBar.trade_date <= end,
            )
            .order_by(StockDailyBar.ts_code, StockDailyBar.trade_date),
        ),
        TableSlice(
            "stock_adjust_factors",
            factor_columns,
            ("ts_code", "trade_date"),
            select(*columns(StockAdjustFactor, factor_columns))
            .where(
                StockAdjustFactor.ts_code.in_(members),
                StockAdjustFactor.trade_date >= start,
                StockAdjustFactor.trade_date <= end,
            )
            .order_by(StockAdjustFactor.ts_code, StockAdjustFactor.trade_date),
        ),
        TableSlice(
            "stock_limit_prices",
            limit_columns,
            ("ts_code", "trade_date"),
            select(*columns(StockLimitPrice, limit_columns))
            .where(
                StockLimitPrice.ts_code.in_(members),
                StockLimitPrice.trade_date >= start,
                StockLimitPrice.trade_date <= end,
            )
            .order_by(StockLimitPrice.ts_code, StockLimitPrice.trade_date),
        ),
        TableSlice(
            "stock_suspend_events",
            suspend_columns,
            ("ts_code", "trade_date", "suspend_type", "suspend_timing"),
            select(*columns(StockSuspendEvent, suspend_columns))
            .where(
                StockSuspendEvent.ts_code.in_(members),
                StockSuspendEvent.trade_date >= start,
                StockSuspendEvent.trade_date <= end,
            )
            .order_by(
                StockSuspendEvent.ts_code,
                StockSuspendEvent.trade_date,
                StockSuspendEvent.suspend_type,
                StockSuspendEvent.suspend_timing,
            ),
            allow_empty=True,
        ),
        TableSlice(
            "industry_members",
            membership_columns,
            ("index_code", "con_code", "in_date"),
            select(*columns(IndustryMember, membership_columns))
            .where(
                IndustryMember.index_code.in_(industry_codes),
                IndustryMember.con_code.in_(members),
                IndustryMember.in_date <= end,
                or_(IndustryMember.out_date.is_(None), IndustryMember.out_date >= start),
            )
            .order_by(
                IndustryMember.index_code,
                IndustryMember.con_code,
                IndustryMember.in_date,
            ),
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
    if isinstance(membership, IndustryLevelMembershipResolution):
        classification_slice = TableSlice(
            "industry_classifications",
            classification_columns,
            ("index_code",),
            select(*columns(IndustryClassification, classification_columns))
            .where(
                IndustryClassification.src == membership.classification_source,
                IndustryClassification.level == membership.classification_level,
            )
            .order_by(IndustryClassification.index_code),
        )
        return (*slices[:6], classification_slice, *slices[6:])
    return slices


def _enforce_capacity(
    root: Path,
    slices: Iterable[TableSlice],
    counts: dict[str, int],
    policy: SnapshotCapacityPolicy,
    *,
    universe_pair_count: int,
) -> None:
    total_rows = sum(counts.values())
    if universe_pair_count > policy.max_universe_date_pairs:
        raise SnapshotCapacityError(
            "快照容量门禁 blocked："
            f"universe_date_pairs={universe_pair_count} 超过上限 "
            f"{policy.max_universe_date_pairs}；不会写文件或自动删除。"
        )
    if total_rows > policy.max_total_rows:
        raise SnapshotCapacityError(
            "快照容量门禁 blocked："
            f"estimated_rows={total_rows} 超过上限 {policy.max_total_rows}；"
            "不会写文件或自动删除。"
        )
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
