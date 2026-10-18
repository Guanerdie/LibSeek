package de.tlovex.unin

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import de.tlovex.unin.ui.UninApp
import de.tlovex.unin.ui.UninDestination

class MainActivity : ComponentActivity() {
    private val viewModel: UninViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        val splashScreen = installSplashScreen()
        super.onCreate(savedInstanceState)
        actionBar?.hide()

        splashScreen.setKeepOnScreenCondition { !viewModel.isInitialized.value }
        setContent {
            val state by viewModel.uiState.collectAsStateWithLifecycle()
            BackHandler(
                enabled = state.isAuthenticated &&
                    state.currentDestination != UninDestination.Missing,
                onBack = viewModel::navigateBack,
            )
            UninApp(
                state = state,
                callbacks = viewModel.callbacks,
            )
        }
    }
}
