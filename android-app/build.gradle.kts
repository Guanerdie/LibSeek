plugins {
    id("com.android.application") version "9.4.0" apply false
    // AGP 9 compiles Kotlin itself; this plugin still brings the Compose
    // compiler, and its version pins the Kotlin toolchain AGP uses.
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
}
