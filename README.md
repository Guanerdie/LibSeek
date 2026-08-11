# UNIN 影视缺失资源检索与下载编排系统

UNIN 当前版本为 `0.8.0`。系统已实现从 NextFind 发现未入库影视、TMDB 身份与季集补全、按显式站点选择的 PT 候选搜索和人工/保守自动化决策，到受控 AvistaZ 取种、qBittorrent 提交、只读进度监控、下载总结和媒体入库规划控制面的完整代码链路。FastAPI、职责隔离的 Worker、PostgreSQL 和 Vue 管理端共同保存脱敏的状态与审计记录。

真实外部能力和阶段 7A 媒体入库规划控制面仍然默认关闭，且当前只完成 Mock、fixture、静态检查和离线迁移验收；尚未使用真实 TMDB、AvistaZ、qBittorrent、NexusPHP 站点或媒体文件完成端到端冒烟测试。只有用户明确批准具体动作、目标和影响，并在本机安全录入运行时 Secret 后，才允许启用相应外部开关。阶段 7A 固定为 `PLAN_ONLY_NO_FILE_OPERATION`：系统不扫描、移动、复制、重命名、硬链接、覆盖或删除媒体文件，也不执行媒体库写回。

## 已实现

- Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、PostgreSQL 16、httpx；前端为 Vue 3、Pinia、Vue Router、Vite 和 Vitest。
- 本地单账号认证与分级授权：签名会话 Cookie、登录前 bootstrap CSRF、所有状态变更双提交 CSRF，以及 `viewer`/`operator`/`admin` 角色层级；认证材料缺失时受保护 API fail closed。
- NextFind 只读发现：从 `https://nextfind.example/#/discover` 对应服务获取未入库条目，使用独立 Cookie、HTTPS 主机白名单、重定向复验、NDJSON 坏行隔离、分页循环检测和响应大小限制。
- TMDB 只读补全：优先使用 NextFind 提供的 TMDB ID，否则按标题、年份和类型返回最多 5 个候选；保存中英文名、IMDb 等外部 ID、已播季集矩阵和冲突。默认由人工确认；只有身份阶段设为 `AUTO_IF_ELIGIBLE`、总闸已开启且候选通过分数、差值、类型、标题/年份或 TMDB ID 精确绑定等全部硬条件时才会自动确认。
- PT 搜索与审阅：AvistaZ 支持 TMDB/IMDb/标题降级搜索、限速和稳定错误；候选展示季集覆盖、规格、音轨、字幕、活跃度、促销、H&R、评分理由和风险警告。目录中的 AvistaZ 声明为 `manual_only=false`，仅表示代码具备受保守策略约束的自动化路径，不代表自动化或实时搜索已开启。默认仍只排序供人工选择；自动选种还要求 AvistaZ 站点绑定、候选 TMDB ID 与已确认影视 TMDB ID 精确一致、无警告、H&R 已知、有效 info hash/大小、做种数和电视剧季集覆盖全部满足策略。IMDb-only 候选转人工确认。
- 多 PT 扩展底座：API、普通 Worker 和下载执行器共享不含 Secret 的 `PtSiteCatalog`；`GET /api/pt-sites/catalog` 只公开站点名称、可用性、搜索模式、媒体类型和能力标记。创建搜索必须显式提交 `site_id`，目录中的 `default_site_id` 只是界面提示，不是服务端回退。搜索任务、Worker、候选、审批、计划和执行均绑定同一站点；`PtSiteRegistry` 与 `PtExecutionRegistry` 分别选择搜索工厂和可选取种工厂，未知、禁用、未就绪、能力或绑定不一致时失败关闭。生产目录和两类生产工厂仍只接入 `avistaz`；`synthetic-two` 仅用于完全离线测试，没有任何真实 NexusPHP/国内 PT 站点经过验证。
- 不可变审批和下载计划：候选快照、预检策略与计划均以规范 JSON SHA-256 绑定，并由 ORM、约束和 PostgreSQL trigger 保护。人工批准可接受新鲜的 `PASS`/`WARNING` 并要求三项逐次确认；自动批准只接受完整、新鲜的 `PASS`，H&R 为 `UNKNOWN` 时禁止自动选种、批准和执行。
- 阶段 6 保守自动化：身份确认、种子选择、审批、执行四阶段均支持 `DISABLED`、`MANUAL`、`AUTO_IF_ELIGIBLE`；默认全为 `MANUAL`，总闸 `ENABLE_AUTOMATION_ENGINE=false`。策略只作用于 `effective_from` 之后产生的新条目，失败或不满足资格时保留人工处理；自动身份、审批和执行 actor 使用独立的 `system:automation:r<revision>` 身份。
- 自动化审计：策略版本以 compare-and-swap 发布并形成不可变 SHA-256 前向哈希链；每次自动化判断保存阶段、动作、结果、理由、脱敏证据及绑定哈希。决策读取时复验哈希与实体绑定，公共 API 不返回内部去重键。
- 独立自动预检 Worker：普通 Worker 明确不 claim `AUTOMATION_PREFLIGHT:*`；可选 `automation-preflight` profile 只持有 qBittorrent 三项运行时 Secret，以只读适配器执行预检，在每个外部请求前复验任务租约、策略、审批快照和队列决策绑定。网络请求期间不持有审批行锁，只有最终保存预检和推进审批时短暂加锁。
- 跨进程能力证明：自动预检和下载执行器分别发布绑定配置指纹的短 TTL readiness。预检指纹绑定预检策略与 qB 目标实例；执行器指纹绑定 AvistaZ/qB 目标和下载策略。只有匹配当前配置的心跳仍新鲜时才会排队预检或自动创建执行；readiness 只证明对应进程与配置就绪，不代表外部连接成功或用户已经授权真实动作。
- 阶段 4 执行控制面：一次性 nonce 只返回一次、数据库只存摘要；`Idempotency-Key`、审批、intent 和计划绑定共同防重。控制面默认关闭，创建执行记录本身不会访问外部服务。
- 阶段 5 独立执行器：租约和 fencing 保护下按不可变计划的 `site_id` 通过 `PtExecutionRegistry` 选择取种工厂，并只使用该站点声明的搜索模式重搜已批准 torrent ID；生产环境当前仍只有 AvistaZ 取种工厂。随后校验 `.torrent` 的 v1/v2 hash、大小和文件数，在任何 qB add 前持久化实际摘要；按全部 hash alias 查重，提交后再只读核验并创建唯一 `DownloadJob`。
- 未知提交结果不会自动重试：写入可能发生时进入 `OUTCOME_UNKNOWN`，已预留但无法验证时进入 `RECONCILIATION_REQUIRED`；人工对账请求只记录为 `RECONCILIATION_PENDING`，不会冒充成功或再次 add。
- 独立只读监控器：读取 qB 状态并更新任务进度、速度、上传量、Ratio 和完成时间；它不推断 H&R，未知值保持 `UNKNOWN`，已有人工或外部写入的 `AT_RISK`/`SATISFIED` 不会被覆盖。
- 阶段 7A 媒体入库规划控制面：只允许为进度 100% 且处于 `SEEDING`、`COMPLETED` 或 `PAUSED` 的已核验下载任务创建 `HARDLINK`/`COPY` 提案。客户端清单和目标映射始终是不受信提案；不可变计划、追加式只读预检和人工决定以 SHA-256 绑定，固定保留源文件并禁止覆盖目标。当前没有真实媒体路径挂载、文件检查 Worker 或文件执行器。
- Vue 管理端读取无 Secret PT 目录，创建搜索时要求选择站点，并在历史运行、候选表格和移动端候选卡中显示站点；还包含自动化策略/决策审计页、审批两步执行确认（默认 `ADD_PAUSED`）、执行列表/详情、人工对账、下载任务列表/总结，以及媒体入库规划列表、创建和详情页。各页面始终显示“仅规划，不操作媒体文件”，`viewer` 只读，不提供暂停、恢复、删除、重校验或媒体文件操作。
- Docker Compose 默认启动 API、普通 Worker、前端和 PostgreSQL；`automation-preflight`、`download-execution` 与 `download-monitor` profile 分别启用独立自动预检 Worker、下载执行器和只读监控器。API 与前端端口只绑定 `127.0.0.1`。

架构见 [docs/architecture.md](docs/architecture.md)，安全边界见 [docs/security.md](docs/security.md)，测试说明见 [docs/testing.md](docs/testing.md)，PT Profile 接入约束见 [docs/pt-site-profiles.md](docs/pt-site-profiles.md)。

## 目录

```text
backend/                 FastAPI、适配器、数据库、普通/自动预检/执行/监控 Worker、Alembic、测试
frontend/                Vue 3、Pinia、Router、审批、执行、下载总结与媒体入库规划页面、Vitest
deploy/                  Docker Secret Compose override 示例
docs/                    架构、安全、测试与 PT Profile 文档
scripts/                 Windows/Linux 启动与测试脚本
secrets/                 按 6 + 3 + 3 阶段录入的本地 Secret 目录，*.txt 已被 Git 忽略
compose.yaml             4 个默认服务及 3 个 opt-in profile 服务
.env.example             无真实密钥的配置样例，真实连接默认关闭
```

## 启动

Windows PowerShell：

```powershell
Set-Location D:\project\unin
Copy-Item .env.example .env
notepad .env
.\scripts\start.ps1 -ValidateOnly
.\scripts\start.ps1
docker compose --env-file .env -f compose.yaml ps
```

先同时修改 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中的同一个密码。本机快速启动还需在 `.env` 填写 `AUTH_LOCAL_USERNAME`、`AUTH_LOCAL_PASSWORD` 和至少 32 个字符的 `AUTH_SESSION_SIGNING_KEY`；此方式只把值传给 API。生产部署推荐让这三项在 `.env` 保持空白，改用下文 Docker Secret override 和双 Compose 文件命令。NextFind 凭据留空时系统仍可启动，但不会创建真实发现任务；认证材料缺失时健康检查仍可用，但受保护 API 会返回 `AUTH_NOT_CONFIGURED`。不要把任何密码、签名密钥、PID、Cookie、Token 或 TMDB Key 发送到聊天或提交到版本库。

启动脚本将 `config`、`up` 和 `ps` 固定到项目根目录的 `.env` 与 `compose.yaml`；`-ValidateOnly` 只执行门禁和 Compose 配置解析，不启动容器。为防止 Compose 优先采用父进程值，脚本拒绝所有已导出的项目配置变量、`*_FILE` Secret 来源和 `COMPOSE_*` 控制变量，即使它们的值为空也会拒绝。请使用 `Remove-Item Env:<NAME>` 真正移除 PowerShell 进程变量；`.env` 只接受直接字面值，不允许 `$VAR` 或 `${VAR}` 二次插值，也不允许非空 `COMPOSE_*` 键。

Linux / Docker：

```bash
cd /path/to/unin
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
sh ./scripts/start.sh --validate-only
sh ./scripts/start.sh
docker compose --env-file .env -f compose.yaml ps
```

Linux 中同样必须用 `unset NAME` 移除父进程配置变量；`export NAME=` 仍会被门禁拒绝。

验收：打开 `http://127.0.0.1:8080`；API 文档位于 `http://127.0.0.1:8000/api/docs`；`docker compose ps` 中四个服务应为 healthy。

## 本地登录与权限

系统只配置一个本地账号。`AUTH_LOCAL_ROLE` 决定该账号的最高权限：`viewer` 只能读取影视、候选、审批、执行、下载任务、媒体入库计划、自动化策略/决策和 qB 只读状态；`operator` 继承读取权限，并可创建发现/解析/搜索/审批申请、执行下载预检、确认身份、拒绝下载审批和创建媒体入库规划请求；`admin` 继承前两级权限，并可批准/撤销下载审批、发布自动化策略修订、创建执行意图、提交执行请求、请求人工对账，以及批准、拒绝或撤销媒体入库规划请求。服务端始终以登录会话中的用户名写人工操作 actor，自动动作使用 `system:automation` 命名空间，客户端伪造的操作者字段不会生效。

认证接口：

- `GET /api/auth/csrf`：登录前获取 bootstrap CSRF；响应同时设置可由前端读取的 `unin_csrf` Cookie。
- `POST /api/auth/login`：提交用户名和密码，同时让 `unin_csrf` Cookie 与 `X-CSRF-Token` 请求头携带相同 token。
- `GET /api/auth/me`：读取当前账号与角色。
- `POST /api/auth/logout`：必须携带当前会话 CSRF，并清除认证 Cookie。

登录成功后，`unin_session` 为 `HttpOnly`，两个 Cookie 均为 `SameSite=Strict`、`Path=/api`；浏览器端不得把会话或密码写入 Web Storage。所有状态变更请求都必须同时携带 CSRF Cookie 和同值的 `X-CSRF-Token`。`AUTH_SESSION_TTL_SECONDS` 默认 28800 秒，登录前 token 的 `AUTH_BOOTSTRAP_CSRF_TTL_SECONDS` 默认 600 秒。本机 HTTP 保持 `AUTH_COOKIE_SECURE=false`；经 HTTPS 反向代理的生产部署必须设为 `true`。

三个认证材料应通过下文 Docker Secret 提供：`auth_local_username`、`auth_local_password`、`auth_session_signing_key`。签名密钥至少 32 个字符且应为独立高熵随机值。任一材料缺失、文件不可读或签名密钥过短时，系统 fail closed，不允许匿名降级。

## 工作流 API

- `GET /api/pt-sites/catalog`
- `POST /api/media/{id}/resolve`
- `GET /api/media/{id}/metadata-candidates`
- `POST /api/media/{id}/identity-confirmations`
- `POST /api/media/{id}/torrent-searches`
- `GET /api/media/{id}/torrent-searches`
- `GET /api/torrent-searches/{id}`
- `GET /api/torrent-searches/{id}/candidates`

PT 目录接口允许 `viewer` 读取且只有 `GET`，响应不含 URL、Cookie、passkey、用户名、密码或 Token。`POST /api/media/{id}/torrent-searches` 的请求体必须包含目录声明的、当前可搜索且支持该媒体类型的 `site_id`；省略时返回 schema 校验错误，未知、禁用或运行时未就绪的站点会在创建 run/job/audit 记录前失败关闭。生产目录的 `default_site_id=avistaz` 不会替客户端补写该字段。

原有健康检查、系统状态、发现任务、影视列表和适配器 API 保持兼容。所有分页有边界，错误包含中文 `message` 与机器可读 `error_code`。

## 审批与只读 qB API

- `POST /api/candidates/{id}/approval-requests`
- `GET /api/approval-requests`
- `GET /api/approval-requests/{id}`
- `POST /api/approval-requests/{id}/approve`
- `POST /api/approval-requests/{id}/reject`
- `POST /api/approval-requests/{id}/revoke`
- `POST /api/approval-requests/{id}/preflight`
- `GET /api/approval-requests/{id}/download-plan`
- `GET /api/downloaders/qbittorrent/status`
- `GET /api/downloaders/qbittorrent/torrents`

qBittorrent 公开命名空间始终只有以上两个 `GET`。系统没有直接映射 qB 的 add/start/resume/pause/delete/recheck/download 业务路由；真实 add 只能由独立执行器在数据库闸门、三开关、租约 fencing 和写前复验全部通过后内部调用。

## 自动化策略与决策 API

- `GET /api/automation/policy`
- `GET /api/automation/policy-revisions`
- `POST /api/automation/policy-revisions`
- `GET /api/automation/decisions`
- `GET /api/automation/decisions/{id}`

策略和决策查询允许 `viewer`，发布新修订只允许 `admin` 且必须携带当前 `base_revision_no`。四阶段默认均为 `MANUAL`；`DISABLED` 会由后端阻止该阶段新的人工和自动正向动作；`AUTO_IF_ELIGIBLE` 只在总闸开启且全部硬条件满足时行动，否则记录 `MANUAL_REQUIRED`、`BLOCKED`、`STALE` 或 `NOOP` 等审计结果并允许人工接管。策略不扫描、不追溯处理 `effective_from` 之前的积压条目。

默认资格阈值为身份最低分 `0.5`、身份前两名最小差值 `0.1`、种子最低分 `0.75`、种子前两名最小差值 `0.1`、最少做种数 `1`。阈值只是必要条件，不会绕过类型/年份/TMDB ID/季集覆盖、候选警告、H&R、预检或执行能力闸门。启用自动审批必须确认 H&R、继续做种和“仅生成计划”；启用自动执行必须另行确认 H&R、继续做种和“只允许 `ADD_PAUSED`”，确认项按策略修订不可变保存。执行阶段为 `AUTO_IF_ELIGIBLE` 时，人工或自动批准后都会立即评估独立执行资格；只有总闸、执行策略、能力心跳和全部资格同时通过才会创建 `ADD_PAUSED` 执行记录。

## 执行控制面与下载任务 API

- `POST /api/approval-requests/{id}/execution-intents`
- `POST /api/approval-requests/{id}/execute`，必须携带 `Idempotency-Key`
- `GET /api/approval-requests/{id}/download-execution`
- `GET /api/download-executions`
- `GET /api/download-executions/{id}`
- `POST /api/download-executions/{id}/reconcile`
- `GET /api/download-jobs`
- `GET /api/download-jobs/{id}`
- `GET /api/download-jobs/{id}/timeline`
- `GET /api/download-jobs/{id}/summary`

执行与下载任务列表使用有界分页和稳定排序。API 不返回 intent nonce 的持久副本、幂等摘要、lease token、Worker ID、真实 qB URL、真实保存路径或 Secret；内部错误只暴露稳定 `error_code` 和固定提示。`reconcile` 只登记人工对账请求，不进行 qB 外部调用，也不会把不确定结果改成成功。

## 媒体入库规划 API

- `POST /api/media-import-requests`：`operator` 创建不受信源清单和目标映射提案。
- `GET /api/media-import-requests`：`viewer` 分页查询。
- `GET /api/media-import-requests/{id}`：`viewer` 查看固定计划、最新只读预检和追加式事件。
- `POST /api/media-import-requests/{id}/approve`：`admin` 在新鲜预检通过后确认仅规划、保留源文件和禁止覆盖；H&R 非 `SATISFIED` 时还要单独确认。
- `POST /api/media-import-requests/{id}/reject`：`admin` 拒绝规划请求。
- `POST /api/media-import-requests/{id}/revoke`：`admin` 撤销已批准的规划请求。

这一命名空间没有 `preflight`、`execute`、`scan`、`move`、`copy`、`hardlink`、`delete` 或媒体库 `writeback` 端点。创建请求只保存 `PLAN_ONLY_NO_FILE_OPERATION` 计划；旧的种子审批和 qB 下载授权不能解释为媒体文件操作授权。批准还要求内部受信的只读 inspection 与客户端提案逐项一致，并使用 30 至 3600 秒范围内的新鲜 `PASS`/`WARNING` 预检。阶段 7A 没有接入真实 inspection 生产者，因此常规部署只能创建和审阅计划，不能凭客户端输入自行产生可批准的预检，更不会执行文件操作。

## 外部能力与执行开关

默认保持：

```dotenv
ENABLE_TMDB_LIVE=false
ENABLE_AVISTAZ_LIVE_SEARCH=false
ENABLE_QB_READ_ONLY=false
ENABLE_AUTOMATION_ENGINE=false
ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE=false
ENABLE_DOWNLOAD_EXECUTOR=false
ENABLE_AVISTAZ_TORRENT_FETCH=false
ENABLE_QB_WRITE=false
ENABLE_DOWNLOAD_MONITOR=false
ENABLE_MEDIA_IMPORT_CONTROL_PLANE=false
```

`ENABLE_AUTOMATION_ENGINE` 是四阶段 `AUTO_IF_ELIGIBLE` 的进程级总闸；它不会覆盖阶段策略，也不会替代 TMDB、AvistaZ、qB 只读、控制面或执行器各自的能力开关。仅发布自动化策略不会访问外部服务或处理旧积压。`AUTOMATION_PREFLIGHT_READY_TTL_SECONDS` 与 `DOWNLOAD_EXECUTOR_READY_TTL_SECONDS` 默认均为 90 秒；过期或配置指纹不匹配的 readiness 会阻止新的自动预检/执行并回退人工。

`ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE` 只允许管理员创建 intent 和 `PENDING` 执行记录。独立执行器要求 `ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 三个开关同时为 `true`；少一个即拒绝启动。实际运行还需要显式启用 AvistaZ 搜索、提供 AvistaZ/qB 运行时 Secret 和 qB 目标策略。自动执行即使满足全部资格也只能创建 `ADD_PAUSED` 任务，不允许自动选择 `START_IMMEDIATELY`。任何真实验证前都必须先向用户列出将访问的 URL、将读取或写入的对象和可能影响，并取得明确授权。

`ENABLE_DOWNLOAD_MONITOR=true` 还必须配合 `ENABLE_QB_READ_ONLY=true`，并要求 `QB_TARGET_SAVE_PATH` 位于非空的 `QB_ALLOWED_SAVE_PATHS` 白名单内。监控器只调用 qB 登录和只读 GET，不调用暂停、恢复、删除或重校验。

`ENABLE_MEDIA_IMPORT_CONTROL_PLANE=true` 只开放数据库中的媒体入库规划状态流。`MEDIA_IMPORT_TARGET_ROOT_REFS` 是逗号分隔的不透明目标根引用白名单，不得填写真实文件系统路径；留空时创建请求失败关闭。`MEDIA_IMPORT_PREFLIGHT_MAX_AGE_SECONDS` 默认 300 秒且只接受 30 至 3600 秒。配置不挂载媒体目录、不创建只读检查器，也不授予任何文件读写权限。

推荐按实际验证阶段叠加 Docker Secret override，不必在第一轮就创建全部 12 个文件：

| override | 本地文件 | 用途 |
|---|---:|---|
| `deploy/compose.secrets.discovery.yaml.example` | 6 | 本地认证 3 个、NextFind 2 个、TMDB 1 个；第一轮只读发现只使用这一层 |
| `deploy/compose.secrets.avistaz.yaml.example` | 3 | AvistaZ username/password/PID；获得 AvistaZ 访问授权后再叠加 |
| `deploy/compose.secrets.qb.yaml.example` | 3 | qBittorrent base URL/username/password；获得目标 qB 访问授权后再叠加 |

三个文件是可叠加的增量 override；只会清空并替换本层对应的明文环境变量，不会自动开启实时开关或可选 profile。现有 [全量 12 Secret override](deploy/compose.secrets.yaml.example) 保持兼容，已安全录入全部 12 个文件的部署可以继续使用；不要把全量文件和三份分层文件重复叠加。未启用阶段的凭据应继续在 `.env` 中保持空白。

以下 PowerShell 片段使用隐藏输入，不会把值打印到终端；它会在本机 `D:\project\unin\secrets` 创建明文 Secret 文件，因此该目录必须仅允许当前用户读取，且不得同步或提交。不要把任何实际值粘贴到聊天。`auth_session_signing_key.txt` 必须至少 32 个字符，建议由本机密码管理器或安全随机生成器创建，不要复用登录密码。第一轮只执行 discovery 分组；AvistaZ 和 qB 分组等取得对应授权时再执行。

```powershell
Set-Location D:\project\unin
New-Item -ItemType Directory -Force .\secrets | Out-Null

function Write-LocalSecret([string]$Path, [string]$Prompt) {
    $secureValue = Read-Host $Prompt -AsSecureString
    $secretPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureValue)
    try {
        $plainValue = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPtr)
        [IO.File]::WriteAllText(
            (Join-Path (Get-Location) $Path),
            $plainValue,
            [Text.UTF8Encoding]::new($false)
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPtr)
        Remove-Variable plainValue -ErrorAction SilentlyContinue
    }
}
```

第一轮在同一个 PowerShell 会话中只执行以下 discovery 分组，然后停止：

```powershell
Write-LocalSecret '.\secrets\auth_local_username.txt' 'UNIN local username'
Write-LocalSecret '.\secrets\auth_local_password.txt' 'UNIN local password'
Write-LocalSecret '.\secrets\auth_session_signing_key.txt' 'UNIN session signing key (minimum 32 characters)'
Write-LocalSecret '.\secrets\nextfind_username.txt' 'NextFind username'
Write-LocalSecret '.\secrets\nextfind_password.txt' 'NextFind password'
Write-LocalSecret '.\secrets\tmdb_access_token.txt' 'TMDB Bearer Access Token'
icacls .\secrets\*.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

AvistaZ 阶段取得授权后，在仍定义有 `Write-LocalSecret` 的 PowerShell 会话中执行；若已打开新会话，先重新执行上面的函数定义，不要重复录入 discovery 文件：

```powershell
Write-LocalSecret '.\secrets\avistaz_username.txt' 'AvistaZ username'
Write-LocalSecret '.\secrets\avistaz_password.txt' 'AvistaZ password'
Write-LocalSecret '.\secrets\avistaz_pid.txt' 'AvistaZ PID'
icacls .\secrets\*.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

qBittorrent 阶段取得指定实例访问授权后同理执行：

```powershell
Write-LocalSecret '.\secrets\qb_base_url.txt' 'qBittorrent base URL'
Write-LocalSecret '.\secrets\qb_username.txt' 'qBittorrent username'
Write-LocalSecret '.\secrets\qb_password.txt' 'qBittorrent password'
icacls .\secrets\*.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

文件名按 override 分组如下；三组并集与全量 override 的 12 个文件完全一致：

```text
# deploy/compose.secrets.discovery.yaml.example（第一轮 6 个）
secrets/auth_local_username.txt
secrets/auth_local_password.txt
secrets/auth_session_signing_key.txt
secrets/nextfind_username.txt
secrets/nextfind_password.txt
secrets/tmdb_access_token.txt

# deploy/compose.secrets.avistaz.yaml.example（后续 3 个）
secrets/avistaz_username.txt
secrets/avistaz_password.txt
secrets/avistaz_pid.txt

# deploy/compose.secrets.qb.yaml.example（后续 3 个）
secrets/qb_base_url.txt
secrets/qb_username.txt
secrets/qb_password.txt
```

Secret 可见范围随已叠加的层增加，但服务边界固定：discovery 层只让 API 读取 6 个、普通 Worker 读取 NextFind/TMDB 3 个；AvistaZ 层只向 API、普通 Worker 和下载执行器各增加 AvistaZ 3 个；qB 层只向 API、`automation-preflight`、下载执行器和只读监控器各增加 qB 3 个；前端始终为 0。三层全部叠加后，权限矩阵与原全量 override 相同：API=12、普通 Worker=6、`automation-preflight`=3、下载执行器=6、监控器=3、前端=0。非 Secret 的主机白名单、目标引用和安全策略仍按职责传入对应服务。

认证与 qB 的非密钥策略仍在本机 `.env` 中配置。认证策略包括 `AUTH_LOCAL_ROLE`、会话/CSRF TTL 和 `AUTH_COOKIE_SECURE`；不要在使用 Secret override 时把三个认证值同时写入 `.env`。`QB_ALLOWED_HOSTS` 必须是 `qb_base_url.txt` 中 URL 的精确主机名；默认只允许 HTTPS。仅在明确接受受信内网明文 HTTP 风险时设置 `QB_ALLOW_INSECURE_HTTP=true`。下载计划需要配置 `QB_TARGET_CATEGORY`、后端预检使用的 `QB_TARGET_SAVE_PATH`、逗号分隔的 `QB_ALLOWED_SAVE_PATHS`、可公开显示的 `QB_SAVE_PATH_REF`、`MAX_CANDIDATE_SIZE_BYTES` 和 `AVISTAZ_FORBIDDEN_QB_VERSIONS`。真实路径只参与后端预检，API 与下载计划只返回 `QB_SAVE_PATH_REF`。

阶段 7A 的目标根配置与 qB 保存路径相互独立。用户只在本机 `.env` 录入经过约定的不透明 `MEDIA_IMPORT_TARGET_ROOT_REFS`；通过页面或 API 提交的也只能是源/目标相对路径和文件大小提案。现阶段不需要录入真实媒体根路径，也不要给 Compose 添加媒体目录 volume。只有后续引入受信只读 inspection 时，才需要先向用户说明将读取的实际下载根和目标根、挂载模式及检查范围并取得确认；引入复制/硬链接执行器还需要新的独立阶段和文件写入授权。

第一轮只读发现只需 6 个 discovery Secret，不声明或挂载 AvistaZ/qB Secret，也不启用自动预检、下载执行或监控 profile：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example up -d --build
```

获得 AvistaZ 只读搜索授权并录入对应 3 个 Secret 后，在 discovery 层之后追加：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example -f deploy\compose.secrets.avistaz.yaml.example up -d --build
```

若本机已经安全录入全部 12 个 Secret，原全量命令仍受支持：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example up -d --build
```

自动审批的 qB 只读预检使用独立 `automation-preflight` profile。只有在用户明确批准目标 qB 实例的只读登录/查询、`ENABLE_AUTOMATION_ENGINE=true`、`ENABLE_QB_READ_ONLY=true`，并确认当前自动化策略后才允许启动：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example -f deploy\compose.secrets.qb.yaml.example --profile automation-preflight up -d --build
```

该服务只挂载 qB 三项 Secret，不持有 NextFind、TMDB 或 AvistaZ Secret，也不调用 qB mutation。它发布绑定 `preflight_policy_fingerprint` 与 `QB_TARGET_INSTANCE_REF` 的短 TTL readiness；系统只有观察到与当前配置匹配的新鲜心跳才会排队自动预检。Worker 随后只对固定审批快照执行登录和只读 GET；完整 `PASS` 才可能自动生成计划，其余结果保留人工处理。

仅在真实执行得到单独授权、三开关已显式启用且目标审批已再次确认后，才允许加入 `download-execution` profile：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example -f deploy\compose.secrets.avistaz.yaml.example -f deploy\compose.secrets.qb.yaml.example --profile download-execution up -d --build
```

这会允许执行器重新搜索已批准的 AvistaZ torrent ID、访问对应 download URL、读取并校验 `.torrent`，并在通过所有闸门后最多向 `qb_base_url.txt` 指定的 qBittorrent 提交一次 add。执行器只有在三开关、AvistaZ/qB 凭据存在性、目标主机与保存路径策略通过时才发布绑定执行配置指纹的短 TTL readiness。自动执行必须观察到匹配心跳，启动模式固定为 `ADD_PAUSED`，提交后还会验证任务确实保持暂停；只有管理员在人工一次性 intent 中显式选择 `START_IMMEDIATELY` 才会立即启动。

需要验证完整自动链路时，必须分别取得 qB 只读预检和真实 qB add 授权，再同时启用两个 profile；仅启用 `automation-preflight` 不授予或启动下载执行器：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example -f deploy\compose.secrets.avistaz.yaml.example -f deploy\compose.secrets.qb.yaml.example --profile automation-preflight --profile download-execution up -d --build
```

只读监控另用 `download-monitor` profile，并要求 `ENABLE_DOWNLOAD_MONITOR=true` 与 `ENABLE_QB_READ_ONLY=true`：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.discovery.yaml.example -f deploy\compose.secrets.qb.yaml.example --profile download-monitor up -d --build
```

TMDB 只读测试会连接 `api.themoviedb.org`；AvistaZ 搜索测试会连接 `avistaz.to` 的认证与搜索端点；qB 只读测试会连接 `qb_base_url.txt` 指定且被 `QB_ALLOWED_HOSTS` 精确允许的实例。当前均未执行真实冒烟测试。执行器测试与只读测试不是同一授权范围：批准搜索或 qB 只读测试，不等于批准访问 AvistaZ download URL 或向 qB add。

媒体入库规划的离线验收只使用合成下载任务、文件清单、目标映射和 inspection 快照，不读取真实文件。需要验证真实数据时，应先由用户在本地完成相应下载链路并选定单个 `DownloadJob`，再确认将提交的相对路径清单、大小、目标根引用和映射；这只授权创建提案。阶段 7A 没有真实 inspection/执行能力，因此不得要求用户录入真实根路径，也不得据此读取或修改媒体文件。

## 明确未实现

- qBittorrent 暂停、恢复、删除、重校验、文件优先级修改、做种控制或 H&R 自动判定。执行器唯一允许的 qB 写操作是受控 add。
- 自动解决 `OUTCOME_UNKNOWN`/`RECONCILIATION_REQUIRED`；当前只能人工登记对账请求，不能自动再次 add。
- 下载完成后的媒体整理执行：真实路径 inspection、重命名、移动、复制、硬链接、删除、扫描入库或 NextFind/媒体库写回。阶段 7A 仅保存不可执行计划与合成只读预检记录。
- 任何已验证可用的真实 NexusPHP 站点 Profile、浏览器 DOM 抓取或验证码/反爬绕过；
  当前仅有默认关闭的声明式适配骨架与本地 HTML fixture 契约，详见
  [`docs/pt-site-profiles.md`](docs/pt-site-profiles.md)。
