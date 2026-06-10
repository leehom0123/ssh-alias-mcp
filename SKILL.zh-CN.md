---
name: ssh-manager
description: Manage remote Linux servers via SSH CLI or server-management MCP. Run commands, deploy, restart services, check logs, and upload scripts.
---

# SSH Manager

两种操作方式：**MCP 工具**（Claude Code 内直接调用）和 **CLI**（终端手动执行）。

## MCP 工具

| 工具 | 参数 | 说明 |
|------|------|------|
| `ssh_list_servers` | — | 列出所有服务器 |
| `ssh_list_aliases` | `server` | 列出 alias 快捷命令 |
| `ssh_list_scripts` | `server` | 列出远程脚本 |
| `ssh_run` | `server`, `command`, `timeout`(默认60s) | 执行命令 |
| `ssh_run_sudo` | `server`, `command`, `timeout`(默认60s) | root 执行（需 `sudo_password`） |
| `ssh_run_alias` | `server`, `alias_name` | 执行 alias |
| `ssh_run_script` | `server`, `script_name`, `timeout`(默认300s) | 执行已上传脚本 |
| `ssh_upload_script` | `server`, `local_path`, `script_name`(可选), `run_immediately`(默认false), `timeout`(默认300s) | 上传脚本 |
| `ssh_alias:{server}:{name}` | —（自动生成） | 一键执行 alias |
| `ssh_upload_all_scripts` | `server` | 按 alias 定义上传所有脚本 |

### 注意事项
- **sudo** 用 `ssh_run_sudo`，不要在 `ssh_run` 里拼 `sudo -S`
- **alias 优先**：常用操作定义为 alias，用 `ssh_run_alias` 执行
- **docker 权限**：用户不在 `docker` 组时，alias 需设 `sudo: true` 或用 `ssh_run_sudo`

## CLI 命令

```
python ./cli.py list-servers                          # 列出所有服务器
python ./cli.py <server> run "<command>" [-t 秒]      # 执行远程命令
python ./cli.py <server> sudo "<command>" [-t 秒]     # root 执行（需 sudo_password）
python ./cli.py <server> alias <alias-name>            # 执行 alias
python ./cli.py <server> upload <本地路径> [-r|--run] [-n 名]  # 上传脚本（-r/--run 立即执行）
python ./cli.py <server> upload-all                    # 按 alias 上传所有脚本
python ./cli.py <server> list-scripts                  # 列出远程脚本
python ./cli.py <server> list-aliases                  # 列出 alias
```

## 配置 & Alias

服务器 yml、alias、共享继承的完整写法见 → [README.zh-CN.md](README.zh-CN.md)
