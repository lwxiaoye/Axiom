import { BasicColumn, FormSchema } from '/@/components/Table';
import { dateUtil } from '/@/utils/dateUtil';

function displayAuditValue(value: unknown): string {
  const text = String(value ?? '').trim();
  return text || '—';
}

function formatAuditTime(value: unknown): string {
  const text = String(value ?? '').trim();
  if (!text) return '—';
  const parsed = dateUtil(text);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm:ss') : text;
}

export const columns: BasicColumn[] = [
  {
    title: '日志内容',
    dataIndex: 'logContent',
    width: 100,
    align: 'left',
  },
  {
    title: '操作人ID',
    dataIndex: 'userid',
    width: 80,
  },
  {
    title: '操作人',
    dataIndex: 'username',
    width: 80,
  },
  {
    title: 'IP',
    dataIndex: 'ip',
    width: 80,
  },
  {
    title: '耗时(毫秒)',
    dataIndex: 'costTime',
    width: 80,
  },
  {
    title: '创建时间',
    dataIndex: 'createTime',
    sorter: true,
    width: 80,
  },
  {
    title: '客户端类型',
    dataIndex: 'clientType_dictText',
    width: 60,
  },
];

/**
 * 操作日志需要操作类型
 */
export const operationLogColumn: BasicColumn[] = [
  ...columns,
  {
    title: '操作类型',
    dataIndex: 'operateType_dictText',
    width: 40,
  },
];

export const exceptionColumns: BasicColumn[] = [
  {
    title: '异常标题',
    dataIndex: 'logContent',
    width: 100,
    align: 'left',
  },
  {
    title: '请求地址',
    dataIndex: 'requestUrl',
    width: 100,
  },
  {
    title: '请求参数',
    dataIndex: 'method',
    width: 60,
  },
  {
    title: '操作人',
    dataIndex: 'username',
    width: 60,
    customRender: ({ record }) => {
      const pname = record.username;
      const pid = record.userid;
      if(!pname && !pid){
        return "";
      }
      return pname + " (账号: "+ pid + " )";
    },
  },
  {
    title: 'IP',
    dataIndex: 'ip',
    width: 60,
  },
  {
    title: '创建时间',
    dataIndex: 'createTime',
    sorter: true,
    width: 60,
  },
  {
    title: '客户端类型',
    dataIndex: 'clientType_dictText',
    width: 60,
  },
];

export const auditColumns: BasicColumn[] = [
  {
    title: '审计类型',
    dataIndex: 'category',
    width: 70,
    customRender: ({ record }) => ({
      login: '用户登录',
      knowledge_access: '知识库访问',
      model_call: '模型调用',
      plugin_call: '插件调用',
      database_query: '数据库查询',
      export: '导出',
    }[record.category] || record.category),
  },
  { title: '操作', dataIndex: 'action', width: 100 },
  { title: '对象', dataIndex: 'resource', width: 150 },
  {
    title: '操作人',
    dataIndex: 'username',
    width: 80,
    customRender: ({ record }) => displayAuditValue(record.username),
  },
  {
    title: '操作人ID',
    dataIndex: 'userid',
    width: 100,
    customRender: ({ record }) => displayAuditValue(record.userid),
  },
  { title: '结果', dataIndex: 'status', width: 60 },
  {
    title: 'IP',
    dataIndex: 'ip',
    width: 80,
    customRender: ({ record }) => displayAuditValue(record.ip),
  },
  {
    title: '创建时间',
    dataIndex: 'createTime',
    sorter: true,
    width: 100,
    customRender: ({ record }) => formatAuditTime(record.createTime),
  },
];

export const searchFormSchema: FormSchema[] = [
  {
    field: 'keyWord',
    label: '搜索日志',
    component: 'Input',
    colProps: { span: 8 },
  },
  {
    field: 'fieldTime',
    component: 'RangePicker',
    label: '创建时间',
    componentProps: {
      valueType: 'Date',
    },
    colProps: {
      span: 6,
    },
  },
];

export const operationSearchFormSchema: FormSchema[] = [
  ...searchFormSchema,
  {
    field: 'operateType',
    label: '操作类型',
    component: 'JDictSelectTag',
    colProps: { span: 4 },
    componentProps: {
      dictCode: 'operate_type',
    },
  },
];

export const auditSearchFormSchema: FormSchema[] = [
  ...searchFormSchema,
  {
    field: 'category',
    label: '审计类型',
    component: 'Select',
    colProps: { span: 4 },
    componentProps: {
      allowClear: true,
      options: [
        { label: '用户登录', value: 'login' },
        { label: '知识库访问', value: 'knowledge_access' },
        { label: '模型调用', value: 'model_call' },
        { label: '插件调用', value: 'plugin_call' },
        { label: '数据库查询', value: 'database_query' },
        { label: '导出', value: 'export' },
      ],
    },
  },
];
