from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib
from typing import Any


@dataclass(slots=True)
class Defaults:
    port: int = 22
    user: str = "root"
    identity_file: str | None = None
    password_env: str | None = None
    connect_timeout: int = 10
    report_dir: str = "./reports"


@dataclass(slots=True)
class CloudMonitorConfig:
    enabled: bool = False
    access_key_id: str | None = None
    access_key_secret: str | None = None
    default_region: str | None = None
    endpoint: str = "https://metrics.cn-hangzhou.aliyuncs.com"
    namespace: str = "acs_ecs_dashboard"
    period: str = "60"
    lookback_minutes: int = 10


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "aliyun-bailian"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: str | None = None
    model: str = "qwen-plus"
    timeout_seconds: int = 20


@dataclass(slots=True)
class AlertRule:
    metric: str
    operator: str
    threshold: float
    severity: str = "warning"
    description: str = ""


@dataclass(slots=True)
class AlertingConfig:
    enabled: bool = False
    webhook_url: str | None = None
    rules: list[AlertRule] = field(default_factory=list)


@dataclass(slots=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass(slots=True)
class RiskPolicyConfig:
    freeze_changes: bool = False
    require_approval_for_production: bool = True
    require_approval_for_high_risk: bool = False
    high_risk_actions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FeishuConfig:
    enabled: bool = False
    app_id: str | None = None
    app_secret: str | None = None
    base_url: str = "https://open.feishu.cn"
    timeout_seconds: int = 20
    import_dir: str = "./knowledge/feishu-imports"


@dataclass(slots=True)
class Server:
    name: str
    host: str
    port: int
    user: str
    identity_file: str | None
    password_env: str | None = None
    tags: list[str] = field(default_factory=list)
    environment: str = "unknown"
    region: str | None = None
    instance_id: str | None = None
    bastion_host: str | None = None


@dataclass(slots=True)
class Inventory:
    defaults: Defaults
    cloud_monitor: CloudMonitorConfig
    llm: LLMConfig
    alerting: AlertingConfig
    web: WebConfig
    risk: RiskPolicyConfig
    feishu: FeishuConfig
    servers: list[Server]

    def get_server(self, target: str) -> Server:
        for server in self.servers:
            if server.name == target or server.host == target:
                return server
        raise KeyError(f"target server not found: {target}")


def _expand_path(raw: str | None) -> str | None:
    if not raw:
        return None
    return str(Path(raw).expanduser())


def _env_or_value(item: dict[str, Any], key: str, env_name: str, *fallback_env_names: str) -> str | None:
    value = item.get(key)
    if value:
        return str(value)
    for candidate in (env_name, *fallback_env_names):
        env_value = os.getenv(candidate)
        if env_value:
            return env_value
    return None


def _server_from_item(item: dict[str, Any], defaults: Defaults) -> Server:
    if "name" not in item or "host" not in item:
        raise ValueError("each [[servers]] entry requires name and host")
    return Server(
        name=str(item["name"]),
        host=str(item["host"]),
        port=int(item.get("port", defaults.port)),
        user=str(item.get("user", defaults.user)),
        identity_file=_expand_path(item.get("identity_file", defaults.identity_file)),
        password_env=str(item.get("password_env", defaults.password_env)) if item.get("password_env", defaults.password_env) else None,
        tags=[str(tag) for tag in item.get("tags", [])],
        environment=_infer_environment(item),
        region=str(item["region"]) if item.get("region") else None,
        instance_id=str(item["instance_id"]) if item.get("instance_id") else None,
        bastion_host=str(item["bastion_host"]) if item.get("bastion_host") else None,
    )


def _infer_environment(item: dict[str, Any]) -> str:
    explicit = str(item.get("environment", "") or "").strip().lower()
    if explicit:
        return explicit
    tags = [str(tag).strip().lower() for tag in item.get("tags", [])]
    for candidate in ("production", "prod", "pre", "staging", "test", "dev"):
        if candidate in tags:
            return candidate
    return "unknown"


def _parse_alert_rules(raw_rules: list[dict[str, Any]]) -> list[AlertRule]:
    rules: list[AlertRule] = []
    for raw_rule in raw_rules:
        if not raw_rule.get("metric") or raw_rule.get("threshold") is None:
            continue
        rules.append(
            AlertRule(
                metric=str(raw_rule["metric"]),
                operator=str(raw_rule.get("operator", ">=")),
                threshold=float(raw_rule["threshold"]),
                severity=str(raw_rule.get("severity", "warning")),
                description=str(raw_rule.get("description", "")),
            )
        )
    return rules


def load_inventory(path: str) -> Inventory:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    defaults_raw = raw.get("defaults", {})
    defaults = Defaults(
        port=int(defaults_raw.get("port", 22)),
        user=str(defaults_raw.get("user", "root")),
        identity_file=_expand_path(defaults_raw.get("identity_file")),
        password_env=str(defaults_raw["password_env"]) if defaults_raw.get("password_env") else None,
        connect_timeout=int(defaults_raw.get("connect_timeout", 10)),
        report_dir=str(defaults_raw.get("report_dir", "./reports")),
    )

    cloud_raw = raw.get("cloud_monitor", {})
    cloud_monitor = CloudMonitorConfig(
        enabled=bool(cloud_raw.get("enabled", False)),
        access_key_id=_env_or_value(cloud_raw, "access_key_id", "ALIYUN_ACCESS_KEY_ID"),
        access_key_secret=_env_or_value(cloud_raw, "access_key_secret", "ALIYUN_ACCESS_KEY_SECRET"),
        default_region=str(cloud_raw["default_region"]) if cloud_raw.get("default_region") else None,
        endpoint=str(cloud_raw.get("endpoint", "https://metrics.cn-hangzhou.aliyuncs.com")),
        namespace=str(cloud_raw.get("namespace", "acs_ecs_dashboard")),
        period=str(cloud_raw.get("period", "60")),
        lookback_minutes=int(cloud_raw.get("lookback_minutes", 10)),
    )

    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        enabled=bool(llm_raw.get("enabled", False)),
        provider=str(llm_raw.get("provider", "aliyun-bailian")),
        base_url=str(llm_raw.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
        api_key=_env_or_value(
            llm_raw,
            "api_key",
            "BAILIAN_API_KEY",
            "ALIYUN_BAILIAN_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
        ),
        model=str(llm_raw.get("model", "qwen-plus")),
        timeout_seconds=int(llm_raw.get("timeout_seconds", 20)),
    )

    alert_raw = raw.get("alerting", {})
    alerting = AlertingConfig(
        enabled=bool(alert_raw.get("enabled", False)),
        webhook_url=_env_or_value(alert_raw, "webhook_url", "OPS_AGENT_WEBHOOK_URL"),
        rules=_parse_alert_rules(alert_raw.get("rules", [])),
    )

    web_raw = raw.get("web", {})
    web = WebConfig(
        enabled=bool(web_raw.get("enabled", True)),
        host=str(web_raw.get("host", "127.0.0.1")),
        port=int(web_raw.get("port", 8787)),
    )

    risk_raw = raw.get("risk", {})
    risk = RiskPolicyConfig(
        freeze_changes=bool(risk_raw.get("freeze_changes", False)),
        require_approval_for_production=bool(risk_raw.get("require_approval_for_production", True)),
        require_approval_for_high_risk=bool(risk_raw.get("require_approval_for_high_risk", False)),
        high_risk_actions=[str(item) for item in risk_raw.get("high_risk_actions", [])],
    )

    feishu_raw = raw.get("feishu", {})
    feishu = FeishuConfig(
        enabled=bool(feishu_raw.get("enabled", False)),
        app_id=_env_or_value(feishu_raw, "app_id", "FEISHU_APP_ID", "LARK_APP_ID"),
        app_secret=_env_or_value(feishu_raw, "app_secret", "FEISHU_APP_SECRET", "LARK_APP_SECRET"),
        base_url=str(feishu_raw.get("base_url", "https://open.feishu.cn")),
        timeout_seconds=int(feishu_raw.get("timeout_seconds", 20)),
        import_dir=str(feishu_raw.get("import_dir", "./knowledge/feishu-imports")),
    )

    servers = [_server_from_item(item, defaults) for item in raw.get("servers", [])]
    if not servers:
        raise ValueError("inventory must contain at least one [[servers]] entry")

    return Inventory(
        defaults=defaults,
        cloud_monitor=cloud_monitor,
        llm=llm,
        alerting=alerting,
        web=web,
        risk=risk,
        feishu=feishu,
        servers=servers,
    )
