from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Callable

from atm_ops_agent.advanced_skills import ADVANCED_SKILLS
from atm_ops_agent.config import Inventory, LLMConfig
from atm_ops_agent.llm_gateway import chat_completion, llm_ready
from atm_ops_agent.nlu import parse_prompt
from atm_ops_agent.planner import PlannedStep
from atm_ops_agent.prompting import build_ops_chat_system_prompt, build_ops_chat_user_payload


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(slots=True)
class ChatAgentResult:
    reply: str
    steps: list[PlannedStep]
    mode: str


class OpsChatAgent:
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

    def decide(
        self,
        *,
        history: list[ChatMessage],
        prompt: str,
        target: str | None,
        confirm_change: bool,
    ) -> ChatAgentResult:
        if not llm_ready(self.config):
            return self._fallback_plan(prompt, target, reason="当前没有可用的大模型凭据或模型开关未打开")
        try:
            return self._llm_decide(history=history, prompt=prompt, target=target, confirm_change=confirm_change)
        except Exception as exc:
            return self._fallback_plan(prompt, target, reason=f"大模型本次未返回可执行的结构化结果：{exc}")

    def _fallback(self, target: str | None, *, reason: str) -> ChatAgentResult:
        target_text = target or "自动识别目标"
        reply = (
            f"我没有直接执行本次请求，已切换为安全规划模式。目标：{target_text}。\n"
            f"原因：{reason}。\n"
            "我会先给出只读排查或审批前预览；涉及数据库修复、重启、回滚、发布等变更动作时，"
            "必须填写审批单号并显式确认后才能执行。"
        )
        return ChatAgentResult(reply=reply, steps=[], mode="reply")

    def _fallback_plan(self, prompt: str, target: str | None, *, reason: str) -> ChatAgentResult:
        try:
            intent = parse_prompt(prompt)
            target_name = target or self._infer_target(prompt)
        except Exception:
            return self._fallback(target, reason=reason)

        step = PlannedStep(
            action=intent.action,
            target=target_name,
            service=intent.service,
            reason=f"本地规则兜底生成：{reason}",
        )
        reply = (
            "大模型规划暂时不可用，已使用本地规则生成审批前预览。\n"
            f"原因：{reason}\n"
            "只读动作可以按预览继续执行；重启、发布、回滚、数据库修复等变更动作仍需要审批单号和显式确认。"
        )
        return ChatAgentResult(reply=reply, steps=[step], mode="plan")

    def _infer_target(self, prompt: str) -> str:
        lowered = prompt.lower()
        for server in self.inventory.servers:
            if server.name.lower() in lowered or server.host in prompt:
                return server.name
            if any(tag.lower() in lowered for tag in server.tags):
                return server.name

        aliases = {
            "atm-app-server": ("app", "nacos", "gateway", "nginx", "web", "frontend", "backend"),
            "atm-bigdata-server": ("bigdata", "spark", "hadoop", "xxl", "xxl-job", "dolphin", "clickhouse", "postgres"),
            "atm-mysql-server": ("mysql", "database", "db"),
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
        raise ValueError("unable to infer target server from prompt")

    def _llm_decide(
        self,
        *,
        history: list[ChatMessage],
        prompt: str,
        target: str | None,
        confirm_change: bool,
    ) -> ChatAgentResult:
        inventory_summary = [{"name": server.name, "host": server.host, "tags": server.tags} for server in self.inventory.servers]
        knowledge_refs = self._knowledge_refs(prompt)
        allowed_actions = sorted(
            {
                "health-check",
                "disk-usage",
                "process-top",
                "service-status",
                "service-restart",
                "security-audit",
            }
            | set(ADVANCED_SKILLS.keys())
            | set(self.action_provider() if self.action_provider else [])
        )
        messages = [{"role": "system", "content": build_ops_chat_system_prompt(allowed_actions)}]
        for item in history[-8:]:
            messages.append({"role": item.role, "content": item.content})
        messages.append(
            {
                "role": "user",
                "content": build_ops_chat_user_payload(
                    prompt=prompt,
                    target=target,
                    confirm_change=confirm_change,
                    inventory_summary=inventory_summary,
                    knowledge_refs=knowledge_refs,
                ),
            }
        )
        content = chat_completion(self.config, messages=messages, json_mode=True)
        parsed = json.loads(content)
        raw_steps = parsed.get("steps", []) if isinstance(parsed, dict) else []
        steps: list[PlannedStep] = []
        for raw_step in raw_steps:
            action = str(raw_step.get("action", "")).strip()
            target_name = str(raw_step.get("target", "")).strip()
            service = raw_step.get("service")
            reason = str(raw_step.get("reason", "")).strip()
            params = raw_step.get("params", {}) if isinstance(raw_step.get("params", {}), dict) else {}
            if not action or not target_name:
                continue
            steps.append(
                PlannedStep(
                    action=action,
                    target=target_name,
                    service=str(service) if service else None,
                    reason=reason,
                    params={str(key): str(value) for key, value in params.items()},
                )
            )
        return ChatAgentResult(
            reply=str(parsed.get("reply", "")).strip() or "我已经分析了当前请求。",
            steps=steps,
            mode=str(parsed.get("mode", "reply")).strip() or "reply",
        )

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
