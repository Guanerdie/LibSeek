package de.tlovex.unin.data.model

internal object ApiErrorPolicy {
    private val sessionAuthenticationErrors = setOf(
        "AUTH_REQUIRED",
        "AUTH_SESSION_INVALID",
        "AUTH_SESSION_EXPIRED",
        "AUTH_SESSION_STALE",
    )
    private val csrfSessionErrors = setOf(
        "CSRF_TOKEN_REQUIRED",
        "CSRF_TOKEN_INVALID",
        "CSRF_TOKEN_EXPIRED",
    )

    fun isSessionAuthenticationFailure(statusCode: Int, errorCode: String?): Boolean =
        (statusCode == 401 && errorCode in sessionAuthenticationErrors) ||
            (statusCode == 403 && errorCode in csrfSessionErrors)
}
