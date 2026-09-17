import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Form';

export const columns: BasicColumn[] = [
  {
    title: 'ID',
    dataIndex: 'id',
    width: 60,
    align: 'center',
  },
  {
    title: '名称',
    dataIndex: 'name',
    width: 150,
    align: 'left',
  },
  {
    title: '密钥',
    dataIndex: 'key',
    width: 200,
    align: 'left',
    ellipsis: true,
    customRender: ({ record }) => {
      return 'sk-'+record.key;
    },
  },
    {
    title: '状态',
    dataIndex: 'status',
    width: 80,
    align: 'center',
    slots: { customRender: 'statusSwitch' },
  },
  {
    title: '可用模型',
    dataIndex: 'model_limits',
    width: 100,
    align: 'left',
    ellipsis: true,
    customRender: ({ record }) => {
      return record.model_limits_enabled ?  record.model_limits:'无限制';
    },
  },
  // {
  //   title: '已用配额',
  //   dataIndex: 'used_quota',
  //   width: 100,
  //   align: 'center',
  // },
  {
    title: '过期时间',
    dataIndex: 'expired_time',
    width: 160,
    align: 'center',
    customRender: ({ record }) => {
      if (record.expired_time === -1) {
        return '永不过期';
      }
      if (record.expired_time) {
        return new Date(record.expired_time * 1000).toLocaleString('zh-CN');
      }
      return '-';
    },
  },
  {
    title: '创建时间',
    dataIndex: 'created_time',
    width: 160,
    align: 'center',
    customRender: ({ record }) => {
      if (record.created_time) {
        return new Date(record.created_time * 1000).toLocaleString('zh-CN');
      }
      return '-';
    },
  },
  {
    title: '最后访问',
    dataIndex: 'accessed_time',
    width: 160,
    align: 'center',
    customRender: ({ record }) => {
      if (record.accessed_time) {
        return new Date(record.accessed_time * 1000).toLocaleString('zh-CN');
      }
      return '-';
    },
  },
];

export const searchFormSchema: FormSchema[] = [
  {
    label: '名称',
    field: 'keyword',
    component: 'Input',
    colProps: { span: 6 },
  },
    {
    label: '密钥',
    field: 'token',
    component: 'Input',
    colProps: { span: 6 },
  },
];

export const formSchema: FormSchema[] = [
  {
    label: 'ID',
    field: 'id',
    component: 'Input',
    show: false,
  },
  {
    label: '名称',
    field: 'name',
    required: true,
    component: 'Input',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请输入令牌名称',
    },
  },
  // {
  //   label: '用户',
  //   field: 'user_id',
  //   component: 'Select',
  //   colProps: { span: 24 },
  //   componentProps: {
  //     placeholder: '请选择用户',
  //     showSearch: true,
  //     filterOption: (input, option) => {
  //       return option.label.toLowerCase().indexOf(input.toLowerCase()) >= 0;
  //     },
  //   },
  // },
    {
    label: '可用模型',
    field: 'models',
    component: 'Select',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请选择模型',
      mode: 'multiple',
    },
  },
  {
    label: '状态',
    field: 'status',
    component: 'Switch',
    defaultValue: true,
    colProps: { span: 24 },
    componentProps: {
      checkedChildren: '启用',
      unCheckedChildren: '禁用',
    },
  },
  // {
  //   label: '剩余配额',
  //   field: 'remain_quota',
  //   component: 'InputNumber',
  //   defaultValue: 0,
  //   colProps: { span: 24 },
  //   componentProps: {
  //     placeholder: '请输入剩余配额',
  //     min: 0,
  //     style: { width: '100%' },
  //   },
  // },
  // {
  //   label: '无限配额',
  //   field: 'unlimited_quota',
  //   component: 'Switch',
  //   defaultValue: false,
  //   colProps: { span: 24 },
  //   componentProps: {
  //     checkedChildren: '开启',
  //     unCheckedChildren: '关闭',
  //   },
  // },
  // {
  //   label: '模型限制',
  //   field: 'model_limits_enabled',
  //   component: 'Switch',
  //   defaultValue: false,
  //   colProps: { span: 24 },
  //   componentProps: {
  //     checkedChildren: '开启',
  //     unCheckedChildren: '关闭',
  //   },
  // },
  // {
  //   label: '允许的模型',
  //   field: 'model_limits',
  //   component: 'InputTextArea',
  //   colProps: { span: 24 },
  //   componentProps: {
  //     placeholder: '请输入允许的模型，多个模型用逗号分隔',
  //     rows: 3,
  //   },
  //   show: ({ values }) => values?.model_limits_enabled,
  // },
  // {
  //   label: '允许的IP',
  //   field: 'allow_ips',
  //   component: 'InputTextArea',
  //   colProps: { span: 24 },
  //   componentProps: {
  //     placeholder: '请输入允许的IP地址，多个IP用逗号分隔',
  //     rows: 3,
  //   },
  // },
  // {
  //   label: '分组',
  //   field: 'group',
  //   component: 'Input',
  //   colProps: { span: 24 },
  //   componentProps: {
  //     placeholder: '请输入分组（可选）',
  //   },
  // },
  {
    label: '过期时间',
    field: 'expired_time',
    component: 'DatePicker',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请选择过期时间，留空则永不过期',
      style: { width: '100%' },
      format: 'YYYY-MM-DD HH:mm:ss',
      showTime: true,
    },
  },
];
