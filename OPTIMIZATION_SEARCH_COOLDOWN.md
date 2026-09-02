# 搜索冷却优化 - 实施说明

## 问题

原有自动化流程会在每个周期对所有缺失影视资源重复搜索，即使这些资源在PT站上根本不存在。这导致：
- 大量无效的PT站请求
- 浪费API配额
- 可能触发PT站反爬限制
- 减慢有效资源的发现速度

## 解决方案

实现按条目的搜索退避机制：
- 首次搜索空手：等待1天再搜
- 连续2次空手：等待3天
- 连续3次及以上：等待7天（封顶）
- 一旦找到候选资源：重置冷却，清空等待时间

## 改动内容

### 1. 数据库迁移 (20260902_0013)

为 `library_media` 表添加三个字段：

```sql
ALTER TABLE library_media ADD COLUMN last_searched_at DATETIME;
ALTER TABLE library_media ADD COLUMN search_miss_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE library_media ADD COLUMN next_search_at DATETIME;
CREATE INDEX ix_library_media_next_search_at ON library_media(next_search_at);
```

### 2. 模型更新 (`app/simple/models.py`)

`LibraryMediaItem` 新增字段：
- `last_searched_at`: 上次搜索时间戳
- `search_miss_count`: 连续空手次数计数器
- `next_search_at`: 下次允许搜索的最早时间

### 3. 自动化逻辑 (`app/simple/automation.py`)

#### 新增函数

```python
def _search_cooldown(miss_count: int) -> timedelta:
    """根据连续空手次数返回冷却时长"""
    # 1次→1天, 2次→3天, 3次+→7天

def _record_search_outcome(media: LibraryMediaItem, *, found_candidate: bool) -> None:
    """记录搜索结果并更新冷却状态"""
```

#### 修改查询逻辑

`run_automation` 取候选条目时，新增过滤条件：

```python
where(
    or_(
        LibraryMediaItem.next_search_at.is_(None),
        LibraryMediaItem.next_search_at <= now
    )
)
```

只取"从未搜索过"或"冷却期已过"的条目。

#### 记录搜索结果

`_execute_job` 在搜索完成后调用 `_record_search_outcome`：
- 找到候选 → `search_miss_count = 0`, `next_search_at = None`
- 没找到候选 → `search_miss_count += 1`, `next_search_at = now + 冷却时长`

## 部署步骤

### 1. 应用数据库迁移

```bash
cd backend
uv run alembic upgrade head
```

迁移会自动为现有条目设置默认值（`search_miss_count = 0`, 其他为 `NULL`）。

### 2. 重启服务

```bash
docker compose down
docker compose up -d --build
```

或本地开发环境：

```bash
# 后端
cd backend
uv run uvicorn app.main:app --reload

# 前端
cd frontend
npm run dev
```

### 3. 验证

启动后第一轮自动化运行时：
1. 所有条目的 `next_search_at` 为空，会全部参与搜索
2. 搜索后根据结果更新冷却状态
3. 第二轮开始，仅冷却期已到的条目会被重新搜索

观察日志或数据库：
```sql
-- 查看冷却中的条目
SELECT title, search_miss_count, next_search_at 
FROM library_media 
WHERE next_search_at IS NOT NULL 
ORDER BY next_search_at;
```

## 预期效果

以100个缺失条目、每轮限制20个为例：

**优化前**：
- 每轮搜索20个条目
- 假设80%搜不到 → 16个无效请求
- 下一轮继续搜同样的16个

**优化后**：
- 第一轮搜索20个，16个空手进入冷却（1天后才能再搜）
- 第二轮（1天后）搜索另外20个新条目
- 资源较少的站点，有效搜索比例可从20%提升到60%+

## 手动重置冷却（可选）

如果需要强制某个条目立即重新搜索：

```sql
UPDATE library_media 
SET next_search_at = NULL, search_miss_count = 0 
WHERE id = '<media_id>';
```

或批量重置所有：

```sql
UPDATE library_media 
SET next_search_at = NULL, search_miss_count = 0;
```

## 兼容性说明

- 手动触发的搜索（用户点击"搜索资源"）不受冷却限制
- 手动重试失败任务不受冷却限制
- 从NextFind同步新条目时，`next_search_at` 为空，立即可搜
- Dry-run 模式同样记录搜索结果，避免演练期间累积无效条目

## 后续可选优化

1. **动态调整冷却策略**
   - 新发布影视（距离首播 < 30天）缩短冷却时间
   - 冷门地区资源（如日本动画）拉长冷却时间

2. **RSS推送通道**
   - 对进入7天冷却期的条目，转由RSS订阅被动匹配
   - 进一步减少主动搜索频率

3. **站点级别限流**
   - 记录每个PT站点的最后请求时间
   - 跨条目全局控制单站请求频率

4. **前端展示**
   - "缺失影视"页面显示下次搜索时间
   - 提供"立即重搜"按钮（重置冷却）

## 文件清单

- `backend/app/simple/models.py` - 模型字段定义
- `backend/app/simple/automation.py` - 自动化逻辑
- `backend/alembic/versions/20260902_0013_library_search_cooldown.py` - 数据库迁移
- `backend/tests/test_search_cooldown.py` - 单元测试（需要Python 3.12+运行）

## 回滚方案

如需回滚此改动：

```bash
cd backend
uv run alembic downgrade -1
```

然后恢复代码：
```bash
git revert <commit-hash>
```

注意：回滚会删除新增字段，已有的搜索历史数据会丢失。
