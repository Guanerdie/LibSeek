# UNIN 影视缺失资源检索与下载编排系统

UNIN 当前版本为 `0.6.0`。系统已实现从 NextFind 发现未入库影视、TMDB 身份与季集补全、PT 候选搜索和人工/保守自动化决策，到受控 AvistaZ 取种、qBittorrent 提交、只读进度监控与下载总结页的完整代码链路。FastAPI、职责隔离的 Worker、PostgreSQL 和 Vue 管理端共同保存脱敏的状态与审计记录。

真实外部能力仍然默认关闭，且当前只完成 Mock、fixture、静态检查和离线迁移验收；尚未使用真实 TMDB、AvistaZ、qBittorrent 或 NexusPHP 站点完成端到端冒烟测试。只有用户明确批准具体动作、目标和影响，并在本机安全录入运行时 Secret 后，才允许启用相应开关。系统不移动、复制、重命名、硬链接或删除媒体文件，也不执行媒体库写入。

## 已实现

- Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、PostgreSQL 16、httpx；前端为 Vue 3、Pinia、Vue Router、Vite 和 Vitest。
- 本地单账号认证与分级授权：签名会话 Cookie、登录前 bootstrap CSRF、所有状态变更双提交 CSRF，以及 `viewer`/`operator`/`admin` 角色层级；认证材料缺失时受保护 API fail closed。
- NextFind 只读发现：从 `https://nextfind.example/#/discover` 对应服务获取未入库条目，使用独立 Cookie、HTTPS 主机白名单、重定向复验、NDJSON 坏行隔离、分页循环检测和响应大小限制。
- TMDB 只读补全：优先使用 NextFind 提供的 TMDB ID，否则按标题、年份和类型返回最多 5 个候选；保存中英文名、IMDb 等外部 ID、已播季集矩阵和冲突。默认由人工确认；只有身份阶段设为 `AUTO_IF_ELIGIBLE`、总闸已开启且候选通过分数、差值、类型、标题/年份或 TMDB ID 精确绑定等全部硬条件时才会自动确认。
- PT 搜索与审阅：AvistaZ 支持 TMDB/IMDb/标题降级搜索、限速和稳定错误；候选展示季集覆盖、规格、音轨、字幕、活跃度、促销、H&R、评分理由和风险警告。默认只排序供人工选择；自动选种还要求 AvistaZ 站点绑定、候选 TMDB ID 与已确认影视 TMDB ID 精确一致、无警告、H&R 已知、有效 info hash/大小、做种数和电视剧季集覆盖全部满足策略。IMDb-only 候选转人工确认。
- 多 PT 扩展底座：搜索任务、Worker 和候选均绑定 `site_id`，注册表未知或禁用站点时失败关闭，不回退到其他站点。声明式 `NexusPhpSiteProfile`、同源 HTTP 会话和脱敏 HTML fixture 契约已实现，但默认注册表仍只有 `avistaz`，公开创建搜索的 API 当前也只允许 `avistaz`；没有任何真实 NexusPHP 站点经过验证。
- 不可变审批和下载计划：候选快照、预检策略与计划均以规范 JSON SHA-256 绑定，并由 ORM、约束和 PostgreSQL trigger 保护。人工批准可接受新鲜的 `PASS`/`WARNING` 并要求三项逐次确认；自动批准只接受完整、新鲜的 `PASS`，H&R 为 `UNKNOWN` 时禁止自动选种、批准和执行。
- 阶段 6 保守自动化：身份确认、种子选择、审批、执行四阶段均支持 `DISABLED`、`MANUAL`、`AUTO_IF_ELIGIBLE`；默认全为 `MANUAL`，总闸 `ENABLE_AUTOMATION_ENGINE=false`。策略只作用于 `effective_from` 之后产生的新条目，失败或不满足资格时保留人工处理；自动身份、审批和执行 actor 使用独立的 `system:automation:r<revision>` 身份。
- 自动化审计：策略版本以 compare-and-swap 发布并形成不可变 SHA-256 前向哈希链；每次自动化判断保存阶段、动作、结果、理由、脱敏证据及绑定哈希。决策读取时复验哈希与实体绑定，公共 API 不返回内部去重键。
- 独立自动预检 Worker：普通 Worker 明确不 claim `AUTOMATION_PREFLIGHT:*`；可选 `automation-preflight` profile 只持有 qBittorrent 三项运行时 Secret，以只读适配器执行预检，在每个外部请求前复验任务租约、策略、审批快照和队列决策绑定。网络请求期间不持有审批行锁，只有最终保存预检和推进审批时短暂加锁。
- 跨进程能力证明：自动预检和下载执行器分别发布绑定配置指纹的短 TTL readiness。预检指纹绑定预检策略与 qB 目标实例；执行器指纹绑定 AvistaZ/qB 目标和下载策略。只有匹配当前配置的心跳仍新鲜时才会排队预检或自动创建执行；readiness 只证明对应进程与配置就绪，不代表外部连接成功或用户已经授权真实动作。
- 阶段 4 执行控制面：一次性 nonce 只返回一次、数据库只存摘要；`Idempotency-Key`、审批、intent 和计划绑定共同防重。控制面默认关闭，创建执行记录本身不会访问外部服务。
- 阶段 5 独立执行器：租约和 fencing 保护下重新搜索已批准的 AvistaZ torrent ID，校验 `.torrent` 的 v1/v2 hash、大小和文件数，在任何 qB add 前持久化实际摘要；按全部 hash alias 查重，提交后再只读核验并创建唯一 `DownloadJob`。
- 未知提交结果不会自动重试：写入可能发生时进入 `OUTCOME_UNKNOWN`，已预留但无法验证时进入 `RECONCILIATION_REQUIRED`；人工对账请求只记录为 `RECONCILIATION_PENDING`，不会冒充成功或再次 add。
- 独立只读监控器：读取 qB 状态并更新任务进度、速度、上传量、Ratio 和完成时间；它不推断 H&R，未知值保持 `UNKNOWN`，已有人工或外部写入的 `AT_RISK`/`SATISFIED` 不会被覆盖。
- Vue 管理端包含自动化策略/决策审计页、审批两步执行确认（默认 `ADD_PAUSED`）、执行列表/详情、人工对账、下载任务列表和总结页；`viewer` 只读，不提供暂停、恢复、删除、重校验或媒体文件操作。
- Docker Compose 默认启动 API、普通 Worker、前端和 PostgreSQL；`automation-preflight`、`download-execution` 与 `download-monitor` profile 分别启用独立自动预检 Worker、下载执行器和只读监控器。API 与前端端口只绑定 `127.0.0.1`。

架构见 [docs/architecture.md](docs/architecture.md)，安全边界见 [docs/security.md](docs/security.md)，测试说明见 [docs/testing.md](docs/testing.md)，PT Profile 接入约束见 [docs/pt-site-profiles.md](docs/pt-site-profiles.md)。

## 目录

```text
backend/                 FastAPI、适配器、数据库、普通/自动预检/执行/监控 Worker、Alembic、测试
frontend/                Vue 3、Pinia、Router、审批、执行与下载总结页面、Vitest
deploy/                  Docker Secret Compose override 示例
docs/                    架构、安全、测试与 PT Profile 文档
scripts/                 Windows/Linux 启动与测试脚本
secrets/                 12 个本地 Secret 文件目录，*.txt 已被 Git 忽略
compose.yaml             4 个默认服务及 2 个 opt-in profile 服务
.env.example             无真实密钥的配置样例，真实连接默认关闭
```

## 启动

Windows PowerShell：

```powershell
Set-Location D:\project\unin
Copy-Item .env.example .env
notepad .env
.\scripts\start.ps1
docker compose ps
```

先同时修改 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中的同一个密码。本机快速启动还需在 `.env` 填写 `AUTH_LOCAL_USERNAME`、`AUTH_LOCAL_PASSWORD` 和至少 32 个字符的 `AUTH_SESSION_SIGNING_KEY`；此方式只把值传给 API。生产部署推荐让这三项在 `.env` 保持空白，改用下文 Docker Secret override 和双 Compose 文件命令。NextFind 凭据留空时系统仍可启动，但不会创建真实发现任务；认证材料缺失时健康检查仍可用，但受保护 API 会返回 `AUTH_NOT_CONFIGURED`。不要把任何密码、签名密钥、PID、Cookie、Token 或 TMDB Key 发送到聊天或提交到版本库。

Linux / Docker：

```bash
cd /path/to/unin
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
sh ./scripts/start.sh
docker compose ps
```

验收：打开 `http://127.0.0.1:8080`；API 文档位于 `http://127.0.0.1:8000/api/docs`；`docker compose ps` 中四个服务应为 healthy。

## 本地登录与权限

系统只配置一个本地账号。`AUTH_LOCAL_ROLE` 决定该账号的最高权限：`viewer` 只能读取影视、候选、审批、执行、下载任务、自动化策略/决策和 qB 只读状态；`operator` 继承读取权限，并可创建发现/解析/搜索/审批申请、执行预检、确认身份和拒绝审批；`admin` 继承前两级权限，并可批准/撤销审批、发布自动化策略修订、创建执行意图、提交执行请求和请求人工对账。服务端始终以登录会话中的用户名写人工操作 actor，自动动作使用 `system:automation` 命名空间，客户端伪造的操作者字段不会生效。

认证接口：

- `GET /api/auth/csrf`：登录前获取 bootstrap CSRF；响应同时设置可由前端读取的 `unin_csrf` Cookie。
- `POST /api/auth/login`：提交用户名和密码，同时让 `unin_csrf` Cookie 与 `X-CSRF-Token` 请求头携带相同 token。
- `GET /api/auth/me`：读取当前账号与角色。
- `POST /api/auth/logout`：必须携带当前会话 CSRF，并清除认证 Cookie。

登录成功后，`unin_session` 为 `HttpOnly`，两个 Cookie 均为 `SameSite=Strict`、`Path=/api`；浏览器端不得把会话或密码写入 Web Storage。所有状态变更请求都必须同时携带 CSRF Cookie 和同值的 `X-CSRF-Token`。`AUTH_SESSION_TTL_SECONDS` 默认 28800 秒，登录前 token 的 `AUTH_BOOTSTRAP_CSRF_TTL_SECONDS` 默认 600 秒。本机 HTTP 保持 `AUTH_COOKIE_SECURE=false`；经 HTTPS 反向代理的生产部署必须设为 `true`。

三个认证材料应通过下文 Docker Secret 提供：`auth_local_username`、`auth_local_password`、`auth_session_signing_key`。签名密钥至少 32 个字符且应为独立高熵随机值。任一材料缺失、文件不可读或签名密钥过短时，系统 fail closed，不允许匿名降级。

## 工作流 API

- `POST /api/media/{id}/resolve`
- `GET /api/media/{id}/metadata-candidates`
- `POST /api/media/{id}/identity-confirmations`
- `POST /api/media/{id}/torrent-searches`
- `GET /api/media/{id}/torrent-searches`
- `GET /api/torrent-searches/{id}`
- `GET /api/torrent-searches/{id}/candidates`

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

默认资格阈值为身份最低分 `0.5`、身份前两名最小差值 `0.1`、种子最低分 `0.75`、种子前两名最小差值 `0.1`、最少做种数 `1`。阈值只是必要条件，不会绕过类型/年份/TMDB ID/季集覆盖、候选警告、H&R、预检或执行能力闸门。启用自动审批必须确认 H&R、继续做种和“仅生成计划”；启用自动执行必须另行确认 H&R、继续做种和“只允许 `ADD_PAUSED`”，确认项按策略修订不可变保存。

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
```

`ENABLE_AUTOMATION_ENGINE` 是四阶段 `AUTO_IF_ELIGIBLE` 的进程级总闸；它不会覆盖阶段策略，也不会替代 TMDB、AvistaZ、qB 只读、控制面或执行器各自的能力开关。仅发布自动化策略不会访问外部服务或处理旧积压。`AUTOMATION_PREFLIGHT_READY_TTL_SECONDS` 与 `DOWNLOAD_EXECUTOR_READY_TTL_SECONDS` 默认均为 90 秒；过期或配置指纹不匹配的 readiness 会阻止新的自动预检/执行并回退人工。

`ENABLE_DOWNLOAD_EXECUTION_CONTROL_PLANE` 只允许管理员创建 intent 和 `PENDING` 执行记录。独立执行器要求 `ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 三个开关同时为 `true`；少一个即拒绝启动。实际运行还需要显式启用 AvistaZ 搜索、提供 AvistaZ/qB 运行时 Secret 和 qB 目标策略。自动执行即使满足全部资格也只能创建 `ADD_PAUSED` 任务，不允许自动选择 `START_IMMEDIATELY`。任何真实验证前都必须先向用户列出将访问的 URL、将读取或写入的对象和可能影响，并取得明确授权。

`ENABLE_DOWNLOAD_MONITOR=true` 还必须配合 `ENABLE_QB_READ_ONLY=true`。监控器只调用 qB 登录和只读 GET，不调用暂停、恢复、删除或重校验。

推荐使用项目内的本地 Secret 文件和 Compose override。共需 12 个文件：本地认证 3 个、NextFind 2 个、TMDB 1 个、AvistaZ 3 个、qBittorrent 3 个。以下 PowerShell 片段使用隐藏输入，不会把值打印到终端；它会在本机 `D:\project\unin\secrets` 创建明文 Secret 文件，因此该目录必须仅允许当前用户读取，且不得同步或提交。不要把任何实际值粘贴到聊天。`auth_session_signing_key.txt` 必须至少 32 个字符，建议由本机密码管理器或安全随机生成器创建，不要复用登录密码。

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

Write-LocalSecret '.\secrets\auth_local_username.txt' 'UNIN local username'
Write-LocalSecret '.\secrets\auth_local_password.txt' 'UNIN local password'
Write-LocalSecret '.\secrets\auth_session_signing_key.txt' 'UNIN session signing key (minimum 32 characters)'
Write-LocalSecret '.\secrets\nextfind_username.txt' 'NextFind username'
Write-LocalSecret '.\secrets\nextfind_password.txt' 'NextFind password'
Write-LocalSecret '.\secrets\tmdb_access_token.txt' 'TMDB Bearer Access Token'
Write-LocalSecret '.\secrets\avistaz_username.txt' 'AvistaZ username'
Write-LocalSecret '.\secrets\avistaz_password.txt' 'AvistaZ password'
Write-LocalSecret '.\secrets\avistaz_pid.txt' 'AvistaZ PID'
Write-LocalSecret '.\secrets\qb_base_url.txt' 'qBittorrent base URL'
Write-LocalSecret '.\secrets\qb_username.txt' 'qBittorrent username'
Write-LocalSecret '.\secrets\qb_password.txt' 'qBittorrent password'
icacls .\secrets\*.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

12 个文件名必须与 [deploy/compose.secrets.yaml.example](deploy/compose.secrets.yaml.example) 一致：

```text
secrets/auth_local_username.txt
secrets/auth_local_password.txt
secrets/auth_session_signing_key.txt
secrets/nextfind_username.txt
secrets/nextfind_password.txt
secrets/tmdb_access_token.txt
secrets/avistaz_username.txt
secrets/avistaz_password.txt
secrets/avistaz_pid.txt
secrets/qb_base_url.txt
secrets/qb_username.txt
secrets/qb_password.txt
```

Secret 可见范围固定为：API 读取全部 12 个；普通 Worker 只读取 NextFind 2 个、TMDB 1 个和 AvistaZ 3 个，明确没有 qB Secret；`automation-preflight` 只读取 qB 3 个；下载执行器读取 AvistaZ 3 个和 qB 3 个；只读监控器只读取 qB 3 个；前端不挂载任何 Secret。非 Secret 的主机白名单、目标引用和安全策略仍按职责传入对应服务。

认证与 qB 的非密钥策略仍在本机 `.env` 中配置。认证策略包括 `AUTH_LOCAL_ROLE`、会话/CSRF TTL 和 `AUTH_COOKIE_SECURE`；不要在使用 Secret override 时把三个认证值同时写入 `.env`。`QB_ALLOWED_HOSTS` 必须是 `qb_base_url.txt` 中 URL 的精确主机名；默认只允许 HTTPS。仅在明确接受受信内网明文 HTTP 风险时设置 `QB_ALLOW_INSECURE_HTTP=true`。下载计划需要配置 `QB_TARGET_CATEGORY`、后端预检使用的 `QB_TARGET_SAVE_PATH`、逗号分隔的 `QB_ALLOWED_SAVE_PATHS`、可公开显示的 `QB_SAVE_PATH_REF`、`MAX_CANDIDATE_SIZE_BYTES` 和 `AVISTAZ_FORBIDDEN_QB_VERSIONS`。真实路径只参与后端预检，API 与下载计划只返回 `QB_SAVE_PATH_REF`。

普通开发启动不启用自动预检、下载执行或监控 profile：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example up -d --build
```

自动审批的 qB 只读预检使用独立 `automation-preflight` profile。只有在用户明确批准目标 qB 实例的只读登录/查询、`ENABLE_AUTOMATION_ENGINE=true`、`ENABLE_QB_READ_ONLY=true`，并确认当前自动化策略后才允许启动：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example --profile automation-preflight up -d --build
```

该服务只挂载 qB 三项 Secret，不持有 NextFind、TMDB 或 AvistaZ Secret，也不调用 qB mutation。它发布绑定 `preflight_policy_fingerprint` 与 `QB_TARGET_INSTANCE_REF` 的短 TTL readiness；系统只有观察到与当前配置匹配的新鲜心跳才会排队自动预检。Worker 随后只对固定审批快照执行登录和只读 GET；完整 `PASS` 才可能自动生成计划，其余结果保留人工处理。

仅在真实执行得到单独授权、三开关已显式启用且目标审批已再次确认后，才允许加入 `download-execution` profile：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example --profile download-execution up -d --build
```

这会允许执行器重新搜索已批准的 AvistaZ torrent ID、访问对应 download URL、读取并校验 `.torrent`，并在通过所有闸门后最多向 `qb_base_url.txt` 指定的 qBittorrent 提交一次 add。执行器只有在三开关、AvistaZ/qB 凭据存在性、目标主机与保存路径策略通过时才发布绑定执行配置指纹的短 TTL readiness。自动执行必须观察到匹配心跳，启动模式固定为 `ADD_PAUSED`，提交后还会验证任务确实保持暂停；只有管理员在人工一次性 intent 中显式选择 `START_IMMEDIATELY` 才会立即启动。

需要验证完整自动链路时，必须分别取得 qB 只读预检和真实 qB add 授权，再同时启用两个 profile；仅启用 `automation-preflight` 不授予或启动下载执行器：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example --profile automation-preflight --profile download-execution up -d --build
```

只读监控另用 `download-monitor` profile，并要求 `ENABLE_DOWNLOAD_MONITOR=true` 与 `ENABLE_QB_READ_ONLY=true`：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example --profile download-monitor up -d --build
```

TMDB 只读测试会连接 `api.themoviedb.org`；AvistaZ 搜索测试会连接 `avistaz.to` 的认证与搜索端点；qB 只读测试会连接 `qb_base_url.txt` 指定且被 `QB_ALLOWED_HOSTS` 精确允许的实例。当前均未执行真实冒烟测试。执行器测试与只读测试不是同一授权范围：批准搜索或 qB 只读测试，不等于批准访问 AvistaZ download URL 或向 qB add。

## 明确未实现

- qBittorrent 暂停、恢复、删除、重校验、文件优先级修改、做种控制或 H&R 自动判定。执行器唯一允许的 qB 写操作是受控 add。
- 自动解决 `OUTCOME_UNKNOWN`/`RECONCILIATION_REQUIRED`；当前只能人工登记对账请求，不能自动再次 add。
- 下载完成后的媒体整理、重命名、移动、复制、硬链接、删除、扫描入库或 NextFind/媒体库写回。
- 任何已验证可用的真实 NexusPHP 站点 Profile、浏览器 DOM 抓取或验证码/反爬绕过；
  当前仅有默认关闭的声明式适配骨架与本地 HTML fixture 契约，详见
  [`docs/pt-site-profiles.md`](docs/pt-site-profiles.md)。
