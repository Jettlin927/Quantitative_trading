from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app.personal_workspace.mcp_http_server import (
    PERSONAL_MCP_HTTP_HOST,
    PERSONAL_MCP_HTTP_PORT,
    PersonalMcpHttpServerConfigurationError,
    load_http_server_config,
    main,
    run_from_environment,
    serve_http_app,
)
from backend.app.personal_workspace.owner_only_file import (
    OwnerOnlyFileError,
    read_owner_only_file,
)


class PersonalMcpHttpServerTest(unittest.TestCase):
    def test_shared_secret_reader_rejects_non_owner_only_and_symlink_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            secret = root / "secret"
            link = root / "secret-link"
            secret.write_bytes(b"secret")
            secret.chmod(0o640)

            with self.assertRaisesRegex(OwnerOnlyFileError, "permissions"):
                read_owner_only_file(secret, maximum_bytes=6)

            secret.chmod(0o600)
            link.symlink_to(secret)
            with self.assertRaisesRegex(OwnerOnlyFileError, "invalid"):
                read_owner_only_file(link, maximum_bytes=6)
            self.assertEqual(
                read_owner_only_file(secret, maximum_bytes=6),
                b"secret",
            )

    def test_default_disabled_path_has_no_file_composition_or_network_side_effects(self) -> None:
        calls: list[str] = []

        exit_code = run_from_environment(
            {},
            app_builder=lambda _config: calls.append("composition"),
            app_runner=lambda _app: calls.append("network"),
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])

    def test_enabled_configuration_is_fixed_and_requires_only_file_paths(self) -> None:
        environment = {
            "PERSONAL_MCP_ENABLED": "true",
            "PERSONAL_MCP_ACTOR_ID": "fixed-owner",
            "PERSONAL_MCP_DATABASE_URL_FILE": "/run/secrets/personal-mcp-database-url",
            "PERSONAL_MCP_TOKEN_FILE": "/run/secrets/personal-mcp-token",
            "PERSONAL_DATA_KEYRING_FILE": "/run/secrets/personal-keyring.json",
            "ALPACA_CREDENTIALS_FILE": "/run/secrets/alpaca-credentials.json",
            "ALPACA_AUTHORIZATION_FILE": "/run/config/alpaca-authorization.json",
            "INVESTMENT_NEWS_DIR": "/run/news",
        }

        config = load_http_server_config(environment)

        self.assertTrue(config.enabled)
        self.assertEqual(config.actor_id, "fixed-owner")
        self.assertEqual(PERSONAL_MCP_HTTP_HOST, "127.0.0.1")
        self.assertEqual(PERSONAL_MCP_HTTP_PORT, 16174)
        for key in tuple(environment)[1:]:
            with self.subTest(key=key):
                incomplete = dict(environment)
                incomplete.pop(key)
                with self.assertRaisesRegex(
                    PersonalMcpHttpServerConfigurationError,
                    "personal_mcp_http_unconfigured",
                ):
                    load_http_server_config(incomplete)
        for forbidden in (
            "PERSONAL_MCP_HTTP_HOST",
            "PERSONAL_MCP_HTTP_PORT",
            "PRIVATE_DATABASE_URL",
            "PERSONAL_MCP_TOKEN",
        ):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(
                    PersonalMcpHttpServerConfigurationError,
                    "personal_mcp_http_forbidden_configuration",
                ):
                    load_http_server_config({**environment, forbidden: "unsafe"})

    def test_database_url_file_is_owner_only_and_errors_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_file = Path(temporary_directory) / "database-url"
            database_file.write_text(
                "postgresql+psycopg://owner:DATABASE_SECRET@127.0.0.1:5432/private\n",
                encoding="utf-8",
            )
            base = {
                "PERSONAL_MCP_ENABLED": "true",
                "PERSONAL_MCP_ACTOR_ID": "fixed-owner",
                "PERSONAL_MCP_DATABASE_URL_FILE": str(database_file),
                "PERSONAL_MCP_TOKEN_FILE": "/not-used/token",
                "PERSONAL_DATA_KEYRING_FILE": "/not-used/keyring",
                "ALPACA_CREDENTIALS_FILE": "/not-used/alpaca",
                "ALPACA_AUTHORIZATION_FILE": "/not-used/authorization",
                "INVESTMENT_NEWS_DIR": "/not-used/news",
            }
            for mode, expected in ((0o640, 2), (0o600, 0)):
                with self.subTest(mode=oct(mode)):
                    database_file.chmod(mode)
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        patch(
                            "backend.app.personal_workspace.mcp_http_server."
                            "create_http_app_from_config",
                            return_value=object(),
                        ),
                        patch(
                            "backend.app.personal_workspace.mcp_http_server.serve_http_app"
                        ),
                        redirect_stdout(stdout),
                        redirect_stderr(stderr),
                    ):
                        exit_code = main(base)

                    self.assertEqual(exit_code, expected)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertNotIn("DATABASE_SECRET", stderr.getvalue())
                    self.assertNotIn("Traceback", stderr.getvalue())

    def test_uvicorn_binding_is_loopback_only_and_not_configurable(self) -> None:
        app = object()
        server = unittest.mock.Mock()
        with (
            patch("uvicorn.Config") as config,
            patch("uvicorn.Server", return_value=server),
        ):
            serve_http_app(app)

        config.assert_called_once_with(
            app,
            host="127.0.0.1",
            port=16174,
            proxy_headers=False,
            forwarded_allow_ips="",
        )
        server.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
