<template>
  <div class="log-list">
    <BasicTable @register="registerTable" >
      <template #toolbar>
        <a-button type="primary" @click="handleRefresh">
          <template #icon>
            <RedoOutlined />
          </template>
          刷新
        </a-button>
      </template>
    </BasicTable>
  </div>
</template>

<script lang="ts" name="newapi-log" setup>
  import { BasicTable, useTable } from '/@/components/Table';
  import { RedoOutlined } from '@ant-design/icons-vue';
  import { columns, searchFormSchema } from './log.data';
  import { list } from './log.api';
  import { useListPage } from '/@/hooks/system/useListPage';

  const { prefixCls, tableContext } = useListPage({
    tableProps: {
      title: '日志查询',
      api: list,
      columns: columns,
      formConfig: {
        schemas: searchFormSchema,
        transform: (values) => {
          const transformed: Record<string, any> = { ...values };
          if (transformed.start_timestamp) {
            transformed.start_timestamp = Math.floor(new Date(transformed.start_timestamp).getTime() / 1000);
          }
          if (transformed.end_timestamp) {
            transformed.end_timestamp = Math.floor(new Date(transformed.end_timestamp).getTime() / 1000);
          }
          return transformed;
        },
      },
      pagination: {
        pageSize: 20,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (total) => `共 ${total} 条记录`,
      },
    },
  });

  const [registerTable, { reload }] = tableContext;

  function handleRefresh() {
    reload();
  }
</script>

<style scoped lang="less">
.log-list {
  padding: 20px;
}
</style>
