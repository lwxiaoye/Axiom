<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable">
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
    </BasicTable>
    <FlowAuditModal @register="registerModal" />
    <!-- <FlowApplyModal @register="registerModal1" /> -->
  </div>
</template>

<script lang="ts" name="flowData-flowData" setup>
  import { reactive } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { useModal } from '/@/components/Modal';
  import { useListPage } from '/@/hooks/system/useListPage';
  import FlowAuditModal from '@/views/flow/flowData/components/FlowAuditModal.vue';
  import { myDoColumn, searchFormSchemas } from './FlowData.data';
  import { getMyDo, getImportUrl, getExportUrl } from './FlowData.api';
  // import { useUserStore } from '/@/store/modules/user';
  // import FlowApplyModal from '@/views/flow/flowData/components/FlowApplyModal.vue';
  const queryParam = reactive<any>({});
  // const checkedKeys = ref<Array<string | number>>([]);
  // const userStore = useUserStore();
  //注册model
  const [registerModal, { openModal }] = useModal();
  // const [registerModal1, { openModal: openModal1 }] = useModal();
  //注册table数据
  const { tableContext } = useListPage({
    tableProps: {
      title: '我的已办',
      api: getMyDo,
      columns: myDoColumn,
      canResize: false,
      formConfig: {
        //labelWidth: 120,
        schemas: searchFormSchemas,
        autoSubmitOnEnter: true,
        showAdvancedButton: true,
        fieldMapToNumber: [],
        fieldMapToTime: [],
      },
      actionColumn: {
        width: 100,
        fixed: 'right',
      },
      beforeFetch: (params) => {
        return Object.assign(params, queryParam);
      },
    },
    exportConfig: {
      name: '应用流程数据',
      url: getExportUrl,
      params: queryParam,
    },
    importConfig: {
      url: getImportUrl,
      success: handleSuccess,
    },
  });

  const [registerTable, { reload }, { selectedRowKeys }] = tableContext;
  /**
   * 编辑事件
   */
  function handleAudit(record: Recordable, type: string) {
    openModal(true, {
      record,
      type: type,
    });
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
        label: '详情',
        onClick: handleAudit.bind(null, record, 'detail'),
      },
    ];
  }
</script>

<style lang="less" scoped>
  :deep(.ant-picker),
  :deep(.ant-input-number) {
    width: 100%;
  }
</style>
