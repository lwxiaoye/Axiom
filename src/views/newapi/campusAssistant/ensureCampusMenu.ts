import type { Menu } from '/@/router/types';

export const CAMPUS_ASSISTANT_MENU_PATH = '/newapi/campus-assistant';

const CAMPUS_MENU: Menu = {
  name: '校园百事通配置',
  path: CAMPUS_ASSISTANT_MENU_PATH,
  icon: 'ant-design:bank-outlined',
  hideMenu: false,
  alwaysShow: false,
  meta: {
    title: '校园百事通配置',
    icon: 'ant-design:bank-outlined',
    hideChildrenInMenu: false,
  },
};

function menuHasPath(menus: Menu[] | undefined, path: string): boolean {
  for (const menu of menus || []) {
    if (menu.path === path) return true;
    if (menuHasPath(menu.children, path)) return true;
  }
  return false;
}

function isKnowledgeParent(menu: Menu): boolean {
  return menu.name === '知识库管理'
    || menu.name === '知识库内容管理'
    || menu.path === '/knowledge'
    || menu.path === '/knowledge/base';
}

/**
 * Jeecg 侧栏把 alwaysShow=true 的父菜单收成一项，子菜单不会出现。
 * 这里强制知识库父菜单展开，并在缺失时补上校园百事通。
 */
export function ensureCampusAssistantMenu(menus: Menu[]): Menu[] {
  if (!Array.isArray(menus)) return menus;
  const parent = menus.find(isKnowledgeParent);
  if (parent) {
    parent.alwaysShow = false;
    if (parent.meta) parent.meta.hideChildrenInMenu = false;
    parent.children = parent.children ? [...parent.children] : [];
    const hasContentChild = parent.children.some((item) => item.path === '/knowledge/base');
    if (!hasContentChild && parent.path === '/knowledge/base') {
      parent.children.unshift({
        name: '知识库内容管理',
        path: '/knowledge/base',
        icon: parent.icon,
        hideMenu: false,
        alwaysShow: false,
        meta: {
          ...(parent.meta || {}),
          title: '知识库内容管理',
          icon: parent.icon || parent.meta?.icon,
          hideChildrenInMenu: false,
        },
      });
    }
    if (!parent.children.some((item) => item.path === CAMPUS_ASSISTANT_MENU_PATH)) {
      parent.children.push({ ...CAMPUS_MENU });
    }
    return menus;
  }
  if (!menuHasPath(menus, CAMPUS_ASSISTANT_MENU_PATH)) {
    menus.push({ ...CAMPUS_MENU });
  }
  return menus;
}
