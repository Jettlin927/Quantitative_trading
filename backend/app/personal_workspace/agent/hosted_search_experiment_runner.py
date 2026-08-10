"""两阶段、离线计划优先的 Hosted Search 合同实验入口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from .hosted_search_experiment import (
    ANTHROPIC_MESSAGES_URL,
    DEEPSEEK_MODEL,
    EXPERIMENT_REVISION,
    MAX_OUTPUT_TOKENS,
    PUBLIC_EXPERIMENT_INPUT,
    PUBLIC_EXPERIMENT_INSTRUCTIONS,
    WEB_SEARCH_TOOL_TYPE,
)


PLAN_SCHEMA = "hosted-search-experiment-plan-v1"
PUBLIC_CASE_ID = "nvidia-sec-annual-report-v1"
PUBLIC_SOURCE_ALLOWLIST = ("www.sec.gov",)
COST_BOUND_REVISION = "deepseek-hosted-search-cost-bound-v1"
EXPERIMENT_DAILY_LIMIT_CNY = Decimal("5")
XNYS_TIMEZONE = ZoneInfo("America/New_York")
DEEPSEEK_BALANCE_ENDPOINT = "https://api.deepseek.com/user/balance"
DEEPSEEK_KV_CACHE_POLICY_URL = "https://api-docs.deepseek.com/guides/kv_cache"
DEEPSEEK_PRIVACY_POLICY_URL = (
    "https://cdn.deepseek.com/policies/en-US/deepseek-privacy-policy.html"
)


@dataclass(frozen=True)
class ExperimentPricingSnapshot:
    revision: str
    source_url: str
    cache_hit_input_usd_per_million: Decimal
    cache_miss_input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    hosted_search_usd_per_request: Decimal | None
    hosted_search_source_url: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = _json_value(asdict(self))
        payload["sha256"] = _canonical_sha256(payload)
        return payload


@dataclass(frozen=True)
class ExperimentBudgetPolicy:
    market_date: date
    fx_cny_per_usd: Decimal
    fx_snapshot: str
    per_run_reserve_cny: Decimal
    daily_limit_cny: Decimal
    ledger_path: str
    revision: str

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class ExperimentDataPolicySnapshot:
    revision: str
    disk_context_cache_default: bool
    store_false_opt_out_available: bool
    zero_data_retention_available: bool
    inputs_outputs_handled_under_provider_policy: bool
    source_urls: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = _json_value(asdict(self))
        payload["sha256"] = _canonical_sha256(payload)
        return payload


@dataclass(frozen=True)
class ExperimentPlan:
    schema: str
    git_sha: str
    created_at: datetime
    expires_at: datetime
    credential_path: str
    artifact_dir: str
    provider: Mapping[str, Any]
    public_case: Mapping[str, Any]
    data_policy: ExperimentDataPolicySnapshot
    pricing: ExperimentPricingSnapshot
    budget: ExperimentBudgetPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "git_sha": self.git_sha,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "credential_path": self.credential_path,
            "artifact_dir": self.artifact_dir,
            "provider": _json_value(dict(self.provider)),
            "public_case": _json_value(dict(self.public_case)),
            "data_policy": self.data_policy.to_dict(),
            "pricing": self.pricing.to_dict(),
            "budget": self.budget.to_dict(),
        }

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.to_dict())


@dataclass(frozen=True)
class ExperimentReport:
    schema: str
    plan_sha256: str
    status: str
    failure_code: str | None
    provider_requests: int
    billing_reads: int = 0
    billing_readback: Mapping[str, Any] | None = None
    cost_usd: Decimal | None = None
    cost_cny: Decimal | None = None
    budget_settlement_cny: Decimal = Decimal("0")
    daily_cumulative_cny: Decimal = Decimal("0")
    usage: Mapping[str, Any] | None = None
    events: tuple[Mapping[str, Any], ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    citations: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))


@dataclass(frozen=True)
class VerificationResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class SafeHttpsWebEvidenceVerifier:
    """对 Hosted Search URL 做独立、SSRF-safe 的正文读回。"""

    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...] = PUBLIC_SOURCE_ALLOWLIST,
        transport: Callable[..., VerificationResponse] | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        timeout_seconds: int = 5,
        max_body_bytes: int = 512_000,
        max_redirects: int = 3,
    ) -> None:
        self._allowed_hosts = frozenset(item.lower() for item in allowed_hosts)
        self._transport = transport or _default_verification_transport
        self._resolver = resolver or _resolve_host
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._max_body_bytes = max_body_bytes
        self._max_redirects = max_redirects

    def verify(
        self, *, url: str, title: str, provider_excerpt: str
    ):
        from .hosted_search_experiment import VerifiedWebEvidence

        current = url
        response: VerificationResponse | None = None
        for redirect_count in range(self._max_redirects + 1):
            addresses = self._validate_url(current)
            parsed_current = urlsplit(current)
            response = self._transport(
                current,
                timeout_seconds=self._timeout_seconds,
                max_body_bytes=self._max_body_bytes,
                resolved_ip=addresses[0],
                tls_hostname=parsed_current.hostname,
            )
            self._validate_url(response.final_url)
            if response.final_url != current:
                raise ValueError("source_verification_failed")
            if response.status_code in {301, 302, 303, 307, 308}:
                location = _header(response.headers, "location")
                if not location or redirect_count >= self._max_redirects:
                    raise ValueError("source_verification_failed")
                current = urljoin(current, location)
                continue
            break
        if response is None or response.status_code != 200:
            raise ValueError("source_verification_failed")
        content_type = (_header(response.headers, "content-type") or "").split(";", 1)[0].strip().lower()
        if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
            raise ValueError("source_verification_failed")
        if not response.body or len(response.body) > self._max_body_bytes:
            raise ValueError("source_verification_failed")
        decoded = response.body.decode("utf-8", errors="replace")
        body_text = _html_text(decoded) if "html" in content_type else decoded
        normalized_body = _normalized_text(body_text)
        normalized_excerpts = tuple(
            _normalized_text(item) for item in provider_excerpt.splitlines()
        )
        if not normalized_excerpts or any(
            not item or item not in normalized_body for item in normalized_excerpts
        ):
            raise ValueError("source_verification_failed")
        body_sha = sha256(response.body).hexdigest()
        return VerifiedWebEvidence(
            evidence_id=f"web:{sha256((current + '|' + body_sha).encode('utf-8')).hexdigest()}",
            source_url=current,
            title=title,
            excerpt=provider_excerpt,
            content_sha256=body_sha,
            verified_at=self._clock(),
        )

    def _validate_url(self, url: str) -> tuple[str, ...]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or host not in self._allowed_hosts
        ):
            raise ValueError("source_verification_failed")
        try:
            direct = ipaddress.ip_address(host)
        except ValueError:
            direct = None
        if direct is not None and not direct.is_global:
            raise ValueError("source_verification_failed")
        addresses = self._resolver(host)
        if not addresses:
            raise ValueError("source_verification_failed")
        for address in addresses:
            try:
                parsed_ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise ValueError("source_verification_failed") from exc
            if not parsed_ip.is_global:
                raise ValueError("source_verification_failed")
        return addresses


class HostedSearchExperimentRunner:
    def __init__(
        self,
        *,
        git_reader: Callable[[], tuple[str, bool]] | None = None,
        budget_ledger_path: str | None = None,
        clock: Callable[[], datetime],
    ) -> None:
        self._git_reader = git_reader or _git_state_reader
        self._budget_ledger_path = budget_ledger_path or str(
            Path.home()
            / ".local"
            / "state"
            / "quantitative-trading"
            / "hosted-search-experiment-budget-v1.json"
        )
        self._clock = clock

    def plan(
        self,
        *,
        git_sha: str,
        credential_path: str,
        artifact_dir: str,
        expires_at: datetime,
        max_tool_calls: int,
        max_queries: int,
        pricing: ExperimentPricingSnapshot,
        budget: ExperimentBudgetPolicy,
        timeout_seconds: int = 90,
    ) -> ExperimentPlan:
        now = self._clock()
        expected_market_date = now.astimezone(XNYS_TIMEZONE).date()
        if (
            len(git_sha) != 40
            or any(character not in "0123456789abcdef" for character in git_sha)
            or not credential_path
            or not artifact_dir
            or now.tzinfo is None
            or expires_at.tzinfo is None
            or expires_at <= now
            or not _valid_plan_limit(max_tool_calls, maximum=5)
            or not _valid_plan_limit(max_queries, maximum=10)
            or not _finite_nonnegative(
                pricing.cache_hit_input_usd_per_million
            )
            or not _finite_nonnegative(
                pricing.cache_miss_input_usd_per_million
            )
            or not _finite_nonnegative(pricing.output_usd_per_million)
            or not _finite_optional_nonnegative(
                pricing.hosted_search_usd_per_request
            )
            or bool(pricing.hosted_search_source_url)
            != (pricing.hosted_search_usd_per_request is not None)
            or not pricing.revision.strip()
            or not pricing.source_url.strip()
            or not _finite_positive(budget.fx_cny_per_usd)
            or not _finite_positive(budget.per_run_reserve_cny)
            or not _finite_positive(budget.daily_limit_cny)
            or not budget.fx_snapshot.strip()
            or not budget.ledger_path.strip()
            or budget.daily_limit_cny != EXPERIMENT_DAILY_LIMIT_CNY
            or budget.daily_limit_cny < budget.per_run_reserve_cny
            or budget.market_date != expected_market_date
            or budget.ledger_path != self._budget_ledger_path
            or not 1 <= timeout_seconds <= 300
        ):
            raise ValueError("hosted_search_plan_invalid")
        cost_bound = _maximum_cost_bound(
            pricing=pricing,
            budget=budget,
            max_tool_calls=max_tool_calls,
            max_queries=max_queries,
        )
        if (
            cost_bound["maximum_cost_cny"] is not None
            and budget.per_run_reserve_cny
            < Decimal(cost_bound["maximum_cost_cny"])
        ):
            raise ValueError("hosted_search_plan_invalid")
        return ExperimentPlan(
            schema=PLAN_SCHEMA,
            git_sha=git_sha,
            created_at=now,
            expires_at=expires_at,
            credential_path=credential_path,
            artifact_dir=artifact_dir,
            provider={
                "endpoint": ANTHROPIC_MESSAGES_URL,
                "model": DEEPSEEK_MODEL,
                "tool_type": WEB_SEARCH_TOOL_TYPE,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "max_provider_requests": 1,
                "balance_endpoint": DEEPSEEK_BALANCE_ENDPOINT,
                "max_billing_reads": 2,
                "max_tool_calls": max_tool_calls,
                "max_queries": max_queries,
                "cost_bound": cost_bound,
                "timeout_seconds": timeout_seconds,
                "stream": False,
                "experiment_revision": EXPERIMENT_REVISION,
            },
            public_case={
                "case_id": PUBLIC_CASE_ID,
                "instructions": PUBLIC_EXPERIMENT_INSTRUCTIONS,
                "input": PUBLIC_EXPERIMENT_INPUT,
                "source_allowlist": list(PUBLIC_SOURCE_ALLOWLIST),
            },
            data_policy=_fixed_data_policy_snapshot(),
            pricing=pricing,
            budget=budget,
        )

    def run(
        self, plan: ExperimentPlan, *, approved_plan_sha256: str
    ) -> ExperimentReport:
        if approved_plan_sha256 != plan.sha256:
            return _failed_report(plan, "authorization_mismatch")
        if self._clock() >= plan.expires_at:
            return _failed_report(plan, "plan_expired")
        if not _valid_bound_plan(plan):
            return _failed_report(plan, "plan_contract_invalid")
        if (
            plan.budget.market_date
            != self._clock().astimezone(XNYS_TIMEZONE).date()
            or plan.budget.ledger_path != self._budget_ledger_path
        ):
            return _failed_report(plan, "plan_contract_invalid")
        try:
            current_git_sha, worktree_clean = self._git_reader()
        except Exception:
            return _failed_report(plan, "git_state_unavailable")
        if current_git_sha != plan.git_sha or worktree_clean is not True:
            return _failed_report(plan, "git_state_mismatch")
        return _failed_report(plan, "provider_cost_bound_unavailable")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _failed_report(
    plan: ExperimentPlan,
    code: str,
    *,
    daily_cumulative_cny: Decimal = Decimal("0"),
) -> ExperimentReport:
    return ExperimentReport(
        schema="hosted-search-experiment-report-v1",
        plan_sha256=plan.sha256,
        status="unavailable",
        failure_code=code,
        provider_requests=0,
        daily_cumulative_cny=daily_cumulative_cny,
    )


def _fixed_data_policy_snapshot() -> ExperimentDataPolicySnapshot:
    return ExperimentDataPolicySnapshot(
        revision="deepseek-data-policy-2026-08-10",
        disk_context_cache_default=True,
        store_false_opt_out_available=False,
        zero_data_retention_available=False,
        inputs_outputs_handled_under_provider_policy=True,
        source_urls=(
            DEEPSEEK_KV_CACHE_POLICY_URL,
            DEEPSEEK_PRIVACY_POLICY_URL,
        ),
    )


def _valid_bound_plan(plan: ExperimentPlan) -> bool:
    return (
        plan.schema == PLAN_SCHEMA
        and len(plan.git_sha) == 40
        and all(character in "0123456789abcdef" for character in plan.git_sha)
        and plan.created_at.tzinfo is not None
        and plan.created_at.utcoffset() is not None
        and plan.expires_at.tzinfo is not None
        and plan.expires_at.utcoffset() is not None
        and plan.expires_at > plan.created_at
        and bool(plan.credential_path)
        and bool(plan.artifact_dir)
        and set(plan.provider) == {
            "endpoint",
            "model",
            "tool_type",
            "max_output_tokens",
            "max_provider_requests",
            "balance_endpoint",
            "max_billing_reads",
            "max_tool_calls",
            "max_queries",
            "cost_bound",
            "timeout_seconds",
            "stream",
            "experiment_revision",
        }
        and set(plan.public_case) == {
            "case_id",
            "instructions",
            "input",
            "source_allowlist",
        }
        and plan.provider.get("endpoint") == ANTHROPIC_MESSAGES_URL
        and plan.provider.get("model") == DEEPSEEK_MODEL
        and plan.provider.get("tool_type") == WEB_SEARCH_TOOL_TYPE
        and plan.provider.get("max_output_tokens") == MAX_OUTPUT_TOKENS
        and plan.provider.get("max_provider_requests") == 1
        and plan.provider.get("balance_endpoint") == DEEPSEEK_BALANCE_ENDPOINT
        and plan.provider.get("max_billing_reads") == 2
        and _valid_plan_limit(plan.provider.get("max_tool_calls"), maximum=5)
        and _valid_plan_limit(plan.provider.get("max_queries"), maximum=10)
        and plan.provider.get("cost_bound")
        == _maximum_cost_bound(
            pricing=plan.pricing,
            budget=plan.budget,
            max_tool_calls=int(plan.provider["max_tool_calls"]),
            max_queries=int(plan.provider["max_queries"]),
        )
        and plan.provider.get("experiment_revision") == EXPERIMENT_REVISION
        and plan.provider.get("stream") is False
        and isinstance(plan.provider.get("timeout_seconds"), int)
        and 1 <= int(plan.provider["timeout_seconds"]) <= 300
        and plan.public_case.get("case_id") == PUBLIC_CASE_ID
        and plan.public_case.get("input") == PUBLIC_EXPERIMENT_INPUT
        and plan.public_case.get("instructions") == PUBLIC_EXPERIMENT_INSTRUCTIONS
        and tuple(plan.public_case.get("source_allowlist") or ())
        == PUBLIC_SOURCE_ALLOWLIST
        and plan.data_policy.to_dict() == _fixed_data_policy_snapshot().to_dict()
        and bool(plan.pricing.revision.strip())
        and bool(plan.pricing.source_url.strip())
        and _finite_nonnegative(plan.pricing.cache_hit_input_usd_per_million)
        and _finite_nonnegative(plan.pricing.cache_miss_input_usd_per_million)
        and _finite_nonnegative(plan.pricing.output_usd_per_million)
        and _finite_optional_nonnegative(
            plan.pricing.hosted_search_usd_per_request
        )
        and bool(plan.pricing.hosted_search_source_url)
        == (plan.pricing.hosted_search_usd_per_request is not None)
        and _finite_positive(plan.budget.fx_cny_per_usd)
        and _finite_positive(plan.budget.per_run_reserve_cny)
        and _finite_positive(plan.budget.daily_limit_cny)
        and plan.budget.daily_limit_cny == EXPERIMENT_DAILY_LIMIT_CNY
        and plan.budget.per_run_reserve_cny <= plan.budget.daily_limit_cny
        and bool(plan.budget.fx_snapshot.strip())
        and bool(plan.budget.ledger_path.strip())
        and bool(plan.budget.revision.strip())
    )


def _valid_plan_limit(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= maximum
    )


def _finite_nonnegative(value: Any) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value >= 0


def _finite_optional_nonnegative(value: Any) -> bool:
    return value is None or _finite_nonnegative(value)


def _finite_positive(value: Any) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _maximum_cost_bound(
    *,
    pricing: ExperimentPricingSnapshot,
    budget: ExperimentBudgetPolicy,
    max_tool_calls: int,
    max_queries: int,
) -> dict[str, Any]:
    del max_tool_calls, max_queries
    del pricing, budget
    maximum_cost_usd: Decimal | None = None
    maximum_cost_cny: Decimal | None = None
    return _json_value(
        {
            "revision": COST_BOUND_REVISION,
            "basis": (
                "provider monetary maximum required; query, tool and token limits "
                "are not treated as an internal generation cost bound"
            ),
            "source_url": None,
            "maximum_cost_usd": maximum_cost_usd,
            "maximum_cost_cny": maximum_cost_cny,
        }
    )


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _resolve_host(host: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(
                    host, 443, type=socket.SOCK_STREAM
                )
            }
        )
    )


def _default_verification_transport(
    url: str,
    *,
    timeout_seconds: int,
    max_body_bytes: int,
    resolved_ip: str,
    tls_hostname: str,
) -> VerificationResponse:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    connection = _PinnedHTTPSConnection(
        tls_hostname,
        resolved_ip=resolved_ip,
        timeout=timeout_seconds,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Host": tls_hostname,
                "User-Agent": "quant-hosted-search-contract-experiment/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body = response.read(max_body_bytes + 1)
        if len(body) > max_body_bytes:
            raise ValueError("source_verification_failed")
        return VerificationResponse(
            status_code=response.status,
            headers={key: value for key, value in response.getheaders()},
            body=body,
            final_url=url,
        )
    finally:
        connection.close()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        *,
        resolved_ip: str,
        timeout: int,
    ) -> None:
        super().__init__(host, port=443, timeout=timeout)
        self._resolved_ip = resolved_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._resolved_ip, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(
            raw_socket,
            server_hostname=self.host,
        )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)


def _normalized_text(value: str) -> str:
    return " ".join(value.split())


def _git_state_reader() -> tuple[str, bool]:
    working_directory = Path(__file__).resolve().parent
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    worktree_status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return head, not worktree_status.strip()


def _plan_from_dict(value: Mapping[str, Any]) -> ExperimentPlan:
    if set(value) != {
        "schema",
        "git_sha",
        "created_at",
        "expires_at",
        "credential_path",
        "artifact_dir",
        "provider",
        "public_case",
        "data_policy",
        "pricing",
        "budget",
        "sha256",
    }:
        raise ValueError("plan_contract_invalid")
    data_policy = dict(value["data_policy"])
    if set(data_policy) != {
        "revision",
        "disk_context_cache_default",
        "store_false_opt_out_available",
        "zero_data_retention_available",
        "inputs_outputs_handled_under_provider_policy",
        "source_urls",
        "sha256",
    }:
        raise ValueError("plan_contract_invalid")
    data_policy_sha256 = data_policy.pop("sha256", None)
    if data_policy_sha256 != _canonical_sha256(data_policy):
        raise ValueError("data_policy_sha256_invalid")
    pricing = dict(value["pricing"])
    if set(pricing) != {
        "revision",
        "source_url",
        "cache_hit_input_usd_per_million",
        "cache_miss_input_usd_per_million",
        "output_usd_per_million",
        "hosted_search_usd_per_request",
        "hosted_search_source_url",
        "sha256",
    }:
        raise ValueError("plan_contract_invalid")
    pricing_sha256 = pricing.pop("sha256", None)
    if pricing_sha256 != _canonical_sha256(pricing):
        raise ValueError("pricing_sha256_invalid")
    budget = value["budget"]
    if not isinstance(budget, Mapping) or set(budget) != {
        "market_date",
        "fx_cny_per_usd",
        "fx_snapshot",
        "per_run_reserve_cny",
        "daily_limit_cny",
        "ledger_path",
        "revision",
    }:
        raise ValueError("plan_contract_invalid")
    plan = ExperimentPlan(
        schema=str(value["schema"]),
        git_sha=str(value["git_sha"]),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        expires_at=datetime.fromisoformat(str(value["expires_at"])),
        credential_path=str(value["credential_path"]),
        artifact_dir=str(value["artifact_dir"]),
        provider=dict(value["provider"]),
        public_case=dict(value["public_case"]),
        data_policy=ExperimentDataPolicySnapshot(
            revision=str(data_policy["revision"]),
            disk_context_cache_default=data_policy["disk_context_cache_default"],
            store_false_opt_out_available=data_policy[
                "store_false_opt_out_available"
            ],
            zero_data_retention_available=data_policy[
                "zero_data_retention_available"
            ],
            inputs_outputs_handled_under_provider_policy=data_policy[
                "inputs_outputs_handled_under_provider_policy"
            ],
            source_urls=tuple(data_policy["source_urls"]),
        ),
        pricing=ExperimentPricingSnapshot(
            revision=str(pricing["revision"]),
            source_url=str(pricing["source_url"]),
            cache_hit_input_usd_per_million=Decimal(
                pricing["cache_hit_input_usd_per_million"]
            ),
            cache_miss_input_usd_per_million=Decimal(
                pricing["cache_miss_input_usd_per_million"]
            ),
            output_usd_per_million=Decimal(pricing["output_usd_per_million"]),
            hosted_search_usd_per_request=(
                Decimal(pricing["hosted_search_usd_per_request"])
                if pricing.get("hosted_search_usd_per_request") is not None
                else None
            ),
            hosted_search_source_url=pricing.get("hosted_search_source_url"),
        ),
        budget=ExperimentBudgetPolicy(
            market_date=date.fromisoformat(str(budget["market_date"])),
            fx_cny_per_usd=Decimal(budget["fx_cny_per_usd"]),
            fx_snapshot=str(budget["fx_snapshot"]),
            per_run_reserve_cny=Decimal(budget["per_run_reserve_cny"]),
            daily_limit_cny=Decimal(budget["daily_limit_cny"]),
            ledger_path=str(budget["ledger_path"]),
            revision=str(budget["revision"]),
        ),
    )
    expected = value.get("sha256")
    if expected is not None and expected != plan.sha256:
        raise ValueError("plan_sha256_invalid")
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="隔离 Hosted Search 合同实验")
    commands = parser.add_subparsers(dest="command", required=True)
    plan_parser = commands.add_parser("plan", help="离线生成待批准计划")
    for name in (
        "plan-path",
        "artifact-dir",
        "credential-path",
        "git-sha",
        "expires-at",
        "fx-snapshot",
    ):
        plan_parser.add_argument(f"--{name}", required=True)
    plan_parser.add_argument("--max-tool-calls", type=int, required=True)
    plan_parser.add_argument("--max-queries", type=int, required=True)
    plan_parser.add_argument("--timeout-seconds", type=int, default=90)
    plan_parser.add_argument("--fx-cny-per-usd", type=Decimal, required=True)
    plan_parser.add_argument("--per-run-reserve-cny", type=Decimal, required=True)
    plan_parser.add_argument("--hosted-search-unit-usd", type=Decimal)
    plan_parser.add_argument("--hosted-search-pricing-source")
    run_parser = commands.add_parser("run", help="执行已精确批准的计划")
    run_parser.add_argument("--plan", required=True)
    run_parser.add_argument("--approved-plan-sha256", required=True)
    run_parser.add_argument("--execute", action="store_true", required=True)
    args = parser.parse_args(argv)

    if args.command == "plan":
        runner = HostedSearchExperimentRunner(
            clock=lambda: datetime.now(timezone.utc),
        )
        snapshot = ExperimentPricingSnapshot(
            revision="deepseek-v4-flash-2026-04-24",
            source_url="https://api-docs.deepseek.com/quick_start/pricing/",
            cache_hit_input_usd_per_million=Decimal("0.0028"),
            cache_miss_input_usd_per_million=Decimal("0.14"),
            output_usd_per_million=Decimal("0.28"),
            hosted_search_usd_per_request=args.hosted_search_unit_usd,
            hosted_search_source_url=args.hosted_search_pricing_source,
        )
        plan = runner.plan(
            git_sha=args.git_sha,
            credential_path=args.credential_path,
            artifact_dir=args.artifact_dir,
            expires_at=datetime.fromisoformat(args.expires_at),
            max_tool_calls=args.max_tool_calls,
            max_queries=args.max_queries,
            timeout_seconds=args.timeout_seconds,
            pricing=snapshot,
            budget=ExperimentBudgetPolicy(
                market_date=datetime.now(timezone.utc)
                .astimezone(XNYS_TIMEZONE)
                .date(),
                fx_cny_per_usd=args.fx_cny_per_usd,
                fx_snapshot=args.fx_snapshot,
                per_run_reserve_cny=args.per_run_reserve_cny,
                daily_limit_cny=EXPERIMENT_DAILY_LIMIT_CNY,
                ledger_path=runner._budget_ledger_path,
                revision="hosted-search-budget-v1",
            ),
        )
        destination = Path(args.plan_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as target:
            target.write(
                _canonical_json({**plan.to_dict(), "sha256": plan.sha256}) + "\n"
            )
        run_argv = [
            sys.executable,
            "-m",
            "backend.app.personal_workspace.agent.hosted_search_experiment_runner",
            "run",
            "--plan",
            str(destination),
            "--approved-plan-sha256",
            plan.sha256,
            "--execute",
        ]
        print(
            _canonical_json(
                {
                    "plan": str(destination),
                    "plan_sha256": plan.sha256,
                    "run_argv": run_argv,
                    "run_command": shlex.join(run_argv),
                }
            )
        )
        return 0

    value = json.loads(Path(args.plan).read_text("utf-8"))
    plan = _plan_from_dict(value)
    runner = HostedSearchExperimentRunner(
        clock=lambda: datetime.now(timezone.utc),
    )
    report = runner.run(plan, approved_plan_sha256=args.approved_plan_sha256)
    print(_canonical_json(report.to_dict()))
    return 0 if report.status == "completed" else 2


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
