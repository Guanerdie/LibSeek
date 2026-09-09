package de.tlovex.unin.data.model

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiErrorPolicyTest {
    @Test
    fun sessionErrorsExpireTheLocalSession() {
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(401, "AUTH_REQUIRED"))
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(401, "AUTH_SESSION_EXPIRED"))
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(403, "CSRF_TOKEN_EXPIRED"))
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(403, "CSRF_TOKEN_INVALID"))
        assertTrue(ApiErrorPolicy.isSessionAuthenticationFailure(403, "CSRF_TOKEN_REQUIRED"))
    }

    @Test
    fun upstreamCredentialErrorsDoNotExpireTheUninSession() {
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(401, "QB_AUTH_FAILED"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(401, "PT_AUTH_FAILED"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(502, "AUTH_REQUIRED"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(403, "AUTH_ROLE_FORBIDDEN"))
    }

    @Test
    fun mistypingTheCurrentPasswordDoesNotSignTheUserOut() {
        // Changing the password rejects a wrong current password with a 401.
        // Treating that as an expired session would throw the user back to the
        // login screen for a typo.
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(401, "AUTH_PASSWORD_MISMATCH"))
        assertFalse(ApiErrorPolicy.isSessionAuthenticationFailure(409, "AUTH_PASSWORD_NOT_MANAGED"))
    }
}
