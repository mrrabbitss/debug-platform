<template>
  <el-container class="app-shell">
    <el-aside width="230px" class="sidebar">
      <div class="brand">GW/AP Debug</div>
      <div class="brand-sub">证据驱动故障诊断平台</div>
      <el-menu router :default-active="$route.path" class="nav-menu">
        <el-menu-item index="/cases">故障案例</el-menu-item>
        <el-menu-item index="/knowledge">知识库</el-menu-item>
        <el-menu-item index="/settings">系统设置</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div>GW / AP collectDebuginfo 分析、RAG 与代码关联</div>
        <el-tag effect="plain">Internal</el-tag>
      </el-header>
      <el-main class="main-content"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
