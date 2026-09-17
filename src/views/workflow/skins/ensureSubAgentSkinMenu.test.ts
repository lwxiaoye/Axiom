import { ensureSubAgentSkinMenu, SUB_AGENT_SKIN_MENU_PATH } from './ensureSubAgentSkinMenu';

describe('sub-agent skin menu fallback', () => {
  it('adds the skin manager beside the intelligent-agent manager exactly once', () => {
    const menus: any[] = [
      {
        name: 'AI智能体',
        path: '/workflow',
        children: [{ name: '智能体管理', path: '/workflow/manage' }],
      },
    ];

    ensureSubAgentSkinMenu(menus);
    ensureSubAgentSkinMenu(menus);

    expect(menus[0].children.filter((item) => item.path === SUB_AGENT_SKIN_MENU_PATH)).toHaveLength(1);
    expect(menus[0].children.map((item) => item.path)).toEqual([
      '/workflow/manage',
      SUB_AGENT_SKIN_MENU_PATH,
    ]);
  });

  it('does not expose the admin skin page when the user has no manager menu', () => {
    const menus: any[] = [{ name: '首页', path: '/dashboard' }];

    ensureSubAgentSkinMenu(menus);

    expect(menus.some((item) => item.path === SUB_AGENT_SKIN_MENU_PATH)).toBe(false);
  });
});
