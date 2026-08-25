# UNIN Android

UNIN 的原生 Android 客户端，默认连接 `https://unin.tlovex.de/`。项目使用 Kotlin、Jetpack Compose、Retrofit 与 Material 3，不是 WebView 套壳；手机使用底部导航，平板使用侧边导航。

客户端覆盖登录、缺失影视同步、TMDB 确认、PT 候选搜索、qBittorrent 下载状态、自动化策略、服务配置和连接测试。自动搜索以后台运行 ID 轮询进度，切换页面后可继续恢复跟踪。管理员可在 App 内配置 NextFind、TMDB、出站代理、AvistaZ/NexusPHP 与 qBittorrent；敏感字段只用于一次写入，不会由服务端回显，也不会写入 Android 本地状态或日志。

## 本地环境

- JDK 17 或 21
- Android SDK Platform 35
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
D:\project\unin\output\android\UNIN-1.2.1.apk
```

使用 ADB 安装：

```powershell
adb install -r D:\project\unin\output\android\UNIN-1.2.1.apk
```

也可以把 APK 复制到手机后打开安装。首次启动使用 `unin.tlovex.de` 上已有的 UNIN 账号登录；APK 不包含默认账号或管理员密码。

本地分发签名材料保存在仓库根目录下已忽略的 `secrets/unin-android-release.*` 中，并限制为当前 Windows 用户访问。材料存在且属性完整时，`assembleRelease` 会生成已签名的 `app-release.apk`；材料缺失时仍可编译未签名 Release。后续升级必须使用同一密钥；请安全备份密钥和属性文件，切勿提交、上传或发送给他人。正式商店发布应改用正式发布方托管的签名身份。

## 安全约定

- 应用禁止明文 HTTP，只信任系统证书存储。
- 认证偏好与认证数据库不参与系统云备份或设备迁移。
- 会话 Cookie 使用 Android Keystore AES/GCM 加密后持久化，状态变更请求自动携带 CSRF Token。
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
