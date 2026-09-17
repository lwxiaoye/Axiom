<template>
  <div class="options-editor">
    <div v-for="(option, index) in options" :key="option.key" class="options-row">
      <span class="options-index">{{ index + 1 }}</span>
      <a-input v-model:value="option.value" size="small" placeholder="选项文案，如：确认" class="options-input" />
      <a-button
        size="small"
        type="text"
        class="options-remove"
        :disabled="options.length <= 1"
        @click="removeOption(index)"
      >
        <DeleteOutlined />
      </a-button>
    </div>
    <a-button size="small" type="dashed" block @click="addOption">
      <PlusOutlined />
      添加选项
    </a-button>
    <p class="options-tip">每个选项对应节点右侧一个分支出口；运行时等待用户点选后继续</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import type { FlowNodeInputItemType } from '../../../core/type';
import { ensureArrayValue, getNanoid } from '../../../core/utils';

const props = defineProps<{ input: FlowNodeInputItemType }>();

ensureArrayValue(props.input);

const options = computed<{ key: string; value: string }[]>(() => props.input.value);

function addOption() {
  options.value.push({ key: getNanoid(6), value: '' });
}

function removeOption(index: number) {
  if (options.value.length <= 1) return;
  options.value.splice(index, 1);
}
</script>

<style scoped lang="less">
.options-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.options-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.options-index {
  flex: 0 0 18px;
  color: #94a3b8;
  font-size: 12px;
  text-align: center;
}

.options-input {
  flex: 1;
}

.options-remove {
  color: #94a3b8;
}

.options-tip {
  margin: 2px 0 0;
  color: #94a3b8;
  font-size: 11px;
}
</style>
