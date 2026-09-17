import {
  APP_OPEN_TYPE_OPTIONS,
  getAiAppKind,
  getAiAppPrimaryAction,
  getAiAppRoute,
  normalizeAppOpenType,
  serializeAiAppOptions,
} from './agentApp';

describe('agentApp kind/action/route mapping', () => {
  it('resolves kind from formOptions and legacy appType', () => {
    expect(getAiAppKind({ formOptions: serializeAiAppOptions('workflow') })).toBe('workflow');
    expect(getAiAppKind({ formOptions: serializeAiAppOptions('chatAgent') })).toBe('chatAgent');
    expect(getAiAppKind({ appType: 'ai' })).toBe('chatAgent');
  });

  it('resolves primary action label by kind', () => {
    expect(getAiAppPrimaryAction({ formOptions: serializeAiAppOptions('workflow') })).toBe('编排');
    expect(getAiAppPrimaryAction({ formOptions: serializeAiAppOptions('chatAgent') })).toBe('配置');
  });

  it('routes workflow to editor and chatAgent to agent form', () => {
    expect(getAiAppRoute({ id: 'a1', formOptions: serializeAiAppOptions('workflow') })).toEqual({
      path: '/workflow/editor',
      query: { workflowAppId: 'a1' },
    });
    expect(getAiAppRoute({ id: 'a2', formOptions: serializeAiAppOptions('chatAgent') })).toEqual({
      path: '/workflow/agent',
      query: { workflowAppId: 'a2' },
    });
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
    expect(getAiAppPrimaryAction({ aiAppType: 'workflowTool' })).toBe('编排');
    expect(getAiAppPrimaryAction({ aiAppType: 'simple' })).toBe('配置');
    expect(getAiAppRoute({ id: 'a3', aiAppType: 'workflowTool' })).toEqual({
      path: '/workflow/editor',
      query: { workflowAppId: 'a3' },
    });
    expect(getAiAppRoute({ id: 'a4', aiAppType: 'simple' })).toEqual({
      path: '/workflow/agent',
      query: { workflowAppId: 'a4' },
    });
  });
});
