from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tomllib


ROOT_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
TEMPLATES_DIR = ROOT_DIR / "templates"
SERVER_RECOVERY_RUNBOOK = KNOWLEDGE_DIR / "server-recovery-runbook.md"


@dataclass(slots=True)
class ATMEnvironment:
    data: dict

    @property
    def metadata(self) -> dict:
        return self.data.get("metadata", {})

    @property
    def hosts(self) -> list[dict]:
        return list(self.data.get("hosts", []))

    @property
    def backend_jars(self) -> list[dict]:
        return list(self.data.get("backend_jars", []))

    @property
    def gateway_jars(self) -> list[dict]:
        return list(self.data.get("gateway_jars", []))

    @property
    def health_checks(self) -> list[dict]:
        return list(self.data.get("health_checks", []))


@dataclass(frozen=True, slots=True)
class DeployPreset:
    key: str
    title: str
    target: str
    remote_upload_path: str
    activate_command: str
    description: str


def load_atm_environment() -> ATMEnvironment:
    with open(KNOWLEDGE_DIR / "atm-environment.toml", "rb") as fh:
        return ATMEnvironment(tomllib.load(fh))


def read_atm_runbook() -> str:
    return (KNOWLEDGE_DIR / "atm-ops-runbook.md").read_text(encoding="utf-8")


def read_server_recovery_runbook() -> str:
    return SERVER_RECOVERY_RUNBOOK.read_text(encoding="utf-8")


def get_deploy_presets() -> list[DeployPreset]:
    env = load_atm_environment().data
    paths = env.get("paths", {})
    services_dir = paths.get("services_dir", "/data/atm-pre-services")
    gateway_dir = paths.get("gateway_dir", "/data/atm-gateway-pre")
    ui_dir = paths.get("ui_dir", "/data/atm-ui-pre")
    bigdata_dir = paths.get("bigdata_dir", "/data/bigdata-pre")
    return [
        DeployPreset(
            key="frontend-dist",
            title="前端发布包",
            target="atm-app-server",
            remote_upload_path=f"{ui_dir}/dist.tgz",
            activate_command=(
                f"set -e; cd {ui_dir}; "
                "if [ -x ./release.sh ]; then ./release.sh dist.tgz; "
                "else release_dir=$(date +%Y%m%d%H%M%S); mkdir -p releases/$release_dir; "
                "tar -xzf dist.tgz -C releases/$release_dir; ln -sfn releases/$release_dir/dist current; fi; "
                "nginx -t; systemctl reload nginx"
            ),
            description="上传前端 dist.tgz，发布到 /data/atm-ui-pre 并重载 nginx。",
        ),
        DeployPreset(
            key="backend-bundle",
            title="后端与网关一体包",
            target="atm-app-server",
            remote_upload_path="/tmp/atm-backend.tgz",
            activate_command=(
                "set -e; "
                "mkdir -p /data/atm-auto-release/backend; "
                "rm -rf /data/atm-auto-release/backend/*; "
                "tar -xzf /tmp/atm-backend.tgz -C /data/atm-auto-release/backend; "
                f"mkdir -p {services_dir} {gateway_dir}; "
                f"cp -R /data/atm-auto-release/backend/services-compose/. {services_dir}/; "
                f"cp -R /data/atm-auto-release/backend/gateway-compose/. {gateway_dir}/; "
                f"cd {services_dir} && docker compose up -d --build; "
                f"cd {gateway_dir} && docker compose up -d --build"
            ),
            description="上传 atm-backend.tgz，更新后端与网关 compose 目录并重建容器。",
        ),
        DeployPreset(
            key="bigdata-bundle",
            title="大数据部署包",
            target="atm-bigdata-server",
            remote_upload_path="/tmp/bigdata-pre.tgz",
            activate_command=(
                "set -e; "
                f"rm -rf {bigdata_dir}.new; mkdir -p {bigdata_dir}.new; "
                f"tar -xzf /tmp/bigdata-pre.tgz -C {bigdata_dir}.new --strip-components=1; "
                f"rm -rf {bigdata_dir}.bak; if [ -d {bigdata_dir} ]; then mv {bigdata_dir} {bigdata_dir}.bak; fi; "
                f"mv {bigdata_dir}.new {bigdata_dir}; "
                f"mkdir -p {bigdata_dir}/spark-work {bigdata_dir}/logs/spark; "
                f"chmod -R 777 {bigdata_dir}/spark-work {bigdata_dir}/logs; "
                f"cd {bigdata_dir} && docker compose up -d"
            ),
            description="上传 bigdata-pre.tgz，更新 Spark/Hadoop compose 目录并重启集群。",
        ),
    ]


def get_deploy_preset(key: str) -> DeployPreset:
    for preset in get_deploy_presets():
        if preset.key == key:
            return preset
    raise KeyError(f"unknown deploy preset: {key}")


def render_package_deploy_preview(preset_key: str, package_path: str) -> str:
    preset = get_deploy_preset(preset_key)
    return "\n".join(
        [
            f"preset: {preset.key}",
            f"title: {preset.title}",
            f"target: {preset.target}",
            f"local_package: {package_path}",
            f"remote_upload: {preset.remote_upload_path}",
            f"description: {preset.description}",
            "",
            "activate_command:",
            preset.activate_command,
        ]
    )


def render_deploy_plan() -> str:
    env = load_atm_environment()
    lines = [
        f"ATM environment: {env.metadata.get('name', 'unknown')}",
        f"git: {env.metadata.get('git_url', '-')}",
        f"default_branch: {env.metadata.get('default_branch', '-')}",
        "",
        "hosts:",
    ]
    for host in env.hosts:
        lines.append(f"- {host['name']} ({host['role']}): {host['host']}")
    lines.extend(["", "backend jar mapping:"])
    for item in env.backend_jars:
        lines.append(f"- {item['service']}: {item['source']} -> {item['target_name']}")
    lines.extend(["", "gateway jar mapping:"])
    for item in env.gateway_jars:
        lines.append(f"- {item['service']}: {item['source']} -> {item['target_name']}")
    lines.extend(
        [
            "",
            "workflow:",
            "1. pull code",
            "2. build backend with Maven profile pre",
            "3. build atm-ui",
            "4. copy jars into docker build contexts",
            "5. package backend, gateway, ui, optional bigdata artifacts",
            "6. upload to /data release directories",
            "7. docker compose up -d --build",
            "8. run health checks",
            "",
            "deploy presets:",
        ]
    )
    for preset in get_deploy_presets():
        lines.append(f"- {preset.key}: {preset.description}")
    return "\n".join(lines)


def render_recovery_plan() -> str:
    env = load_atm_environment()
    lines = [
        "ATM recovery checklist",
        "",
        "1. verify ECS instance status and security groups",
        "2. verify SSH access",
        "3. inspect date, uptime, df -h, free -h, docker, nginx",
        "4. recover ATM app services on app host",
        "5. recover frontend symlink and reload nginx",
        "6. recover XXL-Job, DolphinScheduler, ClickHouse, Spark/Hadoop",
        "7. run health checks",
        "",
        "health checks:",
    ]
    for item in env.health_checks:
        lines.append(f"- {item['host']} / {item['name']}: {item['command']}")
    return "\n".join(lines)


def render_server_recovery_plan(scenario: str = "all") -> str:
    scenario = (scenario or "all").strip().lower()
    title = {
        "all": "服务器恢复总预案",
        "web": "Web 页面恢复预案",
        "spark": "Spark 集群恢复预案",
    }.get(scenario, "服务器恢复总预案")
    sections = {
        "all": [
            "## 适用范围",
            "## 准备工作",
            "## Web 恢复",
            "## Spark 恢复",
            "## 验证清单",
            "## 注意事项",
        ],
        "web": ["## Web 恢复", "## 验证清单", "## 注意事项"],
        "spark": ["## Spark 恢复", "## 验证清单", "## 注意事项"],
    }.get(scenario, ["## 适用范围", "## 准备工作", "## Web 恢复", "## Spark 恢复", "## 验证清单", "## 注意事项"])
    raw = read_server_recovery_runbook()
    chunks: list[str] = [title, ""]
    for heading in sections:
        part = _slice_markdown_section(raw, heading)
        if part:
            chunks.append(part.strip())
            chunks.append("")
    return "\n".join(chunks).strip()


def _slice_markdown_section(content: str, heading: str) -> str:
    lines = content.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if line.strip() == heading:
                collecting = True
                collected.append(line)
                continue
            if collecting:
                break
        if collecting:
            collected.append(line)
    return "\n".join(collected)


def export_atm_bundle(output_dir: str) -> list[Path]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    files_to_copy = [
        KNOWLEDGE_DIR / "atm-ops-runbook.md",
        KNOWLEDGE_DIR / "atm-environment.toml",
        TEMPLATES_DIR / "atm-auto-deploy.sh",
        TEMPLATES_DIR / "atm-deploy.env.example",
    ]
    copied: list[Path] = []
    for source in files_to_copy:
        target = destination / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied
