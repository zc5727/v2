from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class Playbook:
    name: str
    mutating: bool
    builder: Callable[..., str]
    description: str


def health_check() -> str:
    return (
        "set -e; "
        "echo '### HOST'; hostname; uptime; "
        "echo '### OS'; cat /etc/os-release 2>/dev/null || uname -a; "
        "echo '### CPU_MEM'; free -h; "
        "echo '### DISK'; df -h; "
        "echo '### TOP'; top -b -n 1 | head -40; "
        "echo '### PORTS'; ss -tulpn; "
        "echo '### ERRORS'; journalctl -p 3 -xb --no-pager | tail -80"
    )


def disk_usage() -> str:
    return "set -e; df -h; echo '### INODES'; df -i; echo '### BIG_DIRS'; du -xh / 2>/dev/null | sort -h | tail -30"


def process_top() -> str:
    return "set -e; echo '### CPU'; ps aux --sort=-%cpu | head -15; echo '### MEM'; ps aux --sort=-%mem | head -15"


def service_status(service: str) -> str:
    return f"set -e; systemctl status {service} --no-pager; echo '### LOGS'; journalctl -u {service} -n 80 --no-pager"


def service_restart(service: str) -> str:
    return f"set -e; systemctl status {service} --no-pager; systemctl restart {service}; sleep 2; systemctl status {service} --no-pager"


def security_audit() -> str:
    return (
        "set -e; "
        "echo '### LISTEN_PORTS'; ss -tulpn; "
        "echo '### SUDO'; sudo -l 2>/dev/null || true; "
        "echo '### LOGIN_HISTORY'; last -n 10 || true; "
        "echo '### FAILED_LOGIN'; lastb -n 10 2>/dev/null || true; "
        "echo '### TIMERS'; systemctl list-timers --all --no-pager | head -40; "
        "echo '### CRON'; crontab -l 2>/dev/null || true"
    )


PLAYBOOKS: dict[str, Playbook] = {
    "health-check": Playbook("health-check", False, health_check, "服务器健康巡检"),
    "disk-usage": Playbook("disk-usage", False, disk_usage, "磁盘与 inode 使用检查"),
    "process-top": Playbook("process-top", False, process_top, "CPU / 内存热点进程检查"),
    "service-status": Playbook("service-status", False, service_status, "查看 systemd 服务状态"),
    "service-restart": Playbook("service-restart", True, service_restart, "重启 systemd 服务"),
    "security-audit": Playbook("security-audit", False, security_audit, "基础安全审计"),
}
