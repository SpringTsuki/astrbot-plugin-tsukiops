import asyncio
import json
from pathlib import Path

import paramiko
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.web import json_response, error_response, request, PluginUploadFile
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_ops"


class OpsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        # ── 数据目录（Docker volume 挂载，重启不丢） ──
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.keys_dir = self.data_dir / "keys"
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self.servers_file = self.data_dir / "servers.json"

        # ── 加载服务器列表 ──
        self.servers: dict[str, dict] = self._load_servers()

        # ── 命令白名单：别名 → 实际命令 ──
        self.MODULES = {
            "uptime": "uptime",
            "memory": "free -h",
            "disk": "df -h",
            "load": "cat /proc/loadavg",
        }

        # ══════════════════════════════════════
        #  注册 Web API（供前端页面调用）
        # ══════════════════════════════════════

        context.register_web_api(
            f"/{PLUGIN_NAME}/servers/list",
            self.api_list_servers, ["GET"], "列出所有服务器"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/servers/add",
            self.api_add_server, ["POST"], "添加服务器"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/servers/remove",
            self.api_remove_server, ["POST"], "删除服务器"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/keys/upload",
            self.api_upload_key, ["POST"], "上传 SSH 私钥"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/keys/list",
            self.api_list_keys, ["GET"], "列出已上传密钥"
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/keys/delete",
            self.api_delete_key, ["POST"], "删除 SSH 私钥"
        )

    # ══════════════════════════════════════
    #  数据持久化
    # ══════════════════════════════════════

    def _load_servers(self) -> dict:
        if self.servers_file.exists():
            return json.loads(self.servers_file.read_text("utf-8"))
        return {}

    def _save_servers(self):
        self.servers_file.write_text(
            json.dumps(self.servers, ensure_ascii=False, indent=2), "utf-8"
        )

    # ══════════════════════════════════════
    #  Web API：服务器管理
    # ══════════════════════════════════════

    # 定义服务器列表
    async def api_list_servers(self):
        return json_response({"servers": self.servers})

    # 添加服务器
    async def api_add_server(self):
        payload = await request.json(default={})
        name = (payload.get("name") or "").strip()
        ip = (payload.get("ip") or "").strip()
        user = (payload.get("user") or "").strip()
        key_name = (payload.get("key_name") or "").strip()

        if not all([name, ip, user, key_name]):
            return error_response("所有字段均为必填", status_code=400)
        if name in self.servers:
            return error_response(f"服务器 {name} 已存在", status_code=400)

        self.servers[name] = {"ip": ip, "user": user, "key_name": key_name}
        self._save_servers()
        return json_response({"ok": True, "name": name})

    # 移除服务器
    async def api_remove_server(self):
        payload = await request.json(default={})
        name = (payload.get("name") or "").strip()
        if name not in self.servers:
            return error_response(f"服务器 {name} 不存在", status_code=404)
        del self.servers[name]
        self._save_servers()
        return json_response({"ok": True, "name": name})

    # ══════════════════════════════════════
    #  Web API：密钥管理
    # ══════════════════════════════════════

    # 上传 SSH Key
    async def api_upload_key(self):
        files = await request.files()
        upload: PluginUploadFile | None = files.get("file")
        if not isinstance(upload, PluginUploadFile):
            return error_response("未收到文件", status_code=400)
        safe_name = Path(upload.filename).name
        target = self.keys_dir / safe_name
        await upload.save(target)
        return json_response({"filename": safe_name, "saved": True})

    # SSH Key 列表
    async def api_list_keys(self):
        files = [f.name for f in self.keys_dir.iterdir() if f.is_file()]
        return json_response({"keys": files})

    # 删除 SSH Key
    async def api_delete_key(self):
        payload = await request.json(default={})
        filename = (payload.get("filename") or "").strip()
        if not filename:
            return error_response("缺少文件名", status_code=400)
        target = self.keys_dir / filename
        if not target.exists():
            return error_response("文件不存在", status_code=404)
        target.unlink()
        return json_response({"ok": True, "filename": filename})

    # ══════════════════════════════════════
    #  SSH 执行（同步，跑在线程池不阻塞）
    # ══════════════════════════════════════

    def _ssh_exec(self, name: str, cmd: str) -> str:
        server = self.servers.get(name)
        if not server:
            return f"✗ 服务器 {name} 不存在"

        key_path = str(self.keys_dir / server["key_name"])
        if not Path(key_path).exists():
            return f"✗ 密钥文件不存在: {key_path}"

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=server["ip"],
                username=server["user"],
                key_filename=key_path,
                timeout=10,
            )
            stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            return out if out else err
        except Exception as e:
            return f"✗ SSH 失败: {e}"
        finally:
            client.close()

    def _check_server_sync(self, name: str, modules: list[str]) -> dict:
        """同步巡检单台服务器，一次 SSH 连接跑完所有模块。
        返回 {"模块名": "结果", ...} 或 {"error": "错误信息"}"""
        results = {}
        server = self.servers.get(name)
        if not server:
            return {"error": f"服务器 {name} 不存在"}

        key_path = str(self.keys_dir / server["key_name"])
        if not Path(key_path).exists():
            return {"error": f"密钥文件不存在: {key_path}"}

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=server["ip"],
                username=server["user"],
                key_filename=key_path,
                timeout=10,
            )
        except Exception as e:
            return {"error": f"连接失败: {e}"}

        try:
            for module in modules:
                cmd = self.MODULES.get(module)
                if not cmd:
                    results[module] = "未知模块"
                    continue
                try:
                    stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                    out = stdout.read().decode("utf-8", errors="replace").strip()
                    err = stderr.read().decode("utf-8", errors="replace").strip()
                    results[module] = out if out else err
                except Exception as e:
                    results[module] = f"✗ {e}"
        finally:
            client.close()

        return results

    # ══════════════════════════════════════
    #  聊天指令：/server
    # ══════════════════════════════════════

    @filter.command_group("server")
    def server(self):
        pass

    @server.command("add")
    async def cmd_server_add(self, event: AstrMessageEvent,
                             name: str = "", ip: str = "",
                             user: str = "", key_name: str = ""):
        """添加服务器。用法: /server add <名称> <IP> <用户> <密钥名>
        例: /server add web01 192.168.1.10 astrbot web01_key"""
        if not all([name, ip, user, key_name]):
            yield event.plain_result("用法: /server add <名称> <IP> <用户> <密钥名>")
            return
        if name in self.servers:
            yield event.plain_result(f"✗ 服务器 {name} 已存在")
            return

        self.servers[name] = {"ip": ip, "user": user, "key_name": key_name}
        self._save_servers()
        yield event.plain_result(f"✓ 服务器 {name} 已添加")

    @server.command("list")
    async def cmd_server_list(self, event: AstrMessageEvent):
        """列出所有已保存的服务器"""
        if not self.servers:
            yield event.plain_result("暂无已保存的服务器")
            return
        lines = [f"{name}: {info['ip']} ({info['user']}, 密钥: {info['key_name']})"
                 for name, info in self.servers.items()]
        yield event.plain_result("\n".join(lines))

    @server.command("remove")
    async def cmd_server_remove(self, event: AstrMessageEvent, name: str = ""):
        """删除服务器。用法: /server remove <名称>"""
        if not name:
            yield event.plain_result("用法: /server remove <名称>")
            return
        if name not in self.servers:
            yield event.plain_result(f"✗ 服务器 {name} 不存在")
            return
        del self.servers[name]
        self._save_servers()
        yield event.plain_result(f"✓ 服务器 {name} 已删除")

    # ══════════════════════════════════════
    #  聊天指令：/check
    # ══════════════════════════════════════

    @filter.command("check")
    async def cmd_check(self, event: AstrMessageEvent,
                        name: str = "", module: str = ""):
        """对服务器执行巡检。用法: /check <服务器名> <模块>
        例: /check web01 uptime
        可用模块: uptime, memory, disk, load"""
        if not name:
            yield event.plain_result(
                "用法: /check <服务器名> <模块>\n"
                "例: /check web01 uptime\n"
                f"可用模块: {', '.join(self.MODULES.keys())}"
            )
            return

        if name not in self.servers:
            yield event.plain_result(
                f"✗ 服务器 {name} 不存在。"
                f"已保存: {', '.join(self.servers.keys()) or '无'}"
            )
            return

        if not module:
            yield event.plain_result(f"可用模块: {', '.join(self.MODULES.keys())}")
            return

        if module not in self.MODULES:
            yield event.plain_result(
                f"✗ 未知模块 {module}。可用: {', '.join(self.MODULES.keys())}"
            )
            return

        cmd = self.MODULES[module]
        yield event.plain_result(f"正在 {name} 执行 {module} ...")

        result = await asyncio.to_thread(self._ssh_exec, name, cmd)

        # 截断过长输出
        if len(result) > 2000:
            result = result[:2000] + "\n...(已截断)"

        yield event.plain_result(f"[{name}] {module}\n{result}")

    # ══════════════════════════════════════
    #  聊天指令：/idc daily
    # ══════════════════════════════════════

    @filter.command_group("idc")
    def idc(self):
        pass

    @idc.command("daily")
    async def cmd_idc_daily(self, event: AstrMessageEvent):
        """批量巡检所有已保存的服务器，生成汇总报告"""

        if not self.servers:
            yield event.plain_result("暂无已保存的服务器")
            return

        modules = list(self.MODULES.keys())
        names = list(self.servers.keys())
        yield event.plain_result(f"正在巡检 {len(names)} 台服务器...")

        # 并发：每台服务器一个任务，单台限时 60 秒
        tasks = [
            asyncio.wait_for(
                asyncio.to_thread(self._check_server_sync, name, modules),
                timeout=60.0
            )
            for name in names
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        # 汇总
        lines = ["═══════════════════",
                 " IDC 日常巡检报告",
                 "═══════════════════"]

        for name, result in zip(names, gathered):
            info = self.servers[name]
            lines.append("")
            lines.append(f"【{name}】{info['ip']}")

            if isinstance(result, Exception):
                lines.append(f"  ✗ {result}")
            elif isinstance(result, dict) and "error" in result:
                lines.append(f"  ✗ {result['error']}")
            else:
                # 正常巡检结果，按模块逐行输出
                for module in modules:
                    value = result.get(module, "无数据") if isinstance(result, dict) else "无数据"
                    # 处理多行输出（如 df -h 会有多行）
                    value_lines = value.split("\n")
                    if len(value_lines) == 1:
                        lines.append(f"  {module}: {value_lines[0]}")
                    else:
                        lines.append(f"  {module}:")
                        for vl in value_lines:
                            lines.append(f"    {vl}")

        output = "\n".join(lines)
        if len(output) > 2000:
            output = output[:2000] + "\n...(已截断)"

        yield event.plain_result(output)

    # ══════════════════════════════════════
    #  插件卸载时清理
    # ══════════════════════════════════════

    async def terminate(self):
        logger.info(f"{PLUGIN_NAME} 已卸载")
