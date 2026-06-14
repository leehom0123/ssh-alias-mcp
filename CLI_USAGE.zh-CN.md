# CLI 使用参考

所有连接 SSH 的命令均支持 `-s`（sudo）和 `-t`（超时）。
`run`、`run-script` 和 `alias` 支持通过 `stream_cb` 实现实时流式输出。

## 命令

```bash
python cli.py list-servers                             # 列出所有服务器
python cli.py <server> run "<command>" [-s] [-t sec]   # 执行命令（-s = sudo，流式输出）
python cli.py <server> run-script <name> [-s] [-t sec]  # 运行已上传脚本（流式输出）
python cli.py <server> alias <name>                     # 运行别名（流式输出，sudo 在 YAML 中设置）
python cli.py <server> upload <local-path> [-r] [-s] [-n name] [-t sec]  # 上传脚本（-s = sudo 安装）
python cli.py <server> download <remote> <local> [-s] [-p PATTERN] [-t sec]  # 下载文件/目录（-s = sudo 读取）
python cli.py <server> upload-all [-s] [-t sec]         # 上传所有别名脚本（-s = sudo）
python cli.py <server> list-scripts [-s] [-t sec]       # 列出远程脚本（-s = 列出 root 拥有的脚本）
python cli.py <server> list-aliases                      # 列出别名（本地，无需 SSH）
```

## 通用标志

- `-s` / `--sudo` — 以 root 身份执行（需要 `sudo_password`）。上传：通过 /tmp 暂存保留所有者/模式。下载：通过 /tmp 暂存 + chown。
- `-t` / `--timeout` SECS — 超时（秒，默认：300）

## 命令专属标志

- `-n` / `--name` NAME —（上传）自定义远程脚本名称
- `-r` / `--run` —（上传）上传后立即执行脚本
- `-p` / `--pattern` PAT —（下载）正则表达式过滤文件名
- `--no-overwrite` —（下载）跳过已存在的本地文件

## 实时流式输出

`run`、`run-script` 和 `alias` 通过 `stream_cb` 回调实时流式输出。
输出随数据到达即时写入 `stdout`/`stderr`，不会等到完成才输出。

## 示例

```bash
# 列出服务器
python cli.py list-servers

# 执行命令
python cli.py my-server run "uptime"

# 以 root 身份执行
python cli.py my-server run "apt update" -s

# 运行已上传脚本
python cli.py my-server run-script restart.sh -s

# 运行别名
python cli.py my-server alias healthcheck

# 上传并立即执行
python cli.py my-server upload ./fix.sh -r -s

# 使用 sudo 下载
python cli.py my-server download /var/log/secure ./secure.log -s

# 列出 root 拥有的脚本
python cli.py my-server list-scripts -s
```

## 注意事项

- **sudo** — 使用 `run` 配合 `sudo: true`，不要在 `run` 中内联 `sudo -S`
- **Docker 权限** — 如果用户不在 `docker` 组中，在别名上设置 `sudo: true`
