import {
  catalogRuntimeAppId,
  excludeOwnedDeletedRuntimeApps,
  isAgentRuntimeCatalogItem,
  isCatalogItemOwnedBy,
  marketplaceCreatorKeys,
} from './marketplaceCatalog';

const user = { id: '2085197534705397761', username: 'wushaoran' };

describe('marketplaceCatalog', () => {
  it('从运行页地址和 form_options.sourceAppId 解析运行时应用 ID', () => {
    expect(catalogRuntimeAppId({ pcUrl: '/agent/run/app-1?x=1' })).toBe('app-1');
    expect(catalogRuntimeAppId({ form_options: '{"sourceAppId":"app-2"}' })).toBe('app-2');
    expect(isAgentRuntimeCatalogItem({ pcUrl: '/flow/old' })).toBe(false);
  });

  it('创建者字段同时兼容用户 ID 与用户名', () => {
    expect(marketplaceCreatorKeys({ ...user, realname: '吴少然' })).toEqual([
      '2085197534705397761',
      'wushaoran',
      '吴少然',
    ]);
    expect(isCatalogItemOwnedBy({ createBy: 'wushaoran' }, user)).toBe(true);
    expect(isCatalogItemOwnedBy({ createBy: '2085197534705397761' }, user)).toBe(true);
    expect(isCatalogItemOwnedBy({ createBy_dictText: '吴少然' }, { ...user, realname: '吴少然' })).toBe(true);
    expect(isCatalogItemOwnedBy({ createBy: 'other' }, user)).toBe(false);
  });

  it('未完成我的智能体加载时不去掉广场卡片，避免把别人的应用误藏', () => {
    const apps = [{ id: 'gone', appName: '前端开发协作助手', pcUrl: '/agent/run/gone', createBy: 'wushaoran' }];
    expect(excludeOwnedDeletedRuntimeApps(apps, [], user, false)).toEqual(apps);
  });

  it('我的智能体加载完成后，藏掉当前用户已删但仍留在 Java 目录里的运行时智能体', () => {
    const apps = [
      { id: 'gone', appName: '前端开发协作助手', pcUrl: '/agent/run/gone', createBy: 'wushaoran' },
      { id: 'live', appName: '会议纪要助手', pcUrl: '/agent/run/live', createBy: 'wushaoran' },
      { id: 'peer', appName: '别人的助手', pcUrl: '/agent/run/peer', createBy: 'admin' },
      { id: 'java', appName: '外部应用', pcUrl: 'https://example.com', createBy: 'wushaoran' },
    ];
    const visible = excludeOwnedDeletedRuntimeApps(apps, [{ id: 'live' }], user, true);
    expect(visible.map((item) => item.id)).toEqual(['live', 'peer', 'java']);
  });
});
