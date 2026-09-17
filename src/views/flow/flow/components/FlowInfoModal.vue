<template>
  <BasicModal v-bind="$attrs" @register="registerModal" destroyOnClose :title="title" :width="1500" :height="contentHeight" @ok="handleSubmit">
    <a-tabs height="100%" v-model:activeKey="activeKey" animated @change="handleChangeTabs" centered>
      <a-tab-pane tab="基本信息" key="basicInfo">
        <BasicForm @register="registerForm" ref="formRef" :style="`height:${height}px;padding-top:12px`" name="FlowInfoForm" />
      </a-tab-pane>
      <a-tab-pane tab="表单设计" key="formInfo">
        <fc-designer ref="designerRef" :config="{}" style="margin-top: 20px !important" :value="formData" :height="`${height}px`" />
      </a-tab-pane>
      <a-tab-pane tab="流程设计" key="flowInfo">
        <BpmnView ref="bpmRef" :processDefinitionKey="processDefinitionKey" :style="`height:${height}px`" />
      </a-tab-pane>
    </a-tabs>
  </BasicModal>
</template>

<script lang="ts" setup>
  import { ref, computed, onMounted, unref } from 'vue';
  import BpmnView from '@/views/flow/flow/components/BpmnView.vue';
  import { BasicModal, useModalInner } from '/src/components/Modal';
  import { BasicForm, useForm } from '/src/components/Form';
  import { formSchema } from '../FlowInfo.data';
  import { queryFlowDept, queryFlowRole, saveOrUpdate } from '../FlowInfo.api';
  import type { FcDesignerInstance } from '@fcdesigner/vue';
  import formCreate from '@form-create/ant-design-vue';
  const designerRef = ref<FcDesignerInstance>();
  const bpmRef = ref(null);
  const formData = ref({});
  // Emits声明
  const emit = defineEmits(['register', 'success']);
  const isUpdate = ref(true);
  const isDetail = ref(false);
  const refKeys = ref(['basicInfo', 'formInfo', 'flowInfo']);
  const activeKey = ref('basicInfo');
  const editData = ref(null);
  const height = ref(0);
  const contentHeight = ref(0);
  let props = defineProps<{
    processDefinitionKey: string; //流程id
  }>();
  const tableRefs = {};
  //表单配置
  const [registerForm, { setProps, resetFields, setFieldsValue, validate, scrollToField }] = useForm({
    labelWidth: 150,
    schemas: formSchema,
    showActionButtonGroup: false,
    baseColProps: { span: 24 },
  });
  //表单赋值
  const [registerModal, { setModalProps, closeModal }] = useModalInner(async (data) => {
    console.log('innerHeight', window.innerHeight);
    contentHeight.value = window.innerHeight - 106;
    height.value = window.innerHeight - 106 - 12 - 46;
    console.log('1111', height.value);
    //重置表单
    await resetFields();
    activeKey.value = 'basicInfo';
    setModalProps({
      height: window.innerHeight - 106,
      centered: true,
      wrapperFooterOffset: 10,
      defaultFullscreen: true,
      confirmLoading: false,
      showCancelBtn: !!data?.showFooter,
      showOkBtn: !!data?.showFooter,
    });
    isUpdate.value = !!data?.isUpdate;
    isDetail.value = !!data?.showFooter;
    editData.value = null;
    if (unref(isUpdate)) {
      editData.value = data.record;
      const flowRoles = await queryFlowRole({ flowId: data.record.id });
      if (flowRoles && flowRoles.length > 0) {
        data.record.selectedRoles = flowRoles;
      }
      const flowDepts = await queryFlowDept({ flowId: data.record.id });
      if (flowDepts && flowDepts.length > 0) {
        data.record.selectedDeparts = flowDepts;
      }
      console.log('data.record', data.record);
    }
    //表单赋值
    await setFieldsValue({
      ...data.record,
    });
    // 隐藏底部时禁用整个表单
    setProps({ disabled: !data?.showFooter });
  });

  //方法配置
  function handleChangeTabs(e: any) {
    if (e === 'formInfo') {
      if (editData.value) {
        setTimeout(() => {
          designerRef.value.setRule(formCreate.parseJson(editData.value.rule));
          designerRef.value.setOptions(formCreate.parseJson(editData.value.formOptions));
          editData.value = null;
        }, 0);
      }
    }
  }

  onMounted(() => {
    try {
      console.log('editData', designerRef);
      // 示例：从服务器端获取保存的JSON规则
      // if (unref(isUpdate)) {
      //   // 回显设计的表单
      //   designerRef.value.setOptions(optionsJson);
      //   designerRef.value.setRule(ruleJson);
      // }
    } catch (error) {
      console.error('加载表单数据失败', error);
    }
  });

  //设置标题
  const title = computed(() => (!unref(isUpdate) ? '新增' : !unref(isDetail) ? '详情' : '编辑'));

  function classifyIntoFormData(allValues) {
    let main = Object.assign({}, allValues.formValue);
    return {
      ...main, // 展开
    };
  }

  async function handleSubmit() {
    let values = await validate();
    if (designerRef.value) {
      values.formOptions = formCreate.toJson(designerRef.value.getOptions());
      values.rule = formCreate.toJson(designerRef.value.getRule());
    } else if (editData.value) {
      values.formOptions = editData.value.formOptions;
      values.rule = editData.value.rule;
    }
    if (bpmRef.value) {
      bpmRef.value.getFile(async (xml) => {
        values.file = xml; //new File([new Blob([xml])], 'new.bpmn');
        await saveOrUpdate(values);
        closeModal();
        emit('success');
      });
    } else {
      await saveOrUpdate(values);
      closeModal();
      emit('success');
    }
  }

  // //表单提交事件
  // async function handleSubmit(v) {
  //   try {
  //     let values = await validate();
  //     setModalProps({ confirmLoading: true });
  //     //提交表单
  //     await saveOrUpdate(values, isUpdate.value);
  //     //关闭弹窗
  //     closeModal();
  //     //刷新列表
  //     emit('success');
  //   } catch ({ errorFields }) {
  //     if (errorFields) {
  //       const firstField = errorFields[0];
  //       if (firstField) {
  //         scrollToField(firstField.name, { behavior: 'smooth', block: 'center' });
  //       }
  //     }
  //     return Promise.reject(errorFields);
  //   } finally {
  //     setModalProps({ confirmLoading: false });
  //   }
  // }
</script>

<style lang="less" scoped>
  /** 时间和数字输入框样式 */
  :deep(.ant-input-number) {
    width: 100%;
  }

  :deep(.ant-calendar-picker) {
    width: 100%;
  }

  //:deep(.ant-tabs-nav) {
  //  margin-bottom: 0px !important;
  //}
  //
  //:deep(.ant-modal-body) :deep(.scroll-container) {
  //  padding: 0px !important;
  //}
</style>
