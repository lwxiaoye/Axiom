<template>
  <BasicModal
    v-bind="$attrs"
    @register="registerModal"
    :title="isUpdate ? '重新发起' : '查看详情'"
    okText="提交"
    :width="1200"
    :maxHeight="700"
    centered
    @ok="onSubmit"
  >
    <template #insertFooter>
      <span style="margin-right: 12px; width: 150px"
        >所属部门：<ApiSelect
          :api="getUserDeparts"
          v-model:value="state.sysDeptId"
          optionFilterProp="所属部门"
          resultField="list"
          labelField="departName"
          valueField="id"
          @change="changeInfo"
      /></span>
    </template>
    <div class="flex height100">
      <div class="rightBox">
        <form-create ref="applyFormRef" v-model="formData" :rule="rule" :option="option" />
      </div>
      <div class="leftBox">
        <div class="mb-8"> <span class="color_red">*</span> 流程审批</div>
<!--        <flowAdd ref="flowAddVue" :processDefinitionKey="processDefinitionKey" />-->
      </div>
    </div>
    <!-- <form-create ref="applyFormRef" v-model="formData" v-model:api="fapi" :rule="rule" :option="option" /> -->
  </BasicModal>
</template>
<script lang="ts" setup>
  import { BasicModal, useModalInner } from '@/components/Modal';
  import { apply, resubmit, queryById } from '../FlowData.api';
  import { reactive, ref, unref } from 'vue';
  import formCreate from '@form-create/ant-design-vue';
  import { ApiSelect } from '@/components/Form';
  import { message } from 'ant-design-vue';
  import { getToken } from '/@/utils/auth';

  import { getUserDeparts } from '@/views/system/depart/depart.api';
  const emit = defineEmits(['success', 'register']);
  let processDefinitionKey = ref<any>({});
  let state = reactive<any>({
    options: [],
    sysDeptId: '',
  });
  const isUpdate = ref(true);

  formCreate.fetch = (options: any) => {
    // 设置请求头，附加 Authorization token
    const headers = {
      ...options.headers,
      'X-Access-Token': getToken(),
    };
    const formData = new FormData();
    formData.append(options.filename, options.file);
    fetch(options.action, {
      method: options.method || 'GET',
      headers: headers,
      body: formData,
    })
      .then((response) => response.json()) // 解析响应为 JSON
      .then((res) => {
        options.onSuccess(res, options.file);
      })
      .catch((error) => {
        if (options.onError) {
          options.onError(error);
        }
      });
  };
  let check = false;
  let flowAddVue = ref<any>(null);
  //表单赋值
  // setModalProps,
  const [registerModal, { closeModal }] = useModalInner(async (data) => {
    console.log('data', data);
    isUpdate.value = !!data?.isUpdate;
    record.value = data.record;
    if (unref(isUpdate)) {
      let info = await queryById(data.record.id);
      processDefinitionKey.value = info.processDefinitionKey || info.record.processDefinitionKey;
      state.sysDeptId = info.sysDeptId;
      formData.value = formCreate.parseJson(info.dataValue);
      option.value.submitBtn = false;
      ruleJson.value = info.rule;
      rule.value = formCreate.parseJson(info.rule);
      option.value = formCreate.parseJson(info.formOptions);
      // 处理上传组件
      rule.value.forEach((item: any) => {
        if (item.type === 'upload') {
          item.props = {
            ...item.props,
            action: '/api/sys/common/upload',
            headers: {
              'X-Access-Token': getToken(),
            },
            onSuccess: (res: any, file: any) => {
              console.log('上传成功', res, file);
              res.url = '/' + res.response.result.path;
            },
          };
        }
      });
      flowAddVue.value.init(processDefinitionKey.value, state.sysDeptId);
    } else {
      formData.value = {};
      getUserDeparts().then((res: any) => {
        let currentId = '';
        res.list.forEach((item: any) => {
          if (item.orgCode == res.orgCode) {
            currentId = item.id;
          }
        });
        if (!currentId) {
          currentId = res.list[0].id;
        }
        state.sysDeptId = currentId;
        flowAddVue.value.init(processDefinitionKey.value, currentId);
      });
      rule.value = formCreate.parseJson(record.value.rule);
      option.value = formCreate.parseJson(record.value.formOptions);
      // 处理上传组件
      rule.value.forEach((item: any) => {
        if (item.type === 'upload') {
          item.props = {
            ...item.props,
            action: '/api/sys/common/upload',
            headers: {
              'X-Access-Token': getToken(),
            },
            onSuccess: (res: any, file: any) => {
              console.log('上传成功', res, file);
              res.url = '/' + res.response.result.path;
            },
          };
        }
      });
    }
  });
  const record: any = ref<any>({});
  const option = ref<any>({});
  const rule = ref<any>([]);
  const ruleJson = ref<any>('');
  // const fapi = ref<any>(null);
  const formData = ref<any>({});
  const applyFormRef = ref<any>(null);
  function changeInfo(row: any) {
    try {
      row.join(',');
    } catch (error) {
      flowAddVue.value.init(processDefinitionKey.value, row);
    }
  }
  async function onSubmit() {
    console.log('1', state.sysDeptId);
    if (!state.sysDeptId) {
      message.warning('表单校验失败');
      return;
    }

    try {
      console.log(applyFormRef.value.fapi.formData());
      const isValid = await applyFormRef.value.fapi.validate();
      let data = record.value;
      if (isValid) {
        console.log('2');
        let nodeAuditUser = {};
        let pass = true;
        let unpassName = '';
        let logs = flowAddVue.value.getLogs();
        console.log('logs123123', logs);
        logs.map((it: any) => {
          if (it.taskName != '发起申请') {
            if (it.startUserSpecify == 'true' && !it.row) {
              pass = false;
              unpassName = it.taskName;
            } else if (it.row && it.startUserSpecify == 'true') {
              try {
                nodeAuditUser[it.taskDefinitionKey] = it.userIds.join(',');
              } catch (error) {
                nodeAuditUser[it.taskDefinitionKey] = it.userIds;
              }
            }
          }
        });
        if (!pass) {
          return message.warning('请选择' + unpassName + '审批人');
        } else {
          data.nodeAuditUser = nodeAuditUser;
        }
        if (check) {
          return;
        }
        check = true;

        setTimeout(() => {
          check = false;
        }, 1000);
        const formDataValue = applyFormRef.value.fapi.formData();
        console.log(formDataValue);
        // data.dataValue = formCreate.toJson(formDataValue);
        data.dataValue = JSON.stringify(formDataValue);
        data.sysDeptId = state.sysDeptId;

        if (unref(isUpdate)) {
          await resubmit(data);
        } else {
          await apply(data);
        }
        emit('success');
        closeModal();
      }
    } catch (error) {
      console.log('3');
      message.warning('表单校验失败');
    }
  }
</script>

<style scoped lang="less">
  .detail-iframe {
    border: 0;
    width: 100%;
    height: 100%;
    min-height: 500px;
    // -update-begin--author:liaozhiyang---date:20240702---for：【TV360X-1685】通知公告查看出现两个滚动条
    display: block;
    // -update-end--author:liaozhiyang---date:20240702---for：【TV360X-1685】通知公告查看出现两个滚动条
  }
  :deep(.scroll-container .scrollbar__wrap) {
    margin-bottom: 0px !important;
  }
  :deep(.ant-modal) {
    top: 50px !important;
  }
  .height100 {
    height: 100%;
    box-sizing: border-box;
  }
  .rightBox {
    width: 800px;
    box-sizing: border-box;
    border-right: 1px solid #ddd;
    padding-right: 8px;
  }
  .leftBox {
    width: 300px;
    height: 100%;
    margin-left: 8px;
    box-sizing: border-box;
  }
  .color_red {
    color: #fc1111;
  }
</style>
