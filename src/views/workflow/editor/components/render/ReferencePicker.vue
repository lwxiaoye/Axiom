<template>
  <a-cascader
    class="reference-picker"
    popup-class-name="wf-ref-popup"
    :value="cascaderValue"
    :options="options"
    :placeholder="placeholder || '选择引用变量'"
    :allow-clear="allowClear"
    expand-trigger="hover"
    @change="handleChange"
  >
    <template #displayRender>
      <span class="reference-display" :class="{ empty: !displayLabel }">
        <ShareAltOutlined />
        {{ displayLabel || placeholder || '选择引用变量' }}
      </span>
    </template>
  </a-cascader>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { ShareAltOutlined } from '@ant-design/icons-vue';
import { WorkflowIOValueTypeLabelMap } from '../../../core/constants';
import type { ReferenceItemValueType, StoreNodeItemType } from '../../../core/type';
import { computeReferenceCandidates, formatReferenceLabel, isReferenceValue } from '../../../core/utils';
import { useEditorContext } from '../../composables/useEditorContext';

const props = defineProps<{
  node: StoreNodeItemType;
  value?: any;
  placeholder?: string;
  allowClear?: boolean;
  /** 限定可选输出的 valueType（如引用合并只允许 datasetQuote） */
  valueTypeFilter?: string[];
  /** 容器节点特殊引用：允许选择当前节点内部子节点输出。 */
  includeChildren?: boolean;
}>();

const emit = defineEmits<{ (e: 'update:value', value: ReferenceItemValueType | undefined): void }>();

const { graph } = useEditorContext();

const options = computed(() => {
  const candidates = computeReferenceCandidates(
    props.node.nodeId,
    graph.value.nodes,
    graph.value.edges,
    graph.value.chatConfig,
    { includeChildren: props.includeChildren }
  );
  return candidates
    .map((candidate) => ({
      value: candidate.nodeId,
      label: candidate.nodeName,
      children: candidate.outputs
        .filter((output) => !props.valueTypeFilter?.length || props.valueTypeFilter.includes(output.valueType || ''))
        .map((output) => ({
          value: output.key,
          label: output.valueType
            ? `${output.label}（${WorkflowIOValueTypeLabelMap[output.valueType] || output.valueType}）`
            : output.label,
        })),
    }))
    .filter((item) => item.children.length > 0);
});

const cascaderValue = computed(() => (isReferenceValue(props.value) ? props.value : undefined));

const displayLabel = computed(() =>
  formatReferenceLabel(cascaderValue.value, graph.value.nodes, graph.value.chatConfig)
);

function handleChange(value: any) {
  if (Array.isArray(value) && value.length === 2) {
    emit('update:value', [String(value[0]), String(value[1])]);
  } else {
    emit('update:value', undefined);
  }
}
</script>

<style scoped lang="less">
.reference-picker {
  width: 100%;

  :deep(.ant-select-selector) {
    min-height: 34px;
    border-color: #d7deea !important;
    border-radius: 8px !important;
    background: #ffffff;
    box-shadow: none !important;
    transition: border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease;
  }

  :deep(.ant-select-selection-item),
  :deep(.ant-select-selection-placeholder) {
    line-height: 32px;
  }

  &:hover :deep(.ant-select-selector) {
    border-color: #3370ff !important;
    background: #fbfdff;
  }

  &.ant-select-focused :deep(.ant-select-selector) {
    border-color: #3370ff !important;
    box-shadow: none !important;
  }
}

.reference-display {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  color: #3370ff;

  &.empty {
    color: #94a3b8;
  }
}
</style>

<style lang="less">
/* 级联下拉在 body 门户渲染，需全局样式；wf-ref-popup 仅本组件挂载。
   项目主题对 cascader active 项有深色全局覆盖，此处用 !important 拉回浅色语义 */
.wf-ref-popup {
  &.ant-cascader-dropdown {
    background: #ffffff !important;
    border-radius: 12px;
    box-shadow: 0 8px 28px rgba(19, 51, 107, 0.16) !important;
  }

  .ant-cascader-menus {
    background: #ffffff;
    border-radius: 12px;
  }

  .ant-cascader-menu {
    min-width: 176px;
    padding: 6px;
    border-inline-end-color: #f0f1f6;
  }

  .ant-cascader-menu-item {
    border-radius: 8px !important;
    padding: 8px 10px !important;
    font-size: 14px !important;
    color: #354052 !important;
    background: transparent !important;

    &:hover {
      background: #f0f1f6 !important;
      color: #111824 !important;
    }
  }

  .ant-cascader-menu-item-active,
  .ant-cascader-menu-item-active:hover {
    background: #f0f4ff !important;
    color: #3370ff !important;
    font-weight: 600;
  }

  .ant-cascader-menu-item-expand-icon {
    color: #8a95a7 !important;
  }
}

.wf-node-select-popup {
  &.ant-select-dropdown {
    padding: 6px !important;
    background: #ffffff !important;
    border-radius: 12px;
    box-shadow: 0 8px 28px rgba(19, 51, 107, 0.16) !important;
  }

  .ant-select-item {
    min-height: 34px;
    border-radius: 8px !important;
    padding: 7px 10px !important;
    font-size: 14px !important;
    color: #354052 !important;
    background: transparent !important;
  }

  .ant-select-item-option-active:not(.ant-select-item-option-disabled) {
    background: #f0f1f6 !important;
    color: #111824 !important;
  }

  .ant-select-item-option-selected:not(.ant-select-item-option-disabled) {
    background: #f0f4ff !important;
    color: #3370ff !important;
    font-weight: 600;
  }

  .ant-select-item-option-state {
    color: #3370ff !important;
  }
}
</style>
