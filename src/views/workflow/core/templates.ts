/**
 * 节点模板注册表，对齐 FastGPT v4.15 蓝本
 * （reference/FastGPT packages/global/core/workflow/template/system/）。
 * 输入/输出的 key、renderTypeList、valueType 与蓝本一致；文案本地化为中文。
 */
import {
  DatasetSearchModeEnum,
  FlowNodeInputTypeEnum,
  FlowNodeOutputTypeEnum,
  FlowNodeTemplateTypeEnum,
  FlowNodeTypeEnum,
  NodeInputKeyEnum,
  NodeOutputKeyEnum,
  WorkflowIOValueTypeEnum,
} from './constants';
import type { FlowNodeInputItemType, FlowNodeTemplateType } from './type';

/** 共享输入模板（蓝本 template/input.ts） */
export const Input_Template_UserChatInput: FlowNodeInputItemType = {
  key: NodeInputKeyEnum.userChatInput,
  renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.textarea],
  valueType: WorkflowIOValueTypeEnum.string,
  label: '用户问题',
  required: true,
  toolDescription: 'user question',
};

export const Input_Template_History: FlowNodeInputItemType = {
  key: NodeInputKeyEnum.history,
  renderTypeList: [FlowNodeInputTypeEnum.numberInput, FlowNodeInputTypeEnum.reference],
  valueType: WorkflowIOValueTypeEnum.chatHistory,
  label: '聊天记录',
  valueDesc: '{ obj: System | Human | AI; value: string }[]',
  description: '最多携带的历史对话轮数',
  required: true,
  min: 0,
  max: 50,
  value: 6,
};

export const Input_Template_SettingAiModel: FlowNodeInputItemType = {
  key: NodeInputKeyEnum.aiModel,
  renderTypeList: [FlowNodeInputTypeEnum.settingLLMModel, FlowNodeInputTypeEnum.reference],
  valueType: WorkflowIOValueTypeEnum.string,
  label: 'AI 模型',
  required: true,
};

export const Input_Template_System_Prompt: FlowNodeInputItemType = {
  key: NodeInputKeyEnum.aiSystemPrompt,
  renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
  valueType: WorkflowIOValueTypeEnum.string,
  label: '提示词',
  description: '模型固定的引导词，可使用变量，通过调整该内容引导模型聊天方向',
  placeholder: '例如：你是一个严谨的校园业务助手。',
  maxLength: 100000,
  isRichText: true,
};

/** 文件链接输入（蓝本 Input_Template_File_Link）：用户上传的文档和图片链接 */
const Input_Template_File_Link = {
  key: NodeInputKeyEnum.fileUrlList,
  renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.input],
  label: '文件链接',
  description: '用户上传的文档和图片链接',
  valueType: WorkflowIOValueTypeEnum.arrayString,
};

const Output_Template_Error_Message = {
  id: NodeOutputKeyEnum.errorText,
  key: NodeOutputKeyEnum.errorText,
  label: '错误信息',
  type: FlowNodeOutputTypeEnum.error,
  valueType: WorkflowIOValueTypeEnum.string,
};

/** 系统配置（蓝本 SystemConfigNode）：固定节点，无锚点无 IO，节点内直接编辑应用级 chatConfig */
export const SystemConfigTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.systemConfig,
  flowNodeType: FlowNodeTypeEnum.systemConfig,
  templateType: FlowNodeTemplateTypeEnum.systemInput,
  name: '系统配置',
  intro: '',
  icon: 'SettingOutlined',
  color: '#d64ba3',
  showSourceHandle: false,
  showTargetHandle: false,
  forbidDelete: true,
  unique: true,
  inputs: [],
  outputs: [],
};

/** 流程开始（蓝本 WorkflowStart）：userFiles 输出由系统配置的文件开关动态注入（syncUserFilesOutput） */
export const WorkflowStartTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.workflowStart,
  flowNodeType: FlowNodeTypeEnum.workflowStart,
  templateType: FlowNodeTemplateTypeEnum.systemInput,
  name: '流程开始',
  intro: '工作流入口，接收用户问题与全局变量',
  icon: 'PlayCircleOutlined',
  color: '#3370ff',
  showSourceHandle: true,
  showTargetHandle: false,
  forbidDelete: true,
  unique: true,
  // 蓝本 workflowStart：inputs 保留原 renderTypeList（节点不渲染 inputs），仅覆盖 toolDescription
  inputs: [{ ...Input_Template_UserChatInput, toolDescription: '用户问题' }],
  outputs: [
    {
      id: NodeOutputKeyEnum.userChatInput,
      key: NodeOutputKeyEnum.userChatInput,
      label: '用户问题',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** AI 对话（蓝本 AiChatModule）：模型参数为 hidden 输入，经模型设置弹窗编辑 */
export const AiChatTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.chatNode,
  flowNodeType: FlowNodeTypeEnum.chatNode,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: 'AI 对话',
  intro: '调用 AI 模型进行一轮对话生成',
  icon: 'MessageOutlined',
  color: '#005bac',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/ai_chat',
  version: '4.9.7',
  isTool: true,
  inputs: [
    Input_Template_SettingAiModel,
    {
      key: NodeInputKeyEnum.aiChatTemperature,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatMaxToken,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatIsResponseText,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      value: true,
      valueType: WorkflowIOValueTypeEnum.boolean,
    },
    {
      key: NodeInputKeyEnum.aiChatVision,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatKnowledgeImages,
      renderTypeList: [FlowNodeInputTypeEnum.select],
      label: '知识库图片输出',
      description: '智能选择会让回答模型在本轮合法候选图中自主选图，程序校验并确定性展示；不会让模型生成或猜测链接。',
      valueType: WorkflowIOValueTypeEnum.string,
      value: 'smart',
      list: [
        { label: '智能选择（推荐）', value: 'smart' },
        { label: '展示所有召回图', value: 'all' },
        { label: '不展示知识库图片', value: 'off' },
      ],
    },
    {
      key: NodeInputKeyEnum.aiChatAudio,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.aiChatVideo,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.aiChatExtractFiles,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatReasoning,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatReasoningEffort,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatTopP,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatStopSign,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatResponseFormat,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatJsonSchema,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Input_Template_System_Prompt,
    Input_Template_History,
    {
      key: NodeInputKeyEnum.aiChatDatasets,
      renderTypeList: [FlowNodeInputTypeEnum.selectDataset],
      label: '知识库',
      description: '直接使用用户问题检索所选知识库；使用上游“知识库引用”时请留空。',
      value: [],
      valueType: WorkflowIOValueTypeEnum.selectDataset,
    },
    {
      key: NodeInputKeyEnum.aiChatDatasetQuote,
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '知识库引用',
      description: '接收上游知识库搜索或引用合并节点的输出；仅在本节点未直接选择知识库时生效。',
      valueType: WorkflowIOValueTypeEnum.datasetQuote,
    },
    {
      key: NodeInputKeyEnum.skills,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: 'Skill',
      value: [],
      valueType: WorkflowIOValueTypeEnum.arrayObject,
    },
    Input_Template_File_Link,
    { ...Input_Template_UserChatInput, toolDescription: '用户问题' },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.history,
      key: NodeOutputKeyEnum.history,
      label: '新的上下文',
      description: '拼接本轮问答的完整上下文',
      valueDesc: '{ obj: System | Human | AI; value: string }[]',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.chatHistory,
      required: true,
    },
    {
      id: NodeOutputKeyEnum.answerText,
      key: NodeOutputKeyEnum.answerText,
      label: 'AI 回复内容',
      description: '触发流式回复输出',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
    {
      id: NodeOutputKeyEnum.reasoningText,
      key: NodeOutputKeyEnum.reasoningText,
      label: '思考过程',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
      invalid: true,
    },
    Output_Template_Error_Message,
  ],
};

/** 知识库搜索（蓝本 DatasetSearchModule）：检索参数为 hidden，经检索参数弹窗编辑 */
export const DatasetSearchTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.datasetSearchNode,
  flowNodeType: FlowNodeTypeEnum.datasetSearchNode,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '知识库搜索',
  intro: '在选定的知识库中检索与问题相关的内容引用',
  icon: 'DatabaseOutlined',
  color: '#047857',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/dataset_search',
  version: '4.9.2',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.datasetSelectList,
      renderTypeList: [FlowNodeInputTypeEnum.selectDataset, FlowNodeInputTypeEnum.reference],
      label: '关联的知识库',
      valueDesc: '[{ "datasetId": "xxx" }]',
      valueType: WorkflowIOValueTypeEnum.selectDataset,
      value: [],
      required: true,
    },
    {
      key: NodeInputKeyEnum.datasetMaxTokens,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '引用上限',
      value: 5000,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.datasetSearchMode,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
      value: DatasetSearchModeEnum.embedding,
    },
    {
      key: NodeInputKeyEnum.datasetSearchEmbeddingWeight,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
      value: 0.5,
    },
    {
      key: NodeInputKeyEnum.datasetSearchUsingReRank,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.datasetSearchRerankModel,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.datasetSearchRerankWeight,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
      value: 0.5,
    },
    {
      key: NodeInputKeyEnum.authTmbId,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.datasetSearchUsingExtensionQuery,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.datasetSearchExtensionModel,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.datasetSearchExtensionBg,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
      value: '',
    },
    {
      key: NodeInputKeyEnum.datasetSearchInput,
      renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.textarea],
      label: '检索问题',
      valueType: WorkflowIOValueTypeEnum.arrayString,
      required: true,
      toolDescription: '需要检索的问题',
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.datasetQuoteQA,
      key: NodeOutputKeyEnum.datasetQuoteQA,
      valueDesc: '{ id, datasetId, collectionId, sourceName, sourceId?, q, a, imageUrls?: string[] }[]',
      label: '知识库引用',
      description: '检索到的引用片段数组，包含关联图片地址，可接入 AI 对话或引用合并节点',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.datasetQuote,
    },
    Output_Template_Error_Message,
  ],
};

/** 知识库搜索引用合并（蓝本 DatasetConcatModule） */
export const DatasetConcatTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.datasetConcatNode,
  flowNodeType: FlowNodeTypeEnum.datasetConcatNode,
  templateType: FlowNodeTemplateTypeEnum.other,
  name: '知识库引用合并',
  intro: '合并多个知识库搜索结果并按上限截断',
  icon: 'MergeCellsOutlined',
  color: '#0f766e',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: false,
  courseUrl: '/guide/build/workflow/nodes/knowledge_base_search_merge',
  inputs: [
    {
      key: NodeInputKeyEnum.datasetMaxTokens,
      renderTypeList: [FlowNodeInputTypeEnum.numberInput],
      label: '引用上限',
      description: '合并后引用内容的最大 Token 数',
      value: 3000,
      min: 100,
      max: 20000,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.datasetQuoteList,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: '引用来源',
      valueType: WorkflowIOValueTypeEnum.datasetQuote,
      value: [],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.datasetQuoteQA,
      key: NodeOutputKeyEnum.datasetQuoteQA,
      valueDesc: '{ id, datasetId, collectionId, sourceName, sourceId?, q, a, imageUrls?: string[] }[]',
      label: '知识库引用',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.datasetQuote,
    },
  ],
};

/** 指定回复（蓝本 AssignedAnswerModule） */
export const AssignedAnswerTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.answerNode,
  flowNodeType: FlowNodeTypeEnum.answerNode,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '指定回复',
  intro: '直接向用户回复一段固定或引用的内容',
  icon: 'CommentOutlined',
  color: '#7c3aed',
  showSourceHandle: true,
  showTargetHandle: true,
  courseUrl: '/guide/build/workflow/nodes/reply',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.answerText,
      renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
      valueType: WorkflowIOValueTypeEnum.any,
      label: '回复内容',
      description: '可使用变量，如 {{var}}。引用变量时会忽略文本内容',
      placeholder: '可以使用 \\n 来实现连续换行。可以通过外部模块输入实现回复，外部模块输入时会覆盖当前填写的内容',
      required: true,
      maxLength: 100000,
    },
  ],
  outputs: [],
};

/** 问题分类（蓝本 ClassifyQuestionModule）：分类项 key 即分支 handle key */
export const ClassifyQuestionTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.classifyQuestion,
  flowNodeType: FlowNodeTypeEnum.classifyQuestion,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '问题分类',
  intro: '根据用户问题类型进入不同分支',
  icon: 'ForkOutlined',
  color: '#d97706',
  showSourceHandle: false,
  showTargetHandle: true,
  showStatus: true,
  courseUrl: '/guide/build/workflow/nodes/question_classify',
  version: '4.9.2',
  inputs: [
    {
      key: NodeInputKeyEnum.aiModel,
      renderTypeList: [FlowNodeInputTypeEnum.selectLLMModel, FlowNodeInputTypeEnum.reference],
      label: '分类模型',
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
    {
      key: NodeInputKeyEnum.aiSystemPrompt,
      renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
      label: '背景知识',
      description: '辅助分类的业务背景说明',
      placeholder: '例如：\n1. AIGC（人工智能生成内容）是指使用人工智能技术自动或半自动地生成数字内容…\n2. 当前对话与 xxx 业务有关。',
      maxLength: 100000,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Input_Template_History,
    Input_Template_UserChatInput,
    {
      key: NodeInputKeyEnum.agents,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      valueType: WorkflowIOValueTypeEnum.any,
      label: '',
      value: [
        { value: '打招呼', key: 'wqre' },
        { value: '关于 xxx 的问题', key: 'sdfa' },
        { value: '其他问题', key: 'agex' },
      ],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.cqResult,
      key: NodeOutputKeyEnum.cqResult,
      label: '分类结果',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
  ],
};

/** 文本内容提取（蓝本 ContextExtractModule） */
export const ContentExtractTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.contentExtract,
  flowNodeType: FlowNodeTypeEnum.contentExtract,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '文本内容提取',
  intro: '从文本中提取指定的结构化字段',
  icon: 'FilterOutlined',
  color: '#4338ca',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/content_extract',
  version: '4.9.2',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.aiModel,
      renderTypeList: [FlowNodeInputTypeEnum.selectLLMModel],
      label: '提取模型',
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
    {
      key: NodeInputKeyEnum.description,
      renderTypeList: [FlowNodeInputTypeEnum.textarea],
      label: '提取要求描述',
      description: '写明需要提取哪些信息，模型会按字段列表输出',
      placeholder: '例如：提取请假的开始时间、结束时间和请假原因',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Input_Template_History,
    {
      key: NodeInputKeyEnum.contextExtractInput,
      renderTypeList: [FlowNodeInputTypeEnum.reference, FlowNodeInputTypeEnum.textarea],
      label: '需要提取的文本',
      required: true,
      valueType: WorkflowIOValueTypeEnum.string,
      toolDescription: '需要检索的内容',
    },
    {
      key: NodeInputKeyEnum.extractKeys,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: '',
      description: "由 '描述' 和 'key' 组成一个目标字段，可提取多个目标字段",
      valueType: WorkflowIOValueTypeEnum.any,
      value: [],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.success,
      key: NodeOutputKeyEnum.success,
      label: '提取是否完整',
      description: '全部必填字段均成功提取时为 true',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.boolean,
      required: true,
    },
    {
      id: NodeOutputKeyEnum.contextExtractFields,
      key: NodeOutputKeyEnum.contextExtractFields,
      label: '完整提取结果',
      description: '提取字段组成的 JSON 字符串',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
    Output_Template_Error_Message,
  ],
};

/** HTTP 请求（蓝本 HttpNode468） */
export const HttpRequestTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.httpRequest468,
  flowNodeType: FlowNodeTypeEnum.httpRequest468,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: 'HTTP 请求',
  intro: '调用外部 HTTP 接口并返回响应',
  icon: 'ApiOutlined',
  color: '#c2410c',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/http',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.addInputParam,
      renderTypeList: [FlowNodeInputTypeEnum.addInputParam],
      valueType: WorkflowIOValueTypeEnum.dynamic,
      label: '自定义输入',
      description: '接收前方节点的输出值作为变量，在 URL/请求头/请求体中以 {{key}} 使用（HTTP 动态输入）',
      required: false,
      customInputConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: true,
      },
    },
    {
      key: NodeInputKeyEnum.httpMethod,
      renderTypeList: [FlowNodeInputTypeEnum.select],
      label: '请求方式',
      value: 'POST',
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
      list: [
        { label: 'GET', value: 'GET' },
        { label: 'POST', value: 'POST' },
        { label: 'PUT', value: 'PUT' },
        { label: 'DELETE', value: 'DELETE' },
        { label: 'PATCH', value: 'PATCH' },
      ],
    },
    {
      key: NodeInputKeyEnum.httpReqUrl,
      renderTypeList: [FlowNodeInputTypeEnum.input],
      label: '请求地址',
      placeholder: 'https://api.example.com/path',
      valueType: WorkflowIOValueTypeEnum.string,
      required: false,
    },
    {
      key: NodeInputKeyEnum.headerSecret,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.object,
      required: false,
    },
    {
      key: NodeInputKeyEnum.httpTimeout,
      renderTypeList: [FlowNodeInputTypeEnum.numberInput],
      label: '超时时间(秒)',
      value: 30,
      min: 5,
      max: 600,
      valueType: WorkflowIOValueTypeEnum.number,
      required: true,
    },
    {
      key: NodeInputKeyEnum.httpHeaders,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: '请求头',
      valueType: WorkflowIOValueTypeEnum.any,
      value: [],
      description: 'Header 键值对，值支持 {{变量}}',
    },
    {
      key: NodeInputKeyEnum.httpParams,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: 'Query 参数',
      valueType: WorkflowIOValueTypeEnum.any,
      value: [],
      description: 'URL 查询参数键值对，值支持 {{变量}}',
    },
    {
      key: NodeInputKeyEnum.httpContentType,
      renderTypeList: [FlowNodeInputTypeEnum.select],
      label: 'Content-Type',
      value: 'json',
      valueType: WorkflowIOValueTypeEnum.string,
      list: [
        { label: 'JSON', value: 'json' },
        { label: 'form-data', value: 'form-data' },
        { label: 'x-www-form-urlencoded', value: 'x-www-form-urlencoded' },
        { label: 'xml', value: 'xml' },
        { label: 'raw text', value: 'raw-text' },
        { label: 'none', value: 'none' },
      ],
    },
    {
      key: NodeInputKeyEnum.httpJsonBody,
      renderTypeList: [FlowNodeInputTypeEnum.JSONEditor],
      label: '请求体(JSON / raw)',
      description: 'Content-Type 为 JSON 或 raw text 时使用；支持 {{变量}} 与 {{$节点ID.输出$}}',
      valueType: WorkflowIOValueTypeEnum.any,
      value: '',
      placeholder: '{\n  "question": "{{userChatInput}}"\n}',
    },
    {
      key: NodeInputKeyEnum.httpFormBody,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      label: '表单请求体',
      description: 'Content-Type 为 form-data / x-www-form-urlencoded 时使用',
      valueType: WorkflowIOValueTypeEnum.any,
      value: [],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.addOutputParam,
      key: NodeOutputKeyEnum.addOutputParam,
      type: FlowNodeOutputTypeEnum.dynamic,
      valueType: WorkflowIOValueTypeEnum.dynamic,
      label: '输出字段提取',
      description: '可以通过 JSONPath 语法来提取响应值中的指定字段',
      customFieldConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: false,
      },
    },
    {
      id: NodeOutputKeyEnum.httpRawResponse,
      key: NodeOutputKeyEnum.httpRawResponse,
      label: '原始响应',
      description: 'HTTP请求的原始响应。只能接受字符串或JSON类型响应数据。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.any,
      required: true,
    },
    {
      id: NodeOutputKeyEnum.httpRawError,
      key: NodeOutputKeyEnum.httpRawError,
      label: '完整错误',
      description: 'HTTP 请求失败时的完整错误对象，包含 message、status、code、data 等字段。',
      type: FlowNodeOutputTypeEnum.error,
      valueType: WorkflowIOValueTypeEnum.object,
    },
    {
      id: NodeOutputKeyEnum.error,
      key: NodeOutputKeyEnum.error,
      label: '错误信息',
      type: FlowNodeOutputTypeEnum.error,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** 判断器（蓝本 IfElseNode）：分支 handle 为 IF / ELSE IF / ELSE */
export const IfElseTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.ifElseNode,
  flowNodeType: FlowNodeTypeEnum.ifElseNode,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '判断器',
  intro: '根据条件判断执行不同分支',
  icon: 'BranchesOutlined',
  color: '#a16207',
  showSourceHandle: false,
  showTargetHandle: true,
  showStatus: true,
  courseUrl: '/guide/build/workflow/nodes/tfswitch',
  inputs: [
    {
      key: NodeInputKeyEnum.ifElseList,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.any,
      value: [
        {
          condition: 'AND',
          list: [{ variable: undefined, condition: undefined, value: undefined, valueType: 'input' }],
        },
      ],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.ifElseResult,
      key: NodeOutputKeyEnum.ifElseResult,
      label: '判断结果',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** 文本拼接（蓝本 TextEditorNode） */
export const TextEditorTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.textEditor,
  flowNodeType: FlowNodeTypeEnum.textEditor,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '文本拼接',
  intro: '对输入的变量与固定文本做模板拼接',
  icon: 'FontSizeOutlined',
  color: '#0369a1',
  showSourceHandle: true,
  showTargetHandle: true,
  courseUrl: '/guide/build/workflow/nodes/text_editor',
  // 蓝本 v4.15：模板已移除 system_addInputParam（运行时保留旧稿兼容读取），文本框内直接引用变量
  inputs: [
    {
      key: NodeInputKeyEnum.textareaInput,
      renderTypeList: [FlowNodeInputTypeEnum.textarea],
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
      label: '拼接文本',
      placeholder: '可输入 / 唤起变量列表；支持 {{变量}} 与 {{$节点ID.输出$}} 引用',
      value: '',
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.text,
      key: NodeOutputKeyEnum.text,
      label: '拼接结果',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** 变量更新（蓝本 VariableUpdateNode） */
export const VariableUpdateTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.variableUpdate,
  flowNodeType: FlowNodeTypeEnum.variableUpdate,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '变量更新',
  intro: '更新全局变量或指定节点输出的值',
  icon: 'SwapOutlined',
  color: '#475569',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: false,
  courseUrl: '/guide/build/workflow/nodes/variable_update',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.updateList,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.any,
      value: [
        {
          variable: ['', ''],
          value: ['', ''],
          valueType: WorkflowIOValueTypeEnum.string,
          renderType: FlowNodeInputTypeEnum.input,
        },
      ],
    },
  ],
  outputs: [],
};

/** 用户选择（蓝本 UserSelectNode，交互节点）：每个选项一个分支出口；运行时挂起等待应答（LangGraph interrupt） */
export const UserSelectTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.userSelect,
  flowNodeType: FlowNodeTypeEnum.userSelect,
  templateType: FlowNodeTemplateTypeEnum.interactive,
  name: '用户选择',
  intro: '暂停运行，等待用户从选项中选择后走对应分支',
  icon: 'InteractionOutlined',
  color: '#16a34a',
  showSourceHandle: false,
  showTargetHandle: true,
  courseUrl: '/guide/build/workflow/nodes/user-selection',
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.description,
      renderTypeList: [FlowNodeInputTypeEnum.textarea],
      label: '说明文案',
      description: '展示给用户的选择提示',
      placeholder: '例如: \n冰箱里是否有西红柿？',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.userSelectOptions,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      valueType: WorkflowIOValueTypeEnum.any,
      label: '选项列表',
      value: [
        { value: '确认', key: 'option1' },
        { value: '取消', key: 'option2' },
      ],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.selectResult,
      key: NodeOutputKeyEnum.selectResult,
      label: '选择结果',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
  ],
};

/** 表单输入（蓝本 FormInputNode，交互节点）：暂停收集表单后继续 */
export const FormInputTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.formInput,
  flowNodeType: FlowNodeTypeEnum.formInput,
  templateType: FlowNodeTemplateTypeEnum.interactive,
  name: '表单输入',
  intro: '暂停运行，等待用户填写表单后继续',
  icon: 'FormOutlined',
  color: '#7c3aed',
  showSourceHandle: true,
  showTargetHandle: true,
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.description,
      renderTypeList: [FlowNodeInputTypeEnum.textarea],
      label: '说明文案',
      description: '展示给用户的填写提示',
      placeholder: '例如：\n补充您的信息',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.userInputForms,
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      valueType: WorkflowIOValueTypeEnum.any,
      label: '表单项',
      value: [],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.formInputResult,
      key: NodeOutputKeyEnum.formInputResult,
      label: '表单结果',
      description: '一个包含完整结果的对象',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.object,
      required: true,
    },
  ],
};

/** 问题优化（蓝本 AiQueryExtension）：结合历史把用户问题改写为检索 query */
export const QueryExtensionTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.queryExtension,
  flowNodeType: FlowNodeTypeEnum.queryExtension,
  templateType: FlowNodeTemplateTypeEnum.other,
  name: '问题优化',
  intro: '结合对话历史改写用户问题，提升知识库检索准确性',
  icon: 'FilterOutlined',
  color: '#4f46e5',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  version: '481',
  inputs: [
    {
      key: NodeInputKeyEnum.aiModel,
      renderTypeList: [FlowNodeInputTypeEnum.selectLLMModel, FlowNodeInputTypeEnum.reference],
      label: 'AI 模型',
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
    },
    {
      key: NodeInputKeyEnum.aiSystemPrompt,
      renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
      label: '检索背景描述',
      description: '描述当前对话的业务场景，帮助模型更准确地补全和改写问题',
      valueType: WorkflowIOValueTypeEnum.string,
      max: 300,
    },
    {
      key: NodeInputKeyEnum.queryExtensionMaxQueries,
      renderTypeList: [FlowNodeInputTypeEnum.numberInput],
      label: '最多检索问题数',
      description: '包含原问题；每增加一条会增加一次知识库检索。',
      value: 4,
      min: 1,
      max: 8,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      ...Input_Template_History,
      value: 3,
      description: '最多携带的历史对话轮数；问题优化通常只需最近 3 轮。',
    },
    Input_Template_UserChatInput,
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.text,
      key: NodeOutputKeyEnum.text,
      label: '优化后的问题',
      description:
        '以字符串数组的形式输出，可将该结果直接连接到「知识库搜索」的「用户问题」中，建议不要连接到「AI 对话」的「用户问题」中',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayString,
    },
  ],
};

/** 自定义反馈（蓝本 CustomFeedbackNode）：记录反馈文本，不进入模型上下文 */
export const CustomFeedbackTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.customFeedback,
  flowNodeType: FlowNodeTypeEnum.customFeedback,
  templateType: FlowNodeTemplateTypeEnum.other,
  name: '自定义反馈',
  intro: '运行到本节点时记录一条反馈内容，不进入模型上下文',
  icon: 'CommentOutlined',
  color: '#65a30d',
  showSourceHandle: true,
  showTargetHandle: true,
  courseUrl: '/guide/build/workflow/nodes/custom_feedback',
  inputs: [
    {
      key: NodeInputKeyEnum.textareaInput,
      renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
      label: '反馈文本',
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.text,
      key: NodeOutputKeyEnum.text,
      label: '反馈文本',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** 文档解析（蓝本 ReadFilesNode）：下载文件列表并提取纯文本 */
export const ReadFilesTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.readFiles,
  flowNodeType: FlowNodeTypeEnum.readFiles,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '文档解析',
  intro: '下载文件链接列表并提取为纯文本（支持 txt/md/json/pdf/docx）',
  icon: 'FilterOutlined',
  color: '#0e7490',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  courseUrl: '/guide/build/general/fileInput',
  version: '4.9.2',
  inputs: [
    {
      key: NodeInputKeyEnum.fileUrlList,
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '文件链接列表',
      valueType: WorkflowIOValueTypeEnum.arrayString,
      required: true,
      value: [],
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.text,
      key: NodeOutputKeyEnum.text,
      label: '解析结果',
      description: '全部文件的纯文本内容（带来源标注）',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      id: NodeOutputKeyEnum.rawResponse,
      key: NodeOutputKeyEnum.rawResponse,
      label: '原始响应',
      description: '工具的原始响应，按文件拆分的对象数组',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayObject,
    },
    Output_Template_Error_Message,
  ],
};

/** 工具调用（蓝本 ToolCallNode）：经紫色工具锚点挂载 isTool 节点，函数调用循环 */
export const ToolCallTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.toolCall,
  flowNodeType: FlowNodeTypeEnum.toolCall,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '工具调用',
  intro: '模型自主决定调用挂载的工具节点，循环执行直到产出回答',
  icon: 'ApiOutlined',
  color: '#6f5dd7',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/tool',
  version: '4.9.2',
  inputs: [
    Input_Template_SettingAiModel,
    {
      key: NodeInputKeyEnum.aiChatTemperature,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatMaxToken,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatIsResponseText,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      value: true,
      valueType: WorkflowIOValueTypeEnum.boolean,
    },
    {
      key: NodeInputKeyEnum.aiChatVision,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatAudio,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.aiChatVideo,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: false,
    },
    {
      key: NodeInputKeyEnum.aiChatExtractFiles,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatReasoning,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.boolean,
      value: true,
    },
    {
      key: NodeInputKeyEnum.aiChatReasoningEffort,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatTopP,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: NodeInputKeyEnum.aiChatStopSign,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatResponseFormat,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      key: NodeInputKeyEnum.aiChatJsonSchema,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Input_Template_System_Prompt,
    Input_Template_History,
    Input_Template_File_Link,
    Input_Template_UserChatInput,
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.answerText,
      key: NodeOutputKeyEnum.answerText,
      label: 'AI 回复内容',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Output_Template_Error_Message,
  ],
};

/** 停止工具循环（蓝本 StopToolNode）：作为工具节点的普通下游，该工具执行完立即终止循环 */
export const StopToolTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.stopTool,
  flowNodeType: FlowNodeTypeEnum.stopTool,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '停止工具循环',
  intro: '将某个工具节点连向本节点：该工具执行完立即结束循环，以工具结果作答',
  icon: 'ApiOutlined',
  color: '#dc2626',
  showSourceHandle: false,
  showTargetHandle: true,
  inputs: [],
  outputs: [],
};

/** 工具参数覆盖（蓝本 ToolParamsNode）：动态参数作为全部工具调用的预置入参 */
export const ToolParamsTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.toolParams,
  flowNodeType: FlowNodeTypeEnum.toolParams,
  templateType: FlowNodeTemplateTypeEnum.ai,
  name: '工具参数',
  intro: '为挂载的工具补充/覆盖固定入参（模型不感知）',
  icon: 'ApiOutlined',
  color: '#6f5dd7',
  showSourceHandle: false,
  showTargetHandle: false,
  isTool: true,
  inputs: [
    {
      key: NodeInputKeyEnum.addInputParam,
      renderTypeList: [FlowNodeInputTypeEnum.addInputParam],
      valueType: WorkflowIOValueTypeEnum.dynamic,
      label: '自定义参数',
      required: false,
    },
  ],
  outputs: [],
};

/** 蓝本 sandbox/constants.ts 双语言默认模板（SANDBOX_CODE_TEMPLATE） */
export const JS_CODE_TEMPLATE =
  'function main({data1, data2}){\n\n    return {\n        result: data1,\n        data2\n    }\n}\n';
export const PY_CODE_TEMPLATE =
  'def main(data1, data2):\n\n    return {\n        "result": data1,\n        "data2": data2\n    }\n';

/** 代码运行（蓝本 CodeNode）：JS/Python 双语言受限子进程（D-15：进程级沙箱而非独立沙箱服务） */
export const CodeTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.code,
  flowNodeType: FlowNodeTypeEnum.code,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '代码运行',
  intro: '在沙盒里执行一段脚本代码，可用于进行复杂的数据处理，语法和可用依赖会受到限制。',
  icon: 'ApiOutlined',
  color: '#334155',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/sandbox-v2',
  inputs: [
    {
      key: NodeInputKeyEnum.addInputParam,
      renderTypeList: [FlowNodeInputTypeEnum.addInputParam],
      valueType: WorkflowIOValueTypeEnum.dynamic,
      label: '自定义输入',
      description: '这些变量会作为代码的运行的输入参数',
      required: false,
      customInputConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: true,
      },
    },
    {
      key: 'data1',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      valueType: WorkflowIOValueTypeEnum.string,
      canEdit: true,
      label: 'data1',
      required: true,
      customInputConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: true,
      },
    },
    {
      key: 'data2',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      valueType: WorkflowIOValueTypeEnum.string,
      canEdit: true,
      label: 'data2',
      required: true,
      customInputConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: true,
      },
    },
    {
      key: NodeInputKeyEnum.codeType,
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      label: '',
      valueType: WorkflowIOValueTypeEnum.string,
      value: 'js',
    },
    {
      key: 'code',
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
      label: '',
      value: JS_CODE_TEMPLATE,
    },
  ],
  outputs: [
    {
      id: NodeOutputKeyEnum.addOutputParam,
      key: NodeOutputKeyEnum.addOutputParam,
      type: FlowNodeOutputTypeEnum.dynamic,
      valueType: WorkflowIOValueTypeEnum.dynamic,
      label: '',
      description: '将代码中 return 的对象作为输出，传递给后续的节点。变量名需要对应 return 的 key',
      customFieldConfig: {
        selectValueTypeList: Object.values(WorkflowIOValueTypeEnum),
        showDescription: false,
        showDefaultValue: false,
      },
    },
    {
      id: 'system_rawResponse',
      key: 'system_rawResponse',
      label: '完整响应数据',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.object,
    },
    {
      id: 'result',
      key: 'result',
      label: 'result',
      type: FlowNodeOutputTypeEnum.dynamic,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      id: 'data2',
      key: 'data2',
      label: 'data2',
      type: FlowNodeOutputTypeEnum.dynamic,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    {
      id: NodeOutputKeyEnum.error,
      key: NodeOutputKeyEnum.error,
      label: '错误信息',
      type: FlowNodeOutputTypeEnum.error,
      valueType: WorkflowIOValueTypeEnum.string,
    },
  ],
};

/** 循环容器（蓝本 LoopRunNode，array 模式）：容器体以 parentNodeId 圈定 */
export const LoopRunTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.loopRun,
  flowNodeType: FlowNodeTypeEnum.loopRun,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '循环节点',
  intro: '对数组逐项执行容器体；容器内可用「跳出循环」终止',
  icon: 'SwapOutlined',
  color: '#9333ea',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  catchError: false,
  courseUrl: '/guide/build/workflow/nodes/loop_run',
  inputs: [
    {
      key: 'loopRunMode',
      renderTypeList: [FlowNodeInputTypeEnum.select],
      valueType: WorkflowIOValueTypeEnum.string,
      required: true,
      label: '循环模式',
      description: '数组循环：按数组逐项执行；条件循环：反复执行直到容器体内命中「跳出循环」',
      list: [
        { label: '数组循环', value: 'array' },
        { label: '条件循环', value: 'conditional' },
      ],
      value: 'array',
    },
    {
      key: 'loopRunInputArray',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '数组',
      description: '仅数组循环需要；条件循环不使用数组输入',
      valueType: WorkflowIOValueTypeEnum.arrayAny,
      required: false,
      value: [],
    },
    {
      key: 'loopCustomOutputs',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '聚合输出',
      description: '引用容器体内某节点输出，每轮取值聚合为结果数组',
      valueType: WorkflowIOValueTypeEnum.any,
    },
  ],
  outputs: [
    {
      id: 'loopArray',
      key: 'loopArray',
      label: '循环结果数组',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayAny,
    },
    Output_Template_Error_Message,
  ],
};

/** 循环体起点（蓝本 LoopRunStartNode）：容器内固定节点 */
export const LoopRunStartTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.loopRunStart,
  flowNodeType: FlowNodeTypeEnum.loopRunStart,
  templateType: FlowNodeTemplateTypeEnum.systemInput,
  name: '循环体开始',
  intro: '',
  icon: 'PlayCircleOutlined',
  color: '#9333ea',
  showSourceHandle: true,
  showTargetHandle: false,
  showStatus: false,
  unique: true,
  forbidDelete: true,
  // 蓝本 loopRunStart 三个 hidden 输入（序列化 schema 对齐；运行值由容器预注入）
  inputs: [
    {
      key: 'loopRunMode',
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      valueType: WorkflowIOValueTypeEnum.string,
      label: '',
      value: 'array',
    },
    {
      key: 'loopStartInput',
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      valueType: WorkflowIOValueTypeEnum.any,
      label: '',
      value: '',
    },
    {
      key: 'loopStartIndex',
      renderTypeList: [FlowNodeInputTypeEnum.hidden],
      valueType: WorkflowIOValueTypeEnum.number,
      label: '',
    },
  ],
  outputs: [
    {
      id: 'currentIndex',
      key: 'currentIndex',
      label: '当前下标',
      description: '数组模式下当前元素的 0-based 下标。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      id: 'currentItem',
      key: 'currentItem',
      label: '当前元素',
      description: '数组模式下当前迭代处理的元素。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.any,
    },
    {
      id: 'currentIteration',
      key: 'currentIteration',
      label: '当前循环次数',
      description: '条件循环模式下的 1-based 迭代次数。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.number,
    },
  ],
};

/** 跳出循环（蓝本 LoopRunBreakNode）：仅容器体内可用 */
export const LoopRunBreakTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.loopRunBreak,
  flowNodeType: FlowNodeTypeEnum.loopRunBreak,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '跳出循环',
  intro: '本轮容器体结束后终止循环',
  icon: 'SwapOutlined',
  color: '#dc2626',
  showSourceHandle: false,
  showTargetHandle: true,
  showStatus: false,
  inputs: [],
  outputs: [],
};

/** 并行容器（蓝本 ParallelRunNode） */
export const ParallelRunTemplate: FlowNodeTemplateType = {
  id: FlowNodeTypeEnum.parallelRun,
  flowNodeType: FlowNodeTypeEnum.parallelRun,
  templateType: FlowNodeTemplateTypeEnum.tools,
  name: '并行执行',
  intro: '输入一个数组，并行执行工作流中的每一个数组元素，最后将结果汇总输出。',
  icon: 'SwapOutlined',
  color: '#9333ea',
  showSourceHandle: true,
  showTargetHandle: true,
  showStatus: true,
  courseUrl: '/guide/build/workflow/nodes/parallel_run',
  inputs: [
    {
      key: 'loopInputArray',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '数组',
      valueType: WorkflowIOValueTypeEnum.arrayAny,
      required: true,
      value: [],
    },
    {
      key: 'parallelRunMaxConcurrency',
      renderTypeList: [FlowNodeInputTypeEnum.numberInput],
      label: '最大并发数',
      description: '同时并行执行的最大任务数，范围 1～上限值（默认 5）。',
      required: true,
      value: 5,
      min: 1,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: 'parallelRunMaxRetryTimes',
      renderTypeList: [FlowNodeInputTypeEnum.numberInput],
      label: '单轮报错重试次数',
      description: '单个任务失败后的最大重试次数，范围 0～5（默认 3）。设为 0 表示失败不重试。',
      required: true,
      value: 3,
      min: 0,
      max: 5,
      valueType: WorkflowIOValueTypeEnum.number,
    },
    {
      key: 'loopCustomOutputs',
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      label: '聚合输出',
      description: '引用容器体内某节点输出，每项取值聚合为结果数组',
      valueType: WorkflowIOValueTypeEnum.any,
    },
  ],
  outputs: [
    {
      id: 'parallelSuccessResults',
      key: 'parallelSuccessResults',
      label: '成功结果',
      description: '所有执行成功的任务输出，按输入顺序排列（仅包含成功项）。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayAny,
    },
    {
      id: 'parallelFullResults',
      key: 'parallelFullResults',
      label: '完整结果',
      description:
        '与输入数组等长的结果数组，每项形如 {success, message, data}：成功时 success=true、message 为空、data 为输出值；失败时 success=false、message 为错误信息、data 为 null。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.arrayObject,
    },
    {
      id: 'parallelStatus',
      key: 'parallelStatus',
      label: '完成状态',
      description: '整体执行状态：success（全部成功）、partial_success（部分失败）、failed（全部失败）。',
      type: FlowNodeOutputTypeEnum.static,
      valueType: WorkflowIOValueTypeEnum.string,
    },
    Output_Template_Error_Message,
  ],
};

/** 节点添加菜单展示的模板（顺序即菜单顺序），流程开始不在菜单中 */
export const nodeTemplateList: FlowNodeTemplateType[] = [
  AiChatTemplate,
  ClassifyQuestionTemplate,
  ContentExtractTemplate,
  DatasetSearchTemplate,
  DatasetConcatTemplate,
  AssignedAnswerTemplate,
  UserSelectTemplate,
  FormInputTemplate,
  IfElseTemplate,
  HttpRequestTemplate,
  TextEditorTemplate,
  VariableUpdateTemplate,
  QueryExtensionTemplate,
  ReadFilesTemplate,
  ToolCallTemplate,
  StopToolTemplate,
  ToolParamsTemplate,
  CodeTemplate,
  LoopRunTemplate,
  LoopRunBreakTemplate,
  ParallelRunTemplate,
];

export const allNodeTemplates: FlowNodeTemplateType[] = [
  SystemConfigTemplate,
  WorkflowStartTemplate,
  LoopRunStartTemplate,
  CustomFeedbackTemplate,
  ...nodeTemplateList,
];

export const getTemplateByFlowNodeType = (flowNodeType: string) =>
  allNodeTemplates.find((item) => item.flowNodeType === flowNodeType);
