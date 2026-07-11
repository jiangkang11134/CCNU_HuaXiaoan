<template>
  <div class="settings-page-view">
    <PageHeader
      v-model:active-key="activeTab"
      title="前端配置"
      :tabs="frontendConfigTabs"
      :show-border="true"
      aria-label="前端配置切换"
    />
    <div class="settings-page-content">
      <FrontendChatSettingsComponent :active-section="activeTab" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import FrontendChatSettingsComponent from '@/components/FrontendChatSettingsComponent.vue'

const activeTab = ref('branding')
const frontendConfigTabs = [
  { key: 'branding', label: '品牌展示' },
  { key: 'portal', label: '前台展示' },
  { key: 'agents', label: '前台智能体' },
  { key: 'input', label: '输入区能力' },
  { key: 'answer', label: '回答展示' }
]
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
