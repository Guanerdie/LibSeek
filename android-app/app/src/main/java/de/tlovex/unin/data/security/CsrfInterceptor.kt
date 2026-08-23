package de.tlovex.unin.data.security

import okhttp3.CookieJar
import okhttp3.Interceptor
import okhttp3.Response

class CsrfInterceptor(
    private val cookieJar: CookieJar,
    expectedHost: String,
) : Interceptor {
    private val expectedHost = expectedHost.lowercase()

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        if (request.url.host != expectedHost || request.method !in MUTATION_METHODS) {
            return chain.proceed(request)
        }

        val csrfToken = cookieJar.loadForRequest(request.url)
            .firstOrNull { it.name == CSRF_COOKIE_NAME }
            ?.value
        val authenticatedRequest = if (csrfToken.isNullOrEmpty()) {
            request
        } else {
            request.newBuilder()
                .header(CSRF_HEADER_NAME, csrfToken)
                .build()
        }
        return chain.proceed(authenticatedRequest)
    }

    private companion object {
        const val CSRF_COOKIE_NAME = "unin_csrf"
        const val CSRF_HEADER_NAME = "X-CSRF-Token"
        val MUTATION_METHODS = setOf("POST", "PUT", "PATCH", "DELETE")
    }
}
