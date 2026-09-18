<template>
  <!-- 反馈列表模态框 -->
  <a-modal v-model:open="modalVisible" title="用户反馈详情" width="1200px" :footer="null">
    <a-space style="margin-bottom: 16px" :size="12" wrap>
      <a-segmented
        v-model:value="feedbackFilter"
        :options="feedbackOptions"
        @change="loadFeedbacks"
      />
      <a-select
        v-model:value="statusFilter"
        :options="statusFilterOptions"
        style="min-width: 180px"
        @change="loadFeedbacks"
      />
    </a-space>

    <!-- 卡片列表 -->
    <div v-if="loadingFeedbacks" class="loading-container">
      <a-spin size="large" />
    </div>

    <div v-else class="feedback-cards-container">
      <div v-for="feedback in feedbacks" :key="feedback.id" class="feedback-card">
        <!-- 卡片头部：用户信息和反馈类型 -->
        <div class="card-header">
          <div class="user-info">
            <FallbackAvatar
              :src="feedback.avatar"
              :default-src="getFeedbackDefaultAvatarSrc(feedback)"
              :name="feedback.username"
              :seed="feedback.uid || feedback.username"
              kind="user"
              :size="32"
              shape="circle"
              :alt="feedback.username"
              class="user-avatar"
            />
            <div class="user-details">
              <div class="username">{{ feedback.username || '未知用户' }}</div>
            </div>
          </div>
          <div class="header-tags">
            <a-tag
              :color="statusColor(feedback.processing_status)"
              size="small"
              class="rating-tag"
            >
              {{ statusLabel(feedback.processing_status) }}
            </a-tag>
            <a-tag
              v-if="feedback.ticket_id"
              color="blue"
              size="small"
              class="rating-tag ticket-tag"
            >
              工单 #{{ feedback.ticket_id }}
            </a-tag>
            <a-tag
              v-else-if="feedback.rating === 'dislike'"
              size="small"
              class="rating-tag ticket-tag"
            >
              未转工单
            </a-tag>
            <a-tag
              :color="feedback.rating === 'like' ? 'green' : 'red'"
              class="rating-tag"
              size="small"
            >
              <template #icon>
                <LikeOutlined v-if="feedback.rating === 'like'" />
                <DislikeOutlined v-else />
              </template>
              {{ feedback.rating === 'like' ? '点赞' : '点踩' }}
            </a-tag>
          </div>
        </div>

        <!-- 卡片内容：对话信息、消息内容和反馈原因 -->
        <div class="card-content">
          <!-- 对话标题 -->
          <div class="conversation-section" v-if="feedback.conversation_title">
            <div class="conversation-info">
              <div class="info-item">
                <span
                  class="conversation-title"
                  :class="{ collapsed: !expandedStates.get(`${feedback.id}-conversation`) }"
                >
                  标题：{{ feedback.conversation_title }}
                </span>
                <a-button
                  v-if="shouldShowConversationExpandButton(feedback.conversation_title)"
                  type="link"
                  size="small"
                  @click="toggleConversationExpand(feedback.id)"
                  class="expand-button-inline"
                >
                  {{ expandedStates.get(`${feedback.id}-conversation`) ? '收起' : '展开' }}
                </a-button>
              </div>
              <div class="info-item" v-if="!props.agentId">
                <span class="label">智能体:</span>
                <span class="value">{{ feedback.agent_id }}</span>
              </div>
            </div>
          </div>

          <!-- 消息内容 -->
          <div class="message-section">
            <div
              class="message-content"
              :class="{ collapsed: !expandedStates.get(`${feedback.id}-message`) }"
            >
              {{ feedback.message_content }}
            </div>
            <a-button
              v-if="shouldShowExpandButton(feedback.message_content)"
              type="link"
              size="small"
              @click="toggleExpand(feedback.id)"
              class="expand-button"
            >
              {{ expandedStates.get(`${feedback.id}-message`) ? '收起' : '展开全部' }}
            </a-button>
          </div>

          <!-- 反馈原因 -->
          <div v-if="feedback.reason" class="reason-section">
            <div class="reason-content">{{ feedback.reason }}</div>
          </div>

          <!-- 处置备注（管理员填写） -->
          <div v-if="feedback.processing_note" class="note-section">
            <div class="note-content">{{ feedback.processing_note }}</div>
          </div>
        </div>

        <!-- 卡片底部：时间 + 处置入口 -->
        <div class="card-footer">
          <div class="footer-row">
            <div class="time-info">
              <ClockCircleOutlined />
              <span>{{ formatFullDate(feedback.created_at) }}</span>
              <span class="priority-hint">优先级 {{ feedback.priority ?? '-' }}</span>
            </div>
            <a-space :size="4">
              <a-tooltip
                :title="canConvert(feedback) ? '' : '只有带理由的点踩、且尚未建单的反馈才能转工单'"
              >
                <a-button
                  type="link"
                  size="small"
                  :disabled="!canConvert(feedback)"
                  @click="openTicketModal(feedback)"
                >
                  转工单
                </a-button>
              </a-tooltip>
              <a-button type="link" size="small" @click="openProcessModal(feedback)">
                处置
              </a-button>
            </a-space>
          </div>
          <div v-if="feedback.processed_by" class="processed-info">
            最近处置：{{ feedback.processed_by }}
            <template v-if="feedback.processed_at">
              · {{ formatFullDate(feedback.processed_at) }}
            </template>
          </div>
        </div>
      </div>

      <!-- 空状态 -->
      <div v-if="feedbacks.length === 0" class="empty-state">
        <a-empty description="暂无反馈数据" />
      </div>
    </div>
  </a-modal>

  <!-- 处置弹窗：改状态 / 优先级 / 备注 -->
  <a-modal
    v-model:open="processModalVisible"
    title="处置反馈"
    :confirm-loading="processSubmitting"
    ok-text="保存"
    cancel-text="取消"
    @ok="submitProcess"
  >
    <a-form layout="vertical">
      <a-form-item label="处置状态">
        <a-select v-model:value="processForm.processing_status" :options="processStatusOptions" />
      </a-form-item>
      <a-form-item label="优先级（0-100，越大越先看）">
        <a-input-number
          v-model:value="processForm.priority"
          :min="0"
          :max="100"
          style="width: 100%"
        />
      </a-form-item>
      <a-form-item label="处置备注">
        <a-textarea
          v-model:value="processForm.processing_note"
          :rows="3"
          :maxlength="2000"
          show-count
          placeholder="记录判断依据，例如「属于个人表达偏好，不转工单」"
        />
      </a-form-item>
    </a-form>
  </a-modal>

  <!-- 转工单弹窗 -->
  <a-modal
    v-model:open="ticketModalVisible"
    title="转为纠错工单"
    :confirm-loading="ticketSubmitting"
    ok-text="确认转单"
    cancel-text="取消"
    @ok="submitTicket"
  >
    <a-alert
      type="info"
      show-icon
      message="工单只会停在待审核状态，不影响线上回答；是否采纳由审核环节决定。"
      style="margin-bottom: 12px"
    />
    <a-form layout="vertical">
      <a-form-item label="作用域">
        <a-select v-model:value="ticketForm.scope" :options="scopeOptions" />
      </a-form-item>
      <a-form-item label="备注（可选）">
        <a-textarea
          v-model:value="ticketForm.note"
          :rows="3"
          :maxlength="2000"
          show-count
          placeholder="留空则自动记为「已由管理员转为纠错工单 #N，待审核」"
        />
      </a-form-item>
    </a-form>
    <a-alert
      v-if="ticketForm.scope === 'kb_truth'"
      type="warning"
      show-icon
      message="知识性偏差会对全体用户生效，请确认这不是个人表达偏好。"
    />
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LikeOutlined, DislikeOutlined, ClockCircleOutlined } from '@ant-design/icons-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'

// 常量配置
const CONFIG = {
  MESSAGE_MAX_LINES: 8, // 消息最大显示行数
  CONVERSATION_MAX_LINES: 2, // 对话标题最大显示行数
  CONVERSATION_MAX_CHARS: 60, // 对话标题字符数阈值
  AVG_CHARS_PER_LINE: 30 // 每行平均字符数（中英文混合）
}

// Props
const props = defineProps({
  agentId: {
    type: String,
    default: null
  }
})

// 模态框状态
const modalVisible = ref(false)

// 反馈相关数据
const feedbacks = ref([])
const loadingFeedbacks = ref(false)
const feedbackFilter = ref('all')
const feedbackOptions = [
  { label: '全部', value: 'all' },
  { label: '点赞', value: 'like' },
  { label: '点踩', value: 'dislike' }
]

// 展开状态映射（使用 Map 避免直接修改对象）
const expandedStates = ref(new Map())

// 显示模态框
const show = () => {
  modalVisible.value = true
  loadStatusOptions()
  loadFeedbacks()
}

// 暴露方法给父组件
defineExpose({ show })

// 计算文本行数的辅助函数（估算）
const estimateLines = (text) => {
  if (!text) return 0
  return Math.ceil(text.length / CONFIG.AVG_CHARS_PER_LINE)
}

// 判断是否显示展开按钮
const shouldShowExpandButton = (content) => {
  return estimateLines(content) > CONFIG.MESSAGE_MAX_LINES
}

// 判断对话标题是否需要展开按钮
const shouldShowConversationExpandButton = (title) => {
  if (!title) return false
  return title.length > CONFIG.CONVERSATION_MAX_CHARS
}

// 切换展开/收起状态
const toggleExpand = (feedbackId) => {
  const key = `${feedbackId}-message`
  const currentState = expandedStates.value.get(key) ?? false
  expandedStates.value.set(key, !currentState)
}

// 切换对话标题展开/收起状态
const toggleConversationExpand = (feedbackId) => {
  const key = `${feedbackId}-conversation`
  const currentState = expandedStates.value.get(key) ?? false
  expandedStates.value.set(key, !currentState)
}

// 加载反馈列表
const loadFeedbacks = async () => {
  loadingFeedbacks.value = true
  try {
    const params = {
      rating: feedbackFilter.value === 'all' ? undefined : feedbackFilter.value,
      agent_id: props.agentId || undefined,
      processing_status: statusFilter.value || undefined
    }

    const response = await dashboardApi.getFeedbacks(params)
    feedbacks.value = response
    // 重置展开状态
    expandedStates.value.clear()
  } catch (error) {
    console.error('加载反馈列表失败:', error)
    message.error('加载反馈列表失败，请稍后重试')
    feedbacks.value = []
  } finally {
    loadingFeedbacks.value = false
  }
}

const getFeedbackDefaultAvatarSrc = (feedback) =>
  feedback.uid ? generatePixelAvatar(feedback.uid) : ''

// 格式化完整日期
const formatFullDate = (dateString) => formatFullDateTime(dateString)

// ---------------------------------------------------------------------------
// 反馈处置（收集层 → 处置层的人工闸门）
//
// 学生的点踩不会自动建单，只会停在 message_feedbacks。这里给管理员补上出口：
// 改处置状态/优先级/备注，或把它转成待审核的纠错工单。
// ---------------------------------------------------------------------------

// 处置状态词表来自后端（唯一口径），不要在前端硬编码
const statusOptions = ref([])
const statusFilter = ref('')

const statusLabelMap = computed(() => {
  const map = {}
  statusOptions.value.forEach((item) => {
    map[item.value] = item.label
  })
  return map
})

const statusLabel = (value) => statusLabelMap.value[value] || value || '-'

// 只影响展示配色；未知状态回落到默认灰
const STATUS_COLORS = {
  backlog: 'default',
  faculty_pending: 'orange',
  admin_pending: 'gold',
  ready_for_review: 'blue',
  ticketed: 'cyan',
  resolved: 'green',
  dismissed: 'default'
}
const statusColor = (value) => STATUS_COLORS[value] || 'default'

const statusFilterOptions = computed(() => [
  { label: '全部处置状态', value: '' },
  ...statusOptions.value
])

const processStatusOptions = computed(() => statusOptions.value)

// 作用域：空值交给后端按内容自动判定（与点踩自动建单同一套判定）
const scopeOptions = [
  { label: '自动判定（按内容）', value: '' },
  { label: '知识性偏差 · 全局生效', value: 'kb_truth' },
  { label: '个人偏好 · 仅本人生效', value: 'user_pref' },
  { label: '部门规则 · 同部门生效', value: 'dept_rule' }
]

const loadStatusOptions = async () => {
  if (statusOptions.value.length) return
  try {
    statusOptions.value = await dashboardApi.getFeedbackStatuses()
  } catch (error) {
    // 拿不到词表不阻塞列表：状态列会退化成显示原始值
    console.error('加载处置状态词表失败:', error)
  }
}

// 只有"带理由的点踩、且尚未建单"才可转工单——后端也会再校验一次
const canConvert = (feedback) =>
  feedback.rating === 'dislike' && !!feedback.reason && !feedback.ticket_id

// 处置弹窗
const processModalVisible = ref(false)
const processSubmitting = ref(false)
const processForm = ref({ id: null, processing_status: 'backlog', priority: 50, processing_note: '' })

const openProcessModal = (feedback) => {
  processForm.value = {
    id: feedback.id,
    processing_status: feedback.processing_status || 'backlog',
    priority: feedback.priority ?? 50,
    processing_note: feedback.processing_note || ''
  }
  processModalVisible.value = true
}

const submitProcess = async () => {
  processSubmitting.value = true
  try {
    await dashboardApi.updateFeedback(processForm.value.id, {
      processing_status: processForm.value.processing_status,
      priority: processForm.value.priority,
      processing_note: processForm.value.processing_note
    })
    message.success('已保存处置结果')
    processModalVisible.value = false
    await loadFeedbacks()
  } catch (error) {
    console.error('保存处置结果失败:', error)
    message.error(error?.message || '保存失败，请稍后重试')
  } finally {
    processSubmitting.value = false
  }
}

// 转工单弹窗
const ticketModalVisible = ref(false)
const ticketSubmitting = ref(false)
const ticketForm = ref({ id: null, scope: '', note: '' })

const openTicketModal = (feedback) => {
  ticketForm.value = { id: feedback.id, scope: '', note: '' }
  ticketModalVisible.value = true
}

const submitTicket = async () => {
  ticketSubmitting.value = true
  try {
    const res = await dashboardApi.convertFeedbackToTicket(ticketForm.value.id, {
      // 空串表示"交给后端自动判定"，不要传成空 scope
      scope: ticketForm.value.scope || undefined,
      note: ticketForm.value.note || undefined
    })
    message.success(`已转为纠错工单 #${res.ticket_id}，等待审核`)
    ticketModalVisible.value = false
    await loadFeedbacks()
  } catch (error) {
    console.error('转工单失败:', error)
    message.error(error?.message || '转工单失败，请稍后重试')
  } finally {
    ticketSubmitting.value = false
  }
}

// 监听 agentId 变化，重新加载数据
watch(
  () => props.agentId,
  () => {
    if (modalVisible.value) {
      loadFeedbacks()
    }
  }
)
</script>

<style scoped lang="less">
// 加载状态
.loading-container {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 40px 0;
}

// 卡片容器 - 自适应多列布局
.feedback-cards-container {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  max-height: 600px;
  overflow-y: auto;
  padding-right: 8px;

  // 滚动条样式
  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-track {
    background: var(--gray-100);
    border-radius: 3px;
  }

  &::-webkit-scrollbar-thumb {
    background: var(--gray-300);
    border-radius: 3px;

    &:hover {
      background: var(--gray-400);
    }
  }
}

// 反馈卡片 - 紧凑设计
.feedback-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  transition: all 0.2s ease;
  display: flex;
  flex-direction: column;

  &:hover {
    border-color: var(--main-color);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  }
}

// 卡片头部 - 紧凑
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid var(--gray-100);
  background: var(--gray-25);
  border-radius: 8px 8px 0 0;
}

.user-info {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-avatar {
  flex-shrink: 0;
}

.user-details {
  .username {
    font-weight: 500;
    color: var(--gray-900);
    font-size: 13px;
    line-height: 1.2;
  }
}

.rating-tag {
  font-weight: 500;
  font-size: 11px;
}

.ticket-tag {
  margin-right: 4px;
}

// 卡片内容 - 紧凑
.card-content {
  padding: 16px;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.message-section {
  flex: 1;
}

.message-content {
  background: var(--gray-50);
  padding: 10px;
  border-radius: 6px;
  // border-left: 3px solid var(--main-color);
  font-size: 13px;
  line-height: 1.4;
  color: var(--gray-800);
  word-break: break-word;
  overflow: hidden;
  transition: max-height 0.3s ease;
}

.message-content.collapsed {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 8;
  line-clamp: 8;
  overflow: hidden;
  text-overflow: ellipsis;
}

.expand-button {
  padding: 0;
  height: auto;
  font-size: 12px;
  margin-top: 8px;
  color: var(--main-color);
}

.conversation-section {
  margin: 0;
}

.conversation-info {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.info-item {
  display: flex;
  align-items: center;
  font-size: 12px;

  .label {
    color: var(--gray-600);
    margin-right: 6px;
    min-width: 50px;
    font-weight: 500;
  }

  .value {
    color: var(--gray-800);
    font-weight: 400;
    word-break: break-all;
  }

  // 对话标题样式（独立显示）
  .conversation-title {
    display: block;
    color: var(--gray-700);
    font-size: 13px;
    font-weight: 500;
    line-height: 1.4;
    word-break: break-word;
    transition: all 0.3s ease;

    &.collapsed {
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  // 包含对话标题的 info-item 改为垂直布局
  &:has(.conversation-title) {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
}

.expand-button-inline {
  padding: 0;
  height: auto;
  font-size: 11px;
  color: var(--main-color);
  align-self: flex-start;
}

.reason-section {
  margin: 0;
}

.reason-content {
  background: var(--color-warning-50);
  padding: 10px;
  border-radius: 6px;
  border-left: 3px solid var(--color-warning-500);
  font-size: 13px;
  line-height: 1.4;
  color: var(--gray-800);
  word-break: break-word;
}

// 卡片底部 - 紧凑
.card-footer {
  padding: 8px 16px;
  border-top: 1px solid var(--gray-100);
  background: var(--gray-25);
  border-radius: 0 0 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.footer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.time-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--gray-500);
}

.priority-hint {
  color: var(--gray-400);
}

.processed-info {
  font-size: 11px;
  color: var(--gray-500);
  word-break: break-all;
}

// 卡片头部的标签组（状态 / 工单 / 好恶）
.header-tags {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 4px;
}

// 处置备注（管理员填写），与用户反馈原因区分开
.note-section {
  margin: 0;
}

.note-content {
  background: var(--gray-50);
  padding: 10px;
  border-radius: 6px;
  border-left: 3px solid var(--main-color);
  font-size: 12px;
  line-height: 1.4;
  color: var(--gray-700);
  word-break: break-word;
}

// 空状态
.empty-state {
  grid-column: 1 / -1;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 60px 0;
}

// 响应式设计
@media (max-width: 768px) {
  .feedback-cards-container {
    grid-template-columns: 1fr;
    gap: 12px;
  }

  .card-header {
    padding: 10px 12px;
    gap: 8px;
  }

  .card-content {
    padding: 12px;
    gap: 10px;
  }

  .card-footer {
    padding: 6px 12px;
  }
}

@media (max-width: 480px) {
  .feedback-cards-container {
    gap: 8px;
  }

  .card-header {
    padding: 8px 10px;
  }

  .card-content {
    padding: 10px;
    gap: 8px;
  }

  .message-content,
  .reason-content {
    padding: 8px;
    font-size: 12px;
  }

  .info-item {
    font-size: 11px;
  }
}
</style>
