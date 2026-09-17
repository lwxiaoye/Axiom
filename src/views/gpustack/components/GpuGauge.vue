<template>
  <div>
    <div ref="chartRef" :style="{ width: '100%', height: height }"></div>
  </div>
</template>
<script lang="ts" setup>
  import { onMounted, ref, watch, Ref, reactive } from 'vue';
  import { useECharts } from '/@/hooks/web/useECharts';
  import { GaugeChart } from 'echarts/charts';
  import { gaugeColor } from '../gpustack.enums';

  const props = defineProps({
    value: { type: Number, default: 0 },
    name: { type: String, default: '使用率' },
    height: { type: String, default: '240px' },
  });

  const chartRef = ref<HTMLDivElement | null>(null);
  const { setOptions, echarts } = useECharts(chartRef as Ref<HTMLDivElement>);

  function buildOption() {
    const v = Number(props.value || 0);
    return {
      series: [
        {
          type: 'gauge',
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          progress: {
            show: true,
            width: 14,
            itemStyle: { color: gaugeColor(v) },
          },
          axisLine: {
            lineStyle: { width: 14, color: [[1, '#f0f0f0']] },
          },
          axisTick: { show: false },
          splitLine: { length: 10, lineStyle: { width: 2, color: '#999' } },
          axisLabel: { distance: 18, color: '#999', fontSize: 12 },
          pointer: { length: '60%', width: 5 },
          detail: {
            valueAnimation: true,
            fontSize: 26,
            formatter: '{value}%',
            offsetCenter: [0, '70%'],
            color: gaugeColor(v),
          },
          data: [{ value: v, name: props.name }],
          title: { offsetCenter: [0, '95%'], fontSize: 12, color: '#888' },
        },
      ],
    };
  }

  function render() {
    setOptions(buildOption());
  }

  onMounted(() => {
    echarts.use(GaugeChart);
    render();
  });

  watch(() => props.value, render);
</script>
