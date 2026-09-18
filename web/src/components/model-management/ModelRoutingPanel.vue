<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { modelProviderApi, modelRoutingApi } from '@/apis/system_api'

const loading = ref(false)
const saving = ref(false)
const models = ref([])
const form = reactive({
  chat_model_spec: '',
  intent_model_spec: '',
  intent_enabled: true,
  memory_model_spec: '',
  memory_extract_min_interval: 0,
  strategy: 'weighted',
})

const load = async () => {
  loading.value = true
  try {
    const [routing, modelResult] = await Promise.all([modelRoutingApi.getConfig(), modelProviderApi.getV2Models('chat')])
    Object.assign(form, routing.data || {})
    models.value = Object.values(modelResult.data || {}).flatMap((group) => group.models || [])
  } catch (error) { message.error(error.message || '加载模型路由配置失败') } finally { loading.value = false }
}
const save = async () => {
  saving.value = true
  try { await modelRoutingApi.updateConfig(form); message.success('模型路由配置已保存') }
  catch (error) { message.error(error.message || '保存失败') } finally { saving.value = false }
}
onMounted(load)
</script>
<template>
  <a-card title="模型路由与负载均衡" :loading="loading" class="routing-panel">
    <a-form layout="vertical">
      <a-form-item label="回答模型">
        <a-select v-model:value="form.chat_model_spec" allow-clear show-search placeholder="沿用系统默认模型">
          <a-select-option v-for="model in models" :key="`chat-${model.spec}`" :value="model.spec">{{ model.display_name }} ({{ model.spec }})</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="前置意图识别模型">
        <a-select v-model:value="form.intent_model_spec" allow-clear show-search placeholder="不启用时使用规则识别">
          <a-select-option v-for="model in models" :key="`intent-${model.spec}`" :value="model.spec">{{ model.display_name }} ({{ model.spec }})</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="记忆抽取模型">
        <a-select v-model:value="form.memory_model_spec" allow-clear show-search placeholder="沿用本会话的回答模型">
          <a-select-option v-for="model in models" :key="`memory-${model.spec}`" :value="model.spec">{{ model.display_name }} ({{ model.spec }})</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="记忆抽取节流（秒）">
        <a-input-number v-model:value="form.memory_extract_min_interval" :min="0" :max="86400" :step="10" style="width: 200px" />
        <span class="routing-hint">0 表示每轮抽取；大于 0 时攒批抽取，被跳过的轮次会在下次一并补上，不会丢失。</span>
      </a-form-item>
      <a-form-item label="账号选择策略">
        <a-radio-group v-model:value="form.strategy"><a-radio value="weighted">按权重</a-radio></a-radio-group>
      </a-form-item>
      <a-form-item><a-switch v-model:checked="form.intent_enabled" /> 启用前置意图识别</a-form-item>
      <a-button type="primary" :loading="saving" @click="save">保存路由配置</a-button>
    </a-form>
  </a-card>
</template>

<style scoped>
.routing-hint {
  margin-left: 8px;
  color: var(--ant-color-text-secondary, #8c8c8c);
  font-size: 12px;
}
</style>
