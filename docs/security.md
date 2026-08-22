# 安全边界

UNIN 的日常页面不引入审批、计划或多阶段执行门禁，但以下高风险边界仍保留：

- 首次使用创建本地管理员；密码以 PBKDF2 单向摘要保存。
- 会话 Cookie 为 `HttpOnly`、`SameSite=Strict`，所有状态变更请求校验同会话 CSRF。
- NextFind、TMDB、PT、qB 和出站代理凭据仅保存在后端持久配置或部署环境中，API 只返回是否已配置。
- 出站代理只用于 NextFind、TMDB 和 PT 站点的外部 HTTP 请求，不代理 qBittorrent 连接。
- 外部 URL 必须满足协议、主机允许列表、响应大小和超时限制；qB 内网 HTTP 需要显式选择。
- PT 搜索和 qB 状态读取在连接配置完成后可用；qB 写入还要求 `ENABLE_QB_WRITE=true`。
- `.torrent` 在提交前校验结构、文件数量、大小和 info hash；qB 写入超时后按 info hash 对账，
  不直接重复提交。
- API 不提供删除 torrent、删除媒体文件、移动、硬链接、覆盖或媒体库写回操作。

正式发布前仍应使用 HTTPS 反向代理并设置 `AUTH_COOKIE_SECURE=true`，使用独立强密码，
限制 qB 账号权限，并备份 SQLite 文件或 PostgreSQL 数据库。不要把真实凭据提交到 Git。
