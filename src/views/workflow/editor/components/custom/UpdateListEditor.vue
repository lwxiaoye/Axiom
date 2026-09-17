<template>
  <div class="update-editor">
    <div v-for="(item, index) in items" :key="index" class="update-item">
      <div class="update-row">
        <span class="update-label">变量</span>
        <ReferencePicker
          class="update-target"
          :node="node"
          :value="item.variable"
          placeholder="选择要更新的变量"
          @update:value="(value) => (item.variable = value)"
        />
        <a-button size="small" type="text" class="update-remove" @click="removeItem(index)">
          <DeleteOutlined />
        </a-button>
      </div>
      <div class="update-row">
        <span class="update-label">类型</span>
        <a-select
          size="small"
          class="update-type"
          popup-class-name="wf-node-select-popup"
          :value="item.valueType || WorkflowIOValueTypeEnum.string"
          :options="valueTypeOptions"
          @change="(value) => setValueType(item, value as WorkflowIOValueTypeEnum)"
        />
        <!-- 蓝本：按目标类型切换运算模式（仅 input 模式生效） -->
        <template v-if="item.renderType === FlowNodeInputTypeEnum.input">
          <a-select
            v-if="item.valueType === WorkflowIOValueTypeEnum.number"
            size="small"
            class="update-op"
            popup-class-name="wf-node-select-popup"
            :value="item.numberOperator || '='"
            :options="numberOperatorOptions"
            @change="(value) => (item.numberOperator = value as TUpdateListItem['numberOperator'])"
          />
          <a-select
            v-else-if="item.valueType === WorkflowIOValueTypeEnum.boolean"
            size="small"
            class="update-op"
            popup-class-name="wf-node-select-popup"
            :value="item.booleanMode || 'true'"
            :options="booleanModeOptions"
            @change="(value) => (item.booleanMode = value as TUpdateListItem['booleanMode'])"
          />
          <a-select
            v-else-if="isArrayType(item.valueType)"
            size="small"
            class="update-op"
            popup-class-name="wf-node-select-popup"
            :value="item.arrayMode || 'equal'"
            :options="arrayModeOptions"
            @change="(value) => (item.arrayMode = value as TUpdateListItem['arrayMode'])"
          />
        </template>
      </div>
      <div class="update-row">
        <span class="update-label">值</span>
        <a-radio-group v-model:value="item.renderType" size="small" class="update-mode">
          <a-radio-button :value="FlowNodeInputTypeEnum.input">输入</a-radio-button>
          <a-radio-button :value="FlowNodeInputTypeEnum.reference">引用</a-radio-button>
        </a-radio-group>
        <a-input
          v-if="item.renderType === FlowNodeInputTypeEnum.input && !hideValueInput(item)"
          class="update-value"
          size="small"
          :placeholder="valuePlaceholder(item)"
          :value="item.value?.[1] ?? ''"
          @change="item.value = ['', ($event.target as HTMLInputElement).value]"
        />
        <span
          v-else-if="item.renderType === FlowNodeInputTypeEnum.input"
          class="update-value update-no-input"
        >
          {{ item.booleanMode ? '按模式写入，无需输入值' : '清空数组，无需输入值' }}
        </span>
        <ReferencePicker
          v-else
          class="update-value"
          :node="node"
          :value="item.value"
          placeholder="引用上游输出"
          @update:value="(value) => (item.value = value)"
        />
      </div>
    </div>
    <a-button size="small" type="dashed" block @click="addItem">
      <PlusOutlined />
      添加更新项
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { FlowNodeInputTypeEnum, WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType, TUpdateListItem } from '../../../core/type';
import { ensureArrayValue } from '../../../core/utils';
import ReferencePicker from '../render/ReferencePicker.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

ensureArrayValue(props.input);

const items = computed<TUpdateListItem[]>(() => props.input.value);

const valueTypeOptions = [
  { value: WorkflowIOValueTypeEnum.string, label: '字符串' },
  { value: WorkflowIOValueTypeEnum.number, label: '数字' },
  { value: WorkflowIOValueTypeEnum.boolean, label: '布尔' },
  { value: WorkflowIOValueTypeEnum.object, label: '对象' },
  { value: WorkflowIOValueTypeEnum.arrayString, label: 'Array<String>' },
  { value: WorkflowIOValueTypeEnum.arrayNumber, label: 'Array<Number>' },
  { value: WorkflowIOValueTypeEnum.arrayObject, label: 'Array<Object>' },
];

const numberOperatorOptions = [
  { value: '=', label: '= 赋值' },
  { value: '+', label: '+ 加' },
  { value: '-', label: '- 减' },
  { value: '*', label: '× 乘' },
  { value: '/', label: '÷ 除（除零保留旧值）' },
];

const booleanModeOptions = [
  { value: 'true', label: '置为 true' },
  { value: 'false', label: '置为 false' },
  { value: 'negate', label: '取反' },
];

const arrayModeOptions = [
  { value: 'equal', label: '整组替换' },
  { value: 'append', label: '追加元素' },
  { value: 'clear', label: '清空' },
];

function isArrayType(valueType?: string) {
  return !!valueType && valueType.startsWith('array');
}

/** 切换目标类型时清掉与新类型不匹配的运算模式字段（蓝本行为） */
function setValueType(item: TUpdateListItem, valueType: WorkflowIOValueTypeEnum) {
  item.valueType = valueType;
  if (valueType !== WorkflowIOValueTypeEnum.number) delete item.numberOperator;
  if (valueType !== WorkflowIOValueTypeEnum.boolean) delete item.booleanMode;
  if (!isArrayType(valueType)) delete item.arrayMode;
}

/** 布尔置值/取反与数组清空不需要输入值 */
function hideValueInput(item: TUpdateListItem) {
  if (item.valueType === WorkflowIOValueTypeEnum.boolean && item.booleanMode) return true;
  if (item.arrayMode === 'clear') return true;
  return false;
}

function valuePlaceholder(item: TUpdateListItem) {
  if (item.numberOperator && item.numberOperator !== '=') return '运算数';
  if (item.arrayMode === 'append') return '追加的元素值';
  return '新值';
}

function addItem() {
  items.value.push({
    variable: undefined,
    value: ['', ''],
    valueType: WorkflowIOValueTypeEnum.string,
    renderType: FlowNodeInputTypeEnum.input,
  });
}

function removeItem(index: number) {
  items.value.splice(index, 1);
}
</script>

<style scoped lang="less">
.update-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.update-item {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #fafbfc;
}

.update-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.update-label {
  width: 32px;
  font-size: 12px;
  color: #64748b;
  flex-shrink: 0;
}

.update-target,
.update-value {
  flex: 1;
  min-width: 0;
}

.update-type {
  width: 130px;
  flex-shrink: 0;
}

.update-op {
  flex: 1;
  min-width: 0;
}

.update-no-input {
  font-size: 12px;
  color: #94a3b8;
}

.update-mode {
  flex-shrink: 0;
}

.update-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}
</style>
