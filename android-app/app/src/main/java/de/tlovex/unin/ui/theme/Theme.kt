package de.tlovex.unin.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = UninBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE8ECFF),
    onPrimaryContainer = Color(0xFF1B2D87),
    secondary = UninCyan,
    onSecondary = Color(0xFF00373C),
    secondaryContainer = Color(0xFFD5F7FA),
    tertiary = UninViolet,
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFEDE6FF),
    background = UninCanvas,
    onBackground = UninInk,
    surface = UninSurface,
    onSurface = UninInk,
    surfaceVariant = Color(0xFFEDF2F9),
    onSurfaceVariant = UninInkMuted,
    outline = UninOutline,
    error = UninDanger,
    onError = Color.White,
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFAEBBFF),
    secondary = Color(0xFF67DCE6),
    tertiary = Color(0xFFC8B5FF),
    background = Color(0xFF101621),
    surface = Color(0xFF17202D),
    surfaceVariant = Color(0xFF212C3C),
    onBackground = Color(0xFFE7EDF7),
    onSurface = Color(0xFFE7EDF7),
    onSurfaceVariant = Color(0xFFB9C5D6),
    outline = Color(0xFF46546A),
    error = Color(0xFFFFB2C0),
)

@Composable
fun UninTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        typography = UninTypography,
        content = content,
    )
}
