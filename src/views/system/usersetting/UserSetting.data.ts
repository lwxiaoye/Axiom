import { FormSchema } from '/@/components/Form/index';
import { rules } from '/@/utils/helper/validator';

export interface ListItem {
  key: string;
  title: string;
  description: string;
  extra?: string;
  avatar?: string;
  color?: string;
}

/**
 * 用户表单
 */
export const formSchema: FormSchema[] = [
  {
    field: 'realname',
    component: 'Input',
    label: '姓名',
    colProps: { span: 24 },
    required:true
  },
  {
    field: 'birthday',
    component: 'DatePicker',
    label: '生日',
    colProps: { span: 24 },
    componentProps:{
      showTime:false,
      valueFormat:"YYYY-MM-DD",
      getPopupContainer: () => document.body,
    },
  },
  {
    field: 'sex',
    component: 'RadioGroup',
    label: '性别',
    colProps: { span: 24 },
    componentProps:{
      options: [
        {
          label: '男',
          value: 1,
        },
        {
          label: '女',
          value: 2,
        },
      ],
    }
  },
  {
    field: 'relTenantIds',
    component: 'JDictSelectTag',
    label: '租户',
    colProps: { span: 24 },
    componentProps:{
      mode:'multiple',
      dictCode:'sys_tenant,name,id',
      disabled:true
    }
  },
  {
    field: 'post',
    component: 'JDictSelectTag',
    label: '职位',
    colProps: { span: 24 },
    componentProps:{
      mode:'multiple',
      dictCode:'sys_position,name,id',
      disabled:true
    }
  },
  {
    label: '',
    field: 'id',
    component: 'Input',
    show: false,
  },
]

//密码弹窗
export const formPasswordSchema: FormSchema[] = [
  {
    label: '用户账号',
    field: 'username',
    component: 'Input',
    componentProps: { readOnly: true },
  },
  {
    label: '旧密码',
    field: 'oldpassword',
    component: 'InputPassword',
    required: true,
  },
  {
    label: '新密码',
    field: 'password',
    component: 'StrengthMeter',
    componentProps: {
      placeholder: '请输入新密码',
    },
    rules: [
      {
        required: true,
        message: '请输入新密码',
      },
    ],
  },
  {
    label: '确认新密码',
    field: 'confirmpassword',
    component: 'InputPassword',
    dynamicRules: ({ values }) => rules.confirmPassword(values, true),
  },
];
