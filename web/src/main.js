import { createApp } from 'vue'
import App from './App.vue'
// UNE 디자인 시스템 (design_handoff_une_ui) — 토큰이 style.css보다 먼저 로드되어야 한다
import './assets/une/fonts.css'
import './assets/une/fig-tokens.css'
import './assets/une/typography.css'
import './style.css'

createApp(App).mount('#app')
