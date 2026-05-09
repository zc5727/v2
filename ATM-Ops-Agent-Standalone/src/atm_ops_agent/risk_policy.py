from __future__ import annotations

from dataclasses import dataclass

from atm_ops_agent.config import RiskPolicyConfig, Server


DEFAULT_HIGH_RISK_ACTIONS = {
    "service-restart",
    "frontend-rollback",
    "data-repair-run",
    "mysql-repair",
    "spark-backfill",
    "xxl-job-rerun",
}


@dataclass(frozen=True, slots=True)
class RiskDecision:
    risk_level: str
    approval_required: bool
    reasons: list[str]


def evaluate_risk(
    policy: RiskPolicyConfig,
    *,
    server: Server,
    action: str,
    mutating: bool,
    confirm_change: bool,
    approval_ticket: str | None,
) -> RiskDecision:
    reasons: list[str] = []
    approval_required = False
    risk_level = "low"
    if not mutating:
        return RiskDecision(risk_level="low", approval_required=False, reasons=["read-only action"])
    risk_level = "medium"
    if not confirm_change:
        reasons.append("mutating action requires confirm_change")
    if policy.freeze_changes:
        reasons.append("change freeze is enabled")
        risk_level = "high"
    if _is_production(server.environment):
        risk_level = "high"
        if policy.require_approval_for_production:
            approval_required = True
            reasons.append(f"server environment is {server.environment}")
    if action in _high_risk_actions(policy):
        risk_level = "high"
        if policy.require_approval_for_high_risk:
            approval_required = True
            reasons.append(f"action {action} is marked high risk")
    if approval_required and not (approval_ticket or "").strip():
        reasons.append("approval_ticket is required")
    if not reasons:
        reasons.append("mutating action confirmed")
    return RiskDecision(risk_level=risk_level, approval_required=approval_required, reasons=reasons)


def enforce_risk(
    policy: RiskPolicyConfig,
    *,
    server: Server,
    action: str,
    mutating: bool,
    confirm_change: bool,
    approval_ticket: str | None,
) -> RiskDecision:
    decision = evaluate_risk(
        policy,
        server=server,
        action=action,
        mutating=mutating,
        confirm_change=confirm_change,
        approval_ticket=approval_ticket,
    )
    if not mutating:
        return decision
    if not confirm_change:
        raise PermissionError(f"{action} 属于变更动作，需要先勾选变更确认")
    if policy.freeze_changes:
        raise PermissionError("当前处于变更冻结期，变更动作已被阻止")
    if decision.approval_required and not (approval_ticket or "").strip():
        raise PermissionError(f"{action} 作用于 {server.environment or 'unknown'} 环境，需要填写审批单号")
    return decision


def _is_production(environment: str) -> bool:
    return environment.strip().lower() in {"prod", "production"}


def _high_risk_actions(policy: RiskPolicyConfig) -> set[str]:
    configured = {item.strip() for item in policy.high_risk_actions if item.strip()}
    return configured or set(DEFAULT_HIGH_RISK_ACTIONS)
