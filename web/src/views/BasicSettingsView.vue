<template>
  <div class="settings-page-view">
    <PageHeader
      v-model:active-key="activeTab"
      title="基本设置"
      :tabs="settingTabs"
      :show-border="true"
      aria-label="基本设置切换"
    />
    <div class="settings-page-content">
      <BasicSettingsSection :active-section="activeTab" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'

const activeTab = ref('defaults')
const settingTabs = [
  { key: 'defaults', label: '默认项配置' },
  { key: 'contentGuard', label: '内容审查配置' }
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
