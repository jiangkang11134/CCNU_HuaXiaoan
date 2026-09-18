<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { modelProviderApi, modelRoutingApi } from '@/apis/system_api'

const loading = ref(false)
const saving = ref(false)
const models = ref([])
const form = reactive({
  chat_model_spec: '',
  // 主备故障切换：备用按数组顺序接管，空位表示该档备用未配置
  chat_fallback_specs: ['', ''],
  chat_fallback_enabled: false,
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
    const specs = Array.isArray(form.chat_fallback_specs) ? form.chat_fallback_specs : []
    form.chat_fallback_specs = [specs[0] || '', specs[1] || '']
    models.value = Object.values(modelResult.data || {}).flatMap((group) => group.models || [])
  } catch (error) { message.error(error.message || '加载模型路由配置失败') } finally { loading.value = false }
}
const save = async () => {
  saving.value = true
  try {
    // 空位不落库，避免故障切换链里出现空 spec
    const payload = { ...form, chat_fallback_specs: (form.chat_fallback_specs || []).filter(Boolean) }
    await modelRoutingApi.updateConfig(payload)
    message.success('模型路由配置已保存')
  }
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
      <a-form-item label="备用模型 1（主模型不可用时接管）">
        <a-select v-model:value="form.chat_fallback_specs[0]" allow-clear show-search placeholder="留空表示不配置该备用">
          <a-select-option v-for="model in models" :key="`fb1-${model.spec}`" :value="model.spec">{{ model.display_name }} ({{ model.spec }})</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item label="备用模型 2（备用 1 也不可用时接管）">
        <a-select v-model:value="form.chat_fallback_specs[1]" allow-clear show-search placeholder="留空表示不配置该备用">
          <a-select-option v-for="model in models" :key="`fb2-${model.spec}`" :value="model.spec">{{ model.display_name }} ({{ model.spec }})</a-select-option>
        </a-select>
      </a-form-item>
      <a-form-item>
        <a-switch v-model:checked="form.chat_fallback_enabled" /> 启用主备故障切换
        <div class="routing-hint">主模型调用失败时按顺序自动切到备用模型；已经开始输出后不再切换（避免内容重复）。各模型的 base_url / API Key 请在“模型供应商”里编辑其所属供应商，密钥不会回显。</div>
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
