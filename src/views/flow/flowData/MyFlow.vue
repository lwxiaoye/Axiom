<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable">
      <!--插槽:table标题-->
      <template #tableTitle>
        <a-button type="primary" preIcon="ant-design:export-outlined" @click="onExportXls"> 导出</a-button>
        <a-button type="primary" preIcon="ant-design:export-outlined" @click="openExportModel"> 导出流程应用数据</a-button>
      </template>
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
      <!--字段回显插槽-->
      <!-- <template #bodyCell="{ column, record, index, text }"> </template> -->
    </BasicTable>
    <FlowAuditModal @register="registerModal" @success="handleSuccess" />
    <FlowApplyModal @register="registerModal1" @success="handleSuccess" />

    <ChooseModelModal @register="registerModal4" @success="handleSuccess" />
  </div>
</template>

<script lang="ts" name="flowData-flowData" setup>
  import { reactive } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { useModal } from '/@/components/Modal';
  import { useListPage } from '/@/hooks/system/useListPage';
  import FlowAuditModal from '@/views/flow/flowData/components/FlowAuditModal.vue';
  import { columns, searchFormSchema } from './FlowData.data';
  import { getMyFlow, getImportUrl, getExportUrl } from './FlowData.api';
  import { useUserStore } from '/@/store/modules/user';
  import FlowApplyModal from '@/views/flow/flowData/components/FlowApplyModal.vue';

  import ChooseModelModal from '@/views/flow/flowData/components/chooseModel.vue';
  const queryParam = reactive<any>({});
  // const checkedKeys = ref<Array<string | number>>([]);
  const { userInfo } = useUserStore();
  //注册model
  const [registerModal, { openModal }] = useModal();
  const [registerModal1, { openModal: openModal1 }] = useModal();

  const [registerModal4, { openModal: openModal4 }] = useModal();
  //注册table数据
  // onExportXls, onImportXls prefixCls
  const { tableContext, onExportXls } = useListPage({
    tableProps: {
      title: '我的流程',
      api: getMyFlow,
      columns,
      canResize: false,
      formConfig: {
        //labelWidth: 120,
        schemas: searchFormSchema,
        autoSubmitOnEnter: true,
        showAdvancedButton: false,
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
      name: '我的流程',
      url: getExportUrl,
      params: queryParam,
    },
    importConfig: {
      url: getImportUrl,
      success: handleSuccess,
    },
  });

  const [registerTable, { reload }, { selectedRowKeys }] = tableContext;

  // // 高级查询配置
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
  // /**
  //  * 新增事件
  //  */
  // function handleAdd() {
  //   openModal(true, {
  //     isUpdate: false,
  //     showFooter: true,
  //   });
  // }
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
        id: record.id,
      },
      isUpdate: true,
    });
  }
  function openExportModel() {
    openModal4(true);
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
    if (!record.isStartNode && record.assignee && record.assignee.split(',').indexOf(currentUserId) > -1) {
      list.push({
        label: '审核',
        onClick: handleAudit.bind(null, record, 'audit'),
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
