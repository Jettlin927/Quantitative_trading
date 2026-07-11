import argparse
from collections.abc import Generator
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://quant:quant_password@localhost:5432/quant_trading",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "alembic.ini"
BASELINE_REVISION = "0001_existing_schema_baseline"
BASELINE_SCHEMA_FINGERPRINT = "1956bc7ef21d73f504605089f3ec4a2a65d343ac7457c18c45b7be7828763785"
BASELINE_TABLE_FINGERPRINTS = {
    "asset_daily_prices": "8086fb2a788c70783e88c37f851b5372bd421965966abafe76151d2c98ffe746",
    "assets": "1aeb183d377bae5387a2df7d82fbacd026f5db5c6b6027cf422beeacc299928a",
    "data_overview_snapshots": "1cc2ea2ceed5c6bad813c5a8e8e61d8823907333eceaed622d281bdc6cdf0ab5",
    "data_sync_jobs": "72ec22123edd8d0042b890a7147448a2bd7544715b0f28f2cab652f56bdf735f",
    "data_sync_runs": "935165a516c60852badfcd00e7cafa0468354aeec344651b9ef8a8b9413b5d23",
    "fund_adjust_factors": "8668e642d8d01bb5e5830babf5e5279dc657475dc688ed5a1143b2b7bd7c786e",
    "fund_daily_bars": "0ca899b3f71579e970ddb213d0c30aa536a477526216e8347833ea1c4c3d689a",
    "funds": "b363178e2e9f2713c7b1d0cd438f0d186b53929bca2a59b8345f9498c22a08a0",
    "index_daily_bars": "2c7762b2ab4d17d23a4f3b2a6f3da542762b6c59ca49ee67e7ab44169288793a",
    "indices": "8582a815de22508171989218eaa34b46482c2e21eaff7451e2ce719f42d3e71c",
    "industry_classifications": "fc167804029ba0778dd2eee56cb02fcc980f842c0081db0f830a159ef7a153d5",
    "industry_members": "32231c222c162022d7fb35e18525ca804f2256b9391e86580ce8a4dc01f9310c",
    "portfolio_snapshots": "be95f75a07f94bfe47cd732ad2d5247044da02b2c9a13b26af21447232b6bacc",
    "stock_adjust_factors": "438dcc04d488dc0e7baea425c0e8eb03ff6f9ac2afb180bdd18ab551f631782d",
    "stock_daily_bars": "2de1552fa83c50106479e564bcbc9a091d9a9e8991fb57d211f74f9d69696cad",
    "stock_daily_basic": "1bcfb3e62abaf23731ca5a39f9e1b2773fc9726c97020e301b0cd06522552817",
    "stock_financial_indicators": "0d85001a4f9354014f95a80545e47fa0df038c0cac8352f4e4828adf5d3f8526",
    "stock_limit_prices": "3d966d0022d6546b8ad297664dcea855949fb4d2f34b3434f35c6e1e587d6b0b",
    "stock_listings": "0475c608f578501d3e4b8d31fd04671cd6a5ef9cc68267af773a6027707e19d0",
    "stock_pool_members": "a2754f430bc86b6abffe18ce238c9744e960b5f6f66f9c9c3512383986db9741",
    "stock_pools": "61ba8a5a0726d38270b79abd4f8f4e2ed9695d0e8a5ef93bf0307beb20a1eed0",
    "stock_suspend_events": "b8e70a762b6e3b56ac7f65223778c6baa36dd8e488e90cd27042e9f20a3c4a6c",
    "stocks": "38f95c4474ad517652c39fbf41e49ef922a1bf9df601f6d9e8cc2f0701837292",
    "trade_calendars": "176adacd56d1006f89e35b09d324cc5f7e48885cf898371cd33932b9cafa0974",
    "watchlist_items": "47d41d0052eebbab33ab3b88b83718341424af1704ec0888027c9d636aa1ec55",
}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class SchemaRevisionError(RuntimeError):
    pass


class SchemaFingerprintError(RuntimeError):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def alembic_config(connection: Connection | None = None) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def expected_schema_heads() -> tuple[str, ...]:
    return tuple(sorted(ScriptDirectory.from_config(alembic_config()).get_heads()))


def current_schema_heads(connection: Connection) -> tuple[str, ...]:
    return tuple(sorted(MigrationContext.configure(connection).get_current_heads()))


def assert_schema_revision_at_head(bind: Engine = engine) -> None:
    expected = expected_schema_heads()
    with bind.connect() as connection:
        current = current_schema_heads(connection)
    if current != expected:
        current_text = ",".join(current) if current else "<none>"
        expected_text = ",".join(expected) if expected else "<none>"
        raise SchemaRevisionError(
            "数据库 schema revision 未到 head："
            f"current={current_text}, expected={expected_text}。"
            "应用不会自动迁移，请先执行受控 Alembic 流程。"
        )


def schema_fingerprint(connection: Connection) -> dict[str, Any]:
    inspector = inspect(connection)
    schema = "public" if connection.dialect.name == "postgresql" else None
    table_names = sorted(name for name in inspector.get_table_names(schema=schema) if name != "alembic_version")
    sequence_names = sorted(inspector.get_sequence_names(schema=schema)) if connection.dialect.name == "postgresql" else []
    table_fingerprints: dict[str, str] = {}
    for table_name in table_names:
        contract = _table_schema_contract(inspector, connection, table_name, schema)
        table_fingerprints[table_name] = _canonical_sha256(contract)
    return {
        "sha256": _canonical_sha256({"sequences": sequence_names, "tables": table_fingerprints}),
        "sequences": sequence_names,
        "tables": table_fingerprints,
    }


def validate_existing_schema_fingerprint(connection: Connection) -> dict[str, Any]:
    if connection.dialect.name != "postgresql":
        raise SchemaFingerprintError("既有库 baseline stamp 仅允许 PostgreSQL。")

    actual = schema_fingerprint(connection)
    if actual["sha256"] != BASELINE_SCHEMA_FINGERPRINT:
        actual_tables = actual["tables"]
        mismatched = sorted(
            table_name
            for table_name in set(BASELINE_TABLE_FINGERPRINTS) | set(actual_tables)
            if BASELINE_TABLE_FINGERPRINTS.get(table_name) != actual_tables.get(table_name)
        )
        mismatch_text = ",".join(mismatched) if mismatched else "<unknown>"
        if not mismatched:
            mismatch_text = "sequences"
        raise SchemaFingerprintError(
            f"既有库 schema fingerprint 与 {BASELINE_REVISION} 不一致，已拒绝 stamp："
            f"actual={actual['sha256']}, expected={BASELINE_SCHEMA_FINGERPRINT}, "
            f"mismatched_tables={mismatch_text}。"
        )
    return actual


def stamp_existing_schema_baseline(
    bind: Engine = engine,
    *,
    confirm_fingerprint: str,
) -> dict[str, Any]:
    if confirm_fingerprint != BASELINE_SCHEMA_FINGERPRINT:
        raise SchemaFingerprintError(
            "必须显式提供当前 baseline fingerprint 才能 stamp；未做任何修改。"
        )
    if bind.dialect.name != "postgresql":
        raise SchemaFingerprintError("既有库 baseline stamp 仅允许 PostgreSQL。")

    with bind.begin() as connection:
        current = current_schema_heads(connection)
        if current:
            raise SchemaRevisionError(
                "数据库已有 Alembic revision，拒绝重复 baseline stamp："
                f"current={','.join(current)}。"
            )
        fingerprint = validate_existing_schema_fingerprint(connection)
        command.stamp(alembic_config(connection), BASELINE_REVISION)
    return {"revision": BASELINE_REVISION, **fingerprint}


def _table_schema_contract(
    inspector: Any,
    connection: Connection,
    table_name: str,
    schema: str | None,
) -> dict[str, Any]:
    columns = []
    for column in inspector.get_columns(table_name, schema=schema):
        columns.append(
            {
                "name": column["name"],
                "type": _normalize_sql(column["type"].compile(dialect=connection.dialect)),
                "nullable": bool(column["nullable"]),
                "default": _normalize_sql(column.get("default")),
            }
        )

    primary_key = inspector.get_pk_constraint(table_name, schema=schema)
    unique_constraints = [
        {
            "name": constraint.get("name"),
            "columns": list(constraint.get("column_names") or []),
        }
        for constraint in inspector.get_unique_constraints(table_name, schema=schema)
    ]
    indexes = [
        {
            "name": index.get("name"),
            "unique": bool(index.get("unique")),
            "columns": list(index.get("column_names") or []),
        }
        for index in inspector.get_indexes(table_name, schema=schema)
        if not index.get("duplicates_constraint")
    ]
    foreign_keys = [
        {
            "columns": list(foreign_key.get("constrained_columns") or []),
            "referred_schema": foreign_key.get("referred_schema"),
            "referred_table": foreign_key.get("referred_table"),
            "referred_columns": list(foreign_key.get("referred_columns") or []),
            "ondelete": (foreign_key.get("options") or {}).get("ondelete"),
        }
        for foreign_key in inspector.get_foreign_keys(table_name, schema=schema)
    ]
    unique_constraints.sort(key=_contract_sort_key)
    indexes.sort(key=_contract_sort_key)
    foreign_keys.sort(key=_contract_sort_key)
    return {
        "columns": columns,
        "primary_key": list(primary_key.get("constrained_columns") or []),
        "unique_constraints": unique_constraints,
        "indexes": indexes,
        "foreign_keys": foreign_keys,
    }


def _normalize_sql(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _contract_sort_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _migration_cli() -> None:
    parser = argparse.ArgumentParser(description="只读检查 schema，或在指纹匹配后对既有 PostgreSQL 执行 baseline stamp。")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="查看当前 revision 与 head，不修改数据库。")
    subparsers.add_parser("fingerprint", help="计算当前 schema fingerprint，不修改数据库。")
    stamp_parser = subparsers.add_parser("stamp-existing", help="指纹一致时将既有 schema 标记为 baseline revision。")
    stamp_parser.add_argument("--confirm-fingerprint", required=True)
    args = parser.parse_args()

    try:
        if args.command == "status":
            with engine.connect() as connection:
                payload = {
                    "current": list(current_schema_heads(connection)),
                    "expected": list(expected_schema_heads()),
                }
        elif args.command == "fingerprint":
            with engine.connect() as connection:
                payload = schema_fingerprint(connection)
        else:
            payload = stamp_existing_schema_baseline(confirm_fingerprint=args.confirm_fingerprint)
    except (SchemaFingerprintError, SchemaRevisionError) as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    _migration_cli()
