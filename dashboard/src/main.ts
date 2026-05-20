import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import { router } from './router'
import App from './App.vue'
import './styles/global.scss'
import './styles/element-overrides.scss'

createApp(App).use(router).use(ElementPlus).mount('#app')
