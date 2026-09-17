<template>
  <div
    class="flow-node-card"
    :class="[{ selected, folded: !!node.isFolded }, debugStatus ? `debug-${debugStatus}` : '']"
    :style="{ '--node-accent': accentColor }"
    @dblclick="node.isFolded && toggleFold()"
  >
    <Handle
      v-if="showTargetHandle"
      type="target"
      :position="Position.Left"
      :id="targetHandleId"
      class="node-handle target-handle"
    />

    <!-- 工具挂载锚点（蓝本裸 selectedTools handle）：isTool 节点顶部紫色目标锚点 -->
    <Handle
      v-if="isToolMountable"
      type="target"
      :position="Position.Top"
      id="selectedTools"
      class="node-handle tool-handle"
    />

    <header class="node-header">
      <span class="node-icon">
        <img
          v-if="nodeAvatarUrl"
          :src="nodeAvatarUrl"
          alt=""
          class="node-avatar"
          @error="recoverAgentIcon($event, { appIcon: node.avatar, name: node.name })"
        />
        <component :is="iconComponent" v-else />
      </span>
      <div class="node-title">
        <input
          v-if="renaming"
          ref="renameInputRef"
          v-model="renameText"
          class="rename-input nodrag"
          @blur="commitRename"
          @keydown.enter.prevent="commitRename"
          @keydown.esc="renaming = false"
        />
        <strong v-else class="node-name" @dblclick="startRename">{{ node.name }}</strong>
        <span v-if="node.intro && !node.isFolded" class="node-intro">{{ node.intro }}</span>
      </div>
      <!-- 动态节点：来源/版本标签（蓝本工具来源与版本展示的基础形态） -->
      <span v-if="isDynamicNode && sourceLabel" class="source-badge">{{ sourceLabel }}</span>
      <span v-if="isDynamicNode && node.versionLabel" class="version-badge">v{{ node.versionLabel }}</span>
      <span v-if="debugStatus" class="debug-badge" :class="debugStatus">{{ debugStatusLabel }}</span>
      <a-dropdown v-if="!readonly" :trigger="['click']" placement="bottomRight">
        <button type="button" class="node-menu-btn nodrag" @click.stop>
          <EllipsisOutlined />
        </button>
        <template #overlay>
          <a-menu>
            <a-menu-item key="rename" @click="startRename">重命名</a-menu-item>
            <a-menu-item key="copy" :disabled="template?.unique" @click="duplicateNode(node.nodeId)">复制节点</a-menu-item>
            <a-menu-item key="fold" @click="toggleFold">{{ node.isFolded ? '展开节点' : '折叠节点' }}</a-menu-item>
            <a-menu-item v-if="hasErrorOutput" key="catchError" @click="setCatchError(node.nodeId, !node.catchError)">
              {{ node.catchError ? '关闭报错捕获' : '开启报错捕获' }}
            </a-menu-item>
            <a-menu-item key="delete" :disabled="!canRemoveCurrentNode" danger @click="removeNode(node.nodeId)">
              删除节点
            </a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
    </header>

    <section
      v-if="!node.isFolded && (isSystemConfig || visibleInputs.length || isIfElse || isVariableUpdate)"
      class="node-body"
      :class="{ 'body-readonly': readonly }"
    >
      <SystemConfigCard v-if="isSystemConfig" class="nodrag" />
      <template v-else>
        <RenderInput v-for="input in visibleInputs" :key="input.key" :node="node" :input="input" />
        <IfElseEditor v-if="isIfElse && ifElseInput" class="nodrag" :node="node" :input="ifElseInput" />
        <UpdateListEditor v-if="isVariableUpdate && updateListInput" class="nodrag" :node="node" :input="updateListInput" />
        <DynamicOutputs v-if="addOutputAnchor && !readonly" class="nodrag" :node="node" :anchor="addOutputAnchor" />
      </template>
    </section>

    <!-- 分支出口（判断器 / 问题分类 / 用户选择）：每行一个 source handle，折叠时保留（边锚点不可丢） -->
    <section v-if="branchHandles.length" class="node-branches">
      <div v-for="branch in branchHandles" :key="branch.key" class="branch-row">
        <span class="branch-label">{{ branch.label }}</span>
        <Handle
          type="source"
          :position="Position.Right"
          :id="getHandleId(node.nodeId, 'source', branch.key)"
          class="node-handle branch-handle quick-add-handle"
          @pointerdown="recordHandlePointer"
          @click.stop="handleSourceHandleClick($event, node.nodeId, getHandleId(node.nodeId, 'source', branch.key))"
        />
      </div>
    </section>

    <!-- 输出列表（static + dynamic），折叠时隐藏 -->
    <section v-if="!node.isFolded && visibleOutputs.length" class="node-outputs">
      <div class="outputs-title">输出</div>
      <div v-for="output in visibleOutputs" :key="output.key" class="output-row">
        <span class="output-label">
          {{ output.label }}
          <em v-if="output.type === 'dynamic'" class="output-dynamic-tag">动态</em>
        </span>
        <em v-if="output.valueType" class="output-type">{{ valueTypeLabel(output.valueType) }}</em>
      </div>
    </section>

    <!-- 报错捕获：错误输出行 + 错误边锚点（蓝本 source_catch），折叠时保留锚点 -->
    <section v-if="node.catchError && errorOutputs.length" class="node-error-catch">
      <div v-for="(output, index) in errorOutputs" :key="output.key" class="error-row">
        <span class="error-label">
          <WarningOutlined />
          {{ output.label || '错误信息' }}
        </span>
        <Handle
          v-if="index === errorOutputs.length - 1"
          type="source"
          :position="Position.Right"
          :id="getErrorHandleId(node.nodeId)"
          class="node-handle error-handle quick-add-handle"
          @pointerdown="recordHandlePointer"
          @click.stop="handleSourceHandleClick($event, node.nodeId, getErrorHandleId(node.nodeId))"
        />
      </div>
    </section>

    <Handle
      v-if="showDefaultSourceHandle"
      type="source"
      :position="Position.Right"
      :id="getHandleId(node.nodeId, 'source', 'right')"
      class="node-handle source-handle quick-add-handle"
      @pointerdown="recordHandlePointer"
      @click.stop="handleSourceHandleClick($event, node.nodeId, getHandleId(node.nodeId, 'source', 'right'))"
    />

    <!-- 工具调用节点：底部紫色工具锚点，向下挂载工具 -->
    <template v-if="isToolCallNode">
      <div class="tool-anchor-label">工具</div>
      <Handle type="source" :position="Position.Bottom" id="selectedTools" class="node-handle tool-handle" />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import { Handle, Position } from '@vue-flow/core';
import {
  ApiOutlined,
  BranchesOutlined,
  CommentOutlined,
  DatabaseOutlined,
  EllipsisOutlined,
  FilterOutlined,
  FontSizeOutlined,
  ForkOutlined,
  FormOutlined,
  InteractionOutlined,
  MergeCellsOutlined,
  MessageOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  SwapOutlined,
  WarningOutlined,
} from '@ant-design/icons-vue';
import {
  DYNAMIC_NODE_TYPES,
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTypeEnum,
  NodeInputKeyEnum,
  NodeOutputKeyEnum,
  WorkflowIOValueTypeLabelMap,
  getErrorHandleId,
  getHandleId,
} from '../../core/constants';
import type { StoreNodeItemType } from '../../core/type';
import { getTemplateByFlowNodeType } from '../../core/templates';
import { getNodeInput, getNodeSourceHandleKeys } from '../../core/utils';
import { useEditorContext } from '../composables/useEditorContext';
import { isHandleClick } from '../editorCommands';
import { getAgentIconUrl, recoverAgentIcon } from '/@/views/peopleCenter/agentIcon';
import RenderInput from './render/RenderInput.vue';
import DynamicOutputs from './render/DynamicOutputs.vue';
import IfElseEditor from './custom/IfElseEditor.vue';
import UpdateListEditor from './custom/UpdateListEditor.vue';
import SystemConfigCard from './SystemConfigCard.vue';

const props = defineProps<{
  id: string;
  data: { node: StoreNodeItemType };
  selected?: boolean;
}>();

const { readonly, canRemoveNode, removeNode, duplicateNode, quickAddFromHandle, renameNode, setCatchError } =
  useEditorContext();

const node = props.data.node;

const template = getTemplateByFlowNodeType(node.flowNodeType);

const canRemoveCurrentNode = computed(() => canRemoveNode(node.nodeId));

const iconMap: Record<string, any> = {
  PlayCircleOutlined,
  MessageOutlined,
  DatabaseOutlined,
  MergeCellsOutlined,
  CommentOutlined,
  ForkOutlined,
  FormOutlined,
  InteractionOutlined,
  FilterOutlined,
  ApiOutlined,
  BranchesOutlined,
  FontSizeOutlined,
  SettingOutlined,
  SwapOutlined,
};

/** 动态节点（tool/pluginModule/appModule/toolSet）：模板来自服务端 previewNode，不在本地注册表 */
const isDynamicNode = !template && DYNAMIC_NODE_TYPES.includes(node.flowNodeType);

/** 工具调用节点：底部工具锚点 */
const isToolCallNode = node.flowNodeType === FlowNodeTypeEnum.toolCall;

/** 可被挂载为工具（蓝本 isTool）：本地模板 isTool 或动态节点 */
const isToolMountable = !!template?.isTool || isDynamicNode;

const iconComponent = computed(
  () => iconMap[template?.icon || ''] || (isDynamicNode ? ApiOutlined : MessageOutlined)
);

const accentColor = computed(() => template?.color || (isDynamicNode ? '#6f5dd7' : '#475569'));
const nodeAvatarUrl = computed(() => getAgentIconUrl({ appIcon: node.avatar, name: node.name }));

const showTargetHandle = computed(() => !!template?.showTargetHandle || isDynamicNode);

const SOURCE_LABELS: Record<string, string> = {
  systemTool: '系统工具',
  http: 'HTTP 工具',
  mcp: 'MCP 工具',
  workflowTool: '工作流工具',
  app: '应用',
};
const sourceLabel = computed(() => SOURCE_LABELS[node.source || ''] || '');

const targetHandleId = getHandleId(node.nodeId, 'target', 'left');

const isSystemConfig = node.flowNodeType === FlowNodeTypeEnum.systemConfig;
const isIfElse = node.flowNodeType === FlowNodeTypeEnum.ifElseNode;
const isVariableUpdate = node.flowNodeType === FlowNodeTypeEnum.variableUpdate;
const isBranchNode =
  isIfElse ||
  node.flowNodeType === FlowNodeTypeEnum.classifyQuestion ||
  node.flowNodeType === FlowNodeTypeEnum.userSelect;

const ifElseInput = computed(() => getNodeInput(node, NodeInputKeyEnum.ifElseList));
const updateListInput = computed(() => getNodeInput(node, NodeInputKeyEnum.updateList));
const hiddenDatasetSearchInputs = new Set<string>([
  NodeInputKeyEnum.datasetSimilarity,
  NodeInputKeyEnum.collectionFilterMatch,
]);
const loopRunModeInput = computed(() => getNodeInput(node, 'loopRunMode'));
const isConditionalLoop = computed(
  () => node.flowNodeType === FlowNodeTypeEnum.loopRun && loopRunModeInput.value?.value === 'conditional'
);

/** hidden 渲染器、专属编辑器代管的输入与动态输入（canEdit，由 DynamicInputs 代管）不走通用渲染链路 */
const visibleInputs = computed(() => {
  // 蓝本 NodeWorkflowStart：开始节点只渲染输出，inputs 不渲染（但按蓝本 schema 序列化）
  if (node.flowNodeType === FlowNodeTypeEnum.workflowStart) return [];
  return node.inputs.filter((input) => {
    if (input.canEdit || input.deprecated) return false;
    const renderType = input.renderTypeList[input.selectedTypeIndex || 0];
    if (renderType === FlowNodeInputTypeEnum.hidden) return false;
    if (input.key === NodeInputKeyEnum.ifElseList || input.key === NodeInputKeyEnum.updateList) return false;
    if (isConditionalLoop.value && input.key === 'loopRunInputArray') return false;
    if (node.flowNodeType === FlowNodeTypeEnum.datasetSearchNode && hiddenDatasetSearchInputs.has(input.key)) {
      return false;
    }
    return true;
  });
});

const branchHandles = computed(() => (isBranchNode ? getNodeSourceHandleKeys(node) : []));

const showDefaultSourceHandle = computed(
  () => (!!template?.showSourceHandle || isDynamicNode) && !isBranchNode
);

/** static + dynamic 输出可见；error 输出走独立报错捕获区；hidden 与 addOutputParam 编辑锚点不展示 */
const visibleOutputs = computed(() =>
  node.outputs.filter(
    (output) =>
      (output.type === FlowNodeOutputTypeEnum.static || output.type === FlowNodeOutputTypeEnum.dynamic) &&
      !output.deprecated &&
      !output.invalid &&
      output.key !== NodeOutputKeyEnum.addOutputParam
  )
);

/** 蓝本 Output_Template_AddOutput：存在该锚点输出的节点提供"输出字段提取"编辑 UI */
const addOutputAnchor = computed(() =>
  node.outputs.find((output) => output.key === NodeOutputKeyEnum.addOutputParam)
);

const errorOutputs = computed(() => node.outputs.filter((output) => output.type === FlowNodeOutputTypeEnum.error));
const hasErrorOutput = computed(() => errorOutputs.value.length > 0);

const debugStatus = computed(() => node.debugResult?.status);
const debugStatusLabel = computed(
  () =>
    ({ running: '运行中', success: '成功', skipped: '已跳过', failed: '失败' })[debugStatus.value as string] ||
    debugStatus.value
);

function valueTypeLabel(valueType: string) {
  return WorkflowIOValueTypeLabelMap[valueType] || valueType;
}

function toggleFold() {
  node.isFolded = !node.isFolded;
}

const renaming = ref(false);
const handlePointerDown = ref<{ x: number; y: number } | null>(null);
const renameText = ref(node.name);
const renameInputRef = ref<HTMLInputElement>();

function startRename() {
  if (readonly.value) return;
  renameText.value = node.name;
  renaming.value = true;
  nextTick(() => renameInputRef.value?.focus());
}

function commitRename() {
  if (renaming.value) {
    renaming.value = false;
    const name = renameText.value.trim();
    if (name && name !== node.name) renameNode(node.nodeId, name);
  }
}

function recordHandlePointer(event: PointerEvent) {
  handlePointerDown.value = { x: event.clientX, y: event.clientY };
}

function handleSourceHandleClick(event: MouseEvent, nodeId: string, sourceHandle: string) {
  const start = handlePointerDown.value;
  handlePointerDown.value = null;
  if (!start || !isHandleClick(start, { x: event.clientX, y: event.clientY })) return;
  quickAddFromHandle(nodeId, sourceHandle);
}
</script>

<style scoped lang="less">
// 对标蓝本 FastGPT 节点卡片：更大的卡幅与字号、蓝本主蓝 #3370FF 选中/句柄、区块化输出
.flow-node-card {
  width: 480px;
  background: #ffffff;
  border: 1px solid #e4e7ee;
  border-radius: 14px;
  box-shadow: 0 4px 20px rgba(19, 51, 107, 0.08);
  font-size: 14px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &:hover {
    box-shadow: 0 6px 24px rgba(19, 51, 107, 0.12);
  }

  &.selected {
    border-color: #3370ff;
    box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.12), 0 6px 24px rgba(19, 51, 107, 0.12);
  }

  // 蓝本折叠态：240 宽精简卡片，双击展开
  &.folded {
    width: 240px;
    cursor: pointer;
  }

  // 调试运行态（蓝本 debugResult.status 边框态）
  &.debug-running {
    border-color: #3370ff;
    box-shadow: 0 0 0 3px rgba(51, 112, 255, 0.2), 0 6px 24px rgba(19, 51, 107, 0.12);
  }

  &.debug-success {
    border-color: #39cc83;
    box-shadow: 0 0 0 3px rgba(57, 204, 131, 0.16), 0 6px 24px rgba(19, 51, 107, 0.12);
  }

  &.debug-failed {
    border-color: #f56060;
    box-shadow: 0 0 0 3px rgba(245, 96, 96, 0.16), 0 6px 24px rgba(19, 51, 107, 0.12);
  }

  &.debug-skipped {
    opacity: 0.55;
  }
}

.node-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
}

.node-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, var(--node-accent) 12%, #ffffff);
  color: var(--node-accent);
  font-size: 18px;
  flex-shrink: 0;
  overflow: hidden;

  .node-avatar {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
}

.source-badge,
.version-badge {
  flex-shrink: 0;
  font-size: 11px;
  border-radius: 6px;
  padding: 1px 6px;
  color: #6f5dd7;
  background: #f0eeff;
  border: 1px solid #d6d0f5;
}

.version-badge {
  color: #667085;
  background: #f7f8fa;
  border-color: #e4e7ee;
}

.node-title {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;

  .node-name {
    font-size: 16px;
    font-weight: 700;
    color: #111824;
    line-height: 1.3;
    cursor: text;
  }

  .node-intro {
    font-size: 12px;
    color: #8a95a7;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .rename-input {
    border: 1px solid #3370ff;
    border-radius: 6px;
    font-size: 14px;
    padding: 2px 8px;
    outline: none;
    width: 100%;
  }
}

.debug-badge {
  flex-shrink: 0;
  font-size: 12px;
  font-style: normal;
  border-radius: 6px;
  padding: 1px 8px;
  border: 1px solid transparent;

  &.running {
    color: #3370ff;
    background: #f0f4ff;
    border-color: #c5d7ff;
  }

  &.success {
    color: #039855;
    background: #ecfdf3;
    border-color: #abefc6;
  }

  &.failed {
    color: #d92d20;
    background: #fef3f2;
    border-color: #fecdca;
  }

  &.skipped {
    color: #667085;
    background: #f7f8fa;
    border-color: #e4e7ee;
  }
}

.node-menu-btn {
  border: none;
  background: transparent;
  color: #8a95a7;
  cursor: pointer;
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 16px;
  line-height: 1;

  &:hover {
    background: #f0f1f6;
    color: #111824;
  }
}

.node-body {
  padding: 4px 18px 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;

  // VIEWER 只读：节点内部编辑器整体不可交互（内容仍可见）
  &.body-readonly {
    pointer-events: none;
    opacity: 0.85;
  }

  // 提升属性区可读性（RenderInput 内部）
  :deep(.input-label) {
    font-size: 14px;
    font-weight: 600;
    color: #354052;
  }

  :deep(.label-help) {
    font-size: 13px;
  }

  :deep(.variable-textarea),
  :deep(.ant-input),
  :deep(.ant-input-number),
  :deep(.ant-select .ant-select-selector) {
    border-color: #d7deea !important;
    border-radius: 8px !important;
    background: #ffffff;
    color: #354052;
    font-size: 14px;
    box-shadow: none !important;
    transition: border-color 0.16s ease, box-shadow 0.16s ease, background-color 0.16s ease;
  }

  :deep(.ant-input),
  :deep(.ant-input-number),
  :deep(.ant-select-single .ant-select-selector),
  :deep(input.variable-textarea) {
    min-height: 34px;
  }

  :deep(.ant-select-single.ant-select-sm .ant-select-selector),
  :deep(.ant-select-single:not(.ant-select-customize-input) .ant-select-selector) {
    height: 34px;
  }

  :deep(.ant-input-sm) {
    padding: 6px 11px;
  }

  :deep(.ant-select-single.ant-select-sm .ant-select-selector .ant-select-selection-item),
  :deep(.ant-select-single.ant-select-sm .ant-select-selector .ant-select-selection-placeholder),
  :deep(.ant-select-single:not(.ant-select-customize-input) .ant-select-selector .ant-select-selection-item),
  :deep(.ant-select-single:not(.ant-select-customize-input) .ant-select-selector .ant-select-selection-placeholder) {
    line-height: 32px;
  }

  :deep(.ant-select-multiple .ant-select-selector) {
    min-height: 34px;
    padding: 2px 8px;
  }

  :deep(.ant-input:hover),
  :deep(.ant-input-number:hover),
  :deep(.ant-select:hover .ant-select-selector),
  :deep(.variable-textarea:hover) {
    border-color: #3370ff !important;
    background: #fbfdff;
  }

  :deep(.ant-input:focus),
  :deep(.ant-input-focused),
  :deep(.ant-input-number-focused),
  :deep(.ant-select-focused .ant-select-selector),
  :deep(.variable-textarea:focus) {
    border-color: #3370ff !important;
    box-shadow: none !important;
  }

  :deep(.ant-select-arrow),
  :deep(.ant-select-clear) {
    color: #8a95a7;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 32px;
    border-radius: 8px;
    font-size: 13px;
    line-height: 1.25;
  }

  :deep(.ant-btn-sm) {
    height: 32px;
    padding-inline: 12px;
  }

  :deep(.ant-btn-text.ant-btn-sm) {
    min-width: 32px;
    padding-inline: 8px;
  }

  :deep(.ant-btn-dashed.ant-btn-block) {
    height: 36px;
    font-weight: 500;
  }

  :deep(.ant-radio-button-wrapper) {
    height: 32px;
    padding-inline: 12px;
    line-height: 30px;
  }
}

.node-branches {
  border-top: 1px solid #f0f1f6;
  padding: 8px 0;

  .branch-row {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 9px 22px 9px 18px;

    .branch-label {
      font-size: 14px;
      font-weight: 600;
      color: #354052;
    }
  }
}

.node-outputs {
  border-top: 1px solid #f0f1f6;
  padding: 12px 18px 16px;

  .outputs-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: #8a95a7;
    margin-bottom: 8px;
  }

  .output-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: #f7f8fa;
    border: 1px solid #f0f1f6;
    border-radius: 8px;

    & + .output-row {
      margin-top: 6px;
    }

    .output-label {
      font-size: 14px;
      color: #354052;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    .output-dynamic-tag {
      font-size: 11px;
      font-style: normal;
      color: #6f5dd7;
      background: #f0eeff;
      border: 1px solid #d6d0f5;
      border-radius: 4px;
      padding: 0 5px;
    }

    .output-type {
      font-size: 12px;
      font-style: normal;
      color: #667085;
      background: #ffffff;
      border: 1px solid #e4e7ee;
      border-radius: 6px;
      padding: 1px 8px;
    }
  }
}

// 报错捕获区（蓝本 catchError 错误输出 + source_catch 锚点）
.node-error-catch {
  border-top: 1px solid #f0f1f6;
  padding: 8px 0;

  .error-row {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 9px 22px 9px 18px;

    .error-label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 14px;
      font-weight: 600;
      color: #d92d20;
    }
  }
}

// 连接点用描边和外圈反馈，不能覆盖 Vue Flow 的定位 transform，否则 hover 时会发生偏移。
.node-handle {
  width: 14px;
  height: 14px;
  background: #ffffff;
  border: 2.5px solid #3370ff;
  box-shadow: 0 0 0 1px rgba(51, 112, 255, 0.16);
  cursor: crosshair;
  transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;

  &:hover {
    background: #edf4ff;
    border-color: #1d5fce;
    box-shadow: 0 0 0 4px rgba(51, 112, 255, 0.2), 0 2px 6px rgba(19, 51, 107, 0.18);
  }

  &.target-handle {
    border-color: #94a3b8;
    box-shadow: 0 0 0 1px rgba(100, 116, 139, 0.15);

    &:hover {
      background: #f1f5f9;
      border-color: #64748b;
      box-shadow: 0 0 0 4px rgba(100, 116, 139, 0.18), 0 2px 6px rgba(51, 65, 85, 0.15);
    }
  }
}

.branch-handle {
  position: absolute;
  right: -7px;
  top: 50%;
  transform: translateY(-50%);
}

.quick-add-handle::after {
  content: '';
  position: absolute;
  inset: 50% auto auto 50%;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transform: translate(-50%, -50%);
  border: 1px solid #cbd5e1;
  border-radius: 50%;
  background:
    linear-gradient(#3370ff 0 0) center / 8px 2px no-repeat,
    linear-gradient(#3370ff 0 0) center / 2px 8px no-repeat,
    #ffffff;
  box-shadow: 0 2px 6px rgba(19, 51, 107, 0.12);
  pointer-events: none;
  opacity: 0;
  transition: opacity 0.12s ease, border-color 0.12s ease, transform 0.12s ease;
}

.flow-node-card:hover .quick-add-handle::after,
.flow-node-card.selected .quick-add-handle::after,
.quick-add-handle:hover::after {
  opacity: 1;
}

.error-handle {
  position: absolute;
  right: -7px;
  top: 50%;
  transform: translateY(-50%);
  border-color: #f56060;
}

// 工具锚点（蓝本紫色 selectedTools）
.tool-handle {
  border-color: #6f5dd7;
}

.tool-anchor-label {
  position: absolute;
  bottom: -22px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: #6f5dd7;
  background: #f0eeff;
  border: 1px solid #d6d0f5;
  border-radius: 6px;
  padding: 0 6px;
  pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
  .node-handle,
  .quick-add-handle::after {
    transition: none;
  }
}
</style>
