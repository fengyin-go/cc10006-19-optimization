import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { setIdentityProvider } from './api/client'
import { useSessionStore } from './stores/session'
import './styles/global.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)

// 把会话里的当前操作人注入请求层，后端按 X-Operator 做权限与归属判断。
setIdentityProvider(() => {
  const session = useSessionStore(pinia)
  return { operator: session.operator, role: session.role }
})

app.mount('#app')
