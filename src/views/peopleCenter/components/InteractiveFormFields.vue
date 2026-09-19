<template>
  <div class="if-form">
    <div v-for="f in fieldList" :key="f.key" class="if-field">
      <label>
        <span class="if-label">{{ f.label || f.key }}<em v-if="f.required">*</em></span>
        <small v-if="f.description">{{ f.description }}</small>
      </label>
      <a-input-number
        v-if="f.type === 'numberInput'"
        v-model:value="values[f.key]"
        :min="f.min"
        :max="f.max"
        :disabled="disabled"
        class="if-control"
      />
      <a-input-password
        v-else-if="f.type === 'password'"
        v-model:value="values[f.key]"
        :maxlength="f.maxLength"
        :disabled="disabled"
        class="if-control"
      />
      <a-select
        v-else-if="f.type === 'select' || f.type === 'selectLLMModel'"
        v-model:value="values[f.key]"
        :options="fieldOptions(f)"
        :disabled="disabled"
        class="if-control"
      />
      <a-select
        v-else-if="f.type === 'multipleSelect'"
        v-model:value="values[f.key]"
        mode="multiple"
        :options="fieldOptions(f)"
        :disabled="disabled"
        class="if-control"
      />
      <a-switch
        v-else-if="f.type === 'switch'"
        class="if-switch"
        :checked="!!values[f.key]"
        :disabled="disabled"
        @change="values[f.key] = $event"
      />
      <a-textarea
        v-else-if="f.type === 'textarea'"
        v-model:value="values[f.key]"
        :rows="3"
        :maxlength="f.maxLength"
        :disabled="disabled"
        class="if-control"
      />
      <a-date-picker
        v-else-if="f.type === 'timePointSelect'"
        v-model:value="values[f.key]"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        :disabled="disabled"
        class="if-control"
      />
      <a-range-picker
        v-else-if="f.type === 'timeRangeSelect'"
        v-model:value="values[f.key]"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        :disabled="disabled"
        class="if-control"
      />
      <div v-else-if="isFileField(f)" class="if-file-field">
        <input :id="fileInputId(f.key)" class="if-file-input" type="file" multiple @change="onFileChange(f.key, $event)" />
        <button
          type="button"
          class="if-file-btn"
          :disabled="disabled || !!uploadingKey"
          @click="openFilePicker(f.key)"
        >
          <PaperClipOutlined />
          {{ uploadingKey === f.key ? '上传中…' : '上传文件' }}
        </button>
        <div v-if="fileValues(f.key).length" class="if-file-list">
          <span v-for="url in fileValues(f.key)" :key="url" class="if-file-chip" :title="url">
            <FileTextOutlined />
            {{ fileNameFromUrl(url) }}
            <button type="button" title="移除文件" :disabled="disabled" @click="removeFile(f.key, url)">×</button>
          </span>
        </div>
      </div>
      <a-input v-else v-model:value="values[f.key]" :maxlength="f.maxLength" :disabled="disabled" class="if-control" />
    </div>
    <button type="button" class="if-submit" :disabled="disabled || !!uploadingKey" @click="handleSubmit">
      {{ uploadingKey ? '文件上传中…' : submitText }}
    </button>
  </div>
</template>

<script setup lang="ts">
// 交互节点 formInput 的共享表单渲染：字段类型全集与后端 input.required 的 formInput 协议对齐。
// 「我的智能体」运行窗与主对话 HITL 卡共用本组件——两边渲染同一份 inputForm，杜绝
// 字段类型支持漂移（主对话曾是纯文本框阉割版，下拉/文件字段一冒泡到主对话就退化）。
import { computed, reactive, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { FileTextOutlined, PaperClipOutlined } from '@ant-design/icons-vue';
import { uploadImg } from '/@/api/sys/upload';
import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';

export interface InteractiveFormField {
  key: string;
  label?: string;
  description?: string;
  type?: string;
  valueType?: string;
  required?: boolean;
  maxLength?: number;
  min?: number;
  max?: number;
  defaultValue?: any;
  list?: { label?: string; value: string }[];
  enums?: { label?: string; value: string }[];
}

const props = withDefaults(
  defineProps<{
    fields: InteractiveFormField[];
    disabled?: boolean;
    submitText?: string;
    /** 文件 input 的 DOM id 前缀：同页多卡时保证唯一 */
    idPrefix?: string;
  }>(),
  { disabled: false, submitText: '提交', idPrefix: 'interactive-form' },
);

const emit = defineEmits<{ (e: 'submit', values: Record<string, any>): void }>();

const values = reactive<Record<string, any>>({});
const uploadingKey = ref('');

const fieldList = computed(() => (Array.isArray(props.fields) ? props.fields.filter((f) => f?.key) : []));

watch(
  fieldList,
  (fields) => {
    Object.keys(values).forEach((key) => delete values[key]);
    fields.forEach((f) => {
      values[f.key] = initialValue(f);
    });
  },
  { immediate: true },
);

function initialValue(field: InteractiveFormField) {
  if (field.defaultValue !== undefined) return field.defaultValue;
  if (field.type === 'switch') return false;
  if (field.type === 'multipleSelect' || field.type === 'timeRangeSelect' || isFileField(field)) return [];
  return undefined;
}

function fieldOptions(field: InteractiveFormField) {
  return (field.list || field.enums || []).map((item) => ({ label: item.label || item.value, value: item.value }));
}

function isFileField(field: InteractiveFormField) {
  const type = String(field.type || '').trim();
  if (['fileSelect', 'file', 'fileInput', 'uploadFile', 'upload'].includes(type)) return true;
  const keyText = `${field.key || ''} ${field.label || ''}`.toLowerCase();
  const looksLikeFileField = keyText.includes('file') || keyText.includes('文件') || keyText.includes('附件');
  return field.valueType === 'arrayString' && looksLikeFileField;
}

function fileInputId(key: string) {
  return `${props.idPrefix}-file-${key}`;
}

function fileValues(key: string): string[] {
  const value = values[key];
  if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean);
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function fileNameFromUrl(url: string) {
  const clean = String(url || '').split('?')[0].split('#')[0];
  const name = clean.split('/').filter(Boolean).pop();
  try {
    return name ? decodeURIComponent(name) : '已上传文件';
  } catch {
    return name || '已上传文件';
  }
}

function normalizeUploadUrl(response: any): string {
  const result = response?.result ?? response?.data?.result;
  if (typeof result === 'string') return getFileAccessHttpUrl(result.trim());
  const data = result || response?.data || response || {};
  const candidates = [data.url, data.fileUrl, data.path, response?.message, response?.data?.message]
    .map((item) => String(item || '').trim())
    .filter(Boolean);
  const rawUrl = candidates.find((item) => /^https?:\/\//.test(item) || item.startsWith('/') || item.includes('.')) || '';
  return rawUrl ? getFileAccessHttpUrl(rawUrl) : '';
}

function openFilePicker(key: string) {
  if (props.disabled || uploadingKey.value) return;
  document.getElementById(fileInputId(key))?.click();
}

async function onFileChange(key: string, event: Event) {
  const inputEl = event.target as HTMLInputElement;
  const files = Array.from(inputEl.files || []);
  inputEl.value = '';
  if (!files.length) return;
  uploadingKey.value = key;
  try {
    const current = fileValues(key);
    for (const file of files) {
      const response = await uploadImg({ file }, () => {});
      const url = normalizeUploadUrl(response);
      if (!url) throw new Error(`${file.name} 上传后未返回文件地址`);
      if (!current.includes(url)) current.push(url);
    }
    values[key] = current;
    message.success('文件已上传');
  } catch (error: any) {
    message.error(error?.message || '文件上传失败');
  } finally {
    uploadingKey.value = '';
  }
}

function removeFile(key: string, url: string) {
  values[key] = fileValues(key).filter((item) => item !== url);
}

function isMissing(value: any) {
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || (typeof value === 'string' && !value.trim());
}

function handleSubmit() {
  if (props.disabled || uploadingKey.value) return;
  const missing = fieldList.value.find((f) => f.required && isMissing(values[f.key]));
  if (missing) {
    message.warning(`请填写：${missing.label || missing.key}`);
    return;
  }
  emit('submit', { ...values });
}
</script>

<style scoped>
.if-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.if-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.if-field label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  color: #4b5059;
}

.if-label em {
  color: #dc2626;
  font-style: normal;
  margin-left: 2px;
}

.if-field small {
  color: #9096a1;
  font-size: 12px;
}

.if-control {
  width: 100%;
}

/* 开关是行内小件：flex 列布局默认 stretch 会把它拉成通栏 */
.if-switch {
  align-self: flex-start;
}

.if-file-input {
  display: none;
}

.if-file-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  border: 1px solid #d7d8dd;
  border-radius: 8px;
  background: #fff;
  padding: 6px 12px;
  color: #202228;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.if-file-btn:hover:not(:disabled) {
  border-color: #111;
}

.if-file-btn:disabled {
  color: #9096a1;
  cursor: not-allowed;
}

.if-file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.if-file-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  border: 1px solid #e4e4e8;
  border-radius: 6px;
  background: #f6f6f7;
  padding: 3px 8px;
  font-size: 12px;
  color: #4b5059;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.if-file-chip button {
  border: 0;
  background: transparent;
  color: #9096a1;
  cursor: pointer;
  padding: 0 2px;
  font-size: 13px;
  line-height: 1;
}

.if-file-chip button:hover {
  color: #b23b3b;
}

.if-submit {
  align-self: flex-start;
  border: 0;
  border-radius: 8px;
  background: #111;
  padding: 8px 18px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.if-submit:disabled {
  background: #c8c8cc;
  cursor: not-allowed;
}
</style>
