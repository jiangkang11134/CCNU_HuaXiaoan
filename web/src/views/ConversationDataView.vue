<template>
  <div class="conversation-data-view">
    <PageHeader title="对话数据" :loading="loading" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ conversations.length }} 条当前结果</span>
          <span>仅用于运营查看</span>
        </div>
      </template>
      <template #actions>
        <a-button class="lucide-icon-btn" @click="openFeedbacks">
          <MessageSquareWarning :size="14" />
          反馈记录
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadConversations" :loading="loading">
          <RefreshCw :size="14" :class="{ spinning: loading }" />
          刷新
        </a-button>
      </template>
    </PageHeader>

    <PageShoulder v-model:search="filters.uid" search-placeholder="按用户 UID 筛选">
      <template #filters>
        <a-input v-model:value="filters.agent_id" allow-clear class="filter-input" placeholder="智能体 ID" />
        <a-select v-model:value="filters.status" class="status-select" :options="statusOptions" />
      </template>
    </PageShoulder>

    <div class="conversation-table-shell">
      <a-table
        :columns="columns"
        :data-source="conversations"
        :loading="loading"
        :pagination="pagination"
        row-key="thread_id"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'title'">
            <button type="button" class="title-link" @click="openDetail(record)">
              {{ record.title || '未命名对话' }}
            </button>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="record.status === 'active' ? 'green' : 'default'">
              {{ record.status }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'updated_at'">
            {{ formatDate(record.updated_at) }}
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-button size="small" @click="openDetail(record)">查看详情</a-button>
          </template>
        </template>
      </a-table>
    </div>

    <a-modal
      v-model:open="detailOpen"
      title="对话详情"
      width="860px"
      :footer="null"
      :destroy-on-close="true"
    >
      <a-spin :spinning="detailLoading">
        <div v-if="selectedDetail" class="detail-panel">
          <div class="detail-meta">
            <span>用户：{{ selectedDetail.uid }}</span>
            <span>智能体：{{ selectedDetail.agent_id }}</span>
            <span>消息数：{{ selectedDetail.message_count }}</span>
            <span>Token：{{ selectedDetail.total_tokens }}</span>
          </div>
          <div class="message-list">
            <div v-for="message in selectedDetail.messages" :key="message.id" class="message-item">
              <div class="message-role">{{ message.role }}</div>
              <pre class="message-content">{{ message.content }}</pre>
            </div>
          </div>
        </div>
      </a-spin>
    </a-modal>

    <FeedbackModalComponent ref="feedbackModal" />
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { MessageSquareWarning, RefreshCw } from 'lucide-vue-next'
import { dashboardApi } from '@/apis/dashboard_api'
import PageHeader from '@/components/shared/PageHeader.vue'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import FeedbackModalComponent from '@/components/dashboard/FeedbackModalComponent.vue'
import dayjs from '@/utils/time'

const loading = ref(false)
const detailLoading = ref(false)
const detailOpen = ref(false)
const conversations = ref([])
const selectedDetail = ref(null)
const feedbackModal = ref(null)

const filters = reactive({
  uid: '',
  agent_id: '',
  status: 'active'
})

const pagination = reactive({
  current: 1,
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['20', '50', '100']
})

const statusOptions = [
  { label: '活跃对话', value: 'active' },
  { label: '已删除', value: 'deleted' },
  { label: '全部状态', value: 'all' }
]

const columns = [
  { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
  { title: '用户 UID', dataIndex: 'uid', key: 'uid', width: 140 },
  { title: '智能体', dataIndex: 'agent_id', key: 'agent_id', width: 180, ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
  { title: '消息数', dataIndex: 'message_count', key: 'message_count', width: 90 },
  { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 180 },
  { title: '操作', key: 'actions', width: 100 }
]

const buildQuery = () => ({
  uid: filters.uid.trim() || undefined,
  agent_id: filters.agent_id.trim() || undefined,
  status: filters.status,
  limit: pagination.pageSize,
  offset: (pagination.current - 1) * pagination.pageSize
})

const loadConversations = async () => {
  loading.value = true
  try {
    const rows = await dashboardApi.getConversations(buildQuery())
    conversations.value = rows
    pagination.total = rows.length < pagination.pageSize ? (pagination.current - 1) * pagination.pageSize + rows.length : pagination.current * pagination.pageSize + 1
  } catch (error) {
    message.error(error.message || '加载对话数据失败')
  } finally {
    loading.value = false
  }
}

const openDetail = async (record) => {
  detailOpen.value = true
  detailLoading.value = true
  selectedDetail.value = null
  try {
    selectedDetail.value = await dashboardApi.getConversationDetail(record.thread_id)
  } catch (error) {
    message.error(error.message || '加载对话详情失败')
  } finally {
    detailLoading.value = false
  }
}

const openFeedbacks = () => {
  feedbackModal.value?.show()
}

const handleTableChange = (nextPagination) => {
  pagination.current = nextPagination.current
  pagination.pageSize = nextPagination.pageSize
  loadConversations()
}

const formatDate = (value) => {
  if (!value) return ''
  return dayjs(value).tz('Asia/Shanghai').format('YYYY-MM-DD HH:mm:ss')
}

watch(
  () => [filters.uid, filters.agent_id, filters.status],
  () => {
    pagination.current = 1
    loadConversations()
  }
)

onMounted(loadConversations)
</script>

<style lang="less" scoped>
.conversation-data-view {
  min-height: 100%;
  background: var(--gray-0);
}

.summary-strip {
  display: flex;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }
}

.filter-input,
.status-select {
  width: 180px;
}

.conversation-table-shell {
  padding: 16px var(--page-padding) var(--page-padding);
}

.title-link {
  max-width: 100%;
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  span {
    padding: 6px 10px;
    border-radius: 7px;
    background: var(--gray-50);
    color: var(--gray-700);
    font-size: 12px;
  }
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 58vh;
  overflow: auto;
}

.message-item {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.message-role {
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;
}

.message-content {
  margin: 0;
  padding: 12px;
  color: var(--gray-900);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
