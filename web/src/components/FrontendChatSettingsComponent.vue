<template>
  <div class="frontend-chat-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">前端配置</div>
        <p class="section-description">配置前端品牌展示、前台问答门户的可用智能体和输入区能力。</p>
      </div>
      <a-button type="primary" :loading="saving" @click="saveConfig">保存配置</a-button>
    </div>

    <a-spin :spinning="loading">
      <div class="settings-form-grid">
        <a-card size="small" title="品牌展示" :bordered="true">
          <a-form layout="vertical">
            <a-form-item label="组织名称">
              <a-input v-model:value="config.organization_name" placeholder="实验室安全教育智能对话平台" />
            </a-form-item>
            <a-form-item label="侧边栏头像">
              <a-input v-model:value="config.organization_avatar" placeholder="/avatar.jpg" />
              <div class="asset-control">
                <img v-if="config.organization_avatar" :src="config.organization_avatar" alt="侧边栏头像预览" class="asset-preview avatar" />
                <a-upload :show-upload-list="false" accept="image/*" :custom-request="createAssetUploadRequest('organization_avatar')">
                  <a-button :loading="uploadingAsset.organization_avatar">
                    <template #icon><UploadOutlined /></template>
                    上传头像
                  </a-button>
                </a-upload>
              </div>
            </a-form-item>
            <a-form-item label="登录页 Logo">
              <a-input v-model:value="config.organization_logo" placeholder="/favicon.svg" />
              <div class="asset-control">
                <img v-if="config.organization_logo" :src="config.organization_logo" alt="登录页 Logo 预览" class="asset-preview logo" />
                <a-upload :show-upload-list="false" accept="image/*" :custom-request="createAssetUploadRequest('organization_logo')">
                  <a-button :loading="uploadingAsset.organization_logo">
                    <template #icon><UploadOutlined /></template>
                    上传 Logo
                  </a-button>
                </a-upload>
              </div>
            </a-form-item>
            <a-form-item label="登录页背景">
              <a-input v-model:value="config.login_bg" placeholder="/lab-safety-login-bg.svg" />
              <div class="asset-control">
                <img v-if="config.login_bg" :src="config.login_bg" alt="登录页背景预览" class="asset-preview background" />
                <a-upload :show-upload-list="false" accept="image/*" :custom-request="createAssetUploadRequest('login_bg')">
                  <a-button :loading="uploadingAsset.login_bg">
                    <template #icon><UploadOutlined /></template>
                    上传背景
                  </a-button>
                </a-upload>
              </div>
            </a-form-item>
            <a-form-item label="浏览器标题">
              <a-input v-model:value="config.browser_title" placeholder="实验室安全教育智能对话平台" />
            </a-form-item>
            <a-form-item label="系统名称">
              <a-input v-model:value="config.system_name" placeholder="实验室安全教育智能对话平台" />
            </a-form-item>
          </a-form>
        </a-card>

        <a-card size="small" title="前台展示" :bordered="true">
          <a-form layout="vertical">
            <a-form-item label="输入框标题">
              <a-radio-group v-model:value="config.welcome_title_mode">
                <a-radio-button value="dynamic">动态问候语</a-radio-button>
                <a-radio-button value="custom">自定义文字</a-radio-button>
              </a-radio-group>
              <a-input
                v-if="config.welcome_title_mode === 'custom'"
                v-model:value="config.welcome_title"
                class="field-gap"
                placeholder="请输入自定义标题"
              />
            </a-form-item>
            <a-form-item label="输入框提示内容">
              <a-input v-model:value="config.frontend_input_placeholder" />
            </a-form-item>
            <a-form-item label="实验室安全隐患识别">
              <a-switch v-model:checked="config.show_lab_safety_recognition" />
            </a-form-item>
          </a-form>
        </a-card>

        <a-card size="small" title="前台智能体" :bordered="true">
          <a-form layout="vertical">
            <a-form-item label="候选智能体">
              <a-select
                v-model:value="config.selectable_agent_ids"
                mode="multiple"
                allow-clear
                :options="agentOptions"
                placeholder="请选择前台可用智能体"
              />
            </a-form-item>
            <a-form-item label="默认智能体">
              <a-select
                v-model:value="config.default_agent_id"
                allow-clear
                :options="defaultAgentOptions"
                placeholder="请选择默认智能体"
              />
            </a-form-item>
          </a-form>
        </a-card>

        <a-card size="small" title="输入区能力" :bordered="true">
          <div class="switch-grid">
            <label v-for="item in inputSwitches" :key="item.key" class="switch-row">
              <span>{{ item.label }}</span>
              <a-switch v-model:checked="config[item.key]" />
            </label>
          </div>
        </a-card>

        <a-card size="small" title="回答展示" :bordered="true">
          <div class="switch-grid">
            <label class="switch-row">
              <span>思考过程</span>
              <a-switch v-model:checked="config.show_thought_process" />
            </label>
            <label class="switch-row">
              <span>引用文档</span>
              <a-switch v-model:checked="config.show_reference_documents" />
            </label>
          </div>
        </a-card>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { UploadOutlined } from '@ant-design/icons-vue'
import { frontendChatConfigApi } from '@/apis/system_api'
import { useInfoStore } from '@/stores/info'

const DEFAULT_CONFIG = {
  organization_name: '实验室安全教育智能对话平台',
  organization_avatar: '/avatar.jpg',
  organization_logo: '/favicon.svg',
  login_bg: '/lab-safety-login-bg.svg',
  browser_title: '实验室安全教育智能对话平台',
  system_name: '实验室安全教育智能对话平台',
  welcome_title_mode: 'dynamic',
  welcome_title: '实验室安全教育智能对话平台',
  frontend_input_placeholder: '请输入实验室安全教育相关问题',
  default_agent_id: '',
  selectable_agent_ids: [],
  show_agent_selector: true,
  show_model_selector: false,
  show_web_search_toggle: false,
  show_file_upload: false,
  show_image_upload: false,
  show_lab_safety_recognition: true,
  show_voice_input: false,
  show_send_button: true,
  show_thought_process: true,
  show_reference_documents: true,
  agent_options: []
}

const inputSwitches = [
  { key: 'show_agent_selector', label: '智能体选择' },
  { key: 'show_model_selector', label: '模型选择' },
  { key: 'show_web_search_toggle', label: '联网搜索' },
  { key: 'show_file_upload', label: '文件上传' },
  { key: 'show_image_upload', label: '图片上传' },
  { key: 'show_voice_input', label: '语音输入' },
  { key: 'show_send_button', label: '发送按钮' }
]

const loading = ref(false)
const saving = ref(false)
const uploadingAsset = reactive({
  organization_avatar: false,
  organization_logo: false,
  login_bg: false
})
const config = reactive({ ...DEFAULT_CONFIG })
const infoStore = useInfoStore()

const agentOptions = computed(() =>
  (config.agent_options || []).map((agent) => ({
    label: agent.name || agent.id,
    value: agent.id
  }))
)

const defaultAgentOptions = computed(() => {
  const selectedIds = new Set(config.selectable_agent_ids || [])
  return agentOptions.value.filter((agent) => selectedIds.has(agent.value))
})

const applyConfig = (data) => {
  Object.assign(config, { ...DEFAULT_CONFIG, ...(data || {}) })
  config.selectable_agent_ids = Array.isArray(config.selectable_agent_ids)
    ? [...new Set(config.selectable_agent_ids.filter(Boolean))]
    : []
}

const loadConfig = async () => {
  loading.value = true
  try {
    const response = await frontendChatConfigApi.getConfig()
    applyConfig(response.data)
  } catch (error) {
    message.error(error.message || '前端配置加载失败')
  } finally {
    loading.value = false
  }
}

const saveConfig = async () => {
  saving.value = true
  try {
    const payload = { ...config }
    delete payload.agent_options
    const response = await frontendChatConfigApi.updateConfig(payload)
    applyConfig(response.data)
    await infoStore.loadInfoConfig(true)
    message.success('前端配置已保存')
  } catch (error) {
    message.error(error.message || '前端配置保存失败')
  } finally {
    saving.value = false
  }
}

const createAssetUploadRequest = (key) => async ({ file, onSuccess, onError }) => {
  uploadingAsset[key] = true
  try {
    const response = await frontendChatConfigApi.uploadAsset(file)
    const url = response?.data?.url
    if (!url) {
      throw new Error('上传接口未返回资源地址')
    }
    config[key] = url
    message.success('图片已上传，请保存配置使其生效')
    onSuccess?.(response)
  } catch (error) {
    message.error(error.message || '图片上传失败')
    onError?.(error)
  } finally {
    uploadingAsset[key] = false
  }
}

watch(
  () => config.selectable_agent_ids,
  (ids) => {
    if (config.default_agent_id && !(ids || []).includes(config.default_agent_id)) {
      config.default_agent_id = ''
    }
  },
  { deep: true }
)

onMounted(loadConfig)
</script>

<style lang="less" scoped>
.frontend-chat-settings {
  .settings-form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;

    @media (max-width: 760px) {
      grid-template-columns: 1fr;
    }
  }

  .field-gap {
    margin-top: 10px;
  }

  .asset-control {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 10px;
  }

  .asset-preview {
    flex: 0 0 auto;
    object-fit: cover;
    border: 1px solid var(--gray-200);
    background: var(--gray-50);

    &.avatar,
    &.logo {
      width: 40px;
      height: 40px;
      border-radius: 6px;
    }

    &.background {
      width: 112px;
      height: 64px;
      border-radius: 6px;
    }
  }

  .switch-grid {
    display: grid;
    gap: 12px;
  }

  .switch-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    min-height: 32px;
    color: var(--gray-800);
    font-size: 14px;
  }
}
</style>
