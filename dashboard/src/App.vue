<script setup lang="ts">
import { useApiHealth } from '@/composables/useApiHealth'
const { healthy } = useApiHealth()
</script>

<template>
  <div class="layout">
    <header class="layout-header">
      <div class="layout-brand">反馈台</div>
      <div class="layout-status">
        <span class="status-dot" :class="{ down: !healthy }"></span>
        <span class="status-text muted">{{ healthy ? '正常' : '异常' }}</span>
      </div>
    </header>
    <div class="layout-body">
      <aside class="layout-sider">
        <nav class="nav">
          <router-link to="/" class="nav-item">概览</router-link>
          <router-link to="/list" class="nav-item">反馈列表</router-link>
        </nav>
      </aside>
      <main class="layout-main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout { display: flex; flex-direction: column; height: 100vh; }
.layout-header {
  height: 56px; background: #ffffff;
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; padding: 0 24px;
}
.layout-brand { font-size: 16px; font-weight: 600; }
.layout-status { margin-left: auto; display: flex; align-items: center; gap: 6px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success); }
.status-dot.down { background: var(--danger); }
.layout-body { flex: 1; display: flex; min-height: 0; }
.layout-sider {
  width: 200px; background: #ffffff;
  border-right: 1px solid var(--border); padding: 16px 0;
}
.nav { display: flex; flex-direction: column; }
.nav-item {
  display: block; padding: 10px 24px;
  color: var(--text); text-decoration: none; font-size: 14px;
  border-left: 3px solid transparent;
}
.nav-item:hover { background: var(--bg); }
.nav-item.router-link-active {
  background: var(--bg); color: var(--primary);
  border-left-color: var(--primary); font-weight: 600;
}
.layout-main { flex: 1; overflow: auto; }
</style>
