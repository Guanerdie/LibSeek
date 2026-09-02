# UNIN 项目代码走查笔记

> 日期：2026-09-02  
> 版本：0.9.0  
> 走查范围：后端架构、数据模型、自动化逻辑、前端组织

---

## 整体评价

**定位清晰**：面向个人/家庭NAS用户的PT资源自动化管理工具，不是企业级分布式系统。技术选型和架构复杂度与目标场景匹配良好。

**成熟度**：0.9版本完成了一次大重构（从0.8不兼容升级），数据模型清晰，核心流程完整，可以投入实际使用。

**技术债务**：适中。有明确的TODO和已知限制（如文档中提到的"轻量调度器"、"qBittorrent写入需授权"），但没有明显的架构性缺陷。

---

## 架构设计

### 1. 技术栈选择

#### 后端
- **FastAPI**：现代、高性能、自带OpenAPI文档，适合小团队快速迭代 ✅
- **SQLAlchemy 2.0 + Alembic**：成熟的ORM，支持异步，迁移管理规范 ✅
- **SQLite默认 + PostgreSQL可选**：单机部署用SQLite，扩展性需求用PG，合理 ✅
- **无Redis/Celery依赖**：减少部署复杂度，用内置调度器替代，符合目标用户能力 ✅

**评价**：技术栈现代且克制，没有为了"看起来专业"引入不必要的中间件。

#### 前端
- **Vue 3 + Pinia + Vue Router**：标准组合，生态成熟 ✅
- **无UI框架**：手写样式，可能是为了控制包体积或避免设计同质化 ⚠️

**评价**：前端相对简单，适合功能优先的工具型产品。但长期维护可能需要引入组件库或设计系统。

#### 部署
- **Docker Compose**：单文件编排，前后端分离，适合家庭NAS环境 ✅
- **原生Android客户端**：覆盖移动场景，技术投入较大 🎯

---

### 2. 代码组织

#### 后端结构

```
backend/app/
├── adapters/           # 外部系统适配器（PT站、TMDB、qBittorrent等）
│   ├── base.py
│   ├── downloaders/
│   ├── media_sources/
│   ├── metadata/
│   └── pt_sites/
├── api/                # HTTP路由层
│   ├── routes/
│   └── dependencies.py
├── core/               # 核心工具（配置、时间、异常）
├── db/                 # 数据库基础设施
├── models/             # 共享枚举
├── services/           # 业务逻辑（TV包分类、排序等）
└── simple/             # 核心业务模块（数据模型+服务+自动化）
    ├── models.py       # SQLAlchemy模型
    ├── schemas.py      # Pydantic请求/响应模型
    ├── service.py      # CRUD操作
    ├── automation.py   # 自动化流程
    └── integrations.py # 跨适配器编排
```

**亮点**：
- **Adapter模式清晰**：所有外部依赖都通过适配器抽象，易于Mock和替换
- **simple模块职责明确**：数据+服务+流程在一个命名空间下，便于追溯
- **Pydantic严格分层**：ORM模型和API模型分离，避免暴露内部字段

**可改进**：
- `simple`模块名含义模糊，可改为`workflow`或`orchestration`
- `integrations.py`承担了较多跨服务编排，可能成为God Object

#### 前端结构

```
frontend/src/
├── api/          # API客户端
├── auth/         # 认证逻辑
├── components/   # 通用组件
├── router/       # 路由定义
├── stores/       # Pinia状态管理
├── types/        # TypeScript类型
├── utils/        # 工具函数
└── views/        # 页面视图
```

**评价**：标准Vue 3项目结构，没有特别之处。组件数量少（7个视图），说明功能相对精简。

---

### 3. 数据模型

#### 核心表设计

**LibraryMediaItem**（缺失影视条目）
```python
- id (UUID主键)
- source + source_item_id (来源唯一索引)
- tmdb_id + media_type (TMDB唯一索引)
- 状态机字段：state, attention_reason
- 搜索冷却字段：last_searched_at, search_miss_count, next_search_at
- 时间戳：discovered_at, updated_at
```

**优点**：
- 双重唯一索引（源ID + TMDB ID）防止重复
- 状态机设计清晰（MISSING → IDENTIFYING → READY → SEARCHING → CANDIDATES → DOWNLOADING → COMPLETE）
- 新增的冷却字段设计合理，支持退避策略

**潜在问题**：
- `search_titles`、`country_codes` 存JSON，查询性能可能受影响（但数据量小，可接受）
- `attention_reason` 是自由文本，前端展示时可能需要解析

**Episode**（剧集）
```python
- media_id 外键 + season_number + episode_number 联合唯一
- state (MISSING/AVAILABLE/DOWNLOADING)
```

**评价**：设计简洁，满足剧集级别的状态追踪。

**ReleaseCandidate**（候选资源）
```python
- search_id 外键（关联搜索记录）
- site_id, title, size, seeders
- is_auto_eligible (是否符合自动下载条件)
- warnings (JSON数组)
```

**亮点**：
- `is_auto_eligible`字段提前计算，避免前端重复判断
- `warnings`记录质量问题（ID不匹配、未验证等），辅助人工决策

**Download**（下载任务）
```python
- qb_hash (qBittorrent任务哈希)
- state (SUBMITTING → QUEUED → DOWNLOADING → SEEDING → COMPLETED)
- last_polled_at (轮询时间)
```

**评价**：状态机完整，支持异常状态（ERROR, OUTCOME_UNKNOWN）。

**AutomationPolicy + AutomationJob + AutomationRun**（自动化三层）
```python
Policy: 用户配置（每日预算、评分要求、优先级等）
Job: 单个影视的自动化任务（PENDING → RUNNING → SUCCEEDED/FAILED）
Run: 一次批量运行的记录
```

**评价**：三层抽象合理，Policy可复用，Job可重试，Run可追溯。

---

### 4. 自动化逻辑（`automation.py`）

#### 核心流程

```python
run_automation(policy_id, dry_run) -> AutomationRun
  ├─ 查询待处理条目（状态=READY，不在冷却期）
  ├─ 按优先级排序（电影/剧集，评分，发现时间）
  ├─ 限制批次大小（max_parallel_jobs）
  ├─ 为每个条目创建 AutomationJob
  └─ 并发执行 _execute_job()
      ├─ 搜索资源 → run_release_search()
      ├─ 筛选候选 → _filter_candidates()
      ├─ 排序选择 → _rank_candidates()
      ├─ 提交下载 → submit_download()
      └─ 记录结果 → _record_search_outcome()
```

#### 设计亮点

1. **干净的状态机驱动**
   ```python
   # 只处理 READY 状态的条目
   where(LibraryMediaItem.state == MediaState.READY)
   
   # 搜索后自动流转到 SEARCHING
   media.state = MediaState.SEARCHING
   ```

2. **策略与执行分离**
   ```python
   # AutomationPolicy 定义"做什么"
   policy.max_parallel_jobs
   policy.min_score
   policy.size_budget_gb
   
   # _execute_job() 负责"怎么做"
   ```

3. **优先级算法简单有效**
   ```python
   # 电影优先、高分优先、早发现优先
   order_by(
       media_type.desc(),  # MOVIE > TV
       score.desc(),
       discovered_at.asc()
   )
   ```

4. **错误隔离**
   ```python
   # 单个Job失败不影响其他Job
   try:
       await _execute_job(...)
   except Exception:
       job.state = FAILED
       # 继续下一个
   ```

#### 潜在问题

1. **并发控制简陋**
   ```python
   # 用 asyncio.gather() 并发执行Job，但没有限制并发数
   # 如果 max_parallel_jobs=100，会同时发起100个HTTP请求
   ```
   **建议**：引入信号量或使用 `asyncio.Semaphore`。

2. **搜索结果缓存缺失**
   ```python
   # 如果自动化搜索和手动搜索几乎同时发生，会重复请求PT站
   ```
   **建议**：在 `ReleaseSearch` 上加TTL缓存，短时间内相同查询直接返回。

3. **冷却逻辑硬编码**
   ```python
   def _search_cooldown(miss_count: int) -> timedelta:
       if miss_count == 1: return timedelta(days=1)
       if miss_count == 2: return timedelta(days=3)
       return timedelta(days=7)
   ```
   **建议**：迁移到 `AutomationPolicy` 配置字段，让用户自定义。

---

### 5. 适配器设计（`adapters/`）

#### 基类定义清晰

```python
class MetadataProvider(Protocol):
    async def identify_movie(...) -> MovieIdentity
    async def identify_tv_show(...) -> TvShowIdentity

class PtSiteAdapter(Protocol):
    async def search_releases(...) -> list[ReleaseInfo]

class DownloaderAdapter(Protocol):
    async def submit_torrent(...) -> DownloadInfo
    async def poll_download_state(...) -> DownloadState
```

**优点**：
- 用 `Protocol` 定义接口，支持鸭子类型
- 每个适配器职责单一，易于测试

#### 实现质量

**TMDB适配器**（`metadata/tmdb.py`）
- ✅ 带缓存（LRU + TTL）
- ✅ 速率限制（`min_interval_seconds`）
- ✅ 错误处理完善

**PT站适配器**（`pt_sites/`）
- ✅ 支持多架构（AvistaZ, NexusPHP）
- ✅ 用 `BeautifulSoup` 解析HTML（NexusPHP站点）
- ⚠️ 缺少反爬对策（User-Agent轮换、请求头随机化）

**qBittorrent适配器**（`downloaders/qbittorrent.py`）
- ✅ 读写分离（`qbittorrent_readonly.py`）
- ✅ 需要显式授权写入（`ENABLE_QB_WRITE=true`）
- ✅ 轮询状态更新

---

### 6. 配置管理（`core/config.py`）

#### 设计特点

```python
class Settings(BaseSettings):
    # 支持环境变量和.env文件
    model_config = SettingsConfigDict(env_file=".env")
    
    # 支持文件路径（适配Docker Secrets）
    nextfind_username: SecretStr | None
    nextfind_username_file: Path | None
```

**优点**：
- Pydantic自动验证类型
- 支持环境变量 + 文件路径双模式（Docker友好）
- `SecretStr` 防止日志泄露

**问题**：
- 配置项较多（80+），缺少分组和文档
- 部分默认值不合理（如 `automation_scheduler_enabled=False`，用户容易忘记开启）

---

### 7. 测试覆盖

#### 测试文件

```bash
backend/tests/
├── conftest.py
├── test_*.py (单元测试)
└── .test-tmp/ (测试临时数据)
```

**观察**：
- 测试目录存在，但文件数量未知（需要进一步查看）
- 项目要求 Python 3.12，测试环境配置有门槛
- README提到"可以运行测试"，但未给出覆盖率数据

**风险**：
- 自动化逻辑复杂，缺少集成测试容易引入回归
- 适配器测试需要Mock外部API，维护成本高

---

### 8. 安全性

#### 已实现

- ✅ 认证系统（用户名+密码，Session Cookie）
- ✅ CSRF保护（`auth_bootstrap_csrf_ttl_seconds`）
- ✅ 密码用 `SecretStr` 包装
- ✅ qBittorrent写入需要显式授权
- ✅ 外部请求白名单（`allowed_external_hosts`）

#### 潜在风险

- ⚠️ SQLite无多用户权限控制（但目标场景是单用户/家庭，可接受）
- ⚠️ 无HTTPS强制要求（需要用户自行配置反向代理）
- ⚠️ Session签名密钥可以为空（`auth_session_signing_key: SecretStr | None`）

---

### 9. 性能考量

#### 数据库

- **索引完善**：状态字段、外键、时间戳都有索引
- **N+1查询风险低**：用 `selectinload` 预加载关联
- **SQLite瓶颈**：并发写入受限，但读多写少的场景可以接受

#### 自动化调度

- **轮询模式**：每N秒检查一次（`automation_scheduler_poll_seconds`）
- **单实例锁**：用 `asyncio.Lock` 防止重复运行
- **无持久化队列**：重启后Job丢失（但可以手动重新触发）

**改进方向**：
- 引入事件驱动（如NextFind Webhook）替代轮询
- 用Redis保存调度状态（但违背"无Redis依赖"原则）

---

## 代码质量细节

### 好的实践

1. **类型标注完整**
   ```python
   async def list_media(
       session: AsyncSession,
       *,
       state: MediaState | None,
       ...
   ) -> tuple[list[LibraryMediaItem], int]:
   ```

2. **异常处理规范**
   ```python
   class AppError(Exception):
       """基类异常"""
   
   # 业务异常都继承自AppError，便于统一捕获
   ```

3. **时间处理统一**
   ```python
   # 所有时间都用UTC，避免时区混乱
   from app.core.time import utc_now
   ```

4. **迁移文件命名清晰**
   ```python
   # 20260902_0013_library_search_cooldown.py
   # 日期 + 序号 + 描述
   ```

### 需要改进

1. **魔法数字散落**
   ```python
   # automation.py 中硬编码的优先级权重
   tv_pack_score = tv_pack_rank(...) * 1000
   ```
   **建议**：提取为常量或配置。

2. **日志不足**
   ```python
   # 关键决策点缺少日志，如：
   # - 为什么跳过某个候选资源？
   # - 冷却期计算的中间值？
   ```

3. **TODO注释较多**
   ```python
   # TODO: 支持自定义PT站点
   # TODO: 支持多下载器
   ```
   **建议**：迁移到GitHub Issues，避免代码噪音。

---

## 技术债务评估

### 低风险

- 前端无UI框架：长期维护成本略高，但可控
- SQLite性能上限：目标场景下足够用
- 轻量调度器：比Celery简单，但功能够用

### 中风险

- 自动化并发控制简陋：可能触发PT站限流
- 搜索结果无缓存：重复请求浪费配额
- 测试覆盖率未知：回归风险较高

### 高风险（暂无）

---

## 对比类似项目

| 特性 | UNIN | Sonarr/Radarr | Flexget |
|------|------|---------------|---------|
| 部署复杂度 | 低 | 中 | 低 |
| 自动化灵活性 | 中 | 高 | 极高 |
| 中文支持 | 原生 | 社区 | 插件 |
| PT站适配 | 内置 | 需Jackett | 需配置 |
| 学习曲线 | 平缓 | 陡峭 | 陡峭 |

**定位差异**：
- Sonarr/Radarr 是通用自动化工具，功能全面但复杂
- UNIN 专注PT+NAS场景，降低配置门槛
- Flexget 更像脚本引擎，适合高级用户

---

## 总结与建议

### 做得好的地方

1. **架构清晰**：Adapter模式、状态机、分层设计都很规范
2. **技术栈现代**：FastAPI + SQLAlchemy 2.0 + Vue 3，没有历史包袱
3. **部署友好**：Docker Compose单文件，无复杂依赖
4. **业务逻辑完整**：从缺失发现到下载完成的全流程闭环

### 需要优先改进

1. **增加集成测试**：覆盖自动化流程的关键路径
2. **并发控制优化**：限制同时发起的HTTP请求数
3. **日志增强**：记录决策依据，便于排查问题
4. **配置文档化**：为每个配置项添加注释和示例

### 长期优化方向

1. **插件化PT站适配器**：支持用户自定义站点规则
2. **事件驱动调度**：替代轮询，减少资源消耗
3. **前端国际化**：支持英文界面，扩大用户群
4. **性能监控**：记录搜索耗时、下载速度等指标

---

## 结论

UNIN是一个**设计合理、实现扎实**的PT资源自动化工具。架构选择与目标场景匹配，代码质量高于平均水平，适合投入生产使用。

主要不足在于**测试覆盖不足**和**自动化逻辑的边界情况处理**，但这些可以通过渐进式改进解决。

**推荐指数**：⭐⭐⭐⭐☆ (4/5)

适合人群：
- 有NAS的PT玩家
- 能接受命令行部署的用户
- 希望减少手动搜索资源时间的人

不适合人群：
- 需要极致自动化的高级用户（选Sonarr）
- 没有Docker基础的小白用户
- 多用户共享场景（目前单用户设计）
