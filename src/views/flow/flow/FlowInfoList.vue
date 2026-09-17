<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable" :rowSelection="rowSelection">
      <!--插槽:table标题-->
      <template #tableTitle>
        <a-button type="primary" v-auth="'app:app_info:manager'" @click="handleAdd" preIcon="ant-design:plus-outlined"> 新增</a-button>
        <!-- 高级查询 -->
        <super-query :config="superQueryConfig" @search="handleSuperQuery" />
      </template>
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" :dropDownActions="getDropDownAction(record)" />
      </template>
      <!--字段回显插槽-->
      <template #bodyCell="{ column, record, index, text }"> </template>
    </BasicTable>
    <!-- 表单区域 -->
    <FlowInfoModal @register="registerModal" :processDefinitionKey="processDefinitionKey" @success="handleSuccess" />
    <FlowApplyModal @register="registerModal1" />
  </div>
</template>

<script lang="ts" name="flow-flowInfo" setup>
  import { ref, reactive, computed, unref } from 'vue';
  import { BasicTable, useTable, TableAction } from '/src/components/Table';
  import { useModal } from '/src/components/Modal';
  import { useListPage } from '/src/hooks/system/useListPage';
  import FlowInfoModal from './components/FlowInfoModal.vue';
  import FlowApplyModal from '../flowData/components/FlowApplyModal.vue';
  import { columns, searchFormSchema, superQuerySchema } from './FlowInfo.data';
  import { list, deleteOne, batchDelete, getImportUrl, getExportUrl, queryFlowRole } from './FlowInfo.api';
  import { downloadFile } from '/src/utils/common/renderUtils';
  import { getTIME } from '/src/utils/dateUtil';
  import { useUserStore } from '/src/store/modules/user';
  const queryParam = reactive<any>({});
  const checkedKeys = ref<Array<string | number>>([]);
  const userStore = useUserStore();
  const processDefinitionKey = ref('');
  //注册model
  const [registerModal, { openModal }] = useModal();
  const [registerModal1, { openModal: openModal1 }] = useModal();
  //注册table数据
  const { prefixCls, tableContext, onExportXls, onImportXls } = useListPage({
    tableProps: {
      title: '应用流程',
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
      name: '应用流程',
      url: getExportUrl,
      params: queryParam,
    },
    importConfig: {
      url: getImportUrl,
      success: handleSuccess,
    },
  });

  const [registerTable, { reload }, { rowSelection, selectedRowKeys }] = tableContext;

  // 高级查询配置
  const superQueryConfig = reactive(superQuerySchema);

  /**
   * 高级查询事件
   */
  function handleSuperQuery(params) {
    Object.keys(params).map((k) => {
      queryParam[k] = params[k];
    });
    reload();
  }
  /**
   * 新增事件
   */
  function handleAdd() {
    processDefinitionKey.value = 'Flow' + getTIME();
    console.log('add', processDefinitionKey.value);
    openModal(true, {
      record: {
        processDefinitionKey: processDefinitionKey.value,
      },
      isUpdate: false,
      showFooter: true,
    });
  }
  /**
   * 编辑事件
   */
  function handleEdit(record: Recordable) {
    processDefinitionKey.value = record.processDefinitionKey;
    console.log('edit', processDefinitionKey.value);
    openModal(true, {
      record,
      isUpdate: true,
      showFooter: true,
    });
  }
  /**
   * 详情
   */
  function handleDetail(record: Recordable) {
    openModal(true, {
      record,
      isUpdate: true,
      showFooter: false,
    });
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
  async function batchHandleDelete() {
    await batchDelete({ ids: selectedRowKeys.value }, handleSuccess);
  }
  /**
   * 成功回调
   */
  function handleSuccess() {
    (selectedRowKeys.value = []) && reload();
  }
  /**
   * 操作栏
   */
  function getTableAction(record) {
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
  }

  function addApply(record: any) {
    openModal1(true, {
      record: {
        flowId: record.id,
        formOptions: record.formOptions,
        rule: record.rule,
      },
    });
  }
  /**
   * 下拉操作栏
   */
  function getDropDownAction(record) {
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
        auth: 'app:app_info:manager',
      },
    ];
  }
</script>
999

<style lang="less" scoped>
  :deep(.ant-picker),
  :deep(.ant-input-number) {
    width: 100%;
  }
</style>
