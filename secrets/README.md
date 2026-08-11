# 本地运行时 Secret

此目录只保存本机运行时 Secret。`*.txt` 已被 `.gitignore` 排除，但仍是明文文件；不要提交、同步、截图或把内容粘贴到聊天。

配合 `deploy/compose.secrets.yaml.example` 使用时需要以下 12 个文件：

```text
auth_local_username.txt
auth_local_password.txt
auth_session_signing_key.txt
nextfind_username.txt
nextfind_password.txt
tmdb_access_token.txt
avistaz_username.txt
avistaz_password.txt
avistaz_pid.txt
qb_base_url.txt
qb_username.txt
qb_password.txt
```

每个文件只写实际值，不加引号、不写 `KEY=`。`auth_session_signing_key.txt` 必须至少 32 个字符，并应使用独立的高熵随机值；它不是登录密码。

Secret 可见范围固定如下：

| 服务 | 可见 Secret |
|---|---|
| API | 全部 12 个 |
| 普通 Worker | NextFind 2 个、TMDB 1 个、AvistaZ 3 个；无 qB |
| `automation-preflight` | 仅 qB base URL/username/password 3 个 |
| 下载执行器 | AvistaZ 3 个、qB 3 个 |
| 只读下载监控器 | 仅 qB 3 个 |
| 前端 | 0 个 |

qBittorrent SID 只保存在对应适配器进程内存中，不需要也不得创建 SID Secret 文件。`automation-preflight` 不持有 NextFind、TMDB 或 AvistaZ Secret；它只能通过只读适配器执行自动审批预检。readiness 心跳只含非敏感配置指纹和实例 ID，不是 Secret。

Windows PowerShell 请使用 README 中的 `Read-Host -AsSecureString` 片段创建文件并收紧 ACL。Linux 上建议由部署系统提供 Docker Secret；若使用本地文件，至少执行 `chmod 600 secrets/*.txt` 并限制目录访问。

只做 Compose 配置解析时不需要创建这些文件；Compose 配置解析成功不代表 Secret 已就绪。不得用占位凭据启动服务或真实连接。停止使用外部连接后应安全移除对应外部服务 Secret，并把 `ENABLE_TMDB_LIVE`、`ENABLE_AVISTAZ_LIVE_SEARCH`、`ENABLE_QB_READ_ONLY`、`ENABLE_AUTOMATION_ENGINE`、`ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 和 `ENABLE_DOWNLOAD_MONITOR` 恢复为 `false`。认证三个 Secret 缺失时，受保护 API 会 fail closed，而不是退回匿名访问。
