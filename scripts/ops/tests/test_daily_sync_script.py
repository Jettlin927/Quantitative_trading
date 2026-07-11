from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "sync_today_market_data.sh"


class DailySyncScriptTest(unittest.TestCase):
    def test_contract_uses_flock_durable_job_post_refresh_quality_and_no_docker_exec(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        deploy_server = (REPO_ROOT / "scripts" / "ops" / "deploy_server.sh").read_text(encoding="utf-8")

        self.assertIn('"$FLOCK_BIN" -n 9', source)
        self.assertIn('/api/trade-calendars/${SYNC_DATE}', source)
        self.assertIn('/api/data-quality/runs', source)
        self.assertIn('"action":"market_fundamentals"', source)
        self.assertIn('FUNDAMENTALS_START_DATE=', source)
        self.assertIn('FUNDAMENTALS_END_DATE=', source)
        self.assertIn('FUNDAMENTALS_MAX_STOCKS=', source)
        self.assertIn('FUNDAMENTALS_RATE_PER_MINUTE="${FUNDAMENTALS_RATE_PER_MINUTE:-150}"', source)
        self.assertIn('"rate_per_minute":%s', source)
        self.assertIn('QUALITY_BENCHMARK="${QUALITY_BENCHMARK:-000300.SH}"', source)
        self.assertIn('QUALITY_UNIVERSE_CONTAINER_ROOT="${QUALITY_UNIVERSE_CONTAINER_ROOT:-/app/outputs/quality-universes}"', source)
        self.assertIn('mktemp "${universe_file}.tmp.XXXXXX"', source)
        self.assertIn('mv -f "$universe_temp" "$universe_file"', source)
        self.assertIn('submit_and_wait "trade_calendar"', source)
        self.assertIn('"benchmark":"%s"', source)
        self.assertLess(source.index('submit_and_wait "trade_calendar"'), source.index('calendar_response='))
        self.assertLess(source.index('calendar_response='), source.index('submit_and_wait "daily_market"'))
        self.assertLess(source.index('submit_and_wait "daily_market"'), source.index('submit_and_wait "market_fundamentals"'))
        self.assertLess(source.index('submit_and_wait "market_fundamentals"'), source.index('REFRESH database overview'))
        self.assertNotIn("docker exec", source)
        self.assertIn("worker:", compose)
        self.assertIn('command: ["python", "-m", "backend.app.sync_worker"]', compose)
        self.assertNotIn("--reload", dockerfile)
        self.assertNotIn("--reload", compose)
        self.assertIn("compose_cmd build api worker", deploy_server)
        self.assertIn("compose_cmd up -d --no-deps api worker", deploy_server)
        self.assertIn("verify_worker", deploy_server)
        self.assertIn("set_deploy_identity", deploy_server)
        self.assertIn('export APP_GIT_COMMIT="$resolved_commit"', deploy_server)

    def test_lock_contention_skips_before_any_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            api_marker = temp / "api-called"
            self._write_executable(fake_bin / "flock", "#!/bin/sh\nexit 1\n")
            self._write_executable(
                fake_bin / "curl",
                f"#!/bin/sh\ntouch '{api_marker}'\nexit 99\n",
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FLOCK_BIN": str(fake_bin / "flock"),
                    "LOCK_FILE": str(temp / "daily.lock"),
                    "SYNC_DATE": "2026-07-11",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("skipped", result.stdout)
            self.assertFalse(api_marker.exists())

    def test_non_trading_day_refreshes_calendar_then_skips_market_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            curl_log = temp / "curl.log"
            self._write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            self._write_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "url=''\n"
                "for arg in \"$@\"; do case \"$arg\" in http://*|https://*) url=\"$arg\" ;; esac; done\n"
                f"printf '%s\\n' \"$*\" >> '{curl_log}'\n"
                "case \"$url\" in\n"
                "  */api/sync-jobs) printf '%s\\n' '{\"id\":\"calendar-job\",\"status\":\"queued\"}' ;;\n"
                "  */api/sync-jobs/calendar-job) printf '%s\\n' '{\"id\":\"calendar-job\",\"status\":\"ok\"}' ;;\n"
                "  */api/trade-calendars/2026-07-11) printf '%s\\n' '{\"calDate\":\"2026-07-11\",\"isOpen\":false}' ;;\n"
                "  *) printf '%s\\n' \"unexpected URL: $url\" >&2; exit 98 ;;\n"
                "esac\n",
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FLOCK_BIN": str(fake_bin / "flock"),
                    "LOCK_FILE": str(temp / "daily.lock"),
                    "SYNC_DATE": "2026-07-11",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("non_trading_day", result.stdout)
            calls = curl_log.read_text(encoding="utf-8")
            self.assertIn('"action":"trade_calendar"', calls)
            self.assertIn("/api/trade-calendars/2026-07-11", calls)
            self.assertNotIn('"action":"daily_market"', calls)
            self.assertNotIn('"action":"market_fundamentals"', calls)

    def test_success_refreshes_overview_and_runs_quality_only_after_job_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            curl_log = temp / "curl.log"
            self._write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            self._write_executable(
                fake_bin / "curl",
                "#!/bin/sh\n"
                "url=''\n"
                "for arg in \"$@\"; do case \"$arg\" in http://*|https://*) url=\"$arg\" ;; esac; done\n"
                f"printf '%s\\n' \"$*\" >> '{curl_log}'\n"
                "case \"$url\" in\n"
                "  */api/trade-calendars/2026-07-11) printf '%s\\n' '{\"isOpen\":true}' ;;\n"
                "  */api/sync-jobs) printf '%s\\n' '{\"id\":\"job-1\",\"status\":\"queued\"}' ;;\n"
                "  */api/sync-jobs/job-1) printf '%s\\n' '{\"id\":\"job-1\",\"status\":\"ok\"}' ;;\n"
                "  *'/api/db/overview?refresh=true') printf '%s\\n' '{}' ;;\n"
                "  *'/api/stocks?limit=20') printf '%s\\n' '[{\"ts_code\":\"600000.SH\"},{\"ts_code\":\"000001.SZ\"},{\"ts_code\":\"600000.SH\"}]' ;;\n"
                "  */api/data-quality/runs) printf '%s\\n' '{\"qualityRunId\":\"quality-1\",\"status\":\"ready\"}' ;;\n"
                "  *'/api/health?include_counts=false') printf '%s\\n' '{\"status\":\"ok\"}' ;;\n"
                "  *) printf '%s\\n' \"unexpected URL: $url\" >&2; exit 98 ;;\n"
                "esac\n",
            )

            result = subprocess.run(
                ["bash", str(SCRIPT)],
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "FLOCK_BIN": str(fake_bin / "flock"),
                    "LOCK_FILE": str(temp / "daily.lock"),
                    "SYNC_DATE": "2026-07-11",
                    "API_BASE": "http://unit.test",
                    "QUALITY_UNIVERSE_DIR": str(temp / "quality-universes"),
                    "QUALITY_UNIVERSE_CONTAINER_ROOT": "/app/outputs/quality-universes",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = curl_log.read_text(encoding="utf-8")
            calendar_submit = calls.index('"action":"trade_calendar"')
            calendar_read = calls.index("/api/trade-calendars/2026-07-11")
            daily_submit = calls.index('"action":"daily_market"')
            fundamentals_submit = calls.index('"action":"market_fundamentals"')
            job_poll = calls.index("/api/sync-jobs/job-1")
            overview = calls.index("/api/db/overview?refresh=true")
            quality = calls.index("/api/data-quality/runs")
            self.assertLess(calendar_submit, calendar_read)
            self.assertLess(calendar_read, daily_submit)
            self.assertLess(daily_submit, fundamentals_submit)
            self.assertLess(fundamentals_submit, overview)
            self.assertLess(job_poll, overview)
            self.assertLess(overview, quality)
            daily_call = next(line for line in calls.splitlines() if '"action":"daily_market"' in line)
            artifacts = list((temp / "quality-universes").glob("*.txt"))
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].read_text(encoding="utf-8"), "000001.SZ\n600000.SH\n")
            self.assertFalse(list((temp / "quality-universes").glob("*.tmp.*")))
            self.assertIn('"universe_source":"/app/outputs/quality-universes/', calls)
            self.assertIn('"benchmark":"000300.SH"', calls)
            self.assertIn('"benchmark":"000300.SH"', daily_call)
            self.assertIn('"rate_per_minute":150', calls)
            self.assertIn("finish daily market sync", result.stdout)

    @staticmethod
    def _write_executable(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
