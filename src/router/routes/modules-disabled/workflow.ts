import type { AppRouteModule } from '/@/router/types';
import { LAYOUT } from '/@/router/constant';

const workflow: AppRouteModule = {
  path: '/workflow',
  name: 'Workflow',
  component: LAYOUT,
  redirect: '/workflow/editor',
  meta: {
    orderNo: 40,
    icon: 'ant-design:deployment-unit-outlined',
    title: 'AI编排中心',
  },
  children: [
    {
      path: 'editor',
      name: 'WorkflowEditor',
      component: () => import('/@/views/workflow/editor/index.vue'),
      meta: {
        title: '工作流编排',
      },
    },
    {
      path: 'agent',
      name: 'WorkflowAgentConfig',
      component: () => import('/@/views/workflow/agent/index.vue'),
      meta: {
        title: '对话Agent配置',
      },
    },
  ],
};

export default workflow;
