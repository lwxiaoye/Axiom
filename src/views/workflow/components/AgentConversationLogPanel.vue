<template>
  <section class="conversation-log-panel">
    <a-form layout="inline" class="log-filters" @submit.prevent="reload(true)">
      <a-form-item label="时间范围" class="log-filter-item">
        <a-select v-model:value="range" :options="rangeOptions" style="width: 132px" />
      </a-form-item>
      <a-form-item label="状态" class="log-filter-item">
        <a-select v-model:value="status" allow-clear placeholder="全部" style="width: 120px" :options="statusOptions" />
      </a-form-item>
      <a-form-item class="log-filter-item log-filter-keyword">
        <a-input v-model:value="keyword" allow-clear placeholder="会话、用户或消息关键词" style="width: 210px" @press-enter="reload(true)" />
      </a-form-item>
      <a-space class="log-filter-actions">
        <a-button type="primary" :loading="loading" @click="reload(true)">查询</a-button>
        <a-button :disabled="!total || exporting" :loading="exporting" @click="exportCurrentFilters">导出 XLSX</a-button>
      </a-space>
    </a-form>

    <a-alert v-if="error" type="error" show-icon class="log-alert" :message="error" />
    <a-table :columns="columns" :data-source="records" :loading="loading" :pagination="false" row-key="id" size="small" :scroll="{ x: 1080 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'feedback'">
          <span class="feedback-summary" :aria-label="`点赞 ${record.upvotes}，点踩 ${record.downvotes}`">
            <span class="feedback-count like"><LikeOutlined />{{ record.upvotes }}</span>
            <span class="feedback-count dislike"><DislikeOutlined />{{ record.downvotes }}</span>
          </span>
        </template>
        <template v-else-if="column.key === 'lastMessageAt'">{{ formatTime(record.lastMessageAt) }}</template>
        <template v-else-if="column.key === 'action'"><a-button type="link" size="small" @click="openDetail(record)">查看详情</a-button></template>
      </template>
    </a-table>
    <div class="log-pagination">
      <span>共 {{ total }} 条</span>
      <a-pagination v-model:current="pageNo" :total="total" :page-size="pageSize" size="small" show-less-items @change="reload(false)" />
    </div>

    <a-drawer :open="detailOpen" width="min(720px, 94vw)" title="会话详情" @close="detailOpen = false">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <p class="detail-meta"><strong>{{ detail.title }}</strong> · {{ detail.username }} · {{ detail.messages.length }} 条消息</p>
          <div class="conversation-thread">
            <article v-for="item in detail.messages" :key="item.id" :class="['conversation-message', item.role]">
              <span class="conversation-avatar" aria-hidden="true">
                <UserOutlined v-if="item.role === 'user'" />
                <RobotOutlined v-else />
              </span>
              <div class="conversation-content">
                <header>
                  <span>{{ item.role === 'user' ? detail.username : '智能体' }}</span>
                  <time>{{ formatTime(item.createdAt) }}</time>
                </header>
                <div class="conversation-bubble"><pre>{{ item.content || '-' }}</pre></div>
                <footer v-if="item.role === 'assistant' && (item.status || item.feedback)">
                  <a-tag v-if="item.status" :color="statusColor(item.status)">{{ statusLabel(item.status) }}</a-tag>
                  <span v-if="item.feedback" class="message-feedback">
                    <LikeOutlined v-if="item.feedback === 'up'" />
                    <DislikeOutlined v-else />
                    {{ item.feedback === 'up' ? '已点赞' : '已点踩' }}
                  </span>
                </footer>
              </div>
            </article>
          </div>
        </template>
      </a-spin>
    </a-drawer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import { DislikeOutlined, LikeOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons-vue';
import {
  downloadAdminConversationLogs,
  downloadOwnerConversationLogs,
  queryAdminConversationLogs,
  queryOwnerConversationLogs,
  queryAdminConversationLogDetail,
  queryOwnerConversationLogDetail,
  type ConversationLogDetail,
  type ConversationLogQuery,
  type ConversationLogRecord,
  type ConversationLogStatus,
  type MetricRange,
} from '../api/workflow.api';

const props = defineProps<{ appId: string; access: 'admin' | 'owner' }>();
const range = ref<MetricRange>('last_7_days');
const status = ref<ConversationLogStatus>();
const keyword = ref('');
const pageNo = ref(1);
const pageSize = 20;
const records = ref<ConversationLogRecord[]>([]);
const total = ref(0);
const loading = ref(false);
const exporting = ref(false);
const error = ref('');
const detail = ref<ConversationLogDetail | null>(null);
const detailOpen = ref(false);
const detailLoading = ref(false);

const statusOptions = [
  ['completed', '完成'], ['partial', '部分完成'], ['failed', '失败'], ['cancelled', '已取消'], ['interrupted', '已中断'], ['unknown', '未知'],
].map(([value, label]) => ({ value, label }));
const rangeOptions: Array<{ value: MetricRange; label: string }> = [
  { value: 'today', label: '今天' },
  { value: 'last_7_days', label: '过去 7 天' },
  { value: 'last_4_weeks', label: '过去 4 周' },
  { value: 'last_3_months', label: '过去 3 月' },
  { value: 'last_12_months', label: '过去 12 月' },
  { value: 'month_to_date', label: '本月至今' },
  { value: 'quarter_to_date', label: '本季度至今' },
  { value: 'year_to_date', label: '本年至今' },
  { value: 'all_time', label: '所有时间' },
];
const columns = [
  { title: '会话标题', dataIndex: 'title', key: 'title', width: 180, ellipsis: true },
  { title: '用户', dataIndex: 'username', key: 'username', width: 120, ellipsis: true },
  { title: '状态', key: 'status', width: 100 },
  { title: '消息数', key: 'messageCount', dataIndex: 'messageCount', width: 88 },
  { title: '反馈', key: 'feedback', width: 116 },
  { title: '最近消息时间', key: 'lastMessageAt', width: 170 },
  { title: '操作', key: 'action', width: 90, fixed: 'right' as const },
];

function buildListParams(): ConversationLogQuery {
  return { range: range.value, status: status.value, keyword: keyword.value.trim() || undefined, pageNo: pageNo.value, pageSize };
}
function buildConversationLogExportParams(): ConversationLogQuery {
  const { pageNo: _pageNo, pageSize: _pageSize, ...filters } = buildListParams();
  return filters;
}
function statusLabel(value: ConversationLogStatus) { return statusOptions.find((item) => item.value === value)?.label || '未知'; }
function statusColor(value: ConversationLogStatus) { return value === 'completed' ? 'success' : value === 'failed' ? 'error' : value === 'unknown' ? 'default' : 'warning'; }
function formatTime(value?: string | null) { return value ? value.replace('T', ' ').slice(0, 19) : '-'; }

async function openDetail(record: ConversationLogRecord) {
  if (!props.appId) return;
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    const request = props.access === 'admin' ? queryAdminConversationLogDetail : queryOwnerConversationLogDetail;
    detail.value = await request(props.appId, record.threadId);
  } catch (cause: any) {
    detailOpen.value = false;
    error.value = cause?.response?.data?.detail || cause?.message || '加载会话详情失败';
  } finally {
    detailLoading.value = false;
  }
}

async function reload(resetPage: boolean) {
  if (!props.appId) return;
  if (resetPage) pageNo.value = 1;
  loading.value = true;
  error.value = '';
  try {
    const request = props.access === 'admin' ? queryAdminConversationLogs : queryOwnerConversationLogs;
    const result = await request(props.appId, buildListParams());
    records.value = result.records || [];
    total.value = Number(result.total || 0);
  } catch (cause: any) {
    records.value = [];
    total.value = 0;
    error.value = cause?.response?.data?.detail || cause?.message || '加载对话日志失败';
  } finally { loading.value = false; }
}
async function exportCurrentFilters() {
  exporting.value = true;
  error.value = '';
  try {
    const download = props.access === 'admin' ? downloadAdminConversationLogs : downloadOwnerConversationLogs;
    await download(props.appId, buildConversationLogExportParams());
  } catch (cause: any) {
    error.value = cause?.message || '导出对话日志失败';
    message.error(error.value);
  } finally { exporting.value = false; }
}
watch(() => props.appId, () => reload(true));
watch(range, () => reload(true));
onMounted(() => reload(true));
</script>

<style lang="less" scoped>
.log-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-bottom: 16px; }
.log-filter-item { display: flex; flex-direction: column; margin: 0; }
.log-filter-item :deep(.ant-form-item-label) { padding: 0 0 6px; line-height: 20px; text-align: left; }
.log-filter-item :deep(.ant-form-item-label > label) { height: 20px; }
.log-filter-keyword { padding-top: 26px; }
.log-filter-actions { align-self: flex-end; }
.log-alert { margin-bottom: 12px; }
.log-pagination { display: flex; align-items: center; justify-content: space-between; margin-top: 14px; color: #71717a; }
.detail-meta { margin: 0 0 16px; color: #52525b; }
.feedback-summary { display: inline-flex; gap: 6px; white-space: nowrap; }
.feedback-count { display: inline-flex; align-items: center; gap: 3px; padding: 2px 6px; border-radius: 5px; font-size: 12px; line-height: 18px; }
.feedback-count.like { color: #4f83ff; background: #eff5ff; }
.feedback-count.dislike { color: #ef6172; background: #fff1f2; }
.conversation-thread { display: grid; gap: 18px; }
.conversation-message { display: flex; align-items: flex-start; gap: 10px; }
.conversation-message.user { flex-direction: row-reverse; }
.conversation-avatar { display: inline-flex; flex: 0 0 32px; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 50%; color: #5b7fc7; background: #e8f1ff; }
.conversation-message.user .conversation-avatar { color: #667085; background: #f2f4f7; }
.conversation-content { display: flex; flex: 1; flex-direction: column; align-items: flex-start; min-width: 0; }
.conversation-message.user .conversation-content { align-items: flex-end; }
.conversation-content header { display: flex; gap: 8px; margin-bottom: 5px; color: #71717a; font-size: 12px; }
.conversation-bubble { max-width: min(560px, 100%); padding: 10px 12px; border: 1px solid #e4e4e7; border-radius: 14px 14px 14px 4px; background: #f8fafc; }
.conversation-message.user .conversation-bubble { border-color: #dbeafe; border-radius: 14px 14px 4px 14px; background: #edf4ff; }
.conversation-content footer { display: flex; align-items: center; gap: 8px; margin-top: 7px; color: #71717a; font-size: 12px; }
.message-feedback { display: inline-flex; align-items: center; gap: 4px; }
pre { max-height: 280px; margin: 0; overflow: auto; white-space: pre-wrap; word-break: break-word; font: inherit; }
@media (max-width: 720px) { .log-filters { display: grid; gap: 10px; } .log-filter-keyword { padding-top: 0; } .log-filter-actions { justify-self: start; } }
</style>
