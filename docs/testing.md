# 测试与验收

所有自动化测试使用 respx、`httpx.MockTransport`、ASGI transport 或内存数据库替身。默认配置不允许 TMDB、AvistaZ 或 qBittorrent 真实网络请求；测试不得把任何真实连接开关设为 `true`。

## 后端（PowerShell）

```powershell
Set-Location D:\project\unin\backend
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy app
uv run alembic upgrade head --sql
```

认证与授权覆盖：

- 认证 Secret 未配置、文件不可读或签名密钥过短时 fail closed；公开健康/状态接口不泄露用户数据。
- 登录前 bootstrap CSRF、缺失/错误/过期 token、统一的错误用户名或密码响应，以及正确登录、`/auth/me` 和注销清除 Cookie。
- 会话 Cookie 的 `HttpOnly`/`SameSite=Strict`/`Path=/api`、CSRF Cookie 可读、认证响应禁止缓存，以及篡改和过期会话拒绝。
- 所有状态变更必须提供匹配的 CSRF Cookie 与 `X-CSRF-Token`；`viewer` 不能写、`operator` 不能批准/撤销、`admin` 继承 operator 权限。
- 客户端伪造操作者字段不生效，数据库与响应中的 actor 必须来自服务端 Principal 用户名。

第二阶段覆盖：

- TMDB ID 精确详情、无 ID 多候选、电影/电视剧冲突、429、超时、双语字段与已播 episode matrix。
- AvistaZ 认证成功/失败、过期 Token 单次重认证、429 退避、TMDB/IMDb 搜索、无结果与季集解析。
- 默认关闭的 NexusPHP 扩展骨架使用本地脱敏 HTML fixture 验证字段契约、登录/验证码/挑战页
  稳定错误、同源重定向与运行时 Secret 不落入候选；测试不连接任何真实 NexusPHP 站点。
- 身份人工确认、重复提交防护、搜索状态流、候选评分理由与全部指定风险警告。
- 上游 download/announce/passkey 不进入数据库和 API，日志不泄露凭据。
- 第一阶段 NextFind、数据库队列、Worker 重试、幂等与 API 契约回归。

第三阶段覆盖：

- qB SID 登录成功与失败、302/307 跳转、HTML 登录页、应用/Web API 版本读取、种子/文件/分类读取。
- qB 仅 2xx 成功，以及 progress、ratio、时间戳、大小、速度和文件优先级的严格范围校验；错误和日志不泄露凭据。
- 相同 info hash 阻断、相同发布名与大小警告、目标分类、允许保存路径、候选大小限制、活跃做种、候选做种者、H&R 和 AvistaZ 禁止版本规则。
- `BLOCKED`/`UNKNOWN` 不可批准，汇总状态必须与明细一致，12 个必需检查不得缺失/重复；预检过期或策略指纹变化后必须重跑。
- 候选快照与原候选脱钩、重复列绑定、规范 JSON SHA-256、ORM/PostgreSQL 不可变约束、重复有效审批、审批过期、三项人工确认、拒绝/撤销审计和已消费审批不可复用。
- 批准只生成一个绑定审批哈希、策略指纹和计划哈希的 `DownloadPlan`；篡改或注入开放字典字段会被拒绝，计划不含真实下载 URL、announce、PID、Cookie、Token、passkey 或密码，也不会调用 AvistaZ 或 qBittorrent 写操作。
- OpenAPI 的 qB 命名空间只有两个 `GET`，不存在直接映射 qB add/start/resume/pause/delete/recheck/download 的业务 API；`/approval-requests/{id}/execute` 是只创建数据库执行记录的控制面端点。
- `ENABLE_QB_READ_ONLY=false` 时 qB 状态与任务端点稳定返回 `QB_READ_ONLY_DISABLED`，不会尝试连接真实实例。

`alembic upgrade head --sql` 只生成 SQL 到标准输出，不连接或修改数据库。

## 前端（PowerShell）

```powershell
Set-Location D:\project\unin\frontend
npm.cmd ci
npm.cmd test
npm.cmd run build
npm.cmd run lint
```

覆盖原有列表状态以及身份候选、人工确认、PT 候选、评分理由、警告、固定候选审批、三项批准确认、预检状态和下载计划；还覆盖两步执行确认、默认 `ADD_PAUSED`、nonce 请求前销毁、执行列表/详情、人工对账、下载任务列表以及包含媒体/审批/执行/进度/H&R/警告/三源时间线的总结页。qB、审批、执行与下载任务 store 在失败时清空陈旧状态，并使用请求 generation 丢弃迟到响应。源码不得使用 `localStorage`、`sessionStorage` 或 Pinia 持久化保存凭据、nonce、SID 或 qB 数据。

## Compose（PowerShell）

默认配置：

```powershell
Set-Location D:\project\unin
docker compose --env-file .env.example config --quiet
```

Secret override 配置结构：

```powershell
docker compose --env-file .env.example -f compose.yaml -f deploy\compose.secrets.yaml.example config --quiet
```

阶段 5 可选服务 profile（仍使用全 `false` 的 `.env.example`，只验证 Compose 结构，不会启动容器）：

```powershell
docker compose --env-file .env.example -f compose.yaml -f deploy\compose.secrets.yaml.example --profile download-execution config --quiet
docker compose --env-file .env.example -f compose.yaml -f deploy\compose.secrets.yaml.example --profile download-monitor config --quiet
```

这些命令只解析服务配置和 Secret 引用。Docker Compose v5 的 `config` 命令不会检查本地 Secret 文件是否存在，因此成功不代表以下 10 个文件已经就绪，也不代表执行开关已获授权：

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

启动容器前应单独检查文件存在性，不读取或打印其内容：

```powershell
$requiredSecrets = @(
    'auth_local_username.txt', 'auth_local_password.txt',
    'auth_session_signing_key.txt',
    'tmdb_access_token.txt', 'avistaz_username.txt', 'avistaz_password.txt',
    'avistaz_pid.txt', 'qb_base_url.txt', 'qb_username.txt', 'qb_password.txt'
)
$missingSecrets = $requiredSecrets | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path '.\secrets' $_))
}
if ($missingSecrets) {
    throw "缺少本地 Secret 文件：$($missingSecrets -join ', ')"
}
```

不得把真实凭据写入命令行、聊天或版本库，也不得使用占位值启动容器。Secret 文件格式和服务可见范围见 `docs/security.md`。

## 静态安全验收

在仓库根目录运行以下只读检查，用于确认没有新增 qB 写路由或浏览器持久化：

```powershell
Set-Location D:\project\unin
rg -n 'localStorage|sessionStorage|pinia-plugin-persist' frontend\src
rg -n '@router\.(post|put|patch|delete)' backend\app\api\routes\downloaders.py
rg -n '(/api/v2/torrents/(add|pause|resume|delete|recheck)|download_url|announce|passkey)' backend\app frontend\src
rg -n 'proxy_hide_header\s+Set-Cookie' frontend\nginx.conf
```

预期：第一、第二和第四条无匹配；第三条可命中默认关闭的内部 qB 写适配器、安全校验或测试断言，但不得出现在业务路由中，也不得返回真实下载字段。最终还应读取 `/api/openapi.json`，确认认证端点存在、执行 intent/execute/reconcile 端点具有 admin RBAC 与 CSRF，且 `/api/downloaders/qbittorrent/*` 仍只有 `GET`。

阶段 4 控制面定向验收：

```powershell
Set-Location D:\project\unin\backend
uv run pytest tests\test_execution_control_plane.py -q
```

该测试使用 SQLite 与 ASGI Mock，不配置真实 Secret、不访问网络。它覆盖 nonce 仅保存 SHA-256、intent 过期、qB 目标漂移、幂等重放/冲突、审批/intent/幂等键唯一约束、撤销后的提交闸门、实际 info hash 先持久化、未知结果禁止重试及纯数据库对账。

阶段 5 执行、监控、任务总结和 PT 扩展定向验收：

```powershell
Set-Location D:\project\unin\backend
uv run pytest tests\test_download_executor.py tests\test_pt_site_extensibility.py -q
```

覆盖项包括：

- 三开关任一缺失时执行器拒绝运行，监控器必须同时启用自身开关和 qB 只读开关；
- 数据库时间 claim、租约续期/fencing、失效审批在任何 AvistaZ/qB 请求前取消，以及提交预留后的过期租约进入人工对账；
- AvistaZ 精确 torrent ID 重搜、`.torrent` bencode/大小/文件数/v1-v2 hash 校验、候选标题/hash/大小漂移拒绝；
- qB 全 hash alias 查重、真正 POST 前 write guard、add 后 hash/分类/保存路径/大小核验、成功消费审批并唯一创建 `DownloadJob`；
- 写前可重试错误的有界 `RETRY_WAIT`，写入可能发生后的 `OUTCOME_UNKNOWN`，提交预留后无法验证的 `RECONCILIATION_REQUIRED`，以及这些状态禁止自动再次 add；
- 监控器只读更新任务状态和统计、不调用 mutation、不推断 H&R；summary 固定嵌套，timeline 合并三类事件并二次脱敏；
- 多站点 `site_id` 绑定、未知/禁用站点失败关闭、NexusPHP Profile 严格同源、登录/验证码/挑战稳定错误、运行时 Secret 不进入候选或 request gate，以及全部 HTML fixture 离线解析。

## 验收边界

- 自动化验收不得把真实连接开关设为 `true`。
- 未取得用户确认，不运行真实冒烟测试；凭据只通过本地 Secret 或安全输入配置，禁止粘贴到聊天。
- 不启动真实 API/PostgreSQL 也能完成单元、Mock、静态、构建、Alembic 离线 SQL 与 Compose 解析验收。
- Mock 中的 AvistaZ download 和 qB add 都由内存 transport/fake adapter 模拟。未取得独立真实执行授权时，验收完成即停止：不访问真实 download URL、不获取真实 `.torrent`、不调用真实 qB 写接口、不开始真实下载。
- 即使真实执行器另行获准验证，也不执行暂停、恢复、删除、重校验、H&R 推断或媒体文件移动、复制、重命名、硬链接和删除。
