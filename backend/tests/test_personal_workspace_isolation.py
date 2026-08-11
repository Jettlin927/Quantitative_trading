from __future__ import annotations

import asyncio
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch, sentinel

import yaml

from backend.app import main
from backend.app.personal_workspace.runtime import get_personal_runtime


REPO_ROOT = Path(__file__).resolve().parents[2]


class PersonalWorkspaceIsolationTest(unittest.TestCase):
    def test_main_composition_exposes_private_router_without_wildcard_cors(self) -> None:
        route_paths = {route.path for route in main.app.routes}
        self.assertTrue(
            {
                "/api/personal/today",
                "/api/personal/synthetic-traces",
            }.issubset(route_paths)
        )
        self.assertTrue(
            {
                "/api/personal/records",
                "/api/personal/records/{record_id}",
                "/api/personal/records/commands",
                "/api/personal/synthetic-records",
            }.isdisjoint(route_paths)
        )
        cors = next(
            middleware
            for middleware in main.app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertNotIn("*", cors.kwargs["allow_origins"])

    def test_api_startup_binds_legacy_lifecycles_before_serving(self) -> None:
        watchlist = Mock()
        runtime = Mock()
        runtime.access.configured = True
        runtime.watchlist = watchlist
        runtime.actor = sentinel.actor

        async def exercise() -> None:
            async with main.lifespan(main.app):
                watchlist.bind_legacy_holding_lifecycles.assert_called_once_with(
                    runtime.actor
                )

        with patch.object(main, "assert_schema_revision_at_head"), patch.object(
            main, "get_personal_runtime", return_value=runtime
        ):
            asyncio.run(exercise())

    def test_frontend_contains_no_provider_or_gateway_secret_configuration(self) -> None:
        forbidden = {
            "DEEPSEEK_CREDENTIALS_FILE",
            "DEEPSEEK_TOKEN",
            "ALPACA_MARKET_DATA_SECRET_KEY",
            "ALPACA_CREDENTIALS_FILE",
            "ALPACA_AUTHORIZATION_FILE",
            "PERSONAL_DATA_KEYRING_FILE",
            "X-Personal-Gateway",
        }
        violations = {}
        for path in (REPO_ROOT / "frontend" / "src").rglob("*"):
            if not path.is_file() or ".test." in path.name:
                continue
            source = path.read_text(encoding="utf-8")
            matches = sorted(value for value in forbidden if value in source)
            if matches:
                violations[str(path.relative_to(REPO_ROOT))] = matches
        self.assertEqual(violations, {})

    def test_only_personal_analysis_worker_receives_deepseek_secret_file(self) -> None:
        base = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        personal = yaml.safe_load(
            (REPO_ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
        )
        forbidden_environment = {
            "DEEPSEEK_CREDENTIALS_FILE",
            "DEEPSEEK_TOKEN",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_API_BASE",
        }
        for service_name, service in base["services"].items():
            self.assertTrue(
                forbidden_environment.isdisjoint(service.get("environment", {})),
                service_name,
            )
        for service_name in ("api", "frontend"):
            self.assertNotIn(
                "DEEPSEEK_CREDENTIALS_FILE",
                personal["services"][service_name].get("environment", {}),
            )

        worker = personal["services"]["personal-analysis-worker"]
        self.assertEqual(worker["profiles"], ["personal-ai"])
        self.assertEqual(
            worker["environment"]["DEEPSEEK_CREDENTIALS_FILE"],
            "/run/secrets/deepseek-credentials.json",
        )
        self.assertEqual(
            worker["environment"]["ALPACA_CREDENTIALS_FILE"],
            "/run/secrets/alpaca-credentials.json",
        )
        self.assertEqual(
            worker["environment"]["ALPACA_AUTHORIZATION_FILE"],
            "/run/config/alpaca-authorization.json",
        )
        self.assertIn("PERSONAL_ANALYSIS_MODE", worker["environment"])
        mounts = {mount["target"]: mount for mount in worker["volumes"]}
        self.assertEqual(
            set(mounts),
            {
                "/run/secrets/personal-keyring.json",
                "/run/secrets/deepseek-credentials.json",
                "/run/secrets/alpaca-credentials.json",
                "/run/config/alpaca-authorization.json",
                "${INVESTMENT_NEWS_DIR:-/run/disabled/news}",
            },
        )
        self.assertTrue(mounts["/run/secrets/deepseek-credentials.json"]["read_only"])

    def test_only_api_receives_official_analysis_query_and_authorization_files(self) -> None:
        personal = yaml.safe_load(
            (REPO_ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
        )
        api = personal["services"]["api"]
        mounts = {mount["target"]: mount for mount in api["volumes"]}
        expected = {
            "/run/config/official-analysis-queries.json",
            "/run/config/official-analysis-authorization.json",
        }
        self.assertTrue(expected.issubset(mounts))
        self.assertTrue(all(mounts[target]["read_only"] for target in expected))
        for service_name in ("frontend", "personal-analysis-worker"):
            service = personal["services"][service_name]
            targets = {mount["target"] for mount in service.get("volumes", [])}
            environment = service.get("environment", {})
            self.assertTrue(expected.isdisjoint(targets), service_name)
            self.assertNotIn("OFFICIAL_ANALYSIS_QUERY_FILE", environment)
            self.assertNotIn("OFFICIAL_ANALYSIS_AUTHORIZATION_FILE", environment)

    def test_api_receives_read_only_structured_news_snapshot(self) -> None:
        personal = yaml.safe_load(
            (REPO_ROOT / "docker-compose.personal.yml").read_text(encoding="utf-8")
        )
        api = personal["services"]["api"]
        mounts = {mount["target"]: mount for mount in api["volumes"]}
        news = mounts["${INVESTMENT_NEWS_DIR:-/run/disabled/news}"]

        self.assertEqual(
            news["source"], "${INVESTMENT_NEWS_HOST_DIR:-/run/disabled/news}"
        )
        self.assertTrue(news["read_only"])
        self.assertFalse(news["bind"]["create_host_path"])

    def test_missing_any_private_configuration_fails_closed_without_initializing_store(self) -> None:
        names = {
            "PRIVATE_DATABASE_URL",
            "PERSONAL_GATEWAY_TOKEN_FILE",
            "PERSONAL_ALLOWED_ORIGINS",
            "PERSONAL_DATA_KEYRING_FILE",
        }
        environment = {key: value for key, value in os.environ.items() if key not in names}
        with patch.dict(os.environ, environment, clear=True):
            get_personal_runtime.cache_clear()
            runtime = get_personal_runtime()
        get_personal_runtime.cache_clear()

        self.assertFalse(runtime.access.configured)
        self.assertIsNone(runtime.journey)


if __name__ == "__main__":
    unittest.main()
