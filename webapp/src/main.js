import { createApp } from 'vue'
import ElementPlus, { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as Icons from '@element-plus/icons-vue'
import App from './App.vue'
import 'misans/lib/Normal/MiSans-Regular.min.css'
import 'misans/lib/Normal/MiSans-Medium.min.css'
import 'misans/lib/Normal/MiSans-Semibold.min.css'
import './styles/tokens.css'
import './styles/app.css'

document.documentElement.classList.add('dark')

/* 全局错误挂钩: 所有 ElMessage.error 自动进诊断缓冲 (import 需在 lib 之后, 用动态注册) */
import { trackError as _te } from './lib.js'
const _origErr = ElMessage.error.bind(ElMessage)
ElMessage.error = (...args) => {
  const msg = typeof args[0] === 'string' ? args[0] : (args[0]?.message || JSON.stringify(args[0]))
  _te(msg, 'toast')
  return _origErr(...args)
}

const app = createApp(App)
app.use(ElementPlus, { locale: zhCn })
for (const [name, comp] of Object.entries(Icons)) app.component(name, comp)
app.mount('#app')
