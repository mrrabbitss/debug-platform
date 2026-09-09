<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useWorkbench } from '../composables/useWorkbench'
const route = useRoute()
const { config, isAdmin, configLoading, configError, loadConfig } = useWorkbench()
const activeNavigation = computed(() => route.path.startsWith('/cases') ? '/cases' : /^\/(knowledge|admin\/(knowledge|curation))/.test(route.path) ? '/knowledge' : '/settings')
async function initialize() { try { await loadConfig() } catch { /* Keep the permission boundary closed until retry. */ } }
onMounted(initialize)
</script>

<template>
  <a class="skip-link" href="#workbench-content">跳到工作区</a>
  <el-container class="app-shell">
    <el-aside width="216px" class="sidebar">
      <div class="brand"><span class="brand-mark">G</span> GW/AP Debug</div>
      <div class="brand-sub">诊断工作台</div>
      <nav aria-label="主导航"><el-menu router :default-active="activeNavigation" class="nav-menu">
        <el-menu-item index="/cases"><span class="nav-symbol" aria-hidden="true">◎</span>故障定位</el-menu-item>
        <el-menu-item index="/knowledge"><span class="nav-symbol" aria-hidden="true">▤</span>知识库</el-menu-item>
        <el-menu-item index="/settings"><span class="nav-symbol" aria-hidden="true">⚙</span>系统设置</el-menu-item>
      </el-menu></nav>
      <div class="sidebar-note">从问题到证据<br />让每一次诊断积累为知识</div>
    </el-aside>
    <el-container class="workspace-container">
      <el-header class="topbar"><div class="breadcrumb">工作台 <span>/</span> <strong>{{ route.meta.title }}</strong></div>
        <div class="topbar-actions"><el-button v-if="isAdmin" link @click="$router.push('/settings?tab=advanced')">管理员入口</el-button><span class="identity-label">{{ config.principal.display_name || config.principal.username || (isAdmin ? '管理员' : '工作台') }}</span></div>
      </el-header>
      <el-main id="workbench-content" class="main-content" tabindex="-1">
        <div v-if="configError" class="state-panel" role="alert"><h2>暂时无法加载工作台</h2><p>{{ configError }}</p><el-button type="primary" @click="initialize">重新连接</el-button></div>
        <el-skeleton v-else-if="configLoading || !config.principal.role" :rows="7" animated />
        <el-result v-else-if="route.meta.requiresAdmin && !isAdmin" icon="warning" title="此页面仅限管理员" sub-title="可以继续使用故障定位和已发布知识。"><template #extra><el-button @click="$router.replace('/cases')">返回故障定位</el-button></template></el-result>
        <router-view v-else :key="route.path" />
      </el-main>
    </el-container>
  </el-container>
</template>
