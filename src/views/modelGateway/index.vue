<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <PageWrapper contentFullHeight>
    <div class="gateway-page">
      <section class="gateway-header">
        <div>
          <span class="eyebrow">MODEL GATEWAY</span>
          <h1>模型网关配置</h1>
          <p>维护对外模型名称、上游渠道和发布快照，保存草稿后需要发布才进入推理热路径。</p>
        </div>
        <a-space>
          <a-button preIcon="ant-design:reload-outlined" :loading="pageLoading" @click="loadAll">刷新</a-button>
          <a-button preIcon="ant-design:check-circle-outlined" :loading="validating" @click="handleValidate">校验</a-button>
          <a-button type="primary" preIcon="ant-design:cloud-upload-outlined" :loading="publishing" @click="handlePublish">
            发布配置
          </a-button>
        </a-space>
      </section>

      <a-alert
        v-if="loadError"
        class="gateway-alert"
        type="warning"
        show-icon
        :message="loadError"
        closable
        @close="loadError = ''"
      />

      <section class="gateway-metrics" aria-label="模型网关配置概览">
        <div class="metric">
          <span>模型</span>
          <strong>{{ modelRows.length }}</strong>
          <small>{{ enabledModels }} 个启用</small>
        </div>
        <div class="metric">
          <span>渠道</span>
          <strong>{{ channelRows.length }}</strong>
          <small>{{ enabledChannels }} 个启用</small>
        </div>
        <div class="metric">
          <span>已配密钥</span>
          <strong>{{ channelWithCredential }}</strong>
          <small>仅展示脱敏值</small>
        </div>
        <div class="metric">
          <span>发布版本</span>
          <strong>{{ publishResult?.version || '-' }}</strong>
          <small>{{ publishResult?.checksum || '等待发布' }}</small>
        </div>
      </section>

      <section class="gateway-workspace">
        <a-tabs v-model:activeKey="activeTab">
          <a-tab-pane key="models" tab="模型">
            <div class="table-toolbar">
              <div>
                <h2>模型目录</h2>
                <span>网关对外暴露的模型名称和能力声明</span>
              </div>
              <a-button type="primary" preIcon="ant-design:plus-outlined" @click="openModelModal()">新增模型</a-button>
            </div>
            <a-table
              :columns="modelColumns"
              :data-source="modelRows"
              :loading="modelsLoading"
              :pagination="{ pageSize: 10, showSizeChanger: true }"
              row-key="id"
              size="middle"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'model'">
                  <div class="main-cell">
                    <strong>{{ record.displayName }}</strong>
                    <code>{{ record.publicName }}</code>
                  </div>
                </template>
                <template v-else-if="column.key === 'capabilities'">
                  <a-space wrap size="small">
                    <a-tag v-for="capability in record.capabilities || []" :key="capability" color="blue">
                      {{ capability }}
                    </a-tag>
                  </a-space>
                </template>
                <template v-else-if="column.key === 'limits'">
                  <div class="limit-cell">
                    <span>{{ record.contextWindow?.toLocaleString() || '-' }} ctx</span>
                    <small>{{ record.maxOutputTokens?.toLocaleString() || '-' }} out</small>
                  </div>
                </template>
                <template v-else-if="column.key === 'status'">
                  <a-tag :color="record.status === 'ENABLED' ? 'success' : 'default'">
                    {{ record.status === 'ENABLED' ? '启用' : '停用' }}
                  </a-tag>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-space>
                    <a-button size="small" type="link" @click="openModelModal(record)">编辑</a-button>
                    <a-popconfirm title="确认删除此模型？" @confirm="handleDeleteModel(record)">
                      <a-button size="small" danger type="link">删除</a-button>
                    </a-popconfirm>
                  </a-space>
                </template>
              </template>
            </a-table>
          </a-tab-pane>

          <a-tab-pane key="channels" tab="渠道">
            <div class="table-toolbar">
              <div>
                <h2>上游渠道</h2>
                <span>维护供应商地址、密钥、代理和超时参数</span>
              </div>
              <a-button type="primary" preIcon="ant-design:plus-outlined" @click="openChannelModal()">新增渠道</a-button>
            </div>
            <a-table
              :columns="channelColumns"
              :data-source="channelRows"
              :loading="channelsLoading"
              :pagination="{ pageSize: 10, showSizeChanger: true }"
              row-key="id"
              size="middle"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'channel'">
                  <div class="main-cell">
                    <strong>{{ record.name }}</strong>
                    <code>{{ record.baseUrl }}</code>
                  </div>
                </template>
                <template v-else-if="column.key === 'provider'">
                  <a-tag color="geekblue">{{ record.providerType }}</a-tag>
                </template>
                <template v-else-if="column.key === 'timeout'">
                  <div class="limit-cell">
                    <span>{{ record.connectTimeoutMs }} ms</span>
                    <small>{{ record.readTimeoutMs }} ms</small>
                  </div>
                </template>
                <template v-else-if="column.key === 'credential'">
                  <div class="credential-cell">
                    <span>{{ record.credential?.masked || '未配置' }}</span>
                    <small v-if="record.credential?.version">v{{ record.credential.version }}</small>
                  </div>
                </template>
                <template v-else-if="column.key === 'status'">
                  <a-tag :color="record.status === 'ENABLED' ? 'success' : 'default'">
                    {{ record.status === 'ENABLED' ? '启用' : '停用' }}
                  </a-tag>
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-space>
                    <a-button size="small" type="link" @click="openChannelModal(record)">编辑</a-button>
                    <a-popconfirm title="确认删除此渠道？" @confirm="handleDeleteChannel(record)">
                      <a-button size="small" danger type="link">删除</a-button>
                    </a-popconfirm>
                  </a-space>
                </template>
              </template>
            </a-table>
          </a-tab-pane>
        </a-tabs>
      </section>

      <a-modal
        v-model:open="modelModalOpen"
        :title="editingModelId ? '编辑模型' : '新增模型'"
        width="720px"
        :confirm-loading="modelSaving"
        @ok="handleSaveModel"
      >
        <a-form :model="modelForm" layout="vertical">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="公开模型名称" required>
                <a-input v-model:value="modelForm.publicName" placeholder="gpt-4o-mini" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="显示名称" required>
                <a-input v-model:value="modelForm.displayName" placeholder="GPT-4o mini" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="主类型" required>
                <a-select v-model:value="modelForm.primaryType" :options="modelTypeOptions" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="状态" required>
                <a-radio-group v-model:value="modelForm.status" button-style="solid">
                  <a-radio-button value="ENABLED">启用</a-radio-button>
                  <a-radio-button value="DISABLED">停用</a-radio-button>
                </a-radio-group>
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="能力">
                <a-input v-model:value="modelForm.capabilitiesText" placeholder="CHAT, VISION, JSON" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="标签">
                <a-input v-model:value="modelForm.tagsText" placeholder="default, fast" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="上下文 Token 上限" required>
                <a-input-number v-model:value="modelForm.contextWindow" :min="1" :max="10000000" class="full-input" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="输出 Token 上限" required>
                <a-input-number v-model:value="modelForm.maxOutputTokens" :min="1" :max="10000000" class="full-input" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="Tokenizer">
                <a-input v-model:value="modelForm.tokenizer" placeholder="cl100k_base" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="图标地址">
                <a-input v-model:value="modelForm.iconUrl" placeholder="https://..." />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="描述">
            <a-textarea v-model:value="modelForm.description" :rows="3" />
          </a-form-item>
        </a-form>
      </a-modal>

      <a-modal
        v-model:open="channelModalOpen"
        :title="editingChannelId ? '编辑渠道' : '新增渠道'"
        width="780px"
        :confirm-loading="channelSaving"
        @ok="handleSaveChannel"
      >
        <a-form :model="channelForm" layout="vertical">
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="渠道名称" required>
                <a-input v-model:value="channelForm.name" placeholder="OpenAI 兼容渠道" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="供应商" required>
                <a-select v-model:value="channelForm.providerType" :options="providerOptions" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="Base URL" required>
            <a-input v-model:value="channelForm.baseUrl" placeholder="https://api.openai.com/v1" />
          </a-form-item>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="区域">
                <a-input v-model:value="channelForm.region" placeholder="eastus / cn-beijing" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="状态" required>
                <a-radio-group v-model:value="channelForm.status" button-style="solid">
                  <a-radio-button value="ENABLED">启用</a-radio-button>
                  <a-radio-button value="DISABLED">停用</a-radio-button>
                </a-radio-group>
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="12">
              <a-form-item label="连接超时 ms" required>
                <a-input-number v-model:value="channelForm.connectTimeoutMs" :min="100" :max="120000" class="full-input" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="读取超时 ms" required>
                <a-input-number v-model:value="channelForm.readTimeoutMs" :min="100" :max="120000" class="full-input" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-row :gutter="16">
            <a-col :span="8">
              <a-form-item label="启用代理">
                <a-switch v-model:checked="channelForm.proxyEnabled" />
              </a-form-item>
            </a-col>
            <a-col :span="16">
              <a-form-item label="代理地址">
                <a-input v-model:value="channelForm.proxyUrl" :disabled="!channelForm.proxyEnabled" placeholder="http://127.0.0.1:7890" />
              </a-form-item>
            </a-col>
          </a-row>
          <a-form-item label="API Key">
            <a-input-password v-model:value="channelForm.secret" placeholder="留空表示不变" autocomplete="new-password" />
          </a-form-item>
          <a-form-item label="扩展配置 JSON">
            <a-textarea v-model:value="channelForm.configJson" :rows="5" placeholder="{}" />
          </a-form-item>
        </a-form>
      </a-modal>
    </div>
  </PageWrapper>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue';
  import { Modal, message } from 'ant-design-vue';
  import { PageWrapper } from '/@/components/Page';
  import {
    buildChannelPayload,
    buildModelPayload,
    createGatewayChannel,
    createGatewayModel,
    deleteGatewayChannel,
    deleteGatewayModel,
    joinTags,
    listGatewayChannels,
    listGatewayModels,
    publishGatewayConfig,
    updateGatewayChannel,
    updateGatewayModel,
    validateGatewayConfig,
    type ChannelFormState,
    type ChannelView,
    type ModelFormState,
    type ModelView,
    type PublishedConfig,
  } from './modelGateway.api';

  const modelTypeOptions = ['CHAT', 'EMBEDDING', 'RERANK', 'IMAGE', 'SPEECH', 'TRANSCRIPTION', 'MODERATION', 'REALTIME'].map((value) => ({
    label: value,
    value,
  }));

  const providerOptions = [
    'OPENAI',
    'AZURE_OPENAI',
    'ANTHROPIC',
    'GEMINI',
    'DEEPSEEK',
    'ALIBABA_BAILIAN',
    'ZHIPU',
    'BAIDU_QIANFAN',
    'VOLCENGINE_ARK',
    'TENCENT_HUNYUAN',
    'OLLAMA',
    'VLLM',
    'GPUSTACK',
    'OPENAI_COMPATIBLE',
  ].map((value) => ({ label: value, value }));

  const modelColumns = [
    { title: '模型', key: 'model', width: 260 },
    { title: '类型', dataIndex: 'primaryType', width: 130 },
    { title: '能力', key: 'capabilities' },
    { title: 'Token 上限', key: 'limits', width: 150 },
    { title: '状态', key: 'status', width: 100 },
    { title: '操作', key: 'action', width: 140, fixed: 'right' },
  ];

  const channelColumns = [
    { title: '渠道', key: 'channel', width: 330 },
    { title: '供应商', key: 'provider', width: 170 },
    { title: '超时', key: 'timeout', width: 140 },
    { title: '密钥', key: 'credential', width: 180 },
    { title: '状态', key: 'status', width: 100 },
    { title: '操作', key: 'action', width: 140, fixed: 'right' },
  ];

  const activeTab = ref('models');
  const modelRows = ref<ModelView[]>([]);
  const channelRows = ref<ChannelView[]>([]);
  const publishResult = ref<PublishedConfig | null>(null);
  const loadError = ref('');
  const modelsLoading = ref(false);
  const channelsLoading = ref(false);
  const validating = ref(false);
  const publishing = ref(false);
  const modelModalOpen = ref(false);
  const channelModalOpen = ref(false);
  const modelSaving = ref(false);
  const channelSaving = ref(false);
  const editingModelId = ref('');
  const editingChannelId = ref('');

  const modelForm = reactive<ModelFormState>(defaultModelForm());
  const channelForm = reactive<ChannelFormState>(defaultChannelForm());

  const pageLoading = computed(() => modelsLoading.value || channelsLoading.value);
  const enabledModels = computed(() => modelRows.value.filter((item) => item.status === 'ENABLED').length);
  const enabledChannels = computed(() => channelRows.value.filter((item) => item.status === 'ENABLED').length);
  const channelWithCredential = computed(() => channelRows.value.filter((item) => item.credential?.masked).length);

  onMounted(loadAll);

  function defaultModelForm(record?: ModelView): ModelFormState {
    return {
      publicName: record?.publicName || '',
      displayName: record?.displayName || '',
      iconUrl: record?.iconUrl || '',
      description: record?.description || '',
      tagsText: joinTags(record?.tags),
      primaryType: record?.primaryType || 'CHAT',
      capabilitiesText: joinTags(record?.capabilities) || 'CHAT',
      contextWindow: record?.contextWindow || 128000,
      maxOutputTokens: record?.maxOutputTokens || 4096,
      tokenizer: record?.tokenizer || '',
      status: record?.status || 'ENABLED',
    };
  }

  function defaultChannelForm(record?: ChannelView): ChannelFormState {
    return {
      providerType: record?.providerType || 'OPENAI_COMPATIBLE',
      name: record?.name || '',
      baseUrl: record?.baseUrl || '',
      region: record?.region || '',
      proxyEnabled: Boolean(record?.proxyEnabled),
      proxyUrl: record?.proxyUrl || '',
      connectTimeoutMs: record?.connectTimeoutMs || 10000,
      readTimeoutMs: record?.readTimeoutMs || 60000,
      configJson: record?.configJson || '{}',
      status: record?.status || 'ENABLED',
      secret: '',
    };
  }

  async function loadAll() {
    loadError.value = '';
    await Promise.all([loadModels(), loadChannels()]);
  }

  async function loadModels() {
    modelsLoading.value = true;
    try {
      modelRows.value = await listGatewayModels();
    } catch (error) {
      modelRows.value = [];
      loadError.value = readError(error, '模型列表加载失败');
    } finally {
      modelsLoading.value = false;
    }
  }

  async function loadChannels() {
    channelsLoading.value = true;
    try {
      channelRows.value = await listGatewayChannels();
    } catch (error) {
      channelRows.value = [];
      loadError.value = readError(error, '渠道列表加载失败');
    } finally {
      channelsLoading.value = false;
    }
  }

  function openModelModal(record?: ModelView) {
    editingModelId.value = record?.id || '';
    Object.assign(modelForm, defaultModelForm(record));
    modelModalOpen.value = true;
  }

  function openChannelModal(record?: ChannelView) {
    editingChannelId.value = record?.id || '';
    Object.assign(channelForm, defaultChannelForm(record));
    channelModalOpen.value = true;
  }

  async function handleSaveModel() {
    if (!modelForm.publicName?.trim() || !modelForm.displayName?.trim() || !modelForm.capabilitiesText?.trim()) {
      message.warning('请填写模型名称、显示名称和能力');
      return;
    }
    modelSaving.value = true;
    try {
      const payload = buildModelPayload(modelForm);
      if (editingModelId.value) {
        await updateGatewayModel(editingModelId.value, payload);
      } else {
        await createGatewayModel(payload);
      }
      message.success('模型已保存');
      modelModalOpen.value = false;
      await loadModels();
    } catch (error) {
      message.error(readError(error, '模型保存失败'));
    } finally {
      modelSaving.value = false;
    }
  }

  async function handleSaveChannel() {
    if (!channelForm.name?.trim() || !channelForm.baseUrl?.trim()) {
      message.warning('请填写渠道名称和 Base URL');
      return;
    }
    try {
      channelForm.configJson = normalizeJson(channelForm.configJson);
    } catch {
      message.error('扩展配置必须是合法 JSON');
      return;
    }
    channelSaving.value = true;
    try {
      const payload = buildChannelPayload(channelForm);
      if (editingChannelId.value) {
        await updateGatewayChannel(editingChannelId.value, payload);
      } else {
        await createGatewayChannel(payload);
      }
      message.success('渠道已保存');
      channelModalOpen.value = false;
      await loadChannels();
    } catch (error) {
      message.error(readError(error, '渠道保存失败'));
    } finally {
      channelSaving.value = false;
    }
  }

  function handleDeleteModel(record: ModelView) {
    if (!record.id) return;
    return deleteGatewayModel(record.id)
      .then(loadModels)
      .then(() => message.success('模型已删除'));
  }

  function handleDeleteChannel(record: ChannelView) {
    if (!record.id) return;
    return deleteGatewayChannel(record.id)
      .then(loadChannels)
      .then(() => message.success('渠道已删除'));
  }

  async function handleValidate() {
    validating.value = true;
    try {
      await validateGatewayConfig();
      message.success('配置校验通过');
    } catch (error) {
      message.error(readError(error, '配置校验失败'));
    } finally {
      validating.value = false;
    }
  }

  function handlePublish() {
    Modal.confirm({
      title: '发布当前模型网关配置？',
      content: '发布后，最新模型和渠道配置会切换到推理热路径。',
      okText: '确认发布',
      cancelText: '取消',
      async onOk() {
        publishing.value = true;
        try {
          publishResult.value = await publishGatewayConfig();
          message.success(`配置已发布，版本 ${publishResult.value.version}`);
        } catch (error) {
          message.error(readError(error, '配置发布失败'));
        } finally {
          publishing.value = false;
        }
      },
    });
  }

  function normalizeJson(value?: string) {
    const text = value?.trim() || '{}';
    return JSON.stringify(JSON.parse(text), null, 2);
  }

  function readError(error: unknown, fallback: string) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return fallback;
  }
</script>

<style scoped lang="less">
  .gateway-page {
    min-height: 100%;
    padding: 16px;
    color: #1f2937;
    background: #f5f7fb;
  }

  .gateway-header,
  .gateway-workspace,
  .metric {
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    background: #fff;
  }

  .gateway-header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    padding: 18px 20px;

    h1 {
      margin: 4px 0 3px;
      color: #111827;
      font-size: 22px;
      font-weight: 650;
      line-height: 1.25;
    }

    p {
      margin: 0;
      color: #667085;
      font-size: 13px;
      line-height: 1.6;
    }
  }

  .eyebrow {
    color: #4f46e5;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 11px;
    font-weight: 700;
  }

  .gateway-alert {
    margin-top: 12px;
  }

  .gateway-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 12px 0;
  }

  .metric {
    min-width: 0;
    padding: 14px 16px;

    span,
    small {
      display: block;
      overflow: hidden;
      color: #667085;
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    strong {
      display: block;
      margin: 5px 0 2px;
      color: #111827;
      font-size: 24px;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
      line-height: 1.1;
    }
  }

  .gateway-workspace {
    padding: 16px;
  }

  .table-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;

    h2 {
      margin: 0;
      color: #111827;
      font-size: 17px;
      font-weight: 650;
    }

    span {
      display: block;
      margin-top: 2px;
      color: #667085;
      font-size: 12px;
    }
  }

  .main-cell {
    min-width: 0;

    strong,
    code {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    strong {
      color: #1f2937;
      font-weight: 600;
    }

    code {
      margin-top: 2px;
      color: #667085;
      font-family: 'Cascadia Code', Consolas, monospace;
      font-size: 12px;
    }
  }

  .limit-cell,
  .credential-cell {
    span,
    small {
      display: block;
      font-variant-numeric: tabular-nums;
    }

    span {
      color: #344054;
    }

    small {
      color: #667085;
      font-size: 11px;
    }
  }

  .full-input {
    width: 100%;
  }

  :deep(.ant-tabs-nav) {
    margin-bottom: 16px;
  }

  :deep(.ant-table-thead > tr > th) {
    color: #667085;
    background: #f8fafc;
    font-size: 12px;
    font-weight: 600;
  }

  @media (max-width: 900px) {
    .gateway-header,
    .table-toolbar {
      align-items: flex-start;
      flex-direction: column;
    }

    .gateway-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 560px) {
    .gateway-page {
      padding: 10px;
    }

    .gateway-metrics {
      grid-template-columns: 1fr;
    }
  }
</style>
