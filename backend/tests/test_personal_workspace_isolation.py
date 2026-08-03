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
                "/api/personal/synthetic-records",
            }.issubset(route_paths)
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
            "OPENAI_API_KEY",
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
            "OPENAI_API_KEY",
            "ALPACA_CREDENTIALS_FILE",
            "ALPACA_AUTHORIZATION_FILE",
        }
        for service_name in ("worker", "research-worker"):
            environment = compose["services"][service_name].get("environment", {})
            self.assertTrue(forbidden.isdisjoint(environment), service_name)

    def test_frontend_contains_no_provider_or_gateway_secret_configuration(self) -> None:
        forbidden = {
            "OPENAI_API_KEY",
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
