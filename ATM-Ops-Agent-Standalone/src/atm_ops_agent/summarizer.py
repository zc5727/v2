from __future__ import annotations

from dataclasses import dataclass
import re

from atm_ops_agent.ssh_client import CommandResult


ERROR_PATTERNS = (
    "error",
    "failed",
    "exception",
    "traceback",
    "denied",
    "timeout",
    "inactive (dead)",
    "not found",
    "no such file",
    "connection refused",
)

RUNNING_PATTERNS = (
    "active (running)",
    "is running",
    "up ",
    "listening",
)


@dataclass(slots=True)
class ExecutionSummary:
    action_success: bool
    issue_resolved: str
    headline: str
    findings: list[str]

    def render_text(self) -> str:
        lines = [
            "==> summary",
            f"action_success: {'yes' if self.action_success else 'no'}",
            f"issue_resolved: {self.issue_resolved}",
            f"headline: {self.headline}",
        ]
        if self.findings:
            lines.append("findings:")
            lines.extend(f"- {item}" for item in self.findings)
        return "\n".join(lines)


def summarize_execution(action: str, result: CommandResult) -> ExecutionSummary:
    stdout = (result.stdout or "").lower()
    stderr = (result.stderr or "").lower()
    combined = "\n".join(part for part in (stdout, stderr) if part)

    findings: list[str] = []
    action_success = result.returncode == 0

    if result.returncode != 0:
        findings.append(f"command returned non-zero exit code {result.returncode}")

    error_hits = _collect_error_hits(combined)
    findings.extend(error_hits)

    if action == "service-status":
        if "active (running)" in stdout:
            headline = "service is running"
            issue_resolved = "yes"
        elif "inactive (dead)" in stdout or "failed" in combined:
            headline = "service is not healthy"
            issue_resolved = "no"
            action_success = False
        else:
            headline = "service status collected"
            issue_resolved = "unknown"
    elif action == "service-restart":
        if "active (running)" in stdout and result.returncode == 0:
            headline = "service restart finished and service appears healthy"
            issue_resolved = "yes"
        else:
            headline = "service restart did not fully confirm recovery"
            issue_resolved = "no" if result.returncode != 0 else "unknown"
    elif action == "health-check":
        if result.returncode == 0 and error_hits:
            headline = "health check completed but anomalies were detected"
            issue_resolved = "unknown"
        elif result.returncode == 0:
            headline = "health check completed without obvious failures"
            issue_resolved = "yes"
        else:
            headline = "health check failed"
            issue_resolved = "no"
    else:
        if result.returncode == 0 and not error_hits:
            headline = "action completed without obvious failures"
            issue_resolved = "yes"
        elif result.returncode == 0:
            headline = "action completed but output still contains warning signs"
            issue_resolved = "unknown"
        else:
            headline = "action failed"
            issue_resolved = "no"

    if action in {"service-status", "service-restart"} and _contains_running_signal(stdout):
        findings.append("service output contains running signal")

    findings = _unique(findings)
    return ExecutionSummary(
        action_success=action_success,
        issue_resolved=issue_resolved,
        headline=headline,
        findings=findings[:8],
    )


def _collect_error_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in ERROR_PATTERNS:
        if pattern in text:
            hits.append(f"detected '{pattern}' in command output")
    return hits


def _contains_running_signal(text: str) -> bool:
    return any(pattern in text for pattern in RUNNING_PATTERNS)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique_items: list[str] = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item.strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_items.append(normalized)
    return unique_items
