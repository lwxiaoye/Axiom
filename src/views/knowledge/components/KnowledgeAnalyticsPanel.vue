<template>
  <section class="knowledge-analytics" aria-label="知识库运营统计">
    <header class="analytics-header">
      <div>

        <h2>{{ isSingleBase ? '运营统计' : '知识库运营统计' }}</h2>
        <p>统计从功能上线后开始累计；多知识库检索按实际参与的知识库分别归属。</p>
      </div>
      <div class="analytics-range" aria-label="统计时间范围">
        <a-button v-for="item in presets" :key="item.key" :type="preset === item.key ? 'primary' : 'default'" size="small" @click="applyPreset(item.key)">
          {{ item.label }}
        </a-button>
        <a-range-picker v-model:value="customRange" size="small" value-format="YYYY-MM-DD" @change="applyCustomRange" />
      </div>
    </header>

    <a-alert v-if="error" type="warning" show-icon class="analytics-error" role="alert" message="运营统计加载失败">
      <template #description>请稍后重试；持续失败请联系管理员。</template>
      <template #action><a-button size="small" @click="load">重试</a-button></template>
    </a-alert>

    <a-skeleton v-if="loading && !overview" active :paragraph="{ rows: 8 }" />
    <template v-else-if="overview">
      <!-- 接口通了但区间内没有一条日志：这是「还没人用」，不是加载失败，不能拿报错文案当空态 -->
      <a-alert v-if="!hasRetrievals" type="info" show-icon class="analytics-empty" message="还没有检索记录">
        <template #description>{{ emptyHint }}</template>
      </a-alert>
      <div :class="['analytics-stock-grid', { 'single-base-stock': isSingleBase }]" aria-label="当前库存">
        <article v-if="!isSingleBase"><span>知识库总量</span><strong>{{ number(overview.stock?.knowledgeBaseCount) }}</strong><small>当前未删除</small></article>
        <article><span>文件总量</span><strong>{{ number(overview.stock?.documentCount) }}</strong><small>当前未删除</small></article>
        <article><span>分片总量</span><strong>{{ number(overview.stock?.chunkCount) }}</strong><small>当前可用库存</small></article>
      </div>

      <div class="analytics-metric-grid" aria-label="区间运营指标">
        <article><span>知识问答量</span><strong>{{ number(overview.metrics?.qaCount) }}</strong><small>按问答轮次去重</small></article>
        <article><span>召回次数</span><strong>{{ number(overview.metrics?.retrievalCount) }}</strong><small>真实检索执行次数</small></article>
        <article><span>文件召回</span><strong>{{ number(overview.metrics?.fileRetrievalCount) }}</strong><small>按命中文件累计</small></article>
        <article><span>分片命中</span><strong>{{ number(overview.metrics?.chunkHitCount) }}</strong><small>最终返回的唯一分片</small></article>
        <article class="metric-alert"><span>无命中率</span><strong>{{ percent(overview.metrics?.noHitRate) }}</strong><small>{{ number(overview.metrics?.noHitCount) }} 次无命中</small></article>
      </div>

      <div class="analytics-main-grid">
        <section class="analytics-card trend-card">
          <div class="card-heading">
            <div><h3>使用趋势</h3><p>{{ overview.from }} 至 {{ overview.to }}</p></div>
            <span>召回 / 问答</span>
          </div>
          <div v-if="hasTrend" ref="chartRef" class="analytics-chart" role="img" :aria-label="`${overview.from} 至 ${overview.to} 的知识库运营趋势`" />
          <a-empty v-else :image="simpleImage" description="该区间还没有检索记录" />
        </section>

        <section class="analytics-card quality-card">
          <div class="card-heading"><div><h3>检索质量</h3><p>正式来源的最终召回结果</p></div></div>
          <dl>
            <div><dt>平均延迟</dt><dd>{{ number(overview.metrics?.averageLatencyMs) }} <small>ms</small></dd></div>
            <div><dt>无命中次数</dt><dd>{{ number(overview.metrics?.noHitCount) }}</dd></div>
            <div><dt>命中率</dt><dd>{{ percent(1 - Number(overview.metrics?.noHitRate || 0)) }}</dd></div>
          </dl>
          <p class="quality-note">手工召回测试不会计入这里的数据。</p>
        </section>
      </div>

      <section v-if="!isSingleBase" class="analytics-card ranking-card">
        <div class="card-heading"><div><h3>知识库排行</h3><p>按召回次数排序；多库检索按命中归属累计。</p></div></div>
        <a-table :columns="baseColumns" :data-source="overview.knowledgeBases || []" :pagination="false" size="small" row-key="id" :scroll="{ x: 760 }">
          <template #bodyCell="{ column, record }">
            <a-button v-if="column.key === 'name'" type="link" size="small" class="ranking-link" @click="emit('openKnowledge', record.id)">{{ record.name }}</a-button>
            <span v-else-if="column.key === 'noHitRate'">{{ percent(record.noHitRate) }}</span>
          </template>
        </a-table>
      </section>

      <section class="analytics-card ranking-card">
        <div class="card-heading"><div><h3>热门文件</h3><p>同次召回内，同一文件只计一次文件召回。</p></div></div>
        <a-table :columns="documentColumns" :data-source="overview.documents || []" :pagination="false" size="small" row-key="id" :scroll="{ x: 620 }">
          <template #bodyCell="{ column, record }">
            <span v-if="column.key === 'noHitRate'">{{ percent(record.noHitRate) }}</span>
          </template>
        </a-table>
      </section>

      <!-- 只有 agent-api 的单库统计返回 topQueries；无命中的热门问题就是知识库该补的内容 -->
      <section v-if="overview.topQueries?.length" class="analytics-card ranking-card">
        <div class="card-heading"><div><h3>热门问题</h3><p>区间内被检索最多的问题；无命中次数高的说明知识库缺这块内容。</p></div></div>
        <a-table :columns="queryColumns" :data-source="overview.topQueries" :pagination="false" size="small" row-key="query" :scroll="{ x: 620 }" />
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import * as echarts from 'echarts';
import { Empty } from 'ant-design-vue';
import {
  getManagedKnowledgeAnalyticsOverview,
  getManagedKnowledgeBaseAnalytics,
  getOwnedKnowledgeBaseAnalytics,
} from '../knowledge.api';
import type { KnowledgeAnalyticsOverview, KnowledgeAnalyticsPreset, KnowledgeAnalyticsRange } from '../knowledge.types';

const props = withDefaults(defineProps<{
  scope: 'admin' | 'owner';
  knowledgeId?: string;
}>(), { knowledgeId: '' });

const emit = defineEmits<{ openKnowledge: [id: string] }>();
const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE;
const overview = shallowRef<KnowledgeAnalyticsOverview>();
const loading = ref(false);
const error = ref(false);
const preset = ref<KnowledgeAnalyticsPreset>('last30');
const customRange = ref<string[]>([]);
const chartRef = ref<HTMLElement>();
let chart: echarts.ECharts | undefined;

const presets: Array<{ key: Exclude<KnowledgeAnalyticsPreset, 'custom'>; label: string }> = [
  { key: 'today', label: '今天' },
  { key: 'last7', label: '近 7 天' },
  { key: 'last30', label: '近 30 天' },
];
const isSingleBase = computed(() => Boolean(props.knowledgeId));
const hasTrend = computed(() => Boolean(overview.value?.trend?.length));
// 区间内有没有任何一次检索：决定顶部是否提示「还没有检索记录」
const hasRetrievals = computed(() => Number(overview.value?.metrics?.retrievalCount || 0) > 0);
const emptyHint = computed(() => (
  isSingleBase.value
    ? '所选区间内还没有人在对话、智能体或工作流里检索到这个知识库。检索一旦发生就会在这里累计；页面上的「召回测试」不计入。'
    : '所选区间内还没有正式检索记录。'
));
const baseColumns = [
  { title: '知识库', key: 'name', dataIndex: 'name' },
  { title: '问答量', dataIndex: 'qaCount', width: 100 },
  { title: '召回次数', dataIndex: 'retrievalCount', width: 110 },
  { title: '文件召回', dataIndex: 'fileRetrievalCount', width: 110 },
  { title: '分片命中', dataIndex: 'chunkHitCount', width: 110 },
  { title: '无命中率', key: 'noHitRate', width: 100 },
];
const documentColumns = [
  { title: '文件', dataIndex: 'name' },
  { title: '文件召回', dataIndex: 'fileRetrievalCount', width: 120 },
  { title: '分片命中', dataIndex: 'chunkHitCount', width: 120 },
];
const queryColumns = [
  { title: '问题', dataIndex: 'query', ellipsis: true },
  { title: '检索次数', dataIndex: 'count', width: 110 },
  { title: '无命中', dataIndex: 'noHitCount', width: 100 },
];

function isoDate(date: Date) {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}

function rangeForPreset(value: KnowledgeAnalyticsPreset): KnowledgeAnalyticsRange {
  const today = new Date();
  if (value === 'today') return { from: isoDate(today), to: isoDate(today) };
  if (value === 'last7') {
    const start = new Date(today);
    start.setDate(start.getDate() - 6);
    return { from: isoDate(start), to: isoDate(today) };
  }
  if (value === 'custom') return { from: customRange.value[0], to: customRange.value[1] };
  const start = new Date(today);
  start.setDate(start.getDate() - 29);
  return { from: isoDate(start), to: isoDate(today) };
}

function applyPreset(value: Exclude<KnowledgeAnalyticsPreset, 'custom'>) {
  preset.value = value;
  customRange.value = [];
  void load();
}

function applyCustomRange() {
  if (!customRange.value[0] || !customRange.value[1]) return;
  preset.value = 'custom';
  void load();
}

async function load() {
  loading.value = true;
  error.value = false;
  try {
    const range = rangeForPreset(preset.value);
    if (props.knowledgeId) {
      overview.value = props.scope === 'owner'
        ? await getOwnedKnowledgeBaseAnalytics(props.knowledgeId, range)
        : await getManagedKnowledgeBaseAnalytics(props.knowledgeId, range);
    } else {
      overview.value = await getManagedKnowledgeAnalyticsOverview(range);
    }
    await renderChart();
  } catch {
    error.value = true;
  } finally {
    loading.value = false;
  }
}

async function renderChart() {
  await nextTick();
  const data = overview.value?.trend || [];
  if (!chartRef.value || !data.length) return;
  chart ??= echarts.init(chartRef.value);
  chart.setOption({
    animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    tooltip: { trigger: 'axis' },
    legend: { data: ['召回次数', '知识问答量'], right: 4, top: 0, textStyle: { color: '#64748b', fontSize: 12 } },
    grid: { left: 42, right: 18, top: 42, bottom: 30 },
    xAxis: { type: 'category', boundaryGap: false, data: data.map((item) => item.date.slice(5)), axisTick: { show: false }, axisLine: { lineStyle: { color: '#e2e8f0' } }, axisLabel: { color: '#64748b' } },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: '#64748b' } },
    series: [
      { name: '召回次数', type: 'line', data: data.map((item) => item.retrievalCount), symbol: 'circle', symbolSize: 5, lineStyle: { width: 2 }, itemStyle: { color: '#243b75' } },
      { name: '知识问答量', type: 'line', data: data.map((item) => item.qaCount), symbol: 'circle', symbolSize: 5, lineStyle: { width: 2 }, itemStyle: { color: '#7f8db4' } },
    ],
  }, true);
}

function number(value?: number) {
  return Number(value || 0).toLocaleString();
}

function percent(value?: number) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function resizeChart() {
  chart?.resize();
}

watch(() => [props.scope, props.knowledgeId], () => void load());
watch(() => overview.value?.trend, () => void renderChart(), { deep: true });
onMounted(() => {
  window.addEventListener('resize', resizeChart);
  void load();
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart);
  chart?.dispose();
  chart = undefined;
});
</script>

<style scoped lang="less">
.knowledge-analytics { color: #17213b; }
.analytics-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.analytics-eyebrow { color: #63729a; font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.analytics-header h2 { margin: 4px 0; font-size: 22px; letter-spacing: -.02em; }
.analytics-header p, .card-heading p { margin: 0; color: #75829a; font-size: 13px; }
.analytics-range { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.analytics-error, .analytics-empty { margin-bottom: 16px; }
.analytics-stock-grid, .analytics-metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.analytics-stock-grid.single-base-stock { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.analytics-metric-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); margin-top: 10px; }
.analytics-stock-grid article, .analytics-metric-grid article { min-height: 104px; padding: 15px 16px; border: 1px solid #e6eaf1; border-radius: 8px; background: #fff; }
.analytics-stock-grid span, .analytics-metric-grid span { display: block; color: #68758b; font-size: 12px; }
.analytics-stock-grid strong, .analytics-metric-grid strong { display: block; margin-top: 7px; color: #17213b; font-size: 25px; font-variant-numeric: tabular-nums; line-height: 1; }
.analytics-stock-grid small, .analytics-metric-grid small { display: block; margin-top: 9px; color: #98a2b3; font-size: 11px; }
.analytics-metric-grid .metric-alert { border-color: #e1e5ef; background: #fafbfe; }
.analytics-metric-grid .metric-alert strong { color: #4d5e91; }
.analytics-main-grid { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(230px, .8fr); gap: 12px; margin-top: 12px; }
.analytics-card { margin-top: 12px; padding: 16px; border: 1px solid #e6eaf1; border-radius: 8px; background: #fff; }
.analytics-main-grid .analytics-card { margin-top: 0; }
.card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.card-heading h3 { margin: 0 0 3px; color: #27314c; font-size: 15px; font-weight: 650; }
.card-heading > span { color: #7c88a1; font-size: 12px; }
.analytics-chart { height: 238px; margin-top: 8px; }
.quality-card dl { display: grid; gap: 14px; margin: 23px 0 12px; }
.quality-card dl div { display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px dashed #e5e9f0; padding-bottom: 9px; }
.quality-card dt { color: #718096; font-size: 12px; }
.quality-card dd { margin: 0; color: #27314c; font-size: 20px; font-variant-numeric: tabular-nums; }
.quality-card dd small { color: #8490a5; font-size: 11px; }
.quality-note { margin: 0; color: #8b96a9; font-size: 12px; line-height: 1.55; }
.ranking-card :deep(.ant-table) { margin-top: 12px; }
.ranking-link { padding: 0; color: #2e4b8e; font-weight: 600; }
@media (max-width: 1100px) { .analytics-metric-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 760px) { .analytics-header { display: block; }.analytics-range { justify-content: flex-start; margin-top: 12px; }.analytics-stock-grid, .analytics-metric-grid, .analytics-main-grid { grid-template-columns: 1fr; }.analytics-chart { height: 220px; } }
</style>
