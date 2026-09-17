<template>
  <a-drawer
    :open="open"
    title="评测记录"
    width="min(920px, 96vw)"
    @close="$emit('update:open', false)"
  >
    <section class="evaluation-panel" aria-label="工作流评测记录">
      <p class="evaluation-intro">查看运行轨迹、节点中间结果与关联模型调用，定位质量、成本和时延问题。</p>
      <a-form layout="inline" class="evaluation-filters" @submit.prevent="reload(true)">
        <a-form-item label="运行时间" class="filter-item filter-time">
          <a-range-picker
            v-model:value="timeRange"
            show-time
            value-format="YYYY-MM-DDTHH:mm:ss"
            :disabled="loading"
          />
        </a-form-item>
        <a-form-item label="状态" class="filter-item">
          <a-select v-model:value="status" allow-clear placeholder="全部" :options="statusOptions" :disabled="loading" style="width: 112px" />
        </a-form-item>
        <a-form-item label="方式" class="filter-item">
          <a-select v-model:value="mode" allow-clear placeholder="全部" :options="modeOptions" :disabled="loading" style="width: 124px" />
        </a-form-item>
        <a-form-item label="最小耗时" class="filter-item">
          <a-input-number v-model:value="minDurationMs" :min="0" :precision="0" :disabled="loading" placeholder="毫秒" style="width: 104px" />
        </a-form-item>
        <a-form-item label="最小 Token" class="filter-item">
          <a-input-number v-model:value="minTotalTokens" :min="0" :precision="0" :disabled="loading" placeholder="总量" style="width: 104px" />
        </a-form-item>
        <a-space class="filter-actions">
          <a-button type="primary" :loading="loading" @click="reload(true)">查询</a-button>
          <a-button :disabled="loading" @click="resetFilters">重置</a-button>
        </a-space>
      </a-form>

      <a-alert v-if="error" class="evaluation-alert" type="error" show-icon :message="error">
        <template #action><a-button size="small" @click="reload(false)">重试</a-button></template>
      </a-alert>

      <a-table
        v-if="records.length || loading"
        :columns="columns"
        :data-source="records"
        :loading="loading"
        :pagination="false"
        :scroll="{ x: 940 }"
        row-key="runId"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'status'">
            <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'mode'">{{ modeLabel(record.mode) }}</template>
          <template v-else-if="column.key === 'input'">
            <span class="table-ellipsis" :title="record.input">{{ record.input || '-' }}</span>
          </template>
          <template v-else-if="column.key === 'duration'">{{ formatDuration(record.durationMs) }}</template>
          <template v-else-if="column.key === 'startedAt'">{{ formatTime(record.startedAt) }}</template>
          <template v-else-if="column.key === 'action'"><a-button type="link" size="small" @click="openDetail(record)">查看详情</a-button></template>
        </template>
      </a-table>
      <a-empty v-else-if="!error" :image="simpleImage" class="evaluation-empty">
        <template #description>暂无符合筛选条件的评测记录；执行或调试一次工作流后会在这里保留轨迹。</template>
      </a-empty>
      <div class="evaluation-pagination">
        <span>共 {{ total }} 条</span>
        <a-pagination v-model:current="pageNo" :total="total" :page-size="pageSize" size="small" show-less-items @change="reload(false)" />
      </div>
    </section>

    <a-drawer :open="detailOpen" title="运行详情" width="min(760px, 94vw)" @close="detailOpen = false">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <a-descriptions size="small" bordered :column="2" class="detail-summary">
            <a-descriptions-item label="运行状态"><a-tag :color="statusColor(detail.status)">{{ statusLabel(detail.status) }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="执行方式">{{ modeLabel(detail.mode) }}</a-descriptions-item>
            <a-descriptions-item label="开始时间">{{ formatTime(detail.startedAt) }}</a-descriptions-item>
            <a-descriptions-item label="总耗时">{{ formatDuration(detail.durationMs) }}</a-descriptions-item>
            <a-descriptions-item label="运行标识" :span="2"><span class="run-id">{{ detail.runId }}</span></a-descriptions-item>
          </a-descriptions>
          <a-alert v-if="detail.errorMessage" type="error" show-icon :message="detail.errorMessage" class="detail-error" />

          <section class="detail-section">
            <h4>最终输出</h4>
            <pre class="detail-pre">{{ detail.output || '-' }}</pre>
          </section>

          <section class="detail-section">
            <h4>模型调用 <span class="section-count">{{ detail.modelCalls.length }}</span></h4>
            <a-table :columns="modelColumns" :data-source="detail.modelCalls" :pagination="false" :scroll="{ x: 790 }" row-key="id" size="small">
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'modelStatus'"><a-tag :color="modelStatusColor(record.status)">{{ modelStatusLabel(record.status) }}</a-tag></template>
                <template v-else-if="column.key === 'tokens'">{{ tokenSummary(record) }}</template>
                <template v-else-if="column.key === 'duration'">{{ formatDuration(record.durationMs) }}</template>
                <template v-else-if="column.key === 'nodeId'"><span class="table-ellipsis" :title="record.nodeId">{{ record.nodeId || '-' }}</span></template>
              </template>
            </a-table>
          </section>

          <a-collapse ghost class="detail-collapse">
            <a-collapse-panel key="variables" header="变量快照"><pre class="detail-pre">{{ formatJson(detail.variables) }}</pre></a-collapse-panel>
            <a-collapse-panel key="outputs" header="节点输出快照"><pre class="detail-pre">{{ formatJson(detail.outputs) }}</pre></a-collapse-panel>
            <a-collapse-panel key="trace" :header="`节点轨迹（${detail.nodeRuns.length}）`">
              <a-collapse ghost>
                <a-collapse-panel v-for="(nodeRun, index) in detail.nodeRuns" :key="`${nodeRun.nodeId}-${index}`">
                  <template #header>
                    <span class="node-trace-header">
                      <a-tag :color="statusColor(nodeRun.status)">{{ statusLabel(nodeRun.status) }}</a-tag>
                      {{ nodeRun.nodeLabel || nodeRun.nodeId || `节点 ${index + 1}` }}
                      <small>{{ nodeRun.nodeType }} · {{ formatDuration(nodeRun.durationMs) }}</small>
                    </span>
                  </template>
                  <pre class="detail-pre">{{ formatJson(nodeRun) }}</pre>
                </a-collapse-panel>
              </a-collapse>
            </a-collapse-panel>
          </a-collapse>
        </template>
      </a-spin>
    </a-drawer>
  </a-drawer>
</template>

<script setup lang="ts">
import { Empty } from 'ant-design-vue';
import { ref, watch } from 'vue';
import {
  queryWorkflowEvaluationRunDetail,
  queryWorkflowEvaluationRuns,
  type WorkflowEvaluationModelCall,
  type WorkflowEvaluationRunDetail,
  type WorkflowEvaluationRunRecord,
} from '../../api/workflow.api';

const props = defineProps<{ open: boolean; appId: string }>();
defineEmits<{ (e: 'update:open', value: boolean): void }>();

const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE;
const pageNo = ref(1);
const pageSize = 20;
const timeRange = ref<string[]>([]);
const status = ref<string>();
const mode = ref<string>();
const minDurationMs = ref<number>();
const minTotalTokens = ref<number>();
const records = ref<WorkflowEvaluationRunRecord[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref('');
const detailOpen = ref(false);
const detailLoading = ref(false);
const detail = ref<WorkflowEvaluationRunDetail | null>(null);

const statusOptions = [
  ['success', '成功'], ['completed', '已完成'], ['failed', '失败'], ['waiting', '等待输入'], ['paused', '已暂停'], ['stopped', '已停止'],
].map(([value, label]) => ({ value, label }));
const modeOptions = [
  ['execute', '正式执行'], ['debug', '全流程调试'], ['debug_step', '单步调试'],
].map(([value, label]) => ({ value, label }));
const columns = [
  { title: '状态', key: 'status', width: 96 },
  { title: '方式', key: 'mode', width: 108 },
  { title: '调试问题 / 输入', key: 'input', width: 230 },
  { title: '耗时', key: 'duration', width: 96 },
  { title: '开始时间', key: 'startedAt', width: 170 },
  { title: '操作', key: 'action', width: 92, fixed: 'right' as const },
];
const modelColumns = [
  { title: '模型', dataIndex: 'model', key: 'model', width: 180, ellipsis: true },
  { title: '节点', key: 'nodeId', width: 180 },
  { title: '状态', key: 'modelStatus', width: 96 },
  { title: '耗时', key: 'duration', width: 92 },
  { title: 'Token（入/出/推理）', key: 'tokens', width: 170 },
];

function statusLabel(value: string) {
  return statusOptions.find((item) => item.value === value)?.label || value || '未知';
}
function statusColor(value: string) {
  if (['success', 'completed'].includes(value)) return 'success';
  if (value === 'failed') return 'error';
  if (['waiting', 'paused'].includes(value)) return 'processing';
  return 'default';
}
function modelStatusLabel(value: string) { return value === 'succeeded' ? '成功' : value === 'failed' ? '失败' : value || '处理中'; }
function modelStatusColor(value: string) { return value === 'succeeded' ? 'success' : value === 'failed' ? 'error' : 'processing'; }
function modeLabel(value: string) { return modeOptions.find((item) => item.value === value)?.label || value || '-'; }
function formatTime(value?: string | null) { return value ? value.replace('T', ' ').slice(0, 19) : '-'; }
function formatDuration(value?: number | null) { return value == null ? '-' : `${Number(value).toLocaleString()} ms`; }
function tokenSummary(value: WorkflowEvaluationModelCall) {
  return [value.inputTokens, value.outputTokens, value.reasoningTokens].map((item) => item == null ? '-' : Number(item).toLocaleString()).join(' / ');
}
function formatJson(value: unknown) {
  try { return JSON.stringify(value ?? {}, null, 2); } catch { return String(value ?? ''); }
}
function requestParams() {
  return {
    startAt: timeRange.value?.[0] || undefined,
    endAt: timeRange.value?.[1] || undefined,
    status: status.value || undefined,
    mode: mode.value || undefined,
    minDurationMs: minDurationMs.value,
    minTotalTokens: minTotalTokens.value,
    pageNo: pageNo.value,
    pageSize,
  };
}

async function reload(resetPage: boolean) {
  if (!props.appId) return;
  if (resetPage) pageNo.value = 1;
  loading.value = true;
  error.value = '';
  try {
    const result = await queryWorkflowEvaluationRuns(props.appId, requestParams());
    records.value = result.records || [];
    total.value = Number(result.total || 0);
  } catch (cause: any) {
    records.value = [];
    total.value = 0;
    error.value = cause?.response?.data?.detail || cause?.message || '加载评测记录失败，请稍后重试。';
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  timeRange.value = [];
  status.value = undefined;
  mode.value = undefined;
  minDurationMs.value = undefined;
  minTotalTokens.value = undefined;
  reload(true);
}

async function openDetail(record: WorkflowEvaluationRunRecord) {
  if (!props.appId) return;
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = null;
  try {
    detail.value = await queryWorkflowEvaluationRunDetail(props.appId, record.runId);
  } catch (cause: any) {
    detailOpen.value = false;
    error.value = cause?.response?.data?.detail || cause?.message || '加载运行详情失败，请稍后重试。';
  } finally {
    detailLoading.value = false;
  }
}

watch(() => [props.open, props.appId], ([open]) => {
  if (open) reload(true);
}, { immediate: true });
</script>

<style lang="less" scoped>
.evaluation-panel { min-height: 340px; }
.evaluation-intro { margin: 0 0 16px; color: #64748b; line-height: 1.6; }
.evaluation-filters { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 12px; margin-bottom: 16px; }
.filter-item { display: flex; flex-direction: column; margin: 0; }
.filter-item :deep(.ant-form-item-label) { padding: 0 0 5px; line-height: 20px; text-align: left; }
.filter-item :deep(.ant-form-item-label > label) { height: 20px; }
.filter-time :deep(.ant-picker) { width: 280px; }
.filter-actions { align-self: flex-end; }
.evaluation-alert { margin-bottom: 12px; }
.evaluation-pagination { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 14px; color: #64748b; font-size: 13px; }
.evaluation-empty { margin: 68px 0; }
.table-ellipsis { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.detail-summary { margin-bottom: 16px; }
.detail-error { margin-bottom: 16px; }
.run-id { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; word-break: break-all; }
.detail-section { margin: 20px 0; }
.detail-section h4 { display: flex; align-items: center; gap: 6px; margin: 0 0 10px; color: #1e293b; font-size: 14px; }
.section-count { color: #64748b; font-size: 12px; font-weight: 400; }
.detail-collapse { margin-top: 16px; }
.detail-pre { max-height: 360px; margin: 0; overflow: auto; white-space: pre-wrap; word-break: break-word; color: #334155; font: 12px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.node-trace-header { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.node-trace-header small { color: #64748b; }
@media (max-width: 720px) {
  .evaluation-filters { display: grid; gap: 10px; }
  .filter-time :deep(.ant-picker) { width: min(100%, 320px); }
  .filter-actions { justify-self: start; }
  .evaluation-pagination { align-items: flex-start; flex-direction: column; }
}
</style>
