<template>
  <div class="lab-safety-view">
    <div class="lab-safety-panel">
      <section class="inspection-panel" aria-label="实验室安全隐患识别">
        <div class="panel-header">
          <div>
            <h1>实验室安全隐患识别</h1>
            <p>上传实验室现场图片，系统会识别可见安全隐患并生成整改建议。</p>
          </div>
          <a-tag color="processing">前台业务</a-tag>
        </div>

        <div
          class="upload-zone"
          :class="{ 'has-image': !!imagePreviewUrl, disabled: isSubmitting }"
          @click="triggerFileSelect"
          @dragover.prevent
          @drop.prevent="handleDrop"
        >
          <input
            ref="fileInputRef"
            type="file"
            class="file-input"
            accept="image/*"
            :disabled="isSubmitting"
            @change="handleFileChange"
          />
          <img v-if="imagePreviewUrl" :src="imagePreviewUrl" class="image-preview" alt="待识别图片" />
          <div v-else class="upload-placeholder">
            <ImageUp :size="34" />
            <span>选择或拖拽现场图片</span>
          </div>
        </div>

        <div v-if="selectedFile" class="selected-file">
          <span :title="selectedFile.name">{{ selectedFile.name }}</span>
          <button type="button" :disabled="isSubmitting" @click.stop="clearImage">
            <X :size="15" />
          </button>
        </div>

        <a-textarea
          v-model:value="inspectionNote"
          class="inspection-note"
          :disabled="isSubmitting"
          :auto-size="{ minRows: 4, maxRows: 7 }"
          placeholder="可补充实验室类型、区域、设备或你关注的检查重点"
        />

        <a-button
          type="primary"
          block
          size="large"
          :loading="isSubmitting"
          :disabled="!selectedFile"
          @click="submitInspection"
        >
          开始识别
        </a-button>
      </section>

      <section class="criteria-panel" aria-label="识别维度">
        <h2>识别维度</h2>
        <div class="criteria-grid">
          <div v-for="item in criteriaItems" :key="item.title" class="criteria-item">
            <component :is="item.icon" :size="18" />
            <div>
              <strong>{{ item.title }}</strong>
              <span>{{ item.description }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>

    <div class="lab-safety-chat">
      <AgentChatComponent
        ref="chatComponentRef"
        :single-mode="false"
        :frontend-config="frontendConfig"
        @thread-change="handleThreadChange"
      >
        <template #header-left>
          <div class="chat-business-title">
            <ShieldCheck :size="17" />
            <span>识别结果</span>
          </div>
        </template>
      </AgentChatComponent>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, FlaskConical, Flame, ImageUp, ShieldCheck, X, Zap } from 'lucide-vue-next'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import { useAgentStore } from '@/stores/agent'
import { useUserStore } from '@/stores/user'
import { uploadMultimodalImage } from '@/utils/multimodal_image_upload'

const LAB_SAFETY_PORTAL_FEATURE = 'lab_safety_recognition'

const LAB_SAFETY_INSPECTION_PROMPT = `请基于我上传的实验室现场图片进行安全隐患识别。

请严格围绕图片中可见内容输出：
1. 隐患清单：逐项说明隐患位置、可见依据、风险等级（高/中/低）和可能后果。
2. 整改建议：给出可执行的整改措施，区分立即处理和后续完善。
3. 复查要点：列出整改后需要复查的关键点。
4. 无法确认项：对于图片无法判断的信息明确标注“无法确认”，不要编造。`

const criteriaItems = [
  { title: '化学品与试剂', description: '标签、存放、泄漏和混放风险', icon: FlaskConical },
  { title: '用电与设备', description: '插排、电线、设备摆放和过载风险', icon: Zap },
  { title: '消防与通道', description: '明火、消防器材、疏散通道遮挡', icon: Flame },
  { title: '操作与防护', description: '个人防护、现场秩序和明显违规操作', icon: AlertTriangle }
]

const agentStore = useAgentStore()
const userStore = useUserStore()
const route = useRoute()
const router = useRouter()

const chatComponentRef = ref(null)
const fileInputRef = ref(null)
const selectedFile = ref(null)
const imagePreviewUrl = ref('')
const isSubmitting = ref(false)
const inspectionNote = ref('')
const frontendConfig = computed(() => (userStore.isChatOnlyUser ? agentStore.frontendChatConfig : null))

const activeFeatureEnabled = computed(
  () => !userStore.isChatOnlyUser || agentStore.frontendChatConfig?.show_lab_safety_recognition === true
)

const triggerFileSelect = () => {
  if (isSubmitting.value) return
  fileInputRef.value?.click()
}

const setSelectedFile = (file) => {
  if (!file) return
  if (!file.type?.startsWith('image/')) {
    message.error('请选择图片文件')
    return
  }
  clearImage()
  selectedFile.value = file
  imagePreviewUrl.value = URL.createObjectURL(file)
}

const handleFileChange = (event) => {
  const file = event.target?.files?.[0]
  setSelectedFile(file)
  event.target.value = ''
}

const handleDrop = (event) => {
  if (isSubmitting.value) return
  const file = event.dataTransfer?.files?.[0]
  setSelectedFile(file)
}

const clearImage = () => {
  if (imagePreviewUrl.value) {
    URL.revokeObjectURL(imagePreviewUrl.value)
  }
  imagePreviewUrl.value = ''
  selectedFile.value = null
}

const buildInspectionPrompt = () => {
  const note = inspectionNote.value.trim()
  return note ? `${LAB_SAFETY_INSPECTION_PROMPT}\n\n补充现场信息：${note}` : LAB_SAFETY_INSPECTION_PROMPT
}

const submitInspection = async () => {
  if (!selectedFile.value) {
    message.error('请先上传实验室现场图片')
    return
  }

  isSubmitting.value = true
  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
    if (!activeFeatureEnabled.value) {
      throw new Error('后台未开放实验室安全隐患识别')
    }
    const imageData = await uploadMultimodalImage(selectedFile.value, {
      portalFeature: LAB_SAFETY_PORTAL_FEATURE
    })
    if (!imageData?.imageContent) {
      throw new Error('图片处理失败，未返回可识别内容')
    }
    await chatComponentRef.value?.sendPresetMessage({
      text: buildInspectionPrompt(),
      image: imageData,
      meta: { portal_feature: LAB_SAFETY_PORTAL_FEATURE }
    })
    clearImage()
    inspectionNote.value = ''
    await nextTick()
  } catch (error) {
    message.error(error.message || '安全隐患识别提交失败')
  } finally {
    isSubmitting.value = false
  }
}

const handleThreadChange = (threadId) => {
  const currentRouteThreadId = typeof route.params.thread_id === 'string' ? route.params.thread_id : ''
  const nextThreadId = threadId || ''
  if (currentRouteThreadId === nextThreadId) return
  if (nextThreadId) {
    router.replace({ name: 'LabSafetyRecognitionWithThreadId', params: { thread_id: nextThreadId } })
    return
  }
  router.replace({ name: 'LabSafetyRecognition' })
}

watch(
  () => route.params.thread_id,
  async (threadId) => {
    const value = typeof threadId === 'string' ? threadId : ''
    const ok = await chatComponentRef.value?.selectThreadFromRoute?.(value)
    if (value && ok === false) {
      await router.replace({ name: 'LabSafetyRecognition' })
    }
  },
  { immediate: true }
)

watch(chatComponentRef, (instance) => {
  if (!instance) return
  const threadId = typeof route.params.thread_id === 'string' ? route.params.thread_id : ''
  void instance.selectThreadFromRoute?.(threadId)
})

onBeforeUnmount(() => {
  clearImage()
})
</script>

<style lang="less" scoped>
.lab-safety-view {
  display: grid;
  grid-template-columns: minmax(320px, 360px) minmax(0, 1fr);
  width: 100%;
  height: 100%;
  min-height: 0;
  background: var(--main-0);
}

.lab-safety-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 0;
  padding: 18px;
  border-right: 1px solid var(--gray-100);
  background: var(--gray-10);
  overflow-y: auto;
}

.inspection-panel,
.criteria-panel {
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--main-0);
  padding: 16px;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;

  h1 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 18px;
    font-weight: 650;
    line-height: 24px;
  }

  p {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 20px;
  }
}

.upload-zone {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  aspect-ratio: 4 / 3;
  border: 1px dashed var(--gray-300);
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--gray-600);
  cursor: pointer;
  overflow: hidden;
  transition:
    border-color 0.18s ease,
    background-color 0.18s ease;

  &:hover {
    border-color: var(--main-500);
    background: var(--main-20);
  }

  &.disabled {
    cursor: not-allowed;
    opacity: 0.72;
  }
}

.file-input {
  display: none;
}

.upload-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  font-size: 14px;
}

.image-preview {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: var(--gray-25);
}

.selected-file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  color: var(--gray-700);
  font-size: 13px;

  span {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px;
    height: 26px;
    border: 1px solid var(--gray-100);
    border-radius: 6px;
    background: var(--main-0);
    color: var(--gray-600);
    cursor: pointer;
  }
}

.inspection-note {
  margin: 14px 0;
}

.criteria-panel {
  h2 {
    margin: 0 0 12px;
    color: var(--gray-900);
    font-size: 15px;
    font-weight: 650;
  }
}

.criteria-grid {
  display: grid;
  gap: 10px;
}

.criteria-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 10px;
  align-items: flex-start;
  color: var(--main-700);

  strong {
    display: block;
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 650;
    line-height: 18px;
  }

  span {
    display: block;
    margin-top: 2px;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 18px;
  }
}

.lab-safety-chat {
  min-width: 0;
  min-height: 0;
}

.chat-business-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--gray-800);
  font-size: 14px;
  font-weight: 650;
}

:deep(.chat-container) {
  height: 100%;
}

@media (max-width: 980px) {
  .lab-safety-view {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(520px, 1fr);
    overflow-y: auto;
  }

  .lab-safety-panel {
    border-right: 0;
    border-bottom: 1px solid var(--gray-100);
  }
}
</style>
