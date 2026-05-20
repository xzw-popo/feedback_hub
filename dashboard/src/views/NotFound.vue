<script setup lang="ts">
import { useRouter } from 'vue-router'
const router = useRouter()
</script>

<template>
  <div class="not-found">
    <h1>404</h1>
    <p class="muted">页面不存在</p>
    <el-button type="primary" @click="router.push('/')">返回首页</el-button>
  </div>
</template>

<style scoped>
.not-found { text-align: center; padding: 80px 24px; }
.not-found h1 { font-size: 64px; margin: 0 0 8px; color: var(--text-muted); }
.not-found p { margin: 0 0 24px; }
</style>
