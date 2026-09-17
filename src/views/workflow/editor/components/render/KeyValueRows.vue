<template>
  <div class="kv-rows">
    <div v-for="(row, index) in rows" :key="index" class="kv-row">
      <a-input v-model:value="row.key" size="small" placeholder="key" class="kv-key" @change="commit" />
      <VariableTextarea
        :value="row.value"
        :rows="1"
        single-line
        placeholder="value，支持 {{变量}}"
        class="kv-value"
        :groups="textVariableGroups"
        @update:value="updateValue(index, $event)"
      />
      <a-button size="small" type="text" class="kv-remove" @click="removeRow(index)">
        <DeleteOutlined />
      </a-button>
    </div>
    <a-button size="small" type="dashed" block @click="addRow">
      <PlusOutlined />
      添加一行
    </a-button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { buildTextVariableGroups, setInputValue } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';
import VariableTextarea from '../VariableTextarea.vue';

const props = defineProps<{ node: StoreNodeItemType; input: FlowNodeInputItemType }>();
const { graph } = useEditorContext();

type Row = { key: string; type: string; value: string };

const rows = ref<Row[]>(readRows());
const textVariableGroups = computed(() =>
  buildTextVariableGroups(props.node.nodeId, graph.value.nodes, graph.value.edges, graph.value.chatConfig)
);

watch(
  () => props.input,
  () => {
    rows.value = readRows();
  }
);

function readRows(): Row[] {
  const value = Array.isArray(props.input.value) ? props.input.value : [];
  return value.map((item: any) => ({ key: item.key || '', type: item.type || 'string', value: item.value ?? '' }));
}

function commit() {
  setInputValue(
    props.input,
    rows.value.filter((row) => row.key.trim()).map((row) => ({ key: row.key.trim(), type: row.type, value: row.value }))
  );
}

function updateValue(index: number, value: string) {
  rows.value[index].value = value;
  commit();
}

function addRow() {
  rows.value.push({ key: '', type: 'string', value: '' });
}

function removeRow(index: number) {
  rows.value.splice(index, 1);
  commit();
}
</script>

<style scoped lang="less">
.kv-rows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.kv-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.kv-key {
  width: 38%;
}

.kv-value {
  flex: 1;
}

.kv-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover {
    color: #dc2626;
  }
}
</style>
