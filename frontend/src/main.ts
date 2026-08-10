import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { installSessionExpiryHandler } from './auth/session'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles.css'

const pinia = createPinia()
const app = createApp(App)

app.use(pinia)
app.use(router)

const auth = useAuthStore(pinia)
installSessionExpiryHandler(auth, router)

app.mount('#app')
