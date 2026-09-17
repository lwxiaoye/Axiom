<template>
  <a-drawer
    :open="open"
    title="提示词调试"
    :width="760"
    :destroy-on-close="false"
    @close="emit('update:open', false)"
  >
    <div class="prompt-debug">
      <p class="intro">以当前草稿与模型参数进行一次无工具调用的实验，不会保存提示词、执行工作流或影响线上版本。</p>

      <section class="panel">
        <div class="panel-head">
          <h4>系统提示词</h4>
          <div class="panel-actions">
            <a-input v-model:value="generationGoal" :maxlength="10000" placeholder="可选：说明希望它完成的任务或优化目标" />
            <a-button size="small" :loading="generating" :disabled="!model" @click="generate">
              自动生成提示词
            </a-button>
            <a-button size="small" :disabled="workingPrompt === prompt" @click="applyPrompt">应用</a-button>
          </div>
        </div>
        <a-textarea v-model:value="workingPrompt" :rows="8" :maxlength="100000" placeholder="输入系统提示词" />
      </section>

      <section v-if="variables.length" class="panel">
        <div class="panel-head"><h4>模拟变量</h4><span>未填写的变量将保留占位符</span></div>
        <div class="variable-grid">
          <label v-for="variable in variables" :key="variable.key">
            <span>{{ variable.label || variable.key }}</span>
            <a-select
              v-if="variable.enums?.length"
              v-model:value="variableValues[variable.key]"
              allow-clear
              :options="variable.enums"
              :placeholder="`填写 ${variable.key}`"
            />
            <a-input v-else v-model:value="variableValues[variable.key]" :placeholder="`填写 ${variable.key}`" />
          </label>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h4>模拟问题</h4><span>{{ model ? `模型：${model}` : '请先选择模型' }}</span></div>
        <a-textarea v-model:value="question" :rows="3" :maxlength="20000" placeholder="输入用户会如何提问" />
        <a-button type="primary" block class="run-button" :loading="running" :disabled="!model || !question.trim()" @click="run">
          运行提示词调试
        </a-button>
      </section>

      <section v-if="result" class="result-panel">
        <div class="result-meta">
          <span>{{ result.model }}</span>
          <span>{{ result.durationMs }}ms</span>
          <span>{{ result.usage.totalTokens }} Tokens</span>
        </div>
        <div class="raw-output">
          <h4>模型原始响应</h4>
          <pre>{{ result.rawOutput }}</pre>
        </div>
      </section>
    </div>
  </a-drawer>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useMessage } from '/@/hooks/web/useMessage';
import {
  debugWorkflowPrompt,
  generateWorkflowPrompt,
  type PromptDebugResult,
} from '../api/workflow.api';

type PromptVariable = {
  key: string;
  label?: string;
  defaultValue?: unknown;
  enums?: { label?: string; value: string }[];
};

const props = withDefaults(defineProps<{
  open: boolean;
  appId: string;
  prompt: string;
  model: string;
  variables?: PromptVariable[];
  temperature?: number;
  maxToken?: number;
  topP?: number;
  stopSign?: string;
  responseFormat?: string;
  jsonSchema?: string;
}>(), { variables: () => [] });

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void;
  (event: 'apply', prompt: string): void;
}>();

const { createMessage } = useMessage();
const workingPrompt = ref('');
const question = ref('');
const generationGoal = ref('');
const running = ref(false);
const generating = ref(false);
const result = ref<PromptDebugResult | null>(null);
const variableValues = reactive<Record<string, unknown>>({});

function resetForCurrentPrompt() {
  workingPrompt.value = props.prompt || '';
  result.value = null;
  props.variables.forEach((variable) => {
    if (!(variable.key in variableValues) && variable.defaultValue !== undefined) {
      variableValues[variable.key] = variable.defaultValue;
    }
  });
}

watch(() => props.open, (opened) => {
  if (opened) resetForCurrentPrompt();
});

function applyPrompt() {
  emit('apply', workingPrompt.value);
  createMessage.success('已应用到编辑器，记得保存草稿后再发布');
}

function errorMessage(error: any, fallback: string) {
  return error?.response?.data?.detail || error?.message || fallback;
}

async function generate() {
  if (!props.appId || !props.model) return;
  generating.value = true;
  try {
    const response = await generateWorkflowPrompt({
      appId: props.appId,
      currentPrompt: workingPrompt.value,
      goal: generationGoal.value,
      model: props.model,
      variableKeys: props.variables.map((item) => item.key).filter(Boolean),
    });
    workingPrompt.value = response.prompt;
    createMessage.success('已生成候选提示词，请审阅后再应用');
  } catch (error: any) {
    createMessage.error(errorMessage(error, '生成提示词失败'));
  } finally {
    generating.value = false;
  }
}

async function run() {
  if (!props.appId || !props.model || !question.value.trim()) return;
  running.value = true;
  result.value = null;
  try {
    result.value = await debugWorkflowPrompt({
      appId: props.appId,
      prompt: workingPrompt.value,
      question: question.value.trim(),
      model: props.model,
      variables: variableValues,
      temperature: props.temperature,
      maxToken: props.maxToken,
      topP: props.topP,
      stopSign: props.stopSign,
      responseFormat: props.responseFormat,
      jsonSchema: props.jsonSchema,
    });
  } catch (error: any) {
    createMessage.error(errorMessage(error, '提示词调试失败'));
  } finally {
    running.value = false;
  }
}
</script>

<style scoped lang="less">
.prompt-debug { display: grid; gap: 16px; }
.intro { margin: 0; color: #64748b; font-size: 13px; line-height: 1.65; }
.panel, .result-panel { padding: 14px; border: 1px solid #e7edf5; border-radius: 10px; background: #fff; }
.panel-head, .panel-actions, .result-meta { display: flex; align-items: center; gap: 10px; }
.panel-head { justify-content: space-between; margin-bottom: 10px; }
.panel-head h4, .raw-output h4 { margin: 0; color: #1e293b; font-size: 14px; }
.panel-head span { color: #94a3b8; font-size: 12px; }
.panel-actions { flex: 1; justify-content: flex-end; min-width: 0; }
.panel-actions > :first-child { flex: 1; max-width: 420px; }
.variable-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.variable-grid label { display: grid; gap: 5px; color: #475569; font-size: 12px; }
.run-button { margin-top: 10px; }
.result-meta { margin-bottom: 10px; color: #64748b; font-size: 12px; }
.raw-output { margin-top: 10px; min-width: 0; }
.raw-output h4 { margin-bottom: 7px; }
pre { min-height: 140px; max-height: 360px; margin: 0; padding: 10px; overflow: auto; white-space: pre-wrap; word-break: break-word; border-radius: 7px; background: #f8fafc; color: #334155; font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; }
@media (max-width: 640px) {
  .panel-head { align-items: flex-start; flex-wrap: wrap; }
  .panel-actions { flex-basis: 100%; }
  .variable-grid { grid-template-columns: 1fr; }
}
</style>
