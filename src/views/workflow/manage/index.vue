<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="workflow-manage-list">
    <BasicTable @register="registerTable">
      <template #aiAppType="{ record }">
        <a-tag>{{ typeLabel(record.aiAppType) }}</a-tag>
      </template>
      <template #status="{ record }">
        <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status, record.aiAppType) }}</a-tag>
      </template>
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
    </BasicTable>

    <AdminAppDetailDrawer v-model:open="detailDrawerOpen" :app="detailApp" @changed="reload" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { BasicTable, TableAction } from '/@/components/Table';
import type { BasicColumn } from '/@/components/Table';
import type { FormSchema } from '/@/components/Form';
import type { ActionItem } from '/@/components/Table/src/types/tableAction';
import { useListPage } from '/@/hooks/system/useListPage';
import { queryAdminAppDetail, queryAdminAppPage, type AdminAppItem, type WorkflowPageResult } from '../api/workflow.api';
import { message } from 'ant-design-vue';
import { formatManageDateTime } from './manageDate';
import AdminAppDetailDrawer from './AdminAppDetailDrawer.vue';
const managedAppTypes = ['builtin', 'chatAgent', 'workflow'] as const;
const managedAppTypeQuery = managedAppTypes.join(',');

const typeOptions = [
  { label: '定制智能体', value: 'builtin' },
  { label: '对话 Agent', value: 'chatAgent' },
  { label: '工作流', value: 'workflow' },
];

const statusOptions = [
  { label: '草稿', value: 'draft' },
  { label: '待审核', value: 'pending_review' },
  { label: '已发布 / 已启用', value: 'published' },
  { label: '已下架 / 已停用', value: 'unpublished' },
];

const columns: BasicColumn[] = [
  { title: '应用名称', dataIndex: 'name', width: 220, align: 'left', ellipsis: true },
  { title: '类型', dataIndex: 'aiAppType', width: 120, align: 'center', slots: { customRender: 'aiAppType' } },
  { title: '拥有者', dataIndex: 'ownerUsername', width: 140, align: 'center' },
  { title: '状态', dataIndex: 'status', width: 110, align: 'center', slots: { customRender: 'status' } },
  { title: '发布时间', dataIndex: 'publishedAt', width: 170, align: 'center', customRender: ({ text }) => formatManageDateTime(text) },
];

const searchFormSchema: FormSchema[] = [
  {
    label: '名称',
    field: 'keyword',
    component: 'Input',
    componentProps: {
      placeholder: '应用名 / 描述 / 拥有者',
    },
    colProps: { span: 6 },
  },
  {
    label: '类型',
    field: 'aiAppType',
    component: 'Select',
    componentProps: {
      allowClear: true,
      options: typeOptions,
    },
    colProps: { span: 6 },
  },
  {
    label: '状态',
    field: 'status',
    component: 'Select',
    componentProps: {
      allowClear: true,
      options: statusOptions,
    },
    colProps: { span: 6 },
  },
];

type AdminAppPageParams = Parameters<typeof queryAdminAppPage>[0];

function queryManagedAppPage(params: AdminAppPageParams): Promise<WorkflowPageResult<AdminAppItem>> {
  return queryAdminAppPage({
    ...params,
    aiAppTypes: managedAppTypeQuery,
  });
}

const { tableContext } = useListPage({
  tableProps: {
    title: '智能体应用管理',
    api: queryManagedAppPage,
    columns,
    rowKey: 'id',
    formConfig: {
      schemas: searchFormSchema,
    },
    actionColumn: {
      width: 112,
    },
  },
});

const [registerTable, { reload }] = tableContext;
const detailDrawerOpen = ref(false);
const detailApp = ref<AdminAppItem | null>(null);
const route = useRoute();

watch(() => [route.path, route.query.appId] as const, async ([path, appId]) => {
  if (path !== '/workflow/manage' || typeof appId !== 'string' || !appId) return;
  try {
    const detail = await queryAdminAppDetail(appId);
    if (route.path !== path || route.query.appId !== appId) return;
    detailApp.value = detail.app;
    detailDrawerOpen.value = true;
  } catch {
    message.error('该智能体暂时无法打开');
  }
}, { immediate: true });

function typeLabel(t?: string) {
  return typeOptions.find((o) => o.value === t)?.label || t || '-';
}

function statusLabel(s?: string, type?: string) {
  if (type === 'builtin') return s === 'published' ? '已启用' : '已停用';
  return (
    {
      draft: '草稿',
      pending_review: '待审核',
      published: '已发布',
      unpublished: '已下架',
      approved: '已通过',
      rejected: '已驳回',
      cancelled: '已撤回',
    }[s || ''] || s || '-'
  );
}

function statusColor(s?: string) {
  return (
    {
      published: 'success',
      pending_review: 'processing',
      unpublished: 'warning',
      rejected: 'error',
      draft: 'default',
      approved: 'success',
    }[s || ''] || 'default'
  );
}

function getTableAction(record: AdminAppItem): ActionItem[] {
  return [
    {
      label: '管理',
      onClick: () => { detailApp.value = record; detailDrawerOpen.value = true; },
    },
  ];
}

</script>

<style scoped lang="less">
.workflow-manage-list {
  min-height: 100%;
  padding: 24px;
  background: #f0f2f5;
}

</style>
