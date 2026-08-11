# 多 PT 站点扩展边界

当前生产注册表只包含 `avistaz`。仓库中的 `NexusPhpAdapter` 是一个默认关闭的扩展骨架，
不代表已经支持任何国内 PT 站点，也不会根据“看起来像 NexusPHP”自动尝试登录或解析。

## 接入一个指定站点前必须完成

1. 用户明确指定站点，并确认其使用规则允许这种只读访问。
2. 为该站点创建独立 `NexusPhpSiteProfile`。Profile 只描述 HTTPS origin、同源路径、
   分类、查询字段和 CSS selector，不包含 Cookie、passkey、用户名或密码。
3. 从用户自行保存的页面建立脱敏 HTML fixture，覆盖正常搜索、空结果、字段缺失、
   登录页、验证码页和浏览器挑战页。
4. fixture 契约和安全测试通过后，才可把 Profile 显式注册；Profile 的 `enabled` 默认值
   必须继续为 `false`，实时搜索和种子获取还要分别经过运行时开关。
5. Cookie 和 passkey 只能作为运行时构造参数进入适配器内存。不得放进 Profile、数据库、
   API 响应、审计详情、日志、前端存储或 Git 文件。
6. 实时工厂必须为每个 `site_id` 注入基于 PostgreSQL advisory lock 的跨 Worker request
   gate，并设置保守的最小请求间隔；缺少 request gate 时适配器拒绝启用实时能力。

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

- `site_id` 是小写短标识；搜索 run、job payload、job type 和审计事件均绑定同一个值。
- Worker 只从注册表按 `run.site_id` 取适配器。未知、未启用、绑定不一致或候选站点不一致
  都会失败关闭，不会回退到 AvistaZ。
- base URL 必须是无凭据的 HTTPS origin；搜索、详情、下载和所有重定向必须严格同源。
- 每个 fallback 请求都经过站点 request gate 和进程内串行限速；`Retry-After` 会按最多一小时
  的安全上限参与 Worker 的下一次重试时间，避免快速重试风暴。
- 登录页、验证码或浏览器挑战只返回稳定错误，不尝试模拟人工操作、绕过验证码或规避反爬。
- 候选记录只保留站点 ID、种子 ID 和不可逆的内部详情引用，不保留详情/下载 URL。
- H&R 在这个通用骨架中始终为未知，不能据此自动选种、自动审批或自动执行。
- 种子获取默认关闭，并且只能获取当前内存会话刚刚搜索到的精确种子 ID。

## 新站点验收

至少应运行：

```powershell
Set-Location D:\project\unin\backend
uv run pytest tests/test_pt_site_extensibility.py
uv run ruff check app tests
uv run mypy app
```

这些检查只证明本地 Profile/fixture 契约，不证明真实站点登录、页面结构或下载能力可用。
