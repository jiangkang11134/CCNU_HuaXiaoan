<template>
  <div class="my-memory-panel">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">我的记忆</div>
        <p class="section-description">
          系统在对话中观察到的、关于你的信息。只有「已确认」的记忆会被用于回答；
          待确认的记忆需要你确认后才生效。偏好类记忆只影响表达方式（详略、口吻等），
          不会改变安全规范与合规结论。
        </p>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="refresh">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          刷新
        </a-button>
      </div>
    </div>

    <a-alert v-if="error" class="memory-alert" type="error" :message="error" show-icon />

    <!-- 记忆开关关闭时「已确认」并不等于生效，必须明说，否则这页就是在误导用户 -->
    <a-alert
      v-if="!memoryEnabled"
      class="memory-alert"
      type="info"
      show-icon
      message="长期记忆当前已关闭"
      description="下面的记忆不会被用于回答（当前对话内的事实不受影响）。可在「用户配置」中重新开启。"
    />

    <a-spin :spinning="loading">
      <div class="filter-bar">
        <a-segmented v-model:value="filter" :options="filterOptions" size="small" />
        <span class="filter-hint">共 {{ items.length }} 条</span>
      </div>

      <a-alert
        v-if="truncated"
        class="memory-alert"
        type="warning"
        show-icon
        message="仅列出最近 100 条记忆，更早的没有显示。"
      />

      <a-empty v-if="!loading && visibleItems.length === 0" :description="emptyText" />
      <ul v-else class="memory-list">
        <li v-for="row in visibleItems" :key="row.id" class="memory-item">
          <div class="memory-main">
            <div class="memory-content">{{ row.content }}</div>
            <div class="memory-meta">
              <a-tag :color="statusMeta(row.status).color" class="meta-tag">
                {{ statusMeta(row.status).label }}
              </a-tag>
              <span class="meta-key" :title="`fact_key: ${row.fact_key}`">{{ row.fact_key }}</span>
              <span class="meta-sep">·</span>
              <span class="meta-text" :title="formatFullDateTime(row.created_at)">
                {{ formatRelative(row.created_at) }}记下
              </span>
              <template v-if="needsConfirm(row)">
                <span class="meta-sep">·</span>
                <span class="meta-text">置信度 {{ confidenceText(row.confidence) }}</span>
              </template>
              <span class="meta-sep">·</span>
              <span class="meta-text" :title="expiryTitle(row)">{{ expiryText(row) }}</span>
            </div>
          </div>
          <div class="memory-actions">
            <template v-if="needsConfirm(row)">
              <a-button
                type="primary"
                size="small"
                :loading="busyId === row.id"
                @click="confirm(row)"
              >
                确认
              </a-button>
              <a-popconfirm
                title="不再记录这条信息？"
                ok-text="不记录"
                cancel-text="取消"
                @confirm="remove(row)"
              >
                <a-button size="small" :disabled="busyId === row.id">不记录</a-button>
              </a-popconfirm>
            </template>
            <a-popconfirm
              v-else
              title="撤回后这条记忆不再影响你的对话，变更记录会保留。"
              ok-text="撤回"
              cancel-text="取消"
              @confirm="retract(row)"
            >
              <a-button size="small" :loading="busyId === row.id">不再使用</a-button>
            </a-popconfirm>
            <a-button
              class="lucide-icon-btn"
              size="small"
              title="变更记录"
              @click="openEvents(row)"
            >
              <template #icon><ClipboardList :size="14" /></template>
            </a-button>
          </div>
        </li>
      </ul>
    </a-spin>

    <a-modal v-model:open="eventsOpen" :title="eventsTitle" :footer="null" width="560px">
      <a-spin :spinning="eventsLoading">
        <a-empty v-if="events.length === 0" description="暂无变更记录" />
        <ul v-else class="event-list">
          <li v-for="event in events" :key="event.event_id" class="event-item">
            <div class="event-head">
              <span class="event-type">{{ eventLabel(event.event_type) }}</span>
              <span class="event-time">{{ formatFullDateTime(event.created_at) }}</span>
            </div>
            <div class="event-body">
              <span class="event-actor">{{ actorLabel(event.actor_type) }}</span>
              <span v-if="event.before_status || event.after_status" class="event-status">
                {{ event.before_status || '—' }} → {{ event.after_status || '—' }}
              </span>
            </div>
          </li>
        </ul>
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ClipboardList, RefreshCw } from 'lucide-vue-next'
import { myMemoryApi } from '@/apis/memory_api'
import { userConfigApi } from '@/apis/user_config_api'
import { formatFullDateTime, formatRelative, parseToShanghai } from '@/utils/time'

// 后端 GET /memory 的 limit 上限，也是本页一次能展示的条数上限。
const PAGE_LIMIT = 100
// 需要用户拍板才会生效的状态；已 confirmed 再确认会被状态机拒（409）。
const PENDING_STATUSES = ['candidate', 'pending_confirmation']
const STATUS_META = {
  candidate: { label: '候选', color: 'gold' },
  pending_confirmation: { label: '待确认', color: 'gold' },
  confirmed: { label: '已确认', color: 'green' }
}
const EVENT_LABELS = {
  observed: '系统观察到',
  promote: '已生效',
  confirmed: '已确认',
  retracted: '已撤回',
  rejected: '已丢弃',
  tombstoned: '已删除',
  superseded: '被新信息取代',
  conflicted: '与既有记忆冲突',
  renewed: '有效期已续期',
  reclassified: '已改判事实类型',
  expired: '已过期'
}
const ACTOR_LABELS = { user: '你', admin: '管理员', system: '系统' }

const loading = ref(false)
const error = ref('')
const items = ref([])
// 默认按开启处理：读不到配置时宁可不提示，也不要误报"记忆已关闭"
const memoryEnabled = ref(true)
const filter = ref('all')
const busyId = ref(null)
const eventsOpen = ref(false)
const eventsLoading = ref(false)
const events = ref([])
const eventsTitle = ref('变更记录')

const needsConfirm = (row) => PENDING_STATUSES.includes(row.status)
const statusMeta = (status) => STATUS_META[status] || { label: status, color: 'default' }
const confidenceText = (value) => {
  const num = Number(value)
  return Number.isFinite(num) ? `${Math.round(num * 100)}%` : '未知'
}
const eventLabel = (type) => EVENT_LABELS[type] || type || '变更'
const actorLabel = (type) => ACTOR_LABELS[type] || type || '系统'

// 有效期：后端 expires_at 为空**不是**"未知"，而是"不过期"（多数事实键都是这样）。
// 目前只有「研究方向」（30 天）与「工作环境」（7 天）会过期，且**再次提到就会从当天重新计时**——
// 这两句话都得让用户看得见，否则他只会看到一个数字掉到 0 然后记忆"莫名消失"。
const expiryText = (row) => {
  const end = parseToShanghai(row.expires_at)
  if (!end) return '长期有效'
  const hours = end.diff(parseToShanghai(Date.now()), 'hour')
  if (hours <= 0) return '即将到期'
  return `有效期还剩 ${Math.ceil(hours / 24)} 天`
}

const expiryTitle = (row) =>
  row.expires_at
    ? `有效期至 ${formatFullDateTime(row.expires_at)}。到期后这条不再影响对话，记录与变更历史会保留；再次提到它会重新计时。`
    : '长期有效：这条不会自动过期，只有你自己撤回或它被新信息取代时才会失效。'

const truncated = computed(() => items.value.length >= PAGE_LIMIT)

const counts = computed(() => ({
  all: items.value.length,
  todo: items.value.filter(needsConfirm).length,
  confirmed: items.value.filter((row) => row.status === 'confirmed').length
}))

const filterOptions = computed(() => [
  { label: `全部 ${counts.value.all}`, value: 'all' },
  { label: `待确认 ${counts.value.todo}`, value: 'todo' },
  { label: `已确认 ${counts.value.confirmed}`, value: 'confirmed' }
])

const visibleItems = computed(() => {
  if (filter.value === 'todo') return items.value.filter(needsConfirm)
  if (filter.value === 'confirmed') return items.value.filter((row) => row.status === 'confirmed')
  return items.value
})

const emptyText = computed(() => {
  if (filter.value === 'todo') return '没有待确认的记忆'
  if (filter.value === 'confirmed') return '还没有已确认的记忆'
  return '系统还没有记录关于你的信息'
})

const reload = async () => {
  loading.value = true
  error.value = ''
  try {
    const res = await myMemoryApi.list({ limit: PAGE_LIMIT })
    items.value = res?.items || []
  } catch (err) {
    error.value = err.message || '加载记忆失败'
  } finally {
    loading.value = false
  }
}

const loadMemorySwitch = async () => {
  try {
    const res = await userConfigApi.get()
    memoryEnabled.value = res?.enable_memory !== false
  } catch {
    // 配置接口失败不影响记忆列表本身，保持 memoryEnabled=true 不提示
  }
}

// 三个写操作共用一套「禁用按钮 → 调接口 → 刷新列表」的节奏；
// 每次都重新拉列表而不是本地改状态，避免与状态机实际结果不一致。
const runAction = async (row, action, successText) => {
  busyId.value = row.id
  try {
    await action()
    message.success(successText)
    await reload()
  } catch (err) {
    message.error(err.message || '操作失败')
  } finally {
    busyId.value = null
  }
}

const confirm = (row) =>
  runAction(row, () => myMemoryApi.confirm(row.id), '已确认，系统之后会按这条记忆回答')

const remove = (row) => runAction(row, () => myMemoryApi.remove(row.id), '已不再记录')

const retract = (row) => runAction(row, () => myMemoryApi.retract(row.id), '已撤回')

const openEvents = async (row) => {
  eventsOpen.value = true
  eventsTitle.value = `变更记录 · ${row.fact_key}`
  events.value = []
  eventsLoading.value = true
  try {
    const res = await myMemoryApi.events({
      target_type: 'user_memory',
      target_id: row.id,
      limit: 50
    })
    events.value = res?.items || []
  } catch (err) {
    message.error(err.message || '加载变更记录失败')
  } finally {
    eventsLoading.value = false
  }
}

const refresh = async () => {
  await Promise.all([reload(), loadMemorySwitch()])
}

onMounted(refresh)
</script>

<style lang="less" scoped>
.my-memory-panel {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 12px;

    @media (max-width: 760px) {
      align-items: stretch;
      flex-direction: column;
    }
  }

  .header-content {
    flex: 1;
    min-width: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .memory-alert {
    margin-bottom: 12px;
  }

  .filter-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
  }

  .filter-hint {
    color: var(--gray-600);
    font-size: 13px;
    white-space: nowrap;
  }

  .memory-list {
    margin: 0;
    padding: 0;
    list-style: none;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;
  }

  .memory-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    padding: 14px 16px;

    & + .memory-item {
      border-top: 1px solid var(--gray-150);
    }

    @media (max-width: 640px) {
      flex-direction: column;
      align-items: stretch;
      gap: 10px;
    }
  }

  .memory-main {
    min-width: 0;
    flex: 1;
  }

  .memory-content {
    color: var(--gray-900);
    font-size: 14px;
    line-height: 1.5;
    word-break: break-word;
  }

  .memory-meta {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 6px;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.4;
  }

  .meta-tag {
    margin-inline-end: 0;
  }

  .meta-key {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--gray-500);
  }

  .meta-sep {
    color: var(--gray-400);
  }

  .memory-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .event-list {
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .event-item {
    padding: 10px 0;

    & + .event-item {
      border-top: 1px solid var(--gray-150);
    }
  }

  .event-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
  }

  .event-type {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
  }

  .event-time {
    color: var(--gray-500);
    font-size: 12px;
    white-space: nowrap;
  }

  .event-body {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 4px;
    color: var(--gray-600);
    font-size: 12px;
  }

  .event-status {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
}

:deep(.spin) {
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
