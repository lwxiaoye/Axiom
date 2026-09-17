import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Form';
import { WORKER_STATE_MAP } from '../gpustack.enums';

/** Worker 列表列定义 */
export const workerColumns: BasicColumn[] = [
  {
    title: '节点名称',
    dataIndex: 'name',
    width: 140,
    align: 'left',
  },
  {
    title: '状态',
    dataIndex: 'state',
    width: 100,
    align: 'center',
    customRender: ({ record }) => {
      // WorkerPublic.state 优先；部分版本用 status.unreachable
      const state = record.state || (record.unreachable ? 'unreachable' : 'ready');
      const map = WORKER_STATE_MAP[state] || { text: state, color: 'default' };
      return { children: map.text, attrs: { class: `ant-tag ant-tag-${map.color}` } };
    },
  },
  {
    title: 'IP 地址',
    dataIndex: 'ip',
    width: 140,
    align: 'left',
  },
  {
    title: '主机名',
    dataIndex: 'hostname',
    width: 140,
    align: 'left',
  },
  {
    title: 'CPU 使用率',
    dataIndex: 'cpuRate',
    width: 110,
    align: 'center',
    customRender: ({ record }) => {
      const rate = record?.status?.cpu?.utilization_rate ?? 0;
      return `${Number(rate).toFixed(1)}%`;
    },
  },
  {
    title: '内存使用率',
    dataIndex: 'memRate',
    width: 110,
    align: 'center',
    customRender: ({ record }) => {
      const rate = record?.status?.memory?.utilization_rate ?? 0;
      return `${Number(rate).toFixed(1)}%`;
    },
  },
  {
    title: 'GPU 数',
    dataIndex: 'gpuCount',
    width: 90,
    align: 'center',
    customRender: ({ record }) => record?.status?.gpu_devices?.length ?? 0,
  },
  {
    title: '心跳时间',
    dataIndex: 'heartbeat_time',
    width: 170,
    align: 'center',
  },
];

export const workerSearchFormSchema: FormSchema[] = [
  {
    label: '名称',
    field: 'name',
    component: 'Input',
    colProps: { span: 6 },
  },
  {
    label: '关键字',
    field: 'search',
    component: 'Input',
    colProps: { span: 6 },
  },
];

/** GPU 设备列表列定义 */
export const gpuColumns: BasicColumn[] = [
  {
    title: '型号',
    dataIndex: 'name',
    width: 200,
    align: 'left',
  },
  {
    title: '所属节点',
    dataIndex: 'worker_name',
    width: 140,
    align: 'left',
  },
  {
    title: '厂商/架构',
    dataIndex: 'arch',
    width: 140,
    align: 'center',
    customRender: ({ record }) => `${record.vendor || '-'}/${record.arch_family || '-'}`,
  },
  {
    title: '显存使用率',
    dataIndex: 'memRate',
    width: 120,
    align: 'center',
    customRender: ({ record }) => {
      const rate = record?.memory?.utilization_rate ?? 0;
      return `${Number(rate).toFixed(1)}%`;
    },
  },
  {
    title: '核心使用率',
    dataIndex: 'coreRate',
    width: 120,
    align: 'center',
    customRender: ({ record }) => {
      const rate = record?.core?.utilization_rate ?? 0;
      return `${Number(rate).toFixed(1)}%`;
    },
  },
  {
    title: '温度',
    dataIndex: 'temperature',
    width: 80,
    align: 'center',
    customRender: ({ record }) => `${record.temperature ?? '-'}°C`,
  },
  {
    title: '索引',
    dataIndex: 'index',
    width: 70,
    align: 'center',
  },
];
