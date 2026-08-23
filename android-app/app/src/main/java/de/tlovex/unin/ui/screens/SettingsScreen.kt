package de.tlovex.unin.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.Logout
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.ConnectionState
import de.tlovex.unin.ui.ConnectionUi
import de.tlovex.unin.ui.UninCallbacks
import de.tlovex.unin.ui.UninUiState
import de.tlovex.unin.ui.components.GradientMark
import de.tlovex.unin.ui.components.NeumorphicCard
import de.tlovex.unin.ui.components.SectionHeading
import de.tlovex.unin.ui.components.StatusPill
import de.tlovex.unin.ui.theme.UninBlue
import de.tlovex.unin.ui.theme.UninDanger
import de.tlovex.unin.ui.theme.UninSuccess
import de.tlovex.unin.ui.theme.UninWarning

@Composable
fun SettingsScreen(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: androidx.compose.foundation.layout.PaddingValues,
    modifier: Modifier = Modifier,
) {
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
            NeumorphicCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(20.dp)) {
                    Text("数据与安全", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "登录凭据仅用于当前 UNIN 服务；TMDB、PT 与 qBittorrent 密钥由服务端管理，不会编译或写入应用界面。",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
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
                    enabled = connection.state != ConnectionState.Checking &&
                        connection.state != ConnectionState.NotConfigured,
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
