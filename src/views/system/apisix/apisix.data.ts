import type { BasicColumn, FormSchema } from '/@/components/Table';

export const httpMethodOptions = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'].map((value) => ({
  label: value,
  value,
}));

export const pluginOptions = [
  { label: '限流 limit-count', value: 'limit-count' },
  { label: 'JWT 鉴权 jwt-auth', value: 'jwt-auth' },
  { label: '跨域 cors', value: 'cors' },
  { label: '路径改写 proxy-rewrite', value: 'proxy-rewrite' },
  { label: '请求头改写 request-transformer', value: 'request-transformer' },
  { label: 'Prometheus 监控', value: 'prometheus' },
];

export const columns: BasicColumn[] = [
  {
    title: '路由',
    dataIndex: 'name',
    width: 250,
    slots: { customRender: 'route' },
  },
  {
    title: '匹配规则',
    dataIndex: 'uris',
    width: 240,
    slots: { customRender: 'matcher' },
  },
  {
    title: '上游服务',
    dataIndex: 'upstreamName',
    width: 190,
    slots: { customRender: 'upstream' },
  },
  {
    title: '插件',
    dataIndex: 'plugins',
    width: 160,
    slots: { customRender: 'plugins' },
  },
  {
    title: '运行状态',
    dataIndex: 'status',
    width: 120,
    slots: { customRender: 'status' },
  },
  {
    title: '最近更新',
    dataIndex: 'updatedAt',
    width: 170,
    slots: { customRender: 'updatedAt' },
  },
];

export const searchFormSchema: FormSchema[] = [
  {
    field: 'keyword',
    label: '关键词',
    component: 'Input',
    componentProps: {
      placeholder: '搜索名称、路由 ID 或 URI',
      allowClear: true,
    },
    colProps: { span: 6 },
  },
  {
    field: 'status',
    label: '运行状态',
    component: 'Select',
    componentProps: {
      allowClear: true,
      options: [
        { label: '已启用', value: 1 },
        { label: '已停用', value: 0 },
      ],
    },
    colProps: { span: 5 },
  },
];
