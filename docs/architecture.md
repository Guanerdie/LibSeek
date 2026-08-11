# 架构与状态流

版本 `0.7.0` 在 FastAPI 控制面、普通发现 Worker、可选自动预检 Worker、可选下载执行器、可选只读监控器、PostgreSQL 和 Vue 管理端之间加入默认关闭的媒体入库规划控制面。阶段 4 的 intent/execute/reconcile 仍是默认关闭的数据库控制面；阶段 5 的真实 AvistaZ 取种与 qB add 仍位于独立执行器中；阶段 6 只在四阶段策略、总闸、资格硬条件、短 TTL readiness 和原有能力闸门同时允许时推进新条目；阶段 7A 只保存不可执行的媒体入库计划、只读预检记录和人工决定。媒体文件始终不在系统操作范围内。

```mermaid
flowchart LR
  UI[Vue 管理端] -->|签名 Cookie + CSRF| Auth[本地认证与 RBAC]
  Auth -->|授权后的查询与操作| API[FastAPI]
  API -->|队列与查询| DB[(PostgreSQL 16)]
  API -->|策略修订与决策查询| Policy[自动化策略与审计]
  Policy -->|不可变修订与脱敏证据| DB
  Worker[普通 Worker] -->|FOR UPDATE SKIP LOCKED| DB
  Worker -->|HTTPS 只读发现| NF[NextFind]
  Worker -->|开关启用后只读 GET| TMDB[TMDB API]
  Worker -->|认证与只读搜索| AZ[AvistaZ Jackett API]
  TMDB -->|最多 5 个候选| DB
  AZ -->|脱敏候选| DB
  DB -->|候选与审计| UI
  API -->|SID 登录与只读 GET| QB[qBittorrent Web API]
  API -->|固定快照、预检与计划| DB
  API -->|intent 与幂等执行请求| DB
  AutoPreflight[automation-preflight profile] -->|只 claim 自动预检任务| DB
  AutoPreflight -->|SID 登录与只读 GET| QB
  AutoPreflight -->|策略指纹 readiness| DB
  Executor[download-execution profile] -->|租约 claim / fencing| DB
  Executor -->|精确重搜并取种| AZ
  Executor -->|受控 add 后只读核验| QB
  Executor -->|执行配置 readiness| DB
  Monitor[download-monitor profile] -->|只读任务观察| QB
  Monitor -->|进度与状态| DB
  API -->|媒体入库提案与人工决定| ImportPlan[PLAN ONLY]
  ImportPlan -->|不可变计划、预检与事件| DB
```

## 认证与授权流

```text
GET /api/auth/csrf
  -> bootstrap CSRF Cookie + token
POST /api/auth/login (Cookie + X-CSRF-Token + 本地凭据)
  -> HttpOnly 签名会话 Cookie + 会话绑定 CSRF Cookie
GET 受保护资源
  -> 验证会话签名、有效期与 viewer 以上权限
POST 状态变更
  -> 额外验证 CSRF Cookie/Header + operator 或 admin 权限
```

认证材料不完整时受保护链路 fail closed。角色按 `viewer < operator < admin` 继承：读取用户数据至少需要 viewer；一般工作流写入需要 operator；审批批准与撤销需要 admin。服务端把会话用户名传入工作流和审批服务作为 actor，不信任请求体中的操作者字段。会话无状态且由 HMAC 签名，轮换 `auth_session_signing_key` 会使全部现有会话失效。

## 保守自动化策略与决策流

全局策略分别控制 `IDENTITY`、`TORRENT_SELECTION`、`APPROVAL`、`EXECUTION` 四个阶段。每个阶段只有三种模式：

| 模式 | 后端行为 |
|---|---|
| `DISABLED` | 阻止该阶段新的人工和自动正向动作；读取、拒绝、撤销和审计仍可进行。 |
| `MANUAL` | 不创建自动正向动作，记录需要人工处理；这是四阶段默认值。 |
| `AUTO_IF_ELIGIBLE` | 只有总闸和全部资格/能力闸门通过时行动；任何歧义、未知或配置缺失都记录原因并回退人工。 |

`ENABLE_AUTOMATION_ENGINE=false` 是独立的进程级总闸，不能覆盖阶段策略，也不能替代 TMDB、AvistaZ、qB 只读、执行控制面或下载执行器各自的开关。自动化只响应当前修订 `effective_from` 之后创建的新任务/审批触发点，不扫描、不追溯旧积压。人工处理始终使用登录 Principal；自动动作使用 `system:automation:r<revision_no>` actor，并在决策记录中保留 `system:automation` 命名空间。

默认资格阈值为：

```text
identity_min_score    = 0.5
identity_min_margin   = 0.1
torrent_min_score     = 0.75
torrent_min_margin    = 0.1
torrent_min_seeders   = 1
```

阈值只是必要条件。身份自动确认还要求解析任务与输入指纹新鲜、无冲突及精确身份依据；种子自动选择还要求 AvistaZ 与媒体绑定、候选 TMDB ID 与已确认影视 TMDB ID 精确一致、年份、H&R、hash、大小、做种数和季集覆盖安全，IMDb-only 候选转人工。H&R 为 `UNKNOWN` 会阻止自动选种、自动审批和自动执行。自动审批只接受检查完整、仍在有效期内且策略指纹未漂移的 `PASS` 预检；人工审批仍可在明确确认后接受 `WARNING`。自动执行固定创建 `ADD_PAUSED`，不存在自动 `START_IMMEDIATELY` 路径。

管理员以 `base_revision_no` compare-and-swap 发布新修订。每个修订不可修改，规范化策略内容包含上一版 `policy_hash`，形成 SHA-256 前向哈希链；策略 head 只能原子前进一版。自动化每次判断都保存策略修订、阶段、动作、结果、实体绑定、理由、递归脱敏证据和 `evidence_hash`；读取列表/详情时重新计算证据哈希及内部去重绑定，公共响应不暴露 `dedupe_key`。自动执行的允许决策与带策略/决策外键的 intent、execution 在同一数据库事务内创建，失败不会留下可脱离审计绑定的执行记录。

启用自动审批的修订必须确认 H&R、继续做种和“仅生成计划”；启用自动执行还必须确认 H&R、继续做种和“只允许 `ADD_PAUSED`”。四项确认分别保存，不能由一个宽泛布尔值代替。发布策略本身不接触外部服务；不满足资格、预检失败或后续能力闸门关闭时保留现有人工工作流。

自动预检和执行采用两条独立的跨进程 readiness：

- `automation-preflight` profile 在自动化总闸、qB 只读开关、qB 凭据存在性、主机白名单、分类和保存路径策略有效后，发布绑定 `preflight_policy_fingerprint` 与 `QB_TARGET_INSTANCE_REF` 的能力指纹；默认 TTL 为 `AUTOMATION_PREFLIGHT_READY_TTL_SECONDS=90`。系统只在匹配心跳新鲜时排队 `AUTOMATION_PREFLIGHT:*`。
- `download-execution` profile 在控制面、执行器、AvistaZ 取种、qB 写和 AvistaZ 搜索开关以及 AvistaZ/qB 凭据存在性、目标主机/路径策略有效后，发布执行配置指纹；默认 TTL 为 `DOWNLOAD_EXECUTOR_READY_TTL_SECONDS=90`。自动审批完成后只有匹配心跳新鲜时才能创建自动 execution。

readiness 的数据库 `worker_id` 只含非敏感 SHA-256 指纹和实例 ID，不保存凭据，也不表示真实登录已成功。配置变化会改变指纹，进程停止会使心跳自然过期，两种情况都阻止新的自动动作并保留人工路径。

普通 Worker 明确排除 `AUTOMATION_PREFLIGHT:*`，且不持有 qB Secret。专用自动预检 Worker 只持有 qB base URL/username/password 三项 Secret；它在每个 qB 只读请求前重新验证租约、当前策略、固定审批快照、预检前状态哈希和队列决策绑定。qB 网络请求阶段不持有审批行锁，完成后才短暂锁定任务和审批，复验全部绑定并保存预检；只有完整 `PASS` 会继续自动批准。

## 身份工作流

```text
DISCOVERED
  -> METADATA_PENDING
  -> IDENTITY_REVIEW
  -> IDENTITY_CONFIRMED
```

NextFind 已提供 TMDB ID 时，Worker 直接读取对应类型详情，不做模糊搜索；若该类型不存在，会只读检查相反类型以暴露类型冲突。年份或类型冲突都作为候选冲突进入 `IDENTITY_REVIEW`。没有 TMDB ID 时，按标题、年份和类型搜索最多 5 个候选。默认仍由人工确认；身份阶段设为 `AUTO_IF_ELIGIBLE` 且总闸开启时，Worker 只会确认当前解析任务中通过分数、前两名差值、输入新鲜度、无冲突和精确类型/标题/年份或 TMDB ID 硬条件的唯一候选，其他情况进入人工处理。

电视剧的 `episode_matrix` 只保留已播集；`TMDB_ALLOW_FUTURE_EPISODES=false` 是默认值。没有播出日期或播出日期在未来的集数不参与缺失集判断。

## PT 搜索工作流

```text
IDENTITY_CONFIRMED
  -> PT_SEARCH_PENDING
  -> PT_SEARCHING
  -> TORRENT_REVIEW | NO_CANDIDATE | SEARCH_FAILED
```

搜索策略固定按 TMDB ID、IMDb ID、英文名加年份、原名加年份、中文名或别名降级。首个返回候选的策略停止。候选由纯函数评分，外部 ID、类型、季集覆盖、年份和用户偏好的权重高于活跃度、促销与大小。默认评分只用于排序和解释；种子选择阶段设为 `AUTO_IF_ELIGIBLE` 且总闸开启时，最高分候选还必须满足差值、AvistaZ/媒体/年份绑定、候选 TMDB ID 与已确认影视 TMDB ID 精确一致、无警告、H&R 已知、info hash、大小、最少做种数和电视剧缺集覆盖等全部硬条件，才会自动创建审批申请。IMDb-only 搜索结果仍保留给人工审阅。

搜索数据模型、job type 和 Worker 已按 `site_id` 隔离。`PtSiteRegistry` 保存工厂而非凭据；未知、未启用、payload/run 绑定不一致或候选站点不一致时失败关闭，不会回退到 AvistaZ。默认生产注册表仍只包含 `avistaz`，公开搜索创建路由当前也只放行 `avistaz`。

NexusPHP 扩展只实现了无 Secret 的声明式 `NexusPhpSiteProfile`、HTML parser、严格同源会话和本地 fixture 测试契约。Profile 描述 HTTPS origin、同源路径、分类/查询映射和 CSS selector，默认 `enabled=false`；Cookie/passkey 只能在适配器进程内存中提供。该骨架不表示任何真实国内站点可用，也不会绕过登录页、验证码或浏览器挑战。完整边界见 `docs/pt-site-profiles.md`。

## 审批工作流

```mermaid
stateDiagram-v2
  [*] --> PENDING: 创建固定候选快照
  PENDING --> PENDING: 只读预检与审计
  PENDING --> APPROVED: 人工新鲜 PASS/WARNING + 逐次确认
  PENDING --> APPROVED: 自动完整 PASS + 策略确认
  PENDING --> REJECTED: 人工拒绝
  PENDING --> EXPIRED: 有效期结束
  APPROVED --> REVOKED: 人工撤销
  APPROVED --> EXPIRED: 有效期结束
  APPROVED --> DownloadPlan: 批准时同一事务生成
  APPROVED --> EXECUTING: 种子已校验并提交预留
  EXECUTING --> CONSUMED: qB 结果已只读核验
  DownloadPlan --> ExecutionIntent: 管理员二次确认或受控自动化
  ExecutionIntent --> DownloadExecution: 人工 nonce/幂等键或自动审计绑定
```

审批只绑定一个 `torrent_candidates` 记录在申请时的完整快照。快照包含影视与候选标识、发布名、大小、info hash、季集、规格、字幕、做种、促销、H&R、匹配分数、理由、警告和有效期；不重新读取后续变化的候选。规范 JSON 使用 SHA-256 生成 `snapshot_hash`，读取和每次状态动作前都重新校验，且 `media_item_id`、`torrent_candidate_id`、申请时间和有效期必须与固定列一致。ORM 事件与 PostgreSQL trigger 禁止修改或删除固定审批字段；数据库的部分唯一索引禁止同一候选同时存在第二个 `PENDING` 或 `APPROVED` 审批。

`approval_events` 追加记录申请、预检、批准、下载计划创建、执行意图、执行请求、提交预留、拒绝、撤销、过期和消费等事件，包括前后状态、操作者、原因、快照哈希与脱敏详情。事件外键为 `RESTRICT`，ORM 与 PostgreSQL trigger 禁止 UPDATE/DELETE。阶段 4 控制面创建 `PENDING` 执行记录时不会提前消费审批；阶段 5 执行器只有在已校验 `.torrent` 后才将审批置为 `EXECUTING`，并在 qB 提交结果再次读取验证且唯一 `DownloadJob` 创建成功后置为 `CONSUMED`。

人工批准前必须满足全部条件：

- 审批仍为 `PENDING` 且未过期；
- 已完成 qBittorrent 只读预检，结果未超过 `APPROVAL_PREFLIGHT_MAX_AGE_SECONDS`；
- 总体结果只能为 `PASS` 或 `WARNING`，`UNKNOWN` 不得视为通过；
- 操作者明确确认 H&R、下载完成后继续做种、当前阶段仅创建计划；
- 下载计划目标分类已配置。

批准与 `DownloadPlan` 在同一数据库事务中创建，但不会发起网络下载。一个审批最多对应一个计划；计划保存 `approval_snapshot_hash`、`preflight_policy_fingerprint` 和规范化 `plan_hash`，每次读取或内部消费前重新校验，ORM 与 PostgreSQL trigger 禁止计划 UPDATE/DELETE。

自动批准在此基础上进一步收紧：审批和预检都必须新鲜，H&R 必须已知，12 个必需检查必须完整且总体结果只能是 `PASS`；`WARNING`、`UNKNOWN`、`BLOCKED` 一律转人工。自动批准使用策略修订中分别保存的 H&R、继续做种和仅生成计划确认，创建的计划仍不会访问外部网络。

## 下载执行控制面

阶段 4 增加纯数据库控制面，默认由 `ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE=false` 关闭。公开 API 创建执行意图和提交执行请求只允许 `admin`，所有写请求继续要求会话 CSRF；查询允许 `viewer`。阶段 6 的内部自动路径不借用登录用户，会以 `system:automation:r<revision_no>` 创建固定 `ADD_PAUSED` intent/execution，并强制绑定同事务的允许决策和策略修订。控制面本身没有 AvistaZ 取种、qBittorrent 请求或文件操作代码路径。

```text
POST /api/approval-requests/{id}/execution-intents
  -> 返回一次 nonce；数据库只保存 SHA-256
POST /api/approval-requests/{id}/execute + Idempotency-Key
  -> 只创建 PENDING download_execution
POST /api/download-executions/{id}/reconcile
  -> OUTCOME_UNKNOWN/RECONCILIATION_REQUIRED 仅进入 RECONCILIATION_PENDING
```

执行意图绑定审批快照哈希、不可变下载计划哈希、qB 目标指纹、启动模式和有效期。同一审批最多一个 `ACTIVE` 意图；nonce 只在创建响应出现一次，审批事件、执行事件、后续 GET 和数据库都不保存原文。执行请求以全局唯一的 `Idempotency-Key` SHA-256、审批唯一约束和 intent 唯一约束共同防重；相同键与相同请求返回原记录，不同请求复用同一键返回冲突。审批行使用 `SELECT ... FOR UPDATE` 串行化创建。

`download_executions` 初始状态固定为 `PENDING`，审批继续保持 `APPROVED`。`ADD_PAUSED` 是前端和 schema 默认启动模式；`START_IMMEDIATELY` 只能由管理员在人工一次性 intent 中显式选择，自动执行永远只能 `ADD_PAUSED`。`OUTCOME_UNKNOWN`、`RECONCILIATION_REQUIRED` 与 `RECONCILIATION_PENDING` 均禁止自动重试；reconcile 只记录人工对账请求，不访问 qB。

## 下载执行器

`download-execution` Compose profile 中的独立执行器只有在以下三个开关同时为真时才运行：

```text
ENABLE_DOWNLOAD_EXECUTOR=true
ENABLE_AVISTAZ_TORRENT_FETCH=true
ENABLE_QB_WRITE=true
```

它还要求 AvistaZ 实时搜索和运行时凭据、qB 运行时凭据与目标策略已配置，并持续发布与当前执行配置匹配的短 TTL readiness。处理顺序固定如下：

```text
PENDING | 到期的 RETRY_WAIT
  -> VALIDATING（数据库时间、FOR UPDATE SKIP LOCKED、租约 token 与心跳）
  -> AvistaZ 精确重搜已批准 torrent ID
  -> 获取并校验 bencode、v1/v2 hash、大小与文件数
  -> 同一事务持久化实际摘要，Approval -> EXECUTING，Execution -> SUBMITTING
  -> 按全部 hash alias 查询 qB；真正 POST 前再次执行数据库 write guard
  -> add 或确认 ALREADY_PRESENT
  -> 重新读取 qB 并核验 hash、分类、保存路径、大小；自动执行还必须观察到暂停状态
  -> Approval -> CONSUMED，Execution -> SUBMITTED | ALREADY_PRESENT
  -> 创建唯一 DownloadJob
```

执行器在每个外部请求前复验审批、状态、worker、lease token 和基于数据库时间的租约有效期。对于 `origin=AUTOMATION`，还会复验总闸、控制面/三开关、当前策略修订、执行阶段模式、H&R 和 `ADD_PAUSED`；在外部写入前失效会取消执行，写入可能已经发生后失效则进入人工对账。写入前的可重试校验错误才会进入指数退避的 `RETRY_WAIT`；`SUBMITTING` 后租约失效进入 `RECONCILIATION_REQUIRED`。qB POST 可能已经发生但无法验证时进入 `OUTCOME_UNKNOWN`。这三种对账状态的 `next_retry_at` 为空，执行器绝不自动再次 add。

## 下载监控与总结

`download-monitor` profile 要求 `ENABLE_DOWNLOAD_MONITOR=true` 和 `ENABLE_QB_READ_ONLY=true`。监控器只登录并读取 qB 任务列表，以 v1/v2 hash alias 关联 `DownloadJob`，更新 `QUEUED`、`DOWNLOADING`、`PAUSED`、`CHECKING`、`SEEDING`、`COMPLETED`、`MISSING` 或 `ERROR`，以及进度、速度、流量、Ratio、完成时间和最后观察时间。分类或大小漂移会进入 `ERROR`。

监控器不调用任何 qB mutation，也不从 qB 状态、Ratio 或做种时长推断 H&R。新任务初始为 `UNKNOWN`，已有 `AT_RISK`/`SATISFIED` 值不会被监控器覆盖。总结 API 固定返回 `job/media/approval/execution/warnings`，时间线按时间合并 approval、execution、job 三类追加式事件并再次脱敏。

## 媒体入库规划控制面

阶段 7A 由 `ENABLE_MEDIA_IMPORT_CONTROL_PLANE=false` 默认关闭，固定模式为 `PLAN_ONLY_NO_FILE_OPERATION`。它没有独立 Compose profile、媒体目录 volume、真实路径解析器、文件扫描器、inspection Worker 或文件执行器；开启总闸只允许 API/数据库记录规划状态，不增加进程文件权限。

创建请求要求下载任务进度为 100%，状态为 `SEEDING`、`COMPLETED` 或 `PAUSED`，并绑定已消费的下载审批以及 `SUBMITTED`/`ALREADY_PRESENT`、已核验且无需对账的执行终态。客户端提交的源清单、目标映射、`HARDLINK`/`COPY` 选择和根引用都是不受信提案。根引用必须是不透明内部标识，目标引用还必须位于 `MEDIA_IMPORT_TARGET_ROOT_REFS` 白名单；真实绝对路径、盘符、UNC、反斜杠、父目录、控制字符、保留设备名、大小写/Unicode 碰撞和文件/目录前缀碰撞都会在 schema 边界拒绝。单条路径、文件数和累计路径文本量均有上限。

```text
operator 创建提案
  -> PREFLIGHT_REQUIRED + 不可变 MediaImportPlan
  -> 内部受信只读 inspection 与提案逐项绑定
  -> 最新预检 PASS | WARNING -> REVIEW_REQUIRED
  -> admin 三项固定确认 + 条件式 H&R 确认 -> APPROVED_PLAN_ONLY
  -> admin 可 REJECTED，已批准计划可 REVOKED
```

inspection 只允许从内部服务边界传入，公共 API 不接受客户端声称的文件存在性、符号链接、同文件系统或可用空间结果。每次预检都追加保存并绑定计划、下载/执行摘要、info hash、源清单、目标映射和当前配置指纹；`BLOCKED`/`UNKNOWN` 会让请求回到 `PREFLIGHT_REQUIRED`，过期只要求重新预检，计划或配置绑定漂移则拒绝复用旧计划。当前版本没有 inspection 生产者，因此部署链路不会读取真实文件，也不能仅凭提案进入可批准状态。

人工批准只接受 `MEDIA_IMPORT_PREFLIGHT_MAX_AGE_SECONDS` 时间窗内的最新 `PASS`/`WARNING`，并要求分别确认仅规划、保留源文件、禁止覆盖；H&R 不是 `SATISFIED` 时还必须确认风险。旧的种子审批、执行授权或下载完成状态只证明下载链路，不构成任何媒体文件读取或写入授权。API 不提供 execute、scan、move、copy、hardlink、delete 或媒体库 writeback 端点。

## qBittorrent 只读边界

`QbittorrentReadOnlyAdapter` 由 API 的人工预检/只读查询、`automation-preflight` 和下载监控器按各自职责使用；每个进程维护独立的 SID Cookie 会话，只实现以下上游调用：

```text
POST /api/v2/auth/login
GET  /api/v2/app/version
GET  /api/v2/app/webapiVersion
GET  /api/v2/torrents/info
GET  /api/v2/torrents/files
GET  /api/v2/torrents/categories
```

API 对前端只暴露 `/api/downloaders/qbittorrent/status` 和 `/api/downloaders/qbittorrent/torrents` 两个 `GET`。登录跳转不跟随，任何非 2xx 都是失败，HTML 登录页不能当作 JSON 成功；种子和文件模型严格限制进度、ratio、时间戳、大小和优先级范围。

前端 qB Pinia store 在请求开始和失败时清空旧状态，并为每次加载递增 generation；只有当前 generation 的响应才能写回，防止迟到响应覆盖新状态。store 不启用持久化。

## 下载前预检

预检每次从固定审批快照与 qB 只读状态计算，检查：

- qB 连接、应用版本和 Web API 版本；
- qB 应用版本是否命中 `AVISTAZ_FORBIDDEN_QB_VERSIONS` 通配规则；
- 目标分类是否存在；
- 是否已有相同 info hash；
- 是否有相同发布名和大小的疑似重复任务；
- 真实目标保存路径是否位于允许路径内；
- 候选大小是否超过用户限制；
- 当前是否存在活跃做种任务；
- 候选是否有做种者；
- H&R 信息是否已知。

总体状态按严重程度折叠：`BLOCKED > UNKNOWN > WARNING > PASS`。12 个必需检查代码必须各出现一次，服务会从明细重新计算汇总状态。预检保存不暴露真实路径的策略 SHA-256 指纹；分类、保存路径策略、大小限制、禁止版本规则、计划引用/标签或有效时限变化后必须重新预检。连接失败、相同 info hash、禁止版本、分类不存在、路径越界、超限或零做种者会阻断；缺失配置或无法确认的信息为 `UNKNOWN`；疑似重复发布与没有活跃做种任务为 `WARNING`。

## 数据与审计

- `metadata_matches`：TMDB 候选、排序、评分理由、冲突和候选快照。
- `identity_reviews`：人工确认、操作者、时间和当时的候选快照；每个影视只允许一条已确认记录。
- `torrent_search_runs`：搜索状态、脱敏请求、降级策略日志、候选数和稳定错误码。
- `torrent_candidates`：脱敏候选、评分、理由与警告。
- `approval_requests`：固定候选快照、SHA-256、有效期、状态和最近一次预检。
- `approval_events`：审批状态变化和预检的追加式脱敏审计记录。
- `download_plans`：每个已批准审批至多一个不可变计划，只含内部种子引用与保存位置引用；计划本身不执行外部动作。
- `execution_intents`：一次性 nonce 摘要及审批、计划、qB 目标和启动模式绑定。
- `download_executions`：幂等执行请求、租约/fencing、实际 v1/v2 info hash、验证时间和对账状态。
- `download_execution_events`：不可变的执行状态与对账审计事件。
- `download_jobs`：提交后经 qB 只读核验创建的唯一任务，保存公开路径引用、进度、流量、Ratio 与 H&R 状态。
- `download_job_events`：任务创建与状态变化的追加式脱敏事件。
- `media_import_requests`：绑定已完成下载任务的规划状态与人工决定；同一任务同时最多一个活动请求。
- `media_import_plans`：每个请求一个不可变 `PLAN_ONLY_NO_FILE_OPERATION` 计划，固定保留源文件并禁止覆盖。
- `media_import_preflights`：追加式受信只读 inspection 与结果快照；允许过期后产生新记录，查询和批准只使用最新记录。
- `media_import_events`：创建、预检、批准、拒绝和撤销的追加式脱敏审计事件。
- `automation_policy_revisions` / `automation_policy_heads`：不可变策略哈希链与只可原子前进的全局当前版本指针。
- `automation_decisions`：绑定策略、阶段、动作、实体、结果、理由和脱敏证据哈希的不可变自动化审计记录。
- `worker_heartbeats`：普通 Worker 存活状态，以及自动预检/执行器的短 TTL 非敏感能力指纹；不保存 URL 或凭据。
- `jobs`：发现、身份解析、PT 搜索和自动只读预检共用的可恢复数据库队列；不同 Worker 按 job type 和权限边界 claim。
- `audit_events`：关键状态变更与人工操作的脱敏审计记录。

所有数据库时间写 UTC，前端按 `Asia/Shanghai` 展示。候选快照、审批事件、执行响应、下载计划与任务 API 不含 Cookie、Token、PID、密码、真实下载 URL、announce URL、tracker、passkey、lease token、真实 qB URL 或真实保存路径。`torrent_ref`、`save_path_ref` 和媒体入库根引用都是内部引用；下载计划中的 `media_destination_plan.mode` 与阶段 7A 的 `MediaImportPlan.mode` 都固定为 `PLAN_ONLY_NO_FILE_OPERATION`。即使下载执行完成或媒体入库计划获批，也没有媒体文件操作。
