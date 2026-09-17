import {
  AGENT_CAPABILITY_DEFINITIONS,
  buildAgentCapabilityFilters,
  decorateMarketplaceCreator,
  getAgentCapability,
  getAgentVisualVariant,
  getMarketplaceCreatorInitials,
  getMarketplaceCreatorName,
  matchesMarketplaceApp,
  marketplaceCreatorLookupKey,
  type MarketplaceApp,
} from './agentMarketCapabilities';

const apps: MarketplaceApp[] = [
  { id: 'c-1', appName: '面试助手', appRemark: '模拟面试', appCategory: 'communication' },
  { id: 'd-1', appName: '文档摘要', appRemark: '提取要点', appCategory: 'document_knowledge' },
  { id: 't-1', appName: '智能填表', appRemark: '字段填充', appCategory: 'data_table' },
  { id: 'x-1', appName: '未知能力', appRemark: '其他', appCategory: 'legacy_value' },
];

describe('agent market capabilities', () => {
  it('keeps the canonical capability order and only exposes non-empty filters', () => {
    expect(AGENT_CAPABILITY_DEFINITIONS.map((item) => item.key)).toEqual([
      'communication',
      'document_knowledge',
      'data_table',
      'content_creation',
      'planning_structure',
      'developer_automation',
      'image_multimedia',
      'other',
    ]);
    expect(buildAgentCapabilityFilters(apps)).toEqual([
      { value: 'all', label: '全部应用', count: 4 },
      { value: 'communication', label: '沟通交互', count: 1 },
      { value: 'document_knowledge', label: '文档与知识', count: 1 },
      { value: 'data_table', label: '数据与表格', count: 1 },
      { value: 'other', label: '综合/其他', count: 1 },
    ]);
  });

  it('maps blank and unknown backend values to other without inspecting the app name', () => {
    expect(getAgentCapability({ appCategory: 'legacy_value' })).toBe('other');
    expect(getAgentCapability({ appCategory: '' })).toBe('other');
    expect(getAgentCapability({ appCategory: 'communication' })).toBe('communication');
  });

  it('intersects category and search keyword filters', () => {
    expect(apps.filter((item) => matchesMarketplaceApp(item, 'document_knowledge', '要点'))).toEqual([apps[1]]);
    expect(apps.filter((item) => matchesMarketplaceApp(item, 'communication', '要点'))).toEqual([]);
    expect(apps.filter((item) => matchesMarketplaceApp(item, 'all', '智能'))).toEqual([apps[2]]);
  });

  it('selects a stable visual variant from the application id', () => {
    expect(getAgentVisualVariant({ id: 'same-id' })).toBe(getAgentVisualVariant({ id: 'same-id' }));
    expect(['a', 'b']).toContain(getAgentVisualVariant({ id: 'another-id' }));
  });

  it('prefers the API creator name and falls back to readable initials', () => {
    expect(getMarketplaceCreatorName({ createByName: '张同学', createBy_dictText: '旧名称' })).toBe('张同学');
    expect(getMarketplaceCreatorInitials({ createByName: '张同学' })).toBe('张');
    expect(getMarketplaceCreatorInitials({ createByName: 'Alice Chen' })).toBe('AL');
    expect(getMarketplaceCreatorName({ createBy: '2085197534705397761' })).toBe('未知');
    expect(getMarketplaceCreatorInitials({ createBy: '2085197534705397761' })).toBe('?');
    expect(getMarketplaceCreatorName({})).toBe('未知');
  });

  it('normalizes the same creator from username or user id to one real name', () => {
    const profile = {
      id: '2085197534705397761',
      username: 'wushaoran',
      realname: '吴少然',
      avatar: '/avatar/wushaoran.png',
    };
    expect(decorateMarketplaceCreator({
      id: 'by-name',
      appName: '按用户名创建',
      createBy: 'wushaoran',
    }, profile)).toEqual(expect.objectContaining({
      createByName: '吴少然',
      createByAvatar: '/avatar/wushaoran.png',
    }));
    expect(decorateMarketplaceCreator({
      id: 'by-id',
      appName: '按 ID 创建',
      createBy: '2085197534705397761',
    }, profile).createByName).toBe('吴少然');
    expect(decorateMarketplaceCreator({
      id: 'other',
      appName: '其他人创建',
      createBy: 'admin',
    }, profile).createByName).toBeUndefined();
  });

  it('only looks up untranslated creator identifiers', () => {
    expect(marketplaceCreatorLookupKey({
      id: 'raw',
      appName: '原始用户名',
      createBy: 'wushaoran',
    })).toBe('wushaoran');
    expect(marketplaceCreatorLookupKey({
      id: 'translated',
      appName: '已翻译',
      createBy: '2085197534705397761',
      createBy_dictText: '吴少然',
    })).toBe('');
  });
});
