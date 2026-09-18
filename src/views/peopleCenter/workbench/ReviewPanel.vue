<template>
  <div class="workbench-panel review-panel">
    <div class="market-heading">
      <div>
        <span>REVIEW QUEUE</span>
        <h2>待审核</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input v-model="keyword" placeholder="搜索应用名 / 提交人 / 拥有者..." @keydown.enter="reload" />
      </div>
      <button class="market-refresh" type="button" @click="reload">刷新</button>
    </div>

    <p class="wb-panel-tip">
      普通用户提交的发布申请在这里审核：通过即上线到智能体广场，驳回需填写原因（作者会在卡片上看到）。你自己提交的发布会自动通过，不在此列。
    </p>

    <div class="category-filter" aria-label="审核状态筛选">
      <button
        v-for="option in statusOptions"
        :key="option.value"
        type="button"
        :class="['category-filter-item', { active: status === option.value }]"
        @click="switchStatus(option.value)"
      >
        <span>{{ option.label }}</span>
        <em v-if="option.value === 'pending_review' && pendingTotal !== undefined">{{ pendingTotal }}</em>
      </button>
    </div>

    <div v-if="loading && !rows.length" class="status-box"><LoadingOutlined /> 正在加载审核队列...</div>
    <div v-else-if="loadFailed && !rows.length" class="wb-unready">
      <ApiOutlined />
      <strong>审核队列暂不可用</strong>
      <p>工作流运行时（agent-api）未连接，或当前账号没有审核权限。</p>
      <a-button size="small" @click="reload">重试</a-button>
    </div>
    <div v-else-if="!rows.length" class="wb-empty review-empty">
      {{ status === 'pending_review' ? '暂无待审核的发布申请' : '没有对应状态的记录' }}
    </div>
    <div v-else class="review-list">
      <article v-for="record in rows" :key="record.id" class="review-row">
        <div class="review-row-main">
          <div class="review-row-title">
            <strong>{{ record.appName || '未命名应用' }}</strong>
            <span class="review-row-version">v{{ record.versionNo }}</span>
            <span class="review-row-kind">{{ typeLabel(record.aiAppType) }}</span>
            <span :class="['wb-status', statusClass(record.status)]"><i></i>{{ statusLabel(record.status) }}</span>
          </div>
          <p class="review-row-meta">
            提交人 {{ record.submittedByName || '-' }}
            <template v-if="record.ownerUsername"> · 拥有者 {{ record.ownerUsername }}</template>
            <template v-if="record.submittedAt"> · {{ formatTime(record.submittedAt) }}</template>
          </p>
          <p class="review-row-note">{{ record.changeNote || '（未填写变更说明）' }}</p>
          <p v-if="record.status !== 'pending_review' && record.reviewComment" class="review-row-comment">
            审核意见：{{ record.reviewComment }}
            <template v-if="record.reviewedByName">（{{ record.reviewedByName }}）</template>
          </p>
        </div>
        <div class="review-row-ops">
          <a-button size="small" @click="previewApp(record)">预览</a-button>
          <template v-if="record.status === 'pending_review'">
            <a-button size="small" type="primary" @click="openReview(record, 'approve')">通过并上线</a-button>
            <a-button size="small" danger @click="openReview(record, 'reject')">驳回</a-button>
          </template>
        </div>
      </article>
      <div v-if="total > rows.length" class="review-more">
        <a-button size="small" :loading="loading" @click="loadMore">加载更多（{{ rows.length }}/{{ total }}）</a-button>
      </div>
    </div>

    <a-modal
      v-model:open="reviewModal.open"
      :title="reviewModal.mode === 'approve' ? '通过发布' : '驳回发布'"
      :confirm-loading="reviewModal.loading"
      :ok-text="reviewModal.mode === 'approve' ? '通过并上线' : '确认驳回'"
      :ok-button-props="{ danger: reviewModal.mode === 'reject' }"
      cancel-text="取消"
      @ok="submitReview"
    >
      <p class="review-modal-summary">
        应用：<strong>{{ reviewModal.record?.appName }}</strong>
        · v{{ reviewModal.record?.versionNo }}
        · 提交人 {{ reviewModal.record?.submittedByName || '-' }}
      </p>
      <a-textarea
        v-model:value="reviewModal.comment"
        :rows="4"
        :placeholder="reviewModal.mode === 'approve' ? '审核意见（可选）' : '驳回原因（必填，会展示给作者）'"
      />
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { ApiOutlined, LoadingOutlined, SearchOutlined } from '@ant-design/icons-vue';
import {
  approveReview,
  queryReviewPage,
  rejectReview,
  type WorkflowVersionItem,
} from '../../workflow/api/workflow.api';
import { getAiAppRunRoute } from '../../workflow/shared/runtimeRoute';

/**
 * 审核台（原 views/workflow/review/index.vue 没挂任何路由，提交审核后没有任何界面能审批）。
 * 挂在「我的智能体」工作台里，只对审核员（/me/capabilities.isReviewer）显示。
 */
const emit = defineEmits<{
  /** 通过/驳回后通知外层刷新我的智能体与广场 */
  (e: 'changed'): void;
  /** 待审数量变化，供导航栏角标 */
  (e: 'pending-count', count: number): void;
}>();

const PAGE_SIZE = 20;

const statusOptions = [
  { label: '待审核', value: 'pending_review' },
  { label: '已通过', value: 'approved' },
  { label: '已驳回', value: 'rejected' },
  { label: '全部', value: 'all' },
];

const router = useRouter();
const keyword = ref('');
const status = ref('pending_review');
const rows = ref<WorkflowVersionItem[]>([]);
const total = ref(0);
const page = ref(1);
const loading = ref(false);
const loadFailed = ref(false);
const pendingTotal = ref<number | undefined>();

const reviewModal = reactive<{
  open: boolean;
  mode: 'approve' | 'reject';
  record: WorkflowVersionItem | null;
  comment: string;
  loading: boolean;
}>({ open: false, mode: 'approve', record: null, comment: '', loading: false });

function typeLabel(kind?: string) {
  return { chatAgent: '对话 Agent', workflow: '工作流', workflowTool: '工作流工具', simple: '对话 Agent' }[kind || ''] || kind || '-';
}

function statusLabel(value?: string) {
  return (
    { pending_review: '待审核', approved: '已通过', rejected: '已驳回', cancelled: '已撤回', archived: '已归档' }[value || ''] ||
    value ||
    '-'
  );
}

function statusClass(value?: string) {
  return { pending_review: 'pending', approved: 'published', rejected: 'failed', cancelled: 'unpublished', archived: 'unpublished' }[value || ''] || 'draft';
}

function formatTime(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

async function fetchPage(pageNo: number) {
  const result = await queryReviewPage({
    pageNo,
    pageSize: PAGE_SIZE,
    status: status.value,
    keyword: keyword.value.trim() || undefined,
  });
  return { records: result?.records || [], total: Number(result?.total || 0) };
}

async function reload() {
  loading.value = true;
  try {
    page.value = 1;
    const result = await fetchPage(1);
    rows.value = result.records;
    total.value = result.total;
    loadFailed.value = false;
    if (status.value === 'pending_review' && !keyword.value.trim()) {
      pendingTotal.value = result.total;
      emit('pending-count', result.total);
    } else {
      void refreshPendingCount();
    }
  } catch (error) {
    console.error('load review queue failed', error);
    rows.value = [];
    total.value = 0;
    loadFailed.value = true;
  } finally {
    loading.value = false;
  }
}

async function refreshPendingCount() {
  try {
    const result = await queryReviewPage({ pageNo: 1, pageSize: 1, status: 'pending_review' });
    pendingTotal.value = Number(result?.total || 0);
    emit('pending-count', pendingTotal.value);
  } catch {
    // 角标只是提示，失败不打扰
  }
}

async function loadMore() {
  if (loading.value) return;
  loading.value = true;
  try {
    const next = page.value + 1;
    const result = await fetchPage(next);
    rows.value = [...rows.value, ...result.records];
    total.value = result.total;
    page.value = next;
  } catch (error) {
    message.error('加载更多失败');
  } finally {
    loading.value = false;
  }
}

function switchStatus(value: string) {
  if (status.value === value) return;
  status.value = value;
  void reload();
}

function openReview(record: WorkflowVersionItem, mode: 'approve' | 'reject') {
  reviewModal.record = record;
  reviewModal.mode = mode;
  reviewModal.comment = '';
  reviewModal.open = true;
}

async function submitReview() {
  if (!reviewModal.record) return;
  const comment = reviewModal.comment.trim();
  if (reviewModal.mode === 'reject' && !comment) {
    message.warning('驳回必须填写原因');
    return;
  }
  reviewModal.loading = true;
  try {
    if (reviewModal.mode === 'approve') {
      await approveReview(reviewModal.record.id, comment || undefined);
      message.success('已通过并上线，作者和其他可见用户现在可以在广场运行它');
    } else {
      await rejectReview(reviewModal.record.id, comment);
      message.success('已驳回，作者会在卡片上看到原因');
    }
    reviewModal.open = false;
    await reload();
    emit('changed');
  } catch (error: any) {
    const detail = error?.response?.data?.detail;
    message.error(typeof detail === 'string' ? detail : detail?.message || '操作失败');
  } finally {
    reviewModal.loading = false;
  }
}

function previewApp(record: WorkflowVersionItem) {
  const route = getAiAppRunRoute(record);
  router.push({
    ...route,
    query: { ...(route.query || {}), previewVersionId: record.id },
  });
}

onMounted(reload);

defineExpose({ reload });
</script>

<style scoped lang="less">
.review-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.review-row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--line, #e2e8f0);
  border-radius: 12px;
  background: var(--panel, #fff);
}

.review-row-main {
  min-width: 0;
  flex: 1;
}

.review-row-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 14px;
}

.review-row-version,
.review-row-kind {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--subtle, #f1f5f9);
  color: var(--muted, #64748b);
  font-size: 12px;
}

.review-row-meta,
.review-row-note,
.review-row-comment {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--muted, #64748b);
}

.review-row-note {
  color: var(--text, #0f172a);
  white-space: pre-wrap;
}

.review-row-comment {
  color: #b45309;
}

.review-row-ops {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex-shrink: 0;
}

.review-more {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}

.review-empty {
  padding: 32px 0;
  text-align: center;
}

.review-modal-summary {
  margin-bottom: 10px;
}
</style>
