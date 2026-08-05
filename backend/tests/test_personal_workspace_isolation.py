from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_formal_research_paths_cannot_import_or_read_private_workspace(self) -> None:
        protected_paths = [
            *sorted((REPO_ROOT / "backend" / "app" / "quant_research").glob("*.py")),
            REPO_ROOT / "backend" / "app" / "research_worker.py",
            REPO_ROOT / "backend" / "app" / "research_publication.py",
            *sorted((REPO_ROOT / "scripts" / "research").glob("*.py")),
        ]
        forbidden = {
            "personal_workspace",
            "PRIVATE_DATABASE_URL",
            "PERSONAL_DATA_KEYRING_FILE",
            "DEEPSEEK_CREDENTIALS_FILE",
            "ALPACA_CREDENTIALS_FILE",
            "ALPACA_AUTHORIZATION_FILE",
        }

        violations = {}
        for path in protected_paths:
            source = path.read_text(encoding="utf-8")
            matches = sorted(value for value in forbidden if value in source)
            if matches:
                violations[str(path.relative_to(REPO_ROOT))] = matches
        self.assertEqual(violations, {})

    def test_worker_processes_do_not_receive_private_or_ai_configuration(self) -> None:
        compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        forbidden = {
            "PRIVATE_DATABASE_URL",
            "PERSONAL_GATEWAY_TOKEN_FILE",
            "PERSONAL_DATA_KEYRING_FILE",
            "DEEPSEEK_CREDENTIALS_FILE",
            "DEEPSEEK_TOKEN",
            "DEEPSEEK_MODEL",
            "DEEPSEEK_API_BASE",
            "ALPACA_CREDENTIALS_FILE",
            "ALPACA_AUTHORIZATION_FILE",
        }
        for service_name in ("research-worker",):
            environment = compose["services"][service_name].get("environment", {})
            self.assertTrue(forbidden.isdisjoint(environment), service_name)

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
