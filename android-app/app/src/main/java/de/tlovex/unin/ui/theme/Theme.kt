package de.tlovex.unin.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color

private fun UninPalette.toColorScheme(dark: Boolean): ColorScheme {
    val base = if (dark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = accent,
        onPrimary = if (dark) canvas else Color.White,
        primaryContainer = accentSoft,
        onPrimaryContainer = ink,
        secondary = info,
        onSecondary = if (dark) canvas else Color.White,
        secondaryContainer = accentSoft,
        onSecondaryContainer = ink,
        tertiary = neutral,
        onTertiary = if (dark) canvas else Color.White,
        tertiaryContainer = accentSoft,
        onTertiaryContainer = ink,
        background = canvas,
        onBackground = ink,
        surface = surface,
        onSurface = ink,
        surfaceVariant = surfaceSunken,
        onSurfaceVariant = inkMuted,
        surfaceContainerLowest = canvas,
        surfaceContainerLow = surface,
        surfaceContainer = surface,
        surfaceContainerHigh = surface,
        surfaceContainerHighest = surfaceSunken,
        surfaceTint = Color.Transparent,
        outline = line,
        outlineVariant = line,
        error = danger,
        onError = if (dark) canvas else Color.White,
        errorContainer = accentSoft,
        onErrorContainer = danger,
        inverseSurface = ink,
        inverseOnSurface = canvas,
        inversePrimary = accentFill,
        scrim = Color.Black,
    )
}

@Composable
fun UninTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    val palette = if (darkTheme) DarkPalette else LightPalette
    CompositionLocalProvider(LocalUninPalette provides palette) {
        MaterialTheme(
            colorScheme = palette.toColorScheme(darkTheme),
            typography = UninTypography,
            shapes = UninShapes,
            content = content,
        )
    }
}
