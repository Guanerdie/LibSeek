plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

val localReleasePropertiesFile = rootProject.file("../secrets/unin-android-release.properties")
val localReleaseKeystoreFile = rootProject.file("../secrets/unin-android-release.jks")
val localReleaseProperties = if (localReleasePropertiesFile.isFile) {
    localReleasePropertiesFile.readLines().mapNotNull { line ->
        val separator = line.indexOf('=')
        val key = line.take(separator.coerceAtLeast(0)).trim()
        if (separator <= 0 || key.isEmpty() || key.startsWith('#')) {
            null
        } else {
            key to line.substring(separator + 1)
        }
    }.toMap()
} else {
    emptyMap()
}
val hasLocalReleaseSigning = localReleaseKeystoreFile.isFile &&
    listOf("storePassword", "keyPassword", "keyAlias").all {
        !localReleaseProperties[it].isNullOrBlank()
    }

android {
    namespace = "de.tlovex.unin"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.tlovex.unin"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
        buildConfigField("String", "BASE_URL", "\"https://unin.tlovex.de/\"")
    }

    val localReleaseSigningConfig = if (hasLocalReleaseSigning) {
        signingConfigs.create("localRelease") {
            storeFile = localReleaseKeystoreFile
            storePassword = localReleaseProperties["storePassword"]
            keyAlias = localReleaseProperties["keyAlias"]
            keyPassword = localReleaseProperties["keyPassword"]
            enableV1Signing = false
            enableV2Signing = true
            enableV3Signing = true
            enableV4Signing = false
        }
    } else {
        null
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = localReleaseSigningConfig
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.12.01")

    implementation(composeBom)
    androidTestImplementation(composeBom)

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("androidx.activity:activity-compose:1.10.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
