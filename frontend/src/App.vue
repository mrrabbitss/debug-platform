<script setup lang="ts">
import EngineerLoginGate from './components/EngineerLoginGate.vue'
import WorkbenchShell from './components/WorkbenchShell.vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
</script>

<template><el-config-provider :locale="zhCn"><EngineerLoginGate><WorkbenchShell /></EngineerLoginGate></el-config-provider></template>
