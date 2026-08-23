# 本地运行时 Secret

此目录只保存本机运行时 Secret。`*.txt` 已被 `.gitignore` 排除，但仍是明文文件；不要提交、同步、截图或把内容粘贴到聊天。

## Windows 首选：一次性可视化向导

在项目根目录运行：

```powershell
Set-Location D:\project\unin
.\scripts\setup.ps1
```

浏览器会自动打开一次性 localhost 向导，可依次录入本地账号/NextFind/TMDB、AvistaZ 和 qBittorrent。密码永不预填或回显；默认拒绝覆盖已有 Secret，需要替换时使用 `.\scripts\setup.ps1 -ReplaceExisting`。保存不会测试真实连接或启动 Docker；它只开启已录入服务的只读能力，下载写入、自动化和下载监控仍保持关闭。

录入完成后回到本项目/Codex继续。每次真实读取 NextFind、TMDB、AvistaZ 或 qBittorrent，以及任何 qB 写入前，我都会先说明目标、读写对象和可能影响，并等待你的明确批准。

Secret 按阶段分为三组。只创建并叠加当前获准阶段，不需要为尚未启用的外部服务创建占位文件：

```text
# deploy/compose.secrets.discovery.yaml.example（第一轮只读发现，6 个）
auth_local_username.txt
auth_local_password.txt
auth_session_signing_key.txt
nextfind_username.txt
nextfind_password.txt
tmdb_access_token.txt

# deploy/compose.secrets.avistaz.yaml.example（AvistaZ，3 个）
avistaz_username.txt
avistaz_password.txt
avistaz_pid.txt

# deploy/compose.secrets.qb.yaml.example（qBittorrent，3 个）
qb_base_url.txt
qb_username.txt
qb_password.txt
```

三份 override 可以按顺序叠加；每份只声明本组文件。现有 `deploy/compose.secrets.yaml.example` 是全部 12 个 Secret 的兼容入口，只有 12 个文件均已安全录入时才使用。不要同时叠加全量入口和分层入口；不要在未获授权的阶段提前创建凭据，也不要用空文件或虚假值满足启动检查。override 本身不会开启任何实时能力开关或可选 profile。

每个文件只写实际值，不加引号、不写 `KEY=`。`auth_session_signing_key.txt` 必须至少 32 个字符，并应使用独立的高熵随机值；它不是登录密码。

Secret 可见范围按已叠加层累加，服务边界固定如下：

| 层 | 可见服务 |
|---|---|
| discovery 6 个 | API=6；普通 Worker=NextFind 2 个 + TMDB 1 个；其他服务和前端=0 |
| AvistaZ 3 个 | API、普通 Worker、下载执行器各增加这 3 个 |
| qB 3 个 | API、`automation-preflight`、下载执行器、只读监控器各增加这 3 个 |

三层全部叠加后的数量为 API=12、普通 Worker=6、`automation-preflight`=3、下载执行器=6、只读监控器=3、前端=0。普通 Worker 永远没有 qB Secret；`automation-preflight` 永远没有 NextFind、TMDB 或 AvistaZ Secret。

qBittorrent SID 只保存在对应适配器进程内存中，不需要也不得创建 SID Secret 文件。`automation-preflight` 不持有 NextFind、TMDB 或 AvistaZ Secret；它只能通过只读适配器执行自动审批预检。readiness 心跳只含非敏感配置指纹和实例 ID，不是 Secret。

Windows 首选使用上面的可视化向导；向导不可用时，才使用项目 README 中标为“手工备用”的 `Read-Host -AsSecureString` 片段创建文件并收紧 ACL。Linux 上建议由部署系统提供 Docker Secret；若使用本地文件，至少执行 `chmod 600 secrets/*.txt` 并限制目录访问。

只做 Compose 配置解析时不需要创建这些文件；Compose 配置解析成功不代表 Secret 已就绪。启动前只检查本次命令所叠加层的文件，不得用占位凭据启动服务或真实连接。停止使用外部连接后应安全移除对应外部服务 Secret，并把 `ENABLE_TMDB_LIVE`、`ENABLE_AVISTAZ_LIVE_SEARCH`、`ENABLE_QB_READ_ONLY`、`ENABLE_AUTOMATION_ENGINE`、`ENABLE_DOWNLOAD_EXECUTOR`、`ENABLE_AVISTAZ_TORRENT_FETCH`、`ENABLE_QB_WRITE` 和 `ENABLE_DOWNLOAD_MONITOR` 恢复为 `false`。认证三个 Secret 缺失时，受保护 API 会 fail closed，而不是退回匿名访问。

## Android 本地分发签名

Android APK 的本地分发签名使用以下两个文件，它们不会提供给后端容器，也不会进入 Git：

```text
unin-android-release.jks
unin-android-release.properties
```

属性文件含签名口令并按明文 Secret 处理；两个文件当前都限制为本机 Windows 用户访问。后续向已安装应用提供升级包时必须继续使用同一密钥，因此应把二者一起备份到受控的密码库或加密介质。不要截图、同步到普通网盘、提交到 Git 或打包进 APK。正式应用商店发布应由发布方另行托管正式签名身份。
