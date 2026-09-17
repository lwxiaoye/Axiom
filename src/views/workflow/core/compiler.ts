/**
 * 画布模型（FastGPT 对齐）与持久化信封之间的双向转换。
 *
 * 工作流运行时统一由 agent-api（Python，/agent-api/workflow）承担；
 * 持久化信封（WorkflowPersistType）以 fastgpt 画布模型为唯一事实源，
 * 顶层 nodes/edges 为旧 v0.1 编译格式，仅用于读取历史草稿迁移。
 */
import {
  DatasetSearchModeEnum,
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTypeEnum,
  IfElseResultEnum,
  NodeInputKeyEnum,
  NodeOutputKeyEnum,
  TOOL_EDGE_HANDLE,
  VariableConditionEnum,
  getHandleId,
} from './constants';
import {
  AiChatTemplate,
  AssignedAnswerTemplate,
  ClassifyQuestionTemplate,
  ContentExtractTemplate,
  DatasetSearchTemplate,
  HttpRequestTemplate,
  IfElseTemplate,
  SystemConfigTemplate,
  TextEditorTemplate,
  WorkflowStartTemplate,
  getTemplateByFlowNodeType,
} from './templates';
import type {
  IfElseListItemType,
  StoreEdgeItemType,
  StoreNodeItemType,
  WorkflowGraphType,
  WorkflowPersistType,
} from './type';
import {
  createNodeFromTemplate,
  getNodeInputValue,
  isReferenceValue,
  parseHandleKey,
  syncUserFilesOutput,
} from './utils';

/** 2.1.0：信封只含 fastgpt 画布模型，不再写入旧 v0.1 Java 编译结果 */
export const WORKFLOW_PERSIST_VERSION = '2.1.0';

type CompileWarning = { nodeId: string; flowNodeType: string; name: string; reason: string };

type LegacyNode = {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, any>;
};

type LegacyEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  [key: string]: any;
};

const searchModeMap: Record<string, string> = {
  [DatasetSearchModeEnum.mixedRecall]: 'hybrid',
  [DatasetSearchModeEnum.embedding]: 'semantic',
  [DatasetSearchModeEnum.fullTextRecall]: 'fulltext',
};

const conditionOperatorMap: Record<string, string> = {
  [VariableConditionEnum.equalTo]: 'equals',
  [VariableConditionEnum.notEqual]: 'notEquals',
  [VariableConditionEnum.include]: 'contains',
  [VariableConditionEnum.notInclude]: 'notContains',
  [VariableConditionEnum.isEmpty]: 'isEmpty',
  [VariableConditionEnum.isNotEmpty]: 'notEmpty',
  [VariableConditionEnum.startWith]: 'startsWith',
  [VariableConditionEnum.endWith]: 'endsWith',
  [VariableConditionEnum.reg]: 'regex',
  [VariableConditionEnum.greaterThan]: 'gt',
  [VariableConditionEnum.greaterThanOrEqualTo]: 'gte',
  [VariableConditionEnum.lessThan]: 'lt',
  [VariableConditionEnum.lessThanOrEqualTo]: 'lte',
};

/** 引用值 -> v0.1 模板变量。v0.1 运行时以 {{input}} 表示上游输入 */
function refToLegacyExpression(value: any, fallback = '{{input}}') {
  if (isReferenceValue(value)) return fallback;
  if (typeof value === 'string') return value || fallback;
  return fallback;
}

function legacyAppearance(flowNodeType: string) {
  const template = getTemplateByFlowNodeType(flowNodeType);
  return {
    label: template?.name || flowNodeType,
    typeLabel: template?.name || flowNodeType,
    shortName: (template?.name || flowNodeType).slice(0, 2),
    description: template?.intro || '',
    color: '#f1f5f9',
    accent: template?.color || '#475569',
  };
}

function buildLegacyNode(
  node: StoreNodeItemType,
  type: string,
  config: Record<string, any>,
  branches?: { id: string; label: string }[]
): LegacyNode {
  return {
    id: node.nodeId,
    type: 'workflowNode',
    position: node.position,
    data: {
      type,
      ...legacyAppearance(node.flowNodeType),
      label: node.name,
      inputs: [],
      outputs: [],
      ...(branches ? { branches } : {}),
      config,
    },
  };
}

/** 画布模型编译为 v0.1 运行格式 */
export function compileGraphToLegacy(graph: WorkflowGraphType): {
  nodes: LegacyNode[];
  edges: LegacyEdge[];
  warnings: CompileWarning[];
} {
  const warnings: CompileWarning[] = [];
  const branchKeyMap = new Map<string, Record<string, string>>();

  // systemConfig 是纯配置节点（无锚点无 IO），v0.1 运行格式中没有对应角色，直接跳过
  const runnableNodes = graph.nodes.filter((node) => node.flowNodeType !== FlowNodeTypeEnum.systemConfig);

  const nodes = runnableNodes.map((node) => {
    switch (node.flowNodeType) {
      case FlowNodeTypeEnum.workflowStart:
        return buildLegacyNode(node, 'start', {});
      case FlowNodeTypeEnum.chatNode: {
        return buildLegacyNode(node, 'llm', {
          model: getNodeInputValue(node, NodeInputKeyEnum.aiModel, 'default') || 'default',
          prompt: refToLegacyExpression(getNodeInputValue(node, NodeInputKeyEnum.aiSystemPrompt, ''), ''),
          userPrompt: refToLegacyExpression(getNodeInputValue(node, NodeInputKeyEnum.userChatInput)),
          contextVariables: ['{{input}}'],
          knowledgeRefs: [],
          enableTools: false,
          toolIds: [],
          skills: getNodeInputValue(node, NodeInputKeyEnum.skills, []) || [],
          temperature: getNodeInputValue(node, NodeInputKeyEnum.aiChatTemperature, 0.7) ?? 0.7,
          topP: getNodeInputValue(node, NodeInputKeyEnum.aiChatTopP, 1) ?? 1,
          maxTokens: getNodeInputValue(node, NodeInputKeyEnum.aiChatMaxToken, 2048) ?? 2048,
          timeoutSeconds: 60,
        });
      }
      case FlowNodeTypeEnum.datasetSearchNode: {
        const datasets = getNodeInputValue<any[]>(node, NodeInputKeyEnum.datasetSelectList, []) || [];
        return buildLegacyNode(node, 'knowledgeSearch', {
          knowledgeIds: datasets.map((item) => (typeof item === 'object' ? item.datasetId || item.id : item)),
          topK: 5,
          scoreThreshold: getNodeInputValue(node, NodeInputKeyEnum.datasetSimilarity, 0.4) ?? 0.4,
          query: refToLegacyExpression(getNodeInputValue(node, NodeInputKeyEnum.datasetSearchInput)),
          retrievalMode:
            searchModeMap[getNodeInputValue(node, NodeInputKeyEnum.datasetSearchMode, DatasetSearchModeEnum.mixedRecall)] ||
            'hybrid',
          rerank: !!getNodeInputValue(node, NodeInputKeyEnum.datasetSearchUsingReRank, false),
          returnSource: true,
        });
      }
      case FlowNodeTypeEnum.answerNode:
        return buildLegacyNode(node, 'answer', {
          template: refToLegacyExpression(getNodeInputValue(node, NodeInputKeyEnum.answerText)),
          returnFormat: 'text',
          saveChat: true,
          streaming: false,
        });
      case FlowNodeTypeEnum.httpRequest468: {
        const rows = (value: any) =>
          Object.fromEntries(
            (Array.isArray(value) ? value : [])
              .filter((row: any) => row?.key)
              .map((row: any) => [row.key, row.value ?? ''])
          );
        return buildLegacyNode(node, 'httpTool', {
          method: getNodeInputValue(node, NodeInputKeyEnum.httpMethod, 'POST'),
          url: getNodeInputValue(node, NodeInputKeyEnum.httpReqUrl, ''),
          headersText: JSON.stringify(rows(getNodeInputValue(node, NodeInputKeyEnum.httpHeaders, []))),
          queryText: JSON.stringify(rows(getNodeInputValue(node, NodeInputKeyEnum.httpParams, []))),
          body: getNodeInputValue(node, NodeInputKeyEnum.httpJsonBody, '') || '',
          timeoutSeconds: getNodeInputValue(node, NodeInputKeyEnum.httpTimeout, 30) ?? 30,
          maxRetries: 0,
          responsePath: '',
          outputKey: '',
          failStrategy: 'throw',
          fallbackOutput: '{{input}}',
        });
      }
      case FlowNodeTypeEnum.ifElseNode: {
        const groups = getNodeInputValue<IfElseListItemType[]>(node, NodeInputKeyEnum.ifElseList, []) || [];
        const first = groups[0]?.list?.[0];
        if (groups.length > 1 || (groups[0]?.list?.length || 0) > 1) {
          warnings.push({
            nodeId: node.nodeId,
            flowNodeType: node.flowNodeType,
            name: node.name,
            reason: '运行时当前仅支持单条件判断，已按第一个条件编译',
          });
        }
        branchKeyMap.set(node.nodeId, { [IfElseResultEnum.IF]: 'true', [IfElseResultEnum.ELSE]: 'false' });
        return buildLegacyNode(
          node,
          'condition',
          {
            left: '{{input}}',
            operator: (first?.condition && conditionOperatorMap[first.condition]) || 'contains',
            right: first?.value ?? '',
          },
          [
            { id: 'true', label: 'true' },
            { id: 'false', label: 'false' },
          ]
        );
      }
      case FlowNodeTypeEnum.classifyQuestion: {
        const agents = getNodeInputValue<{ key: string; value: string }[]>(node, NodeInputKeyEnum.agents, []) || [];
        warnings.push({
          nodeId: node.nodeId,
          flowNodeType: node.flowNodeType,
          name: node.name,
          reason: '问题分类的多分支与运行时 matched/default 模型不完全一致，请调试验证分支流转',
        });
        return buildLegacyNode(
          node,
          'intent',
          {
            intentsText: JSON.stringify(agents.map((agent) => ({ name: agent.value, keywords: [] }))),
            defaultIntent: agents[agents.length - 1]?.value || 'default',
            threshold: 0.6,
          },
          agents.map((agent) => ({ id: agent.key, label: agent.value }))
        );
      }
      case FlowNodeTypeEnum.contentExtract: {
        const keys = getNodeInputValue<any[]>(node, NodeInputKeyEnum.extractKeys, []) || [];
        return buildLegacyNode(node, 'extract', {
          fieldsText: JSON.stringify(
            keys.map((item) => ({ name: item.key, type: 'string', required: !!item.required, description: item.desc }))
          ),
        });
      }
      case FlowNodeTypeEnum.textEditor:
        return buildLegacyNode(node, 'code', {
          language: 'javascript',
          code: 'input',
          outputTemplate: getNodeInputValue(node, NodeInputKeyEnum.textareaInput, '{{input}}') || '{{input}}',
          inputMappings: {},
        });
      case FlowNodeTypeEnum.datasetConcatNode:
      case FlowNodeTypeEnum.variableUpdate:
      default: {
        warnings.push({
          nodeId: node.nodeId,
          flowNodeType: node.flowNodeType,
          name: node.name,
          reason: '运行时暂不支持该节点，已按日志透传处理',
        });
        return buildLegacyNode(node, 'log', { level: 'info', message: '{{input}}' });
      }
    }
  });

  const edges: LegacyEdge[] = graph.edges.map((edge) => {
    const key = parseHandleKey(edge.sourceHandle, edge.source, 'source');
    const mapped = branchKeyMap.get(edge.source)?.[key];
    const sourceHandle = mapped ?? (key === 'right' ? undefined : key);
    return {
      id: `${edge.source}-${edge.target}-${key}`,
      source: edge.source,
      target: edge.target,
      ...(sourceHandle ? { sourceHandle } : {}),
      type: 'smoothstep',
      animated: true,
    };
  });

  return { nodes, edges, warnings };
}

/** 剔除节点上的运行期字段（蓝本：isFolded/isError/debugResult 属于 FlowNodeItemType，不入 Store） */
function sanitizeNodeForPersist(node: StoreNodeItemType): StoreNodeItemType {
  const rest: StoreNodeItemType = { ...node };
  delete rest.isFolded;
  delete rest.isError;
  delete rest.debugResult;
  return rest;
}

/**
 * 序列化为持久化 JSON。信封只含 fastgpt 画布模型（运行时唯一事实源），
 * 不再写入旧 v0.1 Java 编译结果（parse_graph 从不消费，且其 unsupported 会误报已支持节点）。
 * 不含时间戳：输出用于「未保存变更」比对与撤销栈快照，必须确定性。
 */
export function serializeGraph(graph: WorkflowGraphType): string {
  const persist: WorkflowPersistType = {
    version: WORKFLOW_PERSIST_VERSION,
    fastgpt: {
      nodes: graph.nodes.map(sanitizeNodeForPersist),
      edges: graph.edges,
      chatConfig: graph.chatConfig,
    },
  };
  return JSON.stringify(persist);
}

/** 默认画布：系统配置 + 流程开始。新建工作流不预置业务节点与连线。 */
export function createDefaultGraph(): WorkflowGraphType {
  const systemConfig = createNodeFromTemplate(SystemConfigTemplate, { x: -240, y: 16 });
  const start = createNodeFromTemplate(WorkflowStartTemplate, { x: 376, y: 352 });
  return {
    nodes: [systemConfig, start],
    edges: [],
    chatConfig: { welcomeText: '', variables: [] },
  };
}

/** v0.1 节点 -> 画布节点（旧草稿迁移，尽力映射） */
function migrateLegacyNode(legacy: LegacyNode): { node: StoreNodeItemType | null; branchMap?: Record<string, string> } {
  const config = legacy.data?.config || {};
  const position = legacy.position || { x: 0, y: 0 };
  const withValues = (node: StoreNodeItemType, values: Record<string, any>) => {
    Object.entries(values).forEach(([key, value]) => {
      const input = node.inputs.find((item) => item.key === key);
      if (input && value !== undefined) input.value = value;
    });
    if (legacy.data?.label) node.name = legacy.data.label;
    return node;
  };

  switch (legacy.data?.type) {
    case 'start':
      return { node: createNodeFromTemplate(WorkflowStartTemplate, position, legacy.id) };
    case 'llm':
      return {
        node: withValues(createNodeFromTemplate(AiChatTemplate, position, legacy.id), {
          [NodeInputKeyEnum.aiModel]: config.model === 'default' ? undefined : config.model,
          [NodeInputKeyEnum.aiSystemPrompt]: config.prompt,
          [NodeInputKeyEnum.aiChatTemperature]: config.temperature,
          [NodeInputKeyEnum.aiChatMaxToken]: config.maxTokens,
          [NodeInputKeyEnum.aiChatTopP]: config.topP,
        }),
      };
    case 'knowledgeSearch':
      return {
        node: withValues(createNodeFromTemplate(DatasetSearchTemplate, position, legacy.id), {
          [NodeInputKeyEnum.datasetSelectList]: (config.knowledgeIds || []).map((id: string) => ({ datasetId: id })),
          [NodeInputKeyEnum.datasetSimilarity]: config.scoreThreshold,
          [NodeInputKeyEnum.datasetSearchUsingReRank]: config.rerank,
        }),
      };
    case 'answer':
      return {
        node: withValues(createNodeFromTemplate(AssignedAnswerTemplate, position, legacy.id), {
          [NodeInputKeyEnum.answerText]: config.template,
        }),
      };
    case 'httpTool': {
      const parseRows = (text: string) => {
        try {
          return Object.entries(JSON.parse(text || '{}')).map(([key, value]) => ({ key, type: 'string', value }));
        } catch {
          return [];
        }
      };
      return {
        node: withValues(createNodeFromTemplate(HttpRequestTemplate, position, legacy.id), {
          [NodeInputKeyEnum.httpMethod]: config.method,
          [NodeInputKeyEnum.httpReqUrl]: config.url,
          [NodeInputKeyEnum.httpTimeout]: config.timeoutSeconds,
          [NodeInputKeyEnum.httpHeaders]: parseRows(config.headersText),
          [NodeInputKeyEnum.httpParams]: parseRows(config.queryText),
          [NodeInputKeyEnum.httpJsonBody]: config.body,
        }),
      };
    }
    case 'condition': {
      const reverseOperator = Object.fromEntries(Object.entries(conditionOperatorMap).map(([k, v]) => [v, k]));
      return {
        node: withValues(createNodeFromTemplate(IfElseTemplate, position, legacy.id), {
          [NodeInputKeyEnum.ifElseList]: [
            {
              condition: 'AND',
              list: [
                {
                  variable: undefined,
                  condition: reverseOperator[config.operator] || VariableConditionEnum.include,
                  value: config.right ?? '',
                },
              ],
            },
          ],
        }),
        branchMap: { true: IfElseResultEnum.IF, false: IfElseResultEnum.ELSE },
      };
    }
    case 'intent': {
      let agents: { key: string; value: string }[] = [];
      try {
        agents = (JSON.parse(config.intentsText || '[]') as any[]).map((item, index) => ({
          key: `intent_${index}`,
          value: item.name,
        }));
      } catch {
        agents = [];
      }
      return {
        node: withValues(createNodeFromTemplate(ClassifyQuestionTemplate, position, legacy.id), {
          [NodeInputKeyEnum.agents]: agents,
        }),
      };
    }
    case 'extract': {
      let keys: any[] = [];
      try {
        keys = (JSON.parse(config.fieldsText || '[]') as any[]).map((item) => ({
          key: item.name,
          desc: item.description || '',
          required: !!item.required,
        }));
      } catch {
        keys = [];
      }
      return {
        node: withValues(createNodeFromTemplate(ContentExtractTemplate, position, legacy.id), {
          [NodeInputKeyEnum.extractKeys]: keys,
        }),
      };
    }
    case 'code':
      return {
        node: withValues(createNodeFromTemplate(TextEditorTemplate, position, legacy.id), {
          [NodeInputKeyEnum.textareaInput]: config.outputTemplate,
        }),
      };
    default:
      return { node: null };
  }
}

/**
 * 解析持久化 JSON 为画布模型。
 * 优先读取 fastgpt 字段；旧格式（仅 v0.1 nodes/edges）做尽力迁移，无法映射的节点丢弃并计入 dropped。
 */
export function parsePersistedGraph(json?: string): {
  graph: WorkflowGraphType;
  migrated: boolean;
  dropped: string[];
} {
  const empty = () => ({ graph: createDefaultGraph(), migrated: false, dropped: [] as string[] });
  if (!json || !json.trim()) return empty();

  let parsed: any;
  try {
    parsed = JSON.parse(json);
  } catch {
    return empty();
  }

  if (parsed?.fastgpt?.nodes?.length) {
    const graph: WorkflowGraphType = {
      nodes: parsed.fastgpt.nodes,
      edges: parsed.fastgpt.edges || [],
      chatConfig: parsed.fastgpt.chatConfig || { welcomeText: '', variables: [] },
    };
    migrateProtocolKeys(graph);
    ensureFixedNodes(graph);
    return { graph, migrated: false, dropped: [] };
  }

  const legacyNodes: LegacyNode[] = Array.isArray(parsed?.nodes) ? parsed.nodes : [];
  if (!legacyNodes.length) return empty();

  const dropped: string[] = [];
  const branchMaps = new Map<string, Record<string, string>>();
  const nodes: StoreNodeItemType[] = [];
  legacyNodes.forEach((legacy) => {
    const { node, branchMap } = migrateLegacyNode(legacy);
    if (node) {
      nodes.push(node);
      if (branchMap) branchMaps.set(node.nodeId, branchMap);
    } else {
      dropped.push(legacy.data?.label || legacy.data?.type || legacy.id);
    }
  });

  const nodeIds = new Set(nodes.map((node) => node.nodeId));
  const edges: StoreEdgeItemType[] = (Array.isArray(parsed?.edges) ? parsed.edges : [])
    .filter((edge: LegacyEdge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge: LegacyEdge) => {
      const branchKey = edge.sourceHandle
        ? branchMaps.get(edge.source)?.[edge.sourceHandle] || edge.sourceHandle
        : 'right';
      return {
        source: edge.source,
        sourceHandle: getHandleId(edge.source, 'source', branchKey),
        target: edge.target,
        targetHandle: getHandleId(edge.target, 'target', 'left'),
      };
    });

  const graph: WorkflowGraphType = { nodes, edges, chatConfig: { welcomeText: '', variables: [] } };
  ensureFixedNodes(graph);

  return { graph, migrated: true, dropped };
}

/**
 * 2.1.0 前存量画布的协议键迁移（与蓝本对齐时改名的键）：
 * - 错误输出 key 'error' -> 'system_error_text'（含全图引用重写）
 * - loopRunStart 输出 'loopStartInput'/'loopStartIndex' -> 'currentItem'/'currentIndex'
 * - parallelRun 输入 'loopRunInputArray' -> 'loopInputArray'
 * - stopTool 旧版工具边挂载 -> 蓝本"工具节点普通下游"连线
 */
function migrateProtocolKeys(graph: WorkflowGraphType) {
  graph.nodes.forEach((node) => {
    if (node.toolConfig?.systemTool?.toolId === 'builtin.sql_query') {
      if (node.name === 'SQL 只读查询') node.name = 'SQL 执行';
      node.inputs = (node.inputs || []).filter((input) => input.key !== 'limit_rows');
    }
    if (
      node.toolConfig?.systemTool?.toolId === 'builtin.text_to_sql' &&
      !(node.inputs || []).some((input) => input.key === NodeInputKeyEnum.aiModel)
    ) {
      node.inputs = [
        {
          key: NodeInputKeyEnum.aiModel,
          label: '生成模型',
          renderTypeList: [FlowNodeInputTypeEnum.selectLLMModel],
          valueType: 'string',
          value: 'default',
          required: false,
          description: '用于生成 SQL 的对话模型；选择默认模型时使用当前工作流的默认模型。',
        },
        ...(node.inputs || []),
      ];
    }
  });

  const aiChatDatasetsInput = AiChatTemplate.inputs.find(
    (input) => input.key === NodeInputKeyEnum.aiChatDatasets
  );
  const aiChatDatasetQuoteInput = AiChatTemplate.inputs.find(
    (input) => input.key === NodeInputKeyEnum.aiChatDatasetQuote
  );
  const aiChatSkillsInput = AiChatTemplate.inputs.find((input) => input.key === NodeInputKeyEnum.skills);
  const aiChatKnowledgeImagesInput = AiChatTemplate.inputs.find(
    (input) => input.key === NodeInputKeyEnum.aiChatKnowledgeImages
  );
  graph.nodes.forEach((node) => {
    if (node.flowNodeType !== FlowNodeTypeEnum.chatNode) return;
    node.inputs ||= [];
    const legacyQuoteInput = node.inputs.find((input) => input.key === NodeInputKeyEnum.aiChatDatasetQuote);
    if (legacyQuoteInput && aiChatDatasetQuoteInput) {
      Object.assign(legacyQuoteInput, {
        ...aiChatDatasetQuoteInput,
        renderTypeList: [...aiChatDatasetQuoteInput.renderTypeList],
        value: legacyQuoteInput.value,
        deprecated: false,
      });
    } else if (aiChatDatasetQuoteInput) {
      node.inputs.push({
        ...aiChatDatasetQuoteInput,
        renderTypeList: [...aiChatDatasetQuoteInput.renderTypeList],
      });
    }
    if (!node.inputs.some((input) => input.key === NodeInputKeyEnum.aiChatDatasets) && aiChatDatasetsInput) {
      node.inputs.push({
        ...aiChatDatasetsInput,
        renderTypeList: [...aiChatDatasetsInput.renderTypeList],
        value: [],
      });
    }
    if (!node.inputs.some((input) => input.key === NodeInputKeyEnum.skills) && aiChatSkillsInput) {
      node.inputs.push({
        ...aiChatSkillsInput,
        renderTypeList: [...aiChatSkillsInput.renderTypeList],
        value: [],
      });
    }
    if (
      !node.inputs.some((input) => input.key === NodeInputKeyEnum.aiChatKnowledgeImages) &&
      aiChatKnowledgeImagesInput
    ) {
      node.inputs.push({
        ...aiChatKnowledgeImagesInput,
        renderTypeList: [...aiChatKnowledgeImagesInput.renderTypeList],
        list: [...(aiChatKnowledgeImagesInput.list || [])],
        value: 'smart',
      });
    }
  });

  // stopTool：旧版经工具锚点挂载，蓝本语义是普通连线；改写边并恢复目标锚点
  const stopToolIds = new Set(
    graph.nodes.filter((node) => node.flowNodeType === FlowNodeTypeEnum.stopTool).map((node) => node.nodeId)
  );
  if (stopToolIds.size) {
    graph.edges.forEach((edge) => {
      if (edge.sourceHandle === TOOL_EDGE_HANDLE && stopToolIds.has(edge.target)) {
        edge.sourceHandle = getHandleId(edge.source, 'source', 'right');
        edge.targetHandle = getHandleId(edge.target, 'target', 'left');
      }
    });
    graph.nodes.forEach((node) => {
      if (stopToolIds.has(node.nodeId)) (node as any).showTargetHandle = true;
    });
  }

  const outputRenames = new Map<string, Record<string, string>>();
  // 蓝本两键并存：code/http 节点的错误输出键就是 'error'，不参与统一改名
  const keepsErrorKey = new Set<string>([FlowNodeTypeEnum.code, FlowNodeTypeEnum.httpRequest468]);
  graph.nodes.forEach((node) => {
    const renames: Record<string, string> = {};
    (node.outputs || []).forEach((output: any) => {
      if (
        output.type === FlowNodeOutputTypeEnum.error &&
        output.key === 'error' &&
        !keepsErrorKey.has(node.flowNodeType)
      ) {
        output.id = NodeOutputKeyEnum.errorText;
        output.key = NodeOutputKeyEnum.errorText;
        renames.error = NodeOutputKeyEnum.errorText;
      }
      if (node.flowNodeType === FlowNodeTypeEnum.loopRunStart) {
        if (output.key === 'loopStartInput') {
          output.id = output.key = 'currentItem';
          renames.loopStartInput = 'currentItem';
        }
        if (output.key === 'loopStartIndex') {
          output.id = output.key = 'currentIndex';
          renames.loopStartIndex = 'currentIndex';
        }
      }
    });
    if (node.flowNodeType === FlowNodeTypeEnum.parallelRun) {
      (node.inputs || []).forEach((input: any) => {
        if (input.key === 'loopRunInputArray') input.key = 'loopInputArray';
      });
    }
    if (Object.keys(renames).length) outputRenames.set(node.nodeId, renames);
  });
  if (!outputRenames.size) return;

  // 重写全图引用：[nodeId, oldKey] 二元组出现在任何输入值结构中（含 ifElseList/updateList 深层）
  const rewrite = (value: any): any => {
    if (Array.isArray(value)) {
      if (value.length === 2 && typeof value[0] === 'string' && typeof value[1] === 'string') {
        const renamed = outputRenames.get(value[0])?.[value[1]];
        if (renamed) return [value[0], renamed];
      }
      return value.map(rewrite);
    }
    if (value && typeof value === 'object') {
      Object.keys(value).forEach((key) => {
        value[key] = rewrite(value[key]);
      });
      return value;
    }
    return value;
  };
  graph.nodes.forEach((node) => {
    (node.inputs || []).forEach((input: any) => {
      input.value = rewrite(input.value);
    });
  });
}

/**
 * 补齐固定系统节点（旧数据兼容）：systemConfig / workflowStart 缺失时创建，
 * 并按 chatConfig 文件开关同步 workflowStart.userFiles 输出。
 */
function ensureFixedNodes(graph: WorkflowGraphType) {
  if (!graph.nodes.some((node) => node.flowNodeType === FlowNodeTypeEnum.workflowStart)) {
    graph.nodes.unshift(createNodeFromTemplate(WorkflowStartTemplate, { x: 80, y: 200 }));
  }
  if (!graph.nodes.some((node) => node.flowNodeType === FlowNodeTypeEnum.systemConfig)) {
    const start = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.workflowStart)!;
    graph.nodes.unshift(
      createNodeFromTemplate(SystemConfigTemplate, { x: start.position.x, y: start.position.y - 380 })
    );
  }
  syncUserFilesOutput(graph);
}
