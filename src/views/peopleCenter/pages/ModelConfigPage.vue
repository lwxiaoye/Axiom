<template>
  <main class="model-page">
    <header class="page-heading">
      <div><h1>模型配置</h1><p>连接你的模型服务，为对话设置默认模型。</p></div>
      <span class="protocol">OpenAI 兼容</span>
    </header>
    <div v-if="loading" class="loading" role="status">正在加载配置…</div>
    <div v-else-if="loadError" class="feedback error" role="alert">
      {{ loadError }} <button type="button" @click="load">重试</button>
    </div>
    <form v-else novalidate @submit.prevent="save">
      <section class="config-card">
        <div class="card-heading"><div class="card-icon"><ApiOutlined /></div><div><h2>API 连接</h2><p>配置仅用于当前账号，密钥加密保存。</p></div></div>
        <label for="model-base">请求地址 <span>Base URL</span></label>
        <input id="model-base" v-model="form.base_url" type="url" required maxlength="2048" placeholder="https://api.example.com/v1" :disabled="busy" />
        <p class="field-help">地址须以 https:// 或 http:// 开头（包含两个斜杠），通常以 /v1 结尾。</p>
        <label for="model-key">API Key <span v-if="hasKey" class="saved-key">已配置</span></label>
        <input id="model-key" v-model="form.api_key" type="password" autocomplete="new-password" maxlength="8192" :required="!hasKey" :disabled="busy" :placeholder="hasKey ? '已安全保存，留空保持原密钥' : '输入服务商提供的 API Key'" />
        <p class="field-help">修改请求地址时需要重新输入密钥。</p>
        <label for="model-name">模型名称</label>
        <input id="model-name" v-model="form.model" required maxlength="255" placeholder="例如 gpt-4o-mini 或服务商提供的模型 ID" :disabled="busy" />
        <p class="field-help">请与服务商的模型 ID 完全一致，保存后用于新对话。</p>
        <div class="enable-row"><div><strong>启用此配置</strong><p>关闭后恢复原有模型网关配置。</p></div><a-switch v-model:checked="form.enabled" :disabled="busy" aria-label="启用此模型配置" /></div>
      </section>
      <p v-if="testing" class="feedback" role="status">正在等待模型响应，服务端测试最多 15 秒…</p>
      <div v-if="feedback" class="feedback" :class="feedback.success ? 'success' : 'error'" role="status">
        <CheckCircleOutlined v-if="feedback.success" /><ExclamationCircleOutlined v-else />
        <span>{{ feedback.message }}<template v-if="feedback.latency_ms != null"> · {{ feedback.latency_ms }} ms</template></span>
      </div>
      <footer class="actions"><p>测试会发送一条短消息，可能产生少量费用。</p><div>
        <button type="button" class="secondary" :disabled="busy" @click="test">{{ testing ? '正在测试…' : '测试连接' }}</button>
        <button type="submit" class="primary" :disabled="busy">{{ saving ? '正在保存…' : '保存配置' }}</button>
      </div></footer>
    </form>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';
import { ApiOutlined, CheckCircleOutlined, ExclamationCircleOutlined } from '@ant-design/icons-vue';
import { requestAgentApi } from '../agentApi';

type Config = { base_url: string; model: string; enabled: boolean; has_api_key: boolean };
type Result = { success: boolean; message: string; latency_ms?: number };
const form = reactive({ base_url: '', model: '', api_key: '', enabled: true });
const loading = ref(true);
const loadError = ref('');
const hasKey = ref(false);
const testing = ref(false);
const saving = ref(false);
const feedback = ref<Result | null>(null);
const busy = computed(() => testing.value || saving.value);
watch(form, () => { feedback.value = null; });

function apply(data: Config) {
  Object.assign(form, { base_url: data.base_url, model: data.model, api_key: '', enabled: data.has_api_key ? data.enabled : true });
  hasKey.value = data.has_api_key;
}
async function load() {
  loading.value = true;
  loadError.value = '';
  try { apply(await requestAgentApi<Config>('/model-connection')); }
  catch (error: any) { loadError.value = error.message || '配置加载失败'; }
  finally { loading.value = false; }
}
function valid() {
  if (!form.base_url.trim() || !form.model.trim() || (!hasKey.value && !form.api_key.trim())) {
    feedback.value = { success: false, message: '请填写请求地址、API Key 和模型名称' };
    return false;
  }
  try {
    const address = form.base_url.trim();
    const url = new URL(address);
    if (!/^https?:\/\//i.test(address) || !url.hostname || url.username || url.password || url.search || url.hash || /\s/.test(address)) throw new Error();
  } catch {
    feedback.value = { success: false, message: '请求地址格式错误，请使用 https://域名/v1 或 http://主机:端口/v1，注意冒号后需要两个斜杠 //' };
    return false;
  }
  return true;
}
async function test() {
  if (busy.value || !valid()) return;
  testing.value = true;
  feedback.value = null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  try { feedback.value = await requestAgentApi<Result>('/model-connection/test', { method: 'POST', body: JSON.stringify(form), signal: controller.signal }); }
  catch (error: any) { feedback.value = { success: false, message: controller.signal.aborted ? '等待服务器超时，请检查网络后重试' : error.message || '测试失败' }; }
  finally { clearTimeout(timer); testing.value = false; }
}
async function save() {
  if (busy.value || !valid()) return;
  saving.value = true;
  try {
    apply(await requestAgentApi<Config>('/model-connection', { method: 'PUT', body: JSON.stringify(form) }));
    // Wait for the reactive form reset before showing the saved status.
    await nextTick();
    feedback.value = { success: true, message: '配置已保存，将用于下一次模型请求' };
    window.dispatchEvent(new Event('axiom:model-config-updated'));
  } catch (error: any) { feedback.value = { success: false, message: error.message || '保存失败' }; }
  finally { saving.value = false; }
}
onMounted(load);
</script>

<style scoped>
.model-page { width: 100%; max-width: 980px; margin: 0 auto; padding: 38px 32px 64px; color: #18181b; }
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 28px; }
h1 { margin: 0 0 8px; font-size: 24px; font-weight: 650; letter-spacing: -.6px; }
h2 { margin: 0 0 4px; font-size: 16px; font-weight: 600; }
p { margin: 0; color: #85858f; font-size: 13px; line-height: 1.7; }
.protocol { padding: 5px 10px; border: 1px solid #e5e5e9; border-radius: 8px; color: #71717a; font-size: 12px; white-space: nowrap; }
.config-card { padding: 30px; border: 1px solid #e5e5ea; border-radius: 16px; background: white; }
.card-heading { display: flex; gap: 13px; align-items: center; margin-bottom: 28px; }
.card-icon { display: grid; place-items: center; width: 44px; height: 44px; border-radius: 12px; background: #f4f4f5; font-size: 21px; }
label { display: flex; align-items: center; gap: 10px; margin: 22px 0 9px; font-size: 14px; font-weight: 550; }
label span { font-size: 12px; font-weight: 400; color: #92929b; }
label .saved-key { color: #238257; }
input { width: 100%; height: 44px; padding: 0 13px; color: #27272a; border: 1px solid #dedee5; border-radius: 9px; background: #fff; outline: none; transition: border-color .15s; }
input:focus { border-color: #71717a; box-shadow: 0 0 0 3px #18181b08; }
input::placeholder { color: #a1a1aa; }
.field-help { margin-top: 7px; font-size: 12px; }
.enable-row { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #eeeef1; padding-top: 22px; margin-top: 28px; gap: 20px; }
.enable-row strong { font-weight: 550; font-size: 14px; }
.enable-row p { margin-top: 3px; font-size: 12px; }
.actions { display: flex; align-items: center; justify-content: space-between; margin-top: 22px; gap: 20px; }
.actions div { display: flex; gap: 10px; flex-shrink: 0; }
button { cursor: pointer; border-radius: 9px; padding: 10px 18px; font-size: 13px; font-weight: 550; }
.primary { background: #151923; border: 1px solid #151923; color: white; }
.secondary { background: white; border: 1px solid #dedee5; color: #3f3f46; }
button:disabled { opacity: .55; cursor: wait; }
button:focus-visible { outline: 2px solid #71717a; outline-offset: 3px; }
.feedback { display: flex; align-items: center; gap: 9px; margin-top: 18px; padding: 13px 16px; border-radius: 9px; font-size: 13px; }
.success { color: #23704c; background: #f0f8f3; }
.error { color: #a13737; background: #fff2f2; }
.loading { padding: 50px; text-align: center; color: #85858f; }
@media (max-width: 640px) { .model-page { padding: 24px 16px 48px; } .config-card { padding: 20px; } .actions { align-items: stretch; flex-direction: column; } .actions div { justify-content: flex-end; } h1 { font-size: 22px; } }
</style>
