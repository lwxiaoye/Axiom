/**
 * 工作流模型工具：节点实例化、引用变量解析、分支 handle 计算、上游追溯。
 * handle 约定与蓝本一致：`${nodeId}-source-${key}` / `${nodeId}-target-${key}`，默认 key 为 left/right。
 */
import {
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTypeEnum,
  IfElseResultEnum,
  NodeInputKeyEnum,
  NodeOutputKeyEnum,
  VARIABLE_NODE_ID,
  SYSTEM_VARIABLES,
  WorkflowIOValueTypeEnum,
  getHandleId,
} from './constants';
import type {
  AppChatConfigType,
  FlowNodeTemplateType,
  ReferenceItemValueType,
  StoreEdgeItemType,
  StoreNodeItemType,
  WorkflowGraphType,
} from './type';

export function getNanoid(size = 6) {
  const chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
  let id = '';
  for (let i = 0; i < size; i++) {
    id += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return id;
}

/** 模板上必须随落图保存的元数据键（蓝本 FlowNodeCommonType；动态节点丢失即无法运行/发布） */
const TEMPLATE_META_KEYS = [
  'avatar',
  'avatarLinear',
  'colorSchema',
  'toolDescription',
  'showStatus',
  'version',
  'versionLabel',
  'isLatestVersion',
  'pluginId',
  'source',
  'isFolder',
  'pluginData',
  'toolConfig',
  'catchError',
] as const;

/** 由模板创建节点实例（深拷贝 inputs/outputs，分配 nodeId；动态节点元数据原样保留） */
export function createNodeFromTemplate(
  template: FlowNodeTemplateType,
  position: { x: number; y: number },
  nodeId?: string
): StoreNodeItemType {
  const node: StoreNodeItemType = {
    nodeId: nodeId || `${template.flowNodeType}_${getNanoid()}`,
    flowNodeType: template.flowNodeType,
    name: template.name,
    intro: template.intro,
    position: { ...position },
    // 蓝本 §7.3.5：实例化时过滤 deprecated IO
    inputs: JSON.parse(JSON.stringify((template.inputs || []).filter((item) => !item.deprecated))),
    outputs: JSON.parse(JSON.stringify((template.outputs || []).filter((item) => !item.deprecated))),
  };
  TEMPLATE_META_KEYS.forEach((key) => {
    const value = (template as Record<string, any>)[key];
    if (value !== undefined) {
      (node as Record<string, any>)[key] =
        typeof value === 'object' && value !== null ? JSON.parse(JSON.stringify(value)) : value;
    }
  });
  return node;
}

/**
 * 新节点默认引用回填（蓝本 §7.3.5）：用户问题类输入默认引用
 * 流程开始的用户问题；值优先级 已有 value > defaultValue > 默认引用。
 */
export function applyDefaultReferences(node: StoreNodeItemType, graph: WorkflowGraphType) {
  const start = graph.nodes.find((item) => item.flowNodeType === FlowNodeTypeEnum.workflowStart);
  if (!start) return;
  const defaults: Record<string, any> = {
    [NodeInputKeyEnum.userChatInput]: [start.nodeId, 'userChatInput'],
    [NodeInputKeyEnum.datasetSearchInput]: [start.nodeId, 'userChatInput'],
    question: [start.nodeId, 'userChatInput'],
    query_text: [start.nodeId, 'userChatInput'],
  };
  node.inputs.forEach((input) => {
    if (input.value === undefined && input.defaultValue !== undefined) {
      input.value = JSON.parse(JSON.stringify(input.defaultValue));
    }
    if (input.value === undefined && defaults[input.key] !== undefined) {
      input.value = defaults[input.key];
      const referenceIndex = input.renderTypeList.indexOf(FlowNodeInputTypeEnum.reference);
      if (referenceIndex >= 0) {
        input.selectedTypeIndex = referenceIndex;
      }
    }
  });
}

/** 画布内唯一命名（蓝本 computedNewNodeName）：同名已存在时追加序号 */
export function computeUniqueNodeName(baseName: string, graph: WorkflowGraphType): string {
  const names = new Set(graph.nodes.map((node) => node.name));
  if (!names.has(baseName)) return baseName;
  let index = 2;
  while (names.has(`${baseName} ${index}`)) index += 1;
  return `${baseName} ${index}`;
}

export function getNodeInput(node: StoreNodeItemType | undefined, key: string) {
  return node?.inputs?.find((item) => item.key === key);
}

/** 渲染器统一的写入口：输入项对象属于共享画布模型，组件不直接对 prop 赋值 */
export function setInputValue(input: { value?: any }, value: any) {
  input.value = value;
}

export function setInputRenderTypeIndex(input: { selectedTypeIndex?: number; value?: any }, index: number) {
  input.selectedTypeIndex = index;
  // 引用与字面量互斥，切换渲染器时清空避免脏值
  input.value = undefined;
}

/** 确保输入值为数组（列表型编辑器 setup 时初始化，避免在 computed 中产生副作用） */
export function ensureArrayValue<T = any>(input: { value?: any }, defaultValue: T[] = []): T[] {
  if (!Array.isArray(input.value)) {
    input.value = defaultValue;
  }
  return input.value;
}

export function setNodeOutputs(node: StoreNodeItemType, outputs: StoreNodeItemType['outputs']) {
  node.outputs = outputs;
}

/**
 * 重写节点的动态输入（蓝本 D-6）：动态项是 canEdit=true 的平级 input，
 * 插在 addInputParam 占位项之前；addInputParam 自身 value 不存行数据。
 */
export function replaceDynamicInputs(
  node: StoreNodeItemType,
  rows: { key: string; value?: any; valueType?: string; defaultValue?: any }[],
  addInputKey: string = NodeInputKeyEnum.addInputParam
) {
  const kept = node.inputs.filter((input) => !input.canEdit);
  const anchor = kept.findIndex((input) => input.key === addInputKey);
  const anchorInput = kept[anchor];
  const dynamicItems = rows
    .filter((row) => row.key.trim())
    .map((row) => ({
      key: row.key.trim(),
      label: row.key.trim(),
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      selectedTypeIndex: 0,
      valueType: (row.valueType as WorkflowIOValueTypeEnum) || WorkflowIOValueTypeEnum.any,
      canEdit: true,
      value: row.value,
      // 蓝本 customInputConfig 随动态项保留，供再次编辑时读取配置
      ...(anchorInput?.customInputConfig ? { customInputConfig: anchorInput.customInputConfig } : {}),
      ...(row.defaultValue !== undefined && row.defaultValue !== ''
        ? { defaultValue: row.defaultValue }
        : {}),
    }));
  const index = anchor >= 0 ? anchor : kept.length;
  node.inputs = [...kept.slice(0, index), ...dynamicItems, ...kept.slice(index)];
}

export function getNodeInputValue<T = any>(
  node: StoreNodeItemType | undefined,
  key: string,
  fallback?: T
): T {
  const input = getNodeInput(node, key);
  return (input?.value ?? fallback) as T;
}

/** 值是否为 [nodeId, outputKey] 形式的引用 */
export function isReferenceValue(value: any): value is ReferenceItemValueType {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    typeof value[0] === 'string' &&
    value[0].trim() !== '' &&
    typeof value[1] === 'string' &&
    value[1].trim() !== ''
  );
}

/**
 * 节点的出边分支 key 列表。
 * 判断器：每个条件组一个分支（IF / ELSE IF{n}），末尾固定 ELSE；
 * 问题分类：每个分类项 key 一个分支；
 * 其他节点：单一 right 分支（showSourceHandle 为 true 时）。
 */
export function getNodeSourceHandleKeys(node: StoreNodeItemType): { key: string; label: string }[] {
  if (node.flowNodeType === FlowNodeTypeEnum.ifElseNode) {
    const groups: any[] = getNodeInputValue(node, NodeInputKeyEnum.ifElseList, []);
    const keys = groups.map((_, index) => {
      const key = index === 0 ? IfElseResultEnum.IF : `${IfElseResultEnum.ELSE_IF} ${index}`;
      return { key, label: key };
    });
    keys.push({ key: IfElseResultEnum.ELSE, label: IfElseResultEnum.ELSE });
    return keys;
  }
  if (node.flowNodeType === FlowNodeTypeEnum.classifyQuestion) {
    const agents: { key: string; value: string }[] = getNodeInputValue(node, NodeInputKeyEnum.agents, []);
    return agents.map((agent) => ({ key: agent.key, label: agent.value || agent.key }));
  }
  if (node.flowNodeType === FlowNodeTypeEnum.userSelect) {
    const options: { key: string; value: string }[] = getNodeInputValue(node, NodeInputKeyEnum.userSelectOptions, []);
    return options.map((option) => ({ key: option.key, label: option.value || option.key }));
  }
  return [{ key: 'right', label: '' }];
}

export function buildEdge(source: string, target: string, sourceKey = 'right'): StoreEdgeItemType {
  return {
    source,
    sourceHandle: getHandleId(source, 'source', sourceKey),
    target,
    targetHandle: getHandleId(target, 'target', 'left'),
  };
}

/** 从 sourceHandle 解析分支 key（`${nodeId}-source-${key}` -> key） */
export function parseHandleKey(handle: string | undefined, nodeId: string, type: 'source' | 'target') {
  if (!handle) return type === 'source' ? 'right' : 'left';
  const prefix = `${nodeId}-${type}-`;
  return handle.startsWith(prefix) ? handle.slice(prefix.length) : handle;
}

export type ReferenceCandidate = {
  nodeId: string;
  nodeName: string;
  outputs: { key: string; label: string; valueType?: string }[];
};

export type TextVariableOption = {
  label: string;
  detail: string;
  value: string;
};

export type TextVariableGroup = {
  label: '节点变量' | '全局变量';
  options: TextVariableOption[];
};

export function buildTextareaVariableOptions(chatConfig?: AppChatConfigType) {
  const seen = new Set<string>();
  return [
    ...(chatConfig?.variables || []).map((item) => ({
      label: item.label || item.key,
      value: item.key,
    })),
    ...SYSTEM_VARIABLES.map((item) => ({ label: item.label, value: item.key })),
  ].filter((item) => {
    if (!item.value || seen.has(item.value)) return false;
    seen.add(item.value);
    return true;
  });
}

/** 无节点上下文的文本编辑器仍保留固定两组，节点变量组为空。 */
export function buildTextareaVariableGroups(chatConfig?: AppChatConfigType): TextVariableGroup[] {
  return [
    { label: '节点变量', options: [] },
    {
      label: '全局变量',
      options: buildTextareaVariableOptions(chatConfig).map((item) => ({ ...item, detail: item.value })),
    },
  ];
}

/** 文本编辑器的 / 变量候选：上游节点输出与全局变量始终分为固定两组。 */
export function buildTextVariableGroups(
  targetNodeId: string,
  nodes: StoreNodeItemType[],
  edges: StoreEdgeItemType[],
  chatConfig?: AppChatConfigType
): TextVariableGroup[] {
  const candidates = computeReferenceCandidates(targetNodeId, nodes, edges, chatConfig);
  const globals = candidates.find((candidate) => candidate.nodeId === VARIABLE_NODE_ID)?.outputs || [];
  const nodeOptions = candidates
    .filter((candidate) => candidate.nodeId !== VARIABLE_NODE_ID)
    .flatMap((candidate) =>
      candidate.outputs.map((output) => ({
        label: `${candidate.nodeName} / ${output.label}`,
        detail: output.key,
        value: `node:${candidate.nodeId}:${output.key}`,
      }))
    );

  return [
    { label: '节点变量', options: nodeOptions },
    {
      label: '全局变量',
      options: globals.map((output) => ({ label: output.label, detail: output.key, value: output.key })),
    },
  ];
}

/**
 * 计算某节点可引用的变量候选：全局/系统变量 + 沿入边向上追溯的祖先节点静态输出。
 * 与蓝本一致，引用范围限定上游，避免引用未执行节点的输出。
 */
export function computeReferenceCandidates(
  targetNodeId: string,
  nodes: StoreNodeItemType[],
  edges: StoreEdgeItemType[],
  chatConfig?: AppChatConfigType,
  options?: { includeChildren?: boolean }
): ReferenceCandidate[] {
  const ancestorDistances = collectAncestorDistances(targetNodeId, edges);
  const candidates: ReferenceCandidate[] = [];

  const globalOutputs = [
    ...(chatConfig?.variables || []).map((item) => ({
      key: item.key,
      label: item.label || item.key,
      valueType: item.valueType,
    })),
    ...SYSTEM_VARIABLES.map((item) => ({ key: item.key, label: item.label, valueType: item.valueType })),
  ];
  candidates.push({ nodeId: VARIABLE_NODE_ID, nodeName: '全局变量', outputs: globalOutputs });

  nodes
    .map((node, index) => ({ node, index, distance: ancestorDistances.get(node.nodeId) }))
    .filter((item): item is { node: StoreNodeItemType; index: number; distance: number } => item.distance !== undefined)
    .sort((a, b) => a.distance - b.distance || a.index - b.index)
    .forEach(({ node }) => {
      const outputs = (node.outputs || [])
        .filter((output) => output.key !== NodeOutputKeyEnum.addOutputParam)
        .map((output) => ({
          key: output.key,
          label: output.label || output.key,
          valueType: output.valueType,
        }));
      if (outputs.length) {
        candidates.push({ nodeId: node.nodeId, nodeName: node.name, outputs });
      }
    });

  if (options?.includeChildren) {
    nodes
      .filter((node) => node.parentNodeId === targetNodeId)
      .forEach((node) => {
        const outputs = (node.outputs || [])
          .filter((output) => output.key !== NodeOutputKeyEnum.addOutputParam)
          .map((output) => ({
            key: output.key,
            label: output.label || output.key,
            valueType: output.valueType,
          }));
        if (outputs.length && !candidates.some((candidate) => candidate.nodeId === node.nodeId)) {
          candidates.push({ nodeId: node.nodeId, nodeName: node.name, outputs });
        }
      });
  }

  return candidates;
}

function collectAncestorDistances(targetNodeId: string, edges: StoreEdgeItemType[]) {
  const distances = new Map<string, number>();
  const queue = [{ nodeId: targetNodeId, distance: 0 }];
  while (queue.length) {
    const current = queue.shift()!;
    edges
      .filter((edge) => edge.target === current.nodeId)
      .forEach((edge) => {
        if (!distances.has(edge.source)) {
          const distance = current.distance + 1;
          distances.set(edge.source, distance);
          queue.push({ nodeId: edge.source, distance });
        }
      });
  }
  return distances;
}

/** 引用显示文本：`节点名 / 输出名` */
export function formatReferenceLabel(
  value: ReferenceItemValueType | undefined,
  nodes: StoreNodeItemType[],
  chatConfig?: AppChatConfigType
) {
  if (!isReferenceValue(value)) return '';
  const [nodeId, outputKey] = value;
  if (nodeId === VARIABLE_NODE_ID) {
    const variable =
      (chatConfig?.variables || []).find((item) => item.key === outputKey) ||
      SYSTEM_VARIABLES.find((item) => item.key === outputKey);
    return `全局变量 / ${(variable as any)?.label || outputKey}`;
  }
  const node = nodes.find((item) => item.nodeId === nodeId);
  const output = node?.outputs?.find((item) => item.key === outputKey);
  return node ? `${node.name} / ${output?.label || outputKey}` : `已删除节点 / ${outputKey}`;
}

/**
 * 按 chatConfig.fileSelectConfig 动态维护 workflowStart 的文件输出（蓝本 systemConfig 文件开关行为）：
 * userFiles 保留 URL 兼容通道；userFileIds 是私有上传文件的受控读取通道。
 * 开启文件/图片上传时注入输出；关闭时移除输出并清理下游对它们的引用。
 */
export function syncUserFilesOutput(graph: WorkflowGraphType) {
  const start = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.workflowStart);
  if (!start) return;
  const config = graph.chatConfig?.fileSelectConfig;
  // 蓝本 NodeSystemConfig：五类文件开关任一开启即注入 userFiles 输出
  const enabled = !!(
    config?.canSelectFile ||
    config?.canSelectImg ||
    config?.canSelectVideo ||
    config?.canSelectAudio ||
    config?.canSelectCustomFileExtension
  );
  const fileOutputs = [
    {
      id: 'userFiles',
      key: 'userFiles',
      label: '文件链接',
      description: '用户提供的公开文件链接',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayString,
    },
    {
      id: 'userFileIds',
      key: 'userFileIds',
      label: '已上传文件',
      description: '用户上传到本平台的私有文件，仅支持受控读取节点引用',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayString,
    },
  ];
  if (enabled) {
    fileOutputs.forEach((output) => {
      if (!start.outputs.some((item) => item.key === output.key)) start.outputs.push(output);
    });
  } else {
    const outputKeys = new Set(fileOutputs.map((output) => output.key));
    start.outputs = start.outputs.filter((output) => !outputKeys.has(output.key));
    graph.nodes.forEach((node) => {
      node.inputs.forEach((input) => {
        if (isReferenceValue(input.value) && input.value[0] === start.nodeId && outputKeys.has(input.value[1])) {
          input.value = undefined;
        }
      });
    });
  }
}

/** 校验图中的引用是否仍有效（节点删除后清理悬空引用） */
export function pruneDanglingReferences(graph: WorkflowGraphType) {
  const nodeIds = new Set(graph.nodes.map((node) => node.nodeId));
  graph.nodes.forEach((node) => {
    node.inputs.forEach((input) => {
      if (isReferenceValue(input.value) && input.value[0] !== VARIABLE_NODE_ID && !nodeIds.has(input.value[0])) {
        input.value = undefined;
      }
    });
  });
  graph.edges = graph.edges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
}
