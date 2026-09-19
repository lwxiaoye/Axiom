import {
  APP_OPEN_TYPE_OPTIONS,
  getAiAppKind,
  normalizeAppOpenType,
  serializeAiAppOptions,
} from './agentApp';

describe('agentApp kind mapping', () => {
  it('resolves kind from formOptions and legacy appType', () => {
    expect(getAiAppKind({ formOptions: serializeAiAppOptions('workflow') })).toBe('workflow');
    expect(getAiAppKind({ formOptions: serializeAiAppOptions('chatAgent') })).toBe('chatAgent');
    expect(getAiAppKind({ appType: 'ai' })).toBe('chatAgent');
  });

  it('defaults unknown application open types to a new window while retaining iframe', () => {
    expect(normalizeAppOpenType(undefined)).toBe('_blank');
    expect(normalizeAppOpenType('iframe')).toBe('iframe');
    expect(APP_OPEN_TYPE_OPTIONS).toEqual([
      { label: '新窗口打开', value: '_blank' },
      { label: '内嵌打开', value: 'iframe' },
    ]);
  });

  // v1.9 工作台新增类型（对齐 FastGPT AppTypeEnum）
  it('maps v1.9 aiAppType values (incl. FastGPT advanced/plugin aliases)', () => {
    expect(getAiAppKind({ aiAppType: 'simple' })).toBe('simple');
    expect(getAiAppKind({ aiAppType: 'workflowTool' })).toBe('workflowTool');
    expect(getAiAppKind({ aiAppType: 'httpToolSet' })).toBe('httpToolSet');
    expect(getAiAppKind({ aiAppType: 'advanced' })).toBe('workflow');
    expect(getAiAppKind({ aiAppType: 'plugin' })).toBe('workflowTool');
  });
});
