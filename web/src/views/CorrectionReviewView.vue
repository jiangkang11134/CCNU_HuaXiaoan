<template>
  <div class="correction-review-view">
    <PageHeader title="反馈审批" :show-border="true">
      <template #actions>
        <a-button
          class="lucide-icon-btn"
          :loading="correctionReviewRef?.refreshing"
          title="刷新"
          @click="correctionReviewRef?.handleRefresh()"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: correctionReviewRef?.refreshing }" />
          </template>
        </a-button>
      </template>
    </PageHeader>
    <div class="correction-review-content">
      <CorrectionReviewComponent ref="correctionReviewRef" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/shared/PageHeader.vue'
import CorrectionReviewComponent from '@/components/CorrectionReviewComponent.vue'

const correctionReviewRef = ref(null)
</script>

<style lang="less" scoped>
.correction-review-view {
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.correction-review-content {
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
