from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import asdict
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from atm_ops_agent.agent import AgentRuntime
from atm_ops_agent.atm_ops import get_deploy_presets, load_atm_environment, render_deploy_plan, render_recovery_plan
from atm_ops_agent.chat_agent import ChatMessage


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _result(ok: bool, target: str, summary: str, sections: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "ok": ok,
        "target": target,
        "summary": summary,
        "sections": sections,
    }


def create_api_handler(runtime: AgentRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                _json_response(self, 200, {"ok": True, "service": "ops-agent-api"})
                return
            if parsed.path == "/environment":
                _json_response(self, 200, load_atm_environment().data)
                return
            if parsed.path == "/inventory":
                _json_response(self, 200, self._inventory_payload())
                return
            if parsed.path == "/skills/list":
                self._skills_list()
                return
            if parsed.path == "/deploy-presets":
                _json_response(self, 200, {"items": [asdict(item) for item in get_deploy_presets()]})
                return
            if parsed.path == "/incidents":
                query = parse_qs(parsed.query)
                limit = int(query.get("limit", ["20"])[0] or 20)
                _json_response(
                    self,
                    200,
                    {"incidents": [asdict(item) for item in runtime.list_incidents(limit=limit)]},
                )
                return
            if parsed.path.startswith("/incidents/"):
                incident_id = parsed.path.removeprefix("/incidents/").strip()
                if not incident_id:
                    _json_response(self, 400, {"ok": False, "error": "incident_id is required"})
                    return
                _json_response(self, 200, asdict(runtime.get_incident(incident_id)))
                return
            self.send_error(404)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            payload = self._read_json()
            try:
                if parsed.path == "/inspection/run":
                    self._inspection_run(payload)
                    return
                if parsed.path == "/atm/health-check":
                    self._atm_health_check(payload)
                    return
                if parsed.path == "/atm/deploy-plan":
                    self._atm_deploy_plan(payload)
                    return
                if parsed.path == "/atm/deploy-package":
                    self._atm_deploy_package(payload)
                    return
                if parsed.path == "/ops/chat":
                    self._ops_chat(payload)
                    return
                if parsed.path == "/ops/plan":
                    self._ops_plan(payload)
                    return
                if parsed.path == "/skills/list":
                    self._skills_list()
                    return
                if parsed.path == "/skills/create":
                    self._skills_create(payload)
                    return
                if parsed.path == "/skills/run":
                    self._skills_run(payload)
                    return
                if parsed.path == "/knowledge/search":
                    self._knowledge_search(payload)
                    return
                if parsed.path == "/llm/diagnose":
                    self._llm_diagnose()
                    return
                if parsed.path == "/atm/recovery-plan":
                    self._atm_recovery_plan(payload)
                    return
                if parsed.path == "/action/run":
                    self._action_run(payload)
                    return
            except Exception as exc:
                _json_response(self, 500, {"ok": False, "error": str(exc)})
                return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            return json.loads(raw) if raw.strip() else {}

        def _inspection_run(self, payload: dict[str, Any]) -> None:
            target = str(payload.get("target", "")).strip()
            scope = str(payload.get("scope", "all")).strip() or "all"
            if not target:
                _json_response(self, 400, {"ok": False, "error": "target is required"})
                return
            action = "health-check" if scope in {"all", "basic"} else "health-check"
            output = runtime.execute_single(
                target=target,
                action=action,
                service=None,
                confirm_change=False,
                report=False,
            )
            _json_response(
                self,
                200,
                _result(
                    ok=True,
                    target=target,
                    summary=f"inspection completed with scope={scope}",
                    sections=[{"name": action, "status": "completed", "output": output}],
                ),
            )

        def _atm_health_check(self, payload: dict[str, Any]) -> None:
            environment = str(payload.get("environment", "atm-pre"))
            requested_target = str(payload.get("target", "all")).strip() or "all"
            targets = [requested_target] if requested_target != "all" else ["atm-app-server", "atm-bigdata-server"]
            sections: list[dict[str, str]] = []
            ok = True
            for target in targets:
                output = runtime.execute_single(
                    target=target,
                    action="health-check",
                    service=None,
                    confirm_change=False,
                    report=False,
                )
                sections.append({"name": target, "status": "completed", "output": output})
            _json_response(
                self,
                200,
                _result(
                    ok=ok,
                    target=environment,
                    summary="ATM read-only health check completed.",
                    sections=sections,
                ),
            )

        def _atm_deploy_plan(self, payload: dict[str, Any]) -> None:
            plan = render_deploy_plan()
            repo_url = payload.get("repo_url")
            branch = payload.get("branch")
            if repo_url or branch:
                plan += f"\n\nrequested_repo: {repo_url or '-'}\nrequested_branch: {branch or '-'}"
            _json_response(
                self,
                200,
                {
                    "plan": plan,
                    "risk": "Plan only. No deployment is executed by this endpoint.",
                    "verification": [
                        "docker compose ps",
                        "curl health endpoints",
                        "check gateway and nginx",
                    ],
                },
            )

        def _atm_recovery_plan(self, payload: dict[str, Any]) -> None:
            scenario = str(payload.get("scenario", "all")).strip() or "all"
            _json_response(self, 200, {"plan": render_recovery_plan() if scenario == "all" else render_recovery_plan()})

        def _atm_deploy_package(self, payload: dict[str, Any]) -> None:
            preset = str(payload.get("preset", "")).strip()
            package_path = str(payload.get("package_path", "")).strip()
            confirm_change = bool(payload.get("confirm_change", False))
            approval_ticket = str(payload.get("approval_ticket", "")).strip() or None
            operator = str(payload.get("operator", "")).strip() or None
            if not preset or not package_path:
                _json_response(self, 400, {"ok": False, "error": "preset and package_path are required"})
                return
            output = runtime.deploy_package(
                preset_key=preset,
                package_path=package_path,
                confirm_change=confirm_change,
                approval_ticket=approval_ticket,
                operator=operator,
                report=False,
            )
            _json_response(
                self,
                200,
                _result(
                    ok=True,
                    target=preset,
                    summary="package deployment preview returned" if not confirm_change else "package deployed",
                    sections=[{"name": preset, "status": "completed", "output": output}],
                ),
            )

        def _ops_chat(self, payload: dict[str, Any]) -> None:
            prompt = str(payload.get("prompt", "")).strip()
            target = str(payload.get("target", "")).strip() or None
            mode = str(payload.get("mode", "auto")).strip() or "auto"
            confirm_change = bool(payload.get("confirm_change", False))
            approval_ticket = str(payload.get("approval_ticket", "")).strip() or None
            operator = str(payload.get("operator", "")).strip() or None
            history = [
                item
                for item in payload.get("history", [])
                if isinstance(item, dict) and item.get("role") in {"system", "user", "assistant"}
            ]
            if not prompt:
                _json_response(self, 400, {"ok": False, "error": "prompt is required"})
                return

            if mode == "execute":
                output = runtime.execute_plan_dialogue(
                    prompt=prompt,
                    target=target,
                    confirm_change=confirm_change,
                    approval_ticket=approval_ticket,
                    operator=operator,
                    report=False,
                )
                summary = "ops dialog executed remote actions"
            elif mode == "plan":
                steps = runtime.plan(prompt, target=target)
                output = runtime.render_plan_steps(steps)
                summary = "ops dialog generated a solution plan"
            else:
                output = runtime.chat(
                    history=[ChatMessage(role=str(item["role"]), content=str(item.get("content", ""))) for item in history],
                    prompt=prompt,
                    target=target,
                    confirm_change=confirm_change,
                    approval_ticket=approval_ticket,
                    operator=operator,
                    report=False,
                )
                summary = "ops agent processed the chat request"

            _json_response(
                self,
                200,
                _result(
                    ok=True,
                    target=target or "auto",
                    summary=summary,
                    sections=[{"name": mode, "status": "completed", "output": output}],
                ),
            )

        def _ops_plan(self, payload: dict[str, Any]) -> None:
            prompt = str(payload.get("prompt", "")).strip()
            target = str(payload.get("target", "")).strip() or None
            if not prompt:
                _json_response(self, 400, {"ok": False, "error": "prompt is required"})
                return
            steps = runtime.plan(prompt, target=target)
            _json_response(
                self,
                200,
                {
                    "steps": [asdict(item) for item in steps],
                    "rendered": runtime.render_plan_steps(steps),
                },
            )

        def _skills_list(self) -> None:
            _json_response(self, 200, {"skills": runtime.list_skills()})

        def _skills_create(self, payload: dict[str, Any]) -> None:
            name = str(payload.get("name", "")).strip()
            description = str(payload.get("description", "")).strip()
            command_template = str(payload.get("command_template", "")).strip()
            validation_template = str(payload.get("validation_template", "")).strip()
            params_hint = str(payload.get("params_hint", "")).strip()
            params_schema = json.dumps(payload.get("params_schema", []), ensure_ascii=False) if payload.get("params_schema") is not None else ""
            prompt_hint = str(payload.get("prompt_hint", "")).strip()
            mutating = bool(payload.get("mutating", False))
            if not name or not description or not command_template:
                _json_response(self, 400, {"ok": False, "error": "name, description and command_template are required"})
                return
            spec = runtime.create_custom_skill(
                name=name,
                description=description,
                mutating=mutating,
                command_template=command_template,
                validation_template=validation_template,
                params_hint=params_hint,
                prompt_hint=prompt_hint,
                params_schema=params_schema,
            )
            _json_response(
                self,
                200,
                {
                    "ok": True,
                    "skill": {
                        "name": spec.name,
                        "description": spec.description,
                        "mutating": spec.mutating,
                    },
                },
            )

        def _skills_run(self, payload: dict[str, Any]) -> None:
            target = str(payload.get("target", "")).strip()
            action = str(payload.get("action", "")).strip()
            service = payload.get("service")
            params = payload.get("params", {})
            confirm_change = bool(payload.get("confirm_change", False))
            approval_ticket = str(payload.get("approval_ticket", "")).strip() or None
            operator = str(payload.get("operator", "")).strip() or None
            report = bool(payload.get("report", False))
            prompt = str(payload.get("prompt", "")).strip() or f"run skill {action}"
            if not target or not action:
                _json_response(self, 400, {"ok": False, "error": "target and action are required"})
                return
            if params and not isinstance(params, dict):
                _json_response(self, 400, {"ok": False, "error": "params must be an object"})
                return
            output = runtime.execute_single(
                target=target,
                action=action,
                service=str(service) if service else None,
                confirm_change=confirm_change,
                approval_ticket=approval_ticket,
                operator=operator,
                report=report,
                params={str(key): str(value) for key, value in params.items()} if isinstance(params, dict) else {},
                prompt=prompt,
            )
            _json_response(
                self,
                200,
                _result(
                    ok=True,
                    target=target,
                    summary=f"skill {action} completed",
                    sections=[{"name": action, "status": "completed", "output": output}],
                ),
            )

        def _knowledge_search(self, payload: dict[str, Any]) -> None:
            query = str(payload.get("query", "")).strip()
            if not query:
                _json_response(self, 400, {"ok": False, "error": "query is required"})
                return
            knowledge = runtime.search_knowledge(query, limit=int(payload.get("limit", 5) or 5))
            incidents = runtime.search_incidents(query, limit=int(payload.get("limit", 5) or 5))
            _json_response(
                self,
                200,
                {
                    "query": query,
                    "knowledge": [
                        {
                            "knowledge_id": item.knowledge_id,
                            "title": item.title,
                            "target": item.target,
                            "resolution": item.resolution,
                            "headline": item.final_headline,
                        }
                        for item in knowledge
                    ],
                    "incidents": [
                        {
                            "incident_id": item.incident_id,
                            "title": item.title,
                            "status": item.status,
                            "resolution": item.resolution,
                        }
                        for item in incidents
                    ],
                },
            )

        def _llm_diagnose(self) -> None:
            result = runtime.diagnose_llm()
            _json_response(
                self,
                200,
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
                },
            )

        def _action_run(self, payload: dict[str, Any]) -> None:
            target = str(payload.get("target", "")).strip()
            action = str(payload.get("action", "")).strip()
            service = payload.get("service")
            confirm_change = bool(payload.get("confirm_change", False))
            approval_ticket = str(payload.get("approval_ticket", "")).strip() or None
            operator = str(payload.get("operator", "")).strip() or None
            params = payload.get("params", {})
            report = bool(payload.get("report", False))
            prompt = str(payload.get("prompt", "")).strip() or f"run {action}"
            if not target or not action:
                _json_response(self, 400, {"ok": False, "error": "target and action are required"})
                return
            output = runtime.execute_single(
                target=target,
                action=action,
                service=str(service) if service else None,
                confirm_change=confirm_change,
                approval_ticket=approval_ticket,
                operator=operator,
                report=report,
                params={str(key): str(value) for key, value in params.items()} if isinstance(params, dict) else {},
                prompt=prompt,
            )
            _json_response(
                self,
                200,
                _result(
                    ok=True,
                    target=target,
                    summary=f"action {action} completed",
                    sections=[{"name": action, "status": "completed", "output": output}],
                ),
            )

        def _inventory_payload(self) -> dict[str, Any]:
            inventory = runtime.inventory
            return {
                "mode": "managed",
                "path": "",
                "exists": True,
                "managed_endpoint": "",
                "defaults": {
                    "port": inventory.defaults.port,
                    "user": inventory.defaults.user,
                    "password_env": inventory.defaults.password_env or "",
                    "identity_file": inventory.defaults.identity_file or "",
                    "connect_timeout": inventory.defaults.connect_timeout,
                },
                "llm": {
                    "enabled": inventory.llm.enabled,
                    "provider": inventory.llm.provider,
                    "base_url": inventory.llm.base_url,
                    "model": inventory.llm.model,
                    "timeout_seconds": inventory.llm.timeout_seconds,
                    "key_present": bool(inventory.llm.api_key),
                },
                "servers": [
                    {
                        "name": item.name,
                        "host": item.host,
                        "port": item.port,
                        "user": item.user,
                        "password_env": item.password_env or "",
                        "identity_file": item.identity_file or "",
                        "environment": item.environment,
                        "tags": list(item.tags),
                        "region": item.region or "",
                        "instance_id": item.instance_id or "",
                    }
                    for item in inventory.servers
                ],
            }

    return Handler


def serve_api(runtime: AgentRuntime, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), create_api_handler(runtime))
    print(f"ops agent api listening on http://{host}:{port}")
    server.serve_forever()
