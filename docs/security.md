# 安全边界

## 当前阶段硬限制

- `ENABLE_TMDB_LIVE=false`、`ENABLE_AVISTAZ_LIVE_SEARCH=false` 与 `ENABLE_QB_READ_ONLY=false` 默认禁止三个真实只读边界。
- `ENABLE_MEDIA_IMPORT_CONTROL_PLANE=false` 默认关闭阶段 7A；即使显式开启也只允许数据库规划状态，不挂载媒体目录、不扫描文件且没有文件执行器。
- `ENABLE_AUTOMATION_ENGINE=false` 默认关闭自动化总闸；身份确认、种子选择、审批、执行四阶段的初始策略还分别固定为 `MANUAL`。只有某阶段设为 `AUTO_IF_ELIGIBLE`、总闸开启且该动作依赖的所有原有能力开关与资格硬条件同时通过时，才可能创建自动正向动作。
- 自动审批预检位于默认不启动的 `automation-preflight` profile，额外要求 `ENABLE_QB_READ_ONLY=true`。它只有 qB 三项 Secret，只允许 SID 登录和只读 GET；普通 Worker 不 claim 自动预检任务，也没有 qB Secret。
- AvistaZ 上游响应中的 `download`、announce、tracker、passkey 等字段在标准化时直接丢弃；`details_ref` 仅为 SHA256 派生的不可逆内部引用。
- 阶段 4 控制面由 `ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE=false` 默认关闭；intent/execute/reconcile 只写 PostgreSQL，不直接获取 `.torrent` 或调用 qBittorrent。
- 阶段 5 执行器位于独立 `download-execution` profile。`ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 默认全部为 `false` 且必须同时为真；缺一即拒绝启动。启用意味着会访问已批准候选的 AvistaZ download URL，并可能向指定 qBittorrent 执行一次受控 add，因此必须逐次获得用户明确授权。
- qBittorrent 公开 API 仍只允许 SID 登录及版本、任务、任务文件和分类读取；不存在直接 add/start/resume/pause/delete/recheck/download 业务路由。内部写适配器只允许执行器使用 add，且在真正 POST 前执行数据库 write guard。
- 独立监控器由 `ENABLE_DOWNLOAD_MONITOR=false` 默认关闭，并额外要求 `ENABLE_QB_READ_ONLY=true`，且监控目标必须位于 `QB_ALLOWED_SAVE_PATHS` 白名单内；它只读 qB，不调用任何 mutation，也不推断或覆盖 H&R 结论。
- 没有文件扫描、移动、复制、硬链接、覆盖、删除或媒体库写入代码；`HARDLINK`/`COPY` 只是不受信计划中的建议操作类型。
- 自动化策略不追溯 `effective_from` 之前的积压条目；任何低分、并列、冲突、旧解析、站点/实体绑定不一致、IMDb-only、候选警告、季集覆盖不足、H&R `UNKNOWN`、预检非完整 `PASS` 或能力开关缺失都回退人工。自动选种只接受候选 TMDB ID 与已确认影视 TMDB ID 精确一致；自动执行只能 `ADD_PAUSED`，不能自动立即启动。
- 默认注册表没有任何 NexusPHP 真实站点；声明式 NexusPHP 骨架默认关闭，只按经过审查的本地 HTML fixture 解析，且明确拒绝验证码和浏览器挑战，不提供任何绕过能力。

## 本地认证、RBAC 与 CSRF

- 系统使用一个本地账号；用户名、密码和会话签名密钥只由 API 从运行时配置读取。推荐使用仅挂载给 API 的 `auth_local_username`、`auth_local_password`、`auth_session_signing_key` Docker Secret，Worker 和前端不可见。
- 三项认证材料必须同时存在，签名密钥至少 32 个字符。缺失、文件不可读或密钥过短时，认证接口和所有受保护 API 返回 `AUTH_NOT_CONFIGURED`，不会退回匿名模式；健康检查、系统状态和适配器能力信息保持公开且不返回用户数据。
- `AUTH_LOCAL_ROLE` 只能为 `viewer`、`operator`、`admin`，权限逐级继承。`viewer` 只读；`operator` 还可执行发现、身份解析/确认、PT 搜索、下载审批申请/预检/拒绝及创建媒体入库规划请求；下载审批的批准/撤销以及媒体入库规划的批准/拒绝/撤销仅允许 `admin`。审计 actor 来自服务端会话中的用户名，客户端字段不能覆盖。
- 登录前先请求 `GET /api/auth/csrf` 获取有时限的 bootstrap token。`POST /api/auth/login` 必须同时提交 `unin_csrf` Cookie 与同值的 `X-CSRF-Token`；登录后所有状态变更和注销使用会话绑定的 CSRF token，同样执行双提交校验。
- `unin_session` 使用 HMAC 签名，Cookie 属性为 `HttpOnly`、`SameSite=Strict`、`Path=/api`；`unin_csrf` 必须可由前端读取，但同样使用 `SameSite=Strict` 和 `Path=/api`。认证响应使用 `Cache-Control: no-store`，Nginx 必须原样转发 API 的 `Set-Cookie`。
- `AUTH_SESSION_TTL_SECONDS` 默认 28800 秒，可配置 300 至 604800 秒；`AUTH_BOOTSTRAP_CSRF_TTL_SECONDS` 默认 600 秒，可配置 60 至 3600 秒。`AUTH_COOKIE_SECURE=false` 仅适合绑定 `127.0.0.1` 的本机 HTTP；生产 HTTPS 必须设为 `true`。
- 会话是签名无状态 token，注销会清除浏览器 Cookie，但不会维护服务端逐 token 撤销列表。需要立即使全部现有会话失效时，应轮换签名密钥并要求重新登录。签名密钥不得复用登录密码或写入日志、数据库、前端状态、浏览器 Web Storage。

## 自动化策略、身份与审计

- 四阶段模式是封闭枚举 `DISABLED` / `MANUAL` / `AUTO_IF_ELIGIBLE`。`DISABLED` 由后端路由和自动入口共同阻止该阶段新的正向操作，不依赖前端隐藏按钮；`MANUAL` 是每个阶段的默认值。
- 只有 `admin` 可以发布策略修订，且请求必须携带当前 `base_revision_no`；版本冲突返回 409，不允许后写覆盖先写。策略 revision 不可更新或删除，head 只能原子前进一版，当前 revision 读取时会复验整条 SHA-256 前向哈希链。
- 启用自动审批必须分别确认 H&R、继续做种和仅生成计划；启用自动执行必须分别确认 H&R、继续做种和只允许 `ADD_PAUSED`。确认值进入不可变策略修订，不能由运行时默认值静默补齐。
- 自动动作不使用当前登录用户身份，actor 固定在 `system:automation` 命名空间并包含策略修订号。人工操作仍只使用服务端 Principal，客户端不能伪造为系统 actor。
- 每条自动化决策保存策略修订、阶段、动作、结果、实体外键、去重绑定、理由和递归脱敏证据。API 返回前复验 `evidence_hash` 与绑定哈希并再次脱敏，不返回内部 `dedupe_key`；ORM 与 PostgreSQL trigger 禁止修改或删除策略修订和决策。
- 自动 intent/execution 必须同时具有 `origin=AUTOMATION`、策略修订和允许决策绑定，且启动模式只能为 `ADD_PAUSED`；人工记录不得伪造半套自动化绑定。存在决策、自定义策略或自动 execution/intent 绑定时，阶段 6 迁移 downgrade 会失败关闭，避免静默丢失审计。
- 自动预检 readiness 只在总闸、qB 只读开关、凭据存在性、主机/路径策略有效时发布，指纹绑定当前预检策略和 `QB_TARGET_INSTANCE_REF`；下载执行器 readiness 只在控制面、三开关、AvistaZ 搜索、凭据存在性和目标策略有效时发布。两者 TTL 默认均为 90 秒，配置漂移或进程停止后不再允许新的自动动作。
- readiness 心跳只保存非敏感 SHA-256 配置指纹与实例 ID，不保存 URL、用户名、密码、PID、Cookie 或 Token，也不代表外部认证/请求已经成功。它是跨进程最小能力证明，不能代替用户对真实 qB 只读或 add 的授权。
- 自动预检每个 qB GET 前复验任务租约、当前策略、审批快照、预检前状态和队列决策绑定；网络 I/O 期间不持有审批行锁。结果返回后重新加锁并完整复验，策略/快照/预检状态发生变化时丢弃结果并转人工。
- SHA-256 哈希链和数据库 trigger 提供应用层及普通数据库写入路径的篡改检测，不是外部签名或 WORM 存储；能够禁用 trigger 并同时重写记录与哈希的数据库超级管理员仍超出当前威胁模型。

## 外部请求

- TMDB 真实工厂只允许 `https://api.themoviedb.org`；AvistaZ 真实工厂只允许 `https://avistaz.to`。
- 每次重定向都重新验证 HTTPS 和目标主机，最多三次重定向；不会关闭 TLS 校验。
- 外部请求有连接超时、读取超时和响应总大小限制，异步任务可由协程取消。
- TMDB 使用进程内 TTL 缓存与串行限速；AvistaZ 单站并发为 1，任意两次请求间隔至少 6 秒。
- AvistaZ Bearer Token 只保存在适配器实例内存中；401/412 只重新认证并重试一次；429 使用有界指数退避。
- 稳定错误消息不包含上游响应体或凭据；审计详情会递归清理敏感键和 URL 查询参数。

## qBittorrent 只读连接

- `QB_BASE_URL`、`QB_USERNAME`、`QB_PASSWORD` 只挂载给确实需要 qB 的服务：API、`automation-preflight`、下载执行器和只读监控器。普通 Worker 与前端没有 qB Secret；`automation-preflight` 不持有 NextFind、TMDB 或 AvistaZ Secret。
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
- 预检总体状态采用 `BLOCKED > UNKNOWN > WARNING > PASS`，批准前要求 12 个必需检查各出现一次并从明细重新计算汇总状态。预检绑定后端策略指纹，配置漂移必须重新预检；人工批准只接受 `PASS` 或 `WARNING`，自动批准只接受完整、新鲜的 `PASS`。缺失 info hash、H&R、大小限制、分类、版本规则或无法读取的状态都保持 `UNKNOWN`，不能自动提升。
- 人工批准必须逐次确认 H&R、继续做种和仅生成计划三项声明；自动批准只能使用已经写入当前不可变策略修订的对应三项确认。拒绝与撤销原因可选，但操作者、时间、前后状态、原因、快照哈希与脱敏详情都会进入 `approval_events`。
- 每个审批最多生成一个 `DownloadPlan`。计划只保存严格格式的内部 `torrent_ref`、`save_path_ref`、预期 info hash、分类/标签、预计大小、封闭类型的目的地规划、预检快照和警告；并绑定审批快照哈希、预检策略指纹与自身规范哈希。读取和内部消费前重新校验，ORM 与 PostgreSQL trigger 禁止计划 UPDATE/DELETE。
- 计划禁止包含真实下载 URL、PID、Cookie、Bearer Token、announce URL、passkey 或 qB 密码；`media_destination_plan` 明确标记 `PLAN_ONLY_NO_FILE_OPERATION`。
- `CONSUMED` 是执行器成功终结时使用的内部单次消费保护。execute 控制面只排队，审批保持 `APPROVED`；实际 `.torrent` 校验和提交预留时才进入 `EXECUTING`，qB 结果只读验证且唯一 `DownloadJob` 创建成功后才进入 `CONSUMED`。没有公开消费 API。

## 执行意图、幂等与对账边界

- `ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE=false` 默认关闭创建意图和执行记录；即使显式开启，也只启用数据库控制面，不代表 AvistaZ 取种或 qB 写能力已启用。执行阶段为 `AUTO_IF_ELIGIBLE` 时，人工或自动批准后都会立即评估独立执行资格；自动执行还必须同时通过自动化总闸、执行阶段策略、H&R/确认项和执行能力复核，且只能创建 `ADD_PAUSED` 执行记录。
- 执行意图 nonce 使用密码学安全随机数生成，只在 `POST .../execution-intents` 的首次成功响应返回原文。数据库、审计与后续查询只保存或返回 SHA-256；丢失原文后不能恢复。
- 人工 intent 固定绑定审批快照哈希、下载计划哈希、qB 目标指纹、`ADD_PAUSED`/`START_IMMEDIATELY` 启动模式和短有效期。自动 intent 额外绑定策略修订与允许决策并固定为 `ADD_PAUSED`。执行时重新计算全部绑定，配置漂移、过期、撤销、策略变化、错误 nonce 或已消费 intent 都会失败。
- `Idempotency-Key` 限 16 至 200 位安全字符，数据库只保存 SHA-256 并施加唯一约束；审批 ID 与 intent ID 也分别唯一。相同键只能重放完全相同的请求，不能改绑其他审批、intent 或 nonce。
- execute 只创建 `PENDING` 记录，不消费审批、不取种、不连接 qB。独立执行器随后使用数据库时间和租约 token 锁定记录，在每个外部请求前复验审批与 fencing，并在 qB add 前同一事务持久化实际 v1/v2 info hash、大小、文件数和 `SUBMITTING` 状态。
- 不确定结果进入 `OUTCOME_UNKNOWN` 或 `RECONCILIATION_REQUIRED`，`next_retry_at` 必须为空。reconcile 端点只把它排入 `RECONCILIATION_PENDING` 并写不可变事件，不执行外部请求、不把人工请求冒充为已成功。
- 执行响应不暴露 nonce、幂等键摘要、请求哈希、lease token、真实 qB URL、保存路径或凭据。PostgreSQL trigger 和 ORM 事件禁止修改 intent/execution 固定绑定、删除记录、修改审计事件，实际 info hash 一旦写入也不可替换。

## 执行器与未知结果

- 执行器只处理 `PENDING` 或已到期的 `RETRY_WAIT`，通过 `FOR UPDATE SKIP LOCKED` claim；租约续期、外部请求前检查和真正 qB POST 前的 `write_guard` 都使用数据库时间，避免主机时钟漂移和失去租约的 Worker 继续写入。
- AvistaZ 必须精确重新搜索唯一的已批准 torrent ID；标题、TMDB ID、候选 hash 或大小漂移均失败关闭。`.torrent` 必须是有界合法 bencode，文件数量/总大小受限，并计算 v1/v2 hash alias 后再允许提交。
- qB add 前按全部 hash alias 查重；add 后必须重新读取 qB 并验证 hash、分类、保存路径和大小。无法观察到唯一一致任务不能视为成功。
- `VALIDATING` 中只有明确标记为 retryable 且尚未超过尝试上限的错误可进入 `RETRY_WAIT`。一旦提交预留已经持久化，任何失败都禁止自动再次 add：写入可能发生时进入 `OUTCOME_UNKNOWN`，尚未确认写入但无法完成验证时进入 `RECONCILIATION_REQUIRED`，提交中租约过期同样要求人工对账。
- API 的人工 reconcile 只追加请求事件并进入 `RECONCILIATION_PENDING`；当前没有自动对账执行器，也不会根据人工请求直接标记成功或失败。

## 下载任务与监控边界

- 提交成功或确认已存在后只能创建一个绑定 execution/approval/media/hash 的 `DownloadJob`。列表和详情只返回内部 `save_path_ref`，不返回真实保存路径、qB URL、Worker ID 或 lease token；内部错误消息替换为固定提示。
- 监控器只读取 qB 快照并更新状态、进度、速度、流量、Ratio、完成时间和最后观察时间。任务缺失标记为 `MISSING`，分类或大小漂移标记为 `ERROR`；它不暂停、恢复、删除、重校验或修改 qB。
- H&R 没有可靠通用 qB 来源。新任务固定为 `UNKNOWN`；监控器不会根据 Ratio、做种时间或状态推断 H&R，也不会覆盖已经存在的 `AT_RISK` 或 `SATISFIED`。
- summary 固定组合 job/media/approval/execution/warnings；timeline 合并三类追加式事件并再次执行递归脱敏。`HNR_STATUS_UNKNOWN` 作为警告展示，不会被包装成已满足。

## 媒体入库规划边界

- 媒体入库规划总闸默认关闭。`MEDIA_IMPORT_TARGET_ROOT_REFS` 只接受逗号分隔的不透明内部引用，不是路径映射；为空、重复、大小写碰撞或格式无效时失败关闭。`MEDIA_IMPORT_PREFLIGHT_MAX_AGE_SECONDS` 默认 300 秒，范围固定为 30 至 3600 秒。
- 只有进度为 100% 且状态为 `SEEDING`、`COMPLETED` 或 `PAUSED` 的 `DownloadJob` 可创建请求；绑定的 execution 必须为已核验的 `SUBMITTED`/`ALREADY_PRESENT` 终态、无需对账，原下载审批必须已消费。旧下载审批、执行意图和 qB add 授权不授予媒体文件操作权限。
- 客户端源清单、目标映射、根引用及建议的 `HARDLINK`/`COPY` 都是不受信提案。请求边界禁止绝对/UNC/盘符路径、反斜杠、空/点/父目录段、控制字符、Windows 保留设备名、非规范 Unicode、大小写/Unicode 碰撞、源/目标重复和目标文件/目录前缀碰撞，并限制单条路径、文件数和累计路径文本量。
- `MediaImportPlan` 固定为 `PLAN_ONLY_NO_FILE_OPERATION`、`source_retention=true`、`overwrite_allowed=false`。源清单、目标映射、下载/媒体/执行摘要、配置指纹和计划自身分别使用规范 JSON SHA-256 绑定；ORM 与 PostgreSQL trigger 禁止更新/删除固定计划。
- inspection 只能来自内部受信只读边界，客户端没有提交 inspection 或 preflight 的公开端点。结果必须与提案的任务、info hash、根引用、每个源文件路径/大小及每个目标路径逐项一致，并检查源存在/普通文件/非符号链接/完成状态、目标不存在，以及硬链接同文件系统或复制空间充足。`BLOCKED`/`UNKNOWN` 不得批准。
- preflight 是追加式记录；过期后允许重新检查，读取和批准只使用最新记录。配置指纹或计划绑定漂移时旧结果不可复用。批准只接受新鲜 `PASS`/`WARNING`，要求管理员分别确认仅规划、源保留和禁止覆盖；H&R 非 `SATISFIED` 时还要额外确认。
- 公共命名空间只有 create/list/get/approve/reject/revoke，不存在 execute、scan、move、copy、hardlink、delete 或媒体库 writeback 路由。当前 Compose 没有媒体路径 volume、inspection Worker 或文件执行器，因此获批状态也不会操作文件。

SHA-256、字段绑定和数据库 trigger 提供应用层及普通数据库写入路径的完整性保护，不是外部签名或 WORM 存储。生产数据库仍必须最小化写权限、限制管理员访问并备份 `approval_events`；能够禁用 trigger 并同时改写记录和哈希的数据库超级管理员超出当前应用层威胁模型。若需覆盖该威胁，应引入运行时 HMAC 密钥或外部追加式审计锚点。

## 凭据与分阶段 Docker Secret

本地认证 username/password/session signing key、NextFind username/password、TMDB Bearer Access Token、AvistaZ username/password/PID 以及 qBittorrent base URL/username/password 只可来自服务端运行时 Secret。它们不会写入数据库、前端响应或应用日志。

推荐按授权进度采用三份可叠加的最小 override：`deploy/compose.secrets.discovery.yaml.example` 只声明认证、NextFind 和 TMDB 6 个文件；`deploy/compose.secrets.avistaz.yaml.example` 只声明 AvistaZ 3 个文件；`deploy/compose.secrets.qb.yaml.example` 只声明 qB 3 个文件。每一层只把本组同名明文环境变量清空并改为 `/run/secrets/...`，不会把尚未录入的另一组声明为必需文件，也不会自动开启实时能力开关或 profile。未叠加层的凭据必须在 `.env` 中保持空白。

现有 `deploy/compose.secrets.yaml.example` 继续提供全部 12 个 Secret 的兼容入口，只有全部文件已安全录入时才使用；不得与三份分层 override 重复叠加。三层全部叠加后，API 读取全部 12 个；普通 Worker 读取 NextFind 2 个、TMDB 1 个和 AvistaZ 3 个，明确没有 qB；自动预检 Worker 只读取 qB 3 个；下载执行器读取 AvistaZ 3 个与 qB 3 个；只读监控器只读取 qB 3 个；前端不挂载任何 Secret。

| 本地文件 | 容器 Secret | 可见服务 |
|---|---|---|
| `secrets/auth_local_username.txt` | `auth_local_username` | 仅 API |
| `secrets/auth_local_password.txt` | `auth_local_password` | 仅 API |
| `secrets/auth_session_signing_key.txt` | `auth_session_signing_key` | 仅 API |
| `secrets/nextfind_username.txt` | `nextfind_username` | API、普通 Worker |
| `secrets/nextfind_password.txt` | `nextfind_password` | API、普通 Worker |
| `secrets/tmdb_access_token.txt` | `tmdb_access_token` | API、普通 Worker |
| `secrets/avistaz_username.txt` | `avistaz_username` | API、普通 Worker、下载执行器 |
| `secrets/avistaz_password.txt` | `avistaz_password` | API、普通 Worker、下载执行器 |
| `secrets/avistaz_pid.txt` | `avistaz_pid` | API、普通 Worker、下载执行器 |
| `secrets/qb_base_url.txt` | `qb_base_url` | API、自动预检 Worker、下载执行器、只读监控器 |
| `secrets/qb_username.txt` | `qb_username` | API、自动预检 Worker、下载执行器、只读监控器 |
| `secrets/qb_password.txt` | `qb_password` | API、自动预检 Worker、下载执行器、只读监控器 |

项目内 `secrets/*.txt` 已被 `.gitignore` 排除，但文件仍是本机明文 Secret。部署者必须限制 ACL、禁止云同步/备份到不受控位置，并在不再使用时安全移除。文件内容只放值本身，不加引号、不加 `KEY=`，末尾换行会被读取时移除。

Compose `config` 只证明引用结构能被解析，不检查源文件是否存在。启动前应按实际叠加层分别检查 6/3/3 个文件的存在性；禁止为了通过检查创建空文件、占位值或伪造凭据。只叠加 discovery 层时，AvistaZ/qB 文件缺失是预期状态，不应阻止基础服务启动。

不要在聊天、命令参数、截图、Issue、日志或版本库中提供密码、签名密钥、PID、Cookie、Token 或 TMDB Key。PowerShell 隐藏输入和本地 Secret 文件创建方法见项目 README。

## 真实冒烟测试授权边界

执行前必须向用户说明并取得确认：

- TMDB 目标域名为 `api.themoviedb.org`，只对一个用户指定 TMDB ID 执行详情、外部 ID 和必要季信息 GET。
- AvistaZ 目标域名为 `avistaz.to`，只执行 `POST /api/v1/jackett/auth` 与 `GET /api/v1/jackett/torrents`。
- qBittorrent 只读测试目标必须是 `qb_base_url.txt` 指定且被 `QB_ALLOWED_HOSTS` 精确允许的主机，只执行 SID 登录和已列出的只读 GET。
- 真实数据下启用 `ENABLE_AUTOMATION_ENGINE` 必须单独说明本次准备启用的阶段、策略阈值、目标 TMDB/AvistaZ/qB 服务、会产生的状态变化和人工 fallback。只批准读取自动化策略页或发布 `MANUAL` 修订，不等于批准任何真实外部请求。
- 启动 `automation-preflight` profile 会让专用 Worker 在出现合格审批时主动登录目标 qB、执行只读 GET，并可能在完整 `PASS` 后自动批准和生成计划；必须在启动前单独确认这一动作。该授权不包含 AvistaZ download URL、`.torrent` 获取或 qB add。
- 上述只读授权不包含 AvistaZ download URL、`.torrent` 获取或 qB add。若要验证执行器，必须另行列出已批准候选、将访问的 AvistaZ download URL 类型、目标 qB 实例引用、分类、保存路径引用和启动模式，并明确说明会新增真实 qB 任务；获得单独确认后才可启用三开关和 `download-execution` profile。
- 监控器也需单独确认目标 qB 实例，但只执行登录与只读 GET。任何授权都不包含暂停、恢复、删除、重校验、媒体文件操作或媒体库写入。
- 媒体入库规划真实数据验证应拆分授权：用户在本地选择一个具体 `DownloadJob` 并提交相对路径/大小/目标引用提案，只授权数据库记录；真实 inspection 需要另行列出将读取的实际源/目标根、只读挂载和检查范围；任何复制、硬链接或媒体库写回还需要后续阶段的独立写入授权。阶段 7A 不请求真实根路径，也不执行这两类后续验证。

Mock/fixture 验收通过不代表已验证真实账号、真实站点、真实 qB 实例响应或真实媒体文件。真实开关应在冒烟测试结束后恢复为 `false`。当前未执行任何真实 TMDB、AvistaZ、qBittorrent 或 NexusPHP 连接，没有真实 NexusPHP Profile 可用，也没有挂载或检查任何真实媒体路径。
