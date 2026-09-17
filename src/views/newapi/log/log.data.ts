import { BasicColumn, FormSchema } from '/@/components/Table';

export const columns: BasicColumn[] = [
  {
    title: 'ID',
    dataIndex: 'id',
    width: 80,
    align: 'center',
  },

  {
    title: '创建时间',
    dataIndex: 'created_at',
    width: 160,
    align: 'center',
    customRender: ({ record }) => {
      if (record.created_at) {
        const date = new Date(record.created_at * 1000);
        return date.toLocaleString('zh-CN', {
          year: 'numeric',
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
      }
      return '';
    },
  },
  // {
  //   title: '类型',
  //   dataIndex: 'type',
  //   width: 80,
  //   align: 'center',
  //   customRender: ({ record }) => {
  //     const typeMap: Record<number, string> = {
  //       1: '请求',
  //       2: '测试',
  //       3: '错误',
  //     };
  //     return typeMap[record.type] || `类型${record.type}`;
  //   },
  // },
  // {
  //   title: '内容',
  //   dataIndex: 'content',
  //   width: 200,
  //   align: 'left',
  //   ellipsis: true,
  // },
  {
    title: '令牌名称',
    dataIndex: 'token_name',
    width: 120,
    align: 'left',
    ellipsis: true,
  },
  {
    title: '模型名称',
    dataIndex: 'model_name',
    width: 150,
    align: 'left',
    ellipsis: true,
  },
  {
    title: '配额消耗',
    dataIndex: 'quota',
    width: 100,
    align: 'center',
  },
  {
    title: '输入',
    dataIndex: 'prompt_tokens',
    width: 100,
    align: 'center',
  },
  {
    title: '输出',
    dataIndex: 'completion_tokens',
    width: 120,
    align: 'center',
  },
  {
    title: '耗时(s)',
    dataIndex: 'use_time',
    width: 100,
    align: 'center',
  },
  {
    title: '是否流式',
    dataIndex: 'is_stream',
    width: 80,
    align: 'center',
    customRender: ({ record }) => {
      return record.is_stream ? '是' : '否';
    },
  },
  {
    title: '渠道',
    dataIndex: 'channel_name',
    width: 120,
    align: 'left',
  },
  // {
  //   title: '分组',
  //   dataIndex: 'group',
  //   width: 100,
  //   align: 'left',
  // },
  // {
  //   title: 'IP',
  //   dataIndex: 'ip',
  //   width: 120,
  //   align: 'left',
  // },
];

export const searchFormSchema: FormSchema[] = [
  {
    label: '令牌名称',
    field: 'token_name',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '模型名称',
    field: 'model_name',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '开始时间',
    field: 'start_timestamp',
    component: 'DatePicker',
    colProps: { span: 6 },
    componentProps: {
      showTime: true,
      placeholder: '请选择开始时间',
    },
  },
  {
    label: '结束时间',
    field: 'end_timestamp',
    component: 'DatePicker',
    colProps: { span: 6 },
    componentProps: {
      showTime: true,
      placeholder: '请选择结束时间',
    },
  },
];
