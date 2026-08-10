# UNIN 影视缺失资源检索与下载编排系统

UNIN 当前版本为 `0.3.0`，完成到第三阶段：从 NextFind 只读发现未入库电影和电视剧，通过 TMDB 只读解析生成待人工确认的影视身份，再通过 AvistaZ 只读搜索生成可解释评分的 PT 候选；操作者可以为一个固定候选创建不可变审批快照，运行 qBittorrent 只读预检并生成不可执行的下载计划。FastAPI、独立 Worker、PostgreSQL 和 Vue 管理端共同保存脱敏审计记录并展示整个流程。

真实 TMDB、AvistaZ 与 qBittorrent 连接默认关闭。本阶段不访问 AvistaZ `download` URL，不获取 `.torrent`，不向 qBittorrent 添加或修改任务，也不移动、复制或删除影视文件。所有身份确认、候选审批均由操作者人工提交；批准只生成下载计划，不会自动选择种子或开始下载。

## 已实现

- Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、PostgreSQL 16、httpx。
- PostgreSQL 任务队列、`FOR UPDATE SKIP LOCKED`、锁租约恢复、幂等任务与可审计重试。
- NextFind 只读发现：独立 Cookie、HTTPS 主机白名单、跳转复验、NDJSON 流式解析和响应大小限制。
- TMDB 真实只读 Provider：Bearer Token、中文/英文详情、外部 ID、最多 5 个搜索候选、已播剧集矩阵、TTL 缓存、限速、429 退避与超时。
- AvistaZ 真实只读适配器：进程内 Bearer Token、401/412 单次重新认证、429 指数退避、单站并发 1、请求间隔至少 6 秒、跳转域名复验。
- 人工身份确认：保留 NextFind 原始信息、TMDB 候选、评分理由、冲突、操作者、确认时间和候选快照；重复确认会被拒绝。
- PT 候选审阅：按 TMDB、IMDb、英文名、原名、中文名或别名依次降级搜索，并展示季集覆盖、规格、音轨、字幕、活跃度、促销、H&R、评分理由和风险警告。
- `metadata_matches`、`identity_reviews`、`torrent_search_runs`、`torrent_candidates` 与完整工作流状态迁移。
- qBittorrent 真实只读适配器：SID Cookie 登录，只读取应用版本、Web API 版本、任务、任务文件和分类；仅 HTTP 2xx 成功，拒绝登录跳转、HTML 页面和越界数值。
- 不可变审批：审批绑定一个候选快照和规范 JSON SHA-256；重复列必须与快照一致，ORM 与 PostgreSQL trigger 禁止修改/删除固定审批字段和审批事件。候选后续变化不会继承旧审批，重复有效审批、过期审批和重复消费都会被拒绝。
- 只读下载前预检：检查 qB 连接与版本、AvistaZ 禁止版本规则、分类、重复任务、允许保存路径、大小限制、活跃做种、候选做种者和 H&R。预检绑定策略指纹，必需检查必须完整且汇总状态必须与明细一致；`UNKNOWN` 永远不等于 `PASS`。
- 非执行下载计划：只有新鲜且总体为 `PASS`/`WARNING` 的预检，以及三项人工确认完成后才能生成；计划以审批快照哈希、预检策略指纹和自身规范哈希绑定，并禁止更新/删除，不含下载 URL、announce、Cookie、Token、PID 或密码。
- 人工审批页展示固定快照、预检、有效期、审计事件和下载计划。qB 前端状态使用请求 generation，失败会清除旧数据，避免旧响应覆盖新响应。
- Docker Compose 四服务部署；API 与前端端口只绑定 `127.0.0.1`。qB 的三个 Secret 只挂载给 API，不挂载给 Worker 或前端。

架构见 [docs/architecture.md](docs/architecture.md)，安全边界见 [docs/security.md](docs/security.md)，测试说明见 [docs/testing.md](docs/testing.md)。

## 目录

```text
backend/                 FastAPI、适配器、数据库、Worker、Alembic、测试
frontend/                Vue 3、Pinia、Router、身份与 PT 候选页面、Vitest
deploy/                  Docker Secret Compose override 示例
docs/                    架构、安全、测试文档
scripts/                 Windows/Linux 启动与测试脚本
secrets/                 7 个本地 Secret 文件目录，*.txt 已被 Git 忽略
compose.yaml             api、worker、frontend、postgres
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

先同时修改 `POSTGRES_PASSWORD` 与 `DATABASE_URL` 中的同一个密码。NextFind 凭据留空时系统仍可启动，但不会创建真实发现任务。不要把任何密码、PID、Cookie、Token 或 TMDB Key 发送到聊天或提交到版本库。

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

## 第二阶段 API

- `POST /api/media/{id}/resolve`
- `GET /api/media/{id}/metadata-candidates`
- `POST /api/media/{id}/identity-confirmations`
- `POST /api/media/{id}/torrent-searches`
- `GET /api/media/{id}/torrent-searches`
- `GET /api/torrent-searches/{id}`
- `GET /api/torrent-searches/{id}/candidates`

原有健康检查、系统状态、发现任务、影视列表和适配器 API 保持兼容。所有分页有边界，错误包含中文 `message` 与机器可读 `error_code`。

## 第三阶段 API

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

qBittorrent API 命名空间只有以上两个 `GET`。项目不提供 `execute`、`add`、`start`、`resume`、`pause`、`delete`、`recheck` 或 `download` 路由；适配器支持的任务文件读取只用于后端只读能力，不对前端暴露写入口。`CONSUMED` 是为未来执行阶段预留的内部单次消费保护状态，本阶段没有消费或执行 API。

## 安全配置真实只读连接

默认保持：

```dotenv
ENABLE_TMDB_LIVE=false
ENABLE_AVISTAZ_LIVE_SEARCH=false
ENABLE_QB_READ_ONLY=false
```

推荐使用项目内的本地 Secret 文件和 Compose override。共需 7 个文件：TMDB 1 个、AvistaZ 3 个、qBittorrent 3 个。以下 PowerShell 片段使用隐藏输入，不会把值打印到终端；它会在本机 `D:\project\unin\secrets` 创建明文 Secret 文件，因此该目录必须仅允许当前用户读取，且不得同步或提交。不要把任何实际值粘贴到聊天。

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

Write-LocalSecret '.\secrets\tmdb_access_token.txt' 'TMDB Bearer Access Token'
Write-LocalSecret '.\secrets\avistaz_username.txt' 'AvistaZ username'
Write-LocalSecret '.\secrets\avistaz_password.txt' 'AvistaZ password'
Write-LocalSecret '.\secrets\avistaz_pid.txt' 'AvistaZ PID'
Write-LocalSecret '.\secrets\qb_base_url.txt' 'qBittorrent base URL'
Write-LocalSecret '.\secrets\qb_username.txt' 'qBittorrent username'
Write-LocalSecret '.\secrets\qb_password.txt' 'qBittorrent password'
icacls .\secrets\*.txt /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

7 个文件名必须与 [deploy/compose.secrets.yaml.example](deploy/compose.secrets.yaml.example) 一致：

```text
secrets/tmdb_access_token.txt
secrets/avistaz_username.txt
secrets/avistaz_password.txt
secrets/avistaz_pid.txt
secrets/qb_base_url.txt
secrets/qb_username.txt
secrets/qb_password.txt
```

qB 的非密钥策略仍在本机 `.env` 中配置。`QB_ALLOWED_HOSTS` 必须是 `qb_base_url.txt` 中 URL 的精确主机名；默认只允许 HTTPS。仅在明确接受受信内网明文 HTTP 风险时设置 `QB_ALLOW_INSECURE_HTTP=true`。下载计划需要配置 `QB_TARGET_CATEGORY`、后端预检使用的 `QB_TARGET_SAVE_PATH`、逗号分隔的 `QB_ALLOWED_SAVE_PATHS`、可公开显示的 `QB_SAVE_PATH_REF`、`MAX_CANDIDATE_SIZE_BYTES` 和 `AVISTAZ_FORBIDDEN_QB_VERSIONS`。真实路径只参与后端预检，API 与下载计划只返回 `QB_SAVE_PATH_REF`。

只在用户明确批准真实冒烟测试后，才把本机 `.env` 中对应开关设为 `true`，并使用两个 Compose 文件启动：

```powershell
docker compose --env-file .env -f compose.yaml -f deploy\compose.secrets.yaml.example up -d --build
```

TMDB 测试会连接 `api.themoviedb.org`，执行指定一个 TMDB ID 对应的详情、外部 ID 和必要季信息 GET。AvistaZ 测试会连接 `avistaz.to`，执行一次 `POST /api/v1/jackett/auth` 和只读 `GET /api/v1/jackett/torrents`。测试前必须再次确认目标 TMDB ID；不会访问任何 `download` URL，也不会获取 `.torrent`。

如另行批准 qB 只读冒烟测试，系统只会连接 `qb_base_url.txt` 指定且被 `QB_ALLOWED_HOSTS` 精确允许的主机，执行 `POST /api/v2/auth/login`，以及必要的 `GET /api/v2/app/version`、`GET /api/v2/app/webapiVersion`、`GET /api/v2/torrents/info`、`GET /api/v2/torrents/files`、`GET /api/v2/torrents/categories`。不会调用任何 qB 写接口。当前未执行 TMDB、AvistaZ 或 qBittorrent 真实冒烟测试。

## 明确未实现

- `.torrent` 下载、AvistaZ download URL 访问或候选自动选择。
- qBittorrent 添加、开始、暂停、恢复、删除、重校验、分类/标签/保存路径/文件优先级修改或任何其他写操作。
- 自动批准影视身份。
- 审批自动触发下载、下载执行、完成入库、做种控制或任何媒体文件操作。
- NexusPHP 页面抓取、浏览器 DOM 抓取或验证码绕过。
