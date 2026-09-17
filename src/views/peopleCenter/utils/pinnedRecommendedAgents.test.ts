import { pickPinnedRecommendedAgents, PINNED_RECOMMENDED_AGENT_NAMES } from './pinnedRecommendedAgents';

describe('pickPinnedRecommendedAgents', () => {
  it('按固定名单顺序挑四个，不按 isRecommend 或列表顺序', () => {
    const market = [
      { id: 'w', appName: '天气助手', isRecommend: 'Y', pcUrl: '/w' },
      { id: 'campus-1000', appName: '校园百事通', pcUrl: '/center/chat/campus', builtinPreset: 'campus_services' },
      { id: 'ppt-1000', appName: '演示文稿助手', pcUrl: '/center/chat/ppt', builtinPreset: 'presentation' },
      { id: 'legacy-interview', appName: '面试助手', pcUrl: '/legacy-interview' },
      { id: 'mian', appName: '面试助手', appRemark: '模拟面试', appIcon: 'horse.png', pcUrl: '/center/chat/interview' },
      { id: 'xue', appName: '学伴', appRemark: '', pcUrl: '/xue' },
    ];
    const workflow = [
      { id: 'doc', name: '智能文档识别助手', description: '识别 PDF' },
      { id: 'form', name: '智能填表助手', description: '上传材料填表' },
    ];
    const picked = pickPinnedRecommendedAgents(market, workflow);
    expect(PINNED_RECOMMENDED_AGENT_NAMES).toEqual([
      '校园百事通',
      '演示文稿助手',
      '面试助手',
      '智能填表助手',
    ]);
    expect(picked.map((item) => item.name)).toEqual([
      '校园百事通',
      '演示文稿助手',
      '面试助手',
      '智能填表助手',
    ]);
    expect(picked.map((item) => item.open)).toEqual(['market', 'market', 'market', 'run']);
    expect(picked[0]).toEqual(expect.objectContaining({
      id: 'campus-1000',
    }));
    expect(picked[1]).toEqual(expect.objectContaining({
      id: 'ppt-1000',
    }));
    expect(picked.filter((item) => item.name === '面试助手').map((item) => item.id)).toEqual(['mian']);
    expect(picked.some((item) => item.name === '天气助手')).toBe(false);
  });

  it('无权或已停用的系统应用不在推荐区被前端补回', () => {
    const picked = pickPinnedRecommendedAgents(
      [{ id: 'mian', appName: '面试助手', pcUrl: '/mian' }],
      [],
    );
    expect(picked).toEqual([]);
  });

  it('系统页按固定路径入选，不依赖管理员可编辑的应用名称', () => {
    const picked = pickPinnedRecommendedAgents([
      { id: 'campus', appName: '我的校园助手', pcUrl: '/center/chat/campus' },
      { id: 'ppt', appName: '我的演示助手', pcUrl: '/center/chat/ppt' },
      { id: 'interview', appName: '我的面试练习', pcUrl: '/center/chat/interview' },
    ]);
    expect(picked.map((item) => item.id)).toEqual(['campus', 'ppt', 'interview']);
    expect(picked.map((item) => item.name)).toEqual(['我的校园助手', '我的演示助手', '我的面试练习']);
  });

  it('广场有访问地址时走 market，否则走我的智能体运行页', () => {
    const picked = pickPinnedRecommendedAgents(
      [{ id: 'form-m', appName: '智能填表助手', pcUrl: '/form' }],
      [{ id: 'form-w', name: '智能填表助手' }],
    );
    expect(picked).toEqual([
      expect.objectContaining({ id: 'form-m', open: 'market' }),
    ]);
  });
});
