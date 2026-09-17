<template>
  <a-drawer v-model:open="open" :width="620" title="公开配置" :destroy-on-close="true" @close="clearSecret">
    <a-alert
      type="info"
      show-icon
      message="仅供发布者自用"
      description="API 与 iframe 由此处统一开关。所有模型调用与计费归属该智能体的发布者。"
    />

    <section class="api-section">
      <h3>公开能力</h3>
      <div class="public-switch-row">
        <span><strong>启用 API</strong><em>开放 OpenAI 兼容接口，仅发布者 Key 可调用。</em></span>
        <a-switch v-model:checked="publicConfig.apiEnabled" :loading="configSaving" @change="savePublicConfig" />
      </div>
      <div class="public-switch-row">
        <span><strong>启用 iframe 嵌入</strong><em>仅支持带来源白名单的静态嵌入 Key。</em></span>
        <a-switch v-model:checked="publicConfig.iframeEnabled" :disabled="!publicConfig.apiEnabled" :loading="configSaving" @change="savePublicConfig" />
      </div>
    </section>

    <a-alert
      v-if="!publicConfig.apiEnabled"
      class="api-section"
      type="warning"
      show-icon
      message="API 未启用"
      description="智能体仍正常发布在广场；启用 API 后才可创建或使用发布者 Key。"
    />

    <template v-if="publicConfig.apiEnabled">
    <section class="api-section">
      <h3>OpenAI 兼容</h3>
      <p>Base URL：<code>{{ openaiBaseUrl }}</code></p>
      <p>模型名：<code>agent_{{ app?.id || 'APP_ID' }}</code></p>
      <a-textarea :value="openaiExample" :rows="5" readonly />
    </section>

    <section class="api-section">
      <h3>访问密钥</h3>
      <div class="key-create">
        <a-input v-model:value="newKeyName" :maxlength="128" placeholder="密钥名称，例如生产环境" @press-enter="createKey" />
        <a-button type="primary" :loading="creating" @click="createKey">创建 Key</a-button>
      </div>
      <a-alert v-if="createdApiSecret" type="info" show-icon message="API Key 已创建" class="created-secret">
        <template #description>
          密钥在下方以脱敏形式展示；之后仍可点击“复制”获取完整 Key。
          <a-button type="link" size="small" @click="copyRawSecret(createdApiSecret)">复制完整 Key</a-button>
        </template>
      </a-alert>
      <a-spin :spinning="loading">
        <div v-if="keys.length" class="key-list">
          <div v-for="key in keys" :key="key.id" class="key-row">
            <div class="key-details">
              <div class="key-identity">
                <strong>{{ key.name || '未命名 Key' }}</strong>
                <a-button class="key-copy" type="link" size="small" @click="copyKey(key)">
                  <code>{{ maskedKey(key) }}</code> 复制
                </a-button>
                <a-tag :color="key.status === 'active' ? 'green' : 'default'">{{ keyStatusLabel(key.status) }}</a-tag>
              </div>
              <em v-if="key.status !== 'active' && key.status !== 'disabled'">已撤销的旧 Key 不能重新启用，请删除后新建。</em>
            </div>
            <div class="key-actions">
              <a-switch
                :checked="key.status === 'active'"
                :loading="isKeyUpdating(key.id)"
                :disabled="!canToggleKey(key)"
                checked-children="开"
                un-checked-children="停"
                :aria-label="`${key.name || 'API'} Key 启停`"
                @change="setKeyEnabled('api', key, Boolean($event))"
              />
              <a-popconfirm title="删除后无法恢复，确认删除该 API Key？" @confirm="deleteKey('api', key.id)">
                <a-button danger type="text" size="small" class="key-delete" title="删除 API Key" aria-label="删除 API Key">
                  <DeleteOutlined />
                </a-button>
              </a-popconfirm>
            </div>
          </div>
        </div>
        <a-empty v-else description="尚未创建 API Key" :image="false" />
      </a-spin>
    </section>

    <section v-if="publicConfig.iframeEnabled" class="api-section">
      <h3>静态嵌入 Key</h3>
      <p>为一个精确 HTTPS 来源创建 <code>qze_</code> Key。它不能调用 OpenAI API；Key 只放在 iframe 的 URL fragment，不会发送到服务器。</p>
      <div class="embed-create">
        <a-input v-model:value="embedKeyName" :maxlength="128" placeholder="名称，例如门户首页" />
        <a-input v-model:value="embedOrigin" placeholder="https://portal.example.com" />
        <a-button type="primary" :loading="embedCreating" @click="createEmbedKey">创建嵌入 Key</a-button>
      </div>
      <a-alert v-if="embedSecret" type="info" show-icon message="嵌入 Key 已创建" class="created-secret">
        <template #description>
          <a-textarea :value="directEmbedExample" :rows="4" readonly />
          <a-button type="link" size="small" @click="copyRawSecret(embedSecret.secret)">复制完整 Key</a-button>
          <a-button type="link" size="small" @click="copyEmbedCode">复制嵌入代码</a-button>
        </template>
      </a-alert>
      <div v-if="embedKeys.length" class="key-list">
        <div v-for="key in embedKeys" :key="key.id" class="key-row">
          <div class="key-details">
            <div class="key-identity">
              <strong>{{ key.name || '未命名嵌入 Key' }}</strong>
              <a-button class="key-copy" type="link" size="small" @click="copyKey(key)">
                <code>{{ maskedKey(key) }}</code> 复制
              </a-button>
              <a-tag :color="key.status === 'active' ? 'green' : 'default'">{{ keyStatusLabel(key.status) }}</a-tag>
            </div>
            <em>{{ key.origin }}</em>
          </div>
          <div class="key-actions">
            <a-switch
              :checked="key.status === 'active'"
              :loading="isKeyUpdating(key.id)"
              :disabled="!canToggleKey(key)"
              checked-children="开"
              un-checked-children="停"
              :aria-label="`${key.name || '嵌入'} Key 启停`"
              @change="setKeyEnabled('embed', key, Boolean($event))"
            />
            <a-popconfirm title="删除后无法恢复，确认删除该嵌入 Key？" @confirm="deleteKey('embed', key.id)">
              <a-button danger type="text" size="small" class="key-delete" title="删除嵌入 Key" aria-label="删除嵌入 Key">
                <DeleteOutlined />
              </a-button>
            </a-popconfirm>
          </div>
        </div>
      </div>
    </section>

    <section class="api-section">
      <h3>最近调用</h3>
      <a-spin :spinning="usageLoading">
        <a-table :columns="usageColumns" :data-source="usage" :pagination="false" :row-key="(row: AgentApiUsageItem) => row.id" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'duration'">{{ record.durationMs ?? '-' }} ms</template>
            <template v-else-if="column.key === 'tokens'">{{ record.usageKnown ? `${record.inputTokens ?? 0}/${record.outputTokens ?? 0}` : '未知' }}</template>
            <template v-else-if="column.key === 'amount'">{{ record.providerAmountRaw ?? '未知' }}{{ record.providerAmountUnit ? ` ${record.providerAmountUnit}` : '' }}</template>
          </template>
        </a-table>
      </a-spin>
    </section>
    </template>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { message } from 'ant-design-vue';
import { DeleteOutlined } from '@ant-design/icons-vue';
import {
  createAgentApiKey,
  createAgentEmbedKey,
  deleteAgentApiKey,
  deleteAgentEmbedKey,
  getAgentPublicConfiguration,
  listAgentEmbedKeys,
  listAgentApiKeys,
  queryAgentApiUsage,
  updateAgentApiKeyStatus,
  updateAgentEmbedKeyStatus,
  updateAgentPublicConfiguration,
  type AgentPublicConfiguration,
  type AgentEmbedKeyItem,
  type AgentApiKeyItem,
  type AgentApiUsageItem,
  type AiWorkflowApp,
} from '../../workflow/api/workflow.api';

const open = ref(false);
const app = ref<Partial<AiWorkflowApp> | null>(null);
const keys = ref<AgentApiKeyItem[]>([]);
const usage = ref<AgentApiUsageItem[]>([]);
const loading = ref(false);
const usageLoading = ref(false);
const creating = ref(false);
const newKeyName = ref('');
const createdApiSecret = ref('');
const embedKeys = ref<AgentEmbedKeyItem[]>([]);
const embedKeyName = ref('');
const embedOrigin = ref('');
const embedCreating = ref(false);
const embedSecret = ref<{ id: string; secret: string } | null>(null);
const keyUpdating = ref<Record<string, boolean>>({});
const publicConfig = ref<AgentPublicConfiguration>({ apiEnabled: false, iframeEnabled: false });
const configSaving = ref(false);

const openaiBaseUrl = computed(() => `${window.location.origin}/agent-api/openai/v1`);
const openaiExample = computed(() => `curl ${openaiBaseUrl.value}/chat/completions \\
  -H "Authorization: Bearer qza_..." \\
  -H "Content-Type: application/json" \\
  -d '{"model":"agent_${app.value?.id || 'APP_ID'}","messages":[{"role":"user","content":"你好"}]}'`);
const directEmbedExample = computed(() => {
  if (!embedSecret.value) return '';
  return `<iframe src="${window.location.origin}/agent-api/embed/v1/frame/${app.value?.id || 'APP_ID'}?embedKeyId=${embedSecret.value.id}#embedKey=${embedSecret.value.secret}" width="100%" height="480" frameborder="0"></iframe>`;
});
const usageColumns = [
  { title: '来源', dataIndex: 'source', key: 'source', width: 80 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 82 },
  { title: '耗时', key: 'duration', width: 96 },
  { title: '输入/输出 Token', key: 'tokens' },
  { title: '费用', key: 'amount', width: 116 },
];

async function load() {
  if (!app.value?.id) return;
  loading.value = true;
  usageLoading.value = true;
  try {
    const [keyResult, usageResult, embedKeyResult, configResult] = await Promise.all([
      listAgentApiKeys(app.value.id),
      queryAgentApiUsage(app.value.id, { pageSize: 10 }),
      listAgentEmbedKeys(app.value.id),
      getAgentPublicConfiguration(app.value.id),
    ]);
    keys.value = keyResult?.items || [];
    usage.value = usageResult?.records || [];
    embedKeys.value = embedKeyResult?.items || [];
    publicConfig.value = configResult || { apiEnabled: false, iframeEnabled: false };
  } catch {
    keys.value = [];
    usage.value = [];
    embedKeys.value = [];
    publicConfig.value = { apiEnabled: false, iframeEnabled: false };
    message.error('无法加载 API 管理信息');
  } finally {
    loading.value = false;
    usageLoading.value = false;
  }
}

async function savePublicConfig() {
  if (!app.value?.id || configSaving.value) return;
  if (!publicConfig.value.apiEnabled) publicConfig.value.iframeEnabled = false;
  configSaving.value = true;
  try {
    publicConfig.value = await updateAgentPublicConfiguration(app.value.id, publicConfig.value);
    message.success('公开配置已保存');
    await load();
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '保存公开配置失败');
    await load();
  } finally {
    configSaving.value = false;
  }
}

async function createKey() {
  if (!app.value?.id || creating.value) return;
  creating.value = true;
  try {
    const created = await createAgentApiKey(app.value.id, newKeyName.value.trim());
    createdApiSecret.value = created.secret;
    newKeyName.value = '';
    await load();
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '创建 API Key 失败');
  } finally {
    creating.value = false;
  }
}

async function createEmbedKey() {
  if (!app.value?.id || embedCreating.value) return;
  embedCreating.value = true;
  try {
    const created = await createAgentEmbedKey(app.value.id, embedKeyName.value.trim(), embedOrigin.value.trim());
    embedSecret.value = { id: created.id, secret: created.secret };
    embedKeyName.value = '';
    await load();
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '创建嵌入 Key 失败');
  } finally {
    embedCreating.value = false;
  }
}

async function copyRawSecret(secret: string) {
  try {
    await copyText(secret);
    message.success('已复制完整 Key');
  } catch {
    message.warning('复制失败，请手动复制');
  }
}

type ManagedKey = AgentApiKeyItem | AgentEmbedKeyItem;
type KeyKind = 'api' | 'embed';

function canToggleKey(key: ManagedKey) {
  return key.status === 'active' || key.status === 'disabled';
}

function keyStatusLabel(status: string) {
  if (status === 'active') return '已启用';
  if (status === 'disabled') return '已停用';
  return '已撤销';
}

function maskedKey(key: ManagedKey) {
  const value = key.secret || key.prefix;
  return `${value.slice(0, 4)}••••••••••••`;
}

async function copyText(value: string) {
  if (!value) throw new Error('empty_copy_value');
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // HTTP intranet deployments cannot use the asynchronous Clipboard API.
      // Keep the secret in memory and use the browser's legacy copy command.
    }
  }
  const input = document.createElement('textarea');
  input.value = value;
  input.setAttribute('readonly', '');
  input.style.cssText = 'position:fixed;opacity:0;pointer-events:none;';
  document.body.append(input);
  input.select();
  const copied = document.execCommand('copy');
  input.remove();
  if (!copied) throw new Error('copy_failed');
}

async function copyKey(key: ManagedKey) {
  if (!key.secret) {
    message.warning('此 Key 为旧版本创建，未保存加密副本，无法恢复完整密钥；请删除后新建。');
    return;
  }
  await copyRawSecret(key.secret);
}

function isKeyUpdating(keyId: string) {
  return Boolean(keyUpdating.value[keyId]);
}

async function setKeyEnabled(kind: KeyKind, key: ManagedKey, enabled: boolean) {
  if (!app.value?.id || isKeyUpdating(key.id)) return;
  keyUpdating.value = { ...keyUpdating.value, [key.id]: true };
  try {
    if (kind === 'api') {
      await updateAgentApiKeyStatus(app.value.id, key.id, enabled);
    } else {
      await updateAgentEmbedKeyStatus(app.value.id, key.id, enabled);
    }
    message.success(`${kind === 'api' ? 'API' : '嵌入'} Key 已${enabled ? '启用' : '停用'}`);
    await load();
  } catch (error: any) {
    message.error(error?.response?.data?.detail || `${enabled ? '启用' : '停用'} Key 失败`);
  } finally {
    const remaining = { ...keyUpdating.value };
    delete remaining[key.id];
    keyUpdating.value = remaining;
  }
}

async function deleteKey(kind: KeyKind, keyId: string) {
  if (!app.value?.id) return;
  try {
    if (kind === 'api') {
      await deleteAgentApiKey(app.value.id, keyId);
    } else {
      await deleteAgentEmbedKey(app.value.id, keyId);
    }
    message.success(`${kind === 'api' ? 'API' : '嵌入'} Key 已删除`);
    await load();
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '删除 Key 失败');
  }
}

async function copyEmbedCode() {
  try {
    await copyText(directEmbedExample.value);
    message.success('已复制嵌入代码');
  } catch {
    message.warning('复制失败，请手动复制');
  }
}

function clearSecret() {
  createdApiSecret.value = '';
  embedSecret.value = null;
  keyUpdating.value = {};
}

function init(item: Partial<AiWorkflowApp>) {
  app.value = item;
  createdApiSecret.value = '';
  embedSecret.value = null;
  keyUpdating.value = {};
  publicConfig.value = { apiEnabled: false, iframeEnabled: false };
  open.value = true;
  void load();
}

defineExpose({ init });
</script>

<style scoped lang="less">
.api-section { margin-top: 22px; }
.api-section h3 { margin-bottom: 8px; color: #111827; font-size: 14px; }
.api-section p { color: #64748b; font-size: 12px; line-height: 1.7; }
.public-switch-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
.public-switch-row span { display: grid; gap: 3px; }
.public-switch-row em { color: #94a3b8; font-size: 12px; font-style: normal; }
.key-create { display: flex; gap: 8px; margin: 12px 0; }
.created-secret { margin-bottom: 12px; overflow-wrap: anywhere; }
.key-list { display: grid; gap: 4px; }
.key-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 9px 0; border-bottom: 1px solid #f0f0f0; }
.key-details { display: grid; gap: 2px; min-width: 0; }
.key-identity { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; min-width: 0; }
.key-identity strong { color: #111827; }
.key-copy { padding-inline: 0; font-variant-numeric: tabular-nums; }
.key-copy code { color: #475569; }
.key-details em { color: #94a3b8; font-size: 12px; font-style: normal; }
.key-actions { display: flex; align-items: center; flex: none; gap: 6px; }
.key-delete { min-width: 32px; }
@media (max-width: 560px) {
  .key-row { align-items: flex-start; }
  .key-actions { padding-top: 2px; }
}
</style>
