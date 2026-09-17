import { FormSchema } from '@/components/Form';
import { getAllRolesListNoByTenant } from '@/views/system/user/user.api';

export const formSchema: FormSchema[] = [
  {
    label: '节点名称',
    field: 'name',
    required: true,
    component: 'Input',
  },
  { label: '描述', field: 'remark', component: 'Input' },
];

export const formSchema1: FormSchema[] = [
  {
    label: '审批人类型',
    field: 'auditType',
    required: true,
    component: 'Select',
    componentProps: {
      options: [
        { value: 'user', label: '指定人员' },
        { value: 'role', label: '指定角色' },
        { value: 'dept', label: '指定部门' },
        { value: 'leader', label: '发起部门负责人' },
      ],
    },
  },
  {
    label: '可审批人员',
    field: 'userIds',
    required: true,
    component: 'UserSelect',
    componentProps: {
      //是否多选
      multi: true,
      //从用户表中选择一列，其值作为该控件的存储值，默认id列
      // store: 'username',
    },
    ifShow: ({ values }) => {
      return values.auditType == 'user';
    },
  },
  {
    label: '可审批角色',
    field: 'roleIds',
    required: true,
    component: 'ApiSelect',
    componentProps: {
      mode: 'multiple',
      api: getAllRolesListNoByTenant,
      labelField: 'roleName',
      valueField: 'id',
      immediate: true,
    },
    ifShow: ({ values }) => {
      return values.auditType == 'role';
    },
  },
  {
    label: '可审批部门',
    field: 'deptIds',
    required: true,
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
    ifShow: ({ values }) => {
      return values.auditType == 'dept';
    },
  },
  {
    label: '所选部门领导',
    field: 'deptsLeader',
    component: 'Switch',
    componentProps: {
      //开关大小，可选值：default small
      size: 'default',
      //非选中时的内容
      unCheckedChildren: '',
      //非选中时的值
      unCheckedValue: 'false',
      //选中时的内容
      checkedChildren: '',
      //选中时的值
      checkedValue: 'true',
    },
    ifShow: ({ values }) => {
      return values.auditType == 'dept';
    },
  },
  {
    label: '归属部门过滤',
    field: 'deptsFilter',
    component: 'Switch',
    componentProps: {
      //开关大小，可选值：default small
      size: 'default',
      //非选中时的内容
      unCheckedChildren: '',
      //非选中时的值
      unCheckedValue: 'false',
      //选中时的内容
      checkedChildren: '',
      //选中时的值
      checkedValue: 'true',
    },
    ifShow: ({ values }) => {
      return values.auditType === 'user' || values.auditType === 'role';
    },
  },
];

export const formSchema2: FormSchema[] = [
  {
    label: '审批人为空时',
    field: 'nullType',
    required: true,
    component: 'RadioGroup',
    componentProps: {
      options: [
        { label: '自动通过', value: 'isSkip' },
        { label: '指定人员审批', value: 'user' },
      ],
    },
  },
  {
    label: '指定人员',
    required: true,
    field: 'alternativeUserIds',
    component: 'UserSelect',
    componentProps: {
      //是否多选
      multi: true,
      //从用户表中选择一列，其值作为该控件的存储值，默认id列
      // store: 'username',
    },
    ifShow: ({ values }) => {
      return values.nullType == 'user';
    },
  },
];

export const getDefaultXml = (processDefinitionKey: any) => {
  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="${processDefinitionKey}" isExecutable="true" camunda:historyTimeToLive="36500">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1u8wf48</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_1u8wf48" sourceRef="StartEvent_1" targetRef="Activity_0jmlhay" />
    <bpmn:userTask id="Activity_0jmlhay" name="发起申请">
      <bpmn:extensionElements />
      <bpmn:incoming>Flow_1u8wf48</bpmn:incoming>
      <bpmn:outgoing>Flow_1op7kw8</bpmn:outgoing>
    </bpmn:userTask>
    <bpmn:endEvent id="Event_1bepgez">
      <bpmn:incoming>Flow_1op7kw8</bpmn:incoming>
    </bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1op7kw8" sourceRef="Activity_0jmlhay" targetRef="Event_1bepgez" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="${processDefinitionKey}">
      <bpmndi:BPMNShape id="_BPMNShape_StartEvent_2" bpmnElement="StartEvent_1">
        <dc:Bounds x="173" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Activity_1y9455k_di" bpmnElement="Activity_0jmlhay">
        <dc:Bounds x="260" y="80" width="100" height="80" />
        <bpmndi:BPMNLabel />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Event_1bepgez_di" bpmnElement="Event_1bepgez">
        <dc:Bounds x="412" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1u8wf48_di" bpmnElement="Flow_1u8wf48">
        <di:waypoint x="209" y="120" />
        <di:waypoint x="260" y="120" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_1op7kw8_di" bpmnElement="Flow_1op7kw8">
        <di:waypoint x="360" y="120" />
        <di:waypoint x="412" y="120" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;
};
