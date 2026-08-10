# 本地运行时 Secret

此目录只保存本机运行时 Secret。`*.txt` 已被 `.gitignore` 排除，但仍是明文文件；不要提交、同步、截图或把内容粘贴到聊天。

配合 `deploy/compose.secrets.yaml.example` 使用时需要以下 7 个文件：

```text
tmdb_access_token.txt
avistaz_username.txt
avistaz_password.txt
avistaz_pid.txt
qb_base_url.txt
qb_username.txt
qb_password.txt
```

每个文件只写实际值，不加引号、不写 `KEY=`。API 可读取全部 7 个 Secret；Worker 只能读取 TMDB 与 AvistaZ 的前 4 个；前端不能读取任何 Secret。qBittorrent SID 由 API 在登录后保存在适配器内存中，不需要也不得创建 SID Secret 文件。

Windows PowerShell 请使用 README 中的 `Read-Host -AsSecureString` 片段创建文件并收紧 ACL。Linux 上建议由部署系统提供 Docker Secret；若使用本地文件，至少执行 `chmod 600 secrets/*.txt` 并限制目录访问。

只做 Compose 配置解析时可以使用非敏感占位内容，但不得用占位值启动真实连接。停止使用后应安全移除对应文件，并把 `ENABLE_TMDB_LIVE`、`ENABLE_AVISTAZ_LIVE_SEARCH`、`ENABLE_QB_READ_ONLY` 恢复为 `false`。
