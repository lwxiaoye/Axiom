import { BasicColumn } from '/src/components/Table';
import { FormSchema } from '/src/components/Table';
import { rules } from '/src/utils/helper/validator';
import { render } from '/src/utils/common/renderUtils';
import { getWeekMonthQuarterYear } from '/src/utils';
import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';
//列表数据
export const columns: BasicColumn[] = [
  {
    title: '流程名称',
    align: 'center',
    dataIndex: 'flowName',
  },
  {
    title: '流程描述',
    align: 'center',
    dataIndex: 'flowRemark',
  },
  {
    title: '创建人',
    align: 'center',
    dataIndex: 'createBy_dictText',
  },
  {
    title: '创建日期',
    align: 'center',
    dataIndex: 'createTime',
  },
];
//查询数据
export const searchFormSchema: FormSchema[] = [
  {
    label: '流程名称',
    field: 'flowName',
    component: 'JInput',
  },
  {
    label: '创建人',
    field: 'createBy',
    component: 'JInput',
  },
];
//表单数据
export const formSchema: FormSchema[] = [
  {
    label: '流程名称',
    field: 'flowName',
    component: 'Input',
    dynamicRules: ({ model, schema }) => {
      return [{ required: true, message: '请输入流程名称!' }];
    },
  },
  {
    label: '流程描述',
    field: 'flowRemark',
    component: 'InputTextArea',
  },
  {
    label: '可发起角色',
    field: 'selectedRoles',
    component: 'ApiSelect',
    componentProps: {
      mode: 'multiple',
      api: getAllRolesListNoByTenant,
      labelField: 'roleName',
      valueField: 'id',
      immediate: true,
    },
  },
  {
    label: '可发起部门',
    field: 'selectedDeparts',
    component: 'JSelectDept',
    componentProps: ({ formActionType, formModel }) => {
      return {
        sync: false,
        checkStrictly: true,
        defaultExpandLevel: 2,

        onSelect: (options, values) => {
          const { updateSchema } = formActionType;
          //所属部门修改后更新负责部门下拉框数据
          updateSchema([
            {
              field: 'departIds',
              componentProps: { options },
            },
          ]);
          //update-begin---author:wangshuai---date:2024-05-11---for:【issues/1222】用户编辑界面“所属部门”与“负责部门”联动出错整---
          if (!values) {
            formModel.departIds = [];
            return;
          }
          //update-end---author:wangshuai---date:2024-05-11---for:【issues/1222】用户编辑界面“所属部门”与“负责部门”联动出错整---
          //所属部门修改后更新负责部门数据
          formModel.departIds && (formModel.departIds = formModel.departIds.filter((item) => values.value.indexOf(item) > -1));
        },
      };
    },
  },
  // TODO 主键隐藏字段，目前写死为ID
  {
    label: '',
    field: 'id',
    component: 'Input',
    show: false,
  },
  {
    label: '',
    field: 'processDefinitionKey',
    component: 'Input',
    show: false,
  },
];

// 高级查询数据
export const superQuerySchema = {
  flowName: { title: '流程名称', order: 0, view: 'text', type: 'string' },
  flowRemark: { title: '流程描述', order: 1, view: 'text', type: 'string' },
  createBy: { title: '创建人', order: 2, view: 'text', type: 'string' },
  createTime: { title: '创建日期', order: 3, view: 'datetime', type: 'string' },
};

/**
 * 流程表单调用这个方法获取formSchema
 * @param param
 */
export function getBpmFormSchema(_formData): FormSchema[] {
  // 默认和原始表单保持一致 如果流程中配置了权限数据，这里需要单独处理formSchema
  return formSchema;
}
