<template>
  <div class="dynamic-inputs">
    <div v-for="(row, index) in rows" :key="index" class="dynamic-item">
      <div class="dynamic-row">
        <a-input v-model:value="row.key" size="small" placeholder="变量名" class="dynamic-key" @change="commit" />
        <a-select
          v-model:value="row.valueType"
          size="small"
          class="dynamic-type"
          popup-class-name="wf-node-select-popup"
          :options="valueTypeOptions"
          @change="commit"
        />
        <a-button size="small" type="text" class="dynamic-remove" @click="removeRow(index)">
          <DeleteOutlined />
        </a-button>
      </div>
      <div class="dynamic-row">
        <ReferencePicker
          class="dynamic-ref"
          :node="node"
          :value="row.value"
          allow-clear
          placeholder="引用上游输出"
          @update:value="
            (value) => {
              row.value = value;
              commit();
            }
          "
        />
        <a-input
          v-if="showDefaultValue"
          v-model:value="row.defaultValue"
          size="small"
          class="dynamic-default"
          placeholder="默认值（未引用时生效）"
          @change="commit"
        />
      </div>
    </div>
    <a-button size="small" type="dashed" block @click="addRow">
      <PlusOutlined />
      添加自定义输入
    </a-button>
    <p class="dynamic-tip">{{ tipText }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { replaceDynamicInputs, setInputValue } from '../../../core/utils';
import ReferencePicker from './ReferencePicker.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

type Row = { key: string; value?: [string, string]; valueType?: string; defaultValue?: any };

const rows = ref<Row[]>(readRows());

watch(
  () => props.node.nodeId,
  () => {
    rows.value = readRows();
  }
);

/** 蓝本 customInputConfig.showDefaultValue：允许为动态项直填字面默认值 */
const showDefaultValue = computed(() => !!props.input.customInputConfig?.showDefaultValue);

const tipText = computed(() => props.input.description || '在拼接文本中用 {{变量名}} 引用');

const valueTypeOptions = computed(() => {
  const list = props.input.customInputConfig?.selectValueTypeList?.length
    ? props.input.customInputConfig.selectValueTypeList
    : Object.values(WorkflowIOValueTypeEnum);
  return list.map((value) => ({ label: value, value }));
});

/** 蓝本 D-6：动态项是节点 inputs 里 canEdit=true 的平级项；兼容旧稿存于本输入 value 的行数组 */
function readRows(): Row[] {
  const flat = (props.node.inputs || [])
    .filter((item) => item.canEdit)
    .map((item) => ({
      key: item.key || '',
      value: item.value,
      valueType: item.valueType || WorkflowIOValueTypeEnum.any,
      defaultValue: item.defaultValue,
    }));
  if (flat.length) return flat;
  const legacy = Array.isArray(props.input.value) ? props.input.value : [];
  return legacy.map((item: any) => ({
    key: item.key || '',
    value: item.value,
    valueType: item.valueType || WorkflowIOValueTypeEnum.any,
    defaultValue: item.defaultValue,
  }));
}

function commit() {
  replaceDynamicInputs(props.node, rows.value, props.input.key);
  // 旧稿迁移：行数据不再存 addInputParam value
  if (Array.isArray(props.input.value)) {
    setInputValue(props.input, undefined);
  }
}

function addRow() {
  rows.value.push({ key: '', value: undefined, valueType: WorkflowIOValueTypeEnum.string, defaultValue: undefined });
}

function removeRow(index: number) {
  rows.value.splice(index, 1);
  commit();
}
</script>

<style scoped lang="less">
.dynamic-inputs {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dynamic-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px;
  border: 1px dashed #e2e8f0;
  border-radius: 6px;
}

.dynamic-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.dynamic-key {
  flex: 1;
  min-width: 0;
}

.dynamic-type {
  width: 118px;
  flex-shrink: 0;
}

.dynamic-ref {
  flex: 1;
  min-width: 0;
}

.dynamic-default {
  width: 42%;
  flex-shrink: 0;
}

.dynamic-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}

.dynamic-tip {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
}
</style>
