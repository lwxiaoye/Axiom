<template>
  <div class="quote-editor">
    <div v-for="(item, index) in rows" :key="index" class="quote-row">
      <span class="quote-index">{{ index + 1 }}</span>
      <ReferencePicker
        class="quote-ref"
        :node="node"
        :value="item.value"
        placeholder="选择知识库引用输出"
        :value-type-filter="[WorkflowIOValueTypeEnum.datasetQuote]"
        @update:value="
          (value) => {
            item.value = value;
            commit();
          }
        "
      />
      <a-button size="small" type="text" class="quote-remove" @click="removeRow(index)">
        <DeleteOutlined />
      </a-button>
    </div>
    <a-button size="small" type="dashed" block @click="addRow">
      <PlusOutlined />
      添加引用来源
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import { WorkflowIOValueTypeEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { setInputValue } from '../../../core/utils';
import ReferencePicker from '../render/ReferencePicker.vue';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

type Row = { value?: [string, string] };

const rows = ref<Row[]>(readRows());

watch(
  () => props.input,
  () => {
    rows.value = readRows();
  }
);

function readRows(): Row[] {
  const value = Array.isArray(props.input.value) ? props.input.value : [];
  const list = value.map((item: any) => ({ value: item }));
  return list.length ? list : [{ value: undefined }];
}

function commit() {
  setInputValue(
    props.input,
    rows.value.map((row) => row.value).filter(Boolean)
  );
}

function addRow() {
  rows.value.push({ value: undefined });
}

function removeRow(index: number) {
  rows.value.splice(index, 1);
  if (!rows.value.length) rows.value.push({ value: undefined });
  commit();
}
</script>

<style scoped lang="less">
.quote-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.quote-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.quote-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #ecfdf5;
  color: #047857;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.quote-ref {
  flex: 1;
  min-width: 0;
}

.quote-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}
</style>
