<template>
  <div class="forms-editor">
    <div v-for="(fieldItem, index) in fields" :key="index" class="forms-item">
      <div class="forms-row">
        <a-input v-model:value="fieldItem.label" size="small" placeholder="标签" class="forms-label" @change="syncOutputs" />
        <a-input v-model:value="fieldItem.key" size="small" placeholder="key" class="forms-key" @change="syncOutputs" />
        <a-select
          v-model:value="fieldItem.type"
          size="small"
          class="forms-type"
          popup-class-name="wf-node-select-popup"
          :options="typeOptions"
          @change="onTypeChange(fieldItem)"
        />
        <a-checkbox v-model:checked="fieldItem.required" class="forms-required">必填</a-checkbox>
        <a-button size="small" type="text" class="forms-remove" @click="removeField(index)">
          <DeleteOutlined />
        </a-button>
      </div>
      <div class="forms-row">
        <a-input v-model:value="fieldItem.description" size="small" placeholder="说明（可选）" class="forms-desc" />
        <a-textarea
          v-if="fieldItem.type === 'textarea'"
          v-model:value="fieldItem.defaultValue"
          size="small"
          :rows="1"
          placeholder="默认值（可选）"
          class="forms-default forms-default-textarea"
        />
        <a-input
          v-else-if="!isChoiceType(fieldItem.type)"
          v-model:value="fieldItem.defaultValue"
          size="small"
          placeholder="默认值（可选）"
          class="forms-default"
        />
      </div>
      <!-- 约束（蓝本 InputFormEditModal）：文本长度上限 / 数字范围 -->
      <div v-if="isTextType(fieldItem.type)" class="forms-row">
        <a-input-number
          v-model:value="fieldItem.maxLength"
          size="small"
          class="forms-limit forms-number-control"
          :min="1"
          placeholder="最大长度（可选）"
        />
      </div>
      <div v-else-if="fieldItem.type === 'numberInput'" class="forms-row">
        <a-input-number
          v-model:value="fieldItem.min"
          size="small"
          class="forms-limit forms-number-control"
          placeholder="最小值（可选）"
        />
        <a-input-number
          v-model:value="fieldItem.max"
          size="small"
          class="forms-limit forms-number-control"
          placeholder="最大值（可选）"
        />
      </div>
      <!-- 选择类字段：候选项（每行一个） -->
      <a-textarea
        v-if="isChoiceType(fieldItem.type)"
        size="small"
        :rows="3"
        placeholder="候选项，每行一个"
        class="forms-options"
        :value="(fieldItem.list || []).map((o) => o.value).join('\n')"
        @change="setOptions(fieldItem, ($event.target as HTMLTextAreaElement).value)"
      />
    </div>
    <a-button size="small" type="dashed" block @click="addField">
      <PlusOutlined />
      添加表单项
    </a-button>
    <p class="forms-tip">运行时暂停等待用户填写；每个字段可被下游单独引用，另有 formInputResult 完整对象</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { FlowNodeOutputTypeEnum, NodeOutputKeyEnum, WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { ensureArrayValue, setNodeOutputs } from '../../../core/utils';

type FormFieldItem = {
  label?: string;
  key: string;
  type?: string;
  required?: boolean;
  description?: string;
  defaultValue?: string;
  valueType?: string;
  maxLength?: number;
  min?: number;
  max?: number;
  list?: { label: string; value: string }[];
};

const props = defineProps<{ node: StoreNodeItemType; input: FlowNodeInputItemType }>();

ensureArrayValue(props.input);

const fields = computed<FormFieldItem[]>(() => props.input.value);

/** 表单输入字段类型：平台侧不再暴露模型选择，长文本使用 textarea。 */
const typeOptions = [
  { label: '文本', value: 'input' },
  { label: '多行文本', value: 'textarea' },
  { label: '密码', value: 'password' },
  { label: '数字', value: 'numberInput' },
  { label: '单选', value: 'select' },
  { label: '多选', value: 'multipleSelect' },
  { label: '开关', value: 'switch' },
  { label: '时间点', value: 'timePointSelect' },
  { label: '时间范围', value: 'timeRangeSelect' },
  { label: '文件', value: 'fileSelect' },
];

/** 蓝本 InputFormEditModal defaultValueType：按输入类型映射字段 valueType */
const VALUE_TYPE_BY_INPUT: Record<string, WorkflowIOValueTypeEnum> = {
  input: WorkflowIOValueTypeEnum.string,
  textarea: WorkflowIOValueTypeEnum.string,
  password: WorkflowIOValueTypeEnum.string,
  numberInput: WorkflowIOValueTypeEnum.number,
  select: WorkflowIOValueTypeEnum.string,
  multipleSelect: WorkflowIOValueTypeEnum.arrayString,
  switch: WorkflowIOValueTypeEnum.boolean,
  timePointSelect: WorkflowIOValueTypeEnum.string,
  timeRangeSelect: WorkflowIOValueTypeEnum.arrayString,
  fileSelect: WorkflowIOValueTypeEnum.arrayString,
};

function isChoiceType(type?: string) {
  return type === 'select' || type === 'multipleSelect';
}

function isTextType(type?: string) {
  return type === 'input' || type === 'textarea' || type === 'password';
}

function onTypeChange(field: FormFieldItem) {
  field.valueType = VALUE_TYPE_BY_INPUT[field.type || 'input'] || WorkflowIOValueTypeEnum.string;
  syncOutputs();
}

function setOptions(field: FormFieldItem, text: string) {
  field.list = text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((value) => ({ label: value, value }));
}

/** 蓝本 NodeFormInput addOutput/replaceOutput/delOutput：每个表单字段一个可引用的 static 输出 */
function syncOutputs() {
  const kept = (props.node.outputs || []).filter(
    (output) => output.key === NodeOutputKeyEnum.formInputResult || output.type === FlowNodeOutputTypeEnum.error
  );
  const fieldOutputs = fields.value
    .filter((field) => field.key?.trim())
    .map((field) => ({
      id: field.key,
      key: field.key,
      label: field.label || field.key,
      type: FlowNodeOutputTypeEnum.static,
      valueType:
        (field.valueType as WorkflowIOValueTypeEnum) ||
        VALUE_TYPE_BY_INPUT[field.type || 'input'] ||
        WorkflowIOValueTypeEnum.string,
    }));
  setNodeOutputs(props.node, [...fieldOutputs, ...kept]);
}

function addField() {
  fields.value.push({
    label: '',
    key: `field${fields.value.length + 1}`,
    type: 'textarea',
    valueType: WorkflowIOValueTypeEnum.string,
    required: false,
  });
  syncOutputs();
}

function removeField(index: number) {
  fields.value.splice(index, 1);
  syncOutputs();
}
</script>

<style scoped lang="less">
.forms-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.forms-item {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #fafbfc;
}

.forms-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.forms-label {
  flex: 1 1 30%;
}

.forms-key {
  flex: 1 1 26%;
}

.forms-type {
  flex: 0 0 96px;
}

.forms-desc {
  flex: 1.2;
  min-width: 0;
}

.forms-default {
  flex: 1;
  min-width: 0;
}

:deep(.forms-default-textarea.ant-input-sm) {
  height: 24px;
  min-height: 24px;
  font-size: 12px;
  line-height: 22px;
  padding: 0 7px;
  border-radius: 8px;
  overflow: hidden;
  resize: none;
}

.forms-limit {
  flex: 1;
  min-width: 0;
}

:deep(.forms-number-control.ant-input-number-sm) {
  height: 24px;
  display: inline-flex;
  align-items: center;
}

:deep(.forms-number-control.ant-input-number-sm .ant-input-number-input-wrap) {
  display: flex;
  height: 100%;
  align-items: center;
  flex: 1;
}

:deep(.forms-number-control.ant-input-number-sm .ant-input-number-input) {
  height: 22px;
  line-height: 22px;
  padding-top: 0;
  padding-bottom: 0;
}

.forms-options {
  font-size: 12px;
}

.forms-required {
  flex: 0 0 auto;
  font-size: 12px;
}

.forms-remove {
  color: #94a3b8;
}

.forms-tip {
  margin: 2px 0 0;
  color: #94a3b8;
  font-size: 11px;
}
</style>
