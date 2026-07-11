<template>
  <div class="basic-settings-section">
    <template v-if="userStore.isAdmin && activeSection === 'defaults'">
      <div class="settings-panel">
        <template v-if="userStore.isAdmin">
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">{{ items?.default_model?.des || '默认对话模型' }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleChatModelSelect"
                  :model_spec="draft.default_model"
                  placeholder="请选择默认模型"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.fast_model?.des }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleFastModelSelect"
                  :model_spec="draft.fast_model"
                  placeholder="请选择模型"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">{{ items?.embed_model?.des }}</div>
              <div class="setting-content">
                <EmbeddingModelSelector
                  :value="draft.embed_model"
                  @change="setDraftValue('embed_model', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.reranker?.des }}</div>
              <div class="setting-content">
                <RerankModelSelector
                  :value="draft.reranker"
                  @change="setDraftValue('reranker', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">
                {{ items?.default_ocr_engine?.des || '默认 OCR 解析引擎' }}
              </div>
              <div class="setting-content">
                <a-select
                  :value="draft.default_ocr_engine"
                  @update:value="setDraftValue('default_ocr_engine', $event)"
                  class="full-width"
                >
                  <a-select-option
                    v-for="option in ocrEngineOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </a-select-option>
                </a-select>
              </div>
            </div>
          </div>
          <div class="section-title inline-title">运行时服务配置</div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">Tavily 网页搜索 API Key</div>
              <div class="setting-content">
                <a-input-password
                  :value="secretDrafts.tavily_api_key"
                  :placeholder="secretPlaceholder('tavily_api_key')"
                  @update:value="setSecretDraft('tavily_api_key', $event)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">URL 解析白名单</div>
              <div class="setting-content">
                <a-textarea
                  :value="urlWhitelistText"
                  :auto-size="{ minRows: 1, maxRows: 4 }"
                  placeholder="每行或逗号分隔一个域名"
                  @update:value="setUrlWhitelistText"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">MinerU 官方 API Key</div>
              <div class="setting-content">
                <a-input-password
                  :value="secretDrafts.mineru_api_key"
                  :placeholder="secretPlaceholder('mineru_api_key')"
                  @update:value="setSecretDraft('mineru_api_key', $event)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">MinerU 官方 API 基础地址</div>
              <div class="setting-content">
                <a-input
                  :value="draft.mineru_api_uri"
                  placeholder="https://mineru.net/api/v4"
                  @update:value="setDraftValue('mineru_api_uri', $event)"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">MinerU 超时时间（秒）</div>
              <div class="setting-content">
                <a-input-number
                  :value="draft.mineru_timeout_seconds"
                  :min="1"
                  class="full-width"
                  @update:value="setDraftValue('mineru_timeout_seconds', $event)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">PaddleOCR API Token</div>
              <div class="setting-content">
                <a-input-password
                  :value="secretDrafts.paddleocr_api_token"
                  :placeholder="secretPlaceholder('paddleocr_api_token')"
                  @update:value="setSecretDraft('paddleocr_api_token', $event)"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">PaddleOCR 任务地址</div>
              <div class="setting-content">
                <a-input
                  :value="draft.paddleocr_api_url"
                  @update:value="setDraftValue('paddleocr_api_url', $event)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">DeepSeek OCR API Key</div>
              <div class="setting-content">
                <a-input-password
                  :value="secretDrafts.deepseek_ocr_api_key"
                  :placeholder="secretPlaceholder('deepseek_ocr_api_key')"
                  @update:value="setSecretDraft('deepseek_ocr_api_key', $event)"
                />
              </div>
            </div>
          </div>
        </template>
      </div>

    </template>

    <template v-if="userStore.isAdmin && activeSection === 'contentGuard'">
      <div class="section">
        <div class="card">
          <span class="label">{{ items?.enable_content_guard?.des }}</span>
          <a-switch
            :checked="draft.enable_content_guard"
            @update:checked="setDraftValue('enable_content_guard', $event)"
          />
        </div>
        <div class="card" v-if="draft.enable_content_guard">
          <span class="label">{{ items?.enable_content_guard_llm?.des }}</span>
          <a-switch
            :checked="draft.enable_content_guard_llm"
            @update:checked="setDraftValue('enable_content_guard_llm', $event)"
          />
        </div>
        <div
          class="card card-select"
          v-if="draft.enable_content_guard && draft.enable_content_guard_llm"
        >
          <span class="label">{{ items?.content_guard_llm_model?.des }}</span>
          <ModelSelectorComponent
            @select-model="handleContentGuardModelSelect"
            :model_spec="draft.content_guard_llm_model"
            placeholder="请选择模型"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { useConfigStore } from '@/stores/config'
import { useUserStore } from '@/stores/user'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import RerankModelSelector from '@/components/RerankModelSelector.vue'

defineProps({
  activeSection: {
    type: String,
    default: 'defaults'
  }
})

const emit = defineEmits(['dirty-change'])

const CONFIG_DRAFT_KEYS = [
  'default_model',
  'fast_model',
  'embed_model',
  'reranker',
  'default_ocr_engine',
  'url_whitelist',
  'mineru_api_uri',
  'mineru_timeout_seconds',
  'paddleocr_api_url',
  'enable_content_guard',
  'enable_content_guard_llm',
  'content_guard_llm_model'
]

const SECRET_KEYS = [
  'tavily_api_key',
  'mineru_api_key',
  'paddleocr_api_token',
  'deepseek_ocr_api_key'
]

const configStore = useConfigStore()
const userStore = useUserStore()
const items = computed(() => configStore.config?._config_items || {})
const isConfigReady = computed(() => Object.keys(configStore.config || {}).length > 0)
const draft = reactive({})
const secretDrafts = reactive({
  tavily_api_key: '',
  mineru_api_key: '',
  paddleocr_api_token: '',
  deepseek_ocr_api_key: ''
})
const urlWhitelistText = computed(() => (Array.isArray(draft.url_whitelist) ? draft.url_whitelist : []).join('\n'))
const ocrEngineOptions = [
  { value: 'disable', label: '不启用' },
  { value: 'rapid_ocr', label: 'RapidOCR (ONNX)' },
  { value: 'mineru_official', label: 'MinerU 官方 API' },
  { value: 'pp_structure_v3_ocr', label: 'PP-Structure-V3' },
  { value: 'deepseek_ocr', label: 'DeepSeek OCR' },
  { value: 'paddleocr_vl_1_6', label: 'PaddleOCR-VL-1.6' },
  { value: 'paddleocr_pp_ocrv6', label: 'PP-OCRv6' }
]

const cloneConfigValue = (value) => {
  if (Array.isArray(value)) return [...value]
  if (value && typeof value === 'object') return { ...value }
  return value
}

const areValuesEqual = (left, right) => JSON.stringify(left ?? null) === JSON.stringify(right ?? null)

const syncDraftFromConfig = () => {
  const source = configStore.config || {}
  CONFIG_DRAFT_KEYS.forEach((key) => {
    draft[key] = cloneConfigValue(source[key])
  })
  if (!draft.default_ocr_engine) {
    draft.default_ocr_engine = 'rapid_ocr'
  }
  if (!draft.mineru_timeout_seconds) {
    draft.mineru_timeout_seconds = 1800
  }
  SECRET_KEYS.forEach((key) => {
    secretDrafts[key] = ''
  })
}

const normalConfigChanges = computed(() => {
  if (!isConfigReady.value) return {}

  const source = configStore.config || {}
  const changes = {}
  CONFIG_DRAFT_KEYS.forEach((key) => {
    if (!areValuesEqual(draft[key], source[key])) {
      changes[key] = cloneConfigValue(draft[key])
    }
  })
  return changes
})

const secretChanges = computed(() => {
  const changes = {}
  SECRET_KEYS.forEach((key) => {
    const value = secretDrafts[key].trim()
    if (value) {
      changes[key] = value
    }
  })
  return changes
})

const hasUnsavedChanges = computed(
  () => isConfigReady.value && (Object.keys(normalConfigChanges.value).length > 0 || Object.keys(secretChanges.value).length > 0)
)

const setDraftValue = (key, value) => {
  draft[key] = value
}

const setSecretDraft = (key, value) => {
  secretDrafts[key] = value
}

const setUrlWhitelistText = (value) => {
  draft.url_whitelist = value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

const saveAll = async () => {
  const payload = {
    ...normalConfigChanges.value,
    ...secretChanges.value
  }

  if (Object.keys(payload).length === 0) {
    return
  }

  await configStore.setConfigValues(payload)
  syncDraftFromConfig()
}

const secretPlaceholder = (key) =>
  configStore.config?.[`${key}_configured`] ? '已配置，输入新值后统一保存' : '未配置，输入后统一保存'

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    setDraftValue('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    setDraftValue('fast_model', spec)
  }
}

const handleContentGuardModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    setDraftValue('content_guard_llm_model', spec)
  }
}

watch(
  () => configStore.config,
  () => {
    syncDraftFromConfig()
  },
  { immediate: true }
)

watch(
  hasUnsavedChanges,
  (value) => {
    emit('dirty-change', value)
  },
  { immediate: true }
)

defineExpose({ saveAll, hasUnsavedChanges })

</script>

<style lang="less" scoped>
.basic-settings-section {
  .section {
    background-color: var(--gray-0);
    padding: 10px 16px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    gap: 16px;
    border: 1px solid var(--gray-150);
  }

  .settings-panel {
    background-color: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .setting-row {
    display: flex;
    flex-direction: column;
    gap: 8px;

    &.two-cols {
      flex-direction: row;
      gap: 20px;
    }

    .col-item {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 6px;
      min-width: 0;
    }
  }

  .setting-label {
    font-size: 13px;
    font-weight: 500;
    color: var(--gray-700);
  }

  .inline-title {
    margin-top: 4px;
  }

  .setting-content {
    width: 100%;

    .full-width {
      width: 100%;
    }
  }

  .card {
    display: flex;
    align-items: center;
    justify-content: space-between;

    .label {
      margin-right: 20px;
      font-weight: 500;
      color: var(--gray-800);
      flex-shrink: 0;
      min-width: 140px;
    }

    &.card-select {
      align-items: flex-start;
      gap: 12px;

      .label {
        margin-right: 0;
        margin-top: 6px;
      }
    }
  }

  .agent-select {
    width: 320px;
    max-width: 100%;
  }

  @media (max-width: 768px) {
    .agent-select {
      width: 100%;
    }
  }
}
</style>
