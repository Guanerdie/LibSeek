# 本地运行时 Secret

此目录只保存本机运行时 Secret。`*.txt` 已被 `.gitignore` 排除，但仍是明文文件；不要提交、同步、截图或把内容粘贴到聊天。

配合 `deploy/compose.secrets.yaml.example` 使用时需要以下 10 个文件：

```text
auth_local_username.txt
auth_local_password.txt
auth_session_signing_key.txt
tmdb_access_token.txt
avistaz_username.txt
avistaz_password.txt
avistaz_pid.txt
qb_base_url.txt
qb_username.txt
qb_password.txt
```

每个文件只写实际值，不加引号、不写 `KEY=`。`auth_session_signing_key.txt` 必须至少 32 个字符，并应使用独立的高熵随机值；它不是登录密码。API 可读取全部 10 个 Secret；普通 Worker 只能读取 TMDB 与 AvistaZ 的 4 个外部服务 Secret；独立下载执行器只读取 AvistaZ 与 qBittorrent 的 6 个 Secret；只读下载监控器只读取 qBittorrent 的 3 个 Secret；前端不能读取任何 Secret。qBittorrent SID 只保存在对应适配器进程内存中，不需要也不得创建 SID Secret 文件。

Windows PowerShell 请使用 README 中的 `Read-Host -AsSecureString` 片段创建文件并收紧 ACL。Linux 上建议由部署系统提供 Docker Secret；若使用本地文件，至少执行 `chmod 600 secrets/*.txt` 并限制目录访问。

只做 Compose 配置解析时不需要创建这些文件；Compose 配置解析成功不代表 Secret 已就绪。不得用占位凭据启动服务或真实连接。停止使用外部连接后应安全移除对应外部服务 Secret，并把 `ENABLE_TMDB_LIVE`、`ENABLE_AVISTAZ_LIVE_SEARCH`、`ENABLE_QB_READ_ONLY`、`ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 和 `ENABLE_DOWNLOAD_MONITOR` 恢复为 `false`。认证三个 Secret 缺失时，受保护 API 会 fail closed，而不是退回匿名访问。
