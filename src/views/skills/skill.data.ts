import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Form';

export const columns: BasicColumn[] = [
  {
    title: 'Skill名称',
    dataIndex: 'name',
    width: 180,
    align: 'left',
  },
  {
    title: '标识',
    dataIndex: 'skillId',
    width: 160,
    align: 'left',
  },
  {
    title: '版本',
    dataIndex: 'version',
    width: 90,
    align: 'center',
  },
  {
    title: '来源',
    dataIndex: 'source',
    width: 90,
    align: 'center',
    customRender: ({ record }) => {
      const map = {
        upload: '上传',
        url: '网络',
        builtin: '内置',
      };
      return map[record.source] || record.source || '-';
    },
  },
  {
    title: '描述',
    dataIndex: 'description',
    width: 260,
    align: 'left',
    ellipsis: true,
  },
  {
    title: '启用',
    dataIndex: 'enabled',
    width: 90,
    align: 'center',
    slots: { customRender: 'enabledSwitch' },
  },
  {
    title: '更新时间',
    dataIndex: 'updateTime',
    width: 170,
    align: 'center',
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
    label: '标识',
    field: 'skillId',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '来源',
    field: 'source',
    component: 'Select',
    componentProps: {
      options: [
        { label: '上传', value: 'upload' },
        { label: '网络', value: 'url' },
        { label: '内置', value: 'builtin' },
      ],
    },
    colProps: { span: 6 },
  },
  {
    label: '状态',
    field: 'enabled',
    component: 'Select',
    componentProps: {
      options: [
        { label: '启用', value: 1 },
        { label: '停用', value: 0 },
      ],
    },
    colProps: { span: 6 },
  },
];
