<template>
  <div class="dynamic-outputs" @mousedown.stop @click.stop>
    <div class="outputs-header">
      <span class="outputs-label">{{ anchor.label || '输出字段提取' }}</span>
      <a-tooltip v-if="anchor.description" :title="anchor.description">
        <QuestionCircleOutlined class="outputs-help" />
      </a-tooltip>
    </div>
    <div v-for="(row, index) in rows" :key="index" class="output-edit-row">
      <a-input
        v-model:value="row.key"
        size="small"
        class="output-key"
        :placeholder="keyPlaceholder"
        @change="commit"
      />
      <a-select
        v-model:value="row.valueType"
        size="small"
        class="output-type"
        popup-class-name="wf-node-select-popup"
        :options="valueTypeOptions"
        @change="commit"
      />
      <a-button size="small" type="text" class="output-remove" @click="removeRow(index)">
        <DeleteOutlined />
      </a-button>
    </div>
    <a-button size="small" type="dashed" block @click="addRow">
      <PlusOutlined />
      添加输出字段
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { DeleteOutlined, PlusOutlined, QuestionCircleOutlined } from '@ant-design/icons-vue';
import { FlowNodeOutputTypeEnum, FlowNodeTypeEnum, NodeOutputKeyEnum, WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { FlowNodeOutputItemType, StoreNodeItemType } from '../../../core/type';
import { setNodeOutputs } from '../../../core/utils';

const props = defineProps<{
  node: StoreNodeItemType;
  anchor: FlowNodeOutputItemType;
}>();

type Row = { key: string; valueType?: string };

const rows = ref<Row[]>(readRows());

watch(
  () => props.node.nodeId,
  () => {
    rows.value = readRows();
  }
);

/** HTTP 节点：输出 key 即 JSONPath 提取路径（蓝本 http_extract_output_description） */
const keyPlaceholder = computed(() =>
  props.node.flowNodeType === FlowNodeTypeEnum.httpRequest468 ? '字段名或 JSONPath（如 data.id）' : '变量名（对应 return 的 key）'
);

const valueTypeOptions = computed(() => {
  const list = props.anchor.customFieldConfig?.selectValueTypeList?.length
    ? props.anchor.customFieldConfig.selectValueTypeList
    : Object.values(WorkflowIOValueTypeEnum);
  return list.map((value) => ({ label: value, value }));
});

function readRows(): Row[] {
  return (props.node.outputs || [])
    .filter((output) => output.type === FlowNodeOutputTypeEnum.dynamic && output.key !== NodeOutputKeyEnum.addOutputParam)
    .map((output) => ({ key: output.key || '', valueType: output.valueType || WorkflowIOValueTypeEnum.string }));
}

function commit() {
  const kept = (props.node.outputs || []).filter(
    (output) => !(output.type === FlowNodeOutputTypeEnum.dynamic && output.key !== NodeOutputKeyEnum.addOutputParam)
  );
  const anchorIndex = kept.findIndex((output) => output.key === NodeOutputKeyEnum.addOutputParam);
  const dynamicOutputs = rows.value
    .filter((row) => row.key.trim())
    .map((row) => ({
      id: row.key.trim(),
      key: row.key.trim(),
      label: row.key.trim(),
      type: FlowNodeOutputTypeEnum.dynamic,
      valueType: (row.valueType as WorkflowIOValueTypeEnum) || WorkflowIOValueTypeEnum.string,
    }));
  const index = anchorIndex >= 0 ? anchorIndex + 1 : kept.length;
  setNodeOutputs(props.node, [...kept.slice(0, index), ...dynamicOutputs, ...kept.slice(index)]);
}

function addRow() {
  rows.value.push({ key: '', valueType: WorkflowIOValueTypeEnum.string });
}

function removeRow(index: number) {
  rows.value.splice(index, 1);
  commit();
}
</script>

<style scoped lang="less">
.dynamic-outputs {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0 2px;
  border-top: 1px dashed #e2e8f0;
}

.outputs-header {
  display: flex;
  align-items: center;
  gap: 4px;
}

.outputs-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.outputs-help {
  font-size: 12px;
  color: #94a3b8;
}

.output-edit-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.output-key {
  flex: 1;
  min-width: 0;
}

.output-type {
  width: 112px;
  flex-shrink: 0;
}

.output-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}
</style>
