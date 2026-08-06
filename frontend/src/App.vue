<template>
  <el-container class="app-shell">
    <el-aside width="230px" class="sidebar">
      <div class="brand">GW/AP Debug</div>
      <div class="brand-sub">证据驱动故障诊断平台</div>
      <el-menu router :default-active="$route.path" class="nav-menu">
        <el-menu-item index="/cases">故障案例</el-menu-item>
        <el-menu-item index="/knowledge">知识库</el-menu-item>
        <el-menu-item index="/knowledge/curation">AI 案例提炼</el-menu-item>
        <el-menu-item index="/cognitive-search">认知检索</el-menu-item>
        <el-menu-item index="/agent-runs">运行轨迹</el-menu-item>
        <el-menu-item index="/quality-governance">质量与治理</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
        <el-menu-item index="/security">安全与审计</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div>GW / AP collectDebuginfo 分析、工程图谱与认知检索</div>
        <el-tag effect="plain">Internal</el-tag>
      </el-header>
      <el-main class="main-content"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
