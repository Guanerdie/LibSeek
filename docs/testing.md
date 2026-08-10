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

第二阶段覆盖：

- TMDB ID 精确详情、无 ID 多候选、电影/电视剧冲突、429、超时、双语字段与已播 episode matrix。
- AvistaZ 认证成功/失败、过期 Token 单次重认证、429 退避、TMDB/IMDb 搜索、无结果与季集解析。
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
- OpenAPI 的 qB 命名空间只有两个 `GET`，不存在 execute/add/start/resume/pause/delete/recheck/download 等 API。
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

覆盖原有列表状态以及身份候选、人工确认、PT 候选、评分理由、警告、固定候选审批、三项批准确认、预检状态、下载计划和只读阶段提示。qB store 与审批 store 会在失败时清空旧数据，并使用请求 generation 丢弃迟到响应。源码不得使用 `localStorage`、`sessionStorage` 或 Pinia 持久化保存凭据、SID 或 qB 数据。

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

第二条命令只解析配置，但要求 `secrets` 下 7 个本地文件存在：

```text
tmdb_access_token.txt
avistaz_username.txt
avistaz_password.txt
avistaz_pid.txt
qb_base_url.txt
qb_username.txt
qb_password.txt
```

不得为了配置解析把真实凭据写入命令行、聊天或版本库。可以使用内容为 `compose-config-placeholder` 的本地占位文件完成 `config --quiet`，检查后移除；不要用这些占位值启动容器。Secret 文件格式和服务可见范围见 `docs/security.md`。

## 静态安全验收

在仓库根目录运行以下只读检查，用于确认没有新增 qB 写路由或浏览器持久化：

```powershell
Set-Location D:\project\unin
rg -n 'localStorage|sessionStorage|pinia-plugin-persist' frontend\src
rg -n '@router\.(post|put|patch|delete)' backend\app\api\routes\downloaders.py
rg -n '(/api/v2/torrents/(add|pause|resume|delete|recheck)|download_url|announce|passkey)' backend\app frontend\src
```

预期：前两条无匹配；第三条只能命中显式禁止/脱敏校验或测试断言，不得出现可执行 qB 写调用或可返回的真实下载字段。最终还应读取 `/api/openapi.json`，确认 `/api/downloaders/qbittorrent/*` 只有 `GET`。

## 验收边界

- 自动化验收不得把真实连接开关设为 `true`。
- 未取得用户确认，不运行真实冒烟测试；凭据只通过本地 Secret 或安全输入配置，禁止粘贴到聊天。
- 不启动真实 API/PostgreSQL 也能完成单元、Mock、静态、构建、Alembic 离线 SQL 与 Compose 解析验收。
- 本阶段验收完成即停止：不访问 AvistaZ download URL，不获取 `.torrent`，不调用 qBittorrent 写接口，不开始下载，也不执行媒体文件移动、复制或删除。
