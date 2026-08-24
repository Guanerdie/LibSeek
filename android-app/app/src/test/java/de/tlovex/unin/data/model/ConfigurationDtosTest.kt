package de.tlovex.unin.data.model

import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigurationDtosTest {
    private val gson = Gson()

    @Test
    fun `configuration response reads editable public fields without secrets`() {
        val response = gson.fromJson(
            """
            {
              "nextfind": {
                "base_url": "https://nextfind.invalid",
                "username": "next-user",
                "password_configured": true,
                "configured": true
              },
              "tmdb": { "configured": true },
              "outbound_proxy": {
                "url": "https://proxy.invalid",
                "username": "proxy-user",
                "password_configured": true,
                "configured": true
              },
              "pt_site": {
                "architecture": "avistaz",
                "base_url": "https://tracker.invalid",
                "username": "tracker-user",
                "password_configured": true,
                "pid_configured": true,
                "configured": true,
                "runtime_supported": true,
                "search_ready": true
              },
              "pt_sites": {
                "avistaz": {
                  "architecture": "avistaz",
                  "base_url": "https://tracker.invalid",
                  "username": "tracker-user",
                  "password_configured": true,
                  "pid_configured": true,
                  "configured": true,
                  "runtime_supported": true,
                  "search_ready": true
                },
                "nexusphp": {
                  "architecture": "nexusphp",
                  "site_id": "tracker-two",
                  "display_name": "Tracker Two",
                  "base_url": "https://tracker-two.invalid",
                  "cookie_configured": false,
                  "passkey_configured": false,
                  "configured": false,
                  "runtime_supported": false,
                  "search_ready": false
                }
              },
              "qbittorrent": {
                "url": "https://qb.invalid",
                "username": "qb-user",
                "save_path": "/downloads",
                "category": "unin",
                "configured": true,
                "allow_insecure_http": false
              },
              "configuration_complete": true
            }
            """.trimIndent(),
            ConfigurationStatusDto::class.java,
        )

        assertTrue(response.configurationComplete)
        assertTrue(response.nextfind.configured)
        assertEquals("https://nextfind.invalid", response.nextfind.baseUrl)
        assertEquals("next-user", response.nextfind.username)
        assertTrue(response.nextfind.passwordConfigured)
        assertTrue(response.tmdb.configured)
        assertTrue(response.outboundProxy.configured)
        assertEquals("proxy-user", response.outboundProxy.username)
        assertTrue(response.outboundProxy.passwordConfigured)
        assertTrue(response.qbittorrent.configured)
        assertEquals("/downloads", response.qbittorrent.savePath)
        assertFalse(response.qbittorrent.allowInsecureHttp)
        assertEquals(PtSiteArchitecture.AVISTAZ, response.ptSite?.architecture)
        assertEquals("tracker-user", response.ptSite?.username)
        assertTrue(response.ptSite?.searchReady == true)
        assertEquals("tracker-two", response.ptSites.nexusphp?.siteId)
        assertFalse(response.ptSites.nexusphp?.runtimeSupported ?: true)
    }

    @Test
    fun `connection test response preserves health detail`() {
        val response = gson.fromJson(
            """
            {
              "target": "qbittorrent",
              "healthy": false,
              "error_code": "QB_AUTH_FAILED",
              "message": "认证失败"
            }
            """.trimIndent(),
            ConnectionTestResultDto::class.java,
        )

        assertEquals("qbittorrent", response.target)
        assertFalse(response.healthy)
        assertEquals("QB_AUTH_FAILED", response.errorCode)
        assertEquals("认证失败", response.message)
    }

    @Test
    fun `configuration update serializes backend field names and redacts diagnostics`() {
        val request = ConfigurationUpdateRequestDto(
            nextfind = NextFindConfigurationUpdateDto(
                baseUrl = "https://nextfind.example",
                username = "next-user",
                password = "next-secret",
            ),
            tmdb = TmdbConfigurationUpdateDto(token = "tmdb-secret"),
            outboundProxy = OutboundProxyConfigurationUpdateDto(
                url = "https://proxy.example",
                username = "proxy-user",
                password = "proxy-secret",
            ),
            ptSite = AvistaZConfigurationUpdateDto(
                baseUrl = "https://avistaz.example",
                username = "pt-user",
                password = "pt-secret",
                pid = "pt-pid",
            ),
            qbittorrent = QbittorrentConfigurationUpdateDto(
                url = "https://qb.example",
                username = "qb-user",
                password = "qb-secret",
                savePath = "/downloads",
                category = "unin",
                allowInsecureHttp = false,
            ),
        )

        val json = gson.toJsonTree(request).asJsonObject
        assertEquals("https://nextfind.example", json["nextfind"].asJsonObject["base_url"].asString)
        assertEquals("tmdb-secret", json["tmdb"].asJsonObject["token"].asString)
        assertEquals("https://proxy.example", json["outbound_proxy"].asJsonObject["url"].asString)
        assertEquals("avistaz", json["pt_site"].asJsonObject["architecture"].asString)
        assertEquals("pt-pid", json["pt_site"].asJsonObject["pid"].asString)
        assertEquals("/downloads", json["qbittorrent"].asJsonObject["save_path"].asString)
        assertFalse(json["qbittorrent"].asJsonObject["allow_insecure_http"].asBoolean)

        val diagnosticText = listOf(
            request,
            request.nextfind,
            request.tmdb,
            request.outboundProxy,
            request.ptSite,
            request.qbittorrent,
        ).joinToString()
        assertFalse(diagnosticText.contains("next-secret"))
        assertFalse(diagnosticText.contains("tmdb-secret"))
        assertFalse(diagnosticText.contains("proxy-secret"))
        assertFalse(diagnosticText.contains("pt-secret"))
        assertFalse(diagnosticText.contains("pt-pid"))
        assertFalse(diagnosticText.contains("qb-secret"))
    }

    @Test
    fun `omitted secrets stay absent and nexusphp discriminator is serialized`() {
        val nextFind = gson.toJsonTree(
            ConfigurationUpdateRequestDto(
                nextfind = NextFindConfigurationUpdateDto(
                    baseUrl = "https://nextfind.example",
                    username = "next-user",
                ),
            ),
        ).asJsonObject["nextfind"].asJsonObject
        assertNull(nextFind["password"])

        val nexusPhp = gson.toJsonTree(
            ConfigurationUpdateRequestDto(
                ptSite = NexusPhpConfigurationUpdateDto(
                    siteId = "tracker-one",
                    displayName = "Tracker One",
                    baseUrl = "https://tracker.example",
                ),
            ),
        ).asJsonObject["pt_site"].asJsonObject
        assertEquals("nexusphp", nexusPhp["architecture"].asString)
        assertEquals("tracker-one", nexusPhp["site_id"].asString)
        assertEquals("Tracker One", nexusPhp["display_name"].asString)
        assertNull(nexusPhp["cookie"])
        assertNull(nexusPhp["passkey"])
    }
}
