<template>
  <a-modal v-model:open="open" title="记忆审计" width="980px" :footer="null" destroy-on-close>
    <div class="toolbar">
      <a-select v-model:value="status" :options="statusOptions" class="status-select" @change="reload" />
      <a-button class="lucide-icon-btn" :loading="loading" @click="reload">
        <RefreshCw :size="14" />
        刷新
      </a-button>
    </div>
    <a-table
      :columns="columns"
      :data-source="rows"
      :loading="loading"
      :pagination="pagination"
      row-key="id"
      size="middle"
      @change="handleChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="record.status === 'confirmed' ? 'green' : 'default'">{{ record.status }}</a-tag>
        </template>
        <template v-else-if="column.key === 'content'">
          <span class="content" :title="record.content">{{ record.content }}</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-popconfirm title="确认撤回并软删除这条记忆？" @confirm="remove(record)">
            <a-button danger size="small" :disabled="!deletable(record.status)">撤回</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>
  </a-modal>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { memoryApi } from '@/apis/memory_api'

const open = ref(false)
const loading = ref(false)
const rows = ref([])
const status = ref('')
const pagination = reactive({ current: 1, pageSize: 20, total: 0, showSizeChanger: true })
const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '候选', value: 'candidate' },
  { label: '待确认', value: 'pending_confirmation' },
  { label: '已确认', value: 'confirmed' },
  { label: '已撤回', value: 'retracted' },
  { label: '已删除', value: 'tombstoned' },
  { label: '已过期', value: 'expired' }
]
const columns = [
  { title: '用户 UID', dataIndex: 'uid', key: 'uid', width: 150 },
  { title: '事实类型', dataIndex: 'fact_key', key: 'fact_key', width: 190 },
  { title: '内容', dataIndex: 'content', key: 'content', ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 110 },
  { title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 90 },
  { title: '操作', key: 'actions', width: 90 }
]

const deletable = (value) => ['candidate', 'pending_confirmation', 'confirmed'].includes(value)
const reload = async () => {
  loading.value = true
  try {
    const result = await memoryApi.listAll({
      status: status.value || undefined,
      limit: pagination.pageSize,
      offset: (pagination.current - 1) * pagination.pageSize
    })
    rows.value = result.items || []
    pagination.total = rows.value.length < pagination.pageSize
      ? (pagination.current - 1) * pagination.pageSize + rows.value.length
      : pagination.current * pagination.pageSize + 1
  } catch (error) {
    message.error(error.message || '加载记忆审计数据失败')
  } finally {
    loading.value = false
  }
}
const remove = async (record) => {
  try {
    await memoryApi.remove(record.uid, record.id)
    message.success('记忆已撤回')
    await reload()
  } catch (error) {
    message.error(error.message || '撤回记忆失败')
  }
}
const handleChange = (next) => {
  pagination.current = next.current
  pagination.pageSize = next.pageSize
  reload()
}
const show = () => {
  open.value = true
  pagination.current = 1
  reload()
}
defineExpose({ show })
</script>

<style lang="less" scoped>
.toolbar { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.status-select { width: 180px; }
.content { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
