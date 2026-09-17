<template>
  <a-drawer :open="open" title="调试运行" :width="480" @close="$emit('update:open', false)">
    <div class="debug-panel">
      <section v-if="variableItems.length" class="debug-section">
        <h4>全局变量</h4>
        <div v-for="variable in variableItems" :key="variable.id" class="variable-item">
          <label>
            {{ variable.label }}
            <em v-if="variable.required">*</em>
          </label>
          <a-switch
            v-if="variable.type === VariableInputEnum.switch"
            :checked="!!variableValues[variable.key]"
            size="small"
            @change="variableValues[variable.key] = $event"
          />
          <a-input-number
            v-else-if="variable.type === VariableInputEnum.numberInput"
            :value="variableValues[variable.key]"
            style="width: 100%"
            @change="variableValues[variable.key] = $event"
          />
          <a-select
            v-else-if="variable.type === VariableInputEnum.select"
            :value="variableValues[variable.key]"
            class="workflow-select-control"
            popup-class-name="wf-node-select-popup"
            :options="(variable.enums || []).map((item) => ({ label: item.value, value: item.value }))"
            @change="variableValues[variable.key] = $event"
          />
          <a-textarea
            v-else-if="variable.type === VariableInputEnum.textarea"
            :value="variableValues[variable.key]"
            :rows="2"
            @change="variableValues[variable.key] = ($event.target as HTMLTextAreaElement).value"
          />
          <a-input
            v-else
            :value="variableValues[variable.key]"
            @change="variableValues[variable.key] = ($event.target as HTMLInputElement).value"
          />
        </div>
      </section>

      <section class="debug-section">
        <h4>用户问题</h4>
        <a-textarea
          v-model:value="input"
          :rows="3"
          :disabled="isStepPaused"
          placeholder="输入调试问题，如：帮我查一下请假流程"
        />
        <div class="debug-actions">
          <a-button type="primary" :loading="running" :disabled="isStepPaused" @click="handleRun">
            <PlayCircleOutlined v-if="!running" />
            全流程运行
          </a-button>
          <a-button v-if="!isStepPaused" :loading="stepRunning" :disabled="running" @click="handleStartStep">
            <StepForwardOutlined v-if="!stepRunning" />
            开始单步
          </a-button>
          <a-button v-else :loading="stepRunning" @click="handleNextStep">
            <StepForwardOutlined v-if="!stepRunning" />
            执行下一节点
          </a-button>
          <a-button v-if="isStepPaused" danger :disabled="stepRunning" @click="$emit('stop-step')">
            <StopOutlined />
            结束单步
          </a-button>
        </div>
        <p v-if="isStepPaused" class="step-hint">
          {{ stepSession?.nextNodeId ? `已暂停；下一节点：${stepSession.nextNodeId}` : '已暂停；可继续执行。' }}
        </p>
      </section>

      <section v-if="displayResult" class="debug-section">
        <div class="result-head">
          <h4>运行结果</h4>
          <em :class="['result-status', resultStatusClass]">
            {{ displayStatusLabel }}<template v-if="result?.durationMs !== undefined"> · {{ result.durationMs }}ms</template>
          </em>
        </div>
        <pre v-if="displayResult.output" class="result-output">{{ displayResult.output }}</pre>
        <div v-if="resultChartOutputs.length" class="result-chart-list">
          <EChartsOutputPreview
            v-for="(chartOutput, chartIndex) in resultChartOutputs"
            :key="chartIndex"
            :output="chartOutput"
          />
        </div>
        <a-alert v-if="displayResult.errorMessage" type="error" :message="displayResult.errorMessage" show-icon />

        <div v-if="stepSession" class="step-session-summary">
          <span>单步会话</span>
          <span v-if="stepSession.currentNodeId">最近节点：{{ stepSession.currentNodeId }}</span>
          <span v-if="stepSession.nextNodeId">下一节点：{{ stepSession.nextNodeId }}</span>
        </div>
        <a-collapse v-if="stepSession && Object.keys(stepSession.variables || {}).length" ghost class="variable-snapshot">
          <a-collapse-panel key="variables" header="当前变量快照">
            <pre class="trace-detail">{{ formatTrace(stepSession.variables || {}) }}</pre>
          </a-collapse-panel>
        </a-collapse>

        <div v-if="result?.interactive" class="interactive-box">
          <p class="interactive-desc">{{ result.interactive.params?.description || '工作流等待你的输入后继续' }}</p>
          <div v-if="result.interactive.type === 'userSelect'" class="interactive-options">
            <a-button
              v-for="opt in result.interactive.params?.userSelectOptions || []"
              :key="opt.key || opt.value"
              :loading="running"
              @click="handleResume(opt.value)"
            >
              {{ opt.value }}
            </a-button>
          </div>
          <div v-else class="interactive-form">
            <label v-for="fieldItem in interactiveFormItems" :key="fieldItem.key">
              <span>
                {{ fieldItem.label || fieldItem.key }}<em v-if="fieldItem.required"> *</em>
                <i v-if="fieldItem.description" class="field-desc">{{ fieldItem.description }}</i>
              </span>
              <a-input-number
                v-if="fieldItem.type === 'numberInput'"
                v-model:value="formValues[fieldItem.key]"
                :min="fieldItem.min"
                :max="fieldItem.max"
                style="width: 100%"
              />
              <a-input-password
                v-else-if="fieldItem.type === 'password'"
                v-model:value="formValues[fieldItem.key]"
                :maxlength="fieldItem.maxLength"
              />
              <a-select
                v-else-if="fieldItem.type === 'select' || fieldItem.type === 'selectLLMModel'"
                v-model:value="formValues[fieldItem.key]"
                class="workflow-select-control"
                popup-class-name="wf-node-select-popup"
                :options="fieldItem.list || []"
              />
              <a-select
                v-else-if="fieldItem.type === 'multipleSelect'"
                v-model:value="formValues[fieldItem.key]"
                mode="multiple"
                class="workflow-select-control"
                popup-class-name="wf-node-select-popup"
                :options="fieldItem.list || []"
              />
              <a-switch
                v-else-if="fieldItem.type === 'switch'"
                class="form-switch"
                :checked="!!formValues[fieldItem.key]"
                @change="formValues[fieldItem.key] = $event"
              />
              <a-textarea
                v-else-if="fieldItem.type === 'textarea'"
                v-model:value="formValues[fieldItem.key]"
                :rows="3"
                :maxlength="fieldItem.maxLength"
              />
              <a-date-picker
                v-else-if="fieldItem.type === 'timePointSelect'"
                v-model:value="formValues[fieldItem.key]"
                show-time
                value-format="YYYY-MM-DD HH:mm:ss"
                style="width: 100%"
              />
              <a-range-picker
                v-else-if="fieldItem.type === 'timeRangeSelect'"
                v-model:value="formValues[fieldItem.key]"
                show-time
                value-format="YYYY-MM-DD HH:mm:ss"
                style="width: 100%"
              />
              <div
                v-else-if="fieldItem.type === 'fileSelect'"
                class="form-file-field"
              >
                <input
                  :id="formFileInputId(fieldItem.key)"
                  class="file-input"
                  type="file"
                  multiple
                  @change="handleFormFileChange(fieldItem.key, $event)"
                />
                <a-button
                  size="small"
                  :loading="uploadingFormKey === fieldItem.key"
                  :disabled="running"
                  @click="openFormFilePicker(fieldItem.key)"
                >
                  <template #icon>
                    <PaperClipOutlined />
                  </template>
                  上传文件
                </a-button>
                <div v-if="formFileValues(fieldItem.key).length" class="form-file-list">
                  <span v-for="url in formFileValues(fieldItem.key)" :key="url" class="file-chip" :title="url">
                    <FileTextOutlined />
                    {{ fileNameFromUrl(url) }}
                    <button type="button" title="移除文件" @click="removeFormFile(fieldItem.key, url)">×</button>
                  </span>
                </div>
              </div>
              <a-input v-else v-model:value="formValues[fieldItem.key]" :maxlength="fieldItem.maxLength" />
            </label>
            <a-button type="primary" :loading="running" :disabled="!!uploadingFormKey" @click="handleResume({ ...formValues })">提交并继续</a-button>
          </div>
        </div>

        <div v-if="displayResult.nodeRuns?.length" class="trace-list">
          <h4>节点轨迹</h4>
          <a-collapse ghost>
            <a-collapse-panel v-for="(trace, index) in displayResult.nodeRuns" :key="index">
              <template #header>
                <span class="trace-header">
                  <em :class="['trace-status', trace.status === 'failed' ? 'failed' : 'success']"></em>
                  {{ trace.nodeLabel || trace.nodeId || `节点 ${index + 1}` }}
                  <span class="trace-meta">{{ trace.nodeType }} · {{ trace.durationMs ?? '-' }}ms</span>
                </span>
              </template>
              <pre class="trace-detail">{{ formatTrace(trace) }}</pre>
            </a-collapse-panel>
          </a-collapse>
        </div>
      </section>
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { FileTextOutlined, PaperClipOutlined, PlayCircleOutlined, StepForwardOutlined, StopOutlined } from '@ant-design/icons-vue';
import { uploadImg } from '/@/api/sys/upload';
import { getFileAccessHttpUrl } from '/@/utils/common/compUtils';
import { useMessage } from '/@/hooks/web/useMessage';
import { VariableInputEnum } from '../../core/constants';
import type { AppChatConfigType } from '../../core/type';
import type { WorkflowDebugSession, WorkflowRunResponse } from '../../api/workflow.api';
import { extractChartOutputs } from '../../shared/chartOutput';
import EChartsOutputPreview from './EChartsOutputPreview.vue';

const props = defineProps<{
  open: boolean;
  chatConfig: AppChatConfigType;
  running: boolean;
  stepRunning: boolean;
  result: WorkflowRunResponse | null;
  stepSession: WorkflowDebugSession | null;
}>();

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'run', payload: { input: string; variables: Record<string, any> }): void;
  (e: 'start-step', payload: { input: string; variables: Record<string, any> }): void;
  (e: 'next-step', payload: { variables: Record<string, any> }): void;
  (e: 'stop-step'): void;
  (e: 'resume', payload: { resumeId: string; value: string | Record<string, any> }): void;
}>();

const input = ref('');
const variableValues = reactive<Record<string, any>>({});
const formValues = reactive<Record<string, any>>({});
const uploadingFormKey = ref('');
const { createMessage } = useMessage();

const variableItems = computed(() => props.chatConfig.variables || []);
const isStepPaused = computed(() => props.stepSession?.status === 'paused');
const displayResult = computed(() => props.stepSession || props.result);
const resultStatusClass = computed(() =>
  ['success', 'completed'].includes(displayResult.value?.status || '')
    ? 'success'
    : ['waiting', 'paused'].includes(displayResult.value?.status || '')
      ? 'waiting'
      : 'failed'
);
const displayStatusLabel = computed(() => {
  const labels: Record<string, string> = { paused: '已暂停', completed: '已完成', failed: '失败', stopped: '已停止', success: '成功', waiting: '等待输入' };
  return labels[displayResult.value?.status || ''] || displayResult.value?.status || '';
});
const resultChartOutputs = computed(() => extractChartOutputs(props.result));
const interactiveFormItems = computed<any[]>(() => {
  const params = props.result?.interactive?.params as any;
  return params?.inputForm || params?.userInputForms || [];
});

watch(
  () => props.result?.interactive?.resumeId,
  () => {
    Object.keys(formValues).forEach((key) => delete formValues[key]);
    interactiveFormItems.value.forEach((fieldItem) => {
      if (fieldItem?.key && fieldItem.defaultValue !== undefined) {
        formValues[fieldItem.key] = fieldItem.defaultValue;
      }
    });
  }
);

function handleRun() {
  emit('run', { input: input.value, variables: { ...variableValues } });
}

function handleStartStep() {
  emit('start-step', { input: input.value, variables: { ...variableValues } });
}

function handleNextStep() {
  emit('next-step', { variables: { ...variableValues } });
}

function handleResume(value: string | Record<string, any>) {
  const interactive = props.result?.interactive;
  if (!interactive || props.running) return;
  emit('resume', { resumeId: interactive.resumeId, value });
}

function formFileInputId(key: string) {
  return `workflow-debug-form-file-${key}`;
}

function formFileValues(key: string): string[] {
  const value = formValues[key];
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

function openFormFilePicker(key: string) {
  if (props.running || uploadingFormKey.value) return;
  document.getElementById(formFileInputId(key))?.click();
}

async function handleFormFileChange(key: string, event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = '';
  if (!files.length) return;
  uploadingFormKey.value = key;
  try {
    const current = formFileValues(key);
    for (const file of files) {
      const response = await uploadImg({ file }, () => {});
      const url = normalizeUploadUrl(response);
      if (!url) throw new Error(`${file.name} 上传后未返回文件地址`);
      if (!current.includes(url)) current.push(url);
    }
    formValues[key] = current;
    createMessage.success('文件已上传');
  } catch (error: any) {
    createMessage.error(error?.message || '文件上传失败');
  } finally {
    uploadingFormKey.value = '';
  }
}

function removeFormFile(key: string, url: string) {
  formValues[key] = formFileValues(key).filter((item) => item !== url);
}

function formatTrace(trace: Record<string, any>) {
  const rest = { ...trace };
  ['nodeId', 'nodeType', 'nodeLabel', 'status', 'durationMs'].forEach((key) => delete rest[key]);
  return JSON.stringify(rest, null, 2);
}
</script>

<style scoped lang="less">
.debug-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.debug-section {
  h4 {
    margin: 0 0 8px;
    font-size: 13px;
    color: #0f172a;
  }
}

.variable-item {
  margin-bottom: 10px;

  label {
    display: block;
    font-size: 12px;
    color: #475569;
    margin-bottom: 4px;

    em {
      color: #dc2626;
      font-style: normal;
    }
  }
}

.debug-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;

  .ant-btn {
    min-height: 32px;
  }
}

.step-hint {
  margin: 8px 0 0;
  color: #a16207;
  font-size: 12px;
  line-height: 1.5;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;

  .result-status {
    font-size: 12px;
    font-style: normal;

    &.success {
      color: #047857;
    }

    &.failed {
      color: #dc2626;
    }

    &.waiting {
      color: #c2410c;
    }
  }
}

.result-output {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow: auto;
}

.result-chart-list {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}

.step-session-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  margin-top: 10px;
  color: #475569;
  font-size: 12px;
}

.variable-snapshot {
  margin-top: 6px;
}

.interactive-box {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  background: #fff7ed;

  .interactive-desc {
    margin: 0 0 10px;
    color: #7c2d12;
    font-size: 12px;
    font-weight: 600;
    line-height: 1.6;
  }

  .interactive-options {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .interactive-form {
    display: grid;
    gap: 10px;

    label {
      display: grid;
      gap: 4px;
      color: #7c2d12;
      font-size: 12px;

      em {
        color: #dc2626;
        font-style: normal;
      }

      .field-desc {
        margin-left: 6px;
        color: #a8a29e;
        font-size: 11px;
        font-style: normal;
      }
    }

    .form-switch {
      justify-self: start;
      width: auto;
    }

    .form-file-field {
      display: grid;
      justify-items: start;
      gap: 8px;
    }

    .file-input {
      display: none;
    }

    .form-file-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .file-chip {
      display: inline-flex;
      max-width: 100%;
      align-items: center;
      gap: 6px;
      padding: 5px 8px;
      color: #7c2d12;
      font-size: 12px;
      border: 1px solid #fed7aa;
      border-radius: 8px;
      background: #fff;

      button {
        display: inline-flex;
        width: 16px;
        height: 16px;
        align-items: center;
        justify-content: center;
        padding: 0;
        color: #a16207;
        border: 0;
        background: transparent;
        cursor: pointer;
      }
    }
  }
}

.trace-list {
  margin-top: 8px;

  .trace-header {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;

    .trace-status {
      width: 8px;
      height: 8px;
      border-radius: 50%;

      &.success {
        background: #10b981;
      }

      &.failed {
        background: #dc2626;
      }
    }

    .trace-meta {
      color: #94a3b8;
    }
  }

  .trace-detail {
    font-size: 11px;
    background: #f8fafc;
    border-radius: 6px;
    padding: 8px;
    max-height: 200px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
}
</style>

<style lang="less">
@import '../../shared/workflowSelect.less';
</style>
