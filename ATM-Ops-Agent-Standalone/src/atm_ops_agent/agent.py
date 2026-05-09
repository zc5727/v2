from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from atm_ops_agent.advanced_skills import (
    ADVANCED_SKILLS,
    all_action_names,
    build_app_log_search_command,
    build_clickhouse_table_check_command,
    build_data_repair_run_command,
    build_docker_log_command,
    build_frontend_rollback_command,
    build_frontend_rollback_preview_command,
    build_log_analysis_command,
    build_mysql_repair_command,
    build_spark_backfill_command,
    build_xxl_job_rerun_command,
)
from atm_ops_agent.atm_ops import (
    export_atm_bundle,
    get_deploy_presets,
    get_deploy_preset,
    load_atm_environment,
    read_atm_runbook,
    render_deploy_plan,
    render_package_deploy_preview,
    render_recovery_plan,
    render_server_recovery_plan,
)
from atm_ops_agent.alerts import AlertService
from atm_ops_agent.chat_agent import ChatMessage, OpsChatAgent
from atm_ops_agent.chat_graph import OpsChatGraph
from atm_ops_agent.config import Inventory, load_inventory
from atm_ops_agent.explainer import explain_execution
from atm_ops_agent.execution_guard import ExecutionGuard
from atm_ops_agent.feishu import FeishuDocClient
from atm_ops_agent.llm_gateway import LLMDiagnosis, build_ollama_config_snippet, diagnose_llm
from atm_ops_agent.monitor import CloudMonitorService, MetricSnapshot
from atm_ops_agent.custom_skills import (
    CustomSkillPreview,
    CustomSkillSpec,
    CustomSkillStore,
    build_custom_skill_command,
    parse_params_schema,
    preview_custom_skill_command,
)
from atm_ops_agent.ops_store import ActionRecord, IncidentRecord, OpsStore
from atm_ops_agent.planner import LLMPlanner, PlannedStep
from atm_ops_agent.playbooks import PLAYBOOKS
from atm_ops_agent.repair import render_data_repair_plan
from atm_ops_agent.reporter import write_report
from atm_ops_agent.risk_policy import RiskDecision, enforce_risk
from atm_ops_agent.ssh_client import CommandResult, SSHClient
from atm_ops_agent.summarizer import ExecutionSummary, summarize_execution


def _scenario_from_prompt(prompt: str) -> str:
    lowered = (prompt or "").lower()
    if any(token in lowered for token in ["spark", "master", "worker", "data1", "data2", "data3"]):
        return "spark"
    if any(token in lowered for token in ["web", "login", "大屏", "页面", "登录"]):
        return "web"
    return "all"


def _render_final_conclusion(
    *,
    incident_id: str,
    status: str,
    resolution: str,
    headline: str,
    summaries: list[ExecutionSummary],
) -> str:
    action_success = "yes" if summaries and all(item.action_success for item in summaries) else "no"
    findings: list[str] = []
    for item in summaries:
        findings.extend(item.findings[:2])
    unique_findings: list[str] = []
    seen: set[str] = set()
    for item in findings:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_findings.append(normalized)
    if resolution == "yes":
        next_step = "当前看起来已经恢复正常，建议继续观察监控、日志和业务侧现象是否稳定。"
    elif resolution == "no":
        next_step = "当前问题还没有解决，建议继续深挖日志、依赖链路和最近变更，必要时执行更明确的修复动作。"
    else:
        next_step = "当前还不能完全确认是否恢复，建议补做验证检查或继续观察一段时间。"
    lines = [
        "assistant_result:",
        f"- incident_id: {incident_id}",
        f"- execution_status: {status}",
        f"- action_success: {action_success}",
        f"- issue_resolved: {resolution}",
        f"- headline: {headline}",
        f"- next_step: {next_step}",
    ]
    if unique_findings:
        lines.append("- key_findings:")
        lines.extend(f"  - {item}" for item in unique_findings[:5])
    return "\n".join(lines)


class AgentRuntime:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.ssh_client = SSHClient(connect_timeout=inventory.defaults.connect_timeout)
        self.monitor = CloudMonitorService(inventory.cloud_monitor)
        self.alerts = AlertService(inventory.alerting)
        self.feishu = FeishuDocClient(inventory.feishu)
        self.execution_guard = ExecutionGuard()
        self.store = OpsStore()
        self.custom_skills = CustomSkillStore(self.store.root_dir)
        self.planner = LLMPlanner(
            inventory.llm,
            inventory,
            knowledge_provider=self.search_knowledge,
            action_provider=lambda: [item.name for item in self.custom_skills.list_skills()],
        )
        self.chat_agent = OpsChatAgent(
            inventory.llm,
            inventory,
            knowledge_provider=self.search_knowledge,
            action_provider=lambda: [item.name for item in self.custom_skills.list_skills()],
        )
        self.chat_graph = OpsChatGraph(
            self.chat_agent,
            plan_renderer=self.render_plan_steps,
            execution_renderer=self.execute_steps_dialogue,
        )

    def playbook_names(self) -> list[str]:
        return all_action_names(sorted(PLAYBOOKS.keys()), extra_actions=[item.name for item in self.custom_skills.list_skills()])

    def list_skills(self) -> list[dict[str, str | bool]]:
        items = [
            {
                "name": item.name,
                "mutating": item.mutating,
                "description": item.description,
                "source": "builtin",
            }
            for item in ADVANCED_SKILLS.values()
        ]
        items.extend(
            {
                "name": item.name,
                "mutating": item.mutating,
                "description": item.description,
                "source": "custom",
                "params_hint": item.params_hint,
                "prompt_hint": item.prompt_hint,
                "params_schema": [
                    {
                        "name": param.name,
                        "label": param.label,
                        "required": param.required,
                        "default": param.default,
                        "description": param.description,
                    }
                    for param in item.params_schema
                ],
            }
            for item in self.custom_skills.list_skills()
        )
        return items

    def create_custom_skill(
        self,
        *,
        name: str,
        description: str,
        mutating: bool,
        command_template: str,
        validation_template: str = "",
        params_hint: str = "",
        prompt_hint: str = "",
        params_schema: str = "",
    ) -> CustomSkillSpec:
        if name in ADVANCED_SKILLS or name in PLAYBOOKS:
            raise ValueError(f"skill name already exists: {name}")
        spec = CustomSkillSpec(
            name=name,
            description=description,
            mutating=mutating,
            command_template=command_template,
            validation_template=validation_template,
            params_hint=params_hint,
            prompt_hint=prompt_hint,
            params_schema=parse_params_schema(params_schema),
        )
        self.custom_skills.save_skill(spec)
        return spec

    def preview_skill_execution(
        self,
        *,
        target: str,
        action: str,
        service: str | None,
        params: dict[str, str] | None = None,
    ) -> str:
        params = params or {}
        custom_skill = self.custom_skills.get_skill(action)
        if custom_skill is None:
            raise KeyError(f"preview is only supported for custom skills: {action}")
        preview = preview_custom_skill_command(custom_skill, target=target, service=service, params=params)
        lines = [
            f"skill: {action}",
            f"target: {target}",
            f"service: {service or '-'}",
            f"mutating: {'yes' if custom_skill.mutating else 'no'}",
            "",
            "command_preview:",
            preview.command,
        ]
        if preview.missing_params:
            lines.extend(["", "missing_required_params:", ", ".join(preview.missing_params)])
        return "\n".join(lines)

    def list_incidents(self, limit: int = 30) -> list[IncidentRecord]:
        return self.store.list_incidents(limit=limit)

    def get_incident(self, incident_id: str) -> IncidentRecord:
        return self.store.load_incident(incident_id)

    def list_knowledge(self, limit: int = 30):
        return self.store.list_knowledge(limit=limit)

    def search_knowledge(self, query: str, limit: int = 5):
        store_hits = list(self.store.search_knowledge(query, limit=limit))
        feishu_hits = list(self.feishu.search_imported_documents(query, limit=limit))
        seen = {(item.title, getattr(item, "final_headline", "")) for item in store_hits}
        merged = store_hits[:]
        for item in feishu_hits:
            marker = (item.title, item.final_headline)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
            if len(merged) >= limit:
                break
        return merged[:limit]

    def search_incidents(self, query: str, limit: int = 5):
        return self.store.search_incidents(query, limit=limit)

    def import_feishu_document(self, url_or_token: str) -> dict[str, str]:
        document = self.feishu.import_document(url_or_token)
        return {
            "title": document.title,
            "token": document.token,
            "doc_type": document.doc_type,
            "source_url": document.source_url,
            "saved_path": document.saved_path or "",
            "imported_at": document.imported_at,
        }

    def list_feishu_documents(self, limit: int = 30) -> list[dict[str, str]]:
        return [
            {
                "title": item.title,
                "token": item.token,
                "doc_type": item.doc_type,
                "source_url": item.source_url,
                "saved_path": item.saved_path or "",
                "imported_at": item.imported_at,
            }
            for item in self.feishu.list_imported_documents(limit=limit)
        ]

    def sync_feishu_document(
        self,
        *,
        title: str,
        content: str,
        url_or_token: str | None = None,
        append: bool = False,
        use_user_token: bool = False,
    ) -> dict[str, str]:
        result = self.feishu.sync_document(
            title=title,
            content=content,
            url_or_token=url_or_token,
            append=append,
            use_user_token=use_user_token,
        )
        return {
            "title": result.title,
            "token": result.token,
            "doc_type": result.doc_type,
            "source_url": result.source_url,
            "mode": result.mode,
            "updated_at": result.updated_at,
            "saved_path": result.saved_path or "",
        }

    def diagnose_llm(self) -> LLMDiagnosis:
        return diagnose_llm(self.inventory.llm)

    def ollama_config_snippet(self, model: str = "qwen3:8b") -> str:
        return build_ollama_config_snippet(model=model)

    def _remote_command(self, action: str, service: str | None) -> str:
        playbook = PLAYBOOKS[action]
        if action in {"service-status", "service-restart"}:
            if not service:
                raise ValueError(f"{action} requires --service")
            return playbook.builder(service)
        return playbook.builder()

    def _create_incident(
        self,
        *,
        source: str,
        prompt: str,
        target: str | None,
        title: str,
        status: str = "planned",
        operator: str | None = None,
        approval_ticket: str | None = None,
        tags: list[str] | None = None,
    ) -> IncidentRecord:
        return self.store.create_incident(
            source=source,
            prompt=prompt,
            target=target,
            title=title,
            status=status,
            operator=operator,
            approval_ticket=approval_ticket,
            tags=tags,
        )

    def _final_status_from_summaries(self, summaries: list[ExecutionSummary]) -> tuple[str, str, str]:
        if not summaries:
            return ("planned", "unknown", "no execution yet")
        if any(not item.action_success or item.issue_resolved == "no" for item in summaries):
            return ("unresolved", "no", summaries[-1].headline)
        if all(item.issue_resolved == "yes" for item in summaries):
            return ("resolved", "yes", summaries[-1].headline)
        return ("investigating", "unknown", summaries[-1].headline)

    def _record_action(
        self,
        *,
        incident_id: str,
        target: str,
        action: str,
        service: str | None,
        reason: str | None,
        result: CommandResult,
        summary: ExecutionSummary,
        report_path: Path | None,
        risk_decision: RiskDecision | None = None,
        approval_ticket: str | None = None,
        operator: str | None = None,
        kind: str = "command",
    ) -> None:
        record = ActionRecord(
            action_id=f"ACT-{uuid4().hex[:8]}",
            timestamp=datetime.now().isoformat(timespec="seconds"),
            kind=kind,
            target=target,
            action=action,
            service=service,
            reason=reason,
            command=result.command,
            returncode=result.returncode,
            action_success=summary.action_success,
            issue_resolved=summary.issue_resolved,
            headline=summary.headline,
            risk_level=risk_decision.risk_level if risk_decision else "low",
            approval_ticket=approval_ticket,
            operator=operator,
            findings=list(summary.findings),
            report_path=str(report_path) if report_path else None,
        )
        self.store.append_action(incident_id, record)

    def _risk_check(
        self,
        *,
        server_name: str,
        action: str,
        mutating: bool,
        confirm_change: bool,
        approval_ticket: str | None,
    ) -> RiskDecision:
        server = self.inventory.get_server(server_name)
        return enforce_risk(
            self.inventory.risk,
            server=server,
            action=action,
            mutating=mutating,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
        )

    def _render_knowledge_hits(self, query: str, limit: int = 5) -> str:
        hits = self.search_knowledge(query, limit=limit)
        incidents = self.search_incidents(query, limit=limit)
        lines = [f"knowledge query: {query}", ""]
        if hits:
            lines.append("similar knowledge:")
            for item in hits:
                lines.append(f"- {item.title} / {item.final_headline} / resolution={item.resolution}")
        if incidents:
            lines.append("")
            lines.append("similar incidents:")
            for item in incidents:
                lines.append(f"- {item.incident_id} / {item.title} / status={item.status} / resolution={item.resolution}")
        if len(lines) == 2:
            lines.append("no similar knowledge found")
        return "\n".join(lines)

    def _execute_local_action(
        self,
        *,
        incident_id: str,
        target: str,
        action: str,
        service: str | None,
        reason: str | None,
        content: str,
        risk_decision: RiskDecision | None = None,
        approval_ticket: str | None = None,
        operator: str | None = None,
        report: bool,
    ) -> tuple[CommandResult, ExecutionSummary, str, Path | None]:
        result = CommandResult(command=f"local:{action}", stdout=content, stderr="", returncode=0)
        summary = summarize_execution(action, result)
        report_path = None
        if report:
            server = self.inventory.get_server(target) if target else self.inventory.servers[0]
            report_path = write_report(
                self.inventory.defaults.report_dir,
                server,
                action,
                result,
                incident_id=incident_id,
                prompt=content.splitlines()[0] if content else action,
                reason=reason,
                service=service,
            )
        rendered = self.render_execution(target or "local", action, result)
        if report_path:
            rendered += f"\n\nreport: {report_path}"
        self._record_action(
            incident_id=incident_id,
            target=target or "local",
            action=action,
            service=service,
            reason=reason,
            result=result,
            summary=summary,
            report_path=report_path,
            risk_decision=risk_decision,
            approval_ticket=approval_ticket,
            operator=operator,
            kind="local-skill",
        )
        return result, summary, rendered, report_path

    def _execute_action(
        self,
        *,
        server_name: str,
        action: str,
        service: str | None,
        params: dict[str, str],
        confirm_change: bool,
        approval_ticket: str | None,
        operator: str | None,
        report: bool,
        incident_id: str,
        prompt: str,
        reason: str | None,
    ) -> tuple[CommandResult, ExecutionSummary, str, Path | None]:
        if action in PLAYBOOKS:
            playbook = PLAYBOOKS[action]
            risk_decision = self._risk_check(
                server_name=server_name,
                action=action,
                mutating=playbook.mutating,
                confirm_change=confirm_change,
                approval_ticket=approval_ticket,
            )
            if playbook.mutating:
                with self.execution_guard.acquire(f"mutating:{server_name}"):
                    return self._execute_remote_action(
                        server_name=server_name,
                        action=action,
                        service=service,
                        confirm_change=confirm_change,
                        report=report,
                        incident_id=incident_id,
                        prompt=prompt,
                        reason=reason,
                        risk_decision=risk_decision,
                        approval_ticket=approval_ticket,
                        operator=operator,
                    )
            return self._execute_remote_action(
                server_name=server_name,
                action=action,
                service=service,
                confirm_change=confirm_change,
                report=report,
                incident_id=incident_id,
                prompt=prompt,
                reason=reason,
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
            )

        custom_skill = self.custom_skills.get_skill(action)
        if action not in ADVANCED_SKILLS and custom_skill is None:
            raise KeyError(f"unknown action: {action}")
        spec = ADVANCED_SKILLS.get(action)
        effective_mutating = spec.mutating if spec else bool(custom_skill and custom_skill.mutating)
        risk_decision = self._risk_check(
            server_name=server_name,
            action=action,
            mutating=effective_mutating,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
        )

        if action == "knowledge-search":
            return self._execute_local_action(
                incident_id=incident_id,
                target=server_name,
                action=action,
                service=service,
                reason=reason,
                content=self._render_knowledge_hits(prompt or service or server_name),
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
                report=report,
            )

        if action == "data-repair-plan":
            content = render_data_repair_plan(
                self.inventory.llm,
                prompt=prompt,
                target=server_name,
                knowledge_hits=self.search_knowledge(prompt, limit=5),
            )
            return self._execute_local_action(
                incident_id=incident_id,
                target=server_name,
                action=action,
                service=service,
                reason=reason,
                content=content,
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
                report=report,
            )

        if action == "server-recovery-plan":
            scenario = params.get("scenario", "").strip() or _scenario_from_prompt(prompt)
            content = render_server_recovery_plan(scenario=scenario)
            if prompt:
                content += f"\n\nrequested_prompt: {prompt}"
            return self._execute_local_action(
                incident_id=incident_id,
                target=server_name,
                action=action,
                service=service,
                reason=reason,
                content=content,
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
                report=report,
            )

        if custom_skill is not None:
            remote_command = build_custom_skill_command(custom_skill, target=server_name, service=service, params=params)
            return self._execute_remote_action(
                server_name=server_name,
                action=action,
                service=service,
                confirm_change=confirm_change,
                report=report,
                incident_id=incident_id,
                prompt=prompt,
                reason=reason,
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
                kind="custom-skill",
                remote_command_override=remote_command,
            )

        if action == "log-analysis":
            remote_command = build_log_analysis_command(service, params)
        elif action == "docker-log-analysis":
            remote_command = build_docker_log_command(service, params)
        elif action == "app-log-search":
            remote_command = build_app_log_search_command(params)
        elif action == "clickhouse-table-check":
            remote_command = build_clickhouse_table_check_command(params)
        elif action == "frontend-rollback":
            remote_command = build_frontend_rollback_command(params)
        elif action == "frontend-rollback-preview":
            remote_command = build_frontend_rollback_preview_command(params)
        elif action == "data-repair-run":
            remote_command = build_data_repair_run_command(params)
        elif action == "mysql-repair":
            remote_command = build_mysql_repair_command(params)
        elif action == "spark-backfill":
            remote_command = build_spark_backfill_command(params)
        elif action == "xxl-job-rerun":
            remote_command = build_xxl_job_rerun_command(params)
        else:
            raise KeyError(f"unsupported advanced action: {action}")

        if effective_mutating:
            with self.execution_guard.acquire(f"mutating:{server_name}"):
                return self._execute_remote_action(
                    server_name=server_name,
                    action=action,
                    service=service,
                    confirm_change=confirm_change,
                    report=report,
                    incident_id=incident_id,
                    prompt=prompt,
                    reason=reason,
                    risk_decision=risk_decision,
                    approval_ticket=approval_ticket,
                    operator=operator,
                    kind="skill",
                    remote_command_override=remote_command,
                )
        return self._execute_remote_action(
            server_name=server_name,
            action=action,
            service=service,
            confirm_change=confirm_change,
            report=report,
            incident_id=incident_id,
            prompt=prompt,
            reason=reason,
            risk_decision=risk_decision,
            approval_ticket=approval_ticket,
            operator=operator,
            kind="skill",
            remote_command_override=remote_command,
        )

    def _execute_remote_action(
        self,
        *,
        server_name: str,
        action: str,
        service: str | None,
        confirm_change: bool,
        report: bool,
        incident_id: str,
        prompt: str,
        reason: str | None,
        risk_decision: RiskDecision | None = None,
        approval_ticket: str | None = None,
        operator: str | None = None,
        kind: str = "command",
        remote_command_override: str | None = None,
    ) -> tuple[CommandResult, ExecutionSummary, str, Path | None]:
        server = self.inventory.get_server(server_name)
        remote_command = remote_command_override or self._remote_command(action, service)
        result = self.ssh_client.run(server, remote_command)
        summary = summarize_execution(action, result)
        report_path = None
        rendered = self.render_execution(server.name, action, result)
        if report:
            report_path = write_report(
                self.inventory.defaults.report_dir,
                server,
                action,
                result,
                incident_id=incident_id,
                prompt=prompt,
                reason=reason,
                service=service,
            )
            rendered += f"\n\nreport: {report_path}"
        self._record_action(
            incident_id=incident_id,
            target=server.name,
            action=action,
            service=service,
            reason=reason,
            result=result,
            summary=summary,
            report_path=report_path,
            risk_decision=risk_decision,
            approval_ticket=approval_ticket,
            operator=operator,
            kind=kind,
        )
        return result, summary, rendered, report_path

    def execute_single(
        self,
        *,
        target: str,
        action: str,
        service: str | None,
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
        params: dict[str, str] | None = None,
        prompt: str | None = None,
    ) -> str:
        incident = self._create_incident(
            source="manual-run",
            prompt=prompt or f"run {action}",
            target=target,
            title=f"{target} {action}",
            status="executing",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=[action, "manual"],
        )
        _, summary, rendered, _ = self._execute_action(
            server_name=target,
            action=action,
            service=service,
            params=params or {},
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
            operator=operator,
            report=report,
            incident_id=incident.incident_id,
            prompt=prompt or f"run {action}",
            reason="manual action run",
        )
        status, resolution, headline = self._final_status_from_summaries([summary])
        self.store.finalize_incident(
            incident.incident_id,
            status=status,
            resolution=resolution,
            final_headline=headline,
            capture_knowledge=(status in {"resolved", "unresolved"}),
        )
        final_conclusion = _render_final_conclusion(
            incident_id=incident.incident_id,
            status=status,
            resolution=resolution,
            headline=headline,
            summaries=[summary],
        )
        return final_conclusion + "\n\n" + rendered + f"\n\nincident: {incident.incident_id}"

    def render_execution(self, server_name: str, action: str, result: CommandResult) -> str:
        summary = summarize_execution(action, result).render_text()
        return (
            f"==> target={server_name} action={action} returncode={result.returncode}\n\n"
            f"{summary}\n\n"
            f"[stdout]\n{result.stdout or '(empty)'}\n\n"
            f"[stderr]\n{result.stderr or '(empty)'}"
        )

    def explain_result(self, *, prompt: str, target: str, action: str, result: CommandResult) -> str:
        summary = summarize_execution(action, result)
        return explain_execution(
            self.inventory.llm,
            prompt=prompt,
            server_name=target,
            action=action,
            summary=summary,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )

    def render_plan_steps(self, steps: list[PlannedStep]) -> str:
        lines = []
        for index, step in enumerate(steps, start=1):
            line = (
                f"{index}. 动作：{step.action}\n"
                f"   目标：{step.target}\n"
                f"   服务：{step.service or '无'}\n"
                f"   原因：{step.reason or '根据用户请求生成的受控运维步骤'}"
            )
            if step.params:
                params_text = ", ".join(f"{key}={value}" for key, value in sorted(step.params.items()))
                line += f"\n   参数：{params_text}"
            lines.append(line)
        return "\n".join(lines)

    def plan(self, prompt: str, target: str | None = None) -> list[PlannedStep]:
        return self.planner.plan(prompt, explicit_target=target)

    def plan_with_record(self, prompt: str, target: str | None = None, source: str = "plan") -> tuple[str, str]:
        steps = self.plan(prompt, target=target)
        incident = self._create_incident(
            source=source,
            prompt=prompt,
            target=target,
            title=prompt[:80] or "ops plan",
            status="planned",
            tags=["plan"],
        )
        return self.render_plan_steps(steps), incident.incident_id

    def execute_plan(
        self,
        prompt: str,
        target: str | None,
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
    ) -> str:
        incident = self._create_incident(
            source="ask",
            prompt=prompt,
            target=target,
            title=prompt[:80] or "ops execution",
            status="executing",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=["ask"],
        )
        steps = self.plan(prompt, target=target)
        return self._execute_steps_for_incident(
            incident=incident,
            prompt=prompt,
            steps=steps,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
            operator=operator,
            report=report,
            include_explanation=False,
        )

    def execute_plan_dialogue(
        self,
        prompt: str,
        target: str | None,
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
    ) -> str:
        incident = self._create_incident(
            source="chat-execute",
            prompt=prompt,
            target=target,
            title=prompt[:80] or "chat incident",
            status="executing",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=["chat", "execute"],
        )
        steps = self.plan(prompt, target=target)
        return self._execute_steps_for_incident(
            incident=incident,
            prompt=prompt,
            steps=steps,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
            operator=operator,
            report=report,
            include_explanation=True,
        )

    def _execute_steps_for_incident(
        self,
        *,
        incident: IncidentRecord,
        prompt: str,
        steps: list[PlannedStep],
        confirm_change: bool,
        approval_ticket: str | None,
        operator: str | None,
        report: bool,
        include_explanation: bool,
    ) -> str:
        outputs: list[str] = []
        summaries: list[ExecutionSummary] = []
        for index, step in enumerate(steps, start=1):
            try:
                result, summary, execution_text, _ = self._execute_action(
                    server_name=step.target,
                    action=step.action,
                    service=step.service,
                    params=step.params,
                    confirm_change=confirm_change,
                    approval_ticket=approval_ticket,
                    operator=operator,
                    report=report,
                    incident_id=incident.incident_id,
                    prompt=prompt,
                    reason=step.reason,
                )
            except ValueError as exc:
                if "缺少" not in str(exc) and "requires" not in str(exc) and "params." not in str(exc):
                    raise
                outputs.append(self._render_missing_params_message(step, str(exc)))
                self.store.finalize_incident(
                    incident.incident_id,
                    status="planned",
                    resolution="unknown",
                    final_headline="等待补充执行参数",
                    capture_knowledge=False,
                )
                outputs.append(f"incident: {incident.incident_id}")
                return "\n\n".join(outputs)
            summaries.append(summary)
            header = (
                f"步骤 {index}：动作={step.action} 目标={step.target} "
                f"服务={step.service or '无'} 原因={step.reason or '根据用户请求生成的受控运维步骤'}"
            )
            if step.params:
                header += " params=" + ", ".join(f"{k}={v}" for k, v in sorted(step.params.items()))
            if include_explanation:
                explanation = self.explain_result(
                    prompt=prompt,
                    target=step.target,
                    action=step.action,
                    result=result,
                )
                outputs.append("\n".join([header, "", "assistant_interpretation:", explanation, "", execution_text]))
            else:
                outputs.append("\n".join([header, "", execution_text]))
        status, resolution, headline = self._final_status_from_summaries(summaries)
        self.store.finalize_incident(
            incident.incident_id,
            status=status,
            resolution=resolution,
            final_headline=headline,
            capture_knowledge=(status in {"resolved", "unresolved"}),
        )
        outputs.append(
            _render_final_conclusion(
                incident_id=incident.incident_id,
                status=status,
                resolution=resolution,
                headline=headline,
                summaries=summaries,
            )
        )
        outputs.append(f"incident: {incident.incident_id}")
        return "\n\n".join(outputs)

    @staticmethod
    def _render_missing_params_message(step: PlannedStep, error: str) -> str:
        requirements = {
            "clickhouse-table-check": [
                "database：ClickHouse 数据库名，可选，默认 `atm_chengdu`",
                "table：要核验的表名",
                "time_column：用于按天统计的时间字段",
                "days：核验近几天，可选，默认 5",
            ],
            "mysql-repair": [
                "database：要修复的数据库名",
                "sql：准备执行的修复 SQL",
                "validation_sql：执行后的校验 SQL，可选但建议填写",
            ],
            "data-repair-run": [
                "command：准备执行的数据修复命令",
                "validation_command：执行后的校验命令，可选但建议填写",
            ],
            "spark-backfill": [
                "submit_command：Spark 补数或重算命令",
                "validation_command：执行后的校验命令，可选但建议填写",
            ],
            "xxl-job-rerun": [
                "trigger_command：触发 XXL-Job 的命令",
                "validation_command：执行后的校验命令，可选但建议填写",
            ],
            "frontend-rollback-preview": [
                "release_name：指定要预检的前端版本目录名，可选",
                "base_dir：前端发布根目录，可选，默认 `/data/atm-ui-pre`",
            ],
        }
        fields = requirements.get(step.action, ["请补充该技能要求的必填参数"])
        lines = [
            "我已经识别到这是可执行动作，但当前信息还不够，所以先停在预检阶段，不继续执行。",
            f"动作：{step.action}",
            f"目标：{step.target}",
            f"原因：{AgentRuntime._friendly_param_error(step.action, error)}",
            "",
            "请补充以下参数：",
        ]
        lines.extend(f"- {item}" for item in fields)
        lines.extend(
            [
                "",
                "补充后我会先生成执行预览；涉及写入、修复、重启、回滚或发布时，仍需要审批单号和变更确认。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _friendly_param_error(action: str, error: str) -> str:
        if action == "clickhouse-table-check":
            return "ClickHouse 核验需要明确表名和时间字段"
        if action == "mysql-repair":
            return "数据库修复需要明确数据库名和修复 SQL"
        if action == "data-repair-run":
            return "数据修复执行需要明确修复命令"
        if action == "spark-backfill":
            return "Spark 补数需要明确提交命令"
        if action == "xxl-job-rerun":
            return "XXL-Job 重跑需要明确触发命令"
        if action == "frontend-rollback-preview":
            return "前端回滚预检至少需要可识别的发布目录，必要时请明确 release_name"
        return error

    def execute_steps_dialogue(
        self,
        *,
        prompt: str,
        steps: list[PlannedStep],
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
    ) -> str:
        incident = self._create_incident(
            source="chat-graph-execute",
            prompt=prompt,
            target=steps[0].target if steps else None,
            title=prompt[:80] or "graph incident",
            status="executing",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=["chat", "graph"],
        )
        return self._execute_steps_for_incident(
            incident=incident,
            prompt=prompt,
            steps=steps,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
            operator=operator,
            report=report,
            include_explanation=True,
        )

    def chat(
        self,
        *,
        history: list[ChatMessage],
        prompt: str,
        target: str | None,
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
    ) -> str:
        return self.chat_graph.run(
            history=history,
            prompt=prompt,
            target=target,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
            operator=operator,
            report=report,
        )

    def get_metrics(self, target: str) -> MetricSnapshot:
        server = self.inventory.get_server(target)
        return self.monitor.latest_snapshot(server)

    def safe_get_metrics(self, target: str) -> MetricSnapshot | None:
        try:
            return self.get_metrics(target)
        except Exception:
            return None

    def check_alerts(self, target: str) -> list[str]:
        server = self.inventory.get_server(target)
        snapshot = self.monitor.latest_snapshot(server)
        events = self.alerts.evaluate(server, snapshot)
        self.alerts.notify(events)
        return [event.render_text() for event in events]

    def deploy_package(
        self,
        *,
        preset_key: str,
        package_path: str,
        confirm_change: bool,
        report: bool,
        approval_ticket: str | None = None,
        operator: str | None = None,
    ) -> str:
        package = Path(package_path).expanduser()
        if not package.exists() or not package.is_file():
            raise FileNotFoundError(f"package file not found: {package}")

        preview = render_package_deploy_preview(preset_key, str(package.resolve()))
        incident = self._create_incident(
            source="deploy-package",
            prompt=f"deploy package {preset_key}",
            target=None,
            title=f"deploy {preset_key}",
            status="planned" if not confirm_change else "executing",
            operator=operator,
            approval_ticket=approval_ticket,
            tags=["deploy", preset_key],
        )
        if not confirm_change:
            return preview + f"\n\nincident: {incident.incident_id}\nconfirmation: pending -- rerun with confirmation to upload and activate"

        preset = get_deploy_preset(preset_key)
        server = self.inventory.get_server(preset.target)
        risk_decision = self._risk_check(
            server_name=server.name,
            action=f"deploy:{preset.key}",
            mutating=True,
            confirm_change=confirm_change,
            approval_ticket=approval_ticket,
        )
        with self.execution_guard.acquire(f"mutating:{server.name}"):
            upload = self.ssh_client.upload_file(server, str(package.resolve()), preset.remote_upload_path)
            if upload.returncode != 0:
                raise RuntimeError(self.render_execution(server.name, f"upload:{preset.key}", upload))

            _, summary, rendered, _ = self._execute_remote_action(
                server_name=server.name,
                action=f"deploy:{preset.key}",
                service=None,
                confirm_change=True,
                report=report,
                incident_id=incident.incident_id,
                prompt=f"deploy package {preset_key}",
                reason=f"activate preset {preset.key}",
                risk_decision=risk_decision,
                approval_ticket=approval_ticket,
                operator=operator,
                kind="deploy",
                remote_command_override=preset.activate_command,
            )
        status, resolution, headline = self._final_status_from_summaries([summary])
        self.store.finalize_incident(
            incident.incident_id,
            status=status,
            resolution=resolution,
            final_headline=headline,
            capture_knowledge=(status in {"resolved", "unresolved"}),
        )
        final_conclusion = _render_final_conclusion(
            incident_id=incident.incident_id,
            status=status,
            resolution=resolution,
            headline=headline,
            summaries=[summary],
        )
        return preview + "\n\n" + final_conclusion + "\n\n" + rendered + f"\n\nincident: {incident.incident_id}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ATM ops agent standalone")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory_parser = subparsers.add_parser("inventory", help="inventory operations")
    inventory_subparsers = inventory_parser.add_subparsers(dest="inventory_command", required=True)
    inventory_list = inventory_subparsers.add_parser("list", help="list servers")
    inventory_list.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    run_parser = subparsers.add_parser("run", help="run one action")
    run_parser.add_argument("action", choices=all_action_names(sorted(PLAYBOOKS.keys())))
    run_parser.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    run_parser.add_argument("--target", required=True, help="server name or host")
    run_parser.add_argument("--service", help="service name or compatible resource name")
    run_parser.add_argument("--confirm-change", action="store_true", help="allow mutating actions")
    run_parser.add_argument("--approval-ticket", help="approval ticket or change request id for gated operations")
    run_parser.add_argument("--operator", help="operator name for audit trail")
    run_parser.add_argument("--report", action="store_true", help="write markdown report")
    run_parser.add_argument("--params-json", help='extra params as JSON, e.g. {"lines":"200","keyword":"error"}')
    run_parser.add_argument("--prompt", help="original request text")

    ask_parser = subparsers.add_parser("ask", help="plan and execute from natural language")
    ask_parser.add_argument("prompt", help="for example: check prod-web-01 nginx status")
    ask_parser.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    ask_parser.add_argument("--target", help="explicit target if prompt is ambiguous")
    ask_parser.add_argument("--confirm-change", action="store_true", help="allow mutating actions")
    ask_parser.add_argument("--approval-ticket", help="approval ticket or change request id for gated operations")
    ask_parser.add_argument("--operator", help="operator name for audit trail")
    ask_parser.add_argument("--report", action="store_true", help="write markdown report")

    plan_parser = subparsers.add_parser("plan", help="only show the planned steps")
    plan_parser.add_argument("prompt", help="natural language ops request")
    plan_parser.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    plan_parser.add_argument("--target", help="explicit target if prompt is ambiguous")

    metrics_parser = subparsers.add_parser("metrics", help="query cloud metrics")
    metrics_subparsers = metrics_parser.add_subparsers(dest="metrics_command", required=True)
    metrics_latest = metrics_subparsers.add_parser("latest", help="latest cloud metrics")
    metrics_latest.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    metrics_latest.add_argument("--target", required=True, help="server name or host")

    alerts_parser = subparsers.add_parser("alerts", help="evaluate alert rules")
    alerts_subparsers = alerts_parser.add_subparsers(dest="alerts_command", required=True)
    alerts_check = alerts_subparsers.add_parser("check", help="check one server")
    alerts_check.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    alerts_check.add_argument("--target", required=True, help="server name or host")

    api_parser = subparsers.add_parser("api", help="start HTTP API for Coze plugin tools")
    api_subparsers = api_parser.add_subparsers(dest="api_command", required=True)
    api_serve = api_subparsers.add_parser("serve", help="serve plugin API")
    api_serve.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    api_serve.add_argument("--host", default="127.0.0.1", help="bind host")
    api_serve.add_argument("--port", type=int, default=8790, help="bind port")

    feishu_parser = subparsers.add_parser("feishu", help="import Feishu/Lark documents into local knowledge")
    feishu_subparsers = feishu_parser.add_subparsers(dest="feishu_command", required=True)
    feishu_import = feishu_subparsers.add_parser("import-doc", help="import one Feishu docx/docs document")
    feishu_import.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    feishu_import.add_argument("--url", required=True, help="feishu doc url or token")
    feishu_user_auth = feishu_subparsers.add_parser("user-auth", help="exchange one Feishu user authorization code for user tokens")
    feishu_user_auth.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    feishu_user_auth.add_argument("--code", required=True, help="authorization code from Feishu user login flow")
    feishu_refresh = feishu_subparsers.add_parser("refresh-user-token", help="refresh local Feishu user token")
    feishu_refresh.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    feishu_sync = feishu_subparsers.add_parser("sync-doc", help="create or update one Feishu docx document from local content")
    feishu_sync.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    feishu_sync.add_argument("--title", required=True, help="document title")
    feishu_sync.add_argument("--url", help="existing feishu docx url or token; omit to create a new doc")
    feishu_sync.add_argument("--content-file", required=True, help="utf-8 markdown/text file to sync")
    feishu_sync.add_argument("--append", action="store_true", help="append content instead of overwriting")
    feishu_sync.add_argument("--use-user-token", action="store_true", help="use user_access_token instead of tenant_access_token")

    llm_parser = subparsers.add_parser("llm", help="diagnose or inspect LLM connectivity")
    llm_subparsers = llm_parser.add_subparsers(dest="llm_command", required=True)
    llm_diagnose = llm_subparsers.add_parser("diagnose", help="run one real connectivity check against the configured model")
    llm_diagnose.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    atm_parser = subparsers.add_parser("atm", help="ATM-specific automation helpers")
    atm_subparsers = atm_parser.add_subparsers(dest="atm_command", required=True)

    atm_env = atm_subparsers.add_parser("show-env", help="show ATM structured environment")
    atm_env.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    atm_deploy = atm_subparsers.add_parser("plan-deploy", help="show ATM deploy workflow")
    atm_deploy.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    atm_recover = atm_subparsers.add_parser("plan-recover", help="show ATM recovery workflow")
    atm_recover.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    atm_check = atm_subparsers.add_parser("check", help="run read-only ATM health inspection")
    atm_check.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    atm_check.add_argument("--target", choices=["all", "atm-app-server", "atm-bigdata-server"], default="all")
    atm_check.add_argument("--report", action="store_true", help="write markdown reports")

    atm_runbook = atm_subparsers.add_parser("runbook", help="print ATM runbook")
    atm_runbook.add_argument("--config", default="inventory.atm.toml", help="inventory file")

    atm_export = atm_subparsers.add_parser("export", help="export ATM templates and knowledge files")
    atm_export.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    atm_export.add_argument("--output-dir", required=True, help="destination directory")

    atm_deploy_pkg = atm_subparsers.add_parser("deploy-package", help="upload and activate an ATM deployment package")
    atm_deploy_pkg.add_argument("--config", default="inventory.atm.toml", help="inventory file")
    atm_deploy_pkg.add_argument("--preset", choices=[preset.key for preset in get_deploy_presets()], required=True)
    atm_deploy_pkg.add_argument("--package-path", required=True, help="local package path")
    atm_deploy_pkg.add_argument("--confirm-change", action="store_true", help="allow upload and remote activation")
    atm_deploy_pkg.add_argument("--approval-ticket", help="approval ticket or change request id for gated operations")
    atm_deploy_pkg.add_argument("--operator", help="operator name for audit trail")
    atm_deploy_pkg.add_argument("--report", action="store_true", help="write markdown report")
    return parser


def list_inventory(inventory: Inventory) -> int:
    for server in inventory.servers:
        tags = ",".join(server.tags) if server.tags else "-"
        print(
            f"{server.name}\t{server.host}\tuser={server.user}\tregion={server.region or '-'}\t"
            f"instance_id={server.instance_id or '-'}\ttags={tags}"
        )
    return 0


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_params_json(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("params-json must be a JSON object")
    return {str(key): str(value) for key, value in parsed.items()}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        inventory = load_inventory(args.config)
        runtime = AgentRuntime(inventory)

        if args.command == "inventory":
            return list_inventory(inventory)

        if args.command == "run":
            print(
                runtime.execute_single(
                    target=args.target,
                    action=args.action,
                    service=args.service,
                    confirm_change=args.confirm_change,
                    approval_ticket=args.approval_ticket,
                    operator=args.operator,
                    report=args.report,
                    params=_parse_params_json(args.params_json),
                    prompt=args.prompt,
                )
            )
            return 0

        if args.command == "ask":
            print(
                runtime.execute_plan(
                    prompt=args.prompt,
                    target=args.target,
                    confirm_change=args.confirm_change,
                    approval_ticket=args.approval_ticket,
                    operator=args.operator,
                    report=args.report,
                )
            )
            return 0

        if args.command == "plan":
            steps = runtime.plan(args.prompt, target=args.target)
            print_json([asdict(step) for step in steps])
            return 0

        if args.command == "metrics":
            snapshot = runtime.get_metrics(args.target)
            print_json(
                {
                    "server": snapshot.server,
                    "region": snapshot.region,
                    "points": [
                        {
                            "metric": point.metric,
                            "value": point.value,
                            "unit": point.unit,
                            "timestamp": point.timestamp.isoformat(),
                        }
                        for point in snapshot.points
                    ],
                }
            )
            return 0

        if args.command == "alerts":
            alerts = runtime.check_alerts(args.target)
            print_json(alerts)
            return 0

        if args.command == "api":
            from atm_ops_agent.api_server import serve_api

            serve_api(runtime, host=args.host, port=args.port)
            return 0

        if args.command == "feishu":
            if args.feishu_command == "import-doc":
                print_json(runtime.import_feishu_document(args.url))
                return 0
            if args.feishu_command == "user-auth":
                print_json(runtime.feishu.save_user_access_token(args.code))
                return 0
            if args.feishu_command == "refresh-user-token":
                print_json(runtime.feishu.refresh_user_access_token())
                return 0
            if args.feishu_command == "sync-doc":
                content = Path(args.content_file).read_text(encoding="utf-8")
                print_json(
                    runtime.sync_feishu_document(
                        title=args.title,
                        content=content,
                        url_or_token=args.url,
                        append=args.append,
                        use_user_token=args.use_user_token,
                    )
                )
                return 0

        if args.command == "llm":
            if args.llm_command == "diagnose":
                result = runtime.diagnose_llm()
                print_json(
                    {
                        "ok": result.ok,
                        "provider": result.provider,
                        "model": result.model,
                        "base_url": result.base_url,
                        "key_present": result.key_present,
                        "status": result.status,
                        "headline": result.headline,
                        "detail": result.detail,
                        "recommendation": result.recommendation,
                        "ollama_config_snippet": runtime.ollama_config_snippet(),
                    }
                )
                return 0

        if args.command == "atm":
            if args.atm_command == "show-env":
                print_json(load_atm_environment().data)
                return 0
            if args.atm_command == "plan-deploy":
                print(render_deploy_plan())
                return 0
            if args.atm_command == "plan-recover":
                print(render_recovery_plan())
                return 0
            if args.atm_command == "check":
                targets = ["atm-app-server", "atm-bigdata-server"] if args.target == "all" else [args.target]
                outputs = []
                for target in targets:
                    outputs.append(
                        runtime.execute_single(
                            target=target,
                            action="health-check",
                            service=None,
                            confirm_change=False,
                            report=args.report,
                        )
                    )
                print("\n\n".join(outputs))
                return 0
            if args.atm_command == "runbook":
                print(read_atm_runbook())
                return 0
            if args.atm_command == "export":
                copied = export_atm_bundle(args.output_dir)
                print_json([str(path) for path in copied])
                return 0
            if args.atm_command == "deploy-package":
                print(
                    runtime.deploy_package(
                        preset_key=args.preset,
                        package_path=args.package_path,
                        confirm_change=args.confirm_change,
                        approval_ticket=args.approval_ticket,
                        operator=args.operator,
                        report=args.report,
                    )
                )
                return 0

        raise ValueError(f"unknown command: {args.command}")
    except KeyboardInterrupt:
        print("execution cancelled")
        return 130
    except Exception as exc:
        print(f"execution failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
