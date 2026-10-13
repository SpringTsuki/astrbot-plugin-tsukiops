import asyncio
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import PluginUploadFile, error_response, json_response, request
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .ssh_runner import MODULES, SSHRunner
from .storage import KeyStore, ServerStore

# Keep the old namespace/data path so existing installations can migrate.
PLUGIN_NAME = "astrbot_plugin_ops"


def field(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"缺少字段：{name}")
    return value.strip()


def checked_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("请求必须为 JSON 对象")
    return payload


class OpsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keys = KeyStore(self.data_dir / "keys")
        self.store = ServerStore(self.data_dir / "servers.json", self.keys)
        self.runner = SSHRunner(self.keys, self.data_dir / "known_hosts")
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tsukiops")
        self.slots = asyncio.Semaphore(4)
        self.daily_lock = asyncio.Lock()
        self.stopping = False
        routes = (
            ("servers/list", self.api_list_servers, "GET"),
            ("servers/add", self.api_add_server, "POST"),
            ("servers/remove", self.api_remove_server, "POST"),
            ("keys/upload", self.api_upload_key, "POST"),
            ("keys/list", self.api_list_keys, "GET"),
            ("keys/delete", self.api_delete_key, "POST"),
        )
        for route, handler, method in routes:
            context.register_web_api(
                f"/{PLUGIN_NAME}/{route}", handler, [method], route
            )

    async def api_list_servers(self):
        return json_response({"servers": self.store.snapshot()})

    async def api_add_server(self):
        try:
            payload = checked_payload(await request.json(default={}))
            name = field(payload, "name")
            self.store.add(name, {k: field(payload, k) for k in ("ip", "user", "key_name")})
            return json_response({"ok": True, "name": name})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def api_remove_server(self):
        try:
            name = field(checked_payload(await request.json(default={})), "name")
            self.store.remove(name)
            return json_response({"ok": True, "name": name})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def api_list_keys(self):
        return json_response({"keys": self.keys.names()})

    async def api_upload_key(self):
        temporary = None
        try:
            files = await request.files()
            upload = files.get("file")
            if not isinstance(upload, PluginUploadFile):
                raise ValueError("未收到文件")
            name = upload.filename
            self.keys.path(name)  # Reject separators, traversal and symlinks.
            # Temporary directory also guarantees cleanup on failed validation.
            temporary = tempfile.TemporaryDirectory(prefix="tsukiops-upload-")
            staged = Path(temporary.name) / "key"
            await upload.save(staged)
            self.keys.install(name, staged)
            return json_response({"filename": name, "saved": True})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        finally:
            if temporary is not None:
                temporary.cleanup()

    async def api_delete_key(self):
        try:
            name = field(checked_payload(await request.json(default={})), "filename")
            self.keys.delete(name, self.store.snapshot())
            return json_response({"ok": True, "filename": name})
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

    async def inspect(self, server, modules):
        if self.stopping:
            raise RuntimeError("插件正在停止")
        async with self.slots:
            if self.stopping:
                raise RuntimeError("插件正在停止")
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.pool, self.runner.inspect, server, modules
            )

    @filter.command_group("server")
    def server(self):
        pass

    @server.command("add")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_server_add(self, event: AstrMessageEvent, name: str = "",
                             ip: str = "", user: str = "", key_name: str = ""):
        try:
            self.store.add(name, {"ip": ip, "user": user, "key_name": key_name})
            yield event.plain_result(f"✓ 服务器 {name} 已添加")
        except ValueError as exc:
            yield event.plain_result(f"✗ {exc}")

    @server.command("list")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_server_list(self, event: AstrMessageEvent):
        servers = self.store.snapshot()
        lines = [f"{name}: {info['ip']} ({info['user']}, 密钥: {info['key_name']})"
                 for name, info in servers.items()]
        yield event.plain_result("\n".join(lines) or "暂无已保存的服务器")

    @server.command("remove")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_server_remove(self, event: AstrMessageEvent, name: str = ""):
        try:
            self.store.remove(name)
            yield event.plain_result(f"✓ 服务器 {name} 已删除")
        except ValueError as exc:
            yield event.plain_result(f"✗ {exc}")

    @filter.command("check")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_check(self, event: AstrMessageEvent, name: str = "", module: str = ""):
        servers = self.store.snapshot()
        if name not in servers or module not in MODULES:
            yield event.plain_result("用法：/check <服务器名> <uptime|memory|disk|load>")
            return
        yield event.plain_result(f"正在 {name} 执行 {module} ...")
        try:
            result = await self.inspect(servers[name], [module])
            text = result[module].text()
        except Exception as exc:
            text = f"✗ SSH 失败：{type(exc).__name__}: {exc}"
        if len(text) > 2000:
            text = text[:2000] + "\n...(已截断)"
        yield event.plain_result(f"[{name}] {module}\n{text}")

    @filter.command_group("idc")
    def idc(self):
        pass

    @idc.command("daily")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_idc_daily(self, event: AstrMessageEvent):
        if self.daily_lock.locked():
            yield event.plain_result("已有批量巡检正在执行")
            return
        async with self.daily_lock:
            servers = self.store.snapshot()
            if not servers:
                yield event.plain_result("暂无已保存的服务器")
                return
            yield event.plain_result(f"正在巡检 {len(servers)} 台服务器...")
            names = list(servers)
            results = await asyncio.gather(
                *(self.inspect(servers[name], list(MODULES)) for name in names),
                return_exceptions=True,
            )
            # Use the immutable-per-job snapshot, even if config changes mid-run.
            for name, result in zip(names, results):
                lines = [f"【{name}】{servers[name]['ip']}"]
                if isinstance(result, BaseException):
                    lines.append(f"✗ {type(result).__name__}: {result}")
                else:
                    for module, value in result.items():
                        lines.append(f"{module} ({value.elapsed:.2f}s):\n{value.text()}")
                output = "\n".join(lines)
                # Send every chunk; never silently drop the remaining servers.
                for start in range(0, len(output), 1800):
                    yield event.plain_result(output[start:start + 1800])

    async def terminate(self):
        self.stopping = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        logger.info(f"{PLUGIN_NAME} 已卸载；已运行 SSH 工作会按自身时限退出")
