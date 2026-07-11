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
                  :model_spec="configStore.config?.default_model"
                  placeholder="请选择默认模型"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.fast_model?.des }}</div>
              <div class="setting-content">
                <ModelSelectorComponent
                  @select-model="handleFastModelSelect"
                  :model_spec="configStore.config?.fast_model"
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
                  :value="configStore.config?.embed_model"
                  @change="handleChange('embed_model', $event)"
                  style="width: 100%"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">{{ items?.reranker?.des }}</div>
              <div class="setting-content">
                <RerankModelSelector
                  :value="configStore.config?.reranker"
                  @change="handleChange('reranker', $event)"
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
                  :value="configStore.config?.default_ocr_engine || 'rapid_ocr'"
                  @change="handleChange('default_ocr_engine', $event)"
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
                <a-input-group compact class="secret-input-group">
                  <a-input-password
                    :value="secretDrafts.tavily_api_key"
                    :placeholder="secretPlaceholder('tavily_api_key')"
                    @change="setSecretDraft('tavily_api_key', $event.target.value)"
                    @pressEnter="saveSecret('tavily_api_key')"
                  />
                  <a-button :loading="secretSaving.tavily_api_key" @click="saveSecret('tavily_api_key')">保存</a-button>
                </a-input-group>
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">URL 解析白名单</div>
              <div class="setting-content">
                <a-textarea
                  :value="urlWhitelistText"
                  :auto-size="{ minRows: 1, maxRows: 4 }"
                  placeholder="每行或逗号分隔一个域名"
                  @change="handleUrlWhitelistChange"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">MinerU 官方 API Key</div>
              <div class="setting-content">
                <a-input-group compact class="secret-input-group">
                  <a-input-password
                    :value="secretDrafts.mineru_api_key"
                    :placeholder="secretPlaceholder('mineru_api_key')"
                    @change="setSecretDraft('mineru_api_key', $event.target.value)"
                    @pressEnter="saveSecret('mineru_api_key')"
                  />
                  <a-button :loading="secretSaving.mineru_api_key" @click="saveSecret('mineru_api_key')">保存</a-button>
                </a-input-group>
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">MinerU 官方 API 基础地址</div>
              <div class="setting-content">
                <a-input
                  :value="configStore.config?.mineru_api_uri"
                  placeholder="https://mineru.net/api/v4"
                  @change="handleTextChange('mineru_api_uri', $event.target.value)"
                  @pressEnter="handleTextChange('mineru_api_uri', $event.target.value)"
                />
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">MinerU 超时时间（秒）</div>
              <div class="setting-content">
                <a-input-number
                  :value="configStore.config?.mineru_timeout_seconds || 1800"
                  :min="1"
                  class="full-width"
                  @change="handleChange('mineru_timeout_seconds', $event)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">PaddleOCR API Token</div>
              <div class="setting-content">
                <a-input-group compact class="secret-input-group">
                  <a-input-password
                    :value="secretDrafts.paddleocr_api_token"
                    :placeholder="secretPlaceholder('paddleocr_api_token')"
                    @change="setSecretDraft('paddleocr_api_token', $event.target.value)"
                    @pressEnter="saveSecret('paddleocr_api_token')"
                  />
                  <a-button :loading="secretSaving.paddleocr_api_token" @click="saveSecret('paddleocr_api_token')">保存</a-button>
                </a-input-group>
              </div>
            </div>
          </div>
          <div class="setting-row two-cols">
            <div class="col-item">
              <div class="setting-label">PaddleOCR 任务地址</div>
              <div class="setting-content">
                <a-input
                  :value="configStore.config?.paddleocr_api_url"
                  @change="handleTextChange('paddleocr_api_url', $event.target.value)"
                />
              </div>
            </div>
            <div class="col-item">
              <div class="setting-label">DeepSeek OCR API Key</div>
              <div class="setting-content">
                <a-input-group compact class="secret-input-group">
                  <a-input-password
                    :value="secretDrafts.deepseek_ocr_api_key"
                    :placeholder="secretPlaceholder('deepseek_ocr_api_key')"
                    @change="setSecretDraft('deepseek_ocr_api_key', $event.target.value)"
                    @pressEnter="saveSecret('deepseek_ocr_api_key')"
                  />
                  <a-button :loading="secretSaving.deepseek_ocr_api_key" @click="saveSecret('deepseek_ocr_api_key')">保存</a-button>
                </a-input-group>
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
            :checked="configStore.config?.enable_content_guard"
            @change="handleChange('enable_content_guard', $event)"
          />
        </div>
        <div class="card" v-if="configStore.config?.enable_content_guard">
          <span class="label">{{ items?.enable_content_guard_llm?.des }}</span>
          <a-switch
            :checked="configStore.config?.enable_content_guard_llm"
            @change="handleChange('enable_content_guard_llm', $event)"
          />
        </div>
        <div
          class="card card-select"
          v-if="
            configStore.config?.enable_content_guard &&
            configStore.config?.enable_content_guard_llm
          "
        >
          <span class="label">{{ items?.content_guard_llm_model?.des }}</span>
          <ModelSelectorComponent
            @select-model="handleContentGuardModelSelect"
            :model_spec="configStore.config?.content_guard_llm_model"
            placeholder="请选择模型"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { message } from 'ant-design-vue'
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

const configStore = useConfigStore()
const userStore = useUserStore()
const items = computed(() => configStore.config?._config_items || {})
const secretDrafts = reactive({
  tavily_api_key: '',
  mineru_api_key: '',
  paddleocr_api_token: '',
  deepseek_ocr_api_key: ''
})
const secretSaving = reactive({
  tavily_api_key: false,
  mineru_api_key: false,
  paddleocr_api_token: false,
  deepseek_ocr_api_key: false
})
const urlWhitelistText = computed(() => (configStore.config?.url_whitelist || []).join('\n'))
const ocrEngineOptions = [
  { value: 'disable', label: '不启用' },
  { value: 'rapid_ocr', label: 'RapidOCR (ONNX)' },
  { value: 'mineru_official', label: 'MinerU 官方 API' },
  { value: 'pp_structure_v3_ocr', label: 'PP-Structure-V3' },
  { value: 'deepseek_ocr', label: 'DeepSeek OCR' },
  { value: 'paddleocr_vl_1_6', label: 'PaddleOCR-VL-1.6' },
  { value: 'paddleocr_pp_ocrv6', label: 'PP-OCRv6' }
]

const handleChange = async (key, e) => {
  try {
    await configStore.setConfigValue(key, e)
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '配置保存失败')
  }
}

const handleTextChange = async (key, value) => {
  try {
    await configStore.setConfigValue(key, value)
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '配置保存失败')
  }
}

const handleUrlWhitelistChange = (event) => {
  const value = event.target.value || ''
  const domains = value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
  handleChange('url_whitelist', domains)
}

const setSecretDraft = (key, value) => {
  secretDrafts[key] = value
}

const saveSecret = async (key) => {
  const value = secretDrafts[key].trim()
  if (!value) {
    message.warning('请输入要保存的配置值')
    return
  }
  secretSaving[key] = true
  try {
    await configStore.setConfigValue(key, value)
    message.success('配置已保存')
  } catch (error) {
    message.error(error.message || '配置保存失败')
  } finally {
    secretSaving[key] = false
  }
}

const secretPlaceholder = (key) =>
  configStore.config?.[`${key}_configured`] ? '已配置，输入新值后回车更新' : '未配置，输入后回车保存'

const handleChatModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('default_model', spec)
  }
}

const handleFastModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('fast_model', spec)
  }
}

const handleContentGuardModelSelect = (spec) => {
  if (typeof spec === 'string' && spec) {
    configStore.setConfigValue('content_guard_llm_model', spec)
  }
}

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

  .secret-input-group {
    display: flex;

    :deep(.ant-input-affix-wrapper) {
      flex: 1;
      min-width: 0;
    }

    :deep(.ant-btn) {
      width: 64px;
      flex: 0 0 64px;
    }
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
