<template>
  <div class="settings-page-view">
    <PageHeader title="用户管理" :show-border="true">
      <template #actions>
        <a-button
          class="lucide-icon-btn"
          :loading="userManagementRef?.userManagement?.refreshing"
          title="刷新"
          @click="userManagementRef?.handleRefresh()"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: userManagementRef?.userManagement?.refreshing }" />
          </template>
        </a-button>
        <a-button type="primary" class="lucide-icon-btn" @click="userManagementRef?.showAddUserModal()">
          <template #icon><Plus :size="16" /></template>
          添加用户
        </a-button>
      </template>
    </PageHeader>
    <div class="settings-page-content">
      <UserManagementComponent ref="userManagementRef" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { Plus, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'

const userManagementRef = ref(null)
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

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
