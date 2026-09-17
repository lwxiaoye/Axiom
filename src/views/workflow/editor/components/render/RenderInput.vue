<template>
  <div v-if="currentRenderType !== FlowNodeInputTypeEnum.hidden" class="render-input nodrag">
    <div v-if="input.label" class="input-label-row">
      <span class="input-label">
        {{ input.label }}
        <em v-if="input.required" class="required">*</em>
      </span>
      <a-tooltip v-if="input.description" :title="input.description">
        <QuestionCircleOutlined class="label-help" />
      </a-tooltip>
      <a-button
        v-if="canDebugPrompt"
        size="small"
        class="prompt-debug-trigger"
        :disabled="readonly"
        @click="promptDebugOpen = true"
      >
        生成
      </a-button>
      <a-tooltip v-if="switchableTypes.length > 1" :title="switchTip">
        <button type="button" class="type-switcher" @click="switchRenderType">
          <SwapOutlined />
        </button>
      </a-tooltip>
    </div>

    <!-- reference -->
    <ReferencePicker
      v-if="currentRenderType === FlowNodeInputTypeEnum.reference"
      :node="node"
      :value="input.value"
      :include-children="input.key === 'loopCustomOutputs'"
      allow-clear
      @update:value="(value) => setInputValue(input, value)"
    />

    <!-- input -->
    <VariableTextarea
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.input"
      :value="stringValue"
      :rows="1"
      single-line
      :placeholder="input.placeholder"
      :groups="textVariableGroups"
      @update:value="(value) => setInputValue(input, value)"
    />

    <!-- textarea（支持 {{变量}} 插入） -->
    <VariableTextarea
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.textarea"
      :value="stringValue"
      :rows="4"
      :placeholder="input.placeholder"
      :max-length="input.maxLength"
      :groups="textVariableGroups"
      @update:value="(value) => setInputValue(input, value)"
    />

    <!-- password：连接串、令牌等敏感字面量；仍可切换为变量引用 -->
    <a-input-password
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.password"
      size="small"
      :value="stringValue"
      :placeholder="input.placeholder"
      autocomplete="new-password"
      @update:value="(value) => setInputValue(input, value)"
    />

    <!-- numberInput -->
    <a-input-number
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.numberInput"
      class="node-number-input"
      size="small"
      style="width: 100%"
      :value="input.value"
      :min="input.min"
      :max="input.max"
      :step="input.step || 1"
      @change="setInputValue(input, $event)"
    />

    <!-- switch -->
    <a-switch
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.switch"
      class="compact-switch"
      size="small"
      :checked="!!input.value"
      @change="setInputValue(input, $event)"
    />

    <!-- select -->
    <a-select
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.select"
      size="small"
      popup-class-name="wf-node-select-popup"
      style="width: 100%"
      :value="input.value"
      :options="input.list || []"
      :show-search="selectSearchable"
      option-filter-prop="label"
      :filter-option="selectFilterOption"
      @change="setInputValue(input, $event)"
    />

    <!-- multipleSelect（蓝本：数组枚举参数） -->
    <a-select
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.multipleSelect"
      size="small"
      mode="multiple"
      popup-class-name="wf-node-select-popup"
      style="width: 100%"
      :value="Array.isArray(input.value) ? input.value : []"
      :options="input.list || []"
      @change="setInputValue(input, $event)"
    />

    <!-- JSONEditor -->
    <div v-else-if="currentRenderType === FlowNodeInputTypeEnum.JSONEditor" class="json-editor">
      <VariableTextarea
        class="json-textarea"
        :value="stringValue"
        :rows="5"
        :placeholder="input.placeholder"
        :groups="textVariableGroups"
        @update:value="(value) => setInputValue(input, value)"
      />
      <span v-if="jsonError" class="json-error">{{ jsonError }}</span>
    </div>

    <!-- settingLLMModel -->
    <LLMModelSetting v-else-if="currentRenderType === FlowNodeInputTypeEnum.settingLLMModel" :node="node" />

    <!-- selectLLMModel -->
    <a-select
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.selectLLMModel"
      size="small"
      popup-class-name="wf-node-select-popup"
      style="width: 100%"
      show-search
      placeholder="选择模型"
      :value="input.value"
      :loading="modelLoading"
      :options="modelSelectOptions"
      @change="setInputValue(input, $event)"
    />

    <!-- selectDataset -->
    <DatasetSelectRender
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.selectDataset"
      :node="node"
      :input="input"
    />

    <!-- addInputParam -->
    <DynamicInputs
      v-else-if="currentRenderType === FlowNodeInputTypeEnum.addInputParam"
      :node="node"
      :input="input"
    />

    <!-- custom：按 input.key 分发节点专属编辑器 -->
    <template v-else-if="currentRenderType === FlowNodeInputTypeEnum.custom">
      <ClassifyAgentsEditor v-if="input.key === NodeInputKeyEnum.agents" :input="input" />
      <SkillRefsEditor v-else-if="input.key === NodeInputKeyEnum.skills" :input="input" />
      <ExtractKeysEditor v-else-if="input.key === NodeInputKeyEnum.extractKeys" :node="node" :input="input" />
      <template v-else-if="input.key === NodeInputKeyEnum.httpHeaders">
        <HeaderAuthConfig :node="node" />
        <KeyValueRows :node="node" :input="input" />
      </template>
      <KeyValueRows
        v-else-if="input.key === NodeInputKeyEnum.httpParams || input.key === NodeInputKeyEnum.httpFormBody"
        :node="node"
        :input="input"
      />
      <QuoteListEditor v-else-if="input.key === NodeInputKeyEnum.datasetQuoteList" :node="node" :input="input" />
      <UserSelectOptionsEditor v-else-if="input.key === NodeInputKeyEnum.userSelectOptions" :input="input" />
      <CodeEditor v-else-if="input.key === 'code'" :node="node" :input="input" />
      <FormInputFieldsEditor v-else-if="input.key === NodeInputKeyEnum.userInputForms" :node="node" :input="input" />
    </template>

    <div v-if="isSqlQueryDbUri" class="db-uri-examples" @mousedown.stop>
      <div class="db-uri-examples-title">配置示例</div>
      <div v-for="example in sqlQueryDbUriExamples" :key="example.label" class="db-uri-example">
        <span>{{ example.label }}</span>
        <code>{{ example.value }}</code>
      </div>
      <div class="db-uri-examples-note">请使用权限范围符合工作流用途的账号；写操作会提交，密码中的特殊字符需要 URL 编码。</div>
    </div>
    <PromptDebugDrawer
      v-if="canDebugPrompt"
      v-model:open="promptDebugOpen"
      :app-id="appId"
      :prompt="stringValue"
      :model="String(nodeInputValue(NodeInputKeyEnum.aiModel) || '')"
      :variables="graph.chatConfig?.variables || []"
      :temperature="numberInputValue(NodeInputKeyEnum.aiChatTemperature)"
      :max-token="numberInputValue(NodeInputKeyEnum.aiChatMaxToken)"
      :top-p="numberInputValue(NodeInputKeyEnum.aiChatTopP)"
      :stop-sign="String(nodeInputValue(NodeInputKeyEnum.aiChatStopSign) || '')"
      :response-format="String(nodeInputValue(NodeInputKeyEnum.aiChatResponseFormat) || '')"
      :json-schema="String(nodeInputValue(NodeInputKeyEnum.aiChatJsonSchema) || '')"
      @apply="setInputValue(input, $event)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { QuestionCircleOutlined, SwapOutlined } from '@ant-design/icons-vue';
import { FlowNodeInputTypeEnum, NodeInputKeyEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import {
  buildTextVariableGroups,
  isReferenceValue,
  setInputRenderTypeIndex,
  setInputValue,
} from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';
import VariableTextarea from '../VariableTextarea.vue';
import ReferencePicker from './ReferencePicker.vue';
import LLMModelSetting from './LLMModelSetting.vue';
import DatasetSelectRender from './DatasetSelectRender.vue';
import KeyValueRows from './KeyValueRows.vue';
import HeaderAuthConfig from './HeaderAuthConfig.vue';
import DynamicInputs from './DynamicInputs.vue';
import ClassifyAgentsEditor from '../custom/ClassifyAgentsEditor.vue';
import ExtractKeysEditor from '../custom/ExtractKeysEditor.vue';
import QuoteListEditor from '../custom/QuoteListEditor.vue';
import UserSelectOptionsEditor from '../custom/UserSelectOptionsEditor.vue';
import FormInputFieldsEditor from '../custom/FormInputFieldsEditor.vue';
import CodeEditor from '../custom/CodeEditor.vue';
import SkillRefsEditor from '../custom/SkillRefsEditor.vue';
import PromptDebugDrawer from '../../../components/PromptDebugDrawer.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

const { graph, appId, modelOptions, modelLoading, readonly } = useEditorContext();
const promptDebugOpen = ref(false);

const sqlQueryDbUriExamples = [
  {
    label: 'MySQL',
    value: 'mysql+pymysql://sql_user:password@127.0.0.1:3306/ai_boot?charset=utf8mb4',
  },
  {
    label: 'PostgreSQL',
    value: 'postgresql+psycopg://sql_user:password@127.0.0.1:5432/ai_runtime',
  },
  { label: 'SQLite · Windows', value: 'sqlite:///G:/data/example.sqlite' },
  { label: 'SQLite · Linux', value: 'sqlite:////opt/data/example.sqlite' },
];
const isSqlQueryDbUri = computed(
  () =>
    props.node.toolConfig?.systemTool?.toolId === 'builtin.sql_query' &&
    props.input.key === 'db_uri'
);

/** hidden 不参与切换（hidden 输入由弹窗类渲染器代管） */
const switchableTypes = computed(() =>
  props.input.renderTypeList.filter((type) => type !== FlowNodeInputTypeEnum.hidden)
);

function effectiveRenderTypeIndex() {
  if (isReferenceValue(props.input.value)) {
    const referenceIndex = props.input.renderTypeList.indexOf(FlowNodeInputTypeEnum.reference);
    if (referenceIndex >= 0) return referenceIndex;
  }
  return props.input.selectedTypeIndex || 0;
}

const currentRenderType = computed(() => {
  const index = effectiveRenderTypeIndex();
  return props.input.renderTypeList[index] || props.input.renderTypeList[0];
});

const switchTip = computed(() => {
  const next = nextRenderType();
  return next === FlowNodeInputTypeEnum.reference ? '切换为变量引用' : '切换为手动输入';
});

function nextRenderType() {
  const index = effectiveRenderTypeIndex();
  const nextIndex = (index + 1) % props.input.renderTypeList.length;
  return props.input.renderTypeList[nextIndex];
}

function switchRenderType() {
  const index = effectiveRenderTypeIndex();
  setInputRenderTypeIndex(props.input, (index + 1) % props.input.renderTypeList.length);
}

const stringValue = computed(() => {
  if (isReferenceValue(props.input.value)) return '';
  return props.input.value == null ? '' : String(props.input.value);
});

const canDebugPrompt = computed(() =>
  props.input.key === NodeInputKeyEnum.aiSystemPrompt &&
  props.input.label === '提示词' &&
  !isReferenceValue(props.input.value) &&
  Boolean(appId.value)
);

function nodeInputValue(key: string) {
  return props.node.inputs.find((item) => item.key === key)?.value;
}

function numberInputValue(key: string): number | undefined {
  const value = nodeInputValue(key);
  return typeof value === 'number' ? value : undefined;
}

const jsonError = computed(() => {
  const value = stringValue.value.trim();
  if (!value) return '';
  try {
    JSON.parse(value.replace(/\{\{[^}]+\}\}/g, '"_var_"'));
    return '';
  } catch {
    return 'JSON 格式无效（变量占位符除外）';
  }
});

const modelSelectOptions = computed(() =>
  modelOptions.value.map((item) => ({ label: item.label || item.value, value: item.value }))
);

const selectSearchable = computed(() => {
  if (props.input.searchable) return true;
  if (props.input.key === 'from_timezone' || props.input.key === 'to_timezone') return true;
  return (props.input.list || []).length > 20;
});

function selectFilterOption(searchText: string, option: any) {
  const keyword = String(searchText || '').trim().toLowerCase();
  if (!keyword) return true;
  return [option?.label, option?.value].some((value) =>
    String(value || '').toLowerCase().includes(keyword)
  );
}

const textVariableGroups = computed(() =>
  buildTextVariableGroups(props.node.nodeId, graph.value.nodes, graph.value.edges, graph.value.chatConfig)
);

</script>

<style scoped lang="less">
.render-input {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.input-label-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.input-label {
  font-size: 14px;
  font-weight: 600;
  color: #354052;

  .required {
    color: #dc2626;
    font-style: normal;
    margin-left: 2px;
  }
}

.label-help {
  color: #94a3b8;
  font-size: 12px;
}

.db-uri-examples {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  cursor: text;
  user-select: text;
  -webkit-user-select: text;
}

.db-uri-examples-title {
  color: #475569;
  font-size: 12px;
  font-weight: 600;
}

.db-uri-example {
  display: grid;
  gap: 2px;
  user-select: text;
  -webkit-user-select: text;

  span {
    color: #64748b;
    font-size: 11px;
  }

  code {
    color: #334155;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 10px;
    line-height: 1.45;
    overflow-wrap: anywhere;
    user-select: text;
    -webkit-user-select: text;
  }
}

.db-uri-examples-note {
  color: #64748b;
  font-size: 11px;
  line-height: 1.5;
}

.type-switcher {
  margin-left: auto;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
  line-height: 1;

  &:hover {
    background: #eef2ff;
    color: #4f46e5;
  }
}

.json-editor {
  display: flex;
  flex-direction: column;
  gap: 4px;

  .json-textarea :deep(.variable-textarea) {
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 12px;
  }

  .json-error {
    font-size: 12px;
    color: #d97706;
  }
}

.compact-switch {
  align-self: flex-start;
  width: auto;
}

:deep(.node-number-input.ant-input-number-sm) {
  display: inline-flex;
  height: 34px;
  align-items: center;
}

.prompt-debug-trigger {
  color: #2563eb;
  border-color: #bfdbfe;
  background: #eff6ff;
}

:deep(.node-number-input.ant-input-number-sm .ant-input-number-input-wrap) {
  display: flex;
  height: 100%;
  flex: 1;
  align-items: center;
}

:deep(.node-number-input.ant-input-number-sm .ant-input-number-input) {
  height: 32px;
  padding-top: 0;
  padding-bottom: 0;
  line-height: 32px;
}

</style>
