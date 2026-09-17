<template>
  <div class="extract-editor">
    <div v-for="(field, index) in fields" :key="index" class="extract-item">
      <div class="extract-row">
        <a-input v-model:value="field.key" size="small" placeholder="字段 key" class="extract-key" @change="syncOutputs" />
        <a-select
          size="small"
          class="extract-type"
          popup-class-name="wf-node-select-popup"
          :value="field.valueType || WorkflowIOValueTypeEnum.string"
          :options="valueTypeOptions"
          @change="(value) => setFieldType(field, value as WorkflowIOValueTypeEnum)"
        />
        <a-checkbox v-model:checked="field.required" class="extract-required">必填</a-checkbox>
        <a-button size="small" type="text" class="extract-remove" @click="removeField(index)">
          <DeleteOutlined />
        </a-button>
      </div>
      <div class="extract-row">
        <a-input v-model:value="field.desc" size="small" placeholder="字段描述（提取要求）" class="extract-desc" @change="syncOutputs" />
        <a-input v-model:value="field.defaultValue" size="small" placeholder="默认值（可选）" class="extract-default" />
      </div>
      <a-textarea
        v-model:value="field.enum"
        size="small"
        :rows="1"
        :auto-size="{ minRows: 1, maxRows: 3 }"
        placeholder="枚举候选值（可选，每行一个；提取结果必须命中其一）"
        class="extract-enum"
      />
    </div>
    <a-button size="small" type="dashed" block @click="addField">
      <PlusOutlined />
      添加提取字段
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { FlowNodeOutputTypeEnum, NodeOutputKeyEnum, WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { ContextExtractAgentItemType, FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { ensureArrayValue, setNodeOutputs } from '../../../core/utils';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

ensureArrayValue(props.input);

const fields = computed<ContextExtractAgentItemType[]>(() => props.input.value);

/** 蓝本字段类型集合（提取目标类型，contextExtract/type.ts） */
const valueTypeOptions = [
  { value: WorkflowIOValueTypeEnum.string, label: '字符串' },
  { value: WorkflowIOValueTypeEnum.number, label: '数字' },
  { value: WorkflowIOValueTypeEnum.boolean, label: '布尔' },
  { value: WorkflowIOValueTypeEnum.arrayString, label: '字符串数组' },
  { value: WorkflowIOValueTypeEnum.arrayNumber, label: '数字数组' },
  { value: WorkflowIOValueTypeEnum.arrayBoolean, label: '布尔数组' },
];

function setFieldType(field: ContextExtractAgentItemType, valueType: WorkflowIOValueTypeEnum) {
  field.valueType = valueType;
  syncOutputs();
}

function addField() {
  fields.value.push({ key: '', desc: '', required: false, valueType: WorkflowIOValueTypeEnum.string });
  syncOutputs();
}

function removeField(index: number) {
  fields.value.splice(index, 1);
  syncOutputs();
}

/** 蓝本：每个提取字段生成一个动态输出，valueType 跟随字段类型 */
function syncOutputs() {
  const staticOutputs = props.node.outputs.filter(
    (output) =>
      output.key === NodeOutputKeyEnum.success ||
      output.key === NodeOutputKeyEnum.contextExtractFields ||
      output.key === NodeOutputKeyEnum.errorText
  );
  const fieldOutputs = fields.value
    .filter((field) => field.key.trim())
    .map((field) => ({
      id: field.key,
      key: field.key,
      label: `提取结果-${field.key}`,
      type: FlowNodeOutputTypeEnum.static,
      valueType: field.valueType || WorkflowIOValueTypeEnum.string,
    }));
  setNodeOutputs(props.node, [...fieldOutputs, ...staticOutputs]);
}
</script>

<style scoped lang="less">
.extract-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.extract-item {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #fafbfc;
}

.extract-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.extract-key {
  flex: 1;
  min-width: 0;
}

.extract-type {
  width: 90px;
  flex-shrink: 0;
}

.extract-desc {
  flex: 1.4;
  min-width: 0;
}

.extract-default {
  flex: 1;
  min-width: 0;
}

.extract-enum {
  font-size: 12px;
}

.extract-required {
  flex-shrink: 0;
  font-size: 12px;
}

.extract-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}
</style>
