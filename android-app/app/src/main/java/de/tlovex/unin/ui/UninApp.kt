package de.tlovex.unin.ui

import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.MovieFilter
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import de.tlovex.unin.ui.components.UninWordmark
import de.tlovex.unin.ui.screens.AutomationScreen
import de.tlovex.unin.ui.screens.DownloadsScreen
import de.tlovex.unin.ui.screens.LoginScreen
import de.tlovex.unin.ui.screens.MissingMediaScreen
import de.tlovex.unin.ui.screens.ResourceCandidatesScreen
import de.tlovex.unin.ui.screens.SettingsScreen
import de.tlovex.unin.ui.theme.UninTheme

private data class NavigationItem(
    val destination: UninDestination,
    val label: String,
    val icon: ImageVector,
)

private val primaryDestinations = listOf(
    NavigationItem(UninDestination.Missing, "缺失", Icons.Outlined.MovieFilter),
    NavigationItem(UninDestination.Downloads, "下载", Icons.Outlined.Download),
    NavigationItem(UninDestination.Automation, "自动化", Icons.Outlined.AutoAwesome),
    NavigationItem(UninDestination.Settings, "设置", Icons.Outlined.Settings),
)

/**
 * Stateless UNIN application UI. The host owns navigation and business state and responds through
 * [UninCallbacks], making this composable suitable for a ViewModel or a preview fixture.
 */
@Composable
fun UninApp(
    state: UninUiState,
    callbacks: UninCallbacks,
    modifier: Modifier = Modifier,
    darkTheme: Boolean = false,
) {
    UninTheme(darkTheme = darkTheme) {
        val snackbarHostState = remember { SnackbarHostState() }
        LaunchedEffect(state.snackbarMessage) {
            state.snackbarMessage?.let {
                snackbarHostState.showSnackbar(it)
                callbacks.onDismissMessage()
            }
        }

        if (!state.isAuthenticated) {
            LoginScreen(state, callbacks, modifier)
            return@UninTheme
        }

        BoxWithConstraints(modifier.fillMaxSize()) {
            val useNavigationRail = maxWidth >= 720.dp
            Scaffold(
                modifier = Modifier.fillMaxSize(),
                containerColor = MaterialTheme.colorScheme.background,
                snackbarHost = { SnackbarHost(snackbarHostState) },
                bottomBar = {
                    if (!useNavigationRail) {
                        PhoneNavigationBar(state.currentDestination, callbacks.onDestinationSelected)
                    }
                },
            ) { scaffoldPadding ->
                if (useNavigationRail) {
                    Row(Modifier.fillMaxSize().padding(scaffoldPadding)) {
                        TabletNavigationRail(state.currentDestination, callbacks.onDestinationSelected)
                        ScreenContent(
                            state = state,
                            callbacks = callbacks,
                            contentPadding = PaddingValues(horizontal = 28.dp, vertical = 24.dp),
                            modifier = Modifier.weight(1f).fillMaxSize(),
                        )
                    }
                } else {
                    ScreenContent(
                        state = state,
                        callbacks = callbacks,
                        contentPadding = PaddingValues(
                            start = 18.dp,
                            top = scaffoldPadding.calculateTopPadding() + 20.dp,
                            end = 18.dp,
                            bottom = scaffoldPadding.calculateBottomPadding() + 20.dp,
                        ),
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }
    }
}

@Composable
private fun ScreenContent(
    state: UninUiState,
    callbacks: UninCallbacks,
    contentPadding: PaddingValues,
    modifier: Modifier,
) {
    when (state.currentDestination) {
        UninDestination.Missing -> MissingMediaScreen(state, callbacks, contentPadding, modifier)
        UninDestination.Resources -> ResourceCandidatesScreen(state, callbacks, contentPadding, modifier)
        UninDestination.Downloads -> DownloadsScreen(state, callbacks, contentPadding, modifier)
        UninDestination.Automation -> AutomationScreen(state, callbacks, contentPadding, modifier)
        UninDestination.Settings -> SettingsScreen(state, callbacks, contentPadding, modifier)
    }
}

@Composable
private fun PhoneNavigationBar(selected: UninDestination, onSelected: (UninDestination) -> Unit) {
    NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
        primaryDestinations.forEach { item ->
            val isSelected = selected == item.destination || (selected == UninDestination.Resources && item.destination == UninDestination.Missing)
            NavigationBarItem(
                selected = isSelected,
                onClick = { onSelected(item.destination) },
                icon = { Icon(item.icon, contentDescription = item.label) },
                label = { Text(item.label) },
                colors = NavigationBarItemDefaults.colors(indicatorColor = MaterialTheme.colorScheme.primaryContainer),
            )
        }
    }
}

@Composable
private fun TabletNavigationRail(selected: UninDestination, onSelected: (UninDestination) -> Unit) {
    NavigationRail(
        containerColor = MaterialTheme.colorScheme.surface,
        header = {
            UninWordmark(Modifier.padding(horizontal = 18.dp, vertical = 20.dp))
            HorizontalDivider(Modifier.padding(horizontal = 16.dp, vertical = 12.dp))
        },
    ) {
        primaryDestinations.forEach { item ->
            val isSelected = selected == item.destination || (selected == UninDestination.Resources && item.destination == UninDestination.Missing)
            NavigationRailItem(
                selected = isSelected,
                onClick = { onSelected(item.destination) },
                icon = { Icon(item.icon, contentDescription = item.label) },
                label = { Text(item.label) },
                alwaysShowLabel = true,
            )
        }
    }
}
