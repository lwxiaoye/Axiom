import { BasicColumn } from '@/components/Table';
import { FormSchema } from '@/components/Table';
// import { rules } from '/src/utils/helper/validator';
// import { getWeekMonthQuarterYear } from '/src/utils';
import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';
import { UploadTypeEnum } from '/@/components/Form/src/jeecg/components/JUpload';
import { APP_CAPABILITY_OPTIONS } from './appCapabilityOptions';
//列表数据
export const columns: BasicColumn[] = [
  // {
  //   title: '应用编号',
  //   align: 'center',
  //   dataIndex: 'id',
  // },
  {
    title: '应用名称',
    align: 'center',
    dataIndex: 'appName',
  },
  // {
  //   title: '是否推荐',
  //   align: 'center',
  //   dataIndex: 'isRecommend',
  //   customRender: ({ text }) => {
  //     return render.renderSwitch(text, [
  //       { text: '是', value: 'Y' },
  //       { text: '否', value: 'N' },
  //     ]);
  //   },
  // },
  {
    title: '是否启用',
    align: 'center',
    dataIndex: 'status',
    slots: { customRender: 'statusSwitch' },
  },
  {
    title: 'H5跳转地址',
    align: 'center',
    dataIndex: 'h5Url',
  },
  {
    title: 'PC跳转地址',
    align: 'center',
    dataIndex: 'pcUrl',
  },
  {
    title: '应用来源',
    align: 'center',
    customRender: ({ record }) => {
      const sourceLabels: Record<string, string> = {
        external: '外部接入应用',
        custom: '自建业务应用',
        agent: '智能体应用',
      };
      return sourceLabels[record.appType] || '未分类';
    },
  },
  {
    title: '能力分类',
    align: 'center',
    dataIndex: 'appCategory_dictText',
    // component: 'JSelectMultiple',
    // componentProps: {
    //   dictCode: 'appCategory',
    // },
  },
  {
    title: '可见角色',
    align: 'center',
    dataIndex: 'selectedRoles_dictText',
  },
  {
    title: '可见部门',
    align: 'center',
    dataIndex: 'selectedDeparts_dictText',
  },
  {
    title: '排序码',
    align: 'center',
    dataIndex: 'orderNum',
  },
];
//查询数据
export const searchFormSchema: FormSchema[] = [
  {
    label: '应用名称',
    field: 'appName',
    component: 'JInput',
  },
  {
    label: '应用来源',
    field: 'appType',
    component: 'Select',
    componentProps: {
      options: [
        { label: '外部接入应用', value: 'external' },
        { label: '自建业务应用', value: 'custom' },
        { label: '智能体应用', value: 'agent' },
      ],
    },
  },
  {
    label: '是否启用',
    field: 'status',
    component: 'Select',
    componentProps: {
      options: [
        {
          label: '启用',
          value: '1',
        },
        {
          label: '停用',
          value: '0',
        },
      ],
    },
  },
  
  // {
  //   label: '是否推荐应用',
  //   field: 'isRecommend',
  //   component: 'Select',
  //   componentProps: {
  //     options: [
  //       {
  //         label: '是',
  //         value: 'Y',
  //       },
  //       {
  //         label: '否',
  //         value: 'N',
  //       },
  //     ],
  //   },
  // },
  {
    label: '能力分类',
    field: 'appCategory',
    component: 'Select',
    componentProps: {
      options: APP_CAPABILITY_OPTIONS,
      showSearch: true,
      optionFilterProp: 'label',
      allowClear: true,
    },
  },
  // {
  //   label: '创建人',
  //   field: 'createBy',
  //   component: 'JInput',
  // },
];
//表单数据
export const formSchema: FormSchema[] = [
  {
    label: '应用名称',
    field: 'appName',
    component: 'Input',
    dynamicRules: () => {
      return [{ required: true, message: '请输入应用名称!' }];
    },
  },
  {
    label: '终端类型',
    field: 'terminalType',
    component: 'JSelectMultiple',
    componentProps: {
      dictCode: 'terminal_type',
    },
    required: true,
  },
  {
    label: '能力分类',
    field: 'appCategory',
    component: 'Select',
    componentProps: {
      options: APP_CAPABILITY_OPTIONS,
      showSearch: true,
      optionFilterProp: 'label',
      allowClear: true,
    },
    required: true,
  },
  {
    label: 'H5跳转地址',
    field: 'h5Url',
    component: 'Input',
  },
  {
    label: 'PC跳转地址',
    field: 'pcUrl',
    component: 'Input',
  },
    {
    label: '接口地址',
    field: 'baseUrl',
    component: 'Input',
  },
  {
    label: 'apiKey',
    field: 'apiKey',
    component: 'InputPassword',
  },
  {
    label: '是否推荐应用',
    field: 'isRecommend',
    component: 'JSwitch',
    componentProps: {},
    defaultValue: 'N',
    show: false,
  },
  {
    label: '状态',
    field: 'status',
    component: 'JDictSelectTag',
    componentProps: {
      dictCode: 'status',
      type: 'radio',
    },
    required: true,
  },
  {
    label: '应用描述',
    field: 'appRemark',
    component: 'InputTextArea',
  },
  {
    label: '应用角色权限',
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
    label: '应用部门权限',
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
  {
    label: '应用图标',
    field: 'appIcon',
    component: 'JUpload',
    componentProps: {
      fileType: UploadTypeEnum.image,
      maxCount:1
    },
  },
  {
    label: '排序码',
    field: 'orderNum',
    component: 'InputNumber',
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
  appName: { title: '应用名称', order: 0, view: 'text', type: 'string' },
  appRemark: { title: '应用描述', order: 1, view: 'text', type: 'string' },
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
