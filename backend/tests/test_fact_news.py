from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backend.app.personal_workspace.agent.fact_news import (
    FactNewsReadContext,
    InvestmentNewsReader,
    InvestmentNewsStructuredSource,
    NewsSourceSnapshot,
)


NOW = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)


class _SpyReader:
    def __init__(self) -> None:
        self.io_calls: list[str] = []

    @property
    def checkout_dir(self) -> Path:
        self.io_calls.append("checkout_dir")
        return Path("/must-not-be-read")

    def refresh(self, *, now: datetime) -> None:
        self.io_calls.append("refresh")

    def load(self):
        self.io_calls.append("load")
        return {"industries": []}


class FactNewsSourceTest(unittest.TestCase):
    def test_investment_news_io_is_localized_to_fact_module(self) -> None:
        agent_dir = Path(__file__).resolve().parents[1] / "app" / "personal_workspace" / "agent"
        offenders = []
        for path in agent_dir.rglob("*.py"):
            if path.name == "fact_news.py":
                continue
            text = path.read_text(encoding="utf-8")
            if any(
                marker in text
                for marker in (
                    "INVESTMENT_NEWS_MARKER",
                    ' / "data.js"',
                    ' / "fetch.py"',
                )
            ):
                offenders.append(str(path.relative_to(agent_dir)))
        self.assertEqual(offenders, [])

    def test_permission_and_purpose_are_checked_before_any_io(self) -> None:
        reader = _SpyReader()
        source = InvestmentNewsStructuredSource(reader)  # type: ignore[arg-type]

        unauthorized = source.read(
            context=FactNewsReadContext(
                permissions=frozenset(), purpose="domain_tool"
            ),
            now=NOW,
        )
        denied_purpose = source.read(
            context=FactNewsReadContext(
                permissions=frozenset({"news:read"}), purpose="mcp_stdio"
            ),
            now=NOW,
        )

        self.assertEqual(unauthorized.gaps[0].code, "source_unauthorized")
        self.assertEqual(denied_purpose.gaps[0].code, "source_purpose_denied")
        self.assertEqual(reader.io_calls, [])

        legacy_reader = InvestmentNewsReader(Path(tempfile.mkdtemp()))
        with patch.object(legacy_reader, "refresh") as refresh:
            with self.assertRaisesRegex(RuntimeError, "source_unauthorized"):
                legacy_reader.search(
                    context=FactNewsReadContext(
                        permissions=frozenset(), purpose="domain_tool"
                    )
                )
            with self.assertRaisesRegex(RuntimeError, "source_purpose_denied"):
                legacy_reader.search(
                    context=FactNewsReadContext(
                        permissions=frozenset({"news:read"}), purpose="mcp_stdio"
                    )
                )
        refresh.assert_not_called()

    def test_corrupt_data_refresh_failure_and_timeout_are_typed_gaps(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir()
        (root / "scripts" / "fetch.py").write_text("", encoding="utf-8")
        data_path = root / "data.js"
        data_path.write_text("window.DATA = {broken", encoding="utf-8")
        fresh = NOW - timedelta(minutes=1)
        os.utime(data_path, (fresh.timestamp(), fresh.timestamp()))
        context = FactNewsReadContext(
            permissions=frozenset({"news:read"}), purpose="domain_tool"
        )

        corrupt = InvestmentNewsStructuredSource(
            InvestmentNewsReader(root, cache_ttl_seconds=3600)
        ).read(context=context, now=NOW)
        os.utime(
            data_path,
            (
                (NOW - timedelta(hours=2)).timestamp(),
                (NOW - timedelta(hours=2)).timestamp(),
            ),
        )
        failed = InvestmentNewsStructuredSource(
            InvestmentNewsReader(
                root, runner=lambda _argv, _cwd, _env: 1, cache_ttl_seconds=3600
            )
        ).read(context=context, now=NOW)
        with patch(
            "backend.app.personal_workspace.agent.fact_news.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["python"], 1),
        ):
            timed_out = InvestmentNewsStructuredSource(
                InvestmentNewsReader(
                    root, fetch_timeout_seconds=1, cache_ttl_seconds=3600
                )
            ).read(context=context, now=NOW)

        self.assertEqual(corrupt.gaps[0].code, "source_unavailable")
        self.assertEqual(corrupt.gaps[0].subject, "news_data_invalid")
        self.assertEqual(failed.gaps[0].subject, "news_fetch_failed")
        self.assertEqual(timed_out.gaps[0].subject, "news_fetch_timeout")

    def test_fetch_process_receives_only_allowlisted_environment(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "scripts").mkdir()
        (root / "scripts" / "fetch.py").write_text("", encoding="utf-8")
        captured = []
        reader = InvestmentNewsReader(
            root,
            runner=lambda argv, cwd, env: captured.append(dict(env)) or 1,
        )

        with patch.dict(
            os.environ,
            {
                "PATH": "/usr/bin",
                "PRIVATE_DATABASE_URL": "postgresql://secret",
                "OPENAI_API_KEY": "secret-key",
                "MODEL_SECRET_PATH": "/private/model-secret",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "news_fetch_failed"):
                reader.refresh(now=NOW)

        self.assertEqual(captured[0]["PYTHONUTF8"], "1")
        self.assertEqual(captured[0]["PATH"], "/usr/bin")
        self.assertFalse(
            {
                "PRIVATE_DATABASE_URL",
                "OPENAI_API_KEY",
                "MODEL_SECRET_PATH",
            }
            & set(captured[0])
        )

    def test_allowed_purposes_are_immutable_at_source_and_snapshot_boundaries(self) -> None:
        purposes = {"domain_tool"}
        reader = _SpyReader()
        source = InvestmentNewsStructuredSource(
            reader, allowed_purposes=purposes  # type: ignore[arg-type]
        )
        purposes.add("mcp_stdio")

        denied = source.read(
            context=FactNewsReadContext(
                permissions=frozenset({"news:read"}), purpose="mcp_stdio"
            ),
            now=NOW,
        )
        snapshot_purposes = {"domain_tool"}
        snapshot = NewsSourceSnapshot(
            items=(), allowed_purposes=snapshot_purposes
        )
        snapshot_purposes.add("mcp_stdio")

        self.assertEqual(denied.gaps[0].code, "source_purpose_denied")
        self.assertEqual(reader.io_calls, [])
        self.assertEqual(snapshot.allowed_purposes, frozenset({"domain_tool"}))


if __name__ == "__main__":
    unittest.main()
