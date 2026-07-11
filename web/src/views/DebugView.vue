<template>
  <div class="debug-view">
    <PageHeader title="调试面板" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>非生产环境</span>
          <span>仅系统管理员可见</span>
        </div>
      </template>
    </PageHeader>
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

.summary-strip {
  display: flex;
  gap: 8px;

  span {
    color: var(--gray-600);
    font-size: 13px;
  }
}
</style>
