package de.tlovex.unin.data.model

internal object ApiErrorPolicy {
    private val sessionAuthenticationErrors = setOf(
        "AUTH_REQUIRED",
        "AUTH_SESSION_INVALID",
        "AUTH_SESSION_EXPIRED",
        "AUTH_SESSION_STALE",
    )

    fun isSessionAuthenticationFailure(statusCode: Int, errorCode: String?): Boolean =
        statusCode == 401 && errorCode in sessionAuthenticationErrors
}
