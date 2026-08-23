package de.tlovex.unin.data

import android.content.Context
import com.google.gson.Gson
import de.tlovex.unin.data.remote.UninApi
import de.tlovex.unin.data.repository.UninRepository
import de.tlovex.unin.data.security.CsrfInterceptor
import de.tlovex.unin.data.security.SecureCookieJar
import okhttp3.OkHttpClient
import okhttp3.HttpUrl.Companion.toHttpUrl
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

data class UninDataLayer(
    val repository: UninRepository,
    val api: UninApi,
    val cookieJar: SecureCookieJar,
) {
    companion object {
        fun create(
            context: Context,
            origin: String,
            gson: Gson = Gson(),
        ): UninDataLayer {
            val baseUrl = origin.trim().toHttpUrl()
            require(baseUrl.isHttps) { "UNIN origin must use HTTPS" }
            require(baseUrl.encodedPath == "/" && baseUrl.query == null && baseUrl.fragment == null) {
                "UNIN origin must not contain a path, query, or fragment"
            }
            require(baseUrl.username.isEmpty() && baseUrl.password.isEmpty()) {
                "UNIN origin must not contain credentials"
            }

            val cookieJar = SecureCookieJar(context, baseUrl.host, gson)
            val client = OkHttpClient.Builder()
                .cookieJar(cookieJar)
                .addInterceptor(CsrfInterceptor(cookieJar, baseUrl.host))
                .connectTimeout(20, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .readTimeout(90, TimeUnit.SECONDS)
                .callTimeout(120, TimeUnit.SECONDS)
                .followRedirects(false)
                .followSslRedirects(false)
                .build()
            val api = Retrofit.Builder()
                .baseUrl(baseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create(gson))
                .build()
                .create(UninApi::class.java)
            return UninDataLayer(
                repository = UninRepository(api, cookieJar),
                api = api,
                cookieJar = cookieJar,
            )
        }
    }
}
