<template>
  <a-drawer
    v-model:open="open"
    :width="680"
    :title="drawerTitle"
    :destroy-on-close="true"
    placement="right"
  >
    <div class="version-summary">
      <strong v-if="liveVersion">当前线上版本 v{{ liveVersion }}</strong>
      <strong v-else>当前没有线上版本</strong>
      <p v-if="hasPendingReview">存在审核中的更新，请先撤回审核后再回滚。</p>
      <p v-else>回滚会以选中的历史快照创建一个新的线上版本；不会删除草稿或既有版本记录。</p>
    </div>

    <a-table
      :columns="columns"
      :data-source="records"
      :loading="loading"
      :pagination="false"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'version'">
          <a-space :size="4">
            <span>v{{ record.versionNo }}</span>
            <a-tag v-if="record.isLive" color="success">线上</a-tag>
          </a-space>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'publishedAt'">
          {{ record.publishedAt || record.submittedAt || '-' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-button
            v-if="canRollback(record)"
            type="link"
            size="small"
            @click="confirmRollback(record)"
          >
            回滚至此版本
          </a-button>
          <span v-else>-</span>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && !records.length" description="暂无版本记录" />
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Modal, message } from 'ant-design-vue';
import {
  queryWorkflowVersionPage,
  rollbackWorkflowVersion,
  type AiWorkflowApp,
  type WorkflowVersionItem,
} from '../../workflow/api/workflow.api';

const emit = defineEmits<{
  (e: 'rolled-back'): void;
}>();

const columns = [
  { title: '版本', key: 'version', width: 110 },
  { title: '状态', key: 'status', width: 100 },
  { title: '变更说明', dataIndex: 'changeNote', key: 'changeNote', ellipsis: true },
  { title: '发布时间', key: 'publishedAt', width: 185 },
  { title: '操作', key: 'action', width: 118, fixed: 'right' },
];

const open = ref(false);
const app = ref<Partial<AiWorkflowApp> | null>(null);
const records = ref<WorkflowVersionItem[]>([]);
const liveVersion = ref(0);
const loading = ref(false);
let requestToken = 0;

const drawerTitle = computed(() => {
  const name = app.value?.name || (app.value as Recordable | null)?.appName;
  return name ? `版本记录 · ${name}` : '版本记录';
});

const hasPendingReview = computed(() => records.value.some((record) => record.status === 'pending_review'));

function statusLabel(status: string) {
  return {
    pending_review: '审核中',
    approved: '已通过',
    rejected: '已驳回',
    cancelled: '已撤回',
    archived: '已归档',
  }[status] || status || '-';
}

function statusColor(status: string) {
  return {
    pending_review: 'processing',
    approved: 'success',
    rejected: 'error',
    cancelled: 'default',
    archived: 'default',
  }[status] || 'default';
}

function canRollback(record: WorkflowVersionItem) {
  return !hasPendingReview.value && !record.isLive && ['approved', 'archived'].includes(record.status);
}

async function load() {
  if (!app.value?.id) return;
  const token = ++requestToken;
  loading.value = true;
  try {
    const page = await queryWorkflowVersionPage({ appId: String(app.value.id), pageNo: 1, pageSize: 50 });
    if (token !== requestToken) return;
    records.value = page?.records || [];
    liveVersion.value = page?.liveVersion || 0;
  } catch (error: any) {
    if (token !== requestToken) return;
    records.value = [];
    liveVersion.value = 0;
    message.error(error?.response?.data?.detail || '加载版本记录失败');
  } finally {
    if (token === requestToken) loading.value = false;
  }
}

function confirmRollback(record: WorkflowVersionItem) {
  if (!app.value?.id) return;
  Modal.confirm({
    title: `回滚至 v${record.versionNo}`,
    content: '将立即以该历史快照创建一个新的线上版本。当前线上版本会被替换，但草稿和全部版本记录都会保留。',
    okText: '确认回滚',
    cancelText: '取消',
    onOk: async () => {
      const result = await rollbackWorkflowVersion(String(app.value?.id), record.versionNo);
      message.success(result?.message || `已回滚至 v${record.versionNo}`);
      await load();
      emit('rolled-back');
    },
  });
}

function init(item: Partial<AiWorkflowApp>) {
  app.value = item;
  records.value = [];
  liveVersion.value = 0;
  open.value = true;
  void load();
}

defineExpose({ init });
</script>

<style scoped lang="less">
.version-summary {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;

  strong {
    color: #0f172a;
    font-size: 14px;
  }

  p {
    margin: 6px 0 0;
    color: #64748b;
    font-size: 12px;
    line-height: 1.65;
  }
}
</style>
