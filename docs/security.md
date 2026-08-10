# 安全边界

## 当前阶段硬限制

- `ENABLE_TMDB_LIVE=false`、`ENABLE_AVISTAZ_LIVE_SEARCH=false` 与 `ENABLE_QB_READ_ONLY=false` 默认禁止三个真实只读边界。
- AvistaZ 上游响应中的 `download`、announce、tracker、passkey 等字段在标准化时直接丢弃；`details_ref` 仅为 SHA256 派生的不可逆内部引用。
- `fetch_torrent()` 固定返回 `PHASE_NOT_ENABLED`，不会访问任何 download URL。
- qBittorrent 适配器只允许 SID 登录及版本、任务、任务文件和分类读取；项目没有添加、开始、暂停、恢复、删除、重校验、改分类/标签/保存路径/文件优先级等方法或 API。
- 批准审批只在数据库中生成非执行 `DownloadPlan`，不会获取 `.torrent`、访问 AvistaZ download URL 或调用 qBittorrent。
- 没有文件移动、复制、硬链接、删除或媒体库写入代码。
- 没有自动身份批准、自动候选批准、自动种子选择、NexusPHP 页面抓取或验证码绕过。

## 外部请求

- TMDB 真实工厂只允许 `https://api.themoviedb.org`；AvistaZ 真实工厂只允许 `https://avistaz.to`。
- 每次重定向都重新验证 HTTPS 和目标主机，最多三次重定向；不会关闭 TLS 校验。
- 外部请求有连接超时、读取超时和响应总大小限制，异步任务可由协程取消。
- TMDB 使用进程内 TTL 缓存与串行限速；AvistaZ 单站并发为 1，任意两次请求间隔至少 6 秒。
- AvistaZ Bearer Token 只保存在适配器实例内存中；401/412 只重新认证并重试一次；429 使用有界指数退避。
- 稳定错误消息不包含上游响应体或凭据；审计详情会递归清理敏感键和 URL 查询参数。

## qBittorrent 只读连接

- `QB_BASE_URL`、`QB_USERNAME`、`QB_PASSWORD` 只由 API 进程从运行时 Secret 读取；qB Secret 不挂载到 Worker 或前端。
- qB URL 必须使用 `QB_ALLOWED_HOSTS` 中的精确主机名。默认仅允许 HTTPS；URL 中不得携带用户名、密码、查询参数或片段。受信内网确需 HTTP 时必须显式设置 `QB_ALLOW_INSECURE_HTTP=true`。
- 客户端设置 `follow_redirects=false`。302/307 登录跳转及任何其他非 2xx 响应统一视为 `QB_HTTP_ERROR`，不能借跳转绕过主机边界。
- HTML 登录页、HTML JSON 响应、空或异常版本字符串、错误 JSON 结构都会返回稳定错误，不会作为成功数据展示。
- SID Cookie 只存在于后端适配器会话内；每次重新认证前先清除旧 SID，任何认证失败或适配器关闭时再次清理。HTTP 客户端设置 `trust_env=false`，不会把 qB 凭据交给环境代理。SID 不写数据库、不返回前端、不进 Pinia 或浏览器存储。
- qB 返回的 progress 必须在 0 到 1，ratio 不小于 -1，时间戳、大小、速度、上传量、做种时长和文件优先级均有严格边界；非法响应整体拒绝。
- 前端每次加载先清空旧状态；错误时再次清空，并使用 generation 丢弃迟到响应，避免旧的“已连接”或任务列表残留。

OpenAPI 中 qB 命名空间只有两个只读端点：

```text
GET /api/downloaders/qbittorrent/status
GET /api/downloaders/qbittorrent/torrents
```

不存在 qB `POST`/`PUT`/`PATCH`/`DELETE` 业务路由。

## 审批与计划完整性

- 审批创建时复制一个完整候选快照；后续候选记录变化不会修改或替换该审批。
- 快照按键排序、无多余空白的规范 JSON 计算 SHA-256；审批读取和任何状态动作前重新计算并比对，同时校验影视 ID、候选 ID、申请时间和有效期重复列。不一致即拒绝。ORM 与 PostgreSQL trigger 禁止更新/删除固定审批字段，并禁止审批事件 UPDATE/DELETE；事件外键使用 `RESTRICT`。
- 同一候选只能存在一个有效的 `PENDING`/`APPROVED` 审批；有效期、最新预检时限和状态机共同防止旧审批复用。
- 预检总体状态采用 `BLOCKED > UNKNOWN > WARNING > PASS`，批准前要求 12 个必需检查各出现一次并从明细重新计算汇总状态。预检绑定后端策略指纹，配置漂移必须重新预检；批准只接受 `PASS` 或 `WARNING`。缺失 info hash、H&R、大小限制、分类、版本规则或无法读取的状态都保持 `UNKNOWN`。
- 批准必须同时确认 H&R、继续做种和仅生成计划三项声明；拒绝与撤销原因可选，但操作者、时间、前后状态、原因、快照哈希与脱敏详情都会进入 `approval_events`。
- 每个审批最多生成一个 `DownloadPlan`。计划只保存严格格式的内部 `torrent_ref`、`save_path_ref`、预期 info hash、分类/标签、预计大小、封闭类型的目的地规划、预检快照和警告；并绑定审批快照哈希、预检策略指纹与自身规范哈希。读取和内部消费前重新校验，ORM 与 PostgreSQL trigger 禁止计划 UPDATE/DELETE。
- 计划禁止包含真实下载 URL、PID、Cookie、Bearer Token、announce URL、passkey 或 qB 密码；`media_destination_plan` 明确标记 `PLAN_ONLY_NO_FILE_OPERATION`。
- `CONSUMED` 是未来阶段的内部单次消费保护，函数会先对审批执行 `SELECT ... FOR UPDATE`；当前没有消费或执行 API，批准本身不会进入 `CONSUMED`。

SHA-256、字段绑定和数据库 trigger 提供应用层及普通数据库写入路径的完整性保护，不是外部签名或 WORM 存储。生产数据库仍必须最小化写权限、限制管理员访问并备份 `approval_events`；能够禁用 trigger 并同时改写记录和哈希的数据库超级管理员超出当前应用层威胁模型。若需覆盖该威胁，应引入运行时 HMAC 密钥或外部追加式审计锚点。

## 凭据与 7 个 Docker Secret

TMDB Bearer Access Token、AvistaZ username/password/PID 以及 qBittorrent base URL/username/password 只可来自服务端运行时 Secret。它们不会写入数据库、前端响应或应用日志。

推荐采用 `deploy/compose.secrets.yaml.example`。API 读取全部 7 个 `/run/secrets/...` 文件；Worker 只读取 TMDB 与 AvistaZ 的前 4 个文件，不获得 qB Secret；前端不挂载任何 Secret。override 会把主 Compose 中同名明文环境变量清空。

| 本地文件 | 容器 Secret | 可见服务 |
|---|---|---|
| `secrets/tmdb_access_token.txt` | `tmdb_access_token` | API、Worker |
| `secrets/avistaz_username.txt` | `avistaz_username` | API、Worker |
| `secrets/avistaz_password.txt` | `avistaz_password` | API、Worker |
| `secrets/avistaz_pid.txt` | `avistaz_pid` | API、Worker |
| `secrets/qb_base_url.txt` | `qb_base_url` | 仅 API |
| `secrets/qb_username.txt` | `qb_username` | 仅 API |
| `secrets/qb_password.txt` | `qb_password` | 仅 API |

项目内 `secrets/*.txt` 已被 `.gitignore` 排除，但文件仍是本机明文 Secret。部署者必须限制 ACL、禁止云同步/备份到不受控位置，并在不再使用时安全移除。文件内容只放值本身，不加引号、不加 `KEY=`，末尾换行会被读取时移除。

不要在聊天、命令参数、截图、Issue、日志或版本库中提供密码、PID、Cookie、Token 或 TMDB Key。PowerShell 隐藏输入和本地 Secret 文件创建方法见项目 README。

## 真实冒烟测试授权边界

执行前必须向用户说明并取得确认：

- TMDB 目标域名为 `api.themoviedb.org`，只对一个用户指定 TMDB ID 执行详情、外部 ID 和必要季信息 GET。
- AvistaZ 目标域名为 `avistaz.to`，只执行 `POST /api/v1/jackett/auth` 与 `GET /api/v1/jackett/torrents`。
- qBittorrent 只读测试目标必须是 `qb_base_url.txt` 指定且被 `QB_ALLOWED_HOSTS` 精确允许的主机，只执行 SID 登录和已列出的只读 GET。
- 不访问任何 download URL，不获取 `.torrent`，不调用 qB 写接口，不操作影视文件。

Mock 验收通过不代表已验证真实账号、真实站点或真实 qB 实例响应。真实开关应在冒烟测试结束后恢复为 `false`。本阶段未执行任何真实 TMDB、AvistaZ 或 qBittorrent 连接。
