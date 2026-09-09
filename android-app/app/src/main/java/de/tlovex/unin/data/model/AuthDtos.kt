package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

/** Body for `POST /api/auth/setup`, which rejects any field it does not know. */
data class CredentialsDto(
    val username: String,
    val password: String,
)

/**
 * Body for `POST /api/auth/login`.
 *
 * Kept separate from [CredentialsDto] because setup forbids unknown fields, so
 * `remember` must not be sent there.
 */
data class LoginRequestDto(
    val username: String,
    val password: String,
    /** Keep this device signed in for thirty days instead of eight hours. */
    val remember: Boolean = false,
)

/**
 * Body for `POST /api/auth/password`.
 *
 * The current password is required: a session cookie alone must not be enough
 * to lock the owner out of their own service. Succeeding rotates the server's
 * signing key, so every other device is signed out and this one is handed a
 * fresh session and CSRF cookie.
 */
data class PasswordChangeRequestDto(
    @SerializedName("current_password") val currentPassword: String,
    @SerializedName("new_password") val newPassword: String,
)

data class SetupStatusDto(
    @SerializedName("admin_initialized") val adminInitialized: Boolean,
    @SerializedName("configuration_complete") val configurationComplete: Boolean,
)

data class CsrfDto(
    @SerializedName("csrf_token") val csrfToken: String,
)

data class PrincipalDto(
    val username: String,
    val role: AuthRole,
)

data class LoginDto(
    val username: String,
    val role: AuthRole,
    @SerializedName("csrf_token") val csrfToken: String,
)

enum class AuthRole {
    @SerializedName("viewer") VIEWER,
    @SerializedName("operator") OPERATOR,
    @SerializedName("admin") ADMIN,
}
