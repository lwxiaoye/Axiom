<template>
  <BasicDrawer
    v-bind="$attrs"
    @register="registerDrawer"
    :title="drawerTitle"
    width="880px"
    showFooter
    destroyOnClose
    :closeFunc="handleBeforeClose"
  >
    <template #titleToolbar>
      <div class="save-indicator">
        <span class="save-dot"></span>
        {{ dirty ? '有未保存更改' : '配置已加载' }}
      </div>
    </template>

    <a-tabs v-model:activeKey="activeTab" class="route-tabs">
      <a-tab-pane key="basic" tab="基础信息">
        <a-form ref="formRef" layout="vertical" :model="formModel" :rules="rules">
          <section class="form-section">
            <div class="section-heading">
              <div>
                <h3>路由标识</h3>
                <p>用于系统检索和 APISIX 资源识别，保存后建议不要随意修改。</p>
              </div>
            </div>
            <a-row :gutter="20">
              <a-col :xs="24" :md="12">
                <a-form-item label="路由名称" name="name">
                  <a-input v-model:value="formModel.name" placeholder="例如：用户服务 API" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="路由 ID" name="routeId">
                  <a-input v-model:value="formModel.routeId" :disabled="isUpdate" placeholder="例如：user-service-route" />
                </a-form-item>
              </a-col>
            </a-row>
            <a-form-item label="说明">
              <a-textarea
                v-model:value="formModel.description"
                :rows="3"
                :maxlength="200"
                show-count
                placeholder="说明该路由的业务用途和维护边界"
              />
            </a-form-item>
          </section>

          <section class="form-section">
            <div class="section-heading">
              <div>
                <h3>匹配条件</h3>
                <p>请求需要同时满足 URI、Method 和 Host 条件；留空的条件不参与匹配。</p>
              </div>
            </div>
            <a-form-item label="请求路径" name="uris" extra="支持 APISIX 通配符，例如 /api/users/*">
              <a-select
                v-model:value="formModel.uris"
                mode="tags"
                :token-separators="[',']"
                placeholder="输入 URI 后按回车"
              />
            </a-form-item>
            <a-row :gutter="20">
              <a-col :xs="24" :md="12">
                <a-form-item label="请求方法">
                  <a-select
                    v-model:value="formModel.methods"
                    mode="multiple"
                    :options="httpMethodOptions"
                    placeholder="不选择表示允许全部方法"
                  />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="域名限制">
                  <a-select
                    v-model:value="formModel.hosts"
                    mode="tags"
                    :token-separators="[',']"
                    placeholder="例如 api.example.com"
                  />
                </a-form-item>
              </a-col>
            </a-row>
            <a-row :gutter="20">
              <a-col :xs="24" :md="12">
                <a-form-item label="优先级" extra="数值越大，匹配优先级越高">
                  <a-input-number v-model:value="formModel.priority" :min="-1000" :max="1000" class="full-width" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="运行状态">
                  <div class="switch-field">
                    <a-switch v-model:checked="routeEnabled" />
                    <span>{{ routeEnabled ? '启用' : '停用' }}</span>
                  </div>
                </a-form-item>
              </a-col>
            </a-row>
          </section>
        </a-form>
      </a-tab-pane>

      <a-tab-pane key="upstream" tab="上游服务">
        <section class="form-section">
          <div class="section-heading">
            <div>
              <h3>选择上游</h3>
              <p>推荐绑定独立 Upstream，便于节点复用、健康检查和统一维护。</p>
            </div>
            <a-tag color="blue">推荐</a-tag>
          </div>
          <a-radio-group v-model:value="upstreamMode" class="mode-switch">
            <a-radio-button value="reference">引用已有 Upstream</a-radio-button>
            <a-radio-button value="inline">内联节点</a-radio-button>
          </a-radio-group>

          <div v-if="upstreamMode === 'reference'" class="mode-panel">
            <a-form layout="vertical">
              <a-form-item label="Upstream">
                <a-select
                  v-model:value="formModel.upstreamId"
                  show-search
                  :options="upstreamOptions"
                  :loading="upstreamLoading"
                  placeholder="选择一个上游服务"
                />
              </a-form-item>
            </a-form>
          </div>

          <div v-else class="mode-panel">
            <div v-for="(node, index) in formModel.nodes" :key="index" class="node-row">
              <a-input v-model:value="node.host" placeholder="节点地址，如 10.0.0.8" />
              <a-input-number v-model:value="node.port" :min="1" :max="65535" placeholder="端口" />
              <a-input-number v-model:value="node.weight" :min="0" :max="1000" placeholder="权重" />
              <a-input-number v-model:value="node.priority" :min="-1000" :max="1000" placeholder="优先级" />
              <a-button
                type="text"
                danger
                aria-label="删除上游节点"
                @click="removeNode(index)"
              >
                <Icon icon="ant-design:delete-outlined" />
              </a-button>
            </div>
            <a-button type="dashed" block preIcon="ant-design:plus-outlined" @click="addNode">添加节点</a-button>
            <a-row :gutter="12" class="upstream-options">
              <a-col :span="6">
                <a-form-item label="负载均衡类型">
                  <a-select v-model:value="formModel.upstreamType" :options="upstreamTypeOptions" />
                </a-form-item>
              </a-col>
              <a-col :span="6">
                <a-form-item label="协议">
                  <a-select v-model:value="formModel.upstreamScheme" :options="upstreamSchemeOptions" />
                </a-form-item>
              </a-col>
              <a-col :span="6">
                <a-form-item label="Hash On">
                  <a-input v-model:value="formModel.upstreamHashOn" />
                </a-form-item>
              </a-col>
              <a-col :span="6">
                <a-form-item label="Host 传递">
                  <a-select v-model:value="formModel.upstreamPassHost" :options="passHostOptions" />
                </a-form-item>
              </a-col>
            </a-row>
          </div>
        </section>

        <section class="form-section">
          <div class="section-heading">
            <div>
              <h3>连接策略</h3>
              <p>超时时间以秒为单位，建议按业务调用链实际耗时设置。</p>
            </div>
          </div>
          <a-row :gutter="16">
            <a-col :span="8">
              <a-form-item label="连接超时">
                <a-input-number v-model:value="formModel.timeout.connect" :min="0.1" :step="0.1" class="full-width" />
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item label="发送超时">
                <a-input-number v-model:value="formModel.timeout.send" :min="0.1" :step="0.1" class="full-width" />
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item label="读取超时">
                <a-input-number v-model:value="formModel.timeout.read" :min="0.1" :step="0.1" class="full-width" />
              </a-form-item>
            </a-col>
          </a-row>
          <div class="setting-row">
            <div>
              <strong>WebSocket</strong>
              <span>允许客户端将连接升级为 WebSocket</span>
            </div>
            <a-switch v-model:checked="formModel.websocket" />
          </div>
        </section>
      </a-tab-pane>

      <a-tab-pane key="plugins">
        <template #tab>
          插件配置
          <a-badge :count="enabledPluginCount" :number-style="{ backgroundColor: '#1677ff' }" />
        </template>
        <section class="form-section">
          <div class="section-heading">
            <div>
              <h3>插件链</h3>
              <p>插件配置会在保存前进行结构校验。敏感信息请使用 APISIX Secret 引用。</p>
            </div>
            <a-select
              class="plugin-picker"
              placeholder="添加插件"
              :options="availablePluginOptions"
              @select="addPlugin"
            />
          </div>

          <a-empty v-if="formModel.plugins.length === 0" description="尚未配置插件">
            <a-button type="primary" ghost @click="addPlugin('prometheus')">添加监控插件</a-button>
          </a-empty>

          <div v-for="(plugin, index) in formModel.plugins" :key="plugin.name" class="plugin-card">
            <div class="plugin-card__header">
              <div class="plugin-identity">
                <span class="plugin-mark">{{ plugin.name.slice(0, 2).toUpperCase() }}</span>
                <div>
                  <strong>{{ plugin.name }}</strong>
                  <span>{{ plugin.enabled ? '将在请求链中生效' : '当前已暂停' }}</span>
                </div>
              </div>
              <div class="plugin-actions">
                <a-switch v-model:checked="plugin.enabled" size="small" />
                <a-button type="text" danger aria-label="删除插件" @click="removePlugin(index)">
                  <Icon icon="ant-design:delete-outlined" />
                </a-button>
              </div>
            </div>
            <a-textarea
              :value="formatPluginConfig(plugin.config)"
              :rows="5"
              class="code-input"
              spellcheck="false"
              @blur="updatePluginConfig(plugin, $event)"
            />
          </div>
        </section>
      </a-tab-pane>

      <a-tab-pane key="advanced" tab="高级配置">
        <section class="form-section">
          <div class="section-heading">
            <div>
              <h3>资源标签</h3>
              <p>标签用于归属识别和配置治理，系统会自动补充 managed-by 标签。</p>
            </div>
          </div>
          <div v-for="(label, index) in labelEntries" :key="index" class="label-row">
            <a-input v-model:value="label.key" placeholder="标签名" />
            <a-input v-model:value="label.value" placeholder="标签值" />
            <a-button type="text" danger aria-label="删除标签" @click="removeLabel(index)">
              <Icon icon="ant-design:delete-outlined" />
            </a-button>
          </div>
          <a-button type="dashed" block preIcon="ant-design:plus-outlined" @click="addLabel">添加标签</a-button>
        </section>
      </a-tab-pane>

      <a-tab-pane key="json" tab="JSON">
        <section class="form-section json-section">
          <div class="section-heading">
            <div>
              <h3>APISIX Route JSON</h3>
              <p>适合配置 vars 等高级能力。应用 JSON 后会覆盖当前可视化表单内容。</p>
            </div>
            <a-button @click="refreshJson">从表单刷新</a-button>
          </div>
          <a-textarea v-model:value="rawJson" :rows="24" class="json-editor" spellcheck="false" />
          <div class="json-actions">
            <span v-if="jsonError" class="json-error" role="alert">{{ jsonError }}</span>
            <span v-else class="json-valid"><Icon icon="ant-design:check-circle-outlined" /> JSON 格式正确</span>
            <a-button type="primary" ghost @click="applyJson">应用 JSON</a-button>
          </div>
        </section>
      </a-tab-pane>
    </a-tabs>

    <template #footer>
      <div class="drawer-footer">
        <a-button :loading="validating" preIcon="ant-design:safety-certificate-outlined" @click="handleValidate">
          校验配置
        </a-button>
        <div class="drawer-footer__actions">
          <a-button @click="handleCancel">取消</a-button>
          <a-button type="primary" :loading="saving" @click="handleSave">保存配置</a-button>
        </div>
      </div>
    </template>
  </BasicDrawer>
</template>

<script setup lang="ts">
  import { computed, reactive, ref, watch } from 'vue';
  import { Modal, message } from 'ant-design-vue';
  import { BasicDrawer, useDrawerInner } from '/@/components/Drawer';
  import {
    getApisixRoute,
    getApisixUpstreamOptions,
    saveApisixRoute,
    validateApisixRoute,
  } from './apisix.api';
  import { httpMethodOptions, pluginOptions } from './apisix.data';
  import type { ApisixPluginConfig, ApisixRoute } from './apisix.types';

  const emit = defineEmits(['register', 'success']);
  const formRef = ref();
  const activeTab = ref('basic');
  const isUpdate = ref(false);
  const dirty = ref(false);
  const saving = ref(false);
  const validating = ref(false);
  const upstreamLoading = ref(false);
  const upstreamOptions = ref<Array<{ label: string; value: string }>>([]);
  const upstreamMode = ref<'reference' | 'inline'>('reference');
  const upstreamTypeOptions = ['roundrobin', 'chash', 'least_conn', 'ewma'].map((value) => ({ label: value, value }));
  const upstreamSchemeOptions = ['http', 'https', 'grpc', 'grpcs'].map((value) => ({ label: value, value }));
  const passHostOptions = ['pass', 'node', 'rewrite'].map((value) => ({ label: value, value }));
  const rawJson = ref('');
  const jsonError = ref('');
  const labelEntries = ref<Array<{ key: string; value: string }>>([]);

  const createDefaultRoute = (): ApisixRoute => ({
    routeId: '',
    name: '',
    description: '',
    uris: [],
    methods: [],
    hosts: [],
    priority: 0,
    status: 1,
    upstreamId: undefined,
    upstreamType: 'roundrobin',
    upstreamScheme: 'http',
    upstreamHashOn: 'vars',
    upstreamPassHost: 'pass',
    nodes: [],
    plugins: [],
    labels: { 'managed-by': 'axiom' },
    timeout: { connect: 6, send: 6, read: 6 },
    websocket: false,
  });

  const formModel = reactive<ApisixRoute>(createDefaultRoute());

  const rules = {
    name: [{ required: true, message: '请输入路由名称', trigger: 'blur' }],
    routeId: [
      { required: true, message: '请输入路由 ID', trigger: 'blur' },
      { pattern: /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/, message: '仅支持字母、数字、点、下划线和中划线' },
    ],
    uris: [{ required: true, type: 'array', min: 1, message: '至少配置一个请求路径', trigger: 'change' }],
  };

  const drawerTitle = computed(() => (isUpdate.value ? `编辑路由 · ${formModel.name || formModel.routeId}` : '新建 APISIX 路由'));
  const routeEnabled = computed({
    get: () => formModel.status === 1,
    set: (value: boolean) => {
      formModel.status = value ? 1 : 0;
    },
  });
  const enabledPluginCount = computed(() => formModel.plugins.filter((item) => item.enabled).length);
  const availablePluginOptions = computed(() =>
    pluginOptions.filter((option) => !formModel.plugins.some((plugin) => plugin.name === option.value))
  );

  const [registerDrawer, { setDrawerProps, closeDrawer }] = useDrawerInner(async (data) => {
    setDrawerProps({ confirmLoading: false });
    activeTab.value = 'basic';
    isUpdate.value = Boolean(data?.isUpdate);
    Object.assign(formModel, createDefaultRoute());

    if (data?.record) {
      Object.assign(formModel, data.record);
    }
    if (data?.record?.id) {
      try {
        const detail = await getApisixRoute(data.record.id);
        Object.assign(formModel, detail);
      } catch {
        // 列表数据足以支撑编辑，详情加载失败时保留当前内容。
      }
    }

    upstreamMode.value = formModel.upstreamId ? 'reference' : 'inline';
    labelEntries.value = Object.entries(formModel.labels || {}).map(([key, value]) => ({ key, value }));
    await loadUpstreams();
    refreshJson();
    dirty.value = false;
  });

  watch(
    formModel,
    () => {
      dirty.value = true;
    },
    { deep: true, flush: 'sync' }
  );

  async function loadUpstreams() {
    upstreamLoading.value = true;
    try {
      upstreamOptions.value = await getApisixUpstreamOptions();
    } catch {
      upstreamOptions.value = [];
    } finally {
      upstreamLoading.value = false;
    }
  }

  function syncLabels() {
    formModel.labels = labelEntries.value.reduce<Record<string, string>>((result, item) => {
      if (item.key.trim()) result[item.key.trim()] = item.value.trim();
      return result;
    }, {});
    formModel.labels['managed-by'] = 'axiom';
  }

  function addNode() {
    formModel.nodes ||= [];
    formModel.nodes.push({ host: '', port: 80, weight: 100, priority: 0 });
  }

  function removeNode(index: number) {
    formModel.nodes?.splice(index, 1);
  }

  function addPlugin(name: string) {
    if (!name || formModel.plugins.some((plugin) => plugin.name === name)) return;
    formModel.plugins.push({ name, enabled: true, config: {} });
  }

  function removePlugin(index: number) {
    formModel.plugins.splice(index, 1);
  }

  function formatPluginConfig(config: Record<string, unknown>) {
    return JSON.stringify(config || {}, null, 2);
  }

  function updatePluginConfig(plugin: ApisixPluginConfig, event: FocusEvent) {
    const value = (event.target as HTMLTextAreaElement).value;
    try {
      plugin.config = JSON.parse(value || '{}');
    } catch {
      message.error(`${plugin.name} 的插件配置不是有效 JSON`);
    }
  }

  function addLabel() {
    labelEntries.value.push({ key: '', value: '' });
  }

  function removeLabel(index: number) {
    labelEntries.value.splice(index, 1);
  }

  function buildPayload() {
    syncLabels();
    if (upstreamMode.value === 'reference') {
      formModel.nodes = [];
    } else {
      formModel.upstreamId = undefined;
    }
    return JSON.parse(JSON.stringify(formModel)) as ApisixRoute;
  }

  function refreshJson() {
    rawJson.value = JSON.stringify(buildPayload(), null, 2);
    jsonError.value = '';
  }

  function applyJson() {
    try {
      const parsed = JSON.parse(rawJson.value) as ApisixRoute;
      Object.assign(formModel, parsed);
      labelEntries.value = Object.entries(formModel.labels || {}).map(([key, value]) => ({ key, value }));
      upstreamMode.value = formModel.upstreamId ? 'reference' : 'inline';
      jsonError.value = '';
      message.success('JSON 已应用到可视化表单');
    } catch (error) {
      jsonError.value = error instanceof Error ? error.message : 'JSON 格式错误';
    }
  }

  async function validateLocalForm() {
    activeTab.value = 'basic';
    await formRef.value?.validate();
    if (upstreamMode.value === 'reference' && !formModel.upstreamId) {
      activeTab.value = 'upstream';
      throw new Error('请选择一个 Upstream');
    }
    if (upstreamMode.value === 'inline' && !formModel.nodes?.length) {
      activeTab.value = 'upstream';
      throw new Error('至少配置一个上游节点');
    }
  }

  async function handleValidate() {
    validating.value = true;
    try {
      await validateLocalForm();
      const result = await validateApisixRoute(buildPayload());
      if (result.valid) {
        message.success('配置校验通过');
      } else {
        Modal.warning({ title: '配置需要调整', content: result.messages.join('；') });
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '请完善必填配置');
    } finally {
      validating.value = false;
    }
  }

  async function persistRoute() {
    await validateLocalForm();
    const saved = await saveApisixRoute(buildPayload());
    Object.assign(formModel, saved);
    dirty.value = false;
    return saved;
  }

  async function handleSave() {
    saving.value = true;
    try {
      await persistRoute();
      message.success('路由配置已写入 APISIX');
      emit('success');
      closeDrawer();
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败，请检查配置');
    } finally {
      saving.value = false;
    }
  }
  async function handleBeforeClose() {
    if (!dirty.value) return true;
    return new Promise<boolean>((resolve) => {
      Modal.confirm({
        title: '放弃未保存的更改？',
        content: '关闭后，本次修改将不会保留。',
        okText: '放弃更改',
        okType: 'danger',
        cancelText: '继续编辑',
        onOk: () => resolve(true),
        onCancel: () => resolve(false),
      });
    });
  }

  async function handleCancel() {
    if (await handleBeforeClose()) {
      closeDrawer();
    }
  }
</script>

<style scoped lang="less">
  .save-indicator {
    display: flex;
    align-items: center;
    gap: 8px;
    color: rgba(0, 0, 0, 0.58);
    font-size: 13px;
  }

  .save-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #faad14;
    box-shadow: 0 0 0 3px rgba(250, 173, 20, 0.14);
  }


  .drawer-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 0 4px;
  }

  .drawer-footer__actions {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .route-tabs {
    :deep(.ant-tabs-nav) {
      margin-bottom: 20px;
    }
  }

  .form-section {
    padding: 20px;
    margin-bottom: 16px;
    border: 1px solid #edf0f5;
    border-radius: 10px;
    background: #fff;
  }

  .section-heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
    margin-bottom: 20px;

    h3 {
      margin: 0 0 4px;
      color: #172033;
      font-size: 15px;
      font-weight: 600;
    }

    p {
      margin: 0;
      color: #7b8496;
      font-size: 13px;
      line-height: 1.6;
    }
  }

  .full-width {
    width: 100%;
  }

  .switch-field {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 32px;
  }

  .mode-switch {
    margin-bottom: 18px;
  }

  .mode-panel {
    padding: 18px;
    border-radius: 8px;
    background: #f7f9fc;
  }

  .node-row {
    display: grid;
    grid-template-columns: minmax(200px, 1fr) 110px 110px 110px 40px;
    gap: 10px;
    margin-bottom: 10px;
  }

  .upstream-options {
    margin-top: 18px;
  }

  .setting-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px;
    border-radius: 8px;
    background: #f7f9fc;

    strong,
    span {
      display: block;
    }

    span {
      margin-top: 3px;
      color: #7b8496;
      font-size: 12px;
    }
  }

  .plugin-picker {
    width: 210px;
  }

  .plugin-card {
    overflow: hidden;
    margin-bottom: 14px;
    border: 1px solid #e7ebf2;
    border-radius: 10px;
    transition: border-color 180ms ease, box-shadow 180ms ease;

    &:focus-within {
      border-color: #91caff;
      box-shadow: none;
    }
  }

  .plugin-card__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 13px 14px;
    border-bottom: 1px solid #edf0f5;
    background: #fafbfd;
  }

  .plugin-identity,
  .plugin-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .plugin-identity {
    strong,
    span {
      display: block;
    }

    span {
      margin-top: 2px;
      color: #8b94a5;
      font-size: 12px;
    }
  }

  .plugin-mark {
    display: grid;
    width: 34px;
    height: 34px;
    place-items: center;
    border-radius: 8px;
    color: #0958d9;
    background: #e6f4ff;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
  }

  .code-input,
  .json-editor {
    border: 0;
    border-radius: 0;
    color: #d8dee9;
    background: #182231;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 13px;
    line-height: 1.65;

    &:focus {
      box-shadow: none;
    }
  }

  .code-input {
    color: #f8fafc;
    caret-color: #ffffff;

    &.ant-input {
      background-color: #182231 !important;
    }

    &::selection {
      color: #ffffff;
      background: #315f9d;
    }
  }

  .label-row {
    display: grid;
    grid-template-columns: 1fr 1fr 40px;
    gap: 10px;
    margin-bottom: 10px;
  }

  .json-section {
    padding-bottom: 14px;
  }

  .json-editor {
    border-radius: 8px;
    color: #f8fafc;
    caret-color: #ffffff;
    resize: vertical;

    &.ant-input {
      background-color: #182231 !important;
    }

    &::selection {
      color: #ffffff;
      background: #315f9d;
    }
  }

  .json-actions {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 12px;
  }

  .json-valid {
    display: flex;
    align-items: center;
    gap: 6px;
    color: #389e0d;
  }

  .json-error {
    color: #cf1322;
  }

  @media (max-width: 768px) {
    .form-section {
      padding: 16px;
    }

    .section-heading {
      flex-direction: column;
    }

    .plugin-picker {
      width: 100%;
    }

    .node-row {
      grid-template-columns: 1fr 1fr;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .plugin-card {
      transition: none;
    }
  }
</style>
