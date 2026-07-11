<template>
  <div class="settings-page-view">
    <PageHeader
      v-model:active-key="activeTab"
      title="基本设置"
      :tabs="settingTabs"
      :loading="isSaving"
      :show-border="true"
      aria-label="基本设置切换"
    >
      <template #actions>
        <div class="save-actions">
          <span class="save-state" :class="saveStateClass">{{ saveStateText }}</span>
          <a-button type="primary" :disabled="!hasUnsavedChanges || isSaving" :loading="isSaving" @click="handleSaveAll">
            全部保存
          </a-button>
        </div>
      </template>
    </PageHeader>
    <div class="settings-page-content">
      <BasicSettingsSection ref="settingsSectionRef" :active-section="activeTab" @dirty-change="handleDirtyChange" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'

const activeTab = ref('defaults')
const settingsSectionRef = ref(null)
const hasUnsavedChanges = ref(false)
const isSaving = ref(false)

const settingTabs = [
  { key: 'defaults', label: '默认项配置' },
  { key: 'contentGuard', label: '内容审查配置' }
]

const saveStateText = computed(() => {
  if (isSaving.value) return '保存中'
  return hasUnsavedChanges.value ? '待保存' : '未修改'
})

const saveStateClass = computed(() => ({
  'is-dirty': hasUnsavedChanges.value && !isSaving.value,
  'is-saving': isSaving.value
}))

const handleDirtyChange = (value) => {
  hasUnsavedChanges.value = value
}

const handleSaveAll = async () => {
  if (!settingsSectionRef.value || !hasUnsavedChanges.value || isSaving.value) return
  isSaving.value = true
  try {
    await settingsSectionRef.value.saveAll()
    message.success('保存成功')
  } catch (error) {
    message.error(error.message || '配置保存失败')
  } finally {
    isSaving.value = false
  }
}
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

.save-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.save-state {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  background: var(--gray-50);
  color: var(--gray-600);
  font-size: 13px;
  font-weight: 500;

  &.is-dirty {
    background: color-mix(in srgb, var(--main-color) 8%, var(--gray-0));
    color: var(--main-color);
  }

  &.is-saving {
    background: var(--gray-100);
    color: var(--gray-700);
  }
}
</style>
