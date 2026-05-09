from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Callable

from atm_ops_agent.advanced_skills import ADVANCED_SKILLS
from atm_ops_agent.config import Inventory, LLMConfig
from atm_ops_agent.llm_gateway import chat_completion, llm_ready
from atm_ops_agent.nlu import parse_prompt
from atm_ops_agent.playbooks import PLAYBOOKS
from atm_ops_agent.prompting import build_ops_planner_system_prompt, build_ops_planner_user_payload


@dataclass(slots=True)
class PlannedStep:
    action: str
    target: str
    service: str | None = None
    reason: str = ""
    params: dict[str, str] = field(default_factory=dict)


class LLMPlanner:
    def __init__(
        self,
        config: LLMConfig,
        inventory: Inventory,
        knowledge_provider=None,
        action_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self.config = config
        self.inventory = inventory
        self.knowledge_provider = knowledge_provider
        self.action_provider = action_provider

    def plan(self, prompt: str, explicit_target: str | None = None) -> list[PlannedStep]:
        fallback = self._fallback_plan(prompt, explicit_target)
        if not llm_ready(self.config):
            return fallback
        try:
            llm_steps = self._llm_plan(prompt, explicit_target)
            return llm_steps or fallback
        except Exception:
            return fallback

    def _fallback_plan(self, prompt: str, explicit_target: str | None) -> list[PlannedStep]:
        intent = parse_prompt(prompt)
        target = explicit_target or self._infer_target(prompt)
        return [
            PlannedStep(
                action=intent.action,
                target=target,
                service=intent.service,
                reason="根据用户请求生成的受控运维步骤",
            )
        ]

    def _infer_target(self, prompt: str) -> str:
        lowered = prompt.lower()
        for server in self.inventory.servers:
            if server.name.lower() in lowered or server.host in prompt:
                return server.name
            if any(tag.lower() in lowered for tag in server.tags):
                return server.name

        aliases = {
            "atm-app-server": ("应用服务器", "app服务器", "nacos", "网关", "gateway", "前端", "nginx", "后端", "web"),
            "atm-bigdata-server": ("大数据", "调度", "spark", "hadoop", "xxl", "xxl-job", "dolphin", "clickhouse", "worker", "master"),
            "atm-mysql-server": ("mysql", "数据库", "db"),
        }
        for target, words in aliases.items():
            if any(word in lowered for word in words):
                try:
                    self.inventory.get_server(target)
                    return target
                except KeyError:
                    continue
        if len(self.inventory.servers) == 1:
            return self.inventory.servers[0].name
        raise ValueError("unable to infer target server from prompt; pass --target")

    def _llm_plan(self, prompt: str, explicit_target: str | None) -> list[PlannedStep]:
        inventory_summary = [
            {
                "name": server.name,
                "host": server.host,
                "tags": server.tags,
                "region": server.region,
                "instance_id": server.instance_id,
            }
            for server in self.inventory.servers
        ]
        knowledge_refs = self._knowledge_refs(prompt)
        allowed_actions = self._allowed_actions()
        system_prompt = build_ops_planner_system_prompt(allowed_actions)
        user_prompt = build_ops_planner_user_payload(
            prompt=prompt,
            explicit_target=explicit_target,
            inventory_summary=inventory_summary,
            knowledge_refs=knowledge_refs,
        )
        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        }
        content = chat_completion(
            self.config,
            messages=payload["messages"],
            json_mode=True,
        )
        parsed = json.loads(content)
        raw_steps = parsed if isinstance(parsed, list) else parsed.get("steps", [])
        allowed = set(allowed_actions)
        steps: list[PlannedStep] = []
        for raw_step in raw_steps:
            action = str(raw_step.get("action", "")).strip()
            target = str(raw_step.get("target", "")).strip()
            service = raw_step.get("service")
            reason = str(raw_step.get("reason", "")).strip()
            params = raw_step.get("params", {}) if isinstance(raw_step.get("params", {}), dict) else {}
            if action not in allowed or not target:
                continue
            steps.append(
                PlannedStep(
                    action=action,
                    target=target,
                    service=str(service) if service else None,
                    reason=reason,
                    params={str(key): str(value) for key, value in params.items()},
                )
            )
        return steps

    def _allowed_actions(self) -> list[str]:
        extra = self.action_provider() if self.action_provider else []
        return sorted(set(PLAYBOOKS.keys()) | set(ADVANCED_SKILLS.keys()) | set(extra))

    def _knowledge_refs(self, prompt: str) -> list[dict[str, str]]:
        if not self.knowledge_provider:
            return []
        try:
            hits = self.knowledge_provider(prompt)
        except Exception:
            return []
        refs = []
        for item in hits[:5]:
            refs.append(
                {
                    "title": item.title,
                    "target": item.target or "",
                    "resolution": item.resolution,
                    "headline": item.final_headline,
                }
            )
        return refs
