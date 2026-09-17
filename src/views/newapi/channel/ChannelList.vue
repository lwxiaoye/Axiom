<template>
  <div class="model-list">
    <BasicTable @register="registerTable">
      <template #tableTitle>
        <a-space>
          <a-button type="primary" preIcon="ant-design:plus-outlined" @click="handleCreate"> 新增模型</a-button>
        </a-space>
      </template>
      <template #action="{ record }">
        <div class="channel-actions">
          <a-dropdown :trigger="['click']" placement="bottomLeft">
            <a-button type="link" size="small" class="protocol-test-trigger">
              测试
              <DownOutlined />
            </a-button>
            <template #overlay>
              <a-menu>
                <a-menu-item key="chat" @click="handleTest(record)">Chat Completions</a-menu-item>
                <a-menu-item key="responses" @click="handleResponsesTest(record)">Responses API</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
          <TableAction :actions="getTableAction(record)" />
        </div>
      </template>
      <template #statusSwitch="{ record }">
        <a-switch
          :checked="record.status === 1"
          checked-children="启用"
          un-checked-children="停用"
          checked-color="#52c41a"
          @change="(checked) => handleStatusChange(record, checked ? 1 : 2)"
        />
     </template>
    </BasicTable>
    <ChannelModal @register="registerModal" @success="handleSuccess" />
  </div>
</template>

<script lang="ts" name="newapi-channel" setup>
  import { BasicTable, TableAction } from '/@/components/Table';
  import { DownOutlined } from '@ant-design/icons-vue';
  import { useModal } from '/@/components/Modal';
  import ChannelModal from './components/ChannelModal.vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { columns, searchFormSchema } from './channel.data.js';
  import { list, deleteChannel, test, updateStatus } from './channel.api.js';
  import { useListPage } from '/@/hooks/system/useListPage';


  const { createMessage } = useMessage();
  const [registerModal, { openModal }] = useModal();

  const { tableContext } = useListPage({
    tableProps: {
      title: '模型管理',
      api: list,
      columns: columns,
      formConfig: {
        schemas: searchFormSchema,
      },
      actionColumn: {
        width: 190,
      },
    },
  });

  const [registerTable, { reload, updateTableDataRecord }] = tableContext;
 
  function handleCreate() {
    openModal(true, {
      isUpdate: false,
    });
  }

  async function handleEdit(record: Recordable) {
    openModal(true, {
      id: record.id,
      isUpdate: true,
    });
  }

  async function handleDelete(record) {
    await deleteChannel({ id: record.id, name: record.name }, reload);
  }

  function handleSuccess({ isUpdate, values }) {
    if (isUpdate) {
      updateTableDataRecord(values.id, values);
    } else {
      reload();
    }
  }
  async function handleTest(record) {
    await test(record.id);
    createMessage.success('Chat 测试成功');
    reload();
  }

  function resolveResponsesTestModel(record: Recordable) {
    const models = String(record.models || '')
      .split(',')
      .map((model) => model.trim())
      .filter(Boolean);
    return models.find((model) => model === 'deepseek-v4-flash') || models[0] || '';
  }

  async function handleResponsesTest(record: Recordable) {
    const model = resolveResponsesTestModel(record);
    if (!model) {
      createMessage.warning('该渠道没有可测试的模型');
      return;
    }
    await test(record.id, {
      model,
      endpoint_type: 'openai-response',
      stream: true,
    });
    createMessage.success(`Responses 测试成功（${model}）`);
    reload();
  }
  async function handleStatusChange(record, status) {
    await updateStatus({ id: record.id, status });
    reload();
  }

  function getTableAction(record) {
    return [
      {
        label: '编辑',
        onClick: handleEdit.bind(null, record),
      },
      {
        label: '删除',
        popConfirm: {
          title: '确定删除吗?',
          confirm: handleDelete.bind(null, record),
        },
      },
    ];
  }
</script>

<style scoped lang="less">
.model-list {
  padding: 24px;
  background: #f0f2f5;
  min-height: 100%;
}

.channel-actions {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;

  .protocol-test-trigger {
    display: inline-flex;
    align-items: center;
    gap: 2px;
    height: 24px;
    padding: 0 4px;
    color: inherit;
    font-weight: 500;
  }

  :deep(.jeecg-basic-table-action) {
    margin-left: 2px;
  }
}
</style>
