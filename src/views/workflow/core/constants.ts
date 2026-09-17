/**
 * 工作流核心枚举，对齐 FastGPT v4.15（reference/FastGPT packages/global/core/workflow）。
 * 枚举成员名与字符串值与蓝本保持一致，保证工作流 JSON 与蓝本实现级兼容；
 * 仅收录 v1.9 §10.5 定型范围内（W0-W2）用到的子集。
 */

export enum AppTypeEnum {
  folder = 'folder',
  toolFolder = 'toolFolder',
  simple = 'simple',
  chatAgent = 'chatAgent',
  workflow = 'advanced',
  workflowTool = 'plugin',
  httpToolSet = 'httpToolSet',
}

/** 「我的智能体」列表展示的应用类型（蓝本 AppTypeList） */
export const AppTypeList = [AppTypeEnum.simple, AppTypeEnum.chatAgent, AppTypeEnum.workflow];

/** 「我的工具」列表展示的应用类型 */
export const ToolTypeList = [AppTypeEnum.workflowTool, AppTypeEnum.httpToolSet];

export const AppTypeLabelMap: Record<string, string> = {
  [AppTypeEnum.simple]: '简易应用',
  [AppTypeEnum.chatAgent]: '对话智能体',
  [AppTypeEnum.workflow]: '工作流',
  [AppTypeEnum.workflowTool]: '工作流工具',
  [AppTypeEnum.httpToolSet]: 'HTTP 工具集',
};

export enum FlowNodeTypeEnum {
  emptyNode = 'emptyNode',
  systemConfig = 'userGuide',
  workflowStart = 'workflowStart',
  chatNode = 'chatNode',
  datasetSearchNode = 'datasetSearchNode',
  datasetConcatNode = 'datasetConcatNode',
  answerNode = 'answerNode',
  classifyQuestion = 'classifyQuestion',
  contentExtract = 'contentExtract',
  httpRequest468 = 'httpRequest468',
  ifElseNode = 'ifElseNode',
  variableUpdate = 'variableUpdate',
  textEditor = 'textEditor',
  comment = 'comment',
  /** 对话 Agent V2 核心节点（蓝本 FlowNodeTypeEnum.agent，nodeId 固定 7BdojPlukIQw） */
  agent = 'agent',
  /** 交互节点（N-5）：LangGraph interrupt 原生实现，legacy 引擎不支持 */
  userSelect = 'userSelect',
  formInput = 'formInput',
  /** 问题优化（蓝本 cfr）：结合历史改写检索 query */
  queryExtension = 'cfr',
  /** 自定义反馈：记录反馈文本，不进入模型上下文 */
  customFeedback = 'customFeedback',
  /** 文档解析：下载 fileUrlList 并提取文本 */
  readFiles = 'readFiles',
  /** 工具调用循环（蓝本 tools）：经工具锚点挂载 isTool 节点 */
  toolCall = 'tools',
  stopTool = 'stopTool',
  toolParams = 'toolParams',
  /** 代码沙箱（保留差异 D-15：仅 Python） */
  code = 'code',
  /** 嵌套容器（子节点以 parentNodeId 圈定） */
  loopRun = 'loopRun',
  loopRunStart = 'loopRunStart',
  loopRunBreak = 'loopRunBreak',
  parallelRun = 'parallelRun',
  /** 动态节点（四 Tab 落图，gap-audit §7.3）：模板由服务端 previewNode 生成，不在本地注册表 */
  tool = 'tool',
  toolSet = 'toolSet',
  appModule = 'appModule',
  pluginModule = 'pluginModule',
}

/** 工具边 handle（蓝本裸 'selectedTools'，源/目标同名，不带 nodeId 前缀） */
export const TOOL_EDGE_HANDLE = 'selectedTools';

/** 动态节点类型集合：落图前必须经 previewNode 获取完整 schema */
export const DYNAMIC_NODE_TYPES: string[] = [
  FlowNodeTypeEnum.tool,
  FlowNodeTypeEnum.toolSet,
  FlowNodeTypeEnum.appModule,
  FlowNodeTypeEnum.pluginModule,
];

export enum FlowNodeTemplateTypeEnum {
  systemInput = 'systemInput',
  ai = 'ai',
  search = 'search',
  interactive = 'interactive',
  tools = 'tools',
  other = 'other',
  /** 动态节点模板（团队工具/应用，蓝本 teamApp） */
  teamApp = 'teamApp',
}

export const FlowNodeTemplateTypeLabelMap: Record<string, string> = {
  [FlowNodeTemplateTypeEnum.systemInput]: '系统',
  [FlowNodeTemplateTypeEnum.ai]: 'AI 能力',
  [FlowNodeTemplateTypeEnum.search]: '知识库',
  [FlowNodeTemplateTypeEnum.interactive]: '交互',
  [FlowNodeTemplateTypeEnum.tools]: '工具',
  [FlowNodeTemplateTypeEnum.other]: '其他',
  [FlowNodeTemplateTypeEnum.teamApp]: '团队工具/应用',
};

/** 输入项渲染器类型：节点属性面板按 renderTypeList[selectedTypeIndex] 选择渲染器 */
export enum FlowNodeInputTypeEnum {
  reference = 'reference',
  input = 'input',
  textarea = 'textarea',
  numberInput = 'numberInput',
  switch = 'switch',
  select = 'select',
  multipleSelect = 'multipleSelect',
  JSONEditor = 'JSONEditor',
  addInputParam = 'addInputParam',
  selectApp = 'selectApp',
  selectLLMModel = 'selectLLMModel',
  settingLLMModel = 'settingLLMModel',
  selectDataset = 'selectDataset',
  selectDatasetParamsModal = 'selectDatasetParamsModal',
  hidden = 'hidden',
  custom = 'custom',
  fileSelect = 'fileSelect',
  password = 'password',
}

export enum FlowNodeOutputTypeEnum {
  hidden = 'hidden',
  source = 'source',
  static = 'static',
  dynamic = 'dynamic',
  error = 'error',
}

export enum WorkflowIOValueTypeEnum {
  string = 'string',
  number = 'number',
  boolean = 'boolean',
  object = 'object',
  arrayString = 'arrayString',
  arrayNumber = 'arrayNumber',
  arrayBoolean = 'arrayBoolean',
  arrayObject = 'arrayObject',
  arrayAny = 'arrayAny',
  any = 'any',
  chatHistory = 'chatHistory',
  datasetQuote = 'datasetQuote',
  dynamic = 'dynamic',
  selectDataset = 'selectDataset',
}

export const WorkflowIOValueTypeLabelMap: Record<string, string> = {
  [WorkflowIOValueTypeEnum.string]: 'String',
  [WorkflowIOValueTypeEnum.number]: 'Number',
  [WorkflowIOValueTypeEnum.boolean]: 'Boolean',
  [WorkflowIOValueTypeEnum.object]: 'Object',
  [WorkflowIOValueTypeEnum.arrayString]: 'Array<String>',
  [WorkflowIOValueTypeEnum.arrayNumber]: 'Array<Number>',
  [WorkflowIOValueTypeEnum.arrayBoolean]: 'Array<Boolean>',
  [WorkflowIOValueTypeEnum.arrayObject]: 'Array<Object>',
  [WorkflowIOValueTypeEnum.arrayAny]: 'Array',
  [WorkflowIOValueTypeEnum.any]: 'Any',
  [WorkflowIOValueTypeEnum.chatHistory]: '聊天记录',
  [WorkflowIOValueTypeEnum.datasetQuote]: '知识库引用',
  [WorkflowIOValueTypeEnum.dynamic]: '动态数据',
  [WorkflowIOValueTypeEnum.selectDataset]: '知识库选择',
};

/** 节点输入 key，字符串值与蓝本 NodeInputKeyEnum 完全一致 */
export enum NodeInputKeyEnum {
  // common
  userChatInput = 'userChatInput',
  history = 'history',
  answerText = 'text',
  aiModel = 'model',
  aiSystemPrompt = 'systemPrompt',
  queryExtensionMaxQueries = 'maxQueries',
  description = 'description',
  textareaInput = 'system_textareaInput',
  addInputParam = 'system_addInputParam',
  headerSecret = 'system_header_secret',
  // ai chat
  aiChatTemperature = 'temperature',
  aiChatMaxToken = 'maxToken',
  aiChatIsResponseText = 'isResponseAnswerText',
  aiChatDatasets = 'aiChatDatasets',
  aiChatDatasetQuote = 'quoteQA',
  skills = 'skills',
  aiChatVision = 'aiChatVision',
  aiChatKnowledgeImages = 'aiChatKnowledgeImages',
  aiChatAudio = 'aiChatAudio',
  aiChatVideo = 'aiChatVideo',
  aiChatExtractFiles = 'aiChatExtractFiles',
  aiChatReasoning = 'aiChatReasoning',
  aiChatReasoningEffort = 'aiChatReasoningEffort',
  aiChatTopP = 'aiChatTopP',
  aiChatStopSign = 'aiChatStopSign',
  aiChatResponseFormat = 'aiChatResponseFormat',
  aiChatJsonSchema = 'aiChatJsonSchema',
  // classify question
  agents = 'agents',
  // dataset
  datasetSelectList = 'datasets',
  datasetSimilarity = 'similarity',
  datasetMaxTokens = 'limit',
  datasetSearchMode = 'searchMode',
  datasetSearchEmbeddingWeight = 'embeddingWeight',
  datasetSearchUsingReRank = 'usingReRank',
  datasetSearchRerankWeight = 'rerankWeight',
  datasetSearchRerankModel = 'rerankModel',
  datasetSearchUsingExtensionQuery = 'datasetSearchUsingExtensionQuery',
  datasetSearchExtensionModel = 'datasetSearchExtensionModel',
  datasetSearchExtensionBg = 'datasetSearchExtensionBg',
  datasetSearchInput = 'datasetSearchInput',
  collectionFilterMatch = 'collectionFilterMatch',
  authTmbId = 'authTmbId',
  datasetQuoteList = 'system_datasetQuoteList',
  // content extract
  contextExtractInput = 'content',
  extractKeys = 'extractKeys',
  // http
  httpReqUrl = 'system_httpReqUrl',
  httpHeaders = 'system_httpHeader',
  httpMethod = 'system_httpMethod',
  httpParams = 'system_httpParams',
  httpJsonBody = 'system_httpJsonBody',
  httpFormBody = 'system_httpFormBody',
  httpContentType = 'system_httpContentType',
  httpTimeout = 'system_httpTimeout',
  addOutputParam = 'system_addOutputParam',
  // if else
  condition = 'condition',
  ifElseList = 'ifElseList',
  // variable update
  updateList = 'updateList',
  // 交互节点（蓝本 interactive）
  userSelectOptions = 'userSelectOptions',
  userInputForms = 'userInputForms',
  // 文档解析（蓝本 readFiles）
  fileUrlList = 'fileUrlList',
  // 代码运行（蓝本 sandbox）
  codeType = 'codeType',
}

/** 节点输出 key，字符串值与蓝本 NodeOutputKeyEnum 完全一致 */
export enum NodeOutputKeyEnum {
  userChatInput = 'userChatInput',
  userFiles = 'userFiles',
  history = 'history',
  answerText = 'answerText',
  reasoningText = 'reasoningText',
  success = 'success',
  text = 'system_text',
  errorText = 'system_error_text',
  /** code/http 节点专用错误输出键（蓝本两键并存） */
  error = 'error',
  addOutputParam = 'system_addOutputParam',
  rawResponse = 'system_rawResponse',
  httpRawError = 'system_httpRawError',
  datasetQuoteQA = 'quoteQA',
  cqResult = 'cqResult',
  contextExtractFields = 'fields',
  httpRawResponse = 'httpRawResponse',
  ifElseResult = 'ifElseResult',
  selectResult = 'selectResult',
  formInputResult = 'formInputResult',
}

/** 知识库检索模式（蓝本 DatasetSearchModeEnum） */
export enum DatasetSearchModeEnum {
  embedding = 'embedding',
  fullTextRecall = 'fullTextRecall',
  mixedRecall = 'mixedRecall',
}

export const DatasetSearchModeLabelMap: Record<string, string> = {
  [DatasetSearchModeEnum.embedding]: '语义检索',
  [DatasetSearchModeEnum.fullTextRecall]: '全文检索',
  [DatasetSearchModeEnum.mixedRecall]: '混合检索',
};

/** 判断器条件（蓝本 VariableConditionEnum） */
export enum VariableConditionEnum {
  equalTo = 'equalTo',
  notEqual = 'notEqual',
  isEmpty = 'isEmpty',
  isNotEmpty = 'isNotEmpty',
  include = 'include',
  notInclude = 'notInclude',
  startWith = 'startWith',
  endWith = 'endWith',
  reg = 'reg',
  greaterThan = 'greaterThan',
  greaterThanOrEqualTo = 'greaterThanOrEqualTo',
  lessThan = 'lessThan',
  lessThanOrEqualTo = 'lessThanOrEqualTo',
  lengthEqualTo = 'lengthEqualTo',
  lengthNotEqualTo = 'lengthNotEqualTo',
  lengthGreaterThan = 'lengthGreaterThan',
  lengthGreaterThanOrEqualTo = 'lengthGreaterThanOrEqualTo',
  lengthLessThan = 'lengthLessThan',
  lengthLessThanOrEqualTo = 'lengthLessThanOrEqualTo',
}

export const VariableConditionLabelMap: Record<string, string> = {
  [VariableConditionEnum.equalTo]: '等于',
  [VariableConditionEnum.notEqual]: '不等于',
  [VariableConditionEnum.isEmpty]: '为空',
  [VariableConditionEnum.isNotEmpty]: '不为空',
  [VariableConditionEnum.include]: '包含',
  [VariableConditionEnum.notInclude]: '不包含',
  [VariableConditionEnum.startWith]: '开头是',
  [VariableConditionEnum.endWith]: '结尾是',
  [VariableConditionEnum.reg]: '正则匹配',
  [VariableConditionEnum.greaterThan]: '大于',
  [VariableConditionEnum.greaterThanOrEqualTo]: '大于等于',
  [VariableConditionEnum.lessThan]: '小于',
  [VariableConditionEnum.lessThanOrEqualTo]: '小于等于',
  [VariableConditionEnum.lengthEqualTo]: '长度等于',
  [VariableConditionEnum.lengthNotEqualTo]: '长度不等于',
  [VariableConditionEnum.lengthGreaterThan]: '长度大于',
  [VariableConditionEnum.lengthGreaterThanOrEqualTo]: '长度大于等于',
  [VariableConditionEnum.lengthLessThan]: '长度小于',
  [VariableConditionEnum.lengthLessThanOrEqualTo]: '长度小于等于',
};

/** ifElse 分支结果值：分支 handle 与运行结果都使用该值 */
export enum IfElseResultEnum {
  IF = 'IF',
  ELSE_IF = 'ELSE IF',
  ELSE = 'ELSE',
}

/** 全局变量输入形态（chatConfig.variables[].type，蓝本 VariableInputEnum；
 * file/llmSelect/datasetSelect/custom/internal 依赖对应运行时，登记保留差异） */
export enum VariableInputEnum {
  input = 'input',
  textarea = 'textarea',
  numberInput = 'numberInput',
  select = 'select',
  multipleSelect = 'multipleSelect',
  switch = 'switch',
  password = 'password',
  timePointSelect = 'timePointSelect',
  timeRangeSelect = 'timeRangeSelect',
}

export const VariableInputLabelMap: Record<string, string> = {
  [VariableInputEnum.input]: '文本',
  [VariableInputEnum.textarea]: '多行文本',
  [VariableInputEnum.numberInput]: '数字',
  [VariableInputEnum.select]: '下拉单选',
  [VariableInputEnum.multipleSelect]: '下拉多选',
  [VariableInputEnum.switch]: '开关',
  [VariableInputEnum.password]: '密码',
  [VariableInputEnum.timePointSelect]: '时间点',
  [VariableInputEnum.timeRangeSelect]: '时间范围',
};

/** 画布连线桩 ID 约定（蓝本 getHandleId）：`${nodeId}-${type}-${key}` */
export const getHandleId = (nodeId: string, type: 'source' | 'target', key: string) =>
  `${nodeId}-${type}-${key}`;

/**
 * catchError 错误边的固定 handle（蓝本 getHandleId(nodeId, 'source_catch', 'right')）。
 * 字符串与 Python 端 workflow_engine._error_handle_id 完全一致。
 */
export const getErrorHandleId = (nodeId: string) => `${nodeId}-source_catch-right`;

/** 系统变量：编辑器变量选择面板固定提供（运行时注入） */
export const SYSTEM_VARIABLES = [
  { key: 'userId', label: '用户 ID', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'username', label: '用户名', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'realname', label: '用户姓名', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'appId', label: '应用 ID', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'chatId', label: '会话 ID', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'responseChatItemId', label: '本轮回复 ID', valueType: WorkflowIOValueTypeEnum.string },
  { key: 'histories', label: '聊天记录', valueType: WorkflowIOValueTypeEnum.chatHistory },
  { key: 'cTime', label: '当前时间', valueType: WorkflowIOValueTypeEnum.string },
];

/** 变量引用的虚拟节点 ID：value = [VARIABLE_NODE_ID, variableKey] 表示引用全局/系统变量 */
export const VARIABLE_NODE_ID = 'VARIABLE_NODE_ID';
