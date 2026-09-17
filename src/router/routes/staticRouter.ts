import type { AppRouteRecordRaw } from '/@/router/types';
import { LAYOUT } from '/@/router/constant';

export const staticRoutesList: AppRouteRecordRaw[] = [
  {
    path: '/workflow/skins',
    name: 'SubAgentSkinManageParent',
    component: LAYOUT,
    meta: {
      title: '子智能体皮肤管理',
      hideMenu: true,
    },
    children: [
      {
        path: '',
        name: 'SubAgentSkinManagePage',
        component: () => import('/@/views/workflow/skins/index.vue'),
        meta: {
          title: '子智能体皮肤管理',
        },
      },
    ],
  },
  {
    path: '/newapi/campus-assistant',
    name: 'CampusAssistantConfigParent',
    component: LAYOUT,
    meta: {
      title: '校园百事通配置',
      hideMenu: true,
    },
    children: [
      {
        path: '',
        name: 'CampusAssistantConfigPage',
        component: () => import('/@/views/newapi/campusAssistant/index.vue'),
        meta: {
          title: '校园百事通配置',
        },
      },
    ],
  },
];
