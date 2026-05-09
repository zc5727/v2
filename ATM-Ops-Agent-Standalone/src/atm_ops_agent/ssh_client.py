from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import PurePosixPath
import subprocess
import sys
from typing import Sequence

from atm_ops_agent.config import Server


@dataclass(slots=True)
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SSHClient:
    def __init__(self, connect_timeout: int = 10) -> None:
        self.connect_timeout = connect_timeout

    def build_ssh_command(self, server: Server, remote_command: str) -> list[str]:
        cmd: list[str] = [
            "ssh",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-p",
            str(server.port),
        ]
        if server.identity_file:
            cmd.extend(["-i", server.identity_file])
        destination = f"{server.user}@{server.host}"
        cmd.extend([destination, remote_command])
        return cmd

    def run(self, server: Server, remote_command: str) -> CommandResult:
        password = os.getenv(server.password_env) if server.password_env else None
        if password:
            return self._run_with_paramiko(server, remote_command, password)

        cmd = self.build_ssh_command(server, remote_command)
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return CommandResult(
            command=remote_command,
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
            returncode=completed.returncode,
        )

    def run_many(self, server: Server, commands: Sequence[str]) -> list[CommandResult]:
        return [self.run(server, command) for command in commands]

    def upload_file(self, server: Server, local_path: str, remote_path: str) -> CommandResult:
        password = os.getenv(server.password_env) if server.password_env else None
        if password:
            return self._upload_with_paramiko(server, local_path, remote_path, password)

        cmd: list[str] = [
            "scp",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-P",
            str(server.port),
        ]
        if server.identity_file:
            cmd.extend(["-i", server.identity_file])
        destination = f"{server.user}@{server.host}:{remote_path}"
        cmd.extend([local_path, destination])
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return CommandResult(
            command=f"scp {local_path} {destination}",
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
            returncode=completed.returncode,
        )

    def _run_with_paramiko(self, server: Server, remote_command: str, password: str) -> CommandResult:
        try:
            import paramiko
        except ImportError as exc:
            return CommandResult(
                command=remote_command,
                stdout="",
                stderr=f"paramiko is required for password login: {exc}",
                returncode=127,
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=server.host,
                port=server.port,
                username=server.user,
                password=password,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
            )
            _, stdout, stderr = client.exec_command(remote_command, timeout=120)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            returncode = stdout.channel.recv_exit_status()
            return CommandResult(command=remote_command, stdout=out, stderr=err, returncode=returncode)
        except Exception as exc:
            return CommandResult(command=remote_command, stdout="", stderr=str(exc), returncode=255)
        finally:
            client.close()

    def _upload_with_paramiko(self, server: Server, local_path: str, remote_path: str, password: str) -> CommandResult:
        try:
            import paramiko
        except ImportError as exc:
            return CommandResult(
                command=f"upload {local_path} -> {remote_path}",
                stdout="",
                stderr=f"paramiko is required for password upload: {exc}",
                returncode=127,
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=server.host,
                port=server.port,
                username=server.user,
                password=password,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
            )
            sftp = client.open_sftp()
            try:
                remote_parent = str(PurePosixPath(remote_path).parent)
                self._mkdir_p_sftp(sftp, remote_parent)
                sftp.put(local_path, remote_path)
            finally:
                sftp.close()
            return CommandResult(
                command=f"upload {local_path} -> {remote_path}",
                stdout=f"uploaded {local_path} -> {remote_path}",
                stderr="",
                returncode=0,
            )
        except Exception as exc:
            return CommandResult(command=f"upload {local_path} -> {remote_path}", stdout="", stderr=str(exc), returncode=255)
        finally:
            client.close()

    def _mkdir_p_sftp(self, sftp: object, remote_dir: str) -> None:
        if not remote_dir or remote_dir in {".", "/"}:
            return
        parts = remote_dir.split("/")
        current = ""
        for part in parts:
            if not part:
                current = "/"
                continue
            current = f"{current.rstrip('/')}/{part}" if current else part
            try:
                sftp.stat(current)
            except OSError:
                sftp.mkdir(current)
