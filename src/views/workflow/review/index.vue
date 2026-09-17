<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="p-4">
    <a-card :bordered="false" title="智能体发布审核台">
      <template #extra>
        <a-space>
          <a-select v-model:value="query.status" style="width: 130px" :options="statusOptions" @change="reload" />
          <a-select
            v-model:value="query.aiAppType"
            style="width: 140px"
            allow-clear
            placeholder="全部类型"
            :options="typeOptions"
            @change="reload"
          />
          <a-input-search
            v-model:value="query.keyword"
            placeholder="应用名 / 提交人 / 拥有者"
            style="width: 200px"
            allow-clear
            @search="reload"
          />
          <a-button @click="reload">刷新</a-button>
        </a-space>
      </template>

      <a-table
        :columns="columns"
        :data-source="rows"
        :loading="loading"
        :pagination="pagination"
        row-key="id"
        size="middle"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'aiAppType'">
            <a-tag>{{ typeLabel(record.aiAppType) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space>
              <a-button type="link" size="small" @click="previewApp(record)">预览</a-button>
              <template v-if="record.status === 'pending_review'">
                <a-button type="link" size="small" @click="openReview(record, 'approve')">通过</a-button>
                <a-button type="link" size="small" danger @click="openReview(record, 'reject')">驳回</a-button>
              </template>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal
      v-model:open="reviewModal.open"
      :title="reviewModal.mode === 'approve' ? '通过发布' : '驳回发布'"
      :confirm-loading="reviewModal.loading"
      :ok-text="reviewModal.mode === 'approve' ? '通过并上线' : '确认驳回'"
      :ok-button-props="{ danger: reviewModal.mode === 'reject' }"
      :body-style="{ padding: '20px 24px' }"
      @ok="submitReview"
    >
      <p class="mb-2">
        应用：<strong>{{ reviewModal.record?.appName }}</strong>
        · v{{ reviewModal.record?.versionNo }}
        · 拥有者 {{ reviewModal.record?.ownerUsername || '-' }}
        · 提交人 {{ reviewModal.record?.submittedByName || '-' }}
      </p>
      <a-textarea
        v-model:value="reviewModal.comment"
        :rows="4"
        :placeholder="reviewModal.mode === 'approve' ? '审核意见（可选）' : '驳回意见（必填，会展示给作者）'"
      />
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import {
  queryReviewPage,
  approveReview,
  rejectReview,
  type WorkflowVersionItem,
} from '../api/workflow.api';
import { getAiAppRunRoute } from '../shared/runtimeRoute';

const columns = [
  { title: '应用', dataIndex: 'appName', key: 'appName', ellipsis: true },
  { title: '拥有者', dataIndex: 'ownerUsername', key: 'ownerUsername', width: 120 },
  { title: '类型', dataIndex: 'aiAppType', key: 'aiAppType', width: 110 },
  { title: '版本', dataIndex: 'versionNo', key: 'versionNo', width: 80, customRender: ({ text }) => `v${text}` },
  { title: '提交人', dataIndex: 'submittedByName', key: 'submittedByName', width: 120 },
  { title: '变更说明', dataIndex: 'changeNote', key: 'changeNote', ellipsis: true },
  { title: '提交时间', dataIndex: 'submittedAt', key: 'submittedAt', width: 170 },
  { title: '状态', dataIndex: 'status', key: 'status', width: 100 },
  { title: '操作', key: 'action', width: 180, fixed: 'right' },
];

const statusOptions = [
  { label: '待审核', value: 'pending_review' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '全部', value: 'all' },
];
const typeOptions = [
  { label: '对话 Agent', value: 'chatAgent' },
  { label: '工作流', value: 'workflow' },
  { label: '工作流工具', value: 'workflowTool' },
];

const query = reactive({ status: 'pending_review', aiAppType: undefined as string | undefined, keyword: '' });
const rows = ref<WorkflowVersionItem[]>([]);
const loading = ref(false);
const pagination = reactive({ current: 1, pageSize: 10, total: 0, showSizeChanger: false });
const router = useRouter();

const reviewModal = reactive<{ open: boolean; mode: 'approve' | 'reject'; record: WorkflowVersionItem | null; comment: string; loading: boolean }>({
  open: false,
  mode: 'approve',
  record: null,
  comment: '',
  loading: false,
});

function typeLabel(t?: string) {
  return { chatAgent: '对话 Agent', workflow: '工作流', workflowTool: '工作流工具' }[t || ''] || t || '-';
}
function statusLabel(s?: string) {
  return { pending_review: '待审核', approved: '已通过', rejected: '已驳回', cancelled: '已撤回', archived: '已归档' }[s || ''] || s || '-';
}
function statusColor(s?: string) {
  return { pending_review: 'processing', approved: 'success', rejected: 'error', cancelled: 'default', archived: 'default' }[s || ''] || 'default';
}

async function reload() {
  loading.value = true;
  try {
    const res = await queryReviewPage({
      pageNo: pagination.current,
      pageSize: pagination.pageSize,
      status: query.status,
      aiAppType: query.aiAppType,
      keyword: query.keyword || undefined,
    });
    rows.value = res?.records || [];
    pagination.total = res?.total || 0;
  } catch (e) {
    rows.value = [];
    pagination.total = 0;
  } finally {
    loading.value = false;
  }
}

function onTableChange(pag: { current: number; pageSize: number }) {
  pagination.current = pag.current;
  pagination.pageSize = pag.pageSize;
  reload();
}

function openReview(record: WorkflowVersionItem, mode: 'approve' | 'reject') {
  reviewModal.record = record;
  reviewModal.mode = mode;
  reviewModal.comment = '';
  reviewModal.open = true;
}

async function submitReview() {
  if (!reviewModal.record) return;
  if (reviewModal.mode === 'reject' && !reviewModal.comment.trim()) {
    message.warning('驳回必须填写意见');
    return;
  }
  reviewModal.loading = true;
  try {
    if (reviewModal.mode === 'approve') {
      await approveReview(reviewModal.record.id, reviewModal.comment.trim() || undefined);
      message.success('已通过并上线');
    } else {
      await rejectReview(reviewModal.record.id, reviewModal.comment.trim());
      message.success('已驳回');
    }
    reviewModal.open = false;
    reload();
  } catch (e: any) {
    message.error(e?.response?.data?.detail || '操作失败');
  } finally {
    reviewModal.loading = false;
  }
}

function previewApp(record: WorkflowVersionItem) {
  const route = getAiAppRunRoute(record);
  router.push({
    ...route,
    query: {
      ...(route.query || {}),
      previewVersionId: record.id,
    },
  });
}

onMounted(reload);
</script>
