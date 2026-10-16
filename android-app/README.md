# UNIN Android

UNIN 的原生 Android 客户端，默认连接 `https://unin.tlovex.de/`。项目使用 Kotlin、Jetpack Compose、Retrofit 与 Material 3，不是 WebView 套壳；手机使用底部导航，平板使用侧边导航。

客户端覆盖登录（可选「记住这台设备」）、修改登录密码、缺失影视同步、批量导入、TMDB 确认、PT 候选搜索、追更订阅、一键补片、qBittorrent 下载状态、空间清理、操作记录、自动化策略与监控摘要、服务配置和连接测试。长列表在手机和平板上按页显示，并在切页后自动回到列表顶部；自动搜索以后台运行 ID 轮询进度，切换页面后可继续恢复跟踪。管理员可在 App 内配置 NextFind、TMDB、出站代理、AvistaZ/NexusPHP 与 qBittorrent；敏感字段只用于一次写入，不会由服务端回显，也不会写入 Android 本地状态或日志。

## 1.5.0 新增

与网页端对齐：

- **空间清理**：「下载」页的第二个标签。显示当前状态、待删除和保留中的种子；可以修改清理设置、逐个保留或放行、勾选多个存量一次放行。开启真正删除或「自动放行存量」、批量放行前都要再次确认。清理设置只提交 `cleanup_*` 字段，不会改动自动化策略的其他设置。
- **下载任务**：每个任务显示清理状态，未标记的任务可以直接切换「保留」。
- **操作记录**：底部导航「记录」。按类别筛选，显示时间、操作人、手动还是自动、原因，点开查看体积、做种天数、策略改动前后等详情。
- **批量导入**：「缺失」页右上角。贴清单 → 匹配 → 选择有歧义的条目、勾选库中已有的 → 开始下载；离开页面后仍会跟踪进度，结束时提示结果。
- **自动化**：策略摘要显示年份范围。
- 修正：服务端返回的时间不带时区（实为 UTC），旧版按本地时间显示，所有时间早 8 小时。

## 本地环境

- JDK 21（Gradle 9 在 JDK 25 上无法运行）
- Android SDK Platform 37（compileSdk；targetSdk 仍为 35）
- Android SDK Build Tools 35.0.0

在 `android-app` 目录配置本机 SDK（`local.properties` 不提交）：

```properties
sdk.dir=D\:\\android-toolchain\\sdk
```

## 构建

Windows PowerShell：

```powershell
./gradlew.bat assembleDebug
./gradlew.bat testDebugUnitTest
```

Debug APK 输出到 `app/build/outputs/apk/debug/app-debug.apk`。

可直接安装的本地分发包输出到：

```text
D:\project\unin\output\android\UNIN-1.5.0.apk
```

使用 ADB 安装：

```powershell
adb install -r D:\project\unin\output\android\UNIN-1.5.0.apk
```

也可以把 APK 复制到手机后打开安装。首次启动使用 `unin.tlovex.de` 上已有的 UNIN 账号登录；APK 不包含默认账号或管理员密码。

本地分发签名材料保存在仓库根目录下已忽略的 `secrets/unin-android-release.*` 中，并限制为当前 Windows 用户访问。材料存在且属性完整时，`assembleRelease` 会生成已签名的 `app-release.apk`；材料缺失时仍可编译未签名 Release。后续升级必须使用同一密钥；请安全备份密钥和属性文件，切勿提交、上传或发送给他人。正式商店发布应改用正式发布方托管的签名身份。

## 安全约定

- 应用禁止明文 HTTP，只信任系统证书存储。
- 认证偏好与认证数据库不参与系统云备份或设备迁移。
- 会话 Cookie 使用 Android Keystore AES/GCM 加密后持久化，状态变更请求自动携带 CSRF Token。
- 登录默认 8 小时；勾选「记住这台设备」后由服务端签发 30 天会话，不勾选或旧客户端仍为 8 小时。
- 修改登录密码必须输入当前密码；成功后服务端轮换会话签名密钥，其他设备需要重新登录，当前设备换发新的会话凭据。密码只作为请求参数传递，不写入界面状态。
- 服务端地址通过 `BuildConfig.BASE_URL` 提供；账号、Cookie、Token 与第三方服务密码不会编译进 APK。
- 第三方秘密只保存在服务端；App 不回填密码、Token、PID、Cookie 或 Passkey，留空表示保留同一服务身份下的现有值。
- 修改服务地址、用户名或站点身份时，App 会要求同时输入新的对应秘密；停用出站代理会在确认后清除服务端代理凭据。
- 带风险提示的候选资源在提交 qBittorrent 前需要再次确认。
- 关闭 Dry-run 的实时自动化在保存和手动运行前需要再次确认；服务端既有写入开关和预算限制仍然生效。

## 验证

当前版本使用普通单元测试与 Android Lint 验证，不使用自定义 hash、冻结 contract、baseline 或发布 gate：

```powershell
./gradlew.bat testDebugUnitTest lintRelease assembleRelease
```

签名 APK 已通过 Android Build Tools 的 `apksigner verify`、16 KiB page-aware `zipalign -c` 与 `aapt dump badging` 检查。
