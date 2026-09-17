import type { AppRouteModule } from '/@/router/types';
import { LAYOUT } from '/@/router/constant';

const modelGateway: AppRouteModule = {
  path: '/channel/model-gateway',
  name: 'ModelGateway',
  component: LAYOUT,
  redirect: '/channel/model-gateway/index',
  meta: {
    orderNo: 45,
    icon: 'ant-design:gateway-outlined',
    title: '模型网关',
  },
  children: [
    {
      path: 'index',
      name: 'ModelGatewayConfig',
      component: () => import('/@/views/modelGateway/index.vue'),
      meta: {
        title: '网关配置',
      },
    },
  ],
};

export default modelGateway;
