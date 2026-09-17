import { agentFormToGraph, createDefaultAgentForm, graphToAgentForm } from './form';
import { parsePersistedGraph, serializeGraph } from '../core/compiler';

describe('agent form graph conversion', () => {
  it('persists file upload and grouped recommendation scene settings', () => {
    const form = createDefaultAgentForm();
    form.model = 'glm-5.1';
    form.extractFiles = true;
    form.presentationPreset = 'campus-welcome-v1';
    form.recommendationScenes = [
      { key: 'start', label: '开始', textList: ['介绍一下你的能力'] },
      { key: 'document', label: '文档', textList: ['帮我总结知识库'] },
    ];
    form.variables = [
      {
        id: 'var_customer',
        key: 'customer_name',
        label: '客户姓名',
        type: 'input' as any,
        required: true,
        defaultValue: '张三',
      },
    ];

    const graph = agentFormToGraph(form);

    expect(graph.chatConfig.fileSelectConfig).toEqual({
      canSelectFile: true,
      canSelectImg: true,
      maxFiles: 10,
    });
    expect(graph.chatConfig.chatInputGuide).toEqual({
      open: true,
      textList: ['介绍一下你的能力', '帮我总结知识库'],
      sceneList: form.recommendationScenes,
    });
    expect(graph.chatConfig.variables).toEqual(form.variables);
    expect(graph.chatConfig.presentation).toEqual({
      schemaVersion: 1,
      preset: 'campus-welcome-v1',
    });
    expect(parsePersistedGraph(serializeGraph(graph)).graph.chatConfig.presentation).toEqual({
      schemaVersion: 1,
      preset: 'campus-welcome-v1',
    });
    expect(graphToAgentForm(graph)?.recommendationScenes).toEqual(form.recommendationScenes);
    expect(graphToAgentForm(graph)?.extractFiles).toBe(true);
    expect(graphToAgentForm(graph)?.presentationPreset).toBe('campus-welcome-v1');
    expect(graphToAgentForm(graph)?.variables).toEqual(form.variables);
  });

  it('upgrades legacy flat recommendations into one default scene', () => {
    const graph = agentFormToGraph(createDefaultAgentForm());
    graph.chatConfig.chatInputGuide = { open: true, textList: ['旧推荐一', '旧推荐二'] };

    expect(graphToAgentForm(graph)?.recommendationScenes).toEqual([
      { key: 'recommended', label: '推荐', textList: ['旧推荐一', '旧推荐二'] },
    ]);
  });
});
