<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable">
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
      <!--字段回显插槽-->
      <!-- <template #bodyCell="{ column, record, index, text }"> </template> -->
    </BasicTable>
    <FlowAuditModal @register="registerModal" @success="handleSuccess" />
    <FlowApplyModal @register="registerModal1" @success="handleSuccess" />

  </div>
</template>

<script lang="ts" name="flowData-flowData" setup>
  import { reactive } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { useModal } from '/@/components/Modal';
  import { useListPage } from '/@/hooks/system/useListPage';
  import FlowAuditModal from '@/views/flow/flowData/components/FlowAuditModal.vue';
  import { myStartColumn, searchFormSchemas } from './FlowData.data';
  import { getMyStart, getImportUrl, getExportUrl } from './FlowData.api';
  import { useUserStore } from '/@/store/modules/user';
  import FlowApplyModal from '@/views/flow/flowData/components/FlowApplyModal.vue';

  const queryParam = reactive<any>({});
  const { userInfo } = useUserStore();
  //注册model
  const [registerModal, { openModal }] = useModal();
  const [registerModal1, { openModal: openModal1 }] = useModal();

  //注册table数据
  const { tableContext } = useListPage({
    tableProps: {
      title: '我的发起',
      api: getMyStart,
      columns: myStartColumn,
      canResize: false,
      formConfig: {
        schemas: searchFormSchemas,
        autoSubmitOnEnter: true,
        showAdvancedButton: true,
        fieldMapToNumber: [],
        fieldMapToTime: [],
      },
      actionColumn: {
        width: 210,
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

  function handleEdit(record: Recordable) {
    openModal1(true, {
      record: {
        id: record.bizId,
      },
      isUpdate: true,
    });
  }
  /**
   * 成功回调
   */
  function handleSuccess() {
    (selectedRowKeys.value = []) && reload();
  }

  function getTableAction(record) {
    let list: any = [];
    let currentUserId = userInfo?.id;
    if (record.isStartNode && record.createId == currentUserId) {
      list.push({
        label: '重新申请',
        onClick: handleEdit.bind(null, record),
      });
      list.push({
        label: '关闭流程',
        onClick: handleAudit.bind(null, record, 'close'),
      });
    }
    if (!record.isStartNode && record.createId == currentUserId && record.status === 'running') {
      list.push({
        label: '撤回申请',
        onClick: handleAudit.bind(null, record, 'revoke'),
      });
    }
    list.push({
      label: '详情',
      onClick: handleAudit.bind(null, record, 'detail'),
    });
    return list;
  }
</script>

<style lang="less" scoped>
  :deep(.ant-picker),
  :deep(.ant-input-number) {
    width: 100%;
  }
</style>
