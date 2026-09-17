import {
  WORKFLOW_PERSIST_VERSION,
  compileGraphToLegacy,
  createDefaultGraph,
  parsePersistedGraph,
  serializeGraph,
} from './compiler';
import {
  FlowNodeInputTypeEnum,
  FlowNodeTypeEnum,
  IfElseResultEnum,
  NodeInputKeyEnum,
  getHandleId,
} from './constants';
import { AiChatTemplate, IfElseTemplate, DatasetSearchTemplate, LoopRunTemplate } from './templates';
import {
  applyDefaultReferences,
  buildEdge,
  computeUniqueNodeName,
  createNodeFromTemplate,
  getNodeInputValue,
  syncUserFilesOutput,
} from './utils';

describe('workflow compiler', () => {
  it('creates AI chat with direct dataset selection and skill picker', () => {
    const input = AiChatTemplate.inputs.find((item) => item.key === NodeInputKeyEnum.aiChatDatasets);
    const quoteInput = AiChatTemplate.inputs.find((item) => item.key === NodeInputKeyEnum.aiChatDatasetQuote);
    const skillInput = AiChatTemplate.inputs.find((item) => item.key === NodeInputKeyEnum.skills);
    const imageInput = AiChatTemplate.inputs.find(
      (item) => item.key === NodeInputKeyEnum.aiChatKnowledgeImages
    );
    const keys = AiChatTemplate.inputs.map((item) => item.key);

    expect(input).toMatchObject({ renderTypeList: [FlowNodeInputTypeEnum.selectDataset] });
    expect(quoteInput).toMatchObject({
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      valueType: 'datasetQuote',
    });
    expect(skillInput).toMatchObject({
      renderTypeList: [FlowNodeInputTypeEnum.custom],
      value: [],
    });
    expect(imageInput).toMatchObject({
      renderTypeList: [FlowNodeInputTypeEnum.select],
      value: 'smart',
    });
    expect(keys).not.toContain('aiChatQuoteRole');
    expect(keys).not.toContain('quoteTemplate');
    expect(keys).not.toContain('quotePrompt');
  });

  it('keeps AI chat input keys unique', () => {
    const keys = AiChatTemplate.inputs.map((item) => item.key);

    expect(new Set(keys).size).toBe(keys.length);
  });

  it('adds smart knowledge-image selection to existing AI chat nodes', () => {
    const graph = createDefaultGraph();
    const chat = createNodeFromTemplate(AiChatTemplate, { x: 0, y: 0 });
    chat.inputs = chat.inputs.filter((item) => item.key !== NodeInputKeyEnum.aiChatKnowledgeImages);
    graph.nodes.push(chat);

    const parsed = parsePersistedGraph(JSON.stringify({
      version: WORKFLOW_PERSIST_VERSION,
      fastgpt: graph,
    }));
    const migratedChat = parsed.graph.nodes.find((node) => node.nodeId === chat.nodeId);

    expect(getNodeInputValue(migratedChat, NodeInputKeyEnum.aiChatKnowledgeImages)).toBe('smart');
  });

  it('requires AI chat model selection', () => {
    const modelInput = AiChatTemplate.inputs.find((item) => item.key === NodeInputKeyEnum.aiModel);

    expect(modelInput).toMatchObject({
      renderTypeList: [FlowNodeInputTypeEnum.settingLLMModel, FlowNodeInputTypeEnum.reference],
      required: true,
    });
  });

  it('does not require array input for conditional loop mode', () => {
    const input = LoopRunTemplate.inputs.find((item) => item.key === 'loopRunInputArray');

    expect(input).toMatchObject({
      required: false,
      description: expect.stringContaining('条件循环不使用数组输入'),
    });
  });

  it('creates default graph with fixed systemConfig + workflowStart only', () => {
    const graph = createDefaultGraph();
    expect(graph.nodes.map((node) => node.flowNodeType)).toEqual([
      FlowNodeTypeEnum.systemConfig,
      FlowNodeTypeEnum.workflowStart,
    ]);
    expect(graph.nodes[0].position).toEqual({ x: -240, y: 16 });
    expect(graph.nodes[1].position).toEqual({ x: 376, y: 352 });
    expect(graph.edges).toEqual([]);
  });

  it('compiles default graph to legacy runtime format (systemConfig skipped, no warnings)', () => {
    const graph = createDefaultGraph();
    const { nodes, edges, warnings } = compileGraphToLegacy(graph);

    expect(nodes).toHaveLength(1);
    expect(nodes[0].data.type).toBe('start');
    expect(edges).toHaveLength(0);
    expect(warnings).toHaveLength(0);
  });

  it('maps ifElse branches to legacy condition true/false handles', () => {
    const graph = createDefaultGraph();
    const ifElse = createNodeFromTemplate(IfElseTemplate, { x: 0, y: 0 });
    const chat = createNodeFromTemplate(AiChatTemplate, { x: 320, y: 0 });
    graph.nodes.push(ifElse, chat);
    graph.edges.push(buildEdge(ifElse.nodeId, chat.nodeId, IfElseResultEnum.IF));

    const { nodes, edges } = compileGraphToLegacy(graph);
    const conditionNode = nodes.find((node) => node.data.type === 'condition');
    expect(conditionNode).toBeTruthy();
    const branchEdge = edges.find((edge) => edge.source === ifElse.nodeId);
    expect(branchEdge?.sourceHandle).toBe('true');
  });

  it('round-trips fastgpt graph through persisted json (fastgpt-only envelope)', () => {
    const graph = createDefaultGraph();
    const json = serializeGraph(graph);
    const parsed = JSON.parse(json);

    expect(parsed.version).toBe(WORKFLOW_PERSIST_VERSION);
    // 2.1.0 起不再写入旧 v0.1 Java 编译结果与 compiler.unsupported
    expect(parsed.nodes).toBeUndefined();
    expect(parsed.edges).toBeUndefined();
    expect(parsed.compiler).toBeUndefined();
    expect(parsed.fastgpt.nodes).toHaveLength(2);

    const restored = parsePersistedGraph(json);
    expect(restored.migrated).toBe(false);
    expect(restored.graph.nodes.map((node) => node.flowNodeType)).toEqual([
      FlowNodeTypeEnum.systemConfig,
      FlowNodeTypeEnum.workflowStart,
    ]);
  });

  it('strips runtime-only fields (debugResult/isFolded/isError) from persisted nodes', () => {
    const graph = createDefaultGraph();
    const chat = createNodeFromTemplate(AiChatTemplate, { x: 320, y: 0 });
    graph.nodes.push(chat);
    chat.isFolded = true;
    chat.isError = true;
    chat.debugResult = { status: 'success', response: 'x' };

    const parsed = JSON.parse(serializeGraph(graph));
    const persistedChat = parsed.fastgpt.nodes.find(
      (node: any) => node.flowNodeType === FlowNodeTypeEnum.chatNode
    );
    expect(persistedChat.isFolded).toBeUndefined();
    expect(persistedChat.isError).toBeUndefined();
    expect(persistedChat.debugResult).toBeUndefined();
    // 序列化不改动内存中的画布模型
    expect(chat.isFolded).toBe(true);
  });

  it('backfills missing systemConfig for old fastgpt drafts', () => {
    const graph = createDefaultGraph();
    graph.nodes = graph.nodes.filter((node) => node.flowNodeType !== FlowNodeTypeEnum.systemConfig);
    const legacyEnvelope = JSON.stringify({ version: '2.0.0', fastgpt: graph });

    const { graph: restored, migrated } = parsePersistedGraph(legacyEnvelope);
    expect(migrated).toBe(false);
    expect(restored.nodes.filter((node) => node.flowNodeType === FlowNodeTypeEnum.systemConfig)).toHaveLength(1);
  });

  it('backfills direct AI chat dataset selection for persisted chat nodes', () => {
    const persisted = JSON.stringify({
      version: '2.0.0',
      fastgpt: {
        nodes: [
          {
            nodeId: 'chat1',
            flowNodeType: FlowNodeTypeEnum.chatNode,
            name: 'AI 对话',
            position: { x: 0, y: 0 },
            inputs: [{ key: NodeInputKeyEnum.aiChatDatasetQuote, value: ['search1', 'quoteQA'] }],
            outputs: [],
          },
        ],
        edges: [],
        chatConfig: { welcomeText: '', variables: [] },
      },
    });

    const { graph } = parsePersistedGraph(persisted);
    const chat = graph.nodes.find((node) => node.nodeId === 'chat1')!;

    expect(getNodeInputValue(chat, NodeInputKeyEnum.aiChatDatasets)).toEqual([]);
    expect(getNodeInputValue(chat, NodeInputKeyEnum.skills)).toEqual([]);
    expect(getNodeInputValue(chat, NodeInputKeyEnum.aiChatDatasetQuote)).toEqual(['search1', 'quoteQA']);
    expect(chat.inputs.find((input) => input.key === NodeInputKeyEnum.aiChatDatasetQuote)).toMatchObject({
      renderTypeList: [FlowNodeInputTypeEnum.reference],
      deprecated: false,
    });
  });

  it('migrates persisted SQL readonly query nodes to the SQL executor contract', () => {
    const persisted = JSON.stringify({
      version: '2.0.0',
      fastgpt: {
        nodes: [
          {
            nodeId: 'sql1',
            flowNodeType: FlowNodeTypeEnum.tool,
            name: 'SQL 只读查询',
            position: { x: 0, y: 0 },
            toolConfig: { systemTool: { toolId: 'builtin.sql_query' } },
            inputs: [
              { key: 'sql', label: 'SQL 语句', renderTypeList: [FlowNodeInputTypeEnum.textarea] },
              { key: 'db_uri', label: 'DB URI', renderTypeList: [FlowNodeInputTypeEnum.password] },
              { key: 'limit_rows', label: '最大返回行数', renderTypeList: [FlowNodeInputTypeEnum.numberInput] },
            ],
            outputs: [],
          },
        ],
        edges: [],
        chatConfig: { welcomeText: '', variables: [] },
      },
    });

    const { graph } = parsePersistedGraph(persisted);
    const sqlNode = graph.nodes.find((node) => node.nodeId === 'sql1')!;

    expect(sqlNode.name).toBe('SQL 执行');
    expect(sqlNode.inputs.map((input) => input.key)).toEqual(['sql', 'db_uri']);
  });

  it('backfills model selector for persisted Text to SQL nodes', () => {
    const persisted = JSON.stringify({
      version: '2.0.0',
      fastgpt: {
        nodes: [
          {
            nodeId: 'textsql1',
            flowNodeType: FlowNodeTypeEnum.tool,
            name: 'Text to SQL',
            position: { x: 0, y: 0 },
            toolConfig: { systemTool: { toolId: 'builtin.text_to_sql' } },
            inputs: [
              { key: 'query_text', label: '自然语言查询', renderTypeList: [FlowNodeInputTypeEnum.textarea] },
              { key: 'database_schema', label: '库表定义', renderTypeList: [FlowNodeInputTypeEnum.textarea] },
              { key: 'limit_rows', label: '返回条数', renderTypeList: [FlowNodeInputTypeEnum.numberInput] },
            ],
            outputs: [],
          },
        ],
        edges: [],
        chatConfig: { welcomeText: '', variables: [] },
      },
    });

    const { graph } = parsePersistedGraph(persisted);
    const textSqlNode = graph.nodes.find((node) => node.nodeId === 'textsql1')!;
    const model = textSqlNode.inputs.find((input) => input.key === NodeInputKeyEnum.aiModel);

    expect(textSqlNode.inputs.map((input) => input.key)).toEqual([
      NodeInputKeyEnum.aiModel,
      'query_text',
      'database_schema',
      'limit_rows',
    ]);
    expect(model).toMatchObject({
      label: '生成模型',
      renderTypeList: [FlowNodeInputTypeEnum.selectLLMModel],
      value: 'default',
      required: false,
    });
  });

  it('migrates legacy v0.1 draft into canvas graph', () => {
    const legacy = JSON.stringify({
      version: '1.0.0',
      nodes: [
        { id: 'start', type: 'workflowNode', position: { x: 0, y: 0 }, data: { type: 'start', config: {} } },
        {
          id: 'llm',
          type: 'workflowNode',
          position: { x: 300, y: 0 },
          data: { type: 'llm', label: '回答模型', config: { model: 'gpt-4o', prompt: 'hi', temperature: 0.3 } },
        },
        {
          id: 'kb',
          type: 'workflowNode',
          position: { x: 300, y: 200 },
          data: { type: 'knowledgeSearch', config: { knowledgeIds: ['k1'], scoreThreshold: 0.5 } },
        },
      ],
      edges: [{ id: 'start-llm', source: 'start', target: 'llm' }],
    });

    const { graph, migrated, dropped } = parsePersistedGraph(legacy);
    expect(migrated).toBe(true);
    expect(dropped).toHaveLength(0);

    // 迁移后补齐固定 systemConfig 节点
    expect(graph.nodes.filter((node) => node.flowNodeType === FlowNodeTypeEnum.systemConfig)).toHaveLength(1);

    const chatNode = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.chatNode);
    expect(chatNode?.name).toBe('回答模型');
    expect(getNodeInputValue(chatNode, NodeInputKeyEnum.aiModel)).toBe('gpt-4o');
    expect(getNodeInputValue(chatNode, NodeInputKeyEnum.aiChatTemperature)).toBe(0.3);

    const datasetNode = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.datasetSearchNode);
    expect(getNodeInputValue(datasetNode, NodeInputKeyEnum.datasetSelectList)).toEqual([{ datasetId: 'k1' }]);

    expect(graph.edges[0].sourceHandle).toBe(getHandleId('start', 'source', 'right'));
  });

  it('keeps dataset select value shape aligned with template default', () => {
    const node = createNodeFromTemplate(DatasetSearchTemplate, { x: 0, y: 0 });
    expect(getNodeInputValue(node, NodeInputKeyEnum.datasetSelectList)).toEqual([]);
  });

  it('preserves dynamic-node metadata when instantiating from a preview-node template', () => {
    // 动态节点（tool/toolSet/pluginModule/appModule）的 preview node 携带来源与配置，
    // 落图丢失即无法保存重载/发布/运行（gap-audit §7.3.5）
    const previewTemplate = {
      ...DatasetSearchTemplate,
      pluginId: 'plugin-abc',
      source: 'systemTool',
      version: '1.2.0',
      versionLabel: 'v1.2.0',
      isLatestVersion: true,
      toolDescription: 'search tool',
      showStatus: true,
      pluginData: { name: '搜索工具', avatar: '/a.png' },
      toolConfig: { systemTool: { toolId: 'plugin-abc' } },
    };
    const node = createNodeFromTemplate(previewTemplate, { x: 0, y: 0 });
    expect(node.pluginId).toBe('plugin-abc');
    expect(node.source).toBe('systemTool');
    expect(node.version).toBe('1.2.0');
    expect(node.versionLabel).toBe('v1.2.0');
    expect(node.isLatestVersion).toBe(true);
    expect(node.toolDescription).toBe('search tool');
    expect(node.showStatus).toBe(true);
    expect(node.pluginData).toEqual({ name: '搜索工具', avatar: '/a.png' });
    expect(node.toolConfig).toEqual({ systemTool: { toolId: 'plugin-abc' } });
    // 深拷贝：修改实例不污染模板
    node.toolConfig!.systemTool!.toolId = 'changed';
    expect(previewTemplate.toolConfig.systemTool.toolId).toBe('plugin-abc');
    // 序列化往返不丢
    const graph = createDefaultGraph();
    graph.nodes.push(node);
    const restored = parsePersistedGraph(serializeGraph(graph)).graph;
    const roundTripped = restored.nodes.find((item) => item.nodeId === node.nodeId);
    expect(roundTripped?.pluginId).toBe('plugin-abc');
    expect(roundTripped?.toolConfig).toEqual({ systemTool: { toolId: 'changed' } });
  });
});

describe('node instantiation helpers (蓝本 §7.3.5)', () => {
  it('applies default references from workflowStart and honors defaultValue precedence', () => {
    const graph = createDefaultGraph();
    const start = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.workflowStart)!;
    const node = createNodeFromTemplate(
      {
        ...DatasetSearchTemplate,
        inputs: [
          { key: 'question', label: '输入问题', renderTypeList: [], valueType: undefined } as any,
          {
            key: 'query_text',
            label: '自然语言查询',
            renderTypeList: [FlowNodeInputTypeEnum.textarea, FlowNodeInputTypeEnum.reference],
            valueType: undefined,
          } as any,
          { key: 'similarity', label: '相关度', renderTypeList: [], defaultValue: 0.66 } as any,
          { key: 'preset', label: '已有值', renderTypeList: [], value: 'keep-me' } as any,
        ],
        outputs: [],
      },
      { x: 0, y: 0 }
    );
    applyDefaultReferences(node, graph);
    expect(getNodeInputValue(node, 'question')).toEqual([start.nodeId, 'userChatInput']);
    expect(getNodeInputValue(node, 'query_text')).toEqual([start.nodeId, 'userChatInput']);
    expect(node.inputs.find((item) => item.key === 'query_text')?.selectedTypeIndex).toBe(1);
    expect(getNodeInputValue(node, 'similarity')).toBe(0.66);
    expect(getNodeInputValue(node, 'preset')).toBe('keep-me');
  });

  it('computes canvas-unique node names with numeric suffix', () => {
    const graph = createDefaultGraph();
    graph.nodes.push(createNodeFromTemplate(AiChatTemplate, { x: 0, y: 0 }));
    expect(computeUniqueNodeName('AI 对话', graph)).toBe('AI 对话 2');
    expect(computeUniqueNodeName('全新节点', graph)).toBe('全新节点');
  });

  it('filters deprecated IO at instantiation', () => {
    const node = createNodeFromTemplate(
      {
        ...DatasetSearchTemplate,
        inputs: [
          { key: 'alive', label: 'alive', renderTypeList: [] } as any,
          { key: 'dead', label: 'dead', renderTypeList: [], deprecated: true } as any,
        ],
        outputs: [{ id: 'gone', key: 'gone', label: 'gone', type: 'static', deprecated: true } as any],
      },
      { x: 0, y: 0 }
    );
    expect(node.inputs.map((input) => input.key)).toEqual(['alive']);
    expect(node.outputs).toHaveLength(0);
  });
});

describe('syncUserFilesOutput', () => {
  it('adds file URL and private file-ID outputs when upload is enabled, then clears references when disabled', () => {
    const graph = createDefaultGraph();
    const start = graph.nodes.find((node) => node.flowNodeType === FlowNodeTypeEnum.workflowStart)!;
    const chat = createNodeFromTemplate(AiChatTemplate, { x: 0, y: 0 });
    graph.nodes.push(chat);
    expect(start.outputs.some((output) => output.key === 'userFiles')).toBe(false);

    graph.chatConfig.fileSelectConfig = { canSelectFile: true, canSelectImg: false, maxFiles: 5 };
    syncUserFilesOutput(graph);
    expect(start.outputs.some((output) => output.key === 'userFiles')).toBe(true);
    expect(start.outputs.some((output) => output.key === 'userFileIds')).toBe(true);

    // 下游引用 userFiles 后关闭开关：输出移除且引用被清理
    const fileInput = chat.inputs.find((input) => input.key === NodeInputKeyEnum.fileUrlList)!;
    fileInput.value = [start.nodeId, 'userFiles'];
    graph.chatConfig.fileSelectConfig = { canSelectFile: false, canSelectImg: false, maxFiles: 5 };
    syncUserFilesOutput(graph);
    expect(start.outputs.some((output) => output.key === 'userFiles')).toBe(false);
    expect(start.outputs.some((output) => output.key === 'userFileIds')).toBe(false);
    expect(fileInput.value).toBeUndefined();
  });
});

describe('migrateProtocolKeys (2.1.0 前存量画布协议键迁移)', () => {
  it('renames legacy error output key and rewrites references; code/http keep blueprint "error"', () => {
    const persisted = JSON.stringify({
      version: '2.1.0',
      fastgpt: {
        nodes: [
          {
            nodeId: 'chat1',
            flowNodeType: FlowNodeTypeEnum.chatNode,
            name: 'AI 对话',
            position: { x: 0, y: 0 },
            inputs: [],
            outputs: [{ id: 'error', key: 'error', label: '错误信息', type: 'error', valueType: 'string' }],
          },
          {
            nodeId: 'code1',
            flowNodeType: FlowNodeTypeEnum.code,
            name: '代码运行',
            position: { x: 0, y: 0 },
            inputs: [],
            outputs: [{ id: 'error', key: 'error', label: '错误信息', type: 'error', valueType: 'string' }],
          },
          {
            nodeId: 'answer1',
            flowNodeType: FlowNodeTypeEnum.answerNode,
            name: '指定回复',
            position: { x: 0, y: 0 },
            inputs: [{ key: NodeInputKeyEnum.answerText, value: ['chat1', 'error'] }],
            outputs: [],
          },
        ],
        edges: [],
        chatConfig: { welcomeText: '', variables: [] },
      },
    });
    const { graph } = parsePersistedGraph(persisted);
    const chat = graph.nodes.find((node) => node.nodeId === 'chat1')!;
    const code = graph.nodes.find((node) => node.nodeId === 'code1')!;
    const answer = graph.nodes.find((node) => node.nodeId === 'answer1')!;
    expect(chat.outputs[0].key).toBe('system_error_text');
    expect(code.outputs[0].key).toBe('error'); // 蓝本 code 专用键不迁移
    expect(answer.inputs[0].value).toEqual(['chat1', 'system_error_text']);
  });

  it('renames loopRunStart outputs / parallelRun input key and rewires legacy stopTool tool-edge', () => {
    const persisted = JSON.stringify({
      version: '2.1.0',
      fastgpt: {
        nodes: [
          {
            nodeId: 'ls1',
            flowNodeType: FlowNodeTypeEnum.loopRunStart,
            name: '循环体开始',
            parentNodeId: 'loop1',
            position: { x: 0, y: 0 },
            inputs: [],
            outputs: [
              { id: 'loopStartInput', key: 'loopStartInput', label: '当前元素', type: 'static' },
              { id: 'loopStartIndex', key: 'loopStartIndex', label: '当前序号', type: 'static' },
            ],
          },
          {
            nodeId: 'text1',
            flowNodeType: FlowNodeTypeEnum.textEditor,
            name: '文本拼接',
            parentNodeId: 'loop1',
            position: { x: 0, y: 0 },
            inputs: [{ key: NodeInputKeyEnum.textareaInput, value: ['ls1', 'loopStartInput'] }],
            outputs: [],
          },
          {
            nodeId: 'par1',
            flowNodeType: FlowNodeTypeEnum.parallelRun,
            name: '并行执行',
            position: { x: 0, y: 0 },
            inputs: [{ key: 'loopRunInputArray', value: [] }],
            outputs: [],
          },
          {
            nodeId: 'tool1',
            flowNodeType: FlowNodeTypeEnum.httpRequest468,
            name: 'HTTP 请求',
            position: { x: 0, y: 0 },
            inputs: [],
            outputs: [],
          },
          {
            nodeId: 'stop1',
            flowNodeType: FlowNodeTypeEnum.stopTool,
            name: '停止工具循环',
            position: { x: 0, y: 0 },
            inputs: [],
            outputs: [],
          },
        ],
        edges: [{ source: 'tool1', sourceHandle: 'selectedTools', target: 'stop1', targetHandle: 'selectedTools' }],
        chatConfig: { welcomeText: '', variables: [] },
      },
    });
    const { graph } = parsePersistedGraph(persisted);
    const loopStart = graph.nodes.find((node) => node.nodeId === 'ls1')!;
    expect(loopStart.outputs.map((output) => output.key)).toEqual(['currentItem', 'currentIndex']);
    const text = graph.nodes.find((node) => node.nodeId === 'text1')!;
    expect(text.inputs[0].value).toEqual(['ls1', 'currentItem']);
    const parallel = graph.nodes.find((node) => node.nodeId === 'par1')!;
    expect(parallel.inputs[0].key).toBe('loopInputArray');
    const stopEdge = graph.edges.find((edge) => edge.target === 'stop1')!;
    expect(stopEdge.sourceHandle).toBe(getHandleId('tool1', 'source', 'right'));
    expect(stopEdge.targetHandle).toBe(getHandleId('stop1', 'target', 'left'));
  });
});
