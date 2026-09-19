/**
 * 工作流核心类型，对齐 FastGPT v4.15 的 Store 层结构
 * （蓝本 packages/global/core/workflow/type/{node,io,edge}.ts）。
 */
import type {
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTemplateTypeEnum,
  FlowNodeTypeEnum,
  IfElseResultEnum,
  VariableConditionEnum,
  VariableInputEnum,
  WorkflowIOValueTypeEnum,
} from './constants';

/** 引用值：[nodeId, outputKey]；nodeId 为 VARIABLE_NODE_ID 时引用全局/系统变量 */
export type ReferenceItemValueType = [string, string];

export type FlowNodeInputItemType = {
  key: string;
  /** 渲染器列表，首项为默认；selectedTypeIndex 指向当前使用的渲染器 */
  renderTypeList: FlowNodeInputTypeEnum[];
  selectedTypeIndex?: number;
  valueType?: WorkflowIOValueTypeEnum;
  /** 字面量或 ReferenceItemValueType（renderType 为 reference 时） */
  value?: any;
  /** 输入的默认值（蓝本 defaultValue），切换渲染方式或重置时回填 */
  defaultValue?: any;
  label: string;
  description?: string;
  /** 输入值含义说明（蓝本 valueDesc），引用面板展示 */
  valueDesc?: string;
  placeholder?: string;
  /** reference 渲染器的占位提示（蓝本 referencePlaceholder） */
  referencePlaceholder?: string;
  required?: boolean;
  /** numberInput 渲染器参数 */
  min?: number;
  max?: number;
  step?: number;
  /** 数字输入的小数精度（蓝本 precision） */
  precision?: number;
  /** select 渲染器参数 */
  list?: { label: string; value: any }[];
  /** select 是否支持本地搜索 */
  searchable?: boolean;
  /** 滑块刻度（蓝本 markList） */
  markList?: { label: string; value: number }[];
  maxLength?: number;
  /** 文本最小长度（蓝本 minLength，password 等渲染器用） */
  minLength?: number;
  /** textarea 是否使用富文本 PromptEditor（蓝本 isRichText） */
  isRichText?: boolean;
  /** 该输入是否可作为工具参数暴露（蓝本 toolDescription），W2 工具调用用 */
  toolDescription?: string;
  /** 动态输入项标记（蓝本 D-6：用户在 addInputParam 中添加的平级输入） */
  canEdit?: boolean;
  /** 商业版能力标记（蓝本 isPro） */
  isPro?: boolean;
  /** 已废弃输入：兼容旧数据但不再展示（蓝本 deprecated） */
  deprecated?: boolean;
  /** 调试模式展示名（蓝本 debugLabel） */
  debugLabel?: string;
  /** 该输入来自工具输出（蓝本 isToolOutput） */
  isToolOutput?: boolean;
  /** 系统密钥输入配置列表（蓝本 inputList，key=system_input_config 时使用） */
  inputList?: NodeInputConfigType[];
  /** 动态输入的编辑配置（蓝本 customInputConfig） */
  customInputConfig?: CustomFieldConfigType;
  /** fileSelect 渲染器参数（蓝本文件输入属性组） */
  canSelectFile?: boolean;
  canSelectImg?: boolean;
  canSelectVideo?: boolean;
  canSelectAudio?: boolean;
  canSelectCustomFileExtension?: boolean;
  customFileExtensionList?: string[];
  canLocalUpload?: boolean;
  canUrlUpload?: boolean;
  maxFiles?: number;
  /** 时间渲染器参数（蓝本 timePointSelect/timeRangeSelect） */
  timeGranularity?: 'day' | 'hour' | 'minute' | 'second';
  timeRangeStart?: string;
  timeRangeEnd?: string;
  /** 知识库选择渲染器可选项（蓝本 datasetOptions） */
  datasetOptions?: { datasetId: string; avatar?: string; name?: string; isDeleted?: boolean }[];
};

/** 动态输出项的编辑配置（蓝本 CustomFieldConfigType） */
export type CustomFieldConfigType = {
  selectValueTypeList?: WorkflowIOValueTypeEnum[];
  showDefaultValue?: boolean;
  showDescription?: boolean;
  hideBottomDivider?: boolean;
};

/** 密钥值存储（蓝本 StoreSecretValueType）：只存配置状态/密文引用，明文不回传浏览器 */
export type StoreSecretValueType = Record<string, { secret: string; value?: string }>;

/** 工具展示元数据（蓝本 ToolDataSchema） */
export type NodeToolDataType = {
  diagram?: string;
  userGuide?: string;
  courseUrl?: string;
  readmeUrl?: string;
  name?: string;
  avatar?: string;
  error?: string;
  status?: string;
};

/** 节点绑定的工具配置（蓝本 NodeToolConfigType）：动态 tool/toolSet/pluginModule 节点用 */
export type NodeToolConfigType = {
  mcpToolSet?: {
    url: string;
    headerSecret?: StoreSecretValueType | null;
    toolList: { name: string; description?: string; inputSchema?: Record<string, any> }[];
  };
  mcpTool?: { toolId: string };
  systemTool?: { toolId: string; source?: string };
  systemToolSet?: {
    toolId: string;
    source?: string;
    toolList: { toolId: string; name: string; description: string }[];
  };
  httpToolSet?: {
    toolList: Record<string, any>[];
    baseUrl?: string;
    apiSchemaStr?: string;
    customHeaders?: string;
    headerSecret?: StoreSecretValueType | null;
  };
  httpTool?: { toolId: string };
};

/** 系统密钥输入配置项（蓝本 InputConfigType，input.inputList 用） */
export type NodeInputConfigType = {
  key: string;
  label: string;
  description?: string;
  required?: boolean;
  inputType: 'input' | 'numberInput' | 'secret' | 'switch' | 'select';
  value?: any;
  list?: { label: string; value: string }[];
};

export type FlowNodeOutputItemType = {
  id: string;
  key: string;
  label: string;
  description?: string;
  /** 输出值含义说明（蓝本 valueDesc） */
  valueDesc?: string;
  type: FlowNodeOutputTypeEnum;
  valueType?: WorkflowIOValueTypeEnum;
  /** 静态输出的默认值/静态值（蓝本 value） */
  value?: any;
  required?: boolean;
  /** required 输出未被执行器赋值时的回填值（蓝本 dispatch） */
  defaultValue?: any;
  /** 该输出当前不可用（蓝本 invalid，如模型不支持 reasoning 时） */
  invalid?: boolean;
  /** 动态输出项编辑配置（蓝本 customFieldConfig） */
  customFieldConfig?: CustomFieldConfigType;
  /** 已废弃输出：兼容旧数据但不再展示（蓝本 deprecated） */
  deprecated?: boolean;
};

/** 节点调试结果（蓝本 FlowNodeItemType.debugResult） */
export type NodeDebugResultType = {
  status: 'running' | 'success' | 'skipped' | 'failed';
  message?: string;
  response?: any;
  isExpired?: boolean;
};

/** 画布节点持久化结构（蓝本 StoreNodeItemType） */
export type StoreNodeItemType = {
  nodeId: string;
  /** 父容器节点 ID（蓝本 parentNodeId，loopRun/parallelRun 子图节点用；容器节点待 P1 实现） */
  parentNodeId?: string;
  flowNodeType: FlowNodeTypeEnum;
  /** 废弃节点标记（蓝本 abandon），兼容旧数据展示提示 */
  abandon?: boolean;
  name: string;
  intro?: string;
  avatar?: string;
  /** 渐变头像（蓝本 avatarLinear） */
  avatarLinear?: string;
  /** 来源色系（蓝本 colorSchema） */
  colorSchema?: string;
  /** 节点作为工具挂载时的说明（蓝本节点级 toolDescription） */
  toolDescription?: string;
  position: { x: number; y: number };
  version?: string;
  /** 版本展示名（蓝本 versionLabel，仅 UI 展示） */
  versionLabel?: string;
  /** 是否保持最新版本（蓝本 isLatestVersion，仅 UI 展示） */
  isLatestVersion?: boolean;
  /** 对话响应中是否展示运行状态（蓝本 showStatus） */
  showStatus?: boolean;
  /** 报错捕获开关（蓝本 catchError）：开启后错误走 source_catch 错误边 */
  catchError?: boolean;
  inputs: FlowNodeInputItemType[];
  outputs: FlowNodeOutputItemType[];
  /** 动态节点来源（蓝本 plugin data）：tool/toolSet/pluginModule/appModule 用 */
  pluginId?: string;
  source?: string;
  isFolder?: boolean;
  pluginData?: NodeToolDataType;
  /** 节点绑定的工具配置（蓝本 toolConfig） */
  toolConfig?: NodeToolConfigType;
  /** 费用/密钥/教程展示字段（蓝本 computed，不参与执行） */
  currentCost?: number;
  systemKeyCost?: number;
  hasTokenFee?: boolean;
  hasSystemSecret?: boolean;
  readmeUrl?: string;
  /** 节点折叠态（蓝本 isFolded，仅画布展示，随图持久化） */
  isFolded?: boolean;
  /** 节点错误态（蓝本 isError，运行时计算，不持久化语义） */
  isError?: boolean;
  /** 调试结果覆盖层数据（蓝本 debugResult，运行时写入） */
  debugResult?: NodeDebugResultType;
};

/** 画布连线持久化结构（蓝本 StoreEdgeItemType） */
export type StoreEdgeItemType = {
  source: string;
  sourceHandle: string;
  target: string;
  targetHandle: string;
};

/** 节点模板（蓝本 FlowNodeTemplateType 子集）：添加节点菜单与实例工厂的数据源；
 * 动态节点（tool/toolSet/pluginModule/appModule）的服务端 preview node 也用此形状表达 */
export type FlowNodeTemplateType = {
  id: string;
  flowNodeType: FlowNodeTypeEnum;
  templateType: FlowNodeTemplateTypeEnum;
  name: string;
  intro: string;
  /** Ant Design 图标组件名，替代蓝本 avatar 图片 */
  icon: string;
  /** 节点头部主题色 */
  color: string;
  /** 动态节点头像 URL（蓝本 avatar，preview node 用；本地模板用 icon） */
  avatar?: string;
  /** 渐变头像（蓝本 avatarLinear，预留） */
  avatarLinear?: string;
  /** 来源色系（蓝本 colorSchema，预留） */
  colorSchema?: string;
  showSourceHandle: boolean;
  showTargetHandle: boolean;
  /** 可被 toolCall/agent 作为工具挂载（蓝本 isTool），工具锚点待 P1 接线 */
  isTool?: boolean;
  /** 对话响应中展示运行状态（蓝本 showStatus） */
  showStatus?: boolean;
  /** 支持报错捕获（蓝本 catchError；字段存在即支持，值为默认开关状态） */
  catchError?: boolean;
  /** 节点帮助文档路径（蓝本 courseUrl，相对 FastGPT 文档站） */
  courseUrl?: string;
  /** 节点作为工具挂载时的说明（蓝本 toolDescription） */
  toolDescription?: string;
  forbidDelete?: boolean;
  unique?: boolean;
  /** 动态节点来源与配置（蓝本 plugin data，preview node 携带，落图时必须原样保存） */
  pluginId?: string;
  source?: string;
  isFolder?: boolean;
  pluginData?: NodeToolDataType;
  toolConfig?: NodeToolConfigType;
  version?: string;
  versionLabel?: string;
  isLatestVersion?: boolean;
  currentCost?: number;
  systemKeyCost?: number;
  hasTokenFee?: boolean;
  hasSystemSecret?: boolean;
  readmeUrl?: string;
  inputs: FlowNodeInputItemType[];
  outputs: FlowNodeOutputItemType[];
};

/** 全局变量声明（chatConfig.variables[]，蓝本 VariableItemType） */
export type VariableItemType = {
  id: string;
  key: string;
  label: string;
  type: VariableInputEnum;
  required: boolean;
  description?: string;
  valueType?: WorkflowIOValueTypeEnum;
  defaultValue?: any;
  /** select / multipleSelect 类型的选项 */
  enums?: { label?: string; value: string }[];
  min?: number;
  max?: number;
  maxLength?: number;
};

/** 文件上传配置（蓝本 AppFileSelectConfigType） */
export type AppFileSelectConfigType = {
  canSelectFile: boolean;
  canSelectImg: boolean;
  canSelectVideo?: boolean;
  canSelectAudio?: boolean;
  canSelectCustomFileExtension?: boolean;
  customFileExtensionList?: string[];
  customPdfParse?: boolean;
  maxFiles: number;
};

export type RecommendationSceneConfig = {
  key: string;
  label: string;
  textList: string[];
};

/** 对话配置（蓝本 AppChatConfigType 九区块） */
export type AppChatConfigType = {
  welcomeText?: string;
  variables?: VariableItemType[];
  fileSelectConfig?: AppFileSelectConfigType;
  /** 问题引导（猜你想问） */
  questionGuide?: { open: boolean; model?: string; customPrompt?: string };
  /** 语音播报（浏览器内置语音合成） */
  ttsConfig?: { type: 'none' | 'web' };
  /** 语音输入（运行时依赖语音识别接入） */
  whisperConfig?: { open: boolean; autoSend: boolean; autoTTSResponse: boolean };
  /** 定时执行（运行时依赖 cron 调度） */
  scheduledTriggerConfig?: { cronString: string; timezone: string; defaultPrompt: string } | null;
  /** 运行页右栏推荐内容；textList 保留为旧数据与第三方格式的扁平兼容层 */
  chatInputGuide?: {
    open: boolean;
    textList: string[];
    sceneList?: RecommendationSceneConfig[];
    customUrl?: string;
  };
  /** 进入会话自动执行 */
  autoExecute?: { open: boolean; defaultPrompt?: string };
  /** 对话前引导说明 */
  instruction?: string;
  [key: string]: any;
};

/** 判断器条件组（蓝本 IfElseListItemType）。
 * valueType 表达右值输入方式（input/reference，蓝本语义），非数据类型 */
export type IfElseConditionItemType = {
  variable?: ReferenceItemValueType;
  condition?: VariableConditionEnum;
  /** input 方式为字符串字面量；reference 方式为 [nodeId, outputKey] */
  value?: string | ReferenceItemValueType;
  valueType?: 'input' | 'reference';
};

export type IfElseListItemType = {
  condition: 'AND' | 'OR';
  list: IfElseConditionItemType[];
};

/** 变量更新节点的更新项（蓝本 TUpdateListItem）；运算模式字段仅 input 模式生效 */
export type TUpdateListItem = {
  variable?: ReferenceItemValueType;
  /** input 模式为 ['', 字面量]；reference 模式为 [nodeId, outputKey] */
  value?: ReferenceItemValueType;
  valueType?: WorkflowIOValueTypeEnum;
  renderType: FlowNodeInputTypeEnum.input | FlowNodeInputTypeEnum.reference;
  /** 数字运算（蓝本 numberOperator）：'/' 且除数为 0 时保留旧值 */
  numberOperator?: '=' | '+' | '-' | '*' | '/';
  /** 布尔模式（蓝本 booleanMode）：negate = 旧值取反 */
  booleanMode?: 'true' | 'false' | 'negate';
  /** 数组模式（蓝本 arrayMode）：equal 整组替换 / append 追加元素 / clear 清空 */
  arrayMode?: 'equal' | 'append' | 'clear';
};

/** 问题分类节点的分类项（蓝本 ClassifyQuestionAgentItemType） */
export type ClassifyQuestionAgentItemType = {
  key: string;
  value: string;
};

/** 文本提取字段（蓝本 ContextExtractAgentItemType） */
export type ContextExtractAgentItemType = {
  desc: string;
  key: string;
  required: boolean;
  defaultValue?: string;
  valueType?: WorkflowIOValueTypeEnum;
  enum?: string;
};

/** 编辑器持久化的画布模型 */
export type WorkflowGraphType = {
  nodes: StoreNodeItemType[];
  edges: StoreEdgeItemType[];
  chatConfig: AppChatConfigType;
};

/**
 * draftJson/publishedJson 的持久化信封：fastgpt 画布模型是唯一事实源
 * （Python 运行时 parse_graph 只读 fastgpt 字段）。
 * 顶层 nodes/edges 为旧 v0.1 Java 编译结果，仅存量数据读取迁移用，保存时不再写入。
 */
export type WorkflowPersistType = {
  version: string;
  fastgpt: WorkflowGraphType;
  /** @deprecated 旧 v0.1 运行格式，读取迁移专用 */
  nodes?: any[];
  /** @deprecated 旧 v0.1 运行格式，读取迁移专用 */
  edges?: any[];
  /** @deprecated 旧编译壳的 unsupported 提示，已不再写入 */
  compiler?: {
    unsupported: { nodeId: string; flowNodeType: string; name: string; reason: string }[];
    compiledAt?: string;
  };
};

export type IfElseBranchKey = IfElseResultEnum | string;
