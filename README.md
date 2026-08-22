# UNIN

UNIN 是一个面向日常使用的影视缺失资源助手：从 NextFind 同步缺失影视，经 TMDB
确认身份、从 PT 站点搜索候选资源，由用户选择后提交到 qBittorrent，并持续显示下载
状态。

当前版本为 0.9.0，主流程只有三个页面：

```text
缺失 -> 资源 -> 下载
```

“设置”页面集中管理 NextFind、TMDB、PT 站点和 qBittorrent 四类连接。旧版独立审批、
计划、执行、批次、自动化策略和媒体入库控制面不再注册到运行时。

## 技术结构

- FastAPI + SQLAlchemy 2 + Alembic
- Vue 3 + Pinia + Vue Router
- 默认 SQLite；PostgreSQL 可通过 Compose override 使用
- 模块化单体，不依赖 Redis、Celery 或独立 Worker

核心数据只有 `library_media`、`episodes`、`searches`、`release_candidates`、`downloads`
和辅助的 `activity_log`。数据完整性使用普通外键、唯一约束、检查约束和测试，不使用
快照 hash、哈希链、冻结 contract、baseline 或发布 gate。

Torrent info hash 是 BitTorrent 资源身份，不属于额外门禁。qB 写请求超时后会进入
`OUTCOME_UNKNOWN`，状态同步按 info hash 对账，以免直接重试造成重复提交。

## Docker 启动

复制示例配置后启动：

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

前端默认位于 `http://127.0.0.1:9527`，API 文档位于
`http://127.0.0.1:8000/api/docs`。首次打开页面时创建本地管理员，然后在“设置”页面
保存四类连接。

连接配置保存后即可用于 NextFind 同步、TMDB 识别、PT 搜索/取种和 qB 状态读取。
向 qBittorrent 添加任务是唯一额外的外部写入授权，需要在 `.env` 中设置：

```dotenv
ENABLE_QB_WRITE=true
```

同时必须配置 qB 保存路径和分类。认证会话与 CSRF 校验始终保留；凭据仅保存在服务端
持久卷或部署环境中，不返回给浏览器。

默认数据保存在 Docker 卷 `unin_data` 的 SQLite 文件中。需要 PostgreSQL 时使用：

```powershell
$env:POSTGRES_PASSWORD = 'replace-with-a-strong-password'
docker compose -f compose.yaml -f deploy/compose.postgres.yaml up --build -d
```

## 本地开发

后端：

```powershell
Set-Location backend
uv sync --extra dev
$env:DATABASE_URL = 'sqlite+aiosqlite:///./unin-dev.db'
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

前端：

```powershell
Set-Location frontend
npm ci
npm run dev
```

验证：

```powershell
Set-Location backend
uv run pytest tests/test_simplified_daily_flow.py tests/test_simplified_migrations.py
uv run ruff check app tests
uv run mypy app

Set-Location ../frontend
npm test
npm run build
npm run lint
```

## 0.8 数据说明

0.9 是重构后的新数据模型，初始迁移只创建简化表，不尝试把旧审批和执行状态解释为新
日常状态。因此不要对 0.8 数据库直接运行 0.9 迁移。升级时先保留旧数据库备份，让
0.9 使用新的数据库；需要迁移的缺失影视可重新从 NextFind 同步。旧代码和迁移可从
Git 历史恢复。

详细范围和状态设计见 [简化版 MVP 设计](docs/simplified-mvp.md)。
