import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Form';
import { CHANNEL_OPTIONS } from '@/utils/channel/channel';

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
    title: '类型',
    dataIndex: 'type',
    width: 120,
    align: 'center',
    customRender: ({ record }) => {
      const channel = CHANNEL_OPTIONS.find(item => item.value === record.type);
      if (channel) {
        return {
          children: channel.label,
          attrs: {
            style: `display: inline-block; padding: 4px 12px; border-radius: 12px; background-color: var(--color-${channel.color}, #e8e8e8); color: #666; font-size: 12px;`,
          },
        };
      }
      return `类型 ${record.type}`;
    },
  },

  {
    title: '模型',
    dataIndex: 'models',
    width: 200,
    align: 'left',
    ellipsis: true,
  },
 
    {
    title: '优先级',
    dataIndex: 'priority',
    width: 60,
    align: 'center',
  },
  {
    title: '权重',
    dataIndex: 'weight',
    width: 60,
    align: 'center',
  },
    {
    title: '备注',
    dataIndex: 'remark',
    width: 60,
    align: 'center',
  },
    {
    title: '状态',
    dataIndex: 'status',
    width: 80,
    align: 'center',
    slots: { customRender: 'statusSwitch' },
  },
 {
    title: '响应时间',
    dataIndex: 'response_time',
    width: 80,
    align: 'center',
    customRender: ({ record }) => {
      return `${record.response_time || 0}ms`;
    },
  },
];

export const searchFormSchema: FormSchema[] = [
  {
    label: '名称',
    field: 'name',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '类型',
    field: 'type',
    component: 'Select',
    componentProps: {
      showSearch: true,
      filterOption: (input, option) => {
        return option.label.toLowerCase().indexOf(input.toLowerCase()) >= 0;
      },
      options: CHANNEL_OPTIONS.map(item => ({
        label: item.label,
        value: item.value,
      })),
    },
    colProps: { span: 6 },
  },
  {
    label: '状态',
    field: 'status',
    component: 'Select',
    componentProps: {
      options: [
        { label: '已启用', value: "enabled" },
        { label: '已禁用', value: "disabled" },
      ],
    },
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
      placeholder: '请输入名称',
    },
  },
    {
    label: '类型',
    field: 'type',
    required: true,
    component: 'Select',
    colProps: { span: 24 },
    componentProps: {
      showSearch: true,
      filterOption: (input, option) => {
        return option.label.toLowerCase().indexOf(input.toLowerCase()) >= 0;
      },
      options: CHANNEL_OPTIONS.map(item => ({
        label: item.label,
        value: item.value,
      })),
    },
  },
  {
    label: '密钥',
    field: 'key',
    component: 'InputPassword',
    componentProps: {
      placeholder: '请输入密钥（编辑模式下，保存的密钥不会显示）',
      autocomplete: 'new-password',
    },
  },
  {
    label: 'API地址',
    field: 'base_url',
    component: 'Input',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请输入API地址',
    },
    helpMessage: '对于官方渠道，new-api已经内置地址，除非是第三方代理站点或者Azure的特殊接入地址，否则不需要填写',
  },
  {
    label: '模型',
    field: 'models',
    required: true,
    component: 'JSelectInput',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请选择模型',
      mode: 'multiple',
    },
  },
    {
    label: '优先级',
    field: 'priority',
    component: 'InputNumber',
    defaultValue: 0,
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请输入优先级',  
      min: 0,
      max: 100,
    },
  },
  {
    label: '权重',
    field: 'weight',
    component: 'InputNumber',
    defaultValue: 0,
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请输入权重',
      min: 0,
      max: 100,
    },
  },
  {
    label: '是否自动禁用',
    field: 'auto_ban',
    component: 'Switch',
    defaultValue: true,
    colProps: { span: 24 },
    componentProps: {
      checkedChildren: '开启',
      unCheckedChildren: '关闭',
    },
  },

    {
    label: '备注',
    field: 'remark',
    component: 'InputTextArea',
    colProps: { span: 24 },
    componentProps: {
      placeholder: '请输入备注',
    },
  },
];
