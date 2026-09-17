<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable">
      <!--插槽:table标题-->
      <template #tableTitle>
        <a-button type="primary" v-auth="'app:app_info:manager'" @click="handleAdd" preIcon="ant-design:plus-outlined"> 新增</a-button>
      </template>
      <template #statusSwitch="{ record }">
        <a-tag v-if="getManagedBuiltinCatalogId(record)" :color="isAppEnabled(record) ? 'success' : 'default'">
          {{ isAppEnabled(record) ? '已启用' : '已停用' }}
        </a-tag>
        <a-popconfirm
          v-else
          :title="getStatusConfirmTitle(record)"
          ok-text="确定"
          cancel-text="取消"
          placement="top"
          @confirm="handleStatusChange(record)"
        >
          <a-switch
            :checked="isAppEnabled(record)"
            :loading="isStatusChanging(record)"
            :disabled="isStatusChanging(record)"
            checked-children="启用"
            un-checked-children="停用"
          />
        </a-popconfirm>
      </template>
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" :dropDownActions="getDropDownAction(record)" />
      </template>
    </BasicTable>
    <!-- 表单区域 -->
    <chooseModels ref="formModels" @success="handleSuccess" />
    <BasicModal v-bind="$attrs" @register="registerModal" :title="title" okText="提交" :width="800" :maxHeight="800" @ok="onSubmit">
      <form-create ref="applyFormRef" v-model="formData" :rule="rule" :option="option" />
      <template #insertFooter>
        <div class="selectDept">
          <span class="mr-4">所属部门</span>
          <ApiSelect
            class="flex-1"
            :api="getUserDeparts"
            v-model:value="sysDeptId"
            optionFilterProp="所属部门"
            resultField="list"
            labelField="departName"
            valueField="id"
          />
        </div>
      </template>
    </BasicModal>
  </div>
</template>

<script lang="ts" name="flow-flowInfo" setup>
  import { ref, reactive } from 'vue';
  import { useRouter } from 'vue-router';
  import { getManagedBuiltinCatalogId } from '../../workflow/manage/builtinManagement';
  import { BasicTable, TableAction } from '@/components/Table';
  import { BasicModal } from '@/components/Modal';
  import { useModal } from '@/components/Modal';
  import { ApiSelect } from '@/components/Form';
  import { useMessage } from '@/hooks/web/useMessage';
  import formCreate from '@form-create/ant-design-vue';
  import { useListPage } from '@/hooks/system/useListPage';
  import { columns, searchFormSchema } from './AppInfo.data';
  import { list, deleteOne, getImportUrl, getExportUrl, updateStatus } from './AppInfo.api';
  import { getUserDeparts } from '@/views/system/depart/depart.api';
  import { apply } from '../flowData/FlowData.api';
  // , resubmit, queryById
  // 引入新增自定义弹窗
  import chooseModels from './components/chooseModel.vue';
  const queryParam = reactive<any>({
    isRecommend: 'N',
  });

  const { createMessage } = useMessage();
  const router = useRouter();
  let sysDeptId = ref<any>(null);
  const statusChangingIds = ref<string[]>([]);
  //注册table数据
  const { tableContext } = useListPage({
    tableProps: {
      title: '应用管理',
      api: list,
      columns,
      canResize: false,
      formConfig: {
        //labelWidth: 120,
        schemas: searchFormSchema,
        autoSubmitOnEnter: true,
        showAdvancedButton: true,
        fieldMapToNumber: [],
        fieldMapToTime: [],
      },
      actionColumn: {
        width: 180,
        fixed: 'right',
      },
      beforeFetch: (params) => {
        return Object.assign(params, queryParam);
      },
    },
    exportConfig: {
      name: '应用管理',
      url: getExportUrl,
      params: queryParam,
    },
    importConfig: {
      url: getImportUrl,
      success: handleSuccess,
    },
  });

  let formModels = ref<any>(null);
  const [registerTable, { reload }, { selectedRowKeys }] = tableContext;

  // 高级查询配置
  // const superQueryConfig = reactive(superQuerySchema);

  /**
   * 高级查询事件
   */
  // function handleSuperQuery(params) {
  //   Object.keys(params).map((k) => {
  //     queryParam[k] = params[k];
  //   });
  //   reload();
  // }
  /**
   * 新增事件
   */
  function handleAdd() {
    formModels.value.init(null, false);
  }
  /**
   * 编辑事件
   */
  async function handleEdit(record: Recordable) {
    try {
      await formModels.value.init(record, false);
    } catch {
      createMessage.error('应用配置或权限暂时无法加载，请重试');
    }
  }
  /**
   * 详情
   */
  async function handleDetail(record: Recordable) {
    try {
      await formModels.value.init(record, true);
    } catch {
      createMessage.error('应用配置或权限暂时无法加载，请重试');
    }
    // openModal(true, {
    //   record,
    //   isUpdate: true,
    //   showFooter: false,
    // });
  }
  /**
   * 删除事件
   */
  async function handleDelete(record) {
    await deleteOne({ id: record.id }, handleSuccess);
  }
  /**
   * 批量删除事件
   */
  // async function batchHandleDelete() {
  //   await batchDelete({ ids: selectedRowKeys.value }, handleSuccess);
  // }
  /**
   * 成功回调
   */
  function handleSuccess() {
    (selectedRowKeys.value = []) && reload();
  }

  function isAppEnabled(record: Recordable) {
    return String(record?.status) === '1';
  }

  function getNextStatus(record: Recordable) {
    return isAppEnabled(record) ? '0' : '1';
  }

  function getStatusConfirmTitle(record: Recordable) {
    return `确定${getNextStatus(record) === '1' ? '启用' : '停用'}「${record?.appName || '该应用'}」吗？`;
  }

  function isStatusChanging(record: Recordable) {
    return statusChangingIds.value.includes(String(record?.id || ''));
  }

  async function handleStatusChange(record: Recordable) {
    const id = String(record?.id || '');
    if (!id || isStatusChanging(record)) return;
    const status = getNextStatus(record);
    statusChangingIds.value = [...statusChangingIds.value, id];
    try {
      await updateStatus({ id, status });
      reload();
    } finally {
      statusChangingIds.value = statusChangingIds.value.filter((item) => item !== id);
    }
  }

  /**
   * 操作栏
   */
  function getTableAction(record: any) {
    const managedId = getManagedBuiltinCatalogId(record);
    if (managedId) {
      return [{ label: '智能体管理', onClick: () => router.push({ path: '/workflow/manage', query: { appId: managedId } }) }];
    }
    console.log('record', record);
    if (record.appType == 'flow') {
      return [
        {
          label: '编辑',
          onClick: handleEdit.bind(null, record),
          auth: 'app:app_info:manager',
        },
        {
          label: '发起申请',
          onClick: addApply.bind(null, record),
        },
      ];
    } else {
      return [
        {
          label: '编辑',
          onClick: handleEdit.bind(null, record),
          auth: 'app:app_info:manager',
        },
      ];
    }
  }
  // 发起申请
  /**
   * 发起申请
   */
  import { getToken } from '/@/utils/auth';
  const [registerModal, { openModal, closeModal }] = useModal();
  // 表单数据
  let formData = ref<any>({});
  let title = ref<any>('');
  const rule = ref<any>([]);
  let processDefinitionKey = ref<any>({});
  const option = ref<any>({
    submitBtn: false, // 不显示默认提交按钮
    form: {
      labelPosition: 'right',
      labelWidth: '150px',
    },
  });

  // 发起申请函数修改
  function addApply(record: any) {
    console.log('record', record);
    title.value = '发起' + record.appName;
    processDefinitionKey.value = record.processDefinitionKey;
    try {
      // 解析表单规则
      rule.value = formCreate.parseJson(record.rule);

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
        sysDeptId.value = currentId;
      });
      // 打开模态框
      openModal(true);
    } catch (error) {
      console.error('表单初始化失败', error);
    }
  }
  /**
   * 下拉操作栏
   */
  function getDropDownAction(record) {
    if (getManagedBuiltinCatalogId(record)) return [];
    return [
      {
        label: '详情',
        onClick: handleDetail.bind(null, record),
      },
      {
        label: '删除',
        popConfirm: {
          title: '是否确认删除',
          confirm: handleDelete.bind(null, record),
          placement: 'topLeft',
        },
        auth: 'app:app_info:delete',
      },
    ];
  }
  let applyFormRef = ref<any>(null);
  async function onSubmit() {
    const isValid = await applyFormRef.value.fapi.validate();
    if (isValid) {
      console.log('2');

      const formData = applyFormRef.value.fapi.formData();
      console.log(formData);
      let data: any = {};
      data.dataValue = formCreate.toJson(formData);
      data.sysDeptId = sysDeptId.value;
      data.processDefinitionKey = processDefinitionKey.value;
      data.formOptions = formCreate.toJson(rule.value);
      // if (unref(isUpdate)) {
      //   await resubmit(data);
      // } else {
      await apply(data);
      // }
      // emit('success');
      closeModal();
    }
  }
</script>

<style lang="less" scoped>
  :deep(.ant-picker),
  :deep(.ant-input-number) {
    width: 100%;
  }
  .selectDept {
    margin-right: 12px;
    display: inline-block;
    text-align: left;
  }
  .flex-1 {
    width: calc(100% - 64px) !important;
  }
</style>
