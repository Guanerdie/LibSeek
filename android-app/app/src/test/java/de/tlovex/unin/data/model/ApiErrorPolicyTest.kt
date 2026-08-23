package de.tlovex.unin.data.model

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiErrorPolicyTest {
    @Test
    fun sessionErrorsExpireTheLocalSession() {
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(401, "AUTH_REQUIRED"))
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(401, "AUTH_SESSION_EXPIRED"))
    }

    @Test
    fun upstreamCredentialErrorsDoNotExpireTheUninSession() {
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(401, "QB_AUTH_FAILED"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(401, "PT_AUTH_FAILED"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(502, "AUTH_REQUIRED"))
    }
}
