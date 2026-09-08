# LibSeek 优化路线图

本文档列出了 LibSeek 项目接下来的优化方向和具体实施方案。按优先级和实施难度排序。

---

## 📊 优化概览

| 优先级 | 优化项 | 预期收益 | 实施难度 | 预计工时 |
|--------|--------|----------|----------|----------|
| 🔴 高 | 自动化并发控制 | 防止账号封禁 | ⭐⭐ | 4h |
| 🔴 高 | 搜索结果缓存 | 减少90%重复请求 | ⭐⭐⭐ | 6h |
| 🔴 高 | 关键路径日志增强 | 排查效率提升5倍 | ⭐ | 2h |
| 🟡 中 | 冷却策略配置化 | 用户可自定义策略 | ⭐⭐ | 4h |
| 🟡 中 | 适配器反爬增强 | 提升搜索成功率 | ⭐⭐⭐ | 6h |
| 🟡 中 | 集成测试覆盖 | 减少回归风险 | ⭐⭐⭐⭐ | 12h |
| 🟢 低 | 性能监控仪表盘 | 数据驱动优化 | ⭐⭐⭐ | 8h |
| 🟢 低 | 连载剧分季支持 | 追剧体验提升 | ⭐⭐⭐⭐ | 10h |
| 🟢 低 | RSS反向匹配 | 抢种时效性提升 | ⭐⭐⭐⭐ | 12h |

---

## 🔴 第一阶段：高优先级（2周内完成）

### 优化1: 自动化并发控制

**当前问题**：
```python
# automation.py 中用 asyncio.gather() 无限制并发
results = await asyncio.gather(*[_execute_job(...) for job in jobs])
# 如果 max_parallel_jobs=50，会同时发起50个PT站请求
```

**风险**：
- 触发PT站反爬限制
- 耗尽系统资源
- TMDB API配额快速耗尽

**实施方案**：

#### 步骤1：修改 `backend/app/simple/automation.py`

```python
# 在文件顶部添加
_CONCURRENT_SEARCH_LIMIT = 5  # 最多同时5个搜索任务

async def run_automation(
    session: AsyncSession,
    policy_id: str = "default",
    dry_run: bool = False,
    limit: int = 20,
) -> AutomationRun:
    # ... 原有代码 ...

    # 创建信号量限制并发
    semaphore = asyncio.Semaphore(_CONCURRENT_SEARCH_LIMIT)

    async def _execute_job_with_limit(job: AutomationJob):
        async with semaphore:
            return await _execute_job(
                session, job, policy, adapters, dry_run
            )

    # 替换原来的 gather
    results = await asyncio.gather(*[
        _execute_job_with_limit(job) for job in jobs
    ])

    # ... 原有代码 ...
```

#### 步骤2：创建数据库迁移（可选，添加配置字段）

```bash
cd backend
uv run alembic revision -m "add_concurrent_limit_to_policy"
```

在生成的迁移文件中：

```python
def upgrade() -> None:
    op.add_column(
        'automation_policy',
        sa.Column('max_concurrent_searches', sa.Integer(), nullable=False, server_default='5')
    )

def downgrade() -> None:
    op.drop_column('automation_policy', 'max_concurrent_searches')
```

#### 步骤3：更新模型

```python
# backend/app/simple/models.py
class AutomationPolicy(Base):
    # ... 现有字段 ...
    max_concurrent_searches: Mapped[int] = mapped_column(
        Integer, default=5, nullable=False
    )
```

#### 步骤4：前端添加配置项

```vue
<!-- frontend/src/views/AutomationSettings.vue -->
<el-form-item label="最大并发搜索数">
  <el-input-number
    v-model="policy.max_concurrent_searches"
    :min="1"
    :max="10"
  />
  <span class="hint">
    同时搜索的最大任务数，建议5-10。过高可能被PT站限流。
  </span>
</el-form-item>
```

**预期效果**：
- 请求速率控制在站点限制以内
- 系统资源占用平稳
- 不再出现429错误

**测试方法**：
```python
# backend/tests/test_automation_concurrency.py
@pytest.mark.asyncio
async def test_concurrent_limit(db_session):
    """验证并发限制生效"""
    # 创建20个待搜索的条目
    # 记录同时活跃的搜索数
    # 断言最大并发数 <= 5
```

---

### 优化2: 搜索结果缓存

**当前问题**：
- 用户手动搜索后，自动化可能在几分钟内重复搜索同一条目
- 浪费PT站配额
- 可能得到不一致的结果

**实施方案**：

#### 步骤1：数据库迁移

```bash
cd backend
uv run alembic revision -m "add_search_cache_fields"
```

```python
def upgrade() -> None:
    op.add_column(
        'release_search',
        sa.Column('cache_key', sa.String(64), nullable=True, index=True)
    )
    op.add_column(
        'release_search',
        sa.Column('cache_expires_at', sa.DateTime(timezone=True), nullable=True)
    )

def downgrade() -> None:
    op.drop_column('release_search', 'cache_expires_at')
    op.drop_column('release_search', 'cache_key')
```

#### 步骤2：更新模型

```python
# backend/app/simple/models.py
import hashlib

class ReleaseSearch(Base):
    # ... 现有字段 ...
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    cache_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @staticmethod
    def make_cache_key(media_id: str, site_ids: list[str]) -> str:
        """生成缓存键"""
        content = f"{media_id}:{':'.join(sorted(site_ids))}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]
```

#### 步骤3：修改搜索逻辑

```python
# backend/app/simple/service.py
from datetime import timedelta

SEARCH_CACHE_TTL = timedelta(minutes=5)

async def get_or_create_search(
    session: AsyncSession,
    media_id: str,
    site_ids: list[str],
    force: bool = False
) -> ReleaseSearch:
    """获取或创建搜索，支持缓存"""

    if not force:
        # 查找5分钟内的缓存
        cache_key = ReleaseSearch.make_cache_key(media_id, site_ids)
        now = utc_now()

        cached = await session.scalar(
            select(ReleaseSearch)
            .where(
                ReleaseSearch.cache_key == cache_key,
                ReleaseSearch.cache_expires_at > now
            )
            .order_by(ReleaseSearch.created_at.desc())
        )

        if cached:
            _logger.info(
                "Using cached search result",
                extra={"search_id": str(cached.id), "media_id": media_id}
            )
            return cached

    # 创建新搜索
    search = ReleaseSearch(
        id=uuid4(),
        media_id=media_id,
        cache_key=ReleaseSearch.make_cache_key(media_id, site_ids),
        cache_expires_at=utc_now() + SEARCH_CACHE_TTL,
        # ... 其他字段 ...
    )
    session.add(search)
    await session.flush()
    return search
```

#### 步骤4：前端支持强制刷新

```vue
<!-- frontend/src/views/MediaDetail.vue -->
<el-button
  @click="searchReleases(true)"
  :loading="searching"
>
  <el-icon><Refresh /></el-icon>
  强制刷新
</el-button>

<script>
async function searchReleases(force = false) {
  searching.value = true
  try {
    const response = await api.post('/api/releases/search', {
      media_id: mediaId.value,
      site_ids: selectedSites.value,
      force: force  // 跳过缓存
    })
    // ...
  } finally {
    searching.value = false
  }
}
</script>
```

**预期效果**：
- 缓存命中率 > 80%
- PT站请求量减少 90%
- 用户体验更一致

---

### 优化3: 关键路径日志增强

**当前问题**：
- 自动化失败时难以定位原因
- 不知道为什么某个候选被拒绝

**实施方案**：

#### 步骤1：安装结构化日志库

```bash
# backend/pyproject.toml
dependencies = [
    # ... 现有依赖 ...
    "structlog>=24.1.0",
]
```

#### 步骤2：配置 structlog

```python
# backend/app/core/logging.py (新建文件)
import structlog
from structlog.processors import (
    JSONRenderer,
    TimeStamper,
    add_log_level,
)

def configure_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_log_level,
            TimeStamper(fmt="iso"),
            JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
```

#### 步骤3：在关键决策点添加日志

```python
# backend/app/simple/automation.py

def _filter_candidates(...):
    logger = structlog.get_logger()

    for candidate in candidates:
        # 体积检查
        if candidate.size_bytes > policy.max_size_bytes:
            logger.info(
                "candidate_rejected",
                reason="size_exceeds_limit",
                candidate_id=str(candidate.id),
                candidate_size_gb=round(candidate.size_bytes / 1e9, 2),
                max_size_gb=round(policy.max_size_bytes / 1e9, 2),
                media_id=str(media.id),
                media_title=media.title,
            )
            continue

        # 做种数检查
        if candidate.seeders < policy.minimum_seeders:
            logger.info(
                "candidate_rejected",
                reason="insufficient_seeders",
                candidate_id=str(candidate.id),
                seeders=candidate.seeders,
                required_seeders=policy.minimum_seeders,
                media_id=str(media.id),
            )
            continue

        # 评分检查
        if candidate.score < policy.minimum_score:
            logger.info(
                "candidate_rejected",
                reason="score_too_low",
                candidate_id=str(candidate.id),
                score=candidate.score,
                required_score=policy.minimum_score,
                media_id=str(media.id),
            )
            continue

        # 身份验证
        if not _is_identity_verified(candidate):
            logger.warning(
                "candidate_rejected",
                reason="identity_unverified",
                candidate_id=str(candidate.id),
                verification_status=candidate.verification_status,
                media_id=str(media.id),
            )
            continue

        logger.info(
            "candidate_accepted",
            candidate_id=str(candidate.id),
            media_id=str(media.id),
            score=candidate.score,
            seeders=candidate.seeders,
            size_gb=round(candidate.size_bytes / 1e9, 2),
        )
        return candidate

    logger.info(
        "no_candidate_selected",
        media_id=str(media.id),
        total_candidates=len(candidates),
    )
    return None
```

#### 步骤4：日志分析脚本

```python
# scripts/analyze_automation_logs.py
#!/usr/bin/env python3
"""分析自动化日志，生成统计报告"""

import json
import sys
from collections import Counter

def analyze_logs(log_file):
    rejection_reasons = Counter()
    accepted_count = 0

    with open(log_file) as f:
        for line in f:
            try:
                log = json.loads(line)
                event = log.get('event')

                if event == 'candidate_rejected':
                    reason = log.get('reason')
                    rejection_reasons[reason] += 1
                elif event == 'candidate_accepted':
                    accepted_count += 1
            except json.JSONDecodeError:
                continue

    print("## 自动化日志分析")
    print(f"\n接受候选: {accepted_count}")
    print(f"\n拒绝原因分布:")
    for reason, count in rejection_reasons.most_common():
        print(f"  - {reason}: {count}")

if __name__ == '__main__':
    analyze_logs(sys.argv[1])
```

使用方法：
```bash
docker compose logs backend | python scripts/analyze_automation_logs.py -
```

**预期效果**：
- 排查问题时间从 30 分钟降到 5 分钟
- 数据驱动优化策略调整

---

## 🟡 第二阶段：中优先级（4周内完成）

### 优化4: 冷却策略配置化

**当前限制**：
冷却时长硬编码为 1天→3天→7天，无法根据站点规则调整。

**实施方案**：

```python
# backend/app/simple/models.py
class AutomationPolicy(Base):
    # 新增字段
    cooldown_tier_1_hours: Mapped[int] = mapped_column(Integer, default=24)
    cooldown_tier_2_hours: Mapped[int] = mapped_column(Integer, default=72)
    cooldown_tier_3_hours: Mapped[int] = mapped_column(Integer, default=168)

# backend/app/simple/automation.py
def _search_cooldown(miss_count: int, policy: AutomationPolicy) -> timedelta:
    if miss_count == 1:
        return timedelta(hours=policy.cooldown_tier_1_hours)
    elif miss_count == 2:
        return timedelta(hours=policy.cooldown_tier_2_hours)
    else:
        return timedelta(hours=policy.cooldown_tier_3_hours)
```

前端添加配置界面，提供预设模板：
- 激进：6小时 → 24小时 → 72小时
- 平衡：24小时 → 72小时 → 168小时（默认）
- 保守：72小时 → 168小时 → 336小时

---

### 优化5: 适配器反爬增强

**当前问题**：
- User-Agent 固定
- 请求间隔固定
- 无失败重试

**实施方案**：

```python
# backend/app/adapters/pt_sites/base.py
import random

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)...",
    "Mozilla/5.0 (X11; Linux x86_64)...",
    # 10+ 真实浏览器UA
]

class PtSiteAdapterBase:
    async def _request_with_retry(
        self,
        method: str,
        url: str,
        max_retries: int = 3,
        **kwargs
    ):
        for attempt in range(max_retries):
            try:
                # 随机UA
                headers = kwargs.get('headers', {})
                headers['User-Agent'] = random.choice(USER_AGENTS)

                # 随机延迟（避免规律性）
                if attempt > 0:
                    jitter = random.uniform(0, 2)
                    await asyncio.sleep(self.min_interval + jitter)

                response = await self.client.request(
                    method, url, headers=headers, **kwargs
                )
                response.raise_for_status()
                return response

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    _logger.warning(
                        "Rate limited, retrying",
                        attempt=attempt,
                        wait_seconds=wait
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
            except httpx.TimeoutException:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

        raise Exception(f"Failed after {max_retries} retries")
```

---

### 优化6: 集成测试覆盖

**目标**：核心流程测试覆盖率 80%

**实施方案**：

```python
# backend/tests/integration/test_automation_flow.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_full_automation_flow(db_session):
    """端到端测试：从发现缺失到提交下载"""

    # 1. 准备测试数据
    media = LibraryMediaItem(
        id=uuid4(),
        title="Test Movie",
        year=2024,
        tmdb_id=12345,
        state=MediaState.READY,
    )
    db_session.add(media)

    policy = AutomationPolicy(
        id="test",
        max_parallel_jobs=1,
        minimum_score=7.0,
        minimum_seeders=5,
    )
    db_session.add(policy)
    await db_session.commit()

    # 2. Mock 外部服务
    with patch('app.adapters.pt_sites.avistaz_live.AvistaZAdapter') as mock_pt:
        # Mock PT站返回结果
        mock_pt.return_value.search_releases = AsyncMock(return_value=[
            ReleaseInfo(
                title="Test.Movie.2024.1080p.BluRay",
                size_bytes=10 * 1024**3,
                seeders=100,
                # ...
            )
        ])

        with patch('app.adapters.downloaders.qbittorrent.QbittorrentAdapter') as mock_qb:
            mock_qb.return_value.add_torrent = AsyncMock(return_value="hash123")

            # 3. 运行自动化
            run = await run_automation(db_session, policy.id, dry_run=False)

            # 4. 验证结果
            assert run.state == AutomationRunState.SUCCEEDED
            assert run.jobs_succeeded == 1

            # 5. 验证下载已提交
            download = await db_session.scalar(
                select(Download).where(Download.media_id == media.id)
            )
            assert download is not None
            assert download.state == DownloadState.PENDING

@pytest.mark.asyncio
async def test_search_cooldown_prevents_redundant_search(db_session):
    """验证冷却机制生效"""
    # 测试连续两次运行，第二次应该跳过冷却期内的条目
    pass

@pytest.mark.asyncio
async def test_concurrent_limit_enforced(db_session):
    """验证并发限制"""
    # 创建20个待搜索条目，验证同时活跃的搜索数 <= 5
    pass
```

---

## 🟢 第三阶段：低优先级（按需实施）

### 优化7: 性能监控仪表盘

在前端添加一个"系统监控"页面，展示：

- 过去7天搜索成功率趋势
- 各站点平均响应时间
- 自动化执行历史
- 资源覆盖率统计

技术栈：
- 后端：新增 `/api/stats` 接口
- 前端：使用 ECharts 绘制图表

---

### 优化8: 连载剧分季支持

**当前限制**：只接受完整季包，正在播出的剧无法自动化。

**实施方案**：

1. 检测剧集是否完结（通过 TMDB `Episode.air_date`）
2. 对已完结的季，自动化只抓取该季的包
3. 数据库记录"已完成的季"状态

详细设计见单独文档。

---

### 优化9: RSS 反向匹配

**原理**：
- 当前：拿 N 个缺失条目 → 搜索站点（N 次请求）
- 优化：拿站点 RSS → 匹配缺失列表（1 次请求）

**适用场景**：NexusPHP 站点（标配 RSS）

**实施方案**：

```python
# backend/app/services/rss_matcher.py
async def match_rss_to_library(
    session: AsyncSession,
    site: PtSiteAdapter
) -> list[Match]:
    """RSS 反向匹配"""

    # 1. 获取站点最近1小时的种子RSS
    feed = await site.fetch_rss(hours=1)

    # 2. 查询所有待搜索的缺失条目
    missing = await session.scalars(
        select(LibraryMediaItem).where(
            LibraryMediaItem.state == MediaState.READY
        )
    )

    # 3. 标题匹配 + TMDB验证
    matches = []
    for entry in feed.entries:
        for media in missing:
            if _title_matches(entry.title, media.title, media.year):
                # 验证身份
                if await _verify_identity(entry, media):
                    matches.append(Match(media, entry))

    return matches
```

定时任务：每10分钟运行一次 RSS 匹配。

---

## 📅 实施时间表

### 第1-2周（高优先级）
- [ ] 周一-周二：并发控制 + 测试
- [ ] 周三-周五：搜索缓存 + 测试
- [ ] 周六：日志增强
- [ ] 周日：集成测试 + 代码审查

### 第3-4周（中优先级）
- [ ] 周一-周二：冷却策略配置化
- [ ] 周三-周五：反爬增强
- [ ] 周末：集成测试补充

### 第5周+（低优先级，按需）
- [ ] 性能监控（2天）
- [ ] 连载剧支持（3天）
- [ ] RSS反向匹配（4天）

---

## 🎯 成功指标

### 技术指标
- PT站请求量减少 70%
- 搜索成功率提升至 85%+
- 自动化失败率 < 5%
- 核心流程测试覆盖率 > 80%

### 用户体验指标
- 自动化每日命中资源数增加 50%
- 用户手动介入次数减少 60%
- 系统稳定性：连续运行 > 30 天无需重启

---

## 📝 开发流程

对于每个优化项：

1. **创建功能分支**
   ```bash
   git checkout -b feature/concurrent-limit
   ```

2. **实现 + 单元测试**
   - 先写测试
   - 再写实现
   - 测试覆盖率 > 80%

3. **集成测试**
   - 在开发环境完整验证
   - 检查日志无异常

4. **代码审查**
   - 自查：是否符合现有代码风格
   - 是否有安全隐患

5. **合并到主分支**
   ```bash
   git checkout main
   git merge feature/concurrent-limit
   git push origin main
   ```

6. **部署到生产**
   - 按照 `PRODUCTION_DEPLOYMENT_STEPS.md`
   - 部署后观察 24 小时

7. **效果评估**
   - 收集前后对比数据
   - 记录到本文档

---

## 🔗 相关文档

- [搜索冷却优化](./OPTIMIZATION_SEARCH_COOLDOWN.md) - 已完成
- [代码走查笔记](./CODE_REVIEW_NOTES.md) - 架构分析
- [部署指南](./DEPLOYMENT_GUIDE.md) - 运维手册
- [生产部署步骤](./PRODUCTION_DEPLOYMENT_STEPS.md) - 当前版本部署

---

## 📞 需要帮助？

如果在实施过程中遇到问题，可以：

1. 查看 GitHub Issues
2. 查看项目 Wiki
3. 在代码中搜索相关注释和文档字符串

祝优化顺利！🚀
