package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Logout
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Security
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.ConnectionState
import de.tlovex.unin.ui.ConnectionUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientMark
import de.tlovex.unin.ui.components.GradientPrimaryButton
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninWarning
import java.net.URI
import java.util.Locale

@Composable
fun SettingsScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
    configurationState: SettingsConfigurationUiState = SettingsConfigurationUiState(),
    configurationCallbacks: SettingsConfigurationCallbacks = SettingsConfigurationCallbacks(),
) {
    var editor by remember { mutableStateOf<ConfigurationEditor?>(null) }

    LazyColumn(
        modifier = modifier,
        contentPadding = contentPadding,
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item { SectionHeading("设置与连接", subtitle = "检查 UNIN 后端及外部服务状态") }
        item {
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Row(Modifier.padding(20.dp), verticalAlignment = Alignment.CenterVertically) {
                    GradientMark(Icons.Outlined.CloudDone, null)
                    Spacer(Modifier.size(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(state.accountDisplayName.ifBlank { "UNIN 用户" }, style = MaterialTheme.typography.titleMedium)
                        Text(state.serverLabel, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    OutlinedButton(onClick = callbacks.onLogout, modifier = Modifier.height(48.dp)) {
                        Icon(Icons.AutoMirrored.Outlined.Logout, null)
                        Spacer(Modifier.size(7.dp))
                        Text("退出")
                    }
                }
            }
        }
        item { Text("服务连接", style = MaterialTheme.typography.titleLarge) }
        items(state.connections, key = { it.key }) { connection ->
            ConnectionCard(connection, callbacks, Modifier.fillMaxWidth())
        }

        item {
            SectionHeading(
                title = "服务配置",
                subtitle = if (configurationState.canEdit) {
                    "敏感字段只允许替换，不会读取或显示服务端现有值"
                } else {
                    "仅管理员可以修改；第三方凭据始终保存在 UNIN 服务端"
                },
            )
        }
        if (configurationState.canEdit) {
            item {
                ConfigurationEntryCard(
                    title = "NextFind",
                    description = configuredDescription(
                        configurationState.nextFind.configured,
                        configurationState.nextFind.passwordConfigured,
                    ),
                    configured = configurationState.nextFind.configured,
                    onEdit = { editor = ConfigurationEditor.NextFind },
                )
            }
            configurationEditorItem(editor, ConfigurationEditor.NextFind, configurationState, configurationCallbacks) {
                editor = null
            }
            item {
                ConfigurationEntryCard(
                    title = "TMDB",
                    description = if (configurationState.tmdb.tokenConfigured) "访问令牌已配置，可在此替换" else "尚未配置访问令牌",
                    configured = configurationState.tmdb.tokenConfigured,
                    onEdit = { editor = ConfigurationEditor.Tmdb },
                )
            }
            configurationEditorItem(editor, ConfigurationEditor.Tmdb, configurationState, configurationCallbacks) {
                editor = null
            }
            item {
                ConfigurationEntryCard(
                    title = "出站代理",
                    description = if (configurationState.outboundProxy.configured) {
                        "NextFind、TMDB 与 PT 请求将通过服务端代理"
                    } else {
                        "可选；当前未启用"
                    },
                    configured = configurationState.outboundProxy.configured,
                    onEdit = { editor = ConfigurationEditor.OutboundProxy },
                )
            }
            configurationEditorItem(editor, ConfigurationEditor.OutboundProxy, configurationState, configurationCallbacks) {
                editor = null
            }
            item {
                val activeName = if (configurationState.activePtArchitecture == PtArchitectureUi.AvistaZ) "AvistaZ" else "NexusPHP"
                ConfigurationEntryCard(
                    title = "PT 站点",
                    description = "当前架构：$activeName；可分别维护已有架构",
                    configured = if (configurationState.activePtArchitecture == PtArchitectureUi.AvistaZ) {
                        configurationState.avistaZ.configured
                    } else {
                        configurationState.nexusPhp.configured
                    },
                    onEdit = { editor = ConfigurationEditor.PtSite },
                )
            }
            configurationEditorItem(editor, ConfigurationEditor.PtSite, configurationState, configurationCallbacks) {
                editor = null
            }
            item {
                ConfigurationEntryCard(
                    title = "qBittorrent",
                    description = configuredDescription(
                        configurationState.qbittorrent.configured,
                        configurationState.qbittorrent.passwordConfigured,
                    ),
                    configured = configurationState.qbittorrent.configured,
                    onEdit = { editor = ConfigurationEditor.Qbittorrent },
                )
            }
            configurationEditorItem(editor, ConfigurationEditor.Qbittorrent, configurationState, configurationCallbacks) {
                editor = null
            }
        }

        item {
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    Text("数据与安全", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "登录凭据仅用于当前 UNIN 服务；TMDB、PT 与 qBittorrent 密钥由服务端管理，不会编译进 APK。配置页只显示“已配置”状态，留空的敏感字段会保留服务端现有值。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

private enum class ConfigurationEditor { NextFind, Tmdb, OutboundProxy, PtSite, Qbittorrent }

private fun LazyListScope.configurationEditorItem(
    selected: ConfigurationEditor?,
    editor: ConfigurationEditor,
    state: SettingsConfigurationUiState,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    if (selected != editor) return
    item(key = "configuration-editor-$editor") {
        ConfigurationEditor(
            editor = editor,
            state = state,
            callbacks = callbacks,
            onClose = onClose,
        )
    }
}

@Composable
private fun ConfigurationEditor(
    editor: ConfigurationEditor,
    state: SettingsConfigurationUiState,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    when (editor) {
        ConfigurationEditor.NextFind -> NextFindEditor(state.nextFind, state.isSaving, callbacks, onClose)
        ConfigurationEditor.Tmdb -> TmdbEditor(state.tmdb, state.isSaving, callbacks, onClose)
        ConfigurationEditor.OutboundProxy -> OutboundProxyEditor(
            state.outboundProxy,
            state.isSaving,
            callbacks,
            onClose,
        )
        ConfigurationEditor.PtSite -> PtSiteEditor(state, callbacks, onClose)
        ConfigurationEditor.Qbittorrent -> QbittorrentEditor(state.qbittorrent, state.isSaving, callbacks, onClose)
    }
}

@Composable
private fun NextFindEditor(
    seed: NextFindConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var baseUrl by remember(seed) { mutableStateOf(seed.baseUrl) }
    var username by remember(seed) { mutableStateOf(seed.username) }
    var password by remember(seed) { mutableStateOf("") }
    val trimmedBaseUrl = baseUrl.trim().trimEnd('/')
    val trimmedUsername = username.trim()
    val identityChanged = trimmedBaseUrl != seed.baseUrl || trimmedUsername != seed.username
    val passwordRequired = !seed.passwordConfigured || identityChanged
    val valid = isHttpsOrigin(trimmedBaseUrl) && trimmedUsername.isNotEmpty() &&
        (!passwordRequired || password.isNotBlank())

    ConfigurationForm("配置 NextFind", "地址和用户名可读取；密码永远不会回填。", onClose) {
        UrlField(baseUrl, { baseUrl = it.take(MAX_URL_LENGTH) }, "NextFind HTTPS 地址")
        PlainField(username, { username = it.take(120) }, "用户名")
        SecretField(
            password,
            { password = it.take(MAX_SECRET_LENGTH) },
            "密码",
            seed.passwordConfigured && !identityChanged,
        )
        SaveButton("保存 NextFind", valid, saving) {
            val replacement = password.takeIf(String::isNotBlank)
            password = ""
            onClose()
            callbacks.onSaveNextFind(trimmedBaseUrl, trimmedUsername, replacement)
        }
    }
}

@Composable
private fun TmdbEditor(
    seed: TmdbConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var token by remember(seed) { mutableStateOf("") }
    ConfigurationForm("配置 TMDB", "访问令牌只用于本次替换，不会从服务端读取。", onClose) {
        SecretField(
            token,
            { token = it.take(MAX_SECRET_LENGTH) },
            "TMDB Access Token",
            seed.tokenConfigured,
        )
        SaveButton("替换 TMDB 令牌", token.isNotBlank(), saving) {
            val replacement = token
            token = ""
            onClose()
            callbacks.onSaveTmdb(replacement)
        }
    }
}

@Composable
private fun OutboundProxyEditor(
    seed: OutboundProxyConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var url by remember(seed) { mutableStateOf(seed.url) }
    var username by remember(seed) { mutableStateOf(seed.username) }
    var password by remember(seed) { mutableStateOf("") }
    var confirmDisable by remember { mutableStateOf(false) }
    val trimmedUrl = url.trim().trimEnd('/')
    val trimmedUsername = username.trim()
    val identityChanged = trimmedUrl != seed.url || trimmedUsername != seed.username
    val passwordRequired = trimmedUsername.isNotEmpty() &&
        (!seed.passwordConfigured || identityChanged)
    val validCredentials = when {
        trimmedUrl.isEmpty() -> trimmedUsername.isEmpty() && password.isEmpty()
        trimmedUsername.isEmpty() -> password.isEmpty()
        else -> !passwordRequired || password.isNotBlank()
    }
    val valid = (trimmedUrl.isEmpty() || isProxyUrl(trimmedUrl)) && validCredentials

    fun submit() {
        val replacement = password.takeIf(String::isNotBlank)
        password = ""
        onClose()
        callbacks.onSaveOutboundProxy(
            trimmedUrl,
            if (trimmedUrl.isEmpty()) "" else trimmedUsername,
            replacement,
        )
    }

    ConfigurationForm(
        "配置出站代理",
        "仅影响 NextFind、TMDB 与 PT 的服务端外部请求，不影响 qBittorrent。留空地址可停用代理。",
        onClose,
    ) {
        ProxyUrlField(url, { url = it }, "HTTP 或 HTTPS 代理地址（可选）")
        PlainField(username, { username = it.take(120) }, "用户名（可选）")
        SecretField(
            password,
            { password = it.take(MAX_SECRET_LENGTH) },
            "密码（可选）",
            seed.passwordConfigured && !identityChanged,
            required = passwordRequired,
        )
        if (trimmedUsername.isEmpty()) {
            Text(
                "不需要代理认证时，请同时留空用户名和密码。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        SaveButton(if (trimmedUrl.isEmpty()) "停用出站代理" else "保存出站代理", valid, saving) {
            if (seed.configured && trimmedUrl.isEmpty()) confirmDisable = true else submit()
        }
    }

    if (confirmDisable) {
        AlertDialog(
            onDismissRequest = { confirmDisable = false },
            title = { Text("停用出站代理？") },
            text = { Text("保存后服务端会清除已存的代理地址、用户名和密码。恢复时需要重新输入。") },
            confirmButton = {
                TextButton(onClick = { confirmDisable = false; submit() }) {
                    Text("停用并清除", color = UninDanger)
                }
            },
            dismissButton = { TextButton(onClick = { confirmDisable = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun QbittorrentEditor(
    seed: QbittorrentConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var baseUrl by remember(seed) { mutableStateOf(seed.baseUrl) }
    var username by remember(seed) { mutableStateOf(seed.username) }
    var password by remember(seed) { mutableStateOf("") }
    var savePath by remember(seed) { mutableStateOf(seed.savePath) }
    var category by remember(seed) { mutableStateOf(seed.category) }
    var allowInsecureHttp by remember(seed) { mutableStateOf(seed.allowInsecureHttp) }
    var confirmInsecure by remember { mutableStateOf(false) }
    val trimmedBaseUrl = baseUrl.trim()
    val trimmedUsername = username.trim()
    val identityChanged = trimmedBaseUrl != seed.baseUrl || trimmedUsername != seed.username
    val passwordRequired = !seed.passwordConfigured || identityChanged
    val valid = isQbUrl(trimmedBaseUrl, allowInsecureHttp) && trimmedUsername.isNotEmpty() &&
        (!passwordRequired || password.isNotBlank())

    fun submit() {
        val replacement = password.takeIf(String::isNotBlank)
        password = ""
        onClose()
        callbacks.onSaveQbittorrent(
            trimmedBaseUrl, trimmedUsername, replacement, savePath.trim(), category.trim(), allowInsecureHttp,
        )
    }

    ConfigurationForm("配置 qBittorrent", "这是 UNIN 服务端到 qBittorrent 的连接，不会把 qB 凭据保存在 APK 中。", onClose) {
        UrlField(baseUrl, { baseUrl = it.take(MAX_URL_LENGTH) }, "qBittorrent 地址", allowHttp = allowInsecureHttp)
        PlainField(username, { username = it.take(120) }, "用户名")
        SecretField(
            password,
            { password = it.take(MAX_SECRET_LENGTH) },
            "密码",
            seed.passwordConfigured && !identityChanged,
        )
        PlainField(savePath, { savePath = it.take(4096) }, "保存路径（可选）")
        PlainField(category, { category = it.take(300) }, "分类（可选）")
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("允许服务端使用 HTTP", style = MaterialTheme.typography.titleSmall)
                Text("仅适用于受信任内网；公网或不可信网络必须使用 HTTPS。", color = UninDanger, style = MaterialTheme.typography.bodySmall)
            }
            Switch(checked = allowInsecureHttp, onCheckedChange = { allowInsecureHttp = it })
        }
        SaveButton("保存 qBittorrent", valid, saving) {
            if (allowInsecureHttp && runCatching { URI(trimmedBaseUrl).scheme.equals("http", ignoreCase = true) }.getOrDefault(false)) {
                confirmInsecure = true
            } else {
                submit()
            }
        }
    }

    if (confirmInsecure) {
        AlertDialog(
            onDismissRequest = { confirmInsecure = false },
            title = { Text("确认使用明文 HTTP？") },
            text = { Text("UNIN 服务端与 qBittorrent 之间的登录信息可能被同一网络中的其他设备窃听。仅在隔离且受信任的内网中继续。") },
            confirmButton = {
                TextButton(onClick = { confirmInsecure = false; submit() }) { Text("确认风险并保存", color = UninDanger) }
            },
            dismissButton = { TextButton(onClick = { confirmInsecure = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun PtSiteEditor(
    state: SettingsConfigurationUiState,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var architecture by remember { mutableStateOf(state.activePtArchitecture) }
    ConfigurationForm("配置 PT 站点", "选择架构后保存会把它设为当前活动站点；敏感字段不会回填。", onClose) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            FilterChip(
                selected = architecture == PtArchitectureUi.AvistaZ,
                onClick = { architecture = PtArchitectureUi.AvistaZ },
                label = { Text("AvistaZ") },
            )
            FilterChip(
                selected = architecture == PtArchitectureUi.NexusPhp,
                onClick = { architecture = PtArchitectureUi.NexusPhp },
                label = { Text("NexusPHP") },
            )
        }
        if (architecture == PtArchitectureUi.AvistaZ) {
            AvistaZEditorFields(state.avistaZ, state.isSaving, callbacks, onClose)
        } else {
            NexusPhpEditorFields(state.nexusPhp, state.isSaving, callbacks, onClose)
        }
    }
}

@Composable
private fun AvistaZEditorFields(
    seed: AvistaZConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var baseUrl by remember(seed) { mutableStateOf(seed.baseUrl) }
    var username by remember(seed) { mutableStateOf(seed.username) }
    var password by remember(seed) { mutableStateOf("") }
    var pid by remember(seed) { mutableStateOf("") }
    val trimmedBaseUrl = baseUrl.trim().trimEnd('/')
    val trimmedUsername = username.trim()
    val identityChanged = trimmedBaseUrl != seed.baseUrl || trimmedUsername != seed.username
    val passwordRequired = !seed.passwordConfigured || identityChanged
    val pidRequired = !seed.pidConfigured || identityChanged
    val valid = isHttpsOrigin(trimmedBaseUrl) && trimmedUsername.isNotEmpty() &&
        (!passwordRequired || password.isNotBlank()) && (!pidRequired || pid.isNotBlank())

    UrlField(baseUrl, { baseUrl = it.take(MAX_URL_LENGTH) }, "AvistaZ HTTPS 地址")
    PlainField(username, { username = it.take(120) }, "用户名")
    SecretField(
        password,
        { password = it.take(MAX_SECRET_LENGTH) },
        "密码",
        seed.passwordConfigured && !identityChanged,
    )
    SecretField(
        pid,
        { pid = it.take(MAX_SECRET_LENGTH) },
        "PID",
        seed.pidConfigured && !identityChanged,
    )
    SaveButton("保存并启用 AvistaZ", valid, saving) {
        val replacementPassword = password.takeIf(String::isNotBlank)
        val replacementPid = pid.takeIf(String::isNotBlank)
        password = ""
        pid = ""
        onClose()
        callbacks.onSaveAvistaZ(trimmedBaseUrl, trimmedUsername, replacementPassword, replacementPid)
    }
}

@Composable
private fun NexusPhpEditorFields(
    seed: NexusPhpConfigurationUi,
    saving: Boolean,
    callbacks: SettingsConfigurationCallbacks,
    onClose: () -> Unit,
) {
    var siteId by remember(seed) { mutableStateOf(seed.siteId) }
    var displayName by remember(seed) { mutableStateOf(seed.displayName) }
    var baseUrl by remember(seed) { mutableStateOf(seed.baseUrl) }
    var cookie by remember(seed) { mutableStateOf("") }
    var passkey by remember(seed) { mutableStateOf("") }
    var confirmUnsupported by remember { mutableStateOf(false) }
    val trimmedSiteId = siteId.trim()
    val trimmedDisplayName = displayName.trim()
    val trimmedBaseUrl = baseUrl.trim().trimEnd('/')
    val identityChanged = trimmedSiteId != seed.siteId || trimmedBaseUrl != seed.baseUrl
    val cookieRequired = !seed.cookieConfigured || identityChanged
    val validSiteId = SITE_ID_PATTERN.matches(trimmedSiteId)
    val valid = validSiteId && trimmedDisplayName.isNotEmpty() && isHttpsOrigin(trimmedBaseUrl) &&
        (!cookieRequired || cookie.isNotBlank())

    fun submit() {
        val replacementCookie = cookie.takeIf(String::isNotBlank)
        val replacementPasskey = passkey.takeIf(String::isNotBlank)
        cookie = ""
        passkey = ""
        onClose()
        callbacks.onSaveNexusPhp(
            trimmedSiteId, trimmedDisplayName, trimmedBaseUrl, replacementCookie, replacementPasskey,
        )
    }

    if (!seed.runtimeSupported) {
        Text("当前服务端版本可以保存和测试 NexusPHP 配置，但尚不能用它执行资源搜索。启用后核心搜索流程可能不可用。", color = UninDanger, style = MaterialTheme.typography.bodyMedium)
    }
    PlainField(
        siteId,
        { siteId = it.lowercase(Locale.ROOT).take(24) },
        "站点标识",
        error = siteId.isNotBlank() && !validSiteId,
    )
    PlainField(displayName, { displayName = it.take(100) }, "显示名称")
    UrlField(baseUrl, { baseUrl = it.take(MAX_URL_LENGTH) }, "NexusPHP HTTPS 地址")
    SecretField(
        cookie,
        { cookie = it.take(MAX_SECRET_LENGTH) },
        "Cookie",
        seed.cookieConfigured && !identityChanged,
    )
    SecretField(
        passkey,
        { passkey = it.take(MAX_SECRET_LENGTH) },
        "Passkey（可选）",
        seed.passkeyConfigured && !identityChanged,
        required = false,
    )
    SaveButton("保存并启用 NexusPHP", valid, saving) {
        if (seed.runtimeSupported) submit() else confirmUnsupported = true
    }

    if (confirmUnsupported) {
        AlertDialog(
            onDismissRequest = { confirmUnsupported = false },
            title = { Text("启用尚未支持的架构？") },
            text = { Text("保存后 NexusPHP 会成为活动 PT 架构，但当前服务端不能用它搜索资源。你仍可保存并执行连接测试。") },
            confirmButton = {
                TextButton(onClick = { confirmUnsupported = false; submit() }) { Text("仍然保存", color = UninDanger) }
            },
            dismissButton = { TextButton(onClick = { confirmUnsupported = false }) { Text("取消") } },
        )
    }
}

@Composable
private fun ConfigurationForm(
    title: String,
    description: String,
    onClose: () -> Unit,
    content: @Composable ColumnScope.() -> Unit,
) {
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Outlined.Security, contentDescription = null, tint = UninBlue)
                Spacer(Modifier.size(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(title, style = MaterialTheme.typography.titleLarge)
                    Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                TextButton(onClick = onClose) { Text("关闭") }
            }
            HorizontalDivider()
            content()
        }
    }
}

@Composable
private fun ConfigurationEntryCard(
    title: String,
    description: String,
    configured: Boolean,
    onEdit: () -> Unit,
) {
    NeumorphicCard(Modifier.fillMaxWidth()) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium)
                Text(description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(horizontalAlignment = Alignment.End) {
                StatusPill(if (configured) "已配置" else "未配置", if (configured) UninSuccess else UninWarning)
                TextButton(onClick = onEdit, modifier = Modifier.height(48.dp)) {
                    Icon(Icons.Outlined.Edit, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.size(5.dp))
                    Text(if (configured) "编辑" else "配置")
                }
            }
        }
    }
}

@Composable
private fun PlainField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    error: Boolean = false,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = true,
        isError = error,
    )
}

@Composable
private fun UrlField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    allowHttp: Boolean = false,
) {
    val valid = if (allowHttp) isQbUrl(value, true) else isHttpsOrigin(value)
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = true,
        isError = value.isNotBlank() && !valid,
        supportingText = if (value.isNotBlank() && !valid) {
            { Text(if (allowHttp) "请输入有效的 HTTP 或 HTTPS 地址" else "请输入有效的 HTTPS 地址") }
        } else {
            null
        },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
    )
}

@Composable
private fun ProxyUrlField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
) {
    val valid = value.isBlank() || isProxyUrl(value)
    OutlinedTextField(
        value = value,
        onValueChange = { onValueChange(it.take(MAX_URL_LENGTH)) },
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        singleLine = true,
        isError = !valid,
        supportingText = if (!valid) {
            { Text("请输入不含路径、查询参数或登录信息的 HTTP/HTTPS 地址") }
        } else {
            null
        },
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
    )
}

@Composable
private fun SecretField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    configured: Boolean,
    required: Boolean = true,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        modifier = Modifier.fillMaxWidth(),
        label = { Text(label) },
        placeholder = {
            Text(
                when {
                    configured -> "已配置 · 留空保持现有值"
                    required -> "尚未配置 · 必填"
                    else -> "未配置 · 可选"
                },
            )
        },
        singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
    )
}

@Composable
private fun SaveButton(text: String, valid: Boolean, saving: Boolean, onClick: () -> Unit) {
    GradientPrimaryButton(
        text = text,
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        enabled = valid,
        loading = saving,
    )
}

@Composable
private fun ConnectionCard(connection: ConnectionUi, callbacks: UninCallbacks, modifier: Modifier = Modifier) {
    val (label, color) = when (connection.state) {
        ConnectionState.Connected -> "已连接" to UninSuccess
        ConnectionState.Configured -> "已配置" to UninBlue
        ConnectionState.Checking -> "检测中" to UninBlue
        ConnectionState.Disconnected -> "连接失败" to UninDanger
        ConnectionState.NotConfigured -> "未配置" to UninWarning
    }
    NeumorphicCard(modifier) {
        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(connection.name, style = MaterialTheme.typography.titleMedium)
                Text(connection.description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Column(horizontalAlignment = Alignment.End) {
                StatusPill(label, color)
                TextButton(
                    onClick = { callbacks.onTestConnection(connection) },
                    enabled = connection.state != ConnectionState.Checking && connection.state != ConnectionState.NotConfigured,
                    modifier = Modifier.height(48.dp),
                ) {
                    Icon(Icons.Outlined.Refresh, null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.size(5.dp))
                    Text("测试")
                }
            }
        }
    }
}

private fun configuredDescription(configured: Boolean, secretConfigured: Boolean): String = when {
    configured && secretConfigured -> "服务端配置完整；敏感字段可在此替换"
    configured -> "基础信息已保存，但敏感凭据尚未配置"
    else -> "服务端尚未配置"
}

private fun isHttpsOrigin(value: String): Boolean = isHttpOrigin(value, allowHttp = false)

private fun isProxyUrl(value: String): Boolean = isHttpOrigin(value, allowHttp = true)

private fun isHttpOrigin(value: String, allowHttp: Boolean): Boolean = runCatching {
    val uri = URI(value.trim())
    val schemeAllowed = uri.scheme.equals("https", ignoreCase = true) ||
        (allowHttp && uri.scheme.equals("http", ignoreCase = true))
    val path = uri.rawPath.orEmpty()
    schemeAllowed &&
        !uri.host.isNullOrBlank() &&
        uri.userInfo == null &&
        uri.rawQuery == null &&
        uri.rawFragment == null &&
        path in setOf("", "/") &&
        (uri.port == -1 || uri.port in 1..65535)
}.getOrDefault(false)

private fun isQbUrl(value: String, allowHttp: Boolean): Boolean = runCatching {
    val uri = URI(value.trim())
    val schemeAllowed = uri.scheme == "https" || (allowHttp && uri.scheme == "http")
    schemeAllowed &&
        !uri.host.isNullOrBlank() &&
        uri.userInfo == null &&
        uri.rawQuery == null &&
        uri.rawFragment == null &&
        (uri.port == -1 || uri.port in 1..65535)
}.getOrDefault(false)

private val SITE_ID_PATTERN = Regex("^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$")
private const val MAX_URL_LENGTH = 2048
private const val MAX_SECRET_LENGTH = 8192
