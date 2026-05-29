import json
import os
import re
import subprocess
import sys
from pathlib import Path


PATCH_FILE_RE = re.compile(
    r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+?)\s*$",
    re.MULTILINE,
)
SKIP_PARTS = {"dist", "node_modules", "__pycache__"}


def main() -> int:
    payload = read_payload()
    if payload.get("hook_event_name") != "PostToolUse":
        return 0

    tool_name = str(payload.get("tool_name", ""))
    if tool_name not in {"apply_patch", "Edit", "Write"}:
        return 0

    patch_text = extract_tool_text(payload.get("tool_input"))
    changed_files = extract_changed_files(patch_text)
    if not changed_files:
        return 0

    repo_root = find_repo_root(get_start_path(payload))
    existing_files = [path for path in changed_files if should_check(repo_root, path)]
    if not existing_files:
        return 0

    check_script = repo_root / "scripts" / "check-file.ps1"
    if not check_script.exists():
        return block("Post-edit check script is missing: scripts/check-file.ps1")

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(check_script),
        *existing_files,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return block("Post-edit checks timed out after 300 seconds.")
    if result.returncode == 0:
        return 0

    output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return block(f"Post-edit checks failed. Fix these before continuing:\n{output}")


def read_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def get_start_path(payload: dict) -> Path:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return Path(cwd)
    return Path.cwd()


def extract_tool_text(tool_input) -> str:
    if isinstance(tool_input, str):
        return tool_input
    if isinstance(tool_input, dict):
        for key in ("command", "patch", "input"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
    return ""


def extract_changed_files(patch_text: str) -> list[str]:
    files = []
    for match in PATCH_FILE_RE.finditer(patch_text or ""):
        normalized = match.group(1).strip().replace("/", os.sep).replace("\\", os.sep)
        if normalized and normalized not in files:
            files.append(normalized)
    return files


def should_check(repo_root: Path, relative_path: str) -> bool:
    full_path = (repo_root / relative_path).resolve()
    try:
        full_path.relative_to(repo_root)
    except ValueError:
        return False
    if not full_path.exists() or not full_path.is_file():
        return False
    return not any(part.lower() in SKIP_PARTS for part in full_path.parts)


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".codex").exists() and (candidate / "AGENTS.md").exists():
            return candidate
    return Path.cwd().resolve()


def block(reason: str) -> int:
    print(
        json.dumps(
            {
                "decision": "block",
                "reason": reason,
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": reason,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
