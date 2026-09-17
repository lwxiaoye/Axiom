<template>
  <div class="dataset-select-render">
    <div class="dataset-toolbar">
      <a-select
        class="dataset-select"
        size="small"
        mode="multiple"
        :value="selectedIds"
        :options="datasetOptions"
        :loading="knowledgeLoading"
        placeholder="选择知识库（可多选）"
        popup-class-name="wf-node-select-popup"
        show-search
        option-filter-prop="label"
        :max-tag-count="2"
        @change="handleChange"
      />
      <a-button v-if="showParamsButton" size="small" class="params-btn" @click="paramsVisible = true">
        <ControlOutlined />
        参数
      </a-button>
    </div>

    <a-modal
      v-if="showParamsButton"
      v-model:open="paramsVisible"
      title="检索参数"
      :width="480"
      :footer="null"
      destroy-on-close
    >
      <div class="params-form">
        <div class="params-item">
          <label>检索模式</label>
          <a-radio-group
            :value="searchMode"
            @change="setSibling(NodeInputKeyEnum.datasetSearchMode, $event.target.value)"
          >
            <a-radio-button v-for="(label, mode) in DatasetSearchModeLabelMap" :key="mode" :value="mode">
              {{ label }}
            </a-radio-button>
          </a-radio-group>
        </div>
        <div v-if="searchMode === DatasetSearchModeEnum.mixedRecall" class="params-item">
          <label>语义权重（{{ embeddingWeight }}）</label>
          <a-slider
            :value="embeddingWeight"
            :min="0"
            :max="1"
            :step="0.1"
            @change="setSibling(NodeInputKeyEnum.datasetSearchEmbeddingWeight, $event)"
          />
        </div>
        <div class="params-item">
          <label>引用上限（Token）</label>
          <a-input-number
            :value="maxTokens"
            :min="100"
            :max="20000"
            :step="100"
            style="width: 100%"
            @change="setSibling(NodeInputKeyEnum.datasetMaxTokens, $event)"
          />
        </div>
        <div class="params-item inline">
          <label>问题优化（补全指代）</label>
          <a-switch
            :checked="usingExtensionQuery"
            size="small"
            @change="setSibling(NodeInputKeyEnum.datasetSearchUsingExtensionQuery, $event)"
          />
        </div>
        <div v-if="usingExtensionQuery" class="params-item">
          <label>问题优化模型</label>
          <a-select
            :value="extensionModel || undefined"
            :options="modelSelectOptions"
            :loading="modelLoading"
            allow-clear
            show-search
            placeholder="默认模型"
            popup-class-name="wf-node-select-popup"
            style="width: 100%"
            @change="setSibling(NodeInputKeyEnum.datasetSearchExtensionModel, $event ?? '')"
          />
        </div>
        <div v-if="usingExtensionQuery" class="params-item">
          <label>优化背景描述</label>
          <a-textarea
            :value="extensionBg"
            :rows="3"
            placeholder="描述对话背景，帮助模型补全问题中的指代"
            @change="setSibling(NodeInputKeyEnum.datasetSearchExtensionBg, ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
        <p class="params-tip">混合检索权重依赖知识库模块能力，未开通时以后端实际检索行为为准。</p>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { ControlOutlined } from '@ant-design/icons-vue';
import { DatasetSearchModeEnum, DatasetSearchModeLabelMap, NodeInputKeyEnum } from '../../../core/constants';
import type { FlowNodeInputItemType, StoreNodeItemType } from '../../../core/type';
import { getNodeInput, setInputValue } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';

const props = defineProps<{
  node: StoreNodeItemType;
  input: FlowNodeInputItemType;
}>();

const { knowledgeOptions, knowledgeLoading, modelOptions, modelLoading } = useEditorContext();

const paramsVisible = ref(false);

const showParamsButton = computed(() => props.input.key !== NodeInputKeyEnum.aiChatDatasets);

const datasetOptions = computed(() =>
  knowledgeOptions.value.map((item) => ({ label: item.name, value: String(item.id) }))
);

const selectedIds = computed(() => {
  const value = Array.isArray(props.input.value) ? props.input.value : [];
  return value.map((item: any) => String(typeof item === 'object' ? item.datasetId || item.id : item));
});

const maxTokens = computed(() => siblingValue(NodeInputKeyEnum.datasetMaxTokens, 3000));
const searchMode = computed(() => siblingValue(NodeInputKeyEnum.datasetSearchMode, DatasetSearchModeEnum.mixedRecall));
const embeddingWeight = computed(() => siblingValue(NodeInputKeyEnum.datasetSearchEmbeddingWeight, 0.5));
const usingExtensionQuery = computed(() =>
  siblingValue(NodeInputKeyEnum.datasetSearchUsingExtensionQuery, false)
);
const extensionBg = computed(() => siblingValue(NodeInputKeyEnum.datasetSearchExtensionBg, ''));
const extensionModel = computed(() => siblingValue(NodeInputKeyEnum.datasetSearchExtensionModel, ''));

const modelSelectOptions = computed(() =>
  modelOptions.value.map((item) => ({ label: item.label || item.value, value: item.value }))
);

function siblingValue(key: string, fallback: any) {
  return getNodeInput(props.node, key)?.value ?? fallback;
}

function setSibling(key: string, value: any) {
  const input = getNodeInput(props.node, key);
  if (input) setInputValue(input, value);
}

function handleChange(ids: any) {
  const list = Array.isArray(ids) ? ids : [];
  setInputValue(
    props.input,
    list.map((id: string) => {
      const found = knowledgeOptions.value.find((item) => String(item.id) === String(id));
      return { datasetId: String(id), name: found?.name };
    })
  );
}
</script>

<style scoped lang="less">
.dataset-toolbar {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}

.dataset-select {
  flex: 1;
  min-width: 0;
}

.params-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #64748b;
}

.params-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 24px 20px;

  .params-item {
    label {
      display: block;
      margin-bottom: 6px;
      font-size: 12px;
      color: #475569;
    }

    &.inline {
      display: flex;
      align-items: center;
      justify-content: space-between;

      label {
        margin-bottom: 0;
      }
    }
  }

  .params-tip {
    margin: 0;
    font-size: 12px;
    color: #94a3b8;
  }
}
</style>
