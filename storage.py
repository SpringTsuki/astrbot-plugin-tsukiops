"""Local configuration and key paths. No AstrBot dependency."""
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock

_KEY_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class KeyStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)

    def path(self, name, must_exist=False):
        if not isinstance(name, str) or not _KEY_NAME.fullmatch(name):
            raise ValueError("密钥名只能使用 ASCII 字母、数字、点、下划线和连字符")
        path = self.directory / name
        # Reject symlinks too: accepting a basename alone is not sufficient.
        if path.is_symlink() or path.resolve().parent != self.directory:
            raise ValueError("密钥路径无效")
        if must_exist and not path.is_file():
            raise ValueError("密钥不存在")
        return path

    def names(self):
        return sorted(p.name for p in self.directory.iterdir()
                      if p.is_file() and not p.is_symlink() and _KEY_NAME.fullmatch(p.name))

    def delete(self, name, servers):
        target = self.path(name, must_exist=True)
        if any(s["key_name"] == name for s in servers.values()):
            raise ValueError("该密钥仍被服务器引用，请先移除对应服务器")
        target.unlink()

    def install(self, name, staged):
        import paramiko

        target = self.path(name)
        staged = Path(staged)
        if not 0 < staged.stat().st_size <= 64 * 1024:
            raise ValueError("私钥必须非空且不超过 64 KiB")
        # Validate before touching the destination; encrypted keys require a
        # separate passphrase workflow and are not supported by this version.
        try:
            paramiko.PKey.from_path(staged)
        except Exception as exc:
            raise ValueError("私钥无效，或已加密而当前版本不支持口令") from exc
        staged.chmod(0o600)
        try:
            # Exclusive creation with private mode from the first instant.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                try:
                    target.chmod(0o600)
                    output.write(staged.read_bytes())
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
        except FileExistsError as exc:
            raise ValueError("同名密钥已存在，禁止覆盖") from exc


class ServerStore:
    def __init__(self, path, keys):
        self.path = Path(path)
        self.keys = keys
        self.lock = RLock()
        self._servers = {}
        if self.path.exists():
            # Never silently replace damaged configuration with an empty file.
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("servers.json 必须是对象")
            for name, server in data.items():
                self._validate(name, server)
            self._servers = data

    def _validate(self, name, server):
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("服务器名必须为 1 至 100 字符")
        if not isinstance(server, dict):
            raise ValueError("服务器配置无效")
        for field in ("ip", "user", "key_name"):
            value = server.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"缺少有效字段：{field}")
        self.keys.path(server["key_name"])
        if any(c.isspace() for c in server["ip"]) or "://" in server["ip"]:
            raise ValueError("地址应为主机名或 IP，不含协议和空白")

    def snapshot(self):
        with self.lock:
            return deepcopy(self._servers)

    def _commit(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".servers-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self._servers = data
        finally:
            Path(temporary).unlink(missing_ok=True)

    def add(self, name, server):
        self._validate(name, server)
        self.keys.path(server["key_name"], must_exist=True)
        with self.lock:
            if name in self._servers:
                raise ValueError("服务器名称已存在")
            updated = deepcopy(self._servers)
            updated[name] = dict(server)
            self._commit(updated)

    def remove(self, name):
        with self.lock:
            if name not in self._servers:
                raise ValueError("服务器不存在")
            updated = deepcopy(self._servers)
            del updated[name]
            self._commit(updated)
