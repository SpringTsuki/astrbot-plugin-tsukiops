"""Bounded synchronous SSH work; the adapter runs it in a dedicated pool."""
import time
from dataclasses import dataclass

MODULES = {"uptime": "uptime", "memory": "free -h",
           "disk": "df -h", "load": "cat /proc/loadavg"}


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed: float

    def text(self):
        parts = [self.stdout] if self.stdout else []
        if self.stderr:
            parts.append("stderr: " + self.stderr)
        if self.exit_code:
            parts.append(f"✗ 退出码：{self.exit_code}")
        return "\n".join(parts) or "✓ 无输出"


def read_channel(channel, deadline, max_bytes=64 * 1024):
    output, errors = bytearray(), bytearray()
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("命令执行超过总时限")
            if channel.recv_ready():
                output.extend(channel.recv(8192))
            if channel.recv_stderr_ready():
                errors.extend(channel.recv_stderr(8192))
            if len(output) + len(errors) > max_bytes:
                raise ValueError("输出超过 64 KiB 限制")
            if (channel.exit_status_ready() and not channel.recv_ready()
                    and not channel.recv_stderr_ready()):
                return CommandResult(
                    output.decode("utf-8", errors="replace").strip(),
                    errors.decode("utf-8", errors="replace").strip(),
                    channel.recv_exit_status(), 0,
                )
            time.sleep(0.01)
    finally:
        channel.close()


class SSHRunner:
    def __init__(self, keys, known_hosts):
        self.keys = keys
        self.known_hosts = known_hosts

    def inspect(self, server, modules, timeout=60):
        import paramiko

        # Resolve every module before opening a connection.
        commands = [(module, MODULES[module]) for module in modules]
        key = self.keys.path(server["key_name"], must_exist=True)
        deadline = time.monotonic() + timeout
        client = paramiko.SSHClient()
        results = {}
        try:
            # No AutoAddPolicy, SSH agent, or implicit default private keys.
            if self.known_hosts.is_file():
                client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            budget = max(0.1, min(10, deadline - time.monotonic()))
            client.connect(hostname=server["ip"], username=server["user"],
                           key_filename=str(key), timeout=budget,
                           banner_timeout=budget, auth_timeout=budget,
                           allow_agent=False, look_for_keys=False)
            for module, command in commands:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("巡检超过总时限")
                started = time.monotonic()
                _, stdout, _ = client.exec_command(command, timeout=min(10, remaining))
                raw = read_channel(stdout.channel, min(deadline, started + 10))
                results[module] = CommandResult(
                    raw.stdout, raw.stderr, raw.exit_code,
                    time.monotonic() - started,
                )
            return results
        finally:
            # Also closes clients when connection/authentication fails.
            client.close()
