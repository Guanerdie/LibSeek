# 多 PT 站点扩展边界

当前生产 `PtSiteCatalog`、`PtSiteRegistry` 和 `PtExecutionRegistry` 都只接入
`avistaz`。仓库中的 `NexusPhpAdapter` 是一个默认关闭的扩展骨架，测试中的
`synthetic-two` 是纯内存适配器；两者都不代表已经支持任何国内 PT 站点，也不会根据
“看起来像 NexusPHP”自动尝试登录、解析或取种。

## 四层接线模型

新站点不是向一个注册表增加一个类即可完成。接线分为四层，并且每层都按同一个
`site_id` 失败关闭：

1. `NexusPhpSiteProfile`：无 Secret 的站点解析配置，包含 HTTPS origin、同源路径、
   分类/查询映射和 CSS selector。Profile 不对外公开，也不保存 Cookie、passkey、用户名
   或密码。
2. `PtSiteCatalog`：API、普通 Worker 和下载执行器共享的不可变能力目录。由 Profile
   生成的目录声明只保留显示信息、可用性、搜索模式、媒体类型、`manual_only`、促销、
   H&R 和取种能力标记，不包含 origin 或任何 Secret。
3. `PtSiteRegistry`：普通 Worker 使用的只读搜索工厂注册表。工厂身份和 manifest
   搜索能力必须覆盖目录声明，否则在搜索前失败关闭。
4. `PtExecutionRegistry`：下载执行器使用的独立取种工厂注册表。只有目录明确声明
   `torrent_fetch_enabled=true`，且执行工厂 manifest 也声明匹配的取种能力时才能创建；
   search-only 站点不会因此获得执行能力。

`GET /api/pt-sites/catalog` 是前端唯一需要读取的站点目录接口。它只有 `GET`，
`default_site_id` 只是界面默认选择提示。`POST /api/media/{id}/torrent-searches` 仍必须
显式提交 `site_id`；服务端不会从默认值补写，也不会在任一层回退到 AvistaZ。

## 接入一个指定站点前必须完成

1. 用户明确指定站点，并确认其使用规则允许所计划的访问；只读搜索、详情读取、种子获取
   和下载器提交是不同授权范围，不能相互推导。
2. 为该站点创建独立 `NexusPhpSiteProfile`。Profile 只描述 HTTPS origin、同源路径、
   分类、查询字段和 CSS selector，不包含 Cookie、passkey、用户名或密码。
3. 从用户自行保存的页面建立脱敏 HTML fixture，覆盖正常搜索、空结果、字段缺失、
   登录页、验证码页和浏览器挑战页。
4. 由 Profile 生成无 Secret 的 `PtSiteDeclaration`，并把它加入 API、普通 Worker 和执行器
   共同使用的同一份 Catalog。Profile 的 `enabled` 默认值必须继续为 `false`；
   `runtime_ready` 只表示所需运行时输入是否齐全，不公开输入内容。
5. fixture 契约、安全测试和目录声明通过后，才可向 `PtSiteRegistry` 显式注册该站点的搜索
   工厂，并检查 Catalog 中所有可搜索站点都有且只有匹配的工厂。公开 API 不维护另一份
   站点 allowlist。
6. 如果只需要候选搜索，保持 `torrent_fetch_enabled=false`，不要注册执行工厂。如果后续需要
   取种，必须另行实现并验证执行适配器、精确 torrent ID 重绑定、`.torrent` 校验和该站点
   的运行时开关，再向 `PtExecutionRegistry` 显式注册；这一步需要用户对真实取种单独授权。
7. Cookie 和 passkey 只能作为运行时构造参数进入适配器内存。不得放进 Profile、Catalog、
   数据库、API 响应、审计详情、日志、前端存储或 Git 文件。
8. 实时搜索工厂必须为每个 `site_id` 注入基于 PostgreSQL advisory lock 的跨 Worker request
   gate，并设置保守的最小请求间隔；缺少 request gate 时适配器拒绝启用实时能力。
   执行器还会为 PT 与 qB 适配器安装逐请求状态 guard。

下面只是无真实站点含义的结构示例：

```python
NexusPhpSiteProfile(
    site_id="example-nexus",
    display_name="Example fixture only",
    enabled=False,
    base_url="https://tracker.example.invalid",
    search_path="/torrents.php",
    download_path="/download.php?id={torrent_id}&passkey={passkey}",
    category_mapping={MediaType.MOVIE: ("401",), MediaType.TV: ("402",)},
    selectors=NexusPhpSelectors(
        row="#torrents tr.torrent",
        details_link="a.details",
        title=".title",
        size=".size",
        seeders=".seeders",
    ),
)
```

## 固定安全行为

- `site_id` 是小写短标识；搜索请求、run、job payload、job type、候选、审批、下载计划、
  执行和审计事件均绑定同一个值。
- API 先从 Catalog 校验站点存在、搜索已启用、运行时已就绪并支持当前媒体类型，再创建
  数据库记录。缺失 `site_id` 由 schema 拒绝，未知或不可用站点不会产生 run/job/audit 写入。
- Worker 只从 `PtSiteRegistry` 按 `run.site_id` 取搜索适配器；执行器只从
  `PtExecutionRegistry` 按不可变计划 `site_id` 取执行适配器。未知、未启用、能力不足、
  绑定不一致或候选站点不一致都会失败关闭，不会回退到 AvistaZ 或其他站点。
- base URL 必须是无凭据的 HTTPS origin；搜索、详情、下载和所有重定向必须严格同源。
- 每个 fallback 请求都经过站点 request gate 和进程内串行限速；`Retry-After` 会按最多一小时
  的安全上限参与 Worker 的下一次重试时间，避免快速重试风暴。
- 登录页、验证码或浏览器挑战只返回稳定错误，不尝试模拟人工操作、绕过验证码或规避反爬。
- 候选记录只保留站点 ID、受限字符集的短种子 ID 和与站点前缀匹配的内部详情引用，
  不保留详情/下载 URL。
- H&R 在这个通用骨架中始终为未知，不能据此自动选种、自动审批或自动执行。
- 种子获取默认关闭，并且只能获取当前内存会话刚刚搜索到的精确种子 ID。

生产 AvistaZ 的 Catalog 项为 `manual_only=false`，因为现有代码具备受阶段 6 策略、总闸、
资格检查、readiness 和执行能力开关共同约束的自动化路径。这不表示默认自动化、实时搜索、
取种或 qB 写入已启用。通用 NexusPHP Profile 仍为 `manual_only=true`、
`torrent_fetch_enabled=false`。

## 新站点验收

至少应运行：

```powershell
Set-Location D:\project\unin\backend
uv run pytest tests/test_pt_site_catalog.py tests/test_pt_site_extensibility.py tests/test_download_executor.py -q
uv run ruff check app tests
uv run mypy app
```

其中 `synthetic-two` 只用合成 Catalog 和内存工厂离线贯通目录 API、显式站点选择、
run/job、Worker、候选和待人工审批快照，并在执行工厂前被取种能力闸门阻断；独立的
`DownloadExecutor.run_once()` search-only 合成测试还验证 PT 与 qB 工厂均不会创建。这条
离线覆盖不会解析 DNS、访问网络、读取真实凭据、生成下载计划或执行下载。

这些检查只证明目录/双注册表路由、安全闸门及本地 Profile/fixture 契约。目前没有连接真实
TMDB、AvistaZ、qBittorrent 或任何国内 PT 站点进行端到端验证；在用户确认具体目标、动作、
读取/写入范围和影响，并通过本机运行时 Secret 安全录入所需信息前，不得把新站点标记为
生产可用，也不得进行真实取种或下载。
