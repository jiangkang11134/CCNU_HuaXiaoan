<template>
  <a-modal
    v-model:open="open"
    title="个人设置"
    width="900px"
    :footer="null"
    :destroy-on-close="true"
    :body-style="{ padding: 0 }"
    class="personal-settings-modal"
  >
    <div class="personal-settings-shell">
      <div class="personal-settings-nav">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="personal-settings-tab"
          :class="{ active: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          <component :is="tab.icon" :size="16" />
          <span>{{ tab.label }}</span>
        </button>
      </div>

      <div class="personal-settings-content">
        <AccountSettingsComponent v-show="activeTab === 'account'" />
        <UserConfigSettingsCard v-show="activeTab === 'userConfig'" />
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { ref } from 'vue'
import { CircleUser, SlidersHorizontal } from 'lucide-vue-next'
import AccountSettingsComponent from '@/components/AccountSettingsComponent.vue'
import UserConfigSettingsCard from '@/components/UserConfigSettingsCard.vue'

const open = defineModel('open', { type: Boolean, default: false })
const activeTab = ref('account')

const tabs = [
  { key: 'account', label: '账户设置', icon: CircleUser },
  { key: 'userConfig', label: '用户配置', icon: SlidersHorizontal }
]
</script>

<style lang="less" scoped>
.personal-settings-shell {
  display: flex;
  min-height: 620px;
  max-height: 78vh;
  background: var(--gray-0);
}

.personal-settings-nav {
  width: 168px;
  flex: 0 0 168px;
  padding: 14px 10px;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-50);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.personal-settings-tab {
  width: 100%;
  border: none;
  background: transparent;
  color: var(--gray-700);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 14px;
  text-align: left;

  &:hover {
    background: var(--gray-100);
  }

  &.active {
    background: var(--gray-150);
    color: var(--main-700);
  }
}

.personal-settings-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  padding: 16px;
}

@media (max-width: 760px) {
  .personal-settings-shell {
    flex-direction: column;
    min-height: 0;
  }

  .personal-settings-nav {
    width: 100%;
    flex-basis: auto;
    flex-direction: row;
    border-right: none;
    border-bottom: 1px solid var(--gray-150);
  }

  .personal-settings-tab {
    justify-content: center;
  }
}
</style>
