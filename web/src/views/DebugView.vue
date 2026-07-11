<template>
  <div class="debug-view">
    <PageHeader title="运行日志" :show-border="true" />
    <div class="debug-view-content">
      <DebugComponent />
    </div>
  </div>
</template>

<script setup>
import PageHeader from '@/components/shared/PageHeader.vue'
import DebugComponent from '@/components/DebugComponent.vue'
</script>

<style lang="less" scoped>
.debug-view {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.debug-view-content {
  padding: 16px var(--page-padding) 24px;
}

</style>
