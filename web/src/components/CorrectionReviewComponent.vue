<template>
  <div class="correction-review">
    <a-tabs v-model:activeKey="activeKey" class="correction-tabs" @change="handleTabChange">
      <!-- Tab 1：待审核 -->
      <a-tab-pane key="pending">
        <template #tab>
          <span class="tab-title">
            待审核
            <a-badge
              :count="tabTotal('pending')"
              :show-zero="false"
              :overflow-count="999"
              class="tab-badge"
            />
          </span>
        </template>

        <a-spin :spinning="state.pending.loading">
          <div v-if="state.pending.error" class="error-message">
            <a-alert type="error" :message="state.pending.error" show-icon />
          </div>

          <template v-if="state.pending.rows.length > 0">
            <a-table
              :columns="pendingColumns"
              :data-source="state.pending.rows"
              :pagination="state.pending.pagination"
              :loading="state.pending.loading"
              row-key="id"
              size="middle"
              :scroll="{ x: 1440 }"
              class="correction-table"
              @change="onPendingTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'id'">
                  <a-tooltip>
                    <template #title>{{ record.id }}</template>
                    <span class="ellipsis-text">{{ record.id }}</span>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'uid'">
                  <a-tooltip>
                    <template #title>{{ record.uid || '-' }}</template>
                    <span class="ellipsis-text">{{ record.uid || '-' }}</span>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'kb_id'">
                  <a-tag v-if="record.kb_id" color="blue">{{ record.kb_id }}</a-tag>
                  <a-tag v-else>全局</a-tag>
                </template>
                <template v-else-if="column.key === 'risk_level'">
                  <a-tag :color="riskMeta(record.risk_level).color">
                    {{ riskMeta(record.risk_level).label }}
                  </a-tag>
                </template>
                <template v-else-if="column.key === 'scope'">
                  <a-tag :color="scopeMeta(record).color">
                    {{ scopeMeta(record).label }}
                  </a-tag>
                  <div v-if="!record.scope_source" class="scope-hint">待确认</div>
                </template>
                <template v-else-if="column.key === 'original_content'">
                  <a-tooltip>
                    <template #title>
                      <div class="tooltip-text">{{ record.original_content || '-' }}</div>
                    </template>
                    <div class="content-clamp">{{ record.original_content || '-' }}</div>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'proposed_content'">
                  <a-tooltip>
                    <template #title>
                      <div class="tooltip-text">{{ record.proposed_content || '-' }}</div>
                    </template>
                    <div class="content-clamp">{{ record.proposed_content || '-' }}</div>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'submit_time'">
                  <span class="time-text">{{ formatDateTime(record.created_at) }}</span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-button type="primary" size="small" @click="openReviewModal(record)">
                    审核
                  </a-button>
                </template>
              </template>
            </a-table>
          </template>

          <div v-else class="empty-state">
            <ResourceEmptyState
              title="暂无待审核的反馈"
              description="老师提交纠错反馈后，会先进入这里等待管理员审核。"
              size="compact"
            />
          </div>
        </a-spin>
      </a-tab-pane>

      <!-- Tab 2：已生效 / 待复核 -->
      <a-tab-pane key="active">
        <template #tab>
          <span class="tab-title">
            已生效 / 待复核
            <a-badge
              :count="tabTotal('active')"
              :show-zero="false"
              :overflow-count="999"
              class="tab-badge"
            />
          </span>
        </template>

        <a-spin :spinning="state.active.loading">
          <div v-if="state.active.error" class="error-message">
            <a-alert type="error" :message="state.active.error" show-icon />
          </div>

          <template v-if="state.active.rows.length > 0">
            <a-table
              :columns="activeColumns"
              :data-source="state.active.rows"
              :pagination="state.active.pagination"
              :loading="state.active.loading"
              row-key="id"
              size="middle"
              :scroll="{ x: 1400 }"
              class="correction-table"
              @change="onActiveTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'id'">
                  <a-tooltip>
                    <template #title>{{ record.id }}</template>
                    <span class="ellipsis-text">{{ record.id }}</span>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'kb_id'">
                  <a-tag v-if="record.kb_id" color="blue">{{ record.kb_id }}</a-tag>
                  <a-tag v-else>全局</a-tag>
                </template>
                <template v-else-if="column.key === 'final_content'">
                  <a-tooltip>
                    <template #title>
                      <div class="tooltip-text">{{ record.final_content || '-' }}</div>
                    </template>
                    <div class="content-clamp">{{ record.final_content || '-' }}</div>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'entity_hints'">
                  <div class="tag-row">
                    <a-tag v-for="(item, index) in record.entity_hints || []" :key="index">
                      {{ item }}
                    </a-tag>
                    <span v-if="!(record.entity_hints || []).length" class="muted-text">—</span>
                  </div>
                </template>
                <template v-else-if="column.key === 'intent_tags'">
                  <div class="tag-row">
                    <a-tag
                      v-for="(item, index) in record.intent_tags || []"
                      :key="index"
                      color="geekblue"
                    >
                      {{ item }}
                    </a-tag>
                    <span v-if="!(record.intent_tags || []).length" class="muted-text">—</span>
                  </div>
                </template>
                <template v-else-if="column.key === 'confidence'">
                  <span>{{ formatConfidence(record.confidence) }}</span>
                </template>
                <template v-else-if="column.key === 'needs_review'">
                  <a-tooltip v-if="record.needs_review">
                    <template #title>
                      <div class="tooltip-text">
                        {{ record.needs_review_reason || '源文档已更新，需确认修正内容是否仍然有效' }}
                      </div>
                    </template>
                    <a-tag color="orange">待复核</a-tag>
                  </a-tooltip>
                  <span v-else class="muted-text">—</span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <div class="action-row">
                    <a-button v-if="record.needs_review" size="small" @click="confirmAckReview(record)">
                      复核确认
                    </a-button>
                    <a-button
                      size="small"
                      type="primary"
                      :loading="writingId === record.id"
                      @click="writeToGraph(record)"
                    >
                      {{ rewriteRejected(record) ? '重新改写并写回' : '写回图谱' }}
                    </a-button>
                  </div>
                </template>
              </template>
            </a-table>
          </template>

          <div v-else class="empty-state">
            <ResourceEmptyState
              title="暂无已生效的反馈修正"
              description="审核通过的修正会在这里展示，源文档更新后需要复核的条目会带「待复核」标记。"
              size="compact"
            />
          </div>
        </a-spin>
      </a-tab-pane>

      <!-- Tab 3：已入图（可撤回） -->
      <a-tab-pane key="graph">
        <template #tab>
          <span class="tab-title">
            已入图
            <a-badge
              :count="tabTotal('graph')"
              :show-zero="false"
              :overflow-count="999"
              class="tab-badge"
            />
          </span>
        </template>

        <a-spin :spinning="state.graph.loading">
          <div v-if="state.graph.error" class="error-message">
            <a-alert type="error" :message="state.graph.error" show-icon />
          </div>

          <template v-if="state.graph.rows.length > 0">
            <a-table
              :columns="graphColumns"
              :data-source="state.graph.rows"
              :pagination="state.graph.pagination"
              :loading="state.graph.loading"
              row-key="id"
              size="middle"
              :scroll="{ x: 1200 }"
              class="correction-table"
              @change="onGraphTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'id'">
                  <span class="ellipsis-text">{{ record.id }}</span>
                </template>
                <template v-else-if="column.key === 'kb_id'">
                  <a-tag v-if="record.kb_id" color="blue">{{ record.kb_id }}</a-tag>
                  <a-tag v-else>全局</a-tag>
                </template>
                <template v-else-if="column.key === 'rewrite_text'">
                  <a-tooltip>
                    <template #title>
                      <div class="tooltip-text">{{ record.rewrite_text || record.final_content || '-' }}</div>
                    </template>
                    <div class="content-clamp">
                      {{ record.rewrite_text || record.final_content || '-' }}
                    </div>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'graph_written_at'">
                  <span class="time-text">{{ formatDateTime(record.graph_written_at) }}</span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-button danger size="small" @click="confirmWithdraw(record)">
                    从图谱撤回
                  </a-button>
                </template>
              </template>
            </a-table>
          </template>

          <div v-else class="empty-state">
            <ResourceEmptyState
              title="暂无已入图的反馈"
              description="审核通过并成功写回知识图谱的修正会出现在这里，可随时从图谱撤回。"
              size="compact"
              :icon="Network"
            />
          </div>
        </a-spin>
      </a-tab-pane>
    </a-tabs>

    <!-- 审核弹窗 -->
    <a-modal
      v-model:open="review.visible"
      title="审核反馈"
      width="860px"
      ok-text="提交审核"
      cancel-text="取消"
      :confirm-loading="review.submitting"
      :mask-closable="false"
      destroy-on-close
      class="review-modal"
      @ok="submitReview"
      @cancel="closeReviewModal"
    >
      <div v-if="review.record" class="review-body">
        <div class="review-meta">
          <a-space :size="6" wrap>
            <a-tag>ID：{{ review.record.id }}</a-tag>
            <a-tag>提交人：{{ review.record.uid || '-' }}</a-tag>
            <a-tag>知识库：{{ review.record.kb_id || '全局' }}</a-tag>
            <a-tag :color="riskMeta(review.record.risk_level).color">
              风险：{{ riskMeta(review.record.risk_level).label }}
            </a-tag>
          </a-space>
        </div>

        <div class="compare-block">
          <div class="compare-col">
            <div class="compare-label">原始问答（提问 + 系统回答）</div>
            <div class="compare-content">{{ review.record.original_content || '-' }}</div>
          </div>
          <div class="compare-col">
            <div class="compare-label">老师反馈内容</div>
            <div class="compare-content compare-content--feedback">
              {{ review.record.proposed_content || '-' }}
            </div>
          </div>
        </div>

        <div class="form-block">
          <div class="block-label">自动关联（建单时预富化）</div>
          <div class="enrich-line">
            <span class="enrich-key">实体</span>
            <div class="tag-row">
              <a-tag v-for="(item, index) in review.record.entity_hints || []" :key="index">
                {{ item }}
              </a-tag>
              <span v-if="!(review.record.entity_hints || []).length" class="muted-text">
                未关联到实体 —— 检索时走稀疏路，图谱不会命中这条修正
              </span>
            </div>
          </div>
          <div class="help-text">
            这些是建单时从知识库实体表匹配出的关联实体，会写进图块正文，决定这条修正能否被图谱检索命中。
          </div>
        </div>

        <div class="form-block">
          <div class="block-label required">作用域</div>
          <a-radio-group v-model:value="reviewForm.scope" class="decision-group">
            <a-space direction="vertical" :size="6">
              <a-radio value="kb_truth">知识性偏差 — 对全体用户生效</a-radio>
              <a-radio value="user_pref">表达偏好 — 仅对该用户生效</a-radio>
              <a-radio value="dept_rule">部门规则 — 仅同部门生效</a-radio>
            </a-space>
          </a-radio-group>
          <div v-if="scopeUndecided" class="warn-text">
            规则未能判定：未命中明确的偏好或知识性特征，请人工确认作用域
          </div>
          <div v-else-if="scopeSourceLabel" class="help-text">
            当前为{{ scopeSourceLabel }}
          </div>
          <div v-if="scopeUpgradeWarning" class="error-text">
            改为「知识性偏差」将对全体用户生效并写回知识图谱。请确认这是真实的知识性错误，而不是表达偏好。
          </div>
        </div>

        <div class="form-block">
          <div class="block-label">审核决策</div>
          <a-radio-group v-model:value="reviewForm.decision" class="decision-group">
            <a-space direction="vertical" :size="6">
              <a-radio value="approve_feedback">通过 — 采用老师反馈</a-radio>
              <a-radio value="approve_override">通过 — 修正内容（管理员填写终裁内容）</a-radio>
              <a-radio value="reject">驳回</a-radio>
            </a-space>
          </a-radio-group>

          <div v-if="reviewForm.decision === 'approve_override'" class="override-wrap">
            <div class="block-label required">终裁内容</div>
            <a-textarea
              v-model:value="reviewForm.overrideContent"
              :rows="4"
              :maxlength="4000"
              show-count
              placeholder="填写最终生效的修正内容，将替换老师反馈内容"
            />
            <div v-if="overrideError" class="error-text">{{ overrideError }}</div>
          </div>
        </div>

        <div class="form-row">
          <div class="form-item">
            <div class="block-label">终裁置信度</div>
            <a-input-number
              v-model:value="reviewForm.confidence"
              :min="0"
              :max="1"
              :step="0.05"
              style="width: 180px"
            />
            <div class="help-text">终裁置信度，影响注入时的置信度档位</div>
          </div>
          <div class="form-item">
            <div class="block-label">有效期至</div>
            <a-date-picker
              v-model:value="reviewForm.expiresAt"
              show-time
              format="YYYY-MM-DD HH:mm:ss"
              placeholder="选择失效时间"
              allow-clear
              style="width: 240px"
            />
            <div class="help-text">留空表示永不过期</div>
          </div>
        </div>

        <div class="form-item">
          <div class="block-label">审核备注</div>
          <a-textarea
            v-model:value="reviewForm.note"
            :rows="3"
            :maxlength="2000"
            show-count
            placeholder="审核说明，驳回时建议填写原因"
          />
        </div>

        <a-checkbox
          v-model:checked="reviewForm.writeToGraph"
          :disabled="reviewForm.decision === 'reject'"
        >
          写回知识图谱（改写成图块存入 Graph RAG；之后可在「已入图」分区撤回）
        </a-checkbox>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Network } from 'lucide-vue-next'
import { selfEvolutionApi } from '@/apis/selfEvolution_api'
import { formatDateTime } from '@/utils/time'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'

// ---------------------------------------------------------------- 列表状态

// 三个分区互斥：待审核 / 已生效（未入图）/ 已入图
const TAB_PARAMS = {
  pending: { status: 'pending,ready_for_review' },
  active: { status: 'approved,applied', graph_written: false },
  graph: { graph_written: true }
}

const createTabState = () => ({
  loading: false,
  rows: [],
  error: '',
  pagination: {
    current: 1,
    pageSize: 20,
    total: 0,
    showSizeChanger: true,
    showQuickJumper: true,
    showTotal: (total) => `共 ${total} 条`
  }
})

const state = reactive({
  pending: createTabState(),
  active: createTabState(),
  graph: createTabState()
})

const activeKey = ref('pending')
const refreshing = ref(false)

const tabTotal = (key) => state[key]?.pagination.total || 0

const loadTab = async (key) => {
  const tab = state[key]
  if (tab.loading) return true

  tab.loading = true
  tab.error = ''
  try {
    const result = await selfEvolutionApi.list({
      ...TAB_PARAMS[key],
      limit: tab.pagination.pageSize,
      offset: (tab.pagination.current - 1) * tab.pagination.pageSize
    })
    tab.rows = result?.items || []
    tab.pagination.total = Number(result?.total) || 0
    return true
  } catch (error) {
    tab.error = readErrorDetail(error, '加载反馈列表失败')
    message.error(tab.error)
    tab.rows = []
    return false
  } finally {
    tab.loading = false
  }
}

// 审核/复核/入图/撤回都会改变多个分区的数量，这些操作后统一重载全部列表
const refreshAll = async () => {
  const results = await Promise.all([loadTab('pending'), loadTab('active'), loadTab('graph')])
  return results.every(Boolean)
}

// 当前页被清空时（例如审核完最后一页的最后一条）回退一页重新加载
const ensureValidPage = async (key) => {
  const tab = state[key]
  if (tab.rows.length === 0 && tab.pagination.current > 1) {
    tab.pagination.current -= 1
    await loadTab(key)
  }
}

const applyPageChange = (key, page) => {
  const tab = state[key]
  tab.pagination.current = page.current
  tab.pagination.pageSize = page.pageSize
  loadTab(key)
}

const onPendingTableChange = (page) => applyPageChange('pending', page)
const onActiveTableChange = (page) => applyPageChange('active', page)
const onGraphTableChange = (page) => applyPageChange('graph', page)

const handleTabChange = (key) => {
  if (TAB_PARAMS[key]) loadTab(key)
}

const handleRefresh = async () => {
  if (refreshing.value) return
  refreshing.value = true
  try {
    if (await refreshAll()) message.success('刷新成功')
  } finally {
    refreshing.value = false
  }
}

// ---------------------------------------------------------------- 展示辅助

const RISK_MAP = {
  critical: { label: '严重', color: 'red' },
  high: { label: '高', color: 'orange' },
  medium: { label: '中', color: 'blue' },
  low: { label: '低', color: 'default' }
}

const riskMeta = (level) => RISK_MAP[level] || { label: level || '未标注', color: 'default' }

// M1 作用域：只有 kb_truth 会全局生效并写回图谱，其余按归属隔离。
const SCOPE_MAP = {
  kb_truth: { label: '知识性偏差·全局', color: 'red' },
  user_pref: { label: '表达偏好·仅本人', color: 'blue' },
  dept_rule: { label: '部门规则·同部门', color: 'purple' }
}

const scopeMeta = (record) =>
  SCOPE_MAP[record?.scope] || { label: '知识性偏差·全局', color: 'red' }

const formatConfidence = (value) => {
  const numeric = Number(value)
  if (Number.isNaN(numeric)) return '—'
  return numeric.toFixed(2)
}

const readErrorDetail = (error, fallback) => {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || item?.message || JSON.stringify(item)).join('；')
  }
  if (detail && typeof detail === 'object') return detail.message || detail.error || fallback
  return error?.message || fallback
}

const pendingColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 140, ellipsis: true },
  { title: '提交人 uid', dataIndex: 'uid', key: 'uid', width: 160, ellipsis: true },
  { title: '知识库', dataIndex: 'kb_id', key: 'kb_id', width: 130 },
  { title: '风险等级', dataIndex: 'risk_level', key: 'risk_level', width: 96, align: 'center' },
  { title: '作用域', key: 'scope', width: 150 },
  { title: '原始回答原文', dataIndex: 'original_content', key: 'original_content', width: 300 },
  { title: '反馈内容', dataIndex: 'proposed_content', key: 'proposed_content', width: 300 },
  { title: '实体提示', dataIndex: 'entity_hints', key: 'entity_hints', width: 180 },
  { title: '提交时间', key: 'submit_time', width: 160 },
  { title: '操作', key: 'action', width: 96, align: 'center', fixed: 'right' }
]

const graphColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 120, ellipsis: true },
  { title: '知识库', dataIndex: 'kb_id', key: 'kb_id', width: 130 },
  { title: '入图图块（改写文本）', dataIndex: 'rewrite_text', key: 'rewrite_text', width: 420 },
  { title: '入图时间', dataIndex: 'graph_written_at', key: 'graph_written_at', width: 160 },
  { title: '操作', key: 'action', width: 130, align: 'center', fixed: 'right' }
]

const activeColumns = [
  { title: 'ID', dataIndex: 'id', key: 'id', width: 140, ellipsis: true },
  { title: '知识库', dataIndex: 'kb_id', key: 'kb_id', width: 130 },
  { title: '生效内容', dataIndex: 'final_content', key: 'final_content', width: 320 },
  { title: '实体提示', dataIndex: 'entity_hints', key: 'entity_hints', width: 200 },
  { title: '意图标签', dataIndex: 'intent_tags', key: 'intent_tags', width: 180 },
  { title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 90, align: 'center' },
  { title: '待复核', dataIndex: 'needs_review', key: 'needs_review', width: 100, align: 'center' },
  { title: '操作', key: 'action', width: 110, align: 'center', fixed: 'right' }
]

// ---------------------------------------------------------------- 审核弹窗

const review = reactive({
  visible: false,
  submitting: false,
  record: null
})

const reviewForm = reactive({
  decision: 'approve_feedback',
  overrideContent: '',
  confidence: 0.8,
  expiresAt: null,
  note: '',
  writeToGraph: true,
  scope: 'kb_truth'
})

// 规则未能判定时（scope_source 为空）必须让管理员人工确认，
// 不能静默按全局放行——进入全局口径的只能是知识性偏差。
const scopeUndecided = computed(() => !review.record?.scope_source)

// 把"个人偏好"升级为"知识性偏差"是高风险动作：会对全体用户生效并写回图谱。
const scopeUpgradeWarning = computed(
  () => review.record?.scope === 'user_pref' && reviewForm.scope === 'kb_truth'
)

const scopeSourceLabel = computed(() => {
  const source = review.record?.scope_source
  if (source === 'rule') return '规则自动判定'
  if (source === 'admin') return '管理员改定'
  return ''
})

const overrideError = ref('')

watch(
  () => reviewForm.overrideContent,
  () => {
    overrideError.value = ''
  }
)

const openReviewModal = (record) => {
  review.record = record
  review.visible = true
  review.submitting = false
  reviewForm.decision = 'approve_feedback'
  reviewForm.overrideContent = ''
  reviewForm.confidence = 0.8
  reviewForm.expiresAt = null
  reviewForm.note = ''
  reviewForm.writeToGraph = true
  // 沿用建单时的自动判定结果，管理员可改；未判定时默认停在知识性偏差但会被标红提示
  reviewForm.scope = record.scope || 'kb_truth'
  overrideError.value = ''
}

const closeReviewModal = () => {
  review.visible = false
  review.submitting = false
}

const clampConfidence = (value) => {
  const numeric = Number(value)
  if (Number.isNaN(numeric)) return 0.8
  return Math.min(1, Math.max(0, numeric))
}

const submitReview = async () => {
  const record = review.record
  if (!record) return

  if (reviewForm.decision === 'approve_override' && !reviewForm.overrideContent.trim()) {
    overrideError.value = '请先填写终裁内容'
    message.error('请先填写终裁内容')
    return
  }

  const payload = {
    approved: reviewForm.decision !== 'reject',
    confidence: clampConfidence(reviewForm.confidence),
    // 通过时默认写回图谱（P4）；驳回时无内容可写回。
    // 非知识性偏差后端会拒绝写回，这里仍传值以保持接口语义单一。
    write_to_graph: reviewForm.decision !== 'reject' && reviewForm.writeToGraph,
    scope: reviewForm.scope
  }
  // 驳回时不传 override_content，避免后端同时收到冲突字段
  if (reviewForm.decision === 'approve_override') {
    payload.override_content = reviewForm.overrideContent.trim()
  }
  const note = reviewForm.note.trim()
  if (note) payload.note = note
  if (reviewForm.expiresAt) payload.expires_at = reviewForm.expiresAt.toISOString()

  review.submitting = true
  try {
    await selfEvolutionApi.review(record.id, payload)
    message.success(reviewForm.decision === 'reject' ? '已驳回该反馈' : '审核通过，修正已生效')
    closeReviewModal()
    await refreshAll()
    await ensureValidPage('pending')
  } catch (error) {
    message.error(readErrorDetail(error, '审核提交失败'))
  } finally {
    review.submitting = false
  }
}

// ---------------------------------------------------------------- 复核确认

const confirmAckReview = (record) => {
  let note = ''
  Modal.confirm({
    title: '复核确认',
    okText: '确认复核',
    cancelText: '取消',
    content: h('div', { class: 'correction-ack-content' }, [
      h('p', { style: { margin: '0 0 12px' } }, '确认后表示修正内容在源文档更新后依然有效，将清除待复核标记。'),
      h('textarea', {
        rows: 3,
        maxLength: 2000,
        placeholder: '备注（可选）',
        style: {
          width: '100%',
          padding: '6px 8px',
          border: '1px solid var(--gray-200)',
          borderRadius: '6px',
          background: 'transparent',
          color: 'inherit',
          fontFamily: 'inherit',
          fontSize: '13px',
          lineHeight: '1.6',
          resize: 'vertical'
        },
        onInput: (event) => {
          note = event.target.value
        }
      })
    ]),
    async onOk() {
      try {
        await selfEvolutionApi.ackReview(record.id, { note: note.trim() || undefined })
        message.success('已确认复核，待复核标记已清除')
        await refreshAll()
        await ensureValidPage('active')
      } catch (error) {
        message.error(readErrorDetail(error, '复核确认失败'))
      }
    }
  })
}

// ---------------------------------------------------------------- 图谱写回

// 改写校验未通过的工单，重试必须丢弃旧改写文本重新改写，
// 否则会拿同一段不合格文本再校验失败一次，形成没有出口的死循环。
const rewriteRejected = (record) =>
  String(record.needs_review_reason || '').startsWith('改写校验未通过')

const writingId = ref(null)

const writeToGraph = async (record) => {
  const forced = rewriteRejected(record)
  writingId.value = record.id
  try {
    const result = await selfEvolutionApi.writeGraph(record.id, forced)
    if (result?.status === 'skipped') {
      message.warning(result.reason || '写回被跳过，请检查知识库模型配置')
    } else if (result?.status === 'already') {
      message.info('该修正已入图')
    } else {
      message.success(`已写回图谱，生成 ${result?.chunk_ids?.length || 0} 个图块`)
    }
    await refreshAll()
    await ensureValidPage('active')
  } catch (error) {
    message.error(readErrorDetail(error, '写回图谱失败'))
  } finally {
    writingId.value = null
  }
}

// ---------------------------------------------------------------- 图谱撤回

const confirmWithdraw = (record) => {
  Modal.confirm({
    title: '从知识图谱撤回',
    okText: '确认撤回',
    okType: 'danger',
    cancelText: '取消',
    content: h('div', { class: 'correction-withdraw-content' }, [
      h('p', { style: { margin: 0 } }, `确认把修正 #${record.id} 从 Graph RAG 中删除？`),
      h(
        'p',
        { style: { margin: '8px 0 0', color: 'var(--gray-600)' } },
        '将删除该修正产生的图块与向量，并清理仅属于它的实体/三元组（与正式文档共享的实体会保留）。工单本身保留，可重新写回。'
      )
    ]),
    async onOk() {
      try {
        await selfEvolutionApi.withdrawGraph(record.id)
        message.success('已撤回，该修正不再参与图谱检索')
        await refreshAll()
        await ensureValidPage('graph')
      } catch (error) {
        message.error(readErrorDetail(error, '撤回失败'))
      }
    }
  })
}

onMounted(() => {
  refreshAll()
})

defineExpose({
  refreshing,
  handleRefresh
})
</script>

<style lang="less" scoped>
.correction-review {
  .correction-tabs {
    :deep(.ant-tabs-nav) {
      margin-bottom: 12px;
    }

    :deep(.ant-tabs-tab) {
      font-size: 14px;
    }
  }

  .tab-title {
    display: inline-flex;
    align-items: center;
  }

  .tab-badge {
    margin-left: 6px;

    :deep(.ant-badge-count) {
      background: var(--main-color);
      color: #fff;
      font-size: 11px;
      line-height: 16px;
      height: 16px;
      min-width: 16px;
      padding: 0 5px;
      box-shadow: none;
    }
  }

  .error-message {
    padding: 8px 0 16px;
  }

  .empty-state {
    padding: 8px 0 24px;
  }

  .correction-table {
    :deep(.ant-table-thead > tr > th) {
      background: var(--gray-50);
      font-weight: 500;
      padding: 8px 12px;
    }

    :deep(.ant-table-tbody > tr > td) {
      padding: 8px 12px;
    }
  }

  .ellipsis-text {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--gray-700, var(--gray-900));
  }

  .content-clamp {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--gray-900);
    line-height: 1.5;
  }

  .muted-text {
    color: var(--gray-600);
  }

  .time-text {
    color: var(--gray-600);
    white-space: nowrap;
  }

  .action-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .enrich-line {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 6px;
  }

  .enrich-key {
    flex: none;
    color: var(--gray-600);
    line-height: 24px;
  }

  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .disabled-action-wrapper {
    display: inline-block;
    cursor: not-allowed;
  }
}

.tooltip-text {
  max-width: 460px;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}

.review-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px;
    border-bottom: 1px solid var(--gray-150);
  }

  :deep(.ant-modal-body) {
    padding: 24px;
  }

  .review-body {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .compare-block {
    display: flex;
    gap: 16px;

    @media (max-width: 767px) {
      flex-direction: column;
    }
  }

  .compare-col {
    flex: 1;
    min-width: 0;
  }

  .compare-label {
    margin-bottom: 6px;
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-700, var(--gray-900));
  }

  .compare-content {
    height: 220px;
    overflow: auto;
    padding: 12px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-25, var(--gray-50));
    color: var(--gray-900);
    font-size: 13px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;

    &--feedback {
      background: var(--main-30, var(--gray-50));
    }
  }

  .block-label {
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-900);

    &.required::before {
      content: '*';
      margin-right: 4px;
      color: var(--color-error-500);
    }
  }

  .decision-group {
    display: block;
  }

  .override-wrap {
    margin-top: 12px;
    padding: 12px;
    border: 1px dashed var(--gray-200);
    border-radius: 8px;
  }

  .form-row {
    display: flex;
    flex-wrap: wrap;
    gap: 24px;
  }

  .form-item {
    min-width: 200px;
  }

  .help-text {
    margin-top: 4px;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.4;
  }

  .error-text {
    margin-top: 4px;
    color: var(--color-error-500);
    font-size: 12px;
    line-height: 1.4;
  }

  .warn-text {
    margin-top: 4px;
    color: var(--color-warning-700);
    font-size: 12px;
    line-height: 1.4;
  }

  .scope-hint {
    margin-top: 2px;
    color: var(--color-warning-700);
    font-size: 11px;
    line-height: 1.3;
  }
}
</style>
