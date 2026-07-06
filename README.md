# Astrbot-Plugin-tsukiOps
`Astrbot-Plugin-tsukiOps` 是面向 Astrbot 的一个可以远程 SSH 链接 Linux 服务器，批量执行巡检脚本或执行巡检指令的插件。

- 插件名：astrbot-plugin-tsukiops
- 版本：1.0
- 适配平台：Astrbot
- 版本：>=4.22.0
- 编码要求：UTF-8
- 如果有更好的想法可以通过各个平台私信我。Blog 内有我的联系方式：springtsuki.xucjno.com

### 功能简介：

本插件由于目前处于开发初期，所以很多功能并不完善，存在bug也可以多多提交issue或相关建议。咱看到的话都会尽力修改！

目前添加的功能有：

```shell
"uptime": "uptime", # 查看系统运行状态
"memory": "free -h", # 内存状态
"disk": "df -h", # 硬盘状态
"load": "cat /proc/loadavg", # 当前平均负载
```

### 安全风险（需额外留意）：

由于此插件需要将 SSH Key 存储在 Astrbot 框架内，所以一旦被暴露在公网环境下，或通过特殊的社工方法对 Bot 发起攻击，则可能会直接导致服务器连带 SSH Key 一并泄露，进而导致服务器面临灭顶之灾。故一定不要将生产服务器让 Bot 巡检。

### 推荐的安全防护措施：

给 Astrbot 的公钥加以限制，只能执行以上功能的指令。

```shell
restrict,command="cat /proc/loadavg; free -h; df -h; uptime",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...
```

Astrbot 的 密钥目录也可以挂载为只读：

```shell
volumes:
 - ./data:/AstrBot/data
 - ./ssh_keys:/AstrBot/data/plugins_data/astrbot_plugin_ops/keys:ro
```

或通过网络隔离的方式，防火墙只允许 Astrbot 容器的出口 IP 访问巡检服务器的 22端口。让私钥脱离框架则无法使用。

当然，如果有条件，最好还是让 `WebUI` 不暴露在公网上，仅通过 `内网` 或 `VPN` 进行访问。

以及，给 Astrbot 的巡检用户可以 ban 掉 shell 。

```shell
adduser --shell /usr/sbin/nologin --disabled-password astrbot
```

