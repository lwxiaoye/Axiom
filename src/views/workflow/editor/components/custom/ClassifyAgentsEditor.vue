<template>
  <div class="classify-editor">
    <div v-for="(agent, index) in agents" :key="agent.key" class="classify-row">
      <span class="classify-index">{{ index + 1 }}</span>
      <a-input
        v-model:value="agent.value"
        size="small"
        placeholder="分类描述，如：关于请假的问题"
        class="classify-input"
      />
      <a-button
        size="small"
        type="text"
        class="classify-remove"
        :disabled="agents.length <= 2"
        @click="removeAgent(index)"
      >
        <DeleteOutlined />
      </a-button>
    </div>
    <a-button size="small" type="dashed" block @click="addAgent">
      <PlusOutlined />
      添加分类
    </a-button>
    <p class="classify-tip">每个分类对应节点右侧一个分支出口</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue';
import type { ClassifyQuestionAgentItemType, FlowNodeInputItemType } from '../../../core/type';
import { ensureArrayValue, getNanoid } from '../../../core/utils';

const props = defineProps<{ input: FlowNodeInputItemType }>();

ensureArrayValue(props.input);

const agents = computed<ClassifyQuestionAgentItemType[]>(() => props.input.value);

function addAgent() {
  agents.value.push({ key: getNanoid(4), value: '' });
}

function removeAgent(index: number) {
  if (agents.value.length <= 2) return;
  agents.value.splice(index, 1);
}
</script>

<style scoped lang="less">
.classify-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.classify-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.classify-index {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.classify-input {
  flex: 1;
}

.classify-remove {
  color: #94a3b8;
  flex-shrink: 0;

  &:hover:not(:disabled) {
    color: #dc2626;
  }
}

.classify-tip {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
}
</style>
