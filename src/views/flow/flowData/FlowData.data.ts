import { BasicColumn } from '/@/components/Table';
import { FormSchema } from '/@/components/Table';
// import { rules } from '/@/utils/helper/validator';
// import { render } from '/@/utils/common/renderUtils';
// import { getWeekMonthQuarterYear } from '/@/utils';
// import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';
import { getProcessList } from '@/views/flow/app/AppInfo.api';
import { getProcessLists } from './FlowData.api';

//查询数据
export const columns: BasicColumn[] = [
  {
    title: '流程名称',
    align: 'center',
    dataIndex: 'processDefinitionName',
  },
  {
    title: '发起人',
    align: 'center',
    dataIndex: 'createId_dictText',
  },
  {
    title: '所属部门',
    align: 'center',
    dataIndex: 'sysDeptId_dictText',
  },
  {
    title: '发起时间',
    align: 'center',
    dataIndex: 'createTime',
  },
  {
    title: '流程状态',
    align: 'center',
    customRender: ({ record }) => {
      return record.status === 'running' ? '进行中' : record.status === 'passed' ? '已通过' : '已关闭';
    },
  },
  {
    title: '当前节点',
    align: 'center',
    dataIndex: 'activityName',
  },
  {
    title: '节点开始时间',
    align: 'center',
    dataIndex: 'startTime',
  },
  {
    title: '节点负责人',
    align: 'center',
    dataIndex: 'assignee_dictText',
  },
];
//列表数据
export const myToDoColumns: BasicColumn[] = [
  {
    title: '流程名称',
    align: 'center',
    dataIndex: 'appName',
  },
  {
    title: '发起人',
    align: 'center',
    dataIndex: 'createBy_dictText',
  },
  {
    title: '所属部门',
    align: 'center',
    dataIndex: 'sysDeptId_dictText',
  },
  {
    title: '发起时间',
    align: 'center',
    dataIndex: 'createTime',
  },
  {
    title: '当前节点',
    align: 'center',
    dataIndex: 'activityName',
  },
  {
    title: '节点开始时间',
    align: 'center',
    dataIndex: 'startTime',
  },
  // {
  //   title: '流程状态',
  //   align: 'center',
  //   dataIndex: 'status',
  // },
  // {
  //   title: '流程实例id',
  //   align: 'center',
  //   dataIndex: 'processInstanceId',
  // },
];

export const searchFormTodoSchema: FormSchema[] = [
  {
    label: '流程名称',
    field: 'processDefinitionKey',
    component: 'ApiSelect',
    componentProps: {
      api: getProcessList,
      labelField: 'name',
      valueField: 'processDefinitionKey',
      immediate: true,
    },
  },
  {
    label: '部门',
    field: 'departId',
    component: 'JSearchSelect',
    componentProps: {
      dict: 'sys_depart,depart_name,id',
    },
    //colProps: {span: 6},
  },
  {
    label: '发起人',
    field: 'startUserId',
    // 自定义组件
    component: 'JSelectUser',
    componentProps: {
      isRadioSelection: true,
    },
  },
];

//查询数据
export const searchFormSchema: FormSchema[] = [
  {
    label: '流程名称',
    field: 'processDefinitionKey',
    component: 'ApiSelect',
    componentProps: {
      api: getProcessList,
      labelField: 'name',
      valueField: 'processDefinitionKey',
      immediate: true,
    },
  },
  {
    label: '部门',
    field: 'departId',
    component: 'JSearchSelect',
    componentProps: {
      dict: 'sys_depart,depart_name,id',
    },
    //colProps: {span: 6},
  },
  {
    label: '发起人',
    field: 'startUserId',
    // 自定义组件
    component: 'JSelectUser',
    componentProps: {
      isRadioSelection: true,
    },
  },
  {
    label: '流程状态',
    field: 'state',
    // 自定义组件
    component: 'Select',
    componentProps: {
      options: [
        {
          label: '进行中',
          value: 'ACTIVE',
          key: 'ACTIVE',
        },
        {
          label: '已通过',
          value: 'COMPLETED',
          key: 'COMPLETED',
        },
        {
          label: '已关闭',
          value: 'INTERNALLY_TERMINATED',
          key: 'INTERNALLY_TERMINATED',
        },
      ],
    },
  },
];
//查询数据
export const searchFormSchemas: FormSchema[] = [
  {
    label: '流程名称',
    field: 'processDefinitionKey',
    component: 'ApiSelect',
    componentProps: {
      api: getProcessList,
      labelField: 'name',
      valueField: 'processDefinitionKey',
      immediate: true,
    },
  },
  {
    label: '部门',
    field: 'departId',
    component: 'JSearchSelect',
    componentProps: {
      dict: 'sys_depart,depart_name,id',
    },
    //colProps: {span: 6},
  },
];
//表单数据
export const formSchema: FormSchema[] = [
  {
    label: '流程数据',
    field: 'dateValue',
    component: 'Input',
  },
  {
    label: '表单options',
    field: 'options',
    component: 'Input',
  },
  {
    label: '表单渲染rule',
    field: 'rule',
    component: 'Input',
  },
  {
    label: '应用id',
    field: 'appId',
    component: 'Input',
  },
  {
    label: '流程状态',
    field: 'status',
    component: 'Input',
  },
  {
    label: '流程实例id',
    field: 'processInstanceId',
    component: 'Input',
  },
  // TODO 主键隐藏字段，目前写死为ID
  {
    label: '',
    field: 'id',
    component: 'Input',
    show: false,
  },
];

export const myDoColumn: BasicColumn[] = [
  {
    title: '流程名称',
    align: 'center',
    dataIndex: 'processDefinitionName',
  },
  {
    title: '发起人',
    align: 'center',
    dataIndex: 'createId_dictText',
  },
  {
    title: '所属部门',
    align: 'center',
    dataIndex: 'sysDeptId_dictText',
  },
  {
    title: '发起时间',
    align: 'center',
    dataIndex: 'createTime',
  },
  {
    title: '办理节点',
    align: 'center',
    dataIndex: 'taskName',
  },
  {
    title: '办理结果',
    align: 'center',
    customRender: ({ record }) => {
      return record.processResult === 'passed' ? '通过' : record.processResult === 'reject' ? '驳回' : '--';
    },
  },
  {
    title: '节点开始时间',
    align: 'center',
    dataIndex: 'startTime',
  },
  {
    title: '节点结束时间',
    align: 'center',
    dataIndex: 'endTime',
  },
];

export const myStartColumn: BasicColumn[] = [
  {
    title: '流程名称',
    align: 'center',
    dataIndex: 'processDefinitionName',
  },
  {
    title: '所属部门',
    align: 'center',
    dataIndex: 'sysDeptId_dictText',
  },
  {
    title: '发起时间',
    align: 'center',
    dataIndex: 'createTime',
  },
  {
    title: '流程状态',
    align: 'center',
    customRender: ({ record }) => {
      return record.status === 'running' ? '进行中' : record.status === 'passed' ? '已通过' : '已关闭';
    },
  },
  {
    title: '当前节点',
    align: 'center',
    dataIndex: 'activityName',
  },
  {
    title: '当前节点负责人',
    align: 'center',
    dataIndex: 'assignee_dictText',
  },
];

// 指派的表单
export const assignFormSchema: FormSchema[] = [
  {
    label: '指派人员',
    required: true,
    field: 'userIds',
    component: 'UserSelect',
    componentProps: {
      //是否多选
      multi: true,
      //从用户表中选择一列，其值作为该控件的存储值，默认id列
      // store: 'username',
    },
    // ifShow: ({ values }) => {
    //   return values.nullType == 'user';
    // },
  },
  // {
  //   label: '指派用户',
  //   field: 'assignee',
  //   component: 'ApiSelect',
  //   componentProps: {
  //     api: getSysUserList,
  //     labelField: 'name',
  //     valueField: 'id',
  //     immediate: false,
  //   },
  // },
];

// 导出查询表单
export const exportFormSchema: FormSchema[] = [
  {
    label: '导出流程类型',
    field: 'processDefinitionKey',
    component: 'ApiSelect',
    componentProps: {
      api: getProcessLists,
      labelField: 'appName',
      valueField: 'processDefinitionKey',
      immediate: true,
    },
    required: true,
  },
];

// 高级查询数据
export const superQuerySchema = {
  dateValue: { title: '流程数据', order: 0, view: 'text', type: 'string' },
  options: { title: '表单options', order: 1, view: 'text', type: 'string' },
  rule: { title: '表单渲染rule', order: 2, view: 'text', type: 'string' },
  appId: { title: '应用id', order: 3, view: 'text', type: 'string' },
  status: { title: '流程状态', order: 4, view: 'text', type: 'string' },
  processInstanceId: { title: '流程实例id', order: 5, view: 'text', type: 'string' },
};

/**
 * 流程表单调用这个方法获取formSchema
 * @param param
 */
export function getBpmFormSchema(_formData): FormSchema[] {
  // 默认和原始表单保持一致 如果流程中配置了权限数据，这里需要单独处理formSchema
  return formSchema;
}
