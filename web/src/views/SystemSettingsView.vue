<template>
  <div class="system-settings-view">
    <PageHeader
      v-model:active-key="activeTab"
      title="系统设置"
      :tabs="settingTabs"
      :show-border="true"
      aria-label="系统设置视图切换"
    />

    <div class="system-settings-content">
      <div v-show="activeTab === 'account'" class="settings-panel">
        <AccountSettingsComponent />
      </div>
      <div v-show="activeTab === 'base'" class="settings-panel">
        <BasicSettingsSection />
      </div>
      <div v-show="activeTab === 'frontendChat'" class="settings-panel">
        <FrontendChatSettingsComponent />
      </div>
      <div v-show="activeTab === 'user'" class="settings-panel">
        <UserManagementComponent />
      </div>
      <div v-show="activeTab === 'department'" class="settings-panel">
        <DepartmentManagementComponent />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/shared/PageHeader.vue'
import AccountSettingsComponent from '@/components/AccountSettingsComponent.vue'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'
import FrontendChatSettingsComponent from '@/components/FrontendChatSettingsComponent.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'
import DepartmentManagementComponent from '@/components/DepartmentManagementComponent.vue'

const route = useRoute()
const router = useRouter()

const settingTabs = [
  { key: 'account', label: '账户设置' },
  { key: 'base', label: '基本设置' },
  { key: 'frontendChat', label: '前端配置' },
  { key: 'user', label: '用户管理' },
  { key: 'department', label: '部门管理' }
]
const settingTabKeys = new Set(settingTabs.map((tab) => tab.key))
const activeTab = ref('base')

const normalizeTab = (tab) => (settingTabKeys.has(tab) ? tab : 'base')

watch(
  () => route.query.tab,
  (tab) => {
    const nextTab = normalizeTab(tab)
    if (activeTab.value !== nextTab) activeTab.value = nextTab
  },
  { immediate: true }
)

watch(activeTab, (tab) => {
  const nextTab = normalizeTab(tab)
  if (nextTab !== tab) {
    activeTab.value = nextTab
    return
  }
  if (route.query.tab === nextTab) return
  router.replace({ query: { ...route.query, tab: nextTab } })
})
</script>

<style lang="less" scoped>
.system-settings-view {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.system-settings-content {
  padding: 16px var(--page-padding) 24px;
}

.settings-panel {
  min-height: 0;
}
</style>
