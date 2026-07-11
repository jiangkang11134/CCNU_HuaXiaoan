<template>
  <div class="settings-page-view">
    <PageHeader title="部门管理" :show-border="true" />
    <div class="settings-page-content">
      <DepartmentManagementComponent />
    </div>
  </div>
</template>

<script setup>
import PageHeader from '@/components/shared/PageHeader.vue'
import DepartmentManagementComponent from '@/components/DepartmentManagementComponent.vue'
</script>

<style lang="less" scoped>
.settings-page-view {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.settings-page-content {
  padding: 16px var(--page-padding) 24px;
}
</style>
