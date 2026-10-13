# Astrbot-Plugin-tsukiOps

面向 AstrBot 的 SSH 只读巡检插件。保留原有命令和 Web API 路径，当前本地重构版尚需 AstrBot 实机联调。

## 命令

聊天入口仅允许 AstrBot 配置的管理员使用：

- `/server add <名称> <主机名或IP> <SSH用户> <密钥名>`
- `/server list`
- `/server remove <名称>`
- `/check <名称> <uptime|memory|disk|load>`
- `/idc daily`

仅执行固定白名单命令：uptime、free -h、df -h、cat /proc/loadavg。批量巡检至多四个工作线程，同一时间只允许一轮 daily；按服务器分段发送结果。

## 安全与迁移

1. 暂不用于生产关键服务器；WebUI 使用内网/VPN，并保留框架鉴权。聊天管理员检查不等于 Web API 鉴权，部署时必须验证两种入口。
2. 旧数据仍在 `plugins_data/astrbot_plugin_ops/`，旧 API 命名空间也保留。先备份目录，再更换插件代码。
3. 密钥文件名仅支持 ASCII 字母、数字、点、下划线和连字符，以字母或数字开头。已有不合规名称需手动迁移并更新服务器配置。
4. 上传私钥最大 64 KiB；仅支持无口令私钥，禁止同名覆盖；被服务器引用的密钥不能删除。上传后服务端校验不替代反向代理/框架的请求体限制。
5. 插件创建专用密钥目录时设为 0700，新增私钥为 0600；既有私钥请管理员核对权限。Windows 的 chmod 不代替 Windows ACL。
6. **SSH 行为有意改变：未知主机和主机密钥变更将拒绝连接。** 不再 AutoAdd，不使用 SSH agent 或默认私钥回退。
7. 在数据目录放入 `known_hosts`：通过服务器控制台或可信渠道取得 SSH 主机公钥和指纹，核对后按 OpenSSH 格式写入，例如 `host.example ssh-ed25519 <主机公钥>`。使用 IP 连接时条目必须匹配该 IP。不要仅执行 ssh-keyscan 就无条件信任结果。
8. 服务器账号只授予巡检所需权限，不使用 root 或 sudo。不依靠“命令是白名单”来替代账号与网络隔离。

### 服务端限制

可参考 `scripts/authorized-command.sh`，由管理员核对命令路径后安装到服务器的 root 所有、巡检用户不可写的路径，例如 `/usr/local/libexec/tsukiops-check`，权限 0755。

authorized_keys 为该专用公钥设置：

```text
restrict,command="/usr/local/libexec/tsukiops-check" ssh-ed25519 <插件专用公钥>
```

包装脚本精确匹配 SSH_ORIGINAL_COMMAND，不使用 eval。不要把四条命令串在 command 中当作可选白名单：那会每次都执行整串命令。不要将账号 shell 直接设为 nologin，因为 SSH 命令仍需要可执行的登录 shell；通过强制命令、专用密钥和禁用转发限制用途。

只读挂载密钥目录适合由管理员预置密钥的模式；此时 Web 上传/删除会失败，应停止使用这两个写入操作。

## 代码结构与验证

- main.py：AstrBot 入口、Web API、巡检任务调度。
- storage.py：安全密钥路径、引用检查、JSON 原子持久化、任务配置快照。
- ssh_runner.py：可信主机检查、超时、并行读取 stdout/stderr、退出码和输出上限。
- tests/test_core.py：不连接 SSH、不依赖 AstrBot 的标准库测试。

```shell
python -m unittest discover -s tests -v
```

依赖：Paramiko >=3.2,<5；AstrBot Web API 兼容性需在实际安装版本上验证。本地测试不等于完整部署认证。

## 已知边界

不支持加密私钥、多进程同时写配置、强制终止已经运行的 Python 线程或多租户权限隔离。卸载后不再接新任务，已经运行的 SSH 工作会自行超时和关闭。单次 check 输出最多显示 2000 字符；底层每个命令最多接收 64 KiB。JSON 损坏时明确报错，不用空配置覆盖旧数据。

作者：浅月。仓库：https://github.com/SpringTsuki/astrbot-plugin-tsukiops

