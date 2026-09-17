<template>
  <div class="echarts-output-preview">
    <div v-if="hasEchartsOption" ref="chartRef" class="echarts-canvas"></div>
    <img v-else-if="imageDataUrl" class="chart-image" :src="imageDataUrl" alt="图表预览" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as echarts from 'echarts';
import type { WorkflowChartOutput } from '../../shared/chartOutput';

const props = defineProps<{
  output: WorkflowChartOutput;
}>();

const chartRef = ref<HTMLDivElement>();
let chart: ReturnType<typeof echarts.init> | null = null;
let resizeObserver: ResizeObserver | null = null;

const hasEchartsOption = computed(
  () => !!props.output?.echarts_option && typeof props.output.echarts_option === 'object'
);

const imageDataUrl = computed(() => {
  const base64 = String(props.output?.image_base64 || '').trim();
  if (!base64) return '';
  return `data:${props.output?.image_mime_type || 'image/svg+xml'};base64,${base64}`;
});

function disposeChart() {
  resizeObserver?.disconnect();
  resizeObserver = null;
  chart?.dispose();
  chart = null;
}

async function renderChart() {
  if (!hasEchartsOption.value || !chartRef.value) {
    disposeChart();
    return;
  }
  await nextTick();
  if (!chartRef.value) return;
  if (!chart) {
    chart = echarts.init(chartRef.value);
    resizeObserver = new ResizeObserver(() => chart?.resize());
    resizeObserver.observe(chartRef.value);
  }
  chart.setOption(props.output.echarts_option || {}, true);
  chart.resize();
}

onMounted(renderChart);
watch(() => props.output?.echarts_option, renderChart, { deep: true });
onBeforeUnmount(disposeChart);
</script>

<style scoped lang="less">
.echarts-output-preview {
  width: 100%;
  min-width: 260px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}

.echarts-canvas {
  width: 100%;
  height: 260px;
}

.chart-image {
  display: block;
  width: 100%;
  max-height: 260px;
  object-fit: contain;
}
</style>
