<template>
  <div class="workflow-editor-page">
    <header class="editor-header">
      <button type="button" class="back-btn" @click="handleBack">
        <ArrowLeftOutlined />
      </button>
      <div class="app-meta">
        <strong>{{ workflowApp?.name || '工作流编排' }}</strong>
        <span class="app-sub">
          {{ workflowApp?.description || '拖拽节点与连线，构建智能体工作流' }}
        </span>
      </div>
      <span v-if="hasUnsavedChanges" class="unsaved-dot" title="有未保存的修改"></span>
      <a-tag v-if="readonly" class="readonly-tag" color="default">只读</a-tag>
      <div class="header-actions">
        <a-button v-if="!readonly" size="small" :disabled="!workflowApp?.id" @click="evaluationVisible = true">
          <BarChartOutlined />
          评测记录
        </a-button>
        <a-button size="small" @click="debugVisible = true">
          <BugOutlined />
          调试
        </a-button>
        <template v-if="!readonly">
          <a-button size="small" :loading="previewing" @click="handlePreview">
            <EyeOutlined />
            运行预览
          </a-button>
          <a-button size="small" :loading="saving" @click="handleSaveDraft">
            <SaveOutlined />
            保存草稿
          </a-button>
        </template>
      </div>
    </header>

    <div class="editor-body" @dragover.prevent @drop="handleDrop">
      <a-spin :spinning="loading" wrapper-class-name="canvas-spin">
        <VueFlow
          v-model:nodes="flowNodes"
          v-model:edges="flowEdges"
          class="workflow-canvas"
          :class="{ 'canvas-readonly': readonly }"
          :node-types="nodeTypes"
          :edge-types="edgeTypes"
          :default-edge-options="defaultEdgeOptions"
          :min-zoom="0.1"
          :max-zoom="3"
          :snap-to-grid="true"
          :snap-grid="[8, 8]"
          :connection-radius="50"
          :nodes-draggable="!readonly"
          :nodes-connectable="!readonly"
          :edges-updatable="!readonly"
          :delete-key-code="WORKFLOW_VUE_FLOW_DELETE_KEY_CODE"
          @pane-ready="handlePaneReady"
          @connect="handleConnect"
          @node-drag-stop="handleNodeDragStop"
          @nodes-change="handleNodesChange"
          @edges-change="handleEdgesChange"
        >
          <!-- 蓝本画布：#F7F8FA 底 + #A4A4A4 点阵（gap 60 / size 3），小地图 150×92 -->
          <Background pattern-color="#A4A4A4" :gap="60" :size="3" />
          <MiniMap class="wf-minimap" :width="150" :height="92" pannable zoomable />
        </VueFlow>
      </a-spin>

      <!-- 四 Tab 节点模板面板（蓝本 460px 左侧面板） -->
      <NodeTemplateMenu
        v-if="templateMenuVisible && !readonly"
        class="template-panel"
        :exclude-app-id="workflowApp?.id"
        @add="handleTemplateAdd"
        @close="closeTemplateMenu"
      />

      <!-- 添加节点 -->
      <div class="canvas-toolbar" :class="{ 'toolbar-shifted': templateMenuVisible && !readonly }">
        <a-button
          v-if="!readonly"
          type="primary"
          class="add-node-btn"
          @click="toggleTemplateMenu"
        >
          <PlusOutlined />
          添加节点
        </a-button>
        <div class="zoom-controls">
          <button type="button" title="放大" @click="zoomIn()"><ZoomInOutlined /></button>
          <button type="button" title="缩小" @click="zoomOut()"><ZoomOutOutlined /></button>
          <button type="button" title="适应画布" @click="fitView({ padding: 0.3 })"><CompressOutlined /></button>
          <template v-if="!readonly">
            <button type="button" title="撤销 (⌘Z)" :disabled="!canUndo" @click="undo"><UndoOutlined /></button>
            <button type="button" title="重做 (⌘⇧Z)" :disabled="!canRedo" @click="redo"><RedoOutlined /></button>
          </template>
        </div>
      </div>

    </div>

    <DebugDrawer
      v-model:open="debugVisible"
      :chat-config="graph.chatConfig"
      :running="debugRunning"
      :step-running="debugRunning"
      :result="debugResult"
      :step-session="debugStepSession"
      @run="runDebug"
      @start-step="startStepDebug"
      @next-step="nextStepDebug"
      @stop-step="stopStepDebug"
      @resume="resumeDebug"
    />
    <EvaluationRunsDrawer v-model:open="evaluationVisible" :app-id="workflowApp?.id || ''" />
  </div>
</template>

<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router';
import { Modal, message } from 'ant-design-vue';
import { useUserStore } from '/@/store/modules/user';
import { readUserScoped, removeUserScoped, writeUserScoped } from '/@/views/peopleCenter/utils/userScopedStorage';
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  BugOutlined,
  CompressOutlined,
  EyeOutlined,
  PlusOutlined,
  RedoOutlined,
  SaveOutlined,
  UndoOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons-vue';
import { VueFlow, useVueFlow, MarkerType } from '@vue-flow/core';
import type { Connection, Edge, EdgeChange, Node, NodeChange } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { MiniMap } from '@vue-flow/minimap';
import {
  createWorkflowDebugSession,
  debugWorkflowDefinition,
  queryNodeTemplatePreview,
  queryWorkflowAppById,
  queryWorkflowApp,
  queryWorkflowDefinition,
  queryWorkflowModelOptions,
  resumeWorkflowDefinition,
  saveWorkflowDefinition,
  stepWorkflowDebugSession,
  stopWorkflowDebugSession,
  type AiWorkflowApp,
  type WorkflowDebugSession,
  type WorkflowModelOption,
  type WorkflowRunResponse,
} from '../api/workflow.api';
import { loadWorkflowSelectableKnowledgeOptions } from '../utils/knowledgeSelection';
import { FlowNodeTypeEnum, TOOL_EDGE_HANDLE, getErrorHandleId, getHandleId } from '../core/constants';
import type { FlowNodeTemplateType, StoreNodeItemType, WorkflowGraphType } from '../core/type';
import { LoopRunStartTemplate, getTemplateByFlowNodeType } from '../core/templates';
import { createDefaultGraph, parsePersistedGraph, serializeGraph } from '../core/compiler';
import { buildWorkflowRuntimeVariables } from '../shared/runtimeVariables';
import { getAiAppDraftPreviewRoute } from '../shared/runtimeRoute';
import {
  collectRemovableNodeIds,
  WORKFLOW_VUE_FLOW_DELETE_KEY_CODE,
  getMeasuredNodeElementUpdates,
  isWorkflowDeleteShortcut,
  isProtectedFromDirectRemoval,
  readWorkflowClipboardPayload,
  shouldIgnoreWorkflowShortcut,
  shouldUseNativeTextShortcut,
  writeWorkflowClipboardPayload,
} from './editorCommands';
import {
  applyDefaultReferences,
  computeUniqueNodeName,
  createNodeFromTemplate,
  getNanoid,
  pruneDanglingReferences,
} from '../core/utils';
import { provideEditorContext, type KnowledgeOption } from './composables/useEditorContext';
import FlowNodeCard from './components/FlowNodeCard.vue';
import FlowEdge from './components/FlowEdge.vue';
import NodeTemplateMenu from './components/NodeTemplateMenu.vue';
import DebugDrawer from './components/DebugDrawer.vue';
import EvaluationRunsDrawer from './components/EvaluationRunsDrawer.vue';

defineOptions({ name: 'WorkflowFlowEditor' });

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const { zoomIn, zoomOut, fitView, screenToFlowCoordinate, getSelectedNodes, getNodes, updateNodeDimensions } =
  useVueFlow();

const nodeTypes = { flowNode: markRaw(FlowNodeCard) } as any;
const edgeTypes = { buttonedge: markRaw(FlowEdge) } as any;
// 蓝本 defaultEdgeOptions：仅 zIndex 0，无 animated；样式由 ButtonEdge 自绘
const defaultEdgeOptions = {
  type: 'buttonedge',
  zIndex: 0,
  markerEnd: MarkerType.ArrowClosed,
};
const workflowAppId = computed(() => String(route.query.workflowAppId || ''));
const appInfoId = computed(() => String(route.query.appInfoId || ''));

const loading = ref(false);
const saving = ref(false);
const workflowApp = ref<AiWorkflowApp | null>(null);

const graph = ref<WorkflowGraphType>(createDefaultGraph());
const flowNodes = ref<Node[]>([]);
const flowEdges = ref<Edge[]>([]);
const savedSnapshot = ref('');
// 初始即渲染默认图；加载失败时画布保持默认图而非空白
syncFlowFromGraph();

const templateMenuVisible = ref(false);
const quickAddRequest = ref<{ sourceNodeId: string; sourceHandle: string; position: { x: number; y: number } } | null>(null);
const debugVisible = ref(false);
const evaluationVisible = ref(false);
const debugRunning = ref(false);
const debugResult = ref<WorkflowRunResponse | null>(null);
const debugStepSession = ref<WorkflowDebugSession | null>(null);

const modelOptions = ref<WorkflowModelOption[]>([]);
const modelLoading = ref(false);
const knowledgeOptions = ref<KnowledgeOption[]>([]);
const knowledgeLoading = ref(false);

/** VIEWER 只读态：后端 _require_permission 会 403 拒绝写入，前端同步禁用编辑入口 */
const readonly = computed(() => workflowApp.value?.sharePermission === 'VIEWER');
const editorAppId = computed(
  () => workflowApp.value?.id || workflowAppId.value || appInfoId.value || '',
);

provideEditorContext({
  graph,
  appId: editorAppId,
  modelOptions,
  modelLoading,
  knowledgeOptions,
  knowledgeLoading,
  readonly,
  canRemoveNode,
  removeNode,
  duplicateNode,
  quickAddFromHandle,
  renameNode,
  setCatchError,
});

const hasUnsavedChanges = computed(() => !readonly.value && serializeGraph(graph.value) !== savedSnapshot.value);

// ---------- 撤销/重做（蓝本：500ms 防抖快照，上限 100） ----------

const history = ref<string[]>([]);
const historyIndex = ref(-1);
const canUndo = computed(() => historyIndex.value > 0);
const canRedo = computed(() => historyIndex.value < history.value.length - 1);
let historyTimer: ReturnType<typeof setTimeout> | null = null;
let restoringHistory = false;

watch(
  () => serializeGraph(graph.value),
  (json) => {
    if (restoringHistory) return;
    if (historyTimer) clearTimeout(historyTimer);
    historyTimer = setTimeout(() => pushHistory(json), 500);
  }
);

function pushHistory(json: string) {
  if (history.value[historyIndex.value] === json) return;
  history.value = history.value.slice(0, historyIndex.value + 1);
  history.value.push(json);
  if (history.value.length > 100) history.value.shift();
  historyIndex.value = history.value.length - 1;
}

function undo() {
  if (!canUndo.value) return;
  historyIndex.value -= 1;
  restoreHistory();
}

function redo() {
  if (!canRedo.value) return;
  historyIndex.value += 1;
  restoreHistory();
}

function restoreHistory() {
  restoringHistory = true;
  const { graph: parsed } = parsePersistedGraph(history.value[historyIndex.value]);
  graph.value = parsed;
  syncFlowFromGraph();
  nextTick(() => {
    restoringHistory = false;
  });
}

function handlePaneReady() {
  fitWhenMeasured();
}

/**
 * 路由过渡帧里挂载的节点 offsetWidth 为 0，Vue Flow 初测失败后不会自愈（尺寸不再变化，
 * ResizeObserver 不会再触发），表现为 dimensions 恒 0、fitView 直接 resolve(false)。
 * 加载完成后强制补测所有节点尺寸（顺带修复 handleBounds），量到后再取景。
 */
function fitWhenMeasured(attempt = 0) {
  const nodes = getNodes.value;
  if (nodes.length) {
    const { updates, allMeasured } = getMeasuredNodeElementUpdates(nodes.map((node) => node.id));
    if (updates.length) updateNodeDimensions(updates);
    if (allMeasured) {
      requestAnimationFrame(() => fitView({ padding: 0.3 }));
      return;
    }
  }
  if (attempt < 40) setTimeout(() => fitWhenMeasured(attempt + 1), 100);
}

// ---------- graph <-> vue-flow 桥接 ----------

function toFlowNode(node: StoreNodeItemType): Node {
  return {
    id: node.nodeId,
    type: 'flowNode',
    position: node.position,
    deletable: canRemoveNode(node.nodeId),
    // 容器子节点跟随父节点移动（蓝本嵌套容器父子关系）
    ...(node.parentNodeId ? { parentNode: node.parentNodeId } : {}),
    data: { node },
  };
}

function toFlowEdge(edge: WorkflowGraphType['edges'][number]): Edge {
  return {
    id: `${edge.source}__${edge.sourceHandle}__${edge.target}`,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
    ...defaultEdgeOptions,
  } as Edge;
}

function syncFlowFromGraph() {
  flowNodes.value = graph.value.nodes.map(toFlowNode);
  flowEdges.value = graph.value.edges.map(toFlowEdge);
}

function handleConnect(connection: Connection) {
  if (readonly.value) return;
  if (!connection.source || !connection.target || connection.source === connection.target) return;
  const sourceHandle = connection.sourceHandle || getHandleId(connection.source, 'source', 'right');
  const targetHandle = connection.targetHandle || getHandleId(connection.target, 'target', 'left');
  // 工具边约束（蓝本）：工具锚点只能连工具锚点，普通锚点不能连工具锚点
  const sourceIsTool = sourceHandle === TOOL_EDGE_HANDLE;
  const targetIsTool = targetHandle === TOOL_EDGE_HANDLE;
  if (sourceIsTool !== targetIsTool) {
    message.warning('工具锚点只能与工具锚点连接');
    return;
  }
  const exists = graph.value.edges.some(
    (edge) => edge.source === connection.source && edge.sourceHandle === sourceHandle && edge.target === connection.target
  );
  if (exists) return;
  // L9：普通流程边禁止成环（后端 LangGraph 不支持自由环、legacy 会走回边护栏），编辑期直接拦截
  if (!sourceIsTool && wouldCreateCycle(connection.source, connection.target)) {
    message.warning('不能连接成环：目标节点已可回到起点');
    return;
  }
  const sourceNode = graph.value.nodes.find((item) => item.nodeId === connection.source);
  const targetNode = graph.value.nodes.find((item) => item.nodeId === connection.target);
  if (sourceNode?.parentNodeId && targetNode && !targetNode.parentNodeId) {
    const parent = graph.value.nodes.find((item) => item.nodeId === sourceNode.parentNodeId);
    if (parent) {
      targetNode.parentNodeId = sourceNode.parentNodeId;
      targetNode.position = {
        x: targetNode.position.x - parent.position.x,
        y: targetNode.position.y - parent.position.y,
      };
      syncFlowFromGraph();
    }
  }
  const edge = { source: connection.source, sourceHandle, target: connection.target, targetHandle };
  graph.value.edges.push(edge);
  flowEdges.value.push(toFlowEdge(edge));
}

function handleNodeDragStop({ node, nodes }: { node: Node; nodes?: Node[] }) {
  // 多选拖动时 vue-flow 传全部被拖节点；只回写主节点会丢其余节点位置（gap-audit §13.2）
  const moved = nodes?.length ? nodes : [node];
  moved.forEach((item) => {
    const target = graph.value.nodes.find((graphNode) => graphNode.nodeId === item.id);
    if (target) target.position = { x: item.position.x, y: item.position.y };
  });
  syncFlowFromGraph();
}

function handleNodesChange(changes: NodeChange[]) {
  if (readonly.value) return;
  changes.forEach((change) => {
    if (change.type === 'remove') {
      removeNodeFromGraph(change.id);
    }
  });
}

function handleEdgesChange(changes: EdgeChange[]) {
  if (readonly.value) return;
  changes.forEach((change) => {
    if (change.type === 'remove') {
      const [source, sourceHandle, target] = change.id.split('__');
      graph.value.edges = graph.value.edges.filter(
        (edge) => !(edge.source === source && edge.sourceHandle === sourceHandle && edge.target === target)
      );
    }
  });
}

// ---------- 节点操作（编辑器上下文） ----------

/** L9：新增 source→target 是否会成环（target 经现有流程边可回到 source）。工具边不计入。 */
function wouldCreateCycle(source: string, target: string): boolean {
  const adj = new Map<string, string[]>();
  graph.value.edges.forEach((edge) => {
    if (edge.sourceHandle === TOOL_EDGE_HANDLE) return;
    if (!adj.has(edge.source)) adj.set(edge.source, []);
    adj.get(edge.source)!.push(edge.target);
  });
  const stack = [target];
  const seen = new Set<string>();
  while (stack.length) {
    const cur = stack.pop()!;
    if (cur === source) return true;
    if (seen.has(cur)) continue;
    seen.add(cur);
    (adj.get(cur) || []).forEach((next) => stack.push(next));
  }
  return false;
}

/** H4：深度重写引用二元组 [nodeId, key]——被复制集合内的节点 id 换成新 id（含 updateList/ifElseList 等嵌套引用）。 */
function deepRemapRefs(value: any, idMap: Map<string, string>): any {
  if (Array.isArray(value)) {
    if (
      value.length === 2 &&
      typeof value[0] === 'string' &&
      typeof value[1] === 'string' &&
      idMap.has(value[0])
    ) {
      return [idMap.get(value[0]), value[1]];
    }
    return value.map((item) => deepRemapRefs(item, idMap));
  }
  if (value && typeof value === 'object') {
    const out: Record<string, any> = {};
    for (const key of Object.keys(value)) out[key] = deepRemapRefs(value[key], idMap);
    return out;
  }
  return value;
}

/** H4/H6：复制一组节点（+内部边），统一分配新 nodeId 并重映射引用、parentNodeId、边与 handle。 */
function cloneNodeSet(
  sourceNodes: StoreNodeItemType[],
  sourceEdges: WorkflowGraphType['edges'],
  offset = 40
): { nodes: StoreNodeItemType[]; edges: WorkflowGraphType['edges'] } {
  const idMap = new Map<string, string>();
  sourceNodes.forEach((node) => idMap.set(node.nodeId, `${node.flowNodeType}_${getNanoid()}`));

  const nodes = sourceNodes.map((src) => {
    const clone: StoreNodeItemType = JSON.parse(JSON.stringify(src));
    clone.nodeId = idMap.get(src.nodeId)!;
    // 重映射 parentNodeId：父也在集合内 → 新父；父不在 → 脱离容器（避免孤儿指向未复制的容器）
    if (clone.parentNodeId) {
      if (idMap.has(clone.parentNodeId)) clone.parentNodeId = idMap.get(clone.parentNodeId);
      else delete clone.parentNodeId;
    }
    // 子节点位置相对父容器，保持不动；顶层节点按 offset 错开，避免叠在原节点上
    if (!clone.parentNodeId) {
      clone.position = { x: (src.position?.x || 0) + offset, y: (src.position?.y || 0) + offset };
    }
    if (clone.inputs) clone.inputs = deepRemapRefs(clone.inputs, idMap);
    return clone;
  });

  const remapHandle = (handle: string | undefined, oldId: string, newId: string) => {
    if (!handle || handle === TOOL_EDGE_HANDLE) return handle;
    return handle.startsWith(`${oldId}-`) ? `${newId}${handle.slice(oldId.length)}` : handle;
  };
  const edges = (sourceEdges || [])
    .filter((edge) => idMap.has(edge.source) && idMap.has(edge.target))
    .map((edge) => {
      const ns = idMap.get(edge.source)!;
      const nt = idMap.get(edge.target)!;
      return {
        source: ns,
        target: nt,
        sourceHandle: remapHandle(edge.sourceHandle, edge.source, ns),
        targetHandle: remapHandle(edge.targetHandle, edge.target, nt),
      };
    });
  return { nodes, edges };
}

/** L10：在现有节点名基础上生成唯一显示名。 */
function uniqueNodeName(base: string): string {
  const names = new Set(graph.value.nodes.map((node) => node.name));
  if (!names.has(base)) return base;
  let i = 2;
  while (names.has(`${base} ${i}`)) i += 1;
  return `${base} ${i}`;
}

function isProtectedFromDirectDelete(node: StoreNodeItemType): boolean {
  return isProtectedFromDirectRemoval(graph.value.nodes, node, (item) =>
    Boolean(getTemplateByFlowNodeType(item.flowNodeType)?.forbidDelete),
  );
}

function canRemoveNode(nodeId: string): boolean {
  return collectRemovableNodeIds(graph.value.nodes, [nodeId], isProtectedFromDirectDelete).has(nodeId);
}

function removeNodeFromGraph(nodeId: string) {
  const removeIds = collectRemovableNodeIds(graph.value.nodes, [nodeId], isProtectedFromDirectDelete);
  if (!removeIds.size) return;
  graph.value.nodes = graph.value.nodes.filter((item) => !removeIds.has(item.nodeId));
  graph.value.edges = graph.value.edges.filter(
    (edge) => !removeIds.has(edge.source) && !removeIds.has(edge.target)
  );
  pruneDanglingReferences(graph.value);
}

function removeNode(nodeId: string) {
  removeNodeFromGraph(nodeId);
  syncFlowFromGraph();
}

function duplicateNode(nodeId: string) {
  const source = graph.value.nodes.find((item) => item.nodeId === nodeId);
  if (!source) return;
  const template = getTemplateByFlowNodeType(source.flowNodeType);
  if (template?.unique) return;
  // H6：容器连同子节点与内部边一起复制；普通节点等价于单节点复制
  const children = graph.value.nodes.filter((item) => item.parentNodeId === nodeId);
  const setIds = new Set<string>([nodeId, ...children.map((c) => c.nodeId)]);
  const setEdges = graph.value.edges.filter((edge) => setIds.has(edge.source) && setIds.has(edge.target));
  const { nodes, edges } = cloneNodeSet([source, ...children], setEdges, 60);
  const topClone = nodes.find((item) => !item.parentNodeId);
  if (topClone) topClone.name = uniqueNodeName(`${source.name} 副本`); // L10：唯一化显示名
  graph.value.nodes.push(...nodes);
  graph.value.edges.push(...edges);
  syncFlowFromGraph();
}

function renameNode(nodeId: string, name: string) {
  const node = graph.value.nodes.find((item) => item.nodeId === nodeId);
  if (node) node.name = name;
}

function setCatchError(nodeId: string, value: boolean) {
  const node = graph.value.nodes.find((item) => item.nodeId === nodeId);
  if (!node) return;
  node.catchError = value;
  if (!value) {
    // 关闭报错捕获时同步移除该节点的错误边（蓝本行为：错误锚点消失，边不能悬空）
    const errorHandle = getErrorHandleId(nodeId);
    graph.value.edges = graph.value.edges.filter((edge) => edge.sourceHandle !== errorHandle);
    syncFlowFromGraph();
  }
}

function addNodeFromTemplate(
  template: FlowNodeTemplateType,
  position?: { x: number; y: number },
  parentNodeId?: string
): StoreNodeItemType | undefined {
  if (readonly.value) return undefined;
  if (template.unique && graph.value.nodes.some((node) => node.flowNodeType === template.flowNodeType)) {
    message.warning(`${template.name} 节点只能存在一个`);
    return undefined;
  }
  const fallback = screenToFlowCoordinate({
    x: window.innerWidth / 2 - (Math.random() * 80 + 40),
    y: window.innerHeight / 2 - (Math.random() * 80 + 120),
  });
  const node = createNodeFromTemplate(template, position || fallback);
  if (parentNodeId) node.parentNodeId = parentNodeId;
  // 蓝本 §7.3.5：画布内唯一命名 + workflowStart 默认引用回填
  node.name = computeUniqueNodeName(template.name, graph.value);
  applyDefaultReferences(node, graph.value);
  // 容器约束（蓝本）：loopRunBreak 必须位于循环容器内；容器自动创建内部起点
  if (template.flowNodeType === FlowNodeTypeEnum.loopRunBreak) {
    const container = parentNodeId
      ? graph.value.nodes.find((item) => item.nodeId === parentNodeId && item.flowNodeType === FlowNodeTypeEnum.loopRun)
      : graph.value.nodes.find((item) => item.flowNodeType === FlowNodeTypeEnum.loopRun);
    if (!container) {
      message.warning('「跳出循环」必须放在循环容器内，请先添加循环节点');
      return undefined;
    }
    node.parentNodeId = container.nodeId;
    node.position = { x: 60, y: 320 };
  }
  graph.value.nodes.push(node);
  flowNodes.value.push(toFlowNode(node));
  if (
    template.flowNodeType === FlowNodeTypeEnum.loopRun ||
    template.flowNodeType === FlowNodeTypeEnum.parallelRun
  ) {
    const start = createNodeFromTemplate(LoopRunStartTemplate, { x: 40, y: 160 });
    start.parentNodeId = node.nodeId;
    graph.value.nodes.push(start);
    flowNodes.value.push(toFlowNode(start));
  }
  return node;
}

function toggleTemplateMenu() {
  quickAddRequest.value = null;
  templateMenuVisible.value = !templateMenuVisible.value;
}

function closeTemplateMenu() {
  quickAddRequest.value = null;
  templateMenuVisible.value = false;
}

function quickAddFromHandle(sourceNodeId: string, sourceHandle: string) {
  if (readonly.value || sourceHandle === TOOL_EDGE_HANDLE) return;
  const source = graph.value.nodes.find((node) => node.nodeId === sourceNodeId);
  if (!source) return;
  quickAddRequest.value = {
    sourceNodeId,
    sourceHandle,
    position: { x: source.position.x + 560, y: source.position.y },
  };
  templateMenuVisible.value = true;
}

function handleTemplateAdd(template: FlowNodeTemplateType) {
  const request = quickAddRequest.value;
  const startNode = graph.value.nodes.find((item) => item.flowNodeType === FlowNodeTypeEnum.workflowStart);
  const hasBusinessNode = graph.value.nodes.some(
    (item) => item.flowNodeType !== FlowNodeTypeEnum.workflowStart && item.flowNodeType !== FlowNodeTypeEnum.systemConfig,
  );
  // 第一个业务节点从工具栏添加时也必须接入开始节点；否则画布看似有节点，发布校验却会因不可达失败。
  const implicitStartRequest = !request && !hasBusinessNode && startNode
    ? {
        sourceNodeId: startNode.nodeId,
        sourceHandle: getHandleId(startNode.nodeId, 'source', 'right'),
        position: { x: startNode.position.x + 560, y: startNode.position.y },
    }
    : undefined;
  const toolCallNode = !request && template.flowNodeType === FlowNodeTypeEnum.tool
    ? graph.value.nodes.find((item) => item.flowNodeType === FlowNodeTypeEnum.toolCall)
    : undefined;
  // 系统/自定义工具若从目录直接添加，应当真实挂载到已有工具调用节点；否则画布虽然有工具，运行时不会注册它。
  const implicitToolRequest = toolCallNode
    ? {
        sourceNodeId: toolCallNode.nodeId,
        sourceHandle: TOOL_EDGE_HANDLE,
        position: { x: toolCallNode.position.x, y: toolCallNode.position.y + 460 },
      }
    : undefined;
  const connectionRequest = request || implicitStartRequest || implicitToolRequest;
  const source = connectionRequest
    ? graph.value.nodes.find((item) => item.nodeId === connectionRequest.sourceNodeId)
    : undefined;
  const inheritedParentNodeId = source?.parentNodeId;
  const node = addNodeFromTemplate(template, connectionRequest?.position, inheritedParentNodeId);
  if (connectionRequest && node) {
    handleConnect({
      source: connectionRequest.sourceNodeId,
      sourceHandle: connectionRequest.sourceHandle,
      target: node.nodeId,
      targetHandle: connectionRequest.sourceHandle === TOOL_EDGE_HANDLE
        ? TOOL_EDGE_HANDLE
        : getHandleId(node.nodeId, 'target', 'left'),
    } as Connection);
  }
  closeTemplateMenu();
}

async function handleDrop(event: DragEvent) {
  // 动态节点拖拽只带摘要：drop 时异步取 previewNode（蓝本：失败不落残缺节点）
  const summaryJson = event.dataTransfer?.getData('application/workflow-node-summary');
  const position = screenToFlowCoordinate({ x: event.clientX, y: event.clientY });
  if (summaryJson) {
    let summary: { id: string } | undefined;
    try {
      summary = JSON.parse(summaryJson);
    } catch {
      summary = undefined;
    }
    if (!summary?.id) return;
    try {
      const template = await queryNodeTemplatePreview({ id: summary.id, excludeAppId: workflowApp.value?.id });
      addNodeFromTemplate(template as unknown as FlowNodeTemplateType, position);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取工具详情失败');
    }
    return;
  }
  // 本地模板：完整模板载荷优先，静态类型查表兜底
  let template: FlowNodeTemplateType | undefined;
  const templateJson = event.dataTransfer?.getData('application/workflow-node-template');
  if (templateJson) {
    try {
      template = JSON.parse(templateJson);
    } catch {
      template = undefined;
    }
  }
  if (!template) {
    const flowNodeType = event.dataTransfer?.getData('application/workflow-node');
    if (!flowNodeType) return;
    template = getTemplateByFlowNodeType(flowNodeType);
  }
  if (!template) return;
  addNodeFromTemplate(template, position);
}

// ---------- 加载 / 保存 / 发布 / 调试 ----------

async function loadWorkflow() {
  if (!workflowAppId.value && !appInfoId.value) {
    message.warning('缺少应用 ID');
    return;
  }
  loading.value = true;
  try {
    workflowApp.value = workflowAppId.value
      ? await queryWorkflowAppById(workflowAppId.value)
      : await queryWorkflowApp({ appInfoId: appInfoId.value, aiAppType: 'workflow' });
    const definition = await queryWorkflowDefinition({
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      appId: workflowApp.value?.id,
    });
    const { graph: parsed, migrated, dropped } = parsePersistedGraph(definition?.draftJson);
    graph.value = parsed;
    syncFlowFromGraph();
    savedSnapshot.value = serializeGraph(graph.value);
    // 历史栈以加载完成的图为基线
    history.value = [savedSnapshot.value];
    historyIndex.value = 0;
    // pane-ready 可能早于数据返回（此时 fit 的是默认图），加载完成、节点量出尺寸后按真实图重新取景
    fitWhenMeasured();
    if (!readonly.value) offerLocalBackupRestore();
    if (migrated) {
      message.info(
        dropped.length
          ? `旧版草稿已迁移到新编排模型，${dropped.length} 个不支持的节点被移除：${dropped.join('、')}`
          : '旧版草稿已迁移到新编排模型，保存后生效'
      );
    }
  } catch (error) {
    console.error('load workflow failed', error);
    message.error('工作流加载失败');
  } finally {
    loading.value = false;
  }
}

async function loadOptions() {
  modelLoading.value = true;
  try {
    const options = await queryWorkflowModelOptions();
    modelOptions.value = Array.isArray(options) && options.length ? options : [{ label: '默认模型', value: 'default' }];
  } catch {
    modelOptions.value = [{ label: '默认模型', value: 'default' }];
  } finally {
    modelLoading.value = false;
  }
  knowledgeLoading.value = true;
  try {
    knowledgeOptions.value = await loadWorkflowSelectableKnowledgeOptions(100);
  } catch {
    knowledgeOptions.value = [];
  } finally {
    knowledgeLoading.value = false;
  }
}

// ---------- 本地草稿备份（蓝本：离开/卸载自动保存 + 本地草稿兜底） ----------

// 备份里是整张编排图，按登录用户作用域存（peopleCenter/utils/userScopedStorage）：
// 公用机换账号打开同一应用不能被弹「恢复别人的本地草稿」；退出登录随作用域一起清。
const backupKey = computed(() => `wf-draft-backup:${workflowApp.value?.id || appInfoId.value || 'unknown'}`);

function writeLocalBackup() {
  // 未登录 / 存储不可用（隐私模式、配额满）时 writeUserScoped 静默放弃，不阻塞主流程
  writeUserScoped(backupKey.value, JSON.stringify({ json: serializeGraph(graph.value), savedAt: Date.now() }));
}

function clearLocalBackup() {
  removeUserScoped(backupKey.value);
}

/** 载入后检查本地备份：比服务器草稿新且内容不同 -> 询问恢复（保存失败/意外关闭的兜底） */
function offerLocalBackupRestore() {
  let backup: { json?: string; savedAt?: number } | null = null;
  try {
    backup = JSON.parse(readUserScoped(backupKey.value) || 'null');
  } catch {
    backup = null;
  }
  if (!backup?.json || backup.json === savedSnapshot.value) {
    clearLocalBackup();
    return;
  }
  Modal.confirm({
    title: '发现未保存的本地草稿',
    content: `本地存有 ${new Date(backup.savedAt || 0).toLocaleString()} 的未保存修改，是否恢复？`,
    okText: '恢复本地草稿',
    cancelText: '丢弃',
    onOk: () => {
      const { graph: parsed } = parsePersistedGraph(backup!.json);
      graph.value = parsed;
      syncFlowFromGraph();
      fitWhenMeasured();
      message.info('已恢复本地草稿，请及时保存');
    },
    onCancel: clearLocalBackup,
  });
}

/** 保存草稿；返回是否成功（路由守卫据此决定放行）。失败时写本地备份兜底 */
async function saveDraft(silent: unknown = false): Promise<boolean> {
  const notify = silent !== true;
  if (readonly.value) return false;
  if (!workflowApp.value?.id && !appInfoId.value) return false;
  saving.value = true;
  try {
    const json = serializeGraph(graph.value);
    await saveWorkflowDefinition({
      appId: workflowApp.value?.id,
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      workflowJson: json,
    });
    savedSnapshot.value = json;
    clearLocalBackup();
    if (notify) message.success({ content: '草稿已保存', key: 'workflow-draft-save' });
    return true;
  } catch (error) {
    console.error('save draft failed', error);
    writeLocalBackup();
    if (notify) message.error({ content: '保存失败，修改已暂存到本地', key: 'workflow-draft-save' });
    return false;
  } finally {
    saving.value = false;
  }
}

async function handleSaveDraft() {
  await saveDraft(false);
}

// 预览：静默保存当前草稿后，新窗口打开运行页用最新草稿运行（行为同发布审核预览）
const previewing = ref(false);
async function handlePreview() {
  if (!workflowApp.value?.id) {
    message.warning('请先创建应用');
    return;
  }
  previewing.value = true;
  try {
    const ok = await saveDraft(true);
    if (!ok) {
      message.error('草稿保存失败，无法预览');
      return;
    }
    const target = getAiAppDraftPreviewRoute(workflowApp.value.id);
    window.open(router.resolve(target).href, '_blank');
  } finally {
    previewing.value = false;
  }
}

/** 调试轨迹回写画布：节点 debugResult 覆盖态 + 连线 waiting/active/skipped 状态（蓝本 ChatTest 行为） */
function applyDebugTrace(result: Pick<WorkflowRunResponse, 'nodeRuns' | 'edges'> | null) {
  graph.value.nodes.forEach((node) => {
    delete node.debugResult;
  });
  (result?.nodeRuns || []).forEach((run: any) => {
    const node = graph.value.nodes.find((item) => item.nodeId === run.nodeId);
    if (!node) return;
    const status = run.status === 'success' ? 'success' : run.status === 'skipped' ? 'skipped' : 'failed';
    node.debugResult = { status, message: run.error, response: run.output };
  });
  const statusByEdge = new Map(
    (result?.edges || []).map((edge) => [`${edge.source}__${edge.sourceHandle}__${edge.target}`, edge.status])
  );
  flowEdges.value = flowEdges.value.map((edge) => ({
    ...edge,
    data: { ...(edge.data || {}), status: statusByEdge.get(edge.id) },
  }));
}

function buildDebugVariables(extra: Record<string, any>) {
  return buildWorkflowRuntimeVariables({
    userInfo: userStore.getUserInfo,
    histories: [],
    extra,
  });
}

async function runDebug(payload: { input: string; variables: Record<string, any> }) {
  if (!payload.input.trim()) {
    message.warning('请输入调试问题');
    return;
  }
  debugRunning.value = true;
  debugResult.value = null;
  debugStepSession.value = null;
  applyDebugTrace(null);
  try {
    debugResult.value = await debugWorkflowDefinition({
      appId: workflowApp.value?.id,
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      input: payload.input,
      workflowJson: serializeGraph(graph.value),
      variables: buildDebugVariables(payload.variables),
    });
  } catch (error: any) {
    debugResult.value = {
      runId: '',
      status: 'failed',
      output: '',
      errorMessage: error?.message || '调试请求失败',
      durationMs: 0,
      nodeRuns: [],
    };
  } finally {
    applyDebugTrace(debugResult.value);
    debugRunning.value = false;
  }
}

async function startStepDebug(payload: { input: string; variables: Record<string, any> }) {
  if (!payload.input.trim()) {
    message.warning('请输入调试问题');
    return;
  }
  debugRunning.value = true;
  debugResult.value = null;
  debugStepSession.value = null;
  applyDebugTrace(null);
  try {
    debugStepSession.value = await createWorkflowDebugSession({
      appId: workflowApp.value?.id,
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      input: payload.input,
      workflowJson: serializeGraph(graph.value),
      variables: buildDebugVariables(payload.variables),
    });
    applyDebugTrace(debugStepSession.value);
  } catch (error: any) {
    message.error(error?.message || '创建单步调试失败');
  } finally {
    debugRunning.value = false;
  }
}

async function nextStepDebug(payload: { variables: Record<string, any> }) {
  const session = debugStepSession.value;
  if (!session || session.status !== 'paused' || debugRunning.value) return;
  debugRunning.value = true;
  try {
    debugStepSession.value = await stepWorkflowDebugSession(session.sessionId, payload.variables);
    applyDebugTrace(debugStepSession.value);
  } catch (error: any) {
    message.error(error?.message || '执行下一节点失败，请重新开始调试');
  } finally {
    debugRunning.value = false;
  }
}

async function stopStepDebug() {
  const session = debugStepSession.value;
  if (!session || session.status !== 'paused' || debugRunning.value) return;
  debugRunning.value = true;
  try {
    debugStepSession.value = await stopWorkflowDebugSession(session.sessionId);
    applyDebugTrace(debugStepSession.value);
  } catch (error: any) {
    message.error(error?.message || '结束单步调试失败');
  } finally {
    debugRunning.value = false;
  }
}

async function resumeDebug(payload: { resumeId: string; value: string | Record<string, any> }) {
  if (!debugResult.value?.interactive || debugRunning.value) return;
  debugRunning.value = true;
  try {
    const previous = debugResult.value;
    const resumed = await resumeWorkflowDefinition({
      appId: workflowApp.value?.id,
      appInfoId: workflowApp.value?.appInfoId || appInfoId.value || undefined,
      resumeId: payload.resumeId,
      value: payload.value,
      workflowJson: serializeGraph(graph.value),
    });
    debugResult.value = {
      ...resumed,
      output: `${previous.output || ''}${resumed.output || ''}`,
      nodeRuns: [...(previous.nodeRuns || []), ...(resumed.nodeRuns || [])],
    };
  } catch (error: any) {
    debugResult.value = {
      ...(debugResult.value || {
        runId: payload.resumeId,
        status: 'failed',
        output: '',
        durationMs: 0,
        nodeRuns: [],
      }),
      status: 'failed',
      errorMessage: error?.message || '恢复调试失败',
    };
    message.error(debugResult.value.errorMessage || '恢复调试失败');
  } finally {
    applyDebugTrace(debugResult.value);
    debugRunning.value = false;
  }
}

function handleBack() {
  router.back();
}

// ---------- 复制/粘贴（系统剪贴板优先，本页内存剪贴板兜底，粘贴重建 nodeId） ----------

async function copySelectedNodes() {
  const selected = getSelectedNodes.value;
  if (!selected.length) return;
  const selectedIds = new Set(selected.map((item) => item.id));
  // H6：选中容器时连同其子节点一起复制（否则粘贴出空壳容器）
  graph.value.nodes.forEach((node) => {
    if (node.parentNodeId && selectedIds.has(node.parentNodeId)) selectedIds.add(node.nodeId);
  });
  const nodes = graph.value.nodes.filter((node) => selectedIds.has(node.nodeId));
  if (!nodes.length) return;
  // 一并复制集合内部边（H4：粘贴时重建为新 id 的连线）
  const edges = graph.value.edges.filter((edge) => selectedIds.has(edge.source) && selectedIds.has(edge.target));
  const systemClipboardOk = await writeWorkflowClipboardPayload({ nodes, edges });
  message.success(systemClipboardOk ? `已复制 ${nodes.length} 个节点` : `已复制 ${nodes.length} 个节点（当前页面可粘贴）`);
}

async function pasteNodes() {
  const payload = await readWorkflowClipboardPayload();
  if (!payload) return;
  // 过滤无模板 / unique 冲突节点，再统一克隆（H4：内部引用、parentNodeId、边一并重映射为新 id）
  const sourceNodes: StoreNodeItemType[] = payload.nodes.filter((source: StoreNodeItemType) => {
    const template = getTemplateByFlowNodeType(source.flowNodeType);
    if (!template) return false;
    if (template.unique && graph.value.nodes.some((node) => node.flowNodeType === source.flowNodeType)) return false;
    return true;
  });
  if (!sourceNodes.length) return;
  const sourceEdges = Array.isArray(payload.edges) ? payload.edges : [];
  const { nodes, edges } = cloneNodeSet(sourceNodes, sourceEdges, 40);
  graph.value.nodes.push(...nodes);
  graph.value.edges.push(...edges);
  pruneDanglingReferences(graph.value);
  syncFlowFromGraph();
  selectNodes(nodes.map((node) => node.nodeId));
  message.success(`已粘贴 ${nodes.length} 个节点`);
}

function selectNodes(nodeIds: string[]) {
  const selectedIds = new Set(nodeIds);
  flowNodes.value = flowNodes.value.map((node) => ({ ...node, selected: selectedIds.has(node.id) }));
  flowEdges.value = flowEdges.value.map((edge) => ({ ...edge, selected: false }));
}

function clearSelection() {
  selectNodes([]);
}

function removeSelectedElements() {
  const selectedNodeIds = flowNodes.value.filter((node) => node.selected).map((node) => node.id);
  const selectedEdgeIds = new Set(flowEdges.value.filter((edge) => edge.selected).map((edge) => edge.id));
  const removeNodeIds = collectRemovableNodeIds(graph.value.nodes, selectedNodeIds, isProtectedFromDirectDelete);
  if (!removeNodeIds.size && !selectedEdgeIds.size) return;
  graph.value.nodes = graph.value.nodes.filter((node) => !removeNodeIds.has(node.nodeId));
  graph.value.edges = graph.value.edges.filter((edge) => {
    const edgeId = `${edge.source}__${edge.sourceHandle}__${edge.target}`;
    return !removeNodeIds.has(edge.source) && !removeNodeIds.has(edge.target) && !selectedEdgeIds.has(edgeId);
  });
  pruneDanglingReferences(graph.value);
  syncFlowFromGraph();
}

function handleKeydown(event: KeyboardEvent) {
  const mod = event.metaKey || event.ctrlKey;
  const key = event.key.toLowerCase();
  const hasWorkflowSelection = getSelectedNodes.value.length > 0 || flowEdges.value.some((edge) => edge.selected);

  if (mod && key === 's') {
    if (shouldIgnoreWorkflowShortcut(event.target, undefined, { hasWorkflowSelection: true })) return;
    event.preventDefault();
    if (!readonly.value) saveDraft();
    return;
  }
  if (mod && shouldUseNativeTextShortcut(key, event.target)) return;

  if (shouldIgnoreWorkflowShortcut(event.target, undefined, { hasWorkflowSelection })) return;
  if (!mod && key === 'escape') {
    clearSelection();
    return;
  }
  if (!mod && !readonly.value && isWorkflowDeleteShortcut(key)) {
    event.preventDefault();
    removeSelectedElements();
    return;
  }
  if (!mod) return;
  if (key === 'c') {
    event.preventDefault();
    copySelectedNodes();
    return;
  }
  if (readonly.value) return; // 只读态屏蔽全部变更类快捷键
  if (key === 'z') {
    event.preventDefault();
    event.shiftKey ? redo() : undo();
  } else if (key === 'y') {
    event.preventDefault();
    redo();
  } else if (key === 'v') {
    event.preventDefault();
    pasteNodes();
  }
}

function handleBeforeUnload(event: BeforeUnloadEvent) {
  if (hasUnsavedChanges.value) {
    // 卸载无法等待网络请求：先同步写本地备份，下次进入编辑器时提示恢复
    writeLocalBackup();
    event.preventDefault();
    event.returnValue = '';
  }
}

// 蓝本行为：离开时自动保存（不弹「是否保存」）；保存失败才提示，本地备份兜底
onBeforeRouteLeave(async (_to, _from, next) => {
  if (!hasUnsavedChanges.value) return next();
  const saved = await saveDraft(true);
  if (saved) {
    message.success('草稿已自动保存');
    return next();
  }
  Modal.confirm({
    title: '自动保存失败',
    content: '修改已暂存到本地（下次打开可恢复）。仍要离开吗？',
    okText: '仍然离开',
    cancelText: '留在本页',
    onOk: () => next(),
    onCancel: () => next(false),
  });
});

onMounted(() => {
  loadWorkflow();
  loadOptions();
  window.addEventListener('keydown', handleKeydown);
  window.addEventListener('beforeunload', handleBeforeUnload);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  window.removeEventListener('beforeunload', handleBeforeUnload);
});
</script>

<style lang="less">
@import '@vue-flow/core/dist/style.css';
@import '@vue-flow/core/dist/theme-default.css';
@import '@vue-flow/minimap/dist/style.css';
</style>

<style scoped lang="less">
.workflow-editor-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 88px);
  min-height: 640px;
  background: #f8fafc;
}

.editor-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;

  .back-btn {
    border: 1px solid #e2e8f0;
    background: #ffffff;
    border-radius: 8px;
    width: 30px;
    height: 30px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    color: #334155;

    &:hover {
      border-color: #4f46e5;
      color: #4f46e5;
    }
  }

  .app-meta {
    display: flex;
    flex-direction: column;
    min-width: 0;

    strong {
      font-size: 14px;
      color: #0f172a;
      line-height: 1.3;
    }

    .app-sub {
      font-size: 11px;
      color: #94a3b8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 380px;
    }
  }

  .unsaved-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #f59e0b;
  }

  .header-actions {
    margin-left: auto;
    display: flex;
    gap: 8px;

    :deep(.ant-btn) {
      border-radius: 0;
    }
  }
}

.editor-body {
  position: relative;
  flex: 1;
  min-height: 0;

  .canvas-spin {
    height: 100%;

    :deep(.ant-spin-container) {
      height: 100%;
    }
  }
}

.workflow-canvas {
  width: 100%;
  height: 100%;
  // 蓝本画布底色，点阵由 <Background> 插件绘制（跟随视口缩放平移）
  background-color: #f7f8fa;

  :deep(.wf-minimap) {
    width: 150px;
    height: 92px;
    border: 1px solid #e4e7ee;
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(19, 51, 107, 0.08);
    overflow: hidden;
  }
}

// 四 Tab 模板面板：编辑区内全高左侧面板（蓝本 NodeTemplatesModal 460px）
.template-panel {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 6;
}

.canvas-toolbar {
  position: absolute;
  top: 16px;
  left: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  z-index: 5;
  transition: left 0.2s ease;

  &.toolbar-shifted {
    left: 476px;
  }

  .add-node-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 38px;
    padding: 0 16px;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
    box-shadow: 0 4px 14px rgba(17, 24, 36, 0.22);
  }

  .zoom-controls {
    display: flex;
    flex-direction: column;
    background: #ffffff;
    border: 1px solid #e4e7ee;
    border-radius: 10px;
    box-shadow: 0 4px 16px rgba(19, 51, 107, 0.08);
    overflow: hidden;
    width: fit-content;

    button {
      border: none;
      background: transparent;
      width: 38px;
      height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 15px;
      cursor: pointer;
      color: #485264;
      transition: background 0.12s ease;

      &:hover:not(:disabled) {
        background: #f0f1f6;
        color: #3370ff;
      }

      &:disabled {
        color: #c4cad6;
        cursor: not-allowed;
      }

      & + button {
        border-top: 1px solid #f0f1f6;
      }
    }
  }
}

</style>
