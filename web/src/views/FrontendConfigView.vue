<template>
  <div class="settings-page-view">
    <PageHeader title="前端配置" :show-border="true" />
    <div class="settings-page-content">
      <FrontendChatSettingsComponent />
    </div>
  </div>
</template>

<script setup>
import PageHeader from '@/components/shared/PageHeader.vue'
import FrontendChatSettingsComponent from '@/components/FrontendChatSettingsComponent.vue'
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
