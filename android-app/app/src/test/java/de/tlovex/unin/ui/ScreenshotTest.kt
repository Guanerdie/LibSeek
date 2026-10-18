package de.tlovex.unin.ui

import com.github.takahirom.roborazzi.captureRoboImage
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * Renders each screen to build/screenshots/ so a visual change can be looked
 * at without a device. Nothing is compared; the images are for reading.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [35], qualifiers = "w400dp-h880dp-xxhdpi")
class ScreenshotTest {
    private fun capture(name: String, state: UninUiState) {
        captureRoboImage("build/screenshots/$name.png") {
            UninApp(state = state, callbacks = UninCallbacks())
        }
    }

    @Test
    fun login() = capture("01-login", ScreenshotFixtures.login())

    @Test
    fun missing() = capture("02-missing", ScreenshotFixtures.base(UninDestination.Missing))

    @Test
    fun resources() = capture("03-resources", ScreenshotFixtures.base(UninDestination.Resources))

    @Test
    fun downloads() = capture("04-downloads", ScreenshotFixtures.base(UninDestination.Downloads))

    @Test
    @Config(qualifiers = "w400dp-h1600dp-xxhdpi")
    fun cleanup() = capture("05-cleanup", ScreenshotFixtures.base(UninDestination.Cleanup))

    @Test
    fun activity() = capture("06-activity", ScreenshotFixtures.base(UninDestination.Activity))

    @Test
    @Config(qualifiers = "w400dp-h1300dp-xxhdpi")
    fun import() = capture("07-import", ScreenshotFixtures.base(UninDestination.Import))

    @Test
    @Config(qualifiers = "w400dp-h1600dp-xxhdpi")
    fun automation() = capture("08-automation", ScreenshotFixtures.base(UninDestination.Automation))

    @Test
    @Config(qualifiers = "w400dp-h1300dp-xxhdpi")
    fun settings() = capture("09-settings", ScreenshotFixtures.base(UninDestination.Settings))

    @Test
    @Config(qualifiers = "w1000dp-h700dp-xhdpi")
    fun tablet() = capture("10-tablet", ScreenshotFixtures.base(UninDestination.Missing))

    // The app follows the system setting; these are the dark versions.

    @Test
    @Config(qualifiers = "+night")
    fun loginDark() = capture("dark-01-login", ScreenshotFixtures.login())

    @Test
    @Config(qualifiers = "+night")
    fun missingDark() = capture("dark-02-missing", ScreenshotFixtures.base(UninDestination.Missing))

    @Test
    @Config(qualifiers = "+night")
    fun downloadsDark() = capture("dark-04-downloads", ScreenshotFixtures.base(UninDestination.Downloads))

    @Test
    @Config(qualifiers = "w400dp-h1600dp-night-xxhdpi")
    fun cleanupDark() = capture("dark-05-cleanup", ScreenshotFixtures.base(UninDestination.Cleanup))

    @Test
    @Config(qualifiers = "+night")
    fun activityDark() = capture("dark-06-activity", ScreenshotFixtures.base(UninDestination.Activity))

    @Test
    @Config(qualifiers = "w400dp-h1600dp-night-xxhdpi")
    fun automationDark() = capture("dark-08-automation", ScreenshotFixtures.base(UninDestination.Automation))

    @Test
    @Config(qualifiers = "w1000dp-h700dp-night-xhdpi")
    fun tabletDark() = capture("dark-10-tablet", ScreenshotFixtures.base(UninDestination.Missing))
}
