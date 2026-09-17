<template>
  <div class="token-list">
    <BasicTable @register="registerTable">
      <template #tableTitle>
        <a-space>
          <a-button type="primary" preIcon="ant-design:plus-outlined" @click="handleCreate"> 新增令牌</a-button>
          <a-button preIcon="ant-design:sync-outlined" :loading="syncLoading" @click="handleSyncUserKeys">
            同步用户令牌
          </a-button>
        </a-space>
      </template>
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
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
    <TokenModal @register="registerModal" @success="handleSuccess" />
  </div>
</template>

<script lang="ts" name="newapi-token" setup>
  import { ref } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { useModal } from '/@/components/Modal';
  import TokenModal from './components/TokenModal.vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { columns, searchFormSchema } from './token.data.js';
  import { list, deleteToken, getTokenKey, syncUserKeys, updateStatus } from './token.api.js';
  import { useListPage } from '/@/hooks/system/useListPage';

  const { createConfirm, createMessage } = useMessage();
  const [registerModal, { openModal }] = useModal();
  const syncLoading = ref(false);

  function getTokenKeyValue(data: any): string {
    if (!data) return '';
    if (typeof data === 'string') return data;

    const candidates = [
      data.key,
      data.token,
      data.value,
      data.data?.key,
      data.data?.token,
      data.data?.value,
      data.result?.key,
      data.result?.token,
      data.result?.value,
    ];

    return String(candidates.find((item) => item) || '').trim();
  }

  async function copyText(text: string) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    textarea.style.top = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();

    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(textarea);
    }
  }

  const handleCopyToken = async (record: Recordable) => {
    try {
      const res = await getTokenKey(record.id);
      console.log(res);

      const tokenKey = getTokenKeyValue(res);
      if (tokenKey) {
        await copyText(tokenKey.startsWith('sk-') ? tokenKey : `sk-${tokenKey}`);
        createMessage.success('密钥已复制到剪贴板');
      } else {
        createMessage.error('获取密钥失败');
      }
    } catch (error) {
      console.error('复制密钥失败:', error);
      createMessage.error('复制密钥失败');
    }
  };

  const { tableContext } = useListPage({
    tableProps: {
      title: '令牌管理',
      api: list,
      columns: columns,
      formConfig: {
        schemas: searchFormSchema,
      },
      actionColumn: {
        width: 180,
      },
    },
  });

  const [registerTable, { reload, updateTableDataRecord }] = tableContext;

  function handleCreate() {
    openModal(true, {
      isUpdate: false,
    });
  }

  function handleSyncUserKeys() {
    createConfirm({
      iconType: 'warning',
      title: '同步用户令牌',
      content: '将为尚未拥有 NewAPI 令牌的有效用户创建令牌，已有令牌不会重复创建。',
      onOk: async () => {
        syncLoading.value = true;
        try {
          await syncUserKeys();
          reload();
        } finally {
          syncLoading.value = false;
        }
      },
    });
  }

  async function handleEdit(record: Recordable) {
    openModal(true, {
      id: record.id,
      isUpdate: true,
    });
  }

  async function handleDelete(record) {
    await deleteToken({ id: record.id });
    reload();
  }

  function handleSuccess({ isUpdate, values }) {
    if (isUpdate) {
      updateTableDataRecord(values.id, values);
    } else {
      reload();
    }
  }

  async function handleStatusChange(record, status) {
    await updateStatus({ id: record.id, status });
    reload();
  }


  function getTableAction(record) {
    return [
      {
        label: '复制密钥',
        onClick: handleCopyToken.bind(null, record),
      },
      {
        label: '编辑',
        onClick: handleEdit.bind(null, record),
      },
      {
        label: '删除',
        popConfirm: {
          title: '确定删除该令牌吗?',
          confirm: handleDelete.bind(null, record),
        },
      },
    ];
  }
</script>

<style scoped lang="less">
.token-list {
  padding: 16px;
  background: #fff;
  border-radius: 8px;
}
</style>
