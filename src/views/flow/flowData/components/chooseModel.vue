<template>
  <BasicModal v-bind="$attrs" @register="registerModal" destroyOnClose :title="title" :width="800" @ok="handleSubmit">
    <BasicForm @register="registerForm" name="selectUserForm" />
  </BasicModal>
</template>

<script lang="ts" setup>
  import { ref, unref } from 'vue';
  import { BasicModal, useModalInner } from '/@/components/Modal';
  import { BasicForm, useForm } from '/@/components/Form/index';
  import { exportFormSchema } from '../FlowData.data';
  import { getToken } from '/@/utils/auth';
  import FileSaver from 'file-saver';
  import axios from 'axios';
  // Emits声明
  const emit = defineEmits(['register', 'success']);
  const isUpdate = ref(true);
  const isDetail = ref(false);
  let chooseValue = ref<any>({});
  //表单配置
  const [registerForm, { setProps, resetFields, setFieldsValue, validate, scrollToField }] = useForm({
    labelWidth: 150,
    schemas: exportFormSchema,
    showActionButtonGroup: false,
    baseColProps: { span: 24 },
  });
  //表单赋值
  const [registerModal, { setModalProps, closeModal }] = useModalInner(async (data) => {
    //重置表单
    await resetFields();
    setModalProps({ confirmLoading: false, showCancelBtn: !!data?.showFooter, showOkBtn: !!data?.showFooter });
    isUpdate.value = !!data?.isUpdate;
    isDetail.value = !!data?.showFooter;
    chooseValue.value = data.record;
    if (unref(isUpdate)) {
      //表单赋值
      await setFieldsValue({
        ...data.record,
      });
    }
    // 隐藏底部时禁用整个表单
    setProps({ disabled: !data?.showFooter });
  });
  let check = false;
  //设置标题
  const title = '选择导出类型';
  //表单提交事件
  async function handleSubmit() {
    
    try {
      let values = await validate();
      console.log(values);
      if (check) {
        return;
      }
      check = true;

      setTimeout(() => {
        check = false
      }, 1000);
      // 提交表单
      const token = getToken();
      axios({
        url: `/api/flow/flowData/app/flow/exportXls`,
        method: 'GET',
        responseType: 'blob',
        headers: {
          'X-Access-Token': token,
        },
        params: {
          ...values,
        },
      })
        .then((response: any) => {
          console.log(response);
          FileSaver.saveAs(response.data, '导出流程应用数据.xls');
        })
        .catch((error: any) => {
          console.error('下载失败:', error);
        });
      //关闭弹窗
      closeModal();
      //刷新列表
      emit('success');
    } catch (error) {
      const { errorFields } = error as { errorFields?: any[] };
      if (errorFields) {
        const firstField = errorFields[0];
        if (firstField) {
          scrollToField(firstField.name, { behavior: 'smooth' });
        }
      }
      return Promise.reject(errorFields);
    } finally {
      setModalProps({ confirmLoading: false });
    }
  }
</script>

<style lang="less" scoped>
  /** 时间和数字输入框样式 */
  :deep(.ant-input-number) {
    width: 100%;
  }

  :deep(.ant-calendar-picker) {
    width: 100%;
  }
</style>
