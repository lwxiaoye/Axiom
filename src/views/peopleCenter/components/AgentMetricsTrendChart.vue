<template>
  <section class="agent-metric-trend" :aria-label="`${title}趋势`">
    <div class="agent-metric-trend-heading">
      <div class="metric-title-row">
        <h3>{{ title }}</h3>
        <a-tooltip :title="tip">
          <QuestionCircleOutlined class="metric-tip" aria-label="指标说明" />
        </a-tooltip>
      </div>
      <span>{{ rangeLabel }}</span>
    </div>
    <strong :class="{ unavailable: !available }">{{ value }}</strong>
    <div v-if="available" ref="chartRef" class="agent-metric-chart" role="img" :aria-label="`${title}在${startDate}至${endDate}的每日趋势`" />
    <p v-else class="unavailable-note">仅对话型应用提供</p>
  </section>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';
import { QuestionCircleOutlined } from '@ant-design/icons-vue';
import type { WorkflowAppMetricsDaily } from '../../workflow/api/workflow.api';

type MetricKey = 'sessions' | 'activeUsers' | 'newUsers' | 'returningUsers' | 'averageMessages' | 'messages';

const props = defineProps<{
  daily: WorkflowAppMetricsDaily[];
  startDate: string;
  endDate: string;
  rangeLabel: string;
  metric: MetricKey;
  title: string;
  value: number | string;
  tip: string;
  available: boolean;
}>();

const chartRef = ref<HTMLElement | null>(null);
let chart: echarts.ECharts | null = null;

function resizeChart() {
  chart?.resize();
}

async function renderChart() {
  await nextTick();
  if (!chartRef.value || !props.available) return;
  chart ??= echarts.init(chartRef.value);
  const colors: Record<MetricKey, string> = {
    sessions: '#0b8da3',
    activeUsers: '#ef7d42',
    newUsers: '#4e73b8',
    returningUsers: '#b4538a',
    averageMessages: '#7257b8',
    messages: '#4e73b8',
  };
  const values = props.daily.map((item) => {
    if (props.metric === 'averageMessages') return item.sessions ? Number((item.messages / item.sessions).toFixed(2)) : 0;
    return item[props.metric];
  });
  chart.setOption(
    {
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      grid: { left: 48, right: 18, top: 18, bottom: 38 },
      tooltip: { trigger: 'axis', valueFormatter: (raw: number | string) => String(raw) },
      xAxis: {
        type: 'category',
        data: props.daily.map((item) => item.date),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#e2e8f0' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#94a3b8',
          fontSize: 11,
          formatter: (value: string) => value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$2/$3'),
        },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#edf1f5' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
      series: [{ type: 'line', smooth: false, showSymbol: true, symbolSize: 5, data: values, color: colors[props.metric], lineStyle: { width: 2 } }],
    },
    true
  );
}

watch(() => [props.daily, props.metric, props.available], renderChart, { deep: true });
onMounted(() => {
  window.addEventListener('resize', resizeChart);
  void renderChart();
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart);
  chart?.dispose();
  chart = null;
});
</script>

<style scoped lang="less">
.agent-metric-trend {
  min-height: 292px;
  padding: 18px 22px 14px;
  border: 1px solid #e8edf3;
  border-radius: 12px;
  background: #fff;
}

.agent-metric-trend-heading {
  color: #5e6e83;
  font-size: 12px;

  span {
    display: block;
    margin-top: 2px;
  }
}

.metric-title-row {
  display: flex;
  align-items: center;
  gap: 5px;

  h3 {
    margin: 0;
    color: #1f365c;
    font-size: 14px;
    font-weight: 600;
  }
}

.metric-tip {
  color: #9eabbc;
  font-size: 13px;
  cursor: help;
}

.agent-metric-trend > strong {
  display: block;
  margin-top: 8px;
  color: #1b365f;
  font-size: 31px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.agent-metric-trend > strong.unavailable {
  color: #9aa6b5;
}

.agent-metric-chart {
  height: 158px;
  margin-top: 10px;
}

.unavailable-note {
  display: grid;
  height: 158px;
  margin: 10px 0 0;
  place-items: center;
  color: #9aa6b5;
  font-size: 13px;
}

@media (max-width: 640px) {
  .agent-metric-trend {
    padding: 18px;
  }

  .agent-metric-chart {
    height: 150px;
  }
}
</style>
