import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Form';
import { MODEL_INSTANCE_STATE_MAP, SOURCE_MAP } from '../gpustack.enums';

/** 模型列表列定义 */
export const modelColumns: BasicColumn[] = [
  {
    title: '模型名称',
    dataIndex: 'name',
    width: 200,
    align: 'left',
  },
  {
    title: '来源',
    dataIndex: 'source',
    width: 120,
    align: 'center',
    customRender: ({ record }) => SOURCE_MAP[record.source] || record.source,
  },
  {
    title: '副本(就绪/期望)',
    dataIndex: 'replicas',
    width: 120,
    align: 'center',
    customRender: ({ record }) => {
      const ready = record.ready_replicas ?? 0;
      const want = record.replicas ?? 0;
      const color = ready === want && want > 0 ? 'green' : ready > 0 ? 'gold' : 'default';
      return {
        children: `${ready} / ${want}`,
        attrs: { class: `ant-tag ant-tag-${color}` },
      };
    },
  },
  {
    title: '后端',
    dataIndex: 'backend',
    width: 120,
    align: 'center',
  },
  {
    title: '调度策略',
    dataIndex: 'placement_strategy',
    width: 120,
    align: 'center',
    customRender: ({ record }) => record.placement_strategy || 'spread',
  },
  {
    title: '状态',
    dataIndex: 'health',
    width: 100,
    align: 'center',
    customRender: ({ record }) => {
      const want = record.replicas ?? 0;
      const ready = record.ready_replicas ?? 0;
      let state = 'pending';
      if (want === 0) state = 'down';
      else if (ready === want) state = 'running';
      else if (ready > 0) state = 'starting';
      const map = MODEL_INSTANCE_STATE_MAP[state] || { text: state, color: 'default' };
      return { children: map.text, attrs: { class: `ant-tag ant-tag-${map.color}` } };
    },
  },
  {
    title: '进度',
    dataIndex: 'progress',
    width: 140,
    align: 'center',
    // 通过 slots 自定义渲染（index.vue 用 #bodyCell 处理 a-progress）。
    // 这里保留字段占位，实际渲染在页面层完成。
    customRender: ({ record }) => {
      const want = record.replicas ?? 0;
      const ready = record.ready_replicas ?? 0;
      if (want === 0) return '-';
      if (ready === want) return { children: '就绪', attrs: { class: `ant-tag ant-tag-green` } };
      if (ready > 0) return { children: `${ready}/${want} 启动中`, attrs: { class: `ant-tag ant-tag-gold` } };
      return { children: '部署中', attrs: { class: `ant-tag ant-tag-blue` } };
    },
  },
  {
    title: '创建时间',
    dataIndex: 'created_at',
    width: 170,
    align: 'center',
  },
];

export const modelSearchFormSchema: FormSchema[] = [
  {
    label: '关键字',
    field: 'search',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '后端',
    field: 'backend',
    component: 'Input',
    colProps: { span: 6 },
  },
];

/** 部署表单中的调度策略字段（DeployModal 复用） */
export const scheduleFormSchema: FormSchema[] = [
  {
    label: '调度策略',
    field: 'placement_strategy',
    component: 'Select',
    defaultValue: 'spread',
    componentProps: {
      options: [
        { label: 'Spread（分散）', value: 'spread' },
        { label: 'Binpack（聚拢）', value: 'binpack' },
      ],
    },
    colProps: { span: 12 },
  },
  {
    label: '副本数',
    field: 'replicas',
    component: 'InputNumber',
    defaultValue: 1,
    componentProps: { min: 0, max: 20 },
    colProps: { span: 12 },
  },
  {
    label: '每副本 GPU 数',
    field: 'gpus_per_replica',
    component: 'InputNumber',
    componentProps: { min: 1, max: 8 },
    colProps: { span: 12 },
    helpMessage: '多卡/张量并行时填写，留空则由 GPUStack 自动分配',
  },
  {
    label: '跨节点分布式推理',
    field: 'distributed_inference_across_workers',
    component: 'Switch',
    defaultValue: false,
    componentProps: { checkedChildren: '开启', unCheckedChildren: '关闭' },
    colProps: { span: 12 },
  },
];
