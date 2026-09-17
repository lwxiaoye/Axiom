<template>
  <a-modal
    v-model:open="open"
    :width="'min(1280px, calc(100vw - 32px))'"
    :footer="null"
    :closable="false"
    :mask-closable="false"
    wrap-class-name="http-config-modal"
    destroy-on-close
  >
    <div class="http-workbench">
      <header class="workbench-head">
        <span class="head-icon"><GlobalOutlined /></span>
        <div>
          <h2>配置 HTTP 请求</h2>
          <p>配置请求地址、参数与鉴权信息，测试并保存可供 Agent 调用的接口。</p>
        </div>
        <button type="button" class="close-button" aria-label="关闭" @click="open = false"><CloseOutlined /></button>
      </header>

      <section class="basic-card">
        <div class="section-title"><i></i><strong>基本信息</strong></div>
        <div class="basic-grid">
          <div class="field-block">
            <label>服务地址（baseUrl）<em>*</em></label>
            <p>调用时以此为前缀拼接接口路径。</p>
            <a-input v-model:value="config.baseUrl" size="large" placeholder="https://api.example.com">
              <template #prefix><GlobalOutlined class="muted-icon" /></template>
            </a-input>
          </div>
          <div class="field-block">
            <label>鉴权 Header <small>可选</small></label>
            <p>值仅保存于服务端，调用时附加到每个请求。</p>
            <div class="auth-list">
              <div v-for="(row, index) in config.headers" :key="index" class="auth-row">
                <a-input v-model:value="row.key" placeholder="Authorization" />
                <a-input-password v-model:value="row.value" placeholder="Bearer {{ token }}" />
                <a-button type="text" danger aria-label="删除 Header" @click="config.headers.splice(index, 1)"><DeleteOutlined /></a-button>
              </div>
              <a-button class="add-wide" @click="config.headers.push({ key: '', value: '' })"><PlusOutlined /> 添加 Header</a-button>
            </div>
          </div>
        </div>
      </section>

      <main class="editor-shell">
        <aside class="endpoint-rail">
          <div class="rail-head">
            <div><strong>接口清单</strong><p>共 {{ config.toolList.length }} 个接口</p></div>
            <a-button type="primary" ghost @click="addTool"><PlusOutlined /> 添加接口</a-button>
          </div>
          <div class="endpoint-search"><SearchOutlined /><input v-model="keyword" placeholder="搜索接口名称或路径" /></div>
          <div class="endpoint-list">
            <button
              v-for="item in filteredTools"
              :key="item.index"
              type="button"
              :class="['endpoint-item', { active: selectedIndex === item.index }]"
              @click="selectedIndex = item.index"
            >
              <span :class="['method-tag', item.tool.method.toLowerCase()]">{{ item.tool.method }}</span>
              <span class="endpoint-copy"><strong>{{ item.tool.name || '未命名接口' }}</strong><small>{{ item.tool.path || '/path' }}</small></span>
              <a-dropdown :trigger="['click']">
                <span class="endpoint-more" aria-label="接口操作" @click.stop><EllipsisOutlined /></span>
                <template #overlay><a-menu><a-menu-item danger @click="removeTool(item.index)"><DeleteOutlined /> 删除接口</a-menu-item></a-menu></template>
              </a-dropdown>
            </button>
            <a-empty v-if="!filteredTools.length" :image="simpleImage" description="没有匹配的接口" />
          </div>
        </aside>

        <section class="endpoint-detail">
          <template v-if="currentTool">
            <div class="detail-head">
              <div><strong>接口详情</strong><p>描述越清楚，模型越容易正确选择并调用。</p></div>
              <a-button @click="importVisible = true"><ImportOutlined /> 从 OpenAPI 导入</a-button>
            </div>

            <div class="operation-grid">
              <div class="field-block compact method-field"><label>请求方法</label><a-select v-model:value="currentTool.method" :options="methodOptions" /></div>
              <div class="field-block compact"><label>工具名 <em>*</em></label><a-input v-model:value="currentTool.name" placeholder="get_user" /></div>
              <div class="field-block compact path-field"><label>接口路径 <em>*</em></label><a-input v-model:value="currentTool.path" placeholder="/user/{userId}" /></div>
            </div>
            <div class="field-block compact description-field"><label>用途说明</label><a-input v-model:value="currentTool.description" placeholder="什么场景下调用，返回什么信息" /></div>

            <div v-if="currentIssues.length" class="validation-box">
              <ExclamationCircleOutlined />
              <span>{{ currentIssues[0].message }}</span>
            </div>

            <section v-for="section in paramSections" :key="section.key" class="param-section">
              <div class="param-head">
                <div><strong>{{ section.title }}</strong><small>{{ section.tip }}</small></div>
                <a-button type="link" @click="addParam(section.key)"><PlusOutlined /> 添加{{ section.action }}</a-button>
              </div>
              <div v-if="currentTool[section.key].length" class="param-table">
                <div class="param-columns"><span>参数名</span><span>类型</span><span>必填</span><span>说明（给模型看）</span><span></span></div>
                <div v-for="(param, pIndex) in currentTool[section.key]" :key="pIndex" class="param-row">
                  <a-input v-model:value="param.key" placeholder="参数名" />
                  <a-select v-model:value="param.type" :options="section.key === 'bodyParams' ? bodyTypeOptions : typeOptions" />
                  <a-checkbox v-model:checked="param.required" aria-label="是否必填" />
                  <a-input v-model:value="param.description" placeholder="说明该参数的含义" />
                  <a-button type="text" danger aria-label="删除参数" @click="currentTool[section.key].splice(pIndex, 1)"><DeleteOutlined /></a-button>
                </div>
              </div>
              <button v-else type="button" class="empty-param" @click="addParam(section.key)"><PlusOutlined /> 添加第一个{{ section.action }}</button>
            </section>
          </template>
          <a-empty v-else description="请先添加一个接口"><a-button type="primary" @click="addTool">添加接口</a-button></a-empty>
        </section>
      </main>

      <footer class="workbench-footer">
        <a-button size="large" :disabled="!currentTool" @click="openTest"><PlayCircleOutlined /> 测试请求</a-button>
        <span></span>
        <a-button size="large" @click="open = false">取消</a-button>
        <a-button type="primary" size="large" :loading="saving" @click="save">保存配置</a-button>
      </footer>
    </div>

    <a-modal v-model:open="importVisible" title="从 OpenAPI Schema 导入" :width="620" ok-text="导入" @ok="importOpenApi">
      <p class="import-tip">粘贴 OpenAPI 3.x JSON，将自动识别路径、查询、Header、JSON Body 与 multipart 文件参数。</p>
      <a-textarea v-model:value="openApiText" :rows="14" placeholder='{"openapi":"3.0.0","paths":{}}' />
    </a-modal>
    <HttpToolTestDrawer ref="testDrawerRef" />
  </a-modal>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Empty, message } from 'ant-design-vue';
import {
  CloseOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  ExclamationCircleOutlined,
  GlobalOutlined,
  ImportOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import { saveWorkflowAppConfig, type AiWorkflowApp } from '../../workflow/api/workflow.api';
import HttpToolTestDrawer from './HttpToolTestDrawer.vue';
import {
  createEmptyConfig,
  createEmptyTool,
  importOpenApiDocument,
  parseHttpToolSetConfig,
  serializeHttpToolSetConfig,
  validateHttpToolSetConfig,
  type ParamRow,
} from './httpToolConfig';

type ParamListKey = 'pathParams' | 'queryParams' | 'headerParams' | 'bodyParams';

const emit = defineEmits<{ (e: 'success'): void }>();
const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE;
const open = ref(false);
const saving = ref(false);
const importVisible = ref(false);
const openApiText = ref('');
const keyword = ref('');
const selectedIndex = ref(0);
const record = ref<Partial<AiWorkflowApp>>({});
const config = ref(createEmptyConfig());
const testDrawerRef = ref<InstanceType<typeof HttpToolTestDrawer> | null>(null);

const methodOptions = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }));
const typeOptions = ['string', 'number', 'integer', 'boolean', 'array', 'object'].map((value) => ({ value, label: value }));
const bodyTypeOptions = [...typeOptions, { value: 'file', label: 'file（文件）' }];
const paramSections: Array<{ key: ParamListKey; title: string; action: string; tip: string }> = [
  { key: 'pathParams', title: '路径参数', action: '参数', tip: '替换路径中的 {param}、{{param}} 或 ${param}' },
  { key: 'queryParams', title: '查询参数', action: '参数', tip: '编码到 URL 查询字符串' },
  { key: 'headerParams', title: '请求 Header', action: 'Header', tip: '当前接口的动态 Header，可覆盖同名鉴权 Header' },
  { key: 'bodyParams', title: '请求体（Body）', action: '字段', tip: '包含 file 时自动使用 multipart/form-data，否则发送 JSON' },
];

const currentTool = computed(() => config.value.toolList[selectedIndex.value] || null);
const filteredTools = computed(() => {
  const key = keyword.value.trim().toLowerCase();
  return config.value.toolList
    .map((tool, index) => ({ tool, index }))
    .filter(({ tool }) => !key || `${tool.name} ${tool.path}`.toLowerCase().includes(key));
});
const currentIssues = computed(() => validateHttpToolSetConfig(config.value).issues.filter((issue) => issue.toolIndex === selectedIndex.value));

function init(app: Partial<AiWorkflowApp>) {
  record.value = app;
  let parsed: any = {};
  try { parsed = JSON.parse(app.configJson || '{}'); } catch { parsed = {}; }
  config.value = parseHttpToolSetConfig(parsed);
  if (!config.value.toolList.length) config.value.toolList.push(createEmptyTool());
  selectedIndex.value = 0;
  keyword.value = '';
  open.value = true;
}

function addTool() {
  config.value.toolList.push(createEmptyTool());
  selectedIndex.value = config.value.toolList.length - 1;
}

function removeTool(index: number) {
  config.value.toolList.splice(index, 1);
  if (selectedIndex.value >= config.value.toolList.length) selectedIndex.value = Math.max(0, config.value.toolList.length - 1);
}

function addParam(key: ParamListKey) {
  const row: ParamRow = { key: '', type: 'string', required: key === 'pathParams', description: '' };
  currentTool.value?.[key].push(row);
}

function importOpenApi() {
  try {
    const result = importOpenApiDocument(JSON.parse(openApiText.value));
    if (!result.tools.length) return message.warning('未解析到有效接口');
    if (!config.value.baseUrl && result.baseUrl) config.value.baseUrl = result.baseUrl;
    config.value.toolList.push(...result.tools);
    selectedIndex.value = config.value.toolList.length - result.tools.length;
    importVisible.value = false;
    openApiText.value = '';
    message.success(`已导入 ${result.tools.length} 个接口`);
  } catch { message.error('OpenAPI JSON 解析失败'); }
}

function openTest() {
  if (!currentTool.value) return;
  const validation = validateHttpToolSetConfig(config.value);
  const blocking = validation.issues.find((issue) => issue.code === 'base-url' || issue.toolIndex === selectedIndex.value);
  if (blocking) return message.warning(blocking.message);
  testDrawerRef.value?.open(currentTool.value, config.value);
}

async function save() {
  const validation = validateHttpToolSetConfig(config.value);
  if (!validation.valid) {
    const issue = validation.issues[0];
    if (issue.toolIndex !== undefined) selectedIndex.value = issue.toolIndex;
    message.warning(issue.message);
    return;
  }
  saving.value = true;
  try {
    await saveWorkflowAppConfig({
      id: record.value.id,
      appInfoId: record.value.appInfoId,
      aiAppType: record.value.aiAppType,
      configJson: JSON.stringify(serializeHttpToolSetConfig(config.value)),
    });
    message.success('HTTP 工具配置已保存');
    open.value = false;
    emit('success');
  } catch { message.error('保存失败'); }
  finally { saving.value = false; }
}

defineExpose({ init });
</script>

<style scoped lang="less">
.http-workbench { display: flex; flex-direction: column; height: min(880px, calc(100vh - 48px)); color: #17233d; background: #f7f9fc; }
.workbench-head { display: flex; align-items: center; gap: 14px; padding: 20px 24px 14px; background: #fff; }
.head-icon { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 50%; color: #1677ff; background: #eaf3ff; font-size: 23px; }
.workbench-head h2 { margin: 0 0 3px; font-size: 22px; font-weight: 700; letter-spacing: -.02em; }
.workbench-head p, .rail-head p, .detail-head p, .field-block p { margin: 0; color: #8a94a6; font-size: 12px; line-height: 1.6; }
.close-button { margin-left: auto; width: 44px; height: 44px; border: 0; color: #667085; background: transparent; cursor: pointer; font-size: 18px; }
.close-button:focus-visible, .endpoint-item:focus-visible, .empty-param:focus-visible { outline: 2px solid #1677ff; outline-offset: 2px; }
.basic-card { margin: 0 18px 16px; padding: 14px 18px 16px; border: 1px solid #e1e6ef; border-radius: 12px; background: #fff; box-shadow: 0 3px 12px rgba(15, 23, 42, .035); }
.section-title { display: flex; align-items: center; gap: 9px; margin-bottom: 12px; }
.section-title i { width: 3px; height: 17px; border-radius: 3px; background: #1677ff; }
.basic-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.field-block label { display: block; margin-bottom: 2px; font-size: 13px; font-weight: 650; }
.field-block label em { margin-left: 4px; color: #ef4444; font-style: normal; }
.field-block label small { color: #98a2b3; font-weight: 400; }
.field-block p { margin-bottom: 8px; }
.muted-icon { color: #98a2b3; }
.auth-list { display: grid; gap: 7px; }
.auth-row { display: grid; grid-template-columns: .72fr 1fr 34px; gap: 7px; }
.add-wide { width: 100%; border-style: dashed; color: #1677ff; }
.editor-shell { display: grid; grid-template-columns: 360px 1fr; min-height: 0; flex: 1; margin: 0 18px 16px; border: 1px solid #e1e6ef; border-radius: 12px; overflow: hidden; background: #fff; }
.endpoint-rail { display: flex; flex-direction: column; min-height: 0; padding: 16px; border-right: 1px solid #e7eaf0; }
.rail-head, .detail-head, .param-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.rail-head { margin-bottom: 13px; }
.endpoint-search { display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px; border: 1px solid #dfe4ec; border-radius: 8px; color: #98a2b3; }
.endpoint-search:focus-within { border-color: #1677ff; box-shadow: none; }
.endpoint-search input { width: 100%; border: 0; outline: 0; color: #17233d; background: transparent; }
.endpoint-list { display: flex; flex-direction: column; gap: 7px; min-height: 0; margin-top: 12px; overflow-y: auto; }
.endpoint-item { display: grid; grid-template-columns: 52px 1fr 32px; align-items: center; gap: 10px; min-height: 66px; padding: 9px 10px; border: 1px solid transparent; border-radius: 9px; text-align: left; background: #fff; cursor: pointer; }
.endpoint-item:hover { background: #f8fafc; }
.endpoint-item.active { border-color: #6aa8ff; background: #f0f6ff; box-shadow: 0 2px 7px rgba(22,119,255,.08); }
.method-tag { justify-self: center; min-width: 44px; padding: 3px 5px; border-radius: 5px; color: #1677ff; background: #eaf3ff; font-size: 11px; font-weight: 700; text-align: center; }
.method-tag.post { color: #079455; background: #e9f9f2; }.method-tag.put,.method-tag.patch { color: #c26a10; background: #fff4e6; }.method-tag.delete { color: #d92d20; background: #fff0f0; }
.endpoint-copy { min-width: 0; }.endpoint-copy strong,.endpoint-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.endpoint-copy small { margin-top: 4px; color: #7a8699; }
.endpoint-more { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 6px; color: #7a8699; }
.endpoint-detail { min-width: 0; overflow-y: auto; padding: 18px 22px 24px; }
.detail-head { margin-bottom: 15px; }.detail-head strong,.rail-head strong { font-size: 15px; }
.operation-grid { display: grid; grid-template-columns: 130px 210px 1fr; gap: 10px; }.compact label { margin-bottom: 6px; }.method-field :deep(.ant-select),.operation-grid :deep(.ant-input),.path-field { width: 100%; }.description-field { margin-top: 10px; }
.validation-box { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding: 9px 11px; border-radius: 8px; color: #b42318; background: #fff1f0; font-size: 12px; }
.param-section { margin-top: 22px; }.param-head { margin-bottom: 9px; }.param-head div { display: flex; align-items: baseline; gap: 9px; }.param-head small { color: #98a2b3; }
.param-table { border: 1px solid #e6eaf0; border-radius: 9px; overflow: hidden; }.param-columns,.param-row { display: grid; grid-template-columns: minmax(120px,1fr) 150px 60px minmax(180px,1.4fr) 42px; gap: 8px; align-items: center; }.param-columns { padding: 7px 10px; color: #8a94a6; background: #f8fafc; font-size: 11px; }.param-row { padding: 7px 10px; border-top: 1px solid #eef1f5; }.param-row :deep(.ant-checkbox-wrapper) { justify-self: center; }
.empty-param { width: 100%; min-height: 44px; border: 1px dashed #d9e0ea; border-radius: 8px; color: #1677ff; background: #fbfcfe; cursor: pointer; }
.workbench-footer { display: grid; grid-template-columns: auto 1fr auto auto; gap: 10px; padding: 14px 24px; border-top: 1px solid #e5e8ee; background: #fff; box-shadow: 0 -5px 18px rgba(15,23,42,.035); }
.import-tip { margin: 0 0 10px; color: #7a8699; font-size: 13px; }
@media (max-width: 900px) { .http-workbench { height: calc(100vh - 24px); }.basic-grid { grid-template-columns: 1fr; }.basic-card { max-height: 280px; overflow-y: auto; }.editor-shell { display: block; overflow-y: auto; }.endpoint-rail { min-height: auto; border-right: 0; border-bottom: 1px solid #e7eaf0; }.endpoint-list { max-height: 210px; }.endpoint-detail { overflow: visible; }.operation-grid { grid-template-columns: 1fr 1fr; }.path-field { grid-column: 1 / -1; }.param-columns { display: none; }.param-row { grid-template-columns: 1fr 1fr 52px; padding: 10px; }.param-row > :nth-child(4) { grid-column: 1 / 3; }.param-row > :nth-child(5) { grid-column: 3; grid-row: 1; } }
@media (max-width: 560px) { .workbench-head { padding: 14px; }.workbench-head p { display: none; }.basic-card,.editor-shell { margin-left: 8px; margin-right: 8px; }.operation-grid { grid-template-columns: 1fr; }.path-field { grid-column: auto; }.auth-row { grid-template-columns: 1fr 34px; }.auth-row > :nth-child(2) { grid-column: 1; }.auth-row > :nth-child(3) { grid-column: 2; grid-row: 1; }.workbench-footer { padding: 10px; }.workbench-footer :deep(.ant-btn) { padding-inline: 10px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
</style>

<style lang="less">
.http-config-modal {
  .ant-modal { top: 12px; padding-bottom: 0; }
  .ant-modal-content { padding: 0; overflow: hidden; border-radius: 14px; }
}
</style>
