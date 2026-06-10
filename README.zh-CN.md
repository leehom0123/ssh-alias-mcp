# 🚀 ssh-alias-mcp — 让你的 AI Agent 直接管理 Linux 服务器

[English](README.md)

📖 **[详细技术文档 (DOCS.zh-CN.md)](DOCS.zh-CN.md)** — 架构、全部配置项、MCP 工具参考、脚本示例


## 一句话agent安装

安装请阅读 https://github.com/leehom0123/ssh-alias-mcp/blob/main/AI_INSTALL.md ，按说明配置好SKILL及安装MCP服务。

## 这是什么？

一个不到 2000 行的 Python 小工具，让 Claude Code、Codex CLI、Cursor、Windsurf 等 AI Agent **能 SSH 到你的 Linux 服务器，执行命令、部署代码、查看日志、重启服务**。同时它也是一把好用的 CLI 工具，平时你自己也能用。

**同一套 YAML 配置，同一条连接池，AI 能用，你也能用。**


## 我为什么写这个

我有几台 VPS 跑着不同的项目。每天要做的事大同小异：看看日志有没有报错、部署新代码、重启个容器、检查磁盘是不是又满了。

每回都要：打开终端 → SSH 登录 → 敲命令 → 退出。不烦，但也绝不享受。

后来 Claude Code 支持了 MCP（Model Context Protocol），AI 能调用外部工具了，我就琢磨：我能不能把这几台服务器的常用操作定义成"技能"，让 AI 帮我去干？

于是花了一天用 paramiko 撸了这个。效果意外地好。现在我对 AI 说一句"帮我把三台 Web 服务器都重启一下"，它就自己跑完了。

开源出来，希望对同样管着几台服务器的朋友有用。


## 怎么玩

📥 **安装请阅读 [AI_INSTALL.zh-CN.md](AI_INSTALL.zh-CN.md)，按说明配置 MCP 并添加 `SKILL.md`。** （你也可以把github地址给智能体叫它自己装）

| 工具 | 说明 |
|------|------|
| `ssh_list_servers` | 列出所有服务器 |
| `ssh_run` | 在远程服务器上执行命令 |
| `ssh_run_sudo` | 以 root 身份执行命令（需配置 `sudo_password`） |
| `ssh_upload_script` | 上传脚本（可选立即执行） |
| `ssh_run_script` | 运行已上传的脚本 |
| `ssh_run_alias` | 执行 alias 快捷命令 |
| `ssh_alias:{server}:{name}` | **一键 alias，每个 alias 自动生成一个 MCP 工具** |

> ⚠️ **sudo 别拼在 `ssh_run` 里** — 用 `ssh_run_sudo`。docker 权限不够时 alias 里设 `sudo: true`。

### ⌨️ 当 CLI 用

```bash
python cli.py list-servers                 # 看看有哪些服务器
python cli.py my-server run "uptime"       # 跑一条命令
python cli.py my-server sudo "apt update"  # root 执行
python cli.py my-server alias healthcheck  # 跑一个 alias
python cli.py my-server upload script.sh -r   # 上传并立即执行
```


## 🎯 核心亮点

### 1. YAML 配置复用 + extends 继承

每台服务器就是一个 YAML 文件。按这个目录结构来：

```
servers/
├── _shared/common.yml        # 公共 alias，所有服务器自动继承
├── prod-web-01.yml           # 生产服务器
├── prod-web-02.yml           # 另一台生产
└── staging.yml               # 测试环境
```

最常用的一些检查和命令，写到 `_shared/common.yml` 里，**所有服务器 `extends` 一下就全有了**：

```yaml
# _shared/common.yml
aliases:
  - name: healthcheck
    inline: "df -h / && free -h && uptime"
    desc: "一键健康检查"
  - name: docker-ps
    inline: "docker ps --format 'table {{.Names}}\t{{.Status}}'"
    desc: "查看运行中的容器"
  - name: logs-nginx
    inline: "tail -50 /var/log/nginx/error.log"
    desc: "Nginx 错误日志"
```

```yaml
# prod-web-01.yml
extends:
  - _shared/common.yml         # 继承公共 alias

server:
  host: "198.51.100.10"
  user: "deploy"
  password: "xxx"
  sudo_password: "xxx"
  system: "Ubuntu 22.04 LTS"

aliases:
  - name: deploy
    script: deploy.sh
    desc: "部署主站"
    timeout: 600
    sudo: true

  - name: restart
    inline: "systemctl restart my-app && echo 'restarted'"
    desc: "重启应用"
    sudo: true
```

5 台、10 台、20 台服务器，维护起来都轻松。**同一套 alias，AI 能用，CLI 也能用。** 你没有写两遍任何东西。

### 2. Alias 自动变 MCP 工具

YAML 里定义了 `deploy`，AI 那边就直接多出一个 `ssh_alias:prod-web-01:deploy` 工具。**一行 YAML = AI 的一个技能**。对 AI 来说，你的服务器操作就跟本地函数一样，张口就来。

### 3. 一条连接池，AI 和人共用

```
AI Agent ──→ MCP 协议 ──→ ssh_client.py ──→ 远程服务器
你的终端 ──→ CLI ────────→ ssh_client.py ──→ 远程服务器
```

同一条 SSH 连接、同一个连接池、同一套配置。AI 刚部署完，你终端 `python cli.py` 跑个验证命令，用的是同一个逻辑。不需要两套工具、两套配置。

### 4. 连接池 + 代理 + Sudo

- **连接池**：SSH 连接复用，后台 60 秒保活，不会每敲一条命令就重新登录一次
- **SOCKS5 代理**：全局或按服务器配代理，代理不通自动走直连，不耽误事
- **Sudo**：`sudo_password` 配好，一条命令就能跑 root 操作，不用手动交互


## 适合谁？

- 🧑‍💻 手头有几台 VPS 跑项目，不想来回 SSH
- 👥 小团队没有专门运维，想让 AI 分担日常检查
- 🤖 想让 AI Agent 不止会"聊天"，而是能直接帮你干活

## 技术栈

Python，依赖只有 `paramiko` + `pyyaml` + `pysocks`。不到 2000 行，好读好改。

```bash
pip install -r requirements.txt
```

## 开源协议 & 反馈

MIT 协议，随便用。Star ⭐ 是对我最大的鼓励。

有问题提 Issue，有想法提 PR，中文也行。

**GitHub: https://github.com/leehom0123/ssh-alias-mcp**


📖 **[详细技术文档 (DOCS.zh-CN.md)](DOCS.zh-CN.md)**
