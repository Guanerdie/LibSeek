package de.tlovex.unin.data.model

import com.google.gson.annotations.SerializedName

data class CredentialsDto(
    val username: String,
    val password: String,
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
