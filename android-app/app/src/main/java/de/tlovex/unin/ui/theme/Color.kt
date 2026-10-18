package de.tlovex.unin.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

/**
 * UNIN's colours: flat and low in saturation, the same family as the web
 * console. No glows and no gradients; status is told by a muted hue.
 */
@Immutable
data class UninPalette(
    val canvas: Color,
    val surface: Color,
    // Inputs, progress tracks and other recessed areas.
    val surfaceSunken: Color,
    val line: Color,
    val ink: Color,
    val inkMuted: Color,
    // Filled buttons and the brand mark.
    val accentFill: Color,
    // Accent text, icons and selected states.
    val accent: Color,
    val accentSoft: Color,
    val success: Color,
    val info: Color,
    val warning: Color,
    val danger: Color,
    val neutral: Color,
)

internal val DarkPalette = UninPalette(
    canvas = Color(0xFF090D15),
    surface = Color(0xFF131B28),
    surfaceSunken = Color(0xFF0E1520),
    line = Color(0xFF232C3A),
    ink = Color(0xFFE8EDF7),
    inkMuted = Color(0xFF8E9BB0),
    accentFill = Color(0xFF4C669F),
    accent = Color(0xFF8FA5D1),
    accentSoft = Color(0xFF1D2738),
    success = Color(0xFF7FB89A),
    info = Color(0xFF8AA6C9),
    warning = Color(0xFFC9A36B),
    danger = Color(0xFFCF8A8F),
    neutral = Color(0xFFA9A3C4),
)

internal val LightPalette = UninPalette(
    canvas = Color(0xFFF3F4F7),
    surface = Color(0xFFFFFFFF),
    surfaceSunken = Color(0xFFECEEF2),
    line = Color(0xFFDDE1E8),
    ink = Color(0xFF1B2230),
    inkMuted = Color(0xFF5F6B7D),
    accentFill = Color(0xFF4C669F),
    accent = Color(0xFF3F5A8F),
    accentSoft = Color(0xFFE3E9F4),
    success = Color(0xFF4E7D63),
    info = Color(0xFF4F6E93),
    warning = Color(0xFF8A6A35),
    danger = Color(0xFFA5545C),
    neutral = Color(0xFF6A6488),
)

internal val LocalUninPalette = staticCompositionLocalOf { DarkPalette }

val uninPalette: UninPalette
    @Composable @ReadOnlyComposable get() = LocalUninPalette.current

// The names the screens use. Blue, cyan and violet once were three bright
// accents; they now map onto the few muted hues of the palette.
val UninBlue: Color @Composable @ReadOnlyComposable get() = uninPalette.accent
val UninCyan: Color @Composable @ReadOnlyComposable get() = uninPalette.info
val UninViolet: Color @Composable @ReadOnlyComposable get() = uninPalette.neutral
val UninSuccess: Color @Composable @ReadOnlyComposable get() = uninPalette.success
val UninWarning: Color @Composable @ReadOnlyComposable get() = uninPalette.warning
val UninDanger: Color @Composable @ReadOnlyComposable get() = uninPalette.danger
