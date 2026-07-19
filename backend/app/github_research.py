from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from .research_orchestration import (
    AUTHORIZED_RESEARCH_APPROVER,
    RESEARCH_STATE_LABELS,
    CommentSnapshot,
    IssueSnapshot,
    OrchestrationResult,
    ResearchAuthorizationError,
    ResearchStateTransitionError,
    apply_issue_plan,
    invalidate_issue_plan,
)
from .research_plan import (
    PreparedResearchPlan,
    ResearchPlanError,
    ResearchServerLimits,
    prepare_research_plan,
)


class GitHubResearchError(RuntimeError):
    pass


class GitHubUnavailableError(GitHubResearchError):
    pass


class GitHubPermissionError(GitHubResearchError):
    pass


@dataclass(frozen=True)
class PollResult:
    github_available: bool
    processed: tuple[OrchestrationResult, ...] = ()
    errors: tuple[str, ...] = ()


SessionFactory = Callable[[], Session]


class GitHubIssueClient:
    """只暴露 Issue、评论与标签接口，不提供代码推送、合并或设置接口。"""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        api_base: str = "https://api.github.com",
        timeout_seconds: int = 20,
    ) -> None:
        if repository.count("/") != 1:
            raise ValueError("GITHUB_REPOSITORY 必须是 owner/repo")
        if not token:
            raise ValueError("研究编排器要求单独的 GitHub Issue token")
        self.repository = repository
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_env(cls) -> "GitHubIssueClient":
        return cls(
            os.getenv("GITHUB_REPOSITORY", "Jettlin927/Quantitative_trading"),
            os.getenv("RESEARCH_GITHUB_TOKEN", ""),
            api_base=os.getenv("GITHUB_API_URL", "https://api.github.com"),
        )

    def list_research_issues(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urlencode(
                {
                    "state": "all",
                    "labels": "类型:策略研究",
                    "per_page": "100",
                    "page": str(page),
                }
            )
            batch = self._request("GET", f"/repos/{self.repository}/issues?{query}")
            issues.extend(item for item in batch if "pull_request" not in item)
            if len(batch) < 100:
                return issues
            page += 1

    def list_comments(self, issue_number: int) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        page = 1
        while True:
            query = urlencode({"per_page": "100", "page": str(page)})
            batch = self._request(
                "GET", f"/repos/{self.repository}/issues/{issue_number}/comments?{query}"
            )
            comments.extend(batch)
            if len(batch) < 100:
                return comments
            page += 1

    def confirm_comment(
        self,
        issue_number: int,
        body: str,
        existing_comments: list[dict[str, Any]],
        *,
        marker: str,
    ) -> dict[str, Any]:
        existing = next(
            (item for item in existing_comments if marker in str(item.get("body") or "")),
            None,
        )
        if existing is not None:
            return self._request(
                "PATCH",
                f"/repos/{self.repository}/issues/comments/{int(existing['id'])}",
                {"body": body},
            )
        return self._request(
            "POST",
            f"/repos/{self.repository}/issues/{issue_number}/comments",
            {"body": body},
        )

    def set_state_label(
        self,
        issue_number: int,
        current_labels: set[str],
        desired_label: str,
    ) -> None:
        if desired_label not in current_labels:
            self._request(
                "POST",
                f"/repos/{self.repository}/issues/{issue_number}/labels",
                {"labels": [desired_label]},
            )
        for label in sorted((current_labels & RESEARCH_STATE_LABELS) - {desired_label}):
            self._request(
                "DELETE",
                f"/repos/{self.repository}/issues/{issue_number}/labels/{quote(label, safe='')}",
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.api_base + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-research-orchestrator/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise GitHubPermissionError(
                    f"GitHub 拒绝 {method} {path}：HTTP {exc.code}"
                ) from exc
            if exc.code == 404 and method == "DELETE":
                return None
            if exc.code >= 500 or exc.code == 429:
                raise GitHubUnavailableError(f"GitHub 暂时不可用：HTTP {exc.code}") from exc
            raise GitHubResearchError(f"GitHub 请求失败：HTTP {exc.code}") from exc
        except (TimeoutError, URLError) as exc:
            raise GitHubUnavailableError(f"GitHub 连接失败：{type(exc).__name__}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))


def poll_research_issues_once(
    client: GitHubIssueClient,
    session_factory: SessionFactory,
    *,
    app_git_commit: str,
    app_git_ref: str,
    limits: ResearchServerLimits | None = None,
) -> PollResult:
    server_limits = limits or ResearchServerLimits.from_env()
    try:
        issues = client.list_research_issues()
    except (GitHubPermissionError, GitHubUnavailableError) as exc:
        return PollResult(github_available=False, errors=(str(exc),))

    processed: list[OrchestrationResult] = []
    errors: list[str] = []
    for raw_issue in issues:
        issue = _issue_snapshot(raw_issue)
        raw_comments: list[dict[str, Any]] = []
        try:
            raw_comments = client.list_comments(issue.number)
            comments = [_comment_snapshot(item) for item in raw_comments]
            prepared = prepare_research_plan(issue.body, limits=server_limits)
            approval_found = any(
                item.author_login == AUTHORIZED_RESEARCH_APPROVER
                and item.body == prepared.approval_comment
                for item in comments
            )
            write_confirmed = False
            if approval_found:
                marker = f"<!-- research-orchestrator:authorization:{prepared.plan_sha256} -->"
                client.confirm_comment(
                    issue.number,
                    _authorization_comment(prepared, marker),
                    raw_comments,
                    marker=marker,
                )
                write_confirmed = True
            with session_factory.begin() as db:
                result = apply_issue_plan(
                    db,
                    issue,
                    comments,
                    prepared,
                    app_git_commit=app_git_commit,
                    app_git_ref=app_git_ref,
                    authorization_write_confirmed=write_confirmed,
                )
            client.set_state_label(issue.number, set(issue.labels), result.desired_label)
            if result.state == "blocked" and approval_found:
                marker = f"<!-- research-orchestrator:blocked:{prepared.plan_sha256} -->"
                client.confirm_comment(
                    issue.number,
                    _blocked_comment(prepared, marker, result.reason),
                    raw_comments,
                    marker=marker,
                )
            elif not approval_found:
                marker_kind = "pending" if result.state == "pending_approval" else "approval-invalid"
                marker = (
                    f"<!-- research-orchestrator:{marker_kind}:{prepared.plan_sha256} -->"
                )
                client.confirm_comment(
                    issue.number,
                    (
                        _pending_comment(prepared, marker)
                        if result.state == "pending_approval"
                        else _approval_invalid_comment(prepared, marker, result.reason)
                    ),
                    raw_comments,
                    marker=marker,
                )
            processed.append(result)
        except (ResearchPlanError, ResearchAuthorizationError, ResearchStateTransitionError) as exc:
            message = f"研究 Issue #{issue.number} 计划或状态无效：{exc}"
            errors.append(message)
            with session_factory.begin() as db:
                invalidate_issue_plan(
                    db,
                    issue.number,
                    issue.body,
                    f"当前 Issue 机器计划无效：{exc}",
                )
            try:
                marker_hash = sha256(issue.body.encode("utf-8")).hexdigest()
                marker = f"<!-- research-orchestrator:invalid:{marker_hash} -->"
                client.confirm_comment(
                    issue.number,
                    f"{marker}\n自动校验未通过：{exc}\n\n未创建正式研究，也未进入服务端队列。",
                    raw_comments,
                    marker=marker,
                )
                client.set_state_label(issue.number, set(issue.labels), "研究:受阻")
            except (GitHubPermissionError, GitHubUnavailableError) as write_exc:
                return PollResult(
                    github_available=False,
                    processed=tuple(processed),
                    errors=tuple([*errors, str(write_exc)]),
                )
        except (GitHubPermissionError, GitHubUnavailableError) as exc:
            return PollResult(
                github_available=False,
                processed=tuple(processed),
                errors=tuple([*errors, str(exc)]),
            )
        except GitHubResearchError as exc:
            errors.append(f"研究 Issue #{issue.number} GitHub 操作失败：{exc}")
    return PollResult(
        github_available=True,
        processed=tuple(processed),
        errors=tuple(errors),
    )


def _issue_snapshot(raw: dict[str, Any]) -> IssueSnapshot:
    labels = tuple(
        str(item.get("name") or "")
        for item in raw.get("labels") or []
        if str(item.get("name") or "")
    )
    return IssueSnapshot(
        number=int(raw["number"]),
        state=str(raw.get("state") or ""),
        body=str(raw.get("body") or ""),
        labels=labels,
    )


def _comment_snapshot(raw: dict[str, Any]) -> CommentSnapshot:
    return CommentSnapshot(
        id=int(raw["id"]),
        author_login=str((raw.get("user") or {}).get("login") or ""),
        body=str(raw.get("body") or ""),
    )


def _pending_comment(prepared: PreparedResearchPlan, marker: str) -> str:
    return (
        f"{marker}\n机器计划已规范化冻结，计划哈希：`{prepared.plan_sha256}`。\n\n"
        "当前仍是研究提案，未创建正式研究、未进入服务端队列。仅 GitHub 用户 "
        f"`{AUTHORIZED_RESEARCH_APPROVER}` 的精确评论 `{prepared.approval_comment}` 才构成批准。"
    )


def _authorization_comment(prepared: PreparedResearchPlan, marker: str) -> str:
    return (
        f"{marker}\n已读回授权用户的精确批准评论，并确认当前 GitHub token 具有 Issue 写权限。\n\n"
        f"计划哈希：`{prepared.plan_sha256}`。服务端仍会校验 Issue 状态、已部署 main 提交、"
        "静态策略登记与资源预算；任一门禁失败都不会启动正式研究。"
    )


def _approval_invalid_comment(
    prepared: PreparedResearchPlan,
    marker: str,
    reason: str | None,
) -> str:
    return (
        f"{marker}\n原批准已失效：{reason or '精确批准评论不再存在'}。\n\n"
        f"计划哈希：`{prepared.plan_sha256}`。编排器已阻止新阶段；"
        "已有事件、checkpoint 与工件均保留。"
    )


def _blocked_comment(
    prepared: PreparedResearchPlan,
    marker: str,
    reason: str | None,
) -> str:
    return (
        f"{marker}\n当前计划未进入新研究阶段：{reason or '服务端门禁未通过'}。\n\n"
        f"计划哈希：`{prepared.plan_sha256}`。已有事件、checkpoint 与工件均保留。"
    )
