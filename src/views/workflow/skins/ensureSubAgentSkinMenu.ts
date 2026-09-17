import type { Menu } from '/@/router/types';

export const SUB_AGENT_SKIN_MENU_PATH = '/workflow/skins';

const SUB_AGENT_SKIN_MENU: Menu = {
  name: '子智能体皮肤管理',
  path: SUB_AGENT_SKIN_MENU_PATH,
  icon: 'ant-design:skin-outlined',
  hideMenu: false,
  alwaysShow: false,
  meta: {
    title: '子智能体皮肤管理',
    icon: 'ant-design:skin-outlined',
    hideChildrenInMenu: false,
  },
};

function menuHasPath(menus: Menu[] | undefined, path: string): boolean {
  return (menus || []).some((menu) => menu.path === path || menuHasPath(menu.children, path));
}

function findParentOfPath(menus: Menu[], path: string): Menu | undefined {
  for (const menu of menus) {
    if (menu.children?.some((child) => child.path === path)) return menu;
    const nested = findParentOfPath(menu.children || [], path);
    if (nested) return nested;
  }
  return undefined;
}

/** Keep the skin manager beside the intelligent-agent manager when the DB menu is not migrated yet. */
export function ensureSubAgentSkinMenu(menus: Menu[]): Menu[] {
  if (!Array.isArray(menus) || menuHasPath(menus, SUB_AGENT_SKIN_MENU_PATH)) return menus;
  const parent = findParentOfPath(menus, '/workflow/manage');
  if (parent) {
    parent.alwaysShow = false;
    if (parent.meta) parent.meta.hideChildrenInMenu = false;
    parent.children = [...(parent.children || []), { ...SUB_AGENT_SKIN_MENU }];
  } else {
    const manageIndex = menus.findIndex((menu) => menu.path === '/workflow/manage');
    if (manageIndex >= 0) menus.splice(manageIndex + 1, 0, { ...SUB_AGENT_SKIN_MENU });
  }
  return menus;
}
