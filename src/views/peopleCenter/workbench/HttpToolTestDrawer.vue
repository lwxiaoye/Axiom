<template>
  <a-drawer v-model:open="visible" title="测试请求" :width="560" class="http-test-drawer" destroy-on-close>
    <div v-if="tool" class="test-panel">
      <div class="test-endpoint">
        <span :class="['method-pill', tool.method.toLowerCase()]">{{ tool.method }}</span>
        <strong>{{ tool.name || '未命名接口' }}</strong>
        <code>{{ tool.path || '/' }}</code>
      </div>

      <a-alert
        v-if="fileParams.length"
        type="info"
        show-icon
        message="文件将由服务端安全转发，单个文件最大 20MB。"
      />

      <div v-if="allParams.length" class="test-fields">
        <div v-for="param in allParams" :key="`${param.location}-${param.key}`" class="test-field">
          <label>
            <span>{{ param.key }}</span>
            <em v-if="param.required">*</em>
            <small>{{ locationLabels[param.location] }} · {{ param.type }}</small>
          </label>
          <input
            v-if="param.type === 'file'"
            type="file"
            :required="param.required"
            @change="onFileChange(param.key, $event)"
          />
          <a-select
            v-else-if="param.type === 'boolean'"
            v-model:value="values[param.key]"
            allow-clear
            :options="booleanOptions"
            placeholder="选择 true 或 false"
          />
          <a-textarea
            v-else-if="param.type === 'array' || param.type === 'object'"
            v-model:value="values[param.key]"
            :rows="3"
            placeholder="输入有效 JSON"
          />
          <a-input v-else v-model:value="values[param.key]" :placeholder="param.description || `输入 ${param.key}`" />
          <p v-if="fieldErrors[param.key]">{{ fieldErrors[param.key] }}</p>
          <small v-else-if="param.description" class="field-help">{{ param.description }}</small>
        </div>
      </div>
      <a-empty v-else description="当前接口没有需要填写的参数" />

      <div v-if="result" class="test-result">
        <div class="result-head">
          <span :class="['result-status', result.ok ? 'success' : 'failed']">
            {{ result.statusCode }} {{ result.ok ? '请求成功' : '请求失败' }}
          </span>
          <span>{{ result.durationMs }} ms</span>
        </div>
        <a-collapse ghost>
          <a-collapse-panel key="headers" header="响应 Header">
            <pre>{{ formatJson(result.headers) }}</pre>
          </a-collapse-panel>
        </a-collapse>
        <div class="response-body">
          <div class="response-label">
            <span>响应正文</span>
            <small v-if="result.truncated">内容已截断</small>
          </div>
          <pre>{{ formatBody(result.body) }}</pre>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="drawer-footer">
        <a-button @click="visible = false">关闭</a-button>
        <a-button type="primary" :loading="testing" @click="runTest">
          <PlayCircleOutlined />
          发送请求
        </a-button>
      </div>
    </template>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { PlayCircleOutlined } from '@ant-design/icons-vue';
import { testHttpToolRequest, type HttpToolTestResult } from '../../workflow/api/workflow.api';
import type { HttpToolItem, HttpToolSetConfig, ParamLocation, ParamRow } from './httpToolConfig';

type LocatedParam = ParamRow & { location: ParamLocation };

const visible = ref(false);
const testing = ref(false);
const tool = ref<HttpToolItem | null>(null);
const config = ref<HttpToolSetConfig | null>(null);
const values = reactive<Record<string, any>>({});
const files = reactive<Record<string, File>>({});
const fieldErrors = reactive<Record<string, string>>({});
const result = ref<HttpToolTestResult | null>(null);

const booleanOptions = [
  { value: true, label: 'true' },
  { value: false, label: 'false' },
];
const locationLabels: Record<ParamLocation, string> = { path: '路径', query: '查询', header: 'Header', body: 'Body' };

const allParams = computed<LocatedParam[]>(() => {
  if (!tool.value) return [];
  return [
    ...tool.value.pathParams.map((row) => ({ ...row, location: 'path' as const })),
    ...tool.value.queryParams.map((row) => ({ ...row, location: 'query' as const })),
    ...tool.value.headerParams.map((row) => ({ ...row, location: 'header' as const })),
    ...tool.value.bodyParams.map((row) => ({ ...row, location: 'body' as const })),
  ];
});
const fileParams = computed(() => allParams.value.filter((row) => row.type === 'file'));

function open(target: HttpToolItem, source: HttpToolSetConfig) {
  tool.value = target;
  config.value = source;
  Object.keys(values).forEach((key) => delete values[key]);
  Object.keys(files).forEach((key) => delete files[key]);
  Object.keys(fieldErrors).forEach((key) => delete fieldErrors[key]);
  result.value = null;
  visible.value = true;
}

function onFileChange(key: string, event: Event) {
  const target = event.target as HTMLInputElement;
  const file = target.files?.[0];
  if (file) files[key] = file;
  else delete files[key];
}

function normalizeValues() {
  const normalized: Record<string, unknown> = {};
  Object.keys(fieldErrors).forEach((key) => delete fieldErrors[key]);
  for (const param of allParams.value) {
    if (param.type === 'file') {
      if (param.required && !files[param.key]) fieldErrors[param.key] = '请选择文件';
      continue;
    }
    const value = values[param.key];
    if (param.required && (value === undefined || value === null || value === '')) {
      fieldErrors[param.key] = '该参数为必填项';
      continue;
    }
    if (value === undefined || value === null || value === '') continue;
    try {
      if (param.type === 'integer') normalized[param.key] = Number.parseInt(String(value), 10);
      else if (param.type === 'number') normalized[param.key] = Number(value);
      else if (param.type === 'array' || param.type === 'object') normalized[param.key] = JSON.parse(String(value));
      else normalized[param.key] = value;
      if ((param.type === 'integer' || param.type === 'number') && Number.isNaN(normalized[param.key])) {
        fieldErrors[param.key] = '请输入有效数字';
      }
    } catch {
      fieldErrors[param.key] = '请输入有效 JSON';
    }
  }
  return normalized;
}

async function runTest() {
  if (!tool.value || !config.value) return;
  const normalized = normalizeValues();
  if (Object.keys(fieldErrors).length) return;
  testing.value = true;
  try {
    result.value = await testHttpToolRequest({
      config: config.value,
      toolName: tool.value.name,
      values: normalized,
      files: { ...files },
    });
  } catch (error: any) {
    message.error(error?.message || '测试请求失败');
  } finally {
    testing.value = false;
  }
}

function formatJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function formatBody(body: string) {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

defineExpose({ open });
</script>

<style scoped lang="less">
.test-panel { display: flex; flex-direction: column; gap: 20px; }
.test-endpoint { display: flex; align-items: center; gap: 10px; padding: 14px; border: 1px solid #e8ebf0; border-radius: 12px; background: #fafbfc; }
.test-endpoint code { margin-left: auto; color: #64748b; }
.method-pill { padding: 3px 8px; border-radius: 6px; color: #1677ff; background: #eaf3ff; font-size: 12px; font-weight: 700; }
.method-pill.post { color: #0f9f6e; background: #e9f9f2; }
.method-pill.put, .method-pill.patch { color: #c26a10; background: #fff4e6; }
.method-pill.delete { color: #d83b3b; background: #fff0f0; }
.test-fields { display: grid; gap: 16px; }
.test-field label { display: flex; align-items: baseline; gap: 6px; margin-bottom: 7px; color: #17233d; font-weight: 600; }
.test-field label em { color: #ef4444; font-style: normal; }
.test-field label small { margin-left: auto; color: #98a2b3; font-weight: 400; }
.test-field p { margin: 5px 0 0; color: #dc2626; font-size: 12px; }
.field-help { display: block; margin-top: 5px; color: #8a94a6; }
.test-result { border-top: 1px solid #eef0f4; padding-top: 18px; }
.result-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; color: #667085; }
.result-status { font-weight: 700; }
.result-status.success { color: #079455; }
.result-status.failed { color: #d92d20; }
.response-label { display: flex; justify-content: space-between; margin-bottom: 8px; font-weight: 600; }
.response-label small { color: #c26a10; font-weight: 400; }
pre { max-height: 320px; overflow: auto; margin: 0; padding: 12px; border-radius: 9px; background: #0f172a; color: #e2e8f0; font: 12px/1.6 Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
.drawer-footer { display: flex; justify-content: flex-end; gap: 10px; }
@media (max-width: 640px) { .test-endpoint { align-items: flex-start; flex-wrap: wrap; } .test-endpoint code { width: 100%; margin-left: 0; } }
</style>
