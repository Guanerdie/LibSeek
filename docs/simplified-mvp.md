# UNIN 简化版 MVP

## 产品目标

UNIN 只解决一个日常问题：把 NextFind 发现的缺失影视，经过 TMDB 识别和 PT
搜索后，提交到 qBittorrent 并展示下载状态。

用户看到的主流程固定为：

```text
发现缺失 -> 选择资源 -> 查看下载
```

TMDB 识别、PT 搜索、候选评分、qBittorrent 提交和状态同步是这三个动作背后的
实现细节，不再各自占用一个控制面页面。

## 页面

MVP 只有四个一级入口：

1. `缺失`：NextFind 同步结果、TMDB 身份和缺集信息。
2. `资源`：单个影视的搜索状态与候选列表；普通候选可直接下载，有警告时一次确认。
3. `下载`：qBittorrent 任务进度、速度、Ratio、错误和完成状态。
4. `设置`：NextFind、TMDB、PT 和 qBittorrent 连接配置。

认证和 CSRF 继续作为应用边界保留，但审批、计划、执行和自动化决策不再作为独立
用户流程。

## 领域模型

业务数据缩减为五个核心实体：

- `MediaItem`：一部电影或一部电视剧，也是日常操作的聚合根。
- `Episode`：电视剧期望集和本地状态。
- `Search`：一次面向一个或多个 PT 站点的搜索。
- `ReleaseCandidate`：可选择的发布资源及其解释性评分和警告。
- `Download`：一次 qBittorrent 提交及后续状态。

`ActivityLog` 只记录便于用户排错的关键事件，不参与业务决策。

## 状态

`MediaItem` 使用面向用户的少量状态：

```text
MISSING -> IDENTIFYING -> READY -> SEARCHING -> CANDIDATES -> DOWNLOADING -> COMPLETE
                    \-> NEEDS_ATTENTION <-/
```

状态表示下一步用户可以做什么，不映射内部进程或锁状态。

`Search` 只有 `PENDING / RUNNING / SUCCEEDED / FAILED`。

`Download` 只有 `SUBMITTING / QUEUED / DOWNLOADING / PAUSED / SEEDING /
COMPLETED / ERROR / OUTCOME_UNKNOWN`。`OUTCOME_UNKNOWN` 仅用于 qBittorrent 写请求超时后
无法判断是否已经添加的具体场景；此时必须按 torrent info hash 读取对账，普通重试会有
重复提交风险。

## 数据完整性原则

默认只使用普通手段：

- 外键和唯一约束保证引用及防重复。
- 非负数值使用简单检查约束。
- Alembic 管理数据库版本。
- 单元测试和 API 测试覆盖状态转换。
- Git 保存历史和回退点。

不为实体添加快照哈希、前向哈希链、冻结 contract、baseline 或发布 gate。

唯一保留的内容哈希是 torrent 自身的 info hash。它是 BitTorrent 与 qBittorrent 的资源
身份，不是额外设计的审计门禁；在网络超时后也需要用它确认写入结果。

## 部署

目标形态为模块化单体：一个应用进程承载 API 和轻量后台任务。默认数据库为 SQLite，
PostgreSQL 是可选部署；不要求 Redis、Celery 或消息队列。

0.9 使用一条全新的初始迁移，只创建上述简化实体。旧 0.8 数据库不会自动原地升级；
升级时保留旧数据库备份，并让 0.9 创建新的 SQLite 数据库。旧实现和旧迁移继续由 Git
历史保存，不在新运行时注册。

## MVP 范围外

- 独立审批、下载计划和执行记录
- 批次与定时启动
- 媒体整理、重命名、硬链接和入库
- 策略修订、决策哈希链和能力心跳
- 插件市场、AI Agent、RSS/IRC 抢种
- 自动质量升级和通知渠道矩阵
