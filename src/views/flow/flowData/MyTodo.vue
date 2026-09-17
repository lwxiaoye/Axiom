<template>
  <div>
    <!--引用表格-->
    <BasicTable @register="registerTable">
      <!--操作栏-->
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
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
  import { columns, searchFormTodoSchema } from "./FlowData.data";
  import { getMyToDo, getImportUrl, getExportUrl, queryByProcessInstanceId, queryById } from './FlowData.api';
  import { useUserStore } from '/@/store/modules/user';
  import FlowApplyModal from '@/views/flow/flowData/components/FlowApplyModal.vue';

  const queryParam = reactive<any>({});
  // const checkedKeys = ref<Array<string | number>>([]);
  const { userInfo } = useUserStore();
  //注册model
  const [registerModal, { openModal }] = useModal();
  const [registerModal1, { openModal: openModal1 }] = useModal();

  //注册table数据
  // onExportXls, onImportXls prefixCls
  const { tableContext } = useListPage({
    tableProps: {
      title: '我的待办',
      api: getMyToDo,
      columns,
      canResize: false,
      formConfig: {
        //labelWidth: 120,
        schemas: searchFormTodoSchema,
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

  import { useRoute } from 'vue-router';
  const route = useRoute();
  let id: any = route.query.id;
  if (id) {
    let currentUserId = userInfo?.id;
    if (route.query.type == 'other') {
      queryById(id).then((res) => {
        console.log(res);
        let record = res;
        if (!record.isStartNode && record.assignee && record.assignee.split(',').indexOf(currentUserId) > -1) {
          handleAudit(record, 'audit');
        } else {
          handleAudit(record, 'detail');
        }
      });
    } else if (route.query.type == 'train') {
      queryByProcessInstanceId({ processInstanceId: id }).then((res) => {
        console.log(res);
        let record = res.processInfoModel;
        if (!record.isStartNode && record.assignee && record.assignee.split(',').indexOf(currentUserId) > -1) {
          handleAudit(record, 'audit');
        } else {
          handleAudit(record, 'detail');
        }
      });
    }
  }
</script>

<style lang="less" scoped>
  :deep(.ant-picker),
  :deep(.ant-input-number) {
    width: 100%;
  }
</style>
