<template>
  <section class="agent-metrics-page">
    <header class="agent-metrics-header">
      <div>
        <button type="button" class="back-link" @click="router.push('/center/my-agent')">
          <ArrowLeftOutlined /> 返回我的智能体
        </button>
        <h2>监测</h2>
        <p>{{ app?.name || '当前智能体' }}的使用数据</p>
      </div>
    </header>

    <a-select v-model:value="selectedRange" class="range-select" :options="rangeOptions" :disabled="loading" aria-label="监测时间范围" />

    <div v-if="loading && !metrics" class="metric-grid" aria-label="正在加载监测数据">
      <a-skeleton v-for="item in 6" :key="item" active :paragraph="false" class="metric-card-skeleton" />
    </div>
    <template v-else>
      <div v-if="loadFailed" class="metrics-failed" role="alert">
        <WarningOutlined />
        <div>
          <strong>监测数据暂时无法加载</strong>
          <p>请稍后重试；如果问题持续存在，请检查智能体服务连接。</p>
        </div>
        <a-button type="primary" :loading="loading" @click="loadMetrics">重试</a-button>
      </div>

      <template v-else-if="metrics">
        <div class="metric-grid">
          <AgentMetricsTrendChart
            v-for="item in metricCards"
            :key="item.key"
            :daily="metrics.daily"
            :start-date="metrics.startDate"
            :end-date="metrics.endDate"
            :range-label="selectedRangeLabel"
            :metric="item.key"
            :title="item.label"
            :value="item.value"
            :tip="item.tip"
            :available="item.available"
          />
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ArrowLeftOutlined, WarningOutlined } from '@ant-design/icons-vue';
import AgentMetricsTrendChart from '../components/AgentMetricsTrendChart.vue';
import {
  queryWorkflowAppById,
  queryWorkflowAppMetrics,
  type AiWorkflowApp,
  type MetricRange,
  type WorkflowAppMetrics,
} from '../../workflow/api/workflow.api';

defineOptions({ name: 'CenterAgentMetricsPage' });

const route = useRoute();
const router = useRouter();
const appId = computed(() => String(route.params.appId || ''));
const app = ref<AiWorkflowApp | null>(null);
const metrics = ref<WorkflowAppMetrics | null>(null);
const loading = ref(false);
const loadFailed = ref(false);
const selectedRange = ref<MetricRange>('last_7_days');
let appRequestId = 0;
let metricsRequestId = 0;

type MetricKey = 'sessions' | 'activeUsers' | 'newUsers' | 'returningUsers' | 'averageMessages' | 'messages';

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

const selectedRangeLabel = computed(() => rangeOptions.find((item) => item.value === selectedRange.value)?.label || '所选时间');

const metricCards = computed<Array<{
  key: MetricKey;
  label: string;
  value: number | string;
  tip: string;
  available: boolean;
}>>(() => {
  const totals = metrics.value?.totals;
  return [
    {
      key: 'sessions',
      label: '全部会话数',
      value: totals?.sessions ?? 0,
      tip: '反映 AI 每天的会话总次数，提示词编排和调试消息不计入。',
      available: true,
    },
    {
      key: 'activeUsers',
      label: '活跃用户数',
      value: totals?.activeUsers ?? 0,
      tip: '与 AI 有效互动，即完成一问一答以上的唯一用户数；提示词编排和调试会话不计入。',
      available: true,
    },
    {
      key: 'newUsers',
      label: '新增用户数',
      value: totals?.newUsers ?? 0,
      tip: '在所选时间范围内，首次与该智能体完成有效问答的唯一用户数，反映拉新效果。',
      available: true,
    },
    {
      key: 'returningUsers',
      label: '回访用户数',
      value: totals?.returningUsers ?? 0,
      tip: '在所选时间范围内活跃，且此前已在该智能体完成有效问答的唯一用户数，反映留存与复用。',
      available: true,
    },
    {
      key: 'averageMessages',
      label: '平均每会话消息数',
      value: totals?.averageMessages ?? 0,
      tip: '反映每个有效会话中，AI 成功回复的平均条数；适用于对话型智能体和工作流。',
      available: true,
    },
    {
      key: 'messages',
      label: '全部消息数',
      value: totals?.messages ?? 0,
      tip: '反映 AI 每天的互动总次数，每回答用户一个问题算一条 Message。',
      available: true,
    },
  ];
});

async function loadMetrics() {
  const currentAppId = appId.value;
  const requestId = ++metricsRequestId;
  if (!currentAppId) {
    loadFailed.value = true;
    return;
  }
  loading.value = true;
  loadFailed.value = false;
  try {
    const nextMetrics = await queryWorkflowAppMetrics(currentAppId, selectedRange.value);
    if (requestId === metricsRequestId && currentAppId === appId.value) {
      metrics.value = nextMetrics;
    }
  } catch {
    if (requestId === metricsRequestId && currentAppId === appId.value) {
      loadFailed.value = true;
    }
  } finally {
    if (requestId === metricsRequestId) {
      loading.value = false;
    }
  }
}

async function loadApp() {
  const currentAppId = appId.value;
  const requestId = ++appRequestId;
  if (!currentAppId) {
    loadFailed.value = true;
    return;
  }
  try {
    const nextApp = await queryWorkflowAppById(currentAppId);
    if (requestId === appRequestId && currentAppId === appId.value) {
      app.value = nextApp;
    }
  } catch {
    if (requestId === appRequestId && currentAppId === appId.value) {
      loadFailed.value = true;
    }
  }
}

function loadPage() {
  app.value = null;
  metrics.value = null;
  loadFailed.value = false;
  void loadApp();
  void loadMetrics();
}

onMounted(() => {
  loadPage();
});

watch(
  () => route.params.appId,
  (nextAppId, previousAppId) => {
    if (nextAppId !== previousAppId) loadPage();
  },
);

watch(selectedRange, () => {
  void loadMetrics();
});
</script>

<style scoped lang="less">
.agent-metrics-page {
  width: min(1280px, 100%);
  margin: 0 auto;
  padding: 28px 28px 48px;
}

.agent-metrics-header {
  margin-bottom: 14px;

  h2 {
    margin: 12px 0 0;
    color: #111827;
    font-size: 20px;
    line-height: 1.2;
  }

  p {
    margin: 6px 0 0;
    color: #7b8798;
    font-size: 13px;
  }
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
}

.back-link:hover {
  color: #4f6ef7;
}

.back-link:focus-visible {
  border-radius: 4px;
  outline: 2px solid #4f6ef7;
  outline-offset: 3px;
}

.range-select {
  width: 108px;
  margin-bottom: 18px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px;
}

.metric-card-skeleton {
  min-height: 292px;
  padding: 22px;
  border: 1px solid #e7eaf0;
  border-radius: 12px;
  background: #fff;
}

.metrics-failed {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 20px;
  margin-bottom: 18px;
  border: 1px solid #e7eaf0;
  border-radius: 14px;
  background: #fff;
  color: #475569;

  > .anticon {
    color: #4f6ef7;
    font-size: 20px;
  }

  strong {
    display: block;
    color: #0f172a;
  }

  p {
    margin: 4px 0 0;
    color: #64748b;
    font-size: 13px;
  }

  .ant-btn {
    margin-left: auto;
  }
}

@media (max-width: 860px) {
  .metric-grid {
    gap: 16px;
  }
}

@media (max-width: 560px) {
  .agent-metrics-page {
    padding: 24px 16px 36px;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }

  .metrics-failed {
    align-items: flex-start;
    flex-wrap: wrap;

    .ant-btn {
      margin-left: 34px;
    }
  }
}

@media (prefers-reduced-motion: reduce) {
  .back-link {
    transition: none;
  }
}
</style>
