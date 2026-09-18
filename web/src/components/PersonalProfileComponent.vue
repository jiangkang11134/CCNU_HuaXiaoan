<template>
  <div class="personal-profile">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">个人资料</div>
        <p class="section-description">
          身份与专业用于让回答更贴合你的场景，回答风格决定回答的详略。
          学工号与身份来自注册信息，不可自行修改。
        </p>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadProfile">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          刷新
        </a-button>
        <a-button type="primary" :loading="saving" :disabled="!loaded" @click="saveProfile">
          {{ saveButtonText }}
        </a-button>
      </div>
    </div>

    <a-spin :spinning="loading">
      <div class="profile-panel">
        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">学工号</span>
              <a-tag class="readonly-tag">注册信息</a-tag>
            </div>
            <p class="profile-description">即注册账号，由管理员统一维护。</p>
          </div>
          <div class="profile-control">
            <span class="readonly-value">{{ userStore.uid || '—' }}</span>
          </div>
        </div>

        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">身份</span>
              <a-tag class="readonly-tag">注册信息</a-tag>
            </div>
            <p class="profile-description">学生 / 教师 / 管理员，注册时确定。</p>
          </div>
          <div class="profile-control">
            <span class="readonly-value">{{ identityLabel }}</span>
          </div>
        </div>

        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">姓名</span>
            </div>
            <p class="profile-description">仅用于界面展示，不会作为回答的依据。</p>
          </div>
          <div class="profile-control">
            <a-input v-model:value="draft.full_name" placeholder="未填写" :maxlength="64" allow-clear />
          </div>
        </div>

        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">性别</span>
            </div>
            <p class="profile-description">可留空。</p>
          </div>
          <div class="profile-control">
            <a-select v-model:value="draft.gender" placeholder="未填写" allow-clear>
              <a-select-option v-for="option in genderOptions" :key="option" :value="option">
                {{ option }}
              </a-select-option>
            </a-select>
          </div>
        </div>

        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">专业</span>
            </div>
            <p class="profile-description">用于检索提示与指代消歧，例如「应用化学」。</p>
          </div>
          <div class="profile-control">
            <a-input v-model:value="draft.major" placeholder="未填写" :maxlength="64" allow-clear />
          </div>
        </div>

        <div class="profile-row">
          <div class="profile-meta">
            <div class="profile-title-line">
              <span class="profile-title">回答风格</span>
            </div>
            <p class="profile-description">
              只影响表达与详略，不影响结论：安全规范、化学品性质与合规判定始终以知识库为准。
            </p>
          </div>
          <div class="profile-control">
            <a-select v-model:value="draft.response_style">
              <a-select-option v-for="option in styleOptions" :key="option.value" :value="option.value">
                {{ option.label }}
              </a-select-option>
            </a-select>
          </div>
        </div>

        <div class="profile-note">
          <div class="note-title">风格说明</div>
          <ul class="note-list">
            <li v-for="option in styleOptions" :key="option.value">
              <strong>{{ option.label }}</strong>：{{ option.hint }}
            </li>
          </ul>
          <p class="note-foot">
            优先级：本轮对话中临时提出的要求 &gt; 此处选择的风格 &gt; 长期记忆中记下的偏好。
          </p>
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { userConfigApi } from '@/apis/user_config_api'
import { useUserStore } from '@/stores/user'

const IDENTITY_LABELS = {
  student: '学生',
  faculty: '教师',
  system_admin: '管理员'
}

const GENDER_OPTIONS = ['男', '女', '其他']

// 与后端 yuxi.services.user_profile 的取值一一对应，新增档位要两边一起改
const STYLE_OPTIONS = [
  { value: 'concise', label: '简单清晰', hint: '先给结论，短句为主，150 字以内；安全警示一条不省。' },
  { value: 'normal', label: '正常风格', hint: '不额外定义风格，由当前对话事实与长期记忆决定。' },
  { value: 'thorough', label: '详细严密', hint: '先说前提与边界，再分步骤给依据，不确定的地方明确标注。' }
]

const userStore = useUserStore()

const loading = ref(false)
const saving = ref(false)
// 未成功加载前禁止保存：PUT /config 是整份替换，带着空值提交会把
// 没显示在这一页的开关（例如长期记忆开关）一起重置掉。
const loaded = ref(false)
const draft = reactive({
  full_name: '',
  gender: undefined,
  major: '',
  response_style: 'normal'
})
const saved = ref({})
// 后端按整份对象保存，这一页不发也要原样回传，否则会被默认值覆盖
const enableMemory = ref(true)

const identityLabel = computed(() => IDENTITY_LABELS[userStore.businessRole] || userStore.businessRole || '—')
const genderOptions = GENDER_OPTIONS
const styleOptions = STYLE_OPTIONS

const hasUnsavedChanges = computed(
  () => JSON.stringify(normalize(draft)) !== JSON.stringify(saved.value)
)
const saveButtonText = computed(() => (hasUnsavedChanges.value ? '保存（有修改）' : '保存'))

function normalize(source) {
  return {
    full_name: source.full_name || '',
    gender: source.gender || '',
    major: source.major || '',
    response_style: source.response_style || 'normal'
  }
}

function applyResponse(res) {
  draft.full_name = res.full_name || ''
  draft.gender = res.gender || undefined
  draft.major = res.major || ''
  draft.response_style = res.response_style || 'normal'
  enableMemory.value = res.enable_memory
  saved.value = normalize(draft)
  loaded.value = true
}

const loadProfile = async () => {
  loading.value = true
  try {
    applyResponse(await userConfigApi.get())
  } catch (error) {
    message.error(error.message || '加载个人资料失败')
  } finally {
    loading.value = false
  }
}

const saveProfile = async () => {
  if (!hasUnsavedChanges.value) {
    message.info('个人资料未变化')
    return
  }

  saving.value = true
  try {
    applyResponse(await userConfigApi.update({
      enable_memory: enableMemory.value,
      full_name: draft.full_name || null,
      gender: draft.gender || null,
      major: draft.major || null,
      response_style: draft.response_style
    }))
    message.success('个人资料已保存')
  } catch (error) {
    message.error(error.message || '保存个人资料失败')
  } finally {
    saving.value = false
  }
}

onMounted(loadProfile)
</script>

<style lang="less" scoped>
.personal-profile {
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

  .profile-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;
  }

  .profile-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--gray-150);

    &:last-child {
      border-bottom: none;
    }

    @media (max-width: 560px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .profile-meta {
    min-width: 0;
  }

  .profile-title-line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .profile-title {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 1.4;
  }

  .readonly-tag {
    margin: 0;
    font-size: 12px;
    color: var(--gray-600);
  }

  .profile-description {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.5;
  }

  .profile-control {
    flex-shrink: 0;
    width: 220px;
    max-width: 100%;
    text-align: right;

    @media (max-width: 560px) {
      width: 100%;
      text-align: left;
    }
  }

  .readonly-value {
    color: var(--gray-700);
    font-size: 14px;
  }

  .profile-note {
    padding: 14px 16px;
    background: var(--gray-50);
    border-top: 1px solid var(--gray-150);

    .note-title {
      color: var(--gray-900);
      font-size: 13px;
      font-weight: 500;
      margin-bottom: 8px;
    }

    .note-list {
      margin: 0;
      padding-left: 18px;
      color: var(--gray-600);
      font-size: 13px;
      line-height: 1.7;
    }

    .note-foot {
      margin: 8px 0 0;
      color: var(--gray-600);
      font-size: 13px;
      line-height: 1.6;
    }
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
