<template>
  <a-modal
    v-model:open="visible"
    :title="null"
    width="90%"
    :style="{ maxWidth: '980px', minWidth: '320px', top: '10%' }"
    :footer="null"
    :closable="false"
    :destroyOnClose="true"
    :bodyStyle="{ padding: 0 }"
    class="settings-modal"
    @cancel="handleClose"
  >
    <div class="settings-container">
      <button class="settings-close-btn lucide-icon-btn" type="button" aria-label="关闭设置" @click="handleClose">
        <X :size="16" />
      </button>

      <div class="settings-sider">
        <div class="settings-sider-nav">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            type="button"
            class="sider-item"
            :class="{ activesec: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            <component :is="tab.icon" class="icon" :size="18" />
            <span>{{ tab.label }}</span>
          </button>
        </div>
      </div>

      <div class="settings-mobile-nav">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="nav-item"
          :class="{ active: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <div class="settings-content-wrapper">
        <div class="settings-content">
          <AccountSettingsComponent v-show="activeTab === 'account'" />
          <UserConfigSettingsCard v-if="activeTab === 'userConfig'" />
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { CircleUser, SlidersHorizontal, X } from 'lucide-vue-next'
import AccountSettingsComponent from '@/components/AccountSettingsComponent.vue'
import UserConfigSettingsCard from '@/components/UserConfigSettingsCard.vue'

const props = defineProps({
  open: {
    type: Boolean,
    default: false,
  },
})

const emit = defineEmits(['update:open'])

const activeTab = ref('account')

const tabs = [
  { key: 'account', label: '账户设置', icon: CircleUser },
  { key: 'userConfig', label: '用户配置', icon: SlidersHorizontal },
]

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
})

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      activeTab.value = 'account'
    }
  },
)

const handleClose = () => {
  emit('update:open', false)
}
</script>

<style lang="less">
.settings-modal.ant-modal {
  .ant-modal-content {
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    position: relative;
    padding: 0;
    overflow: hidden;
  }

  .ant-modal-body {
    padding: 0;
  }
}

.settings-container {
  display: flex;
  height: 70vh;
  width: 100%;
  position: relative;

  @media (max-width: 900px) {
    flex-direction: column;
    height: auto;
    min-height: 70vh;
  }
}

.settings-close-btn {
  position: absolute;
  top: 10px;
  left: 14px;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-700);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 2;

  &:hover {
    background: var(--gray-200);
    color: var(--gray-900);
  }
}

.settings-sider {
  width: 176px;
  height: 100%;
  padding: 52px 10px 12px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-150);

  @media (max-width: 900px) {
    display: none;
  }

  .settings-sider-nav {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
  }

  .sider-item {
    width: 100%;
    padding: 6px 12px;
    cursor: pointer;
    transition: all 0.1s;
    text-align: left;
    font-size: 15px;
    border-radius: 8px;
    color: var(--gray-700);
    display: flex;
    align-items: center;
    gap: 10px;
    border: none;
    background: transparent;

    .icon {
      font-size: 14px;
    }

    &:hover {
      background: var(--gray-50);
    }

    &.activesec {
      background: var(--gray-150);
      color: var(--main-700);
    }
  }
}

.settings-content-wrapper {
  flex: 1;
  height: 100%;
  max-width: calc(100% - 176px);
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  padding: 0;

  @media (max-width: 900px) {
    max-width: 100%;
    padding: 8px;
  }

  .settings-content {
    padding: 16px 16px;
    overflow-y: scroll;
    height: auto;
    flex: 1;
    min-height: 0;

    .header-section {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 16px;
    }

    .header-content {
      flex: 1;
      min-width: 0;
    }

    .section-title {
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
      line-height: 1.4;
      margin: 12px 0 12px;
    }

    .section-description {
      font-size: 14px;
      color: var(--gray-600);
      line-height: 1.4;
      margin: 0;
    }

    .section-subtitle {
      margin: 0;
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
    }

    .add-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    @media (max-width: 900px) {
      height: auto;
      padding: 10px 12px 12px;
    }
  }
}

.settings-mobile-nav {
  display: none;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 0 0 0 42px;
  flex-shrink: 0;

  @media (max-width: 900px) {
    display: flex;
  }

  .nav-item {
    padding: 12px 16px;
    white-space: nowrap;
    cursor: pointer;
    color: var(--gray-600);
    font-weight: 500;
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
    transition: all 0.2s;

    &.active {
      color: var(--main-color);
      border-bottom-color: var(--main-color);
    }
  }
}
</style>
