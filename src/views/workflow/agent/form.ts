/**
 * 对话 Agent V2 表单 <-> 三节点工作流 编译/反解。
 *
 * 蓝本对齐（FastGPT ChatAgent/utils.ts agentForm2AppWorkflow / appWorkflow2AgentForm）：
 * - 固定三节点：systemConfig(userGuide) + workflowStart(workflowStartNodeId) + agent(7BdojPlukIQw)；
 * - 单条边 start -> agent；
 * - 表单不单独持久化，编辑时从 agent 节点 inputs 反解（保证与画布/运行时同源）。
 */
import {
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTypeEnum,
  WorkflowIOValueTypeEnum,
  getHandleId,
} from '../core/constants';
import type {
  AppChatConfigType,
  RecommendationSceneConfig,
  StoreNodeItemType,
  VariableItemType,
  WorkflowGraphType,
} from '../core/type';
import { WorkflowStartTemplate } from '../core/templates';
import { createNodeFromTemplate } from '../core/utils';
import { flattenRecommendationScenes, normalizeRecommendationScenes } from '../shared/recommendationScenes';

/** 蓝本固定 nodeId */
export const SYSTEM_CONFIG_NODE_ID = FlowNodeTypeEnum.systemConfig;
export const WORKFLOW_START_NODE_ID = 'workflowStartNodeId';
export const AGENT_NODE_ID = '7BdojPlukIQw';

export type AgentToolRef = { id: string; name: string; kind: string; description?: string };
export type AgentSkillSource = 'mine' | 'system';
export type AgentSkillRef = { skillId: string; name: string; description?: string; source?: AgentSkillSource };
export type AgentDatasetRef = { datasetId: string; name?: string };

export type AgentFormType = {
  model: string;
  systemPrompt: string;
  temperature?: number;
  maxHistories: number;
  welcomeText: string;
  presentationPreset: string;
  variables: VariableItemType[];
  selectedTools: AgentToolRef[];
  skills: AgentSkillRef[];
  datasets: AgentDatasetRef[];
  similarity: number;
  recommendationScenes: RecommendationSceneConfig[];
  /** 蓝本 agent 模型高级参数（hidden 输入持久化，经模型设置弹窗编辑） */
  maxToken?: number;
  topP?: number;
  stopSign?: string;
  responseFormat?: string;
  jsonSchema?: string;
  vision?: boolean;
  audio?: boolean;
  video?: boolean;
  extractFiles?: boolean;
  reasoning?: boolean;
  reasoningEffort?: string;
  /** 蓝本 agent_datasetParams 扩展检索参数 */
  searchMode?: string;
  embeddingWeight?: number;
  usingReRank?: boolean;
};

export function createDefaultAgentForm(): AgentFormType {
  return {
    model: '',
    systemPrompt: '',
    temperature: undefined,
    maxHistories: 6,
    welcomeText: '',
    presentationPreset: 'default',
    variables: [],
    selectedTools: [],
    skills: [],
    datasets: [],
    similarity: 0.4,
    recommendationScenes: [],
    vision: true,
    extractFiles: false,
    reasoning: true,
  };
}

function hiddenInput(key: string, value: unknown, valueType: WorkflowIOValueTypeEnum) {
  return {
    key,
    label: '',
    renderTypeList: [FlowNodeInputTypeEnum.hidden],
    selectedTypeIndex: 0,
    valueType,
    value,
  };
}

/** 蓝本 agentForm2AppWorkflow：表单 -> systemConfig + workflowStart + agent 三节点图 */
export function agentFormToGraph(form: AgentFormType): WorkflowGraphType {
  const systemConfig: StoreNodeItemType = {
    nodeId: SYSTEM_CONFIG_NODE_ID,
    flowNodeType: FlowNodeTypeEnum.systemConfig,
    name: '系统配置',
    intro: '',
    position: { x: 531.24, y: -486.76 },
    inputs: [],
    outputs: [],
  };

  const start = createNodeFromTemplate(WorkflowStartTemplate, { x: 558.4, y: 123.72 }, WORKFLOW_START_NODE_ID);

  const agent: StoreNodeItemType = {
    nodeId: AGENT_NODE_ID,
    flowNodeType: FlowNodeTypeEnum.agent,
    name: '对话 Agent',
    intro: '模型自主决定调用工具、技能与知识库，产出最终回答。',
    position: { x: 1106.32, y: -350.6 },
    inputs: [
      {
        key: 'model',
        label: '模型',
        // 蓝本 Input_Template_SettingAiModel：弹窗承载温度/上限/topP 等隐藏参数编辑
        renderTypeList: [FlowNodeInputTypeEnum.settingLLMModel, FlowNodeInputTypeEnum.reference],
        selectedTypeIndex: 0,
        valueType: WorkflowIOValueTypeEnum.string,
        value: form.model,
      },
      {
        key: 'systemPrompt',
        label: '提示词',
        renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
        selectedTypeIndex: 0,
        valueType: WorkflowIOValueTypeEnum.string,
        maxLength: 100000,
        description: '模型固定的引导词，可使用变量，通过调整该内容引导模型聊天方向',
        placeholder: '例如：你是一个严谨的校园业务助手。',
        value: form.systemPrompt,
      },
      hiddenInput('temperature', form.temperature, WorkflowIOValueTypeEnum.number),
      hiddenInput('maxToken', form.maxToken, WorkflowIOValueTypeEnum.number),
      hiddenInput('aiChatVision', form.vision ?? true, WorkflowIOValueTypeEnum.boolean),
      hiddenInput('aiChatAudio', form.audio ?? false, WorkflowIOValueTypeEnum.boolean),
      hiddenInput('aiChatVideo', form.video ?? false, WorkflowIOValueTypeEnum.boolean),
      hiddenInput('aiChatExtractFiles', form.extractFiles ?? false, WorkflowIOValueTypeEnum.boolean),
      hiddenInput('aiChatReasoning', form.reasoning ?? true, WorkflowIOValueTypeEnum.boolean),
      hiddenInput('aiChatReasoningEffort', form.reasoningEffort, WorkflowIOValueTypeEnum.string),
      hiddenInput('aiChatTopP', form.topP, WorkflowIOValueTypeEnum.number),
      hiddenInput('aiChatStopSign', form.stopSign, WorkflowIOValueTypeEnum.string),
      hiddenInput('aiChatResponseFormat', form.responseFormat, WorkflowIOValueTypeEnum.string),
      hiddenInput('aiChatJsonSchema', form.jsonSchema, WorkflowIOValueTypeEnum.string),
      // 蓝本 NodeInputKeyEnum.history
      {
        key: 'history',
        label: '上下文轮数',
        renderTypeList: [FlowNodeInputTypeEnum.numberInput],
        selectedTypeIndex: 0,
        valueType: WorkflowIOValueTypeEnum.number,
        min: 0,
        max: 30,
        value: form.maxHistories,
      },
      {
        key: 'fileUrlList',
        label: '文件链接',
        renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.input],
        selectedTypeIndex: 0,
        valueType: WorkflowIOValueTypeEnum.arrayString,
        value: [WORKFLOW_START_NODE_ID, 'userFiles'],
      },
      {
        key: 'userChatInput',
        label: '用户问题',
        renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.textarea],
        selectedTypeIndex: 0,
        valueType: WorkflowIOValueTypeEnum.string,
        required: true,
        toolDescription: '用户问题',
        value: [WORKFLOW_START_NODE_ID, 'userChatInput'],
      },
      hiddenInput('isResponseAnswerText', true, WorkflowIOValueTypeEnum.boolean),
      hiddenInput(
        'agent_selectedTools',
        form.selectedTools.map((tool) => ({ id: tool.id, name: tool.name, kind: tool.kind })),
        WorkflowIOValueTypeEnum.arrayObject
      ),
      hiddenInput(
        'agent_datasetParams',
        {
          datasets: form.datasets.map((item) => ({ datasetId: item.datasetId, name: item.name })),
          similarity: form.similarity,
          limit: 5000,
          searchMode: form.searchMode || 'embedding',
          embeddingWeight: form.embeddingWeight ?? 0.5,
          usingReRank: form.usingReRank ?? false,
        },
        WorkflowIOValueTypeEnum.object
      ),
      hiddenInput(
        'skills',
        form.skills.map(({ skillId, name, description, source }) => ({
          skillId,
          name,
          description,
          source: source === 'system' ? 'system' : 'mine',
        })),
        WorkflowIOValueTypeEnum.arrayObject
      ),
    ],
    outputs: [
      {
        id: 'answerText',
        key: 'answerText',
        label: 'AI 回复内容',
        type: FlowNodeOutputTypeEnum.static,
        valueType: WorkflowIOValueTypeEnum.string,
      },
      {
        id: 'system_error_text',
        key: 'system_error_text',
        label: '错误信息',
        type: FlowNodeOutputTypeEnum.error,
        valueType: WorkflowIOValueTypeEnum.string,
      },
    ],
  };

  const chatConfig: AppChatConfigType = {
    welcomeText: form.welcomeText || '',
    ...(form.presentationPreset && form.presentationPreset !== 'default'
      ? {
          presentation: {
            schemaVersion: 1 as const,
            preset: form.presentationPreset,
          },
        }
      : {}),
    variables: form.variables || [],
    fileSelectConfig: {
      canSelectFile: !!form.extractFiles,
      canSelectImg: !!form.extractFiles,
      maxFiles: 10,
    },
    chatInputGuide: {
      open: form.recommendationScenes.some((scene) => scene.textList.some((item) => item.trim())),
      textList: flattenRecommendationScenes(form.recommendationScenes),
      sceneList: normalizeRecommendationScenes(form.recommendationScenes),
    },
  };

  return {
    nodes: [systemConfig, start, agent],
    edges: [
      {
        source: WORKFLOW_START_NODE_ID,
        sourceHandle: getHandleId(WORKFLOW_START_NODE_ID, 'source', 'right'),
        target: AGENT_NODE_ID,
        targetHandle: getHandleId(AGENT_NODE_ID, 'target', 'left'),
      },
    ],
    chatConfig,
  };
}

/** 蓝本 appWorkflow2AgentForm：从图中的 agent 节点反解表单；无 agent 节点返回 null */
export function graphToAgentForm(graph: WorkflowGraphType | undefined | null): AgentFormType | null {
  const agent = graph?.nodes?.find((node) => node.flowNodeType === FlowNodeTypeEnum.agent);
  if (!agent) return null;
  const inputValue = (key: string) => agent.inputs.find((item) => item.key === key)?.value;

  const form = createDefaultAgentForm();
  form.model = String(inputValue('model') || '');
  form.systemPrompt = String(inputValue('systemPrompt') || '');
  const temperature = inputValue('temperature');
  form.temperature = typeof temperature === 'number' ? temperature : undefined;
  const history = inputValue('history');
  form.maxHistories = typeof history === 'number' ? history : 6;
  form.welcomeText = String(graph?.chatConfig?.welcomeText || '');
  form.presentationPreset = String(graph?.chatConfig?.presentation?.preset || 'default');
  form.variables = Array.isArray(graph?.chatConfig?.variables)
    ? JSON.parse(JSON.stringify(graph.chatConfig.variables))
    : [];

  // 模型高级参数（hidden 输入回读，缺省走默认）
  const numberOf = (key: string) => {
    const value = inputValue(key);
    return typeof value === 'number' ? value : undefined;
  };
  const stringOf = (key: string) => {
    const value = inputValue(key);
    return typeof value === 'string' && value ? value : undefined;
  };
  const boolOf = (key: string, fallback: boolean) => {
    const value = inputValue(key);
    return value === undefined || value === null ? fallback : !!value;
  };
  form.maxToken = numberOf('maxToken');
  form.topP = numberOf('aiChatTopP');
  form.stopSign = stringOf('aiChatStopSign');
  form.responseFormat = stringOf('aiChatResponseFormat');
  form.jsonSchema = stringOf('aiChatJsonSchema');
  form.vision = boolOf('aiChatVision', true);
  form.audio = boolOf('aiChatAudio', false);
  form.video = boolOf('aiChatVideo', false);
  form.extractFiles = boolOf('aiChatExtractFiles', !!graph?.chatConfig?.fileSelectConfig?.canSelectFile);
  form.reasoning = boolOf('aiChatReasoning', true);
  form.reasoningEffort = stringOf('aiChatReasoningEffort');
  form.recommendationScenes = normalizeRecommendationScenes(
    graph?.chatConfig?.chatInputGuide?.sceneList,
    graph?.chatConfig?.chatInputGuide?.textList,
  );

  // 蓝本键 agent_selectedTools / agent_datasetParams；兼容旧键
  const tools = inputValue('agent_selectedTools') ?? inputValue('selectedTools');
  form.selectedTools = Array.isArray(tools)
    ? tools
        .filter((item: any) => item && item.id)
        .map((item: any) => ({ id: String(item.id), name: String(item.name || ''), kind: String(item.kind || '') }))
    : [];

  const datasetParams = inputValue('agent_datasetParams') ?? inputValue('datasetParams');
  if (datasetParams && typeof datasetParams === 'object') {
    form.datasets = Array.isArray((datasetParams as any).datasets)
      ? (datasetParams as any).datasets
          .filter((item: any) => item && (item.datasetId || item.id))
          .map((item: any) => ({ datasetId: String(item.datasetId || item.id), name: item.name }))
      : [];
    const similarity = (datasetParams as any).similarity;
    form.similarity = typeof similarity === 'number' ? similarity : 0.4;
    form.searchMode = (datasetParams as any).searchMode || 'embedding';
    const embeddingWeight = (datasetParams as any).embeddingWeight;
    form.embeddingWeight = typeof embeddingWeight === 'number' ? embeddingWeight : 0.5;
    form.usingReRank = !!(datasetParams as any).usingReRank;
  }

  const skills = inputValue('skills');
  form.skills = Array.isArray(skills)
    ? skills
        .filter((item: any) => item && item.skillId)
        .map((item: any) => ({
          skillId: String(item.skillId),
          name: String(item.name || ''),
          description: item.description ? String(item.description) : undefined,
          source: item.source === 'system' || item.source === 'market' ? 'system' : 'mine',
        }))
    : [];

  return form;
}

/** 旧版占位页 configJson（{model,prompt,knowledgeIds,...}）尽力迁移为表单 */
export function legacyConfigToAgentForm(configJson: string | undefined | null): AgentFormType | null {
  if (!configJson) return null;
  try {
    const parsed = JSON.parse(configJson);
    if (!parsed || typeof parsed !== 'object' || (!parsed.prompt && !parsed.model)) return null;
    const form = createDefaultAgentForm();
    form.model = typeof parsed.model === 'string' && parsed.model !== 'default' ? parsed.model : '';
    form.systemPrompt = String(parsed.prompt || '');
    form.temperature = typeof parsed.temperature === 'number' ? parsed.temperature : undefined;
    form.welcomeText = String(parsed.openingMessage || '');
    form.datasets = Array.isArray(parsed.knowledgeIds)
      ? parsed.knowledgeIds.map((id: any) => ({ datasetId: String(id) }))
      : [];
    return form;
  } catch {
    return null;
  }
}
