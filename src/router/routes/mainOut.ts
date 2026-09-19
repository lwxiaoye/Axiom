/**
The routing of this file will not show the layout.
It is an independent new page.
the contents of the file still need to log in to access
 */
import type { AppRouteModule } from '/@/router/types';
import { legacySectionToPath } from '/@/views/peopleCenter/centerRoute';

export const mainOutRoutes: AppRouteModule[] = [
  {
    // 系统内置 Harness 应用：使用独立运行页，不复用 /center 外壳。
    path: '/center/chat/ppt',
    name: 'BuiltinPresentationRun',
    component: () => import('/@/views/peopleCenter/pages/BuiltinHarnessRunPage.vue'),
    props: { preset: 'presentation' },
    meta: { title: '演示文稿助手' },
  },
  {
    path: '/center/chat/campus',
    name: 'BuiltinCampusRun',
    component: () => import('/@/views/peopleCenter/pages/BuiltinHarnessRunPage.vue'),
    props: { preset: 'campus_services' },
    meta: { title: '校园百事通' },
  },
  {
    path: '/center/chat/interview',
    name: 'BuiltinInterviewRun',
    component: () => import('/@/views/peopleCenter/pages/BuiltinHarnessRunPage.vue'),
    props: { preset: 'interview' },
    meta: { title: '面试助手' },
  },
  {
    // 管理配置：独立页面，由右上角菜单新开标签页打开，不套 center 外壳
    path: '/admin',
    name: 'AdminConsole',
    component: () => import('/@/views/peopleCenter/pages/AdminConsolePage.vue'),
    meta: { title: '管理配置' },
  },
  {
    // WS1：用户侧 Agent 聚合服务改为路由多页（父路由外壳 + 板块子路由）
    path: '/center',
    name: 'Center',
    component: () => import('/@/views/peopleCenter/center.vue'),
    redirect: '/center/chat',
    meta: {
      title: 'AI聚合服务',
    },
    children: [
      {
        path: 'models',
        name: 'CenterModels',
        component: () => import('/@/views/peopleCenter/pages/ModelConfigPage.vue'),
        meta: { title: '模型配置' },
      },
      {
        path: 'chat',
        name: 'CenterChat',
        component: () => import('/@/views/peopleCenter/pages/ChatPage.vue'),
        meta: { title: '新对话' },
      },
      {
        path: 'agent',
        name: 'CenterAgent',
        component: () => import('/@/views/peopleCenter/pages/AgentMarketPage.vue'),
        meta: { title: '智能体广场' },
      },
      {
        path: 'knowledge',
        name: 'CenterKnowledge',
        component: () => import('/@/views/peopleCenter/pages/MyKnowledgePage.vue'),
        meta: { title: '我的知识库' },
      },
      {
        path: 'skill',
        name: 'CenterSkill',
        component: () => import('/@/views/peopleCenter/pages/SkillMarketPage.vue'),
        meta: { title: 'Skill广场' },
      },
      {
        // 「我的文件」用户文件工作区（ADR-047 §6.6）
        path: 'files',
        name: 'CenterFiles',
        component: () => import('/@/views/peopleCenter/pages/MyFilesPage.vue'),
        meta: { title: '我的文件' },
      },
    ],
  },
  {
    // 兼容旧入口 /centerNew?section=xxx → /center/xxx
    path: '/centerNew',
    name: 'centerNew',
    redirect: (to) => legacySectionToPath(to.query.section),
    meta: {
      title: 'AI聚合服务',
    },
  },
];

export const mainOutRouteNames = mainOutRoutes.map((item) => item.name);
