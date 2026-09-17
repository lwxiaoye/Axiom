<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="gpustack-overview">
    <a-spin :spinning="loading">
      <!-- 健康与同步状态 -->
      <a-card class="health-card" :bordered="false">
        <div class="health-row">
          <div class="health-item">
            <a-badge :status="healthy ? 'success' : 'error'" />
            <span class="health-label">GPUStack</span>
            <a-tag :color="healthy ? 'green' : 'red'">{{ healthy ? '健康' : '不可达' }}</a-tag>
            <a-tag v-if="freshness === 'stale'" color="gold">数据可能过期</a-tag>
          </div>
          <div class="sync-info">
            <span>最近同步：{{ syncStateData.lastSyncTime || '-' }}</span>
            <a-button type="link" size="small" :loading="loading" @click="loadAll">刷新</a-button>
          </div>
        </div>
        <a-alert
          v-if="syncStateData.errorMessage"
          class="err-alert"
          type="warning"
          show-icon
          :message="syncStateData.errorMessage"
        />
        <a-alert
          v-if="loadError"
          class="err-alert"
          type="warning"
          show-icon
          closable
          :message="loadError.message"
          @close="loadError = null"
        />
      </a-card>

      <!-- 资源统计卡片 -->
      <a-row :gutter="16" class="stat-row">
        <a-col :span="6">
          <a-card>
            <a-statistic title="计算节点 (Worker)" :value="stat.workerCount" :loading="loading">
              <template #prefix><Icon icon="ant-design:control-outlined" /></template>
            </a-statistic>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card>
            <a-statistic title="GPU 设备" :value="stat.gpuCount" :loading="loading">
              <template #prefix><Icon icon="ant-design:fire-outlined" /></template>
            </a-statistic>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card>
            <a-statistic title="已部署模型" :value="stat.modelCount" :loading="loading">
              <template #prefix><Icon icon="ant-design:appstore-outlined" /></template>
            </a-statistic>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card>
            <a-statistic
              title="运行中实例"
              :value="runningInstances"
              :valueStyle="{ color: '#3f8600' }"
              :loading="loading"
            >
              <template #prefix><Icon icon="ant-design:thunderbolt-outlined" /></template>
            </a-statistic>
          </a-card>
        </a-col>
      </a-row>

      <!-- 显存总览仪表盘 -->
      <a-card title="集群显存使用" class="vram-card" :bordered="false">
        <a-row :gutter="16">
          <a-col :span="8">
            <div class="gauge-wrap">
              <GpuGauge :value="avgVramRate" name="平均显存使用率" />
            </div>
          </a-col>
          <a-col :span="16">
            <a-table
              :dataSource="gpuSummary"
              :columns="summaryColumns"
              :pagination="false"
              rowKey="id"
              size="small"
              :loading="loading"
            />
          </a-col>
        </a-row>
      </a-card>
    </a-spin>
  </div>
</template>

<script lang="ts" name="gpustack-overview" setup>
  import { ref, reactive, computed, onMounted } from 'vue';
  import { Icon } from '/@/components/Icon';
  import GpuGauge from '../components/GpuGauge.vue';
  import { allGpuDevices, overview, overviewHealth, syncState } from '../gpustack.api';
  import { formatBytes } from '../gpustack.enums';
  import { liveResourceCounts, nextFreshness, normalizeGpuStackError } from '../gpustack.state';
  import type { DataFreshness, GpuStackViewError } from '../gpustack.types';

  const loading = ref(false);
  const healthy = ref(false);
  const freshness = ref<DataFreshness>('failed');
  const loadError = ref<GpuStackViewError | null>(null);
  const syncStateData = reactive<any>({});
  const stat = reactive({ workerCount: 0, gpuCount: 0, modelCount: 0, instanceCount: 0 });
  const gpuDevices = ref<any[]>([]);
  const summaryColumns = [
    { title: '型号', dataIndex: 'name', width: 180 },
    { title: '节点', dataIndex: 'worker_name', width: 120 },
    {
      title: '显存',
      dataIndex: 'vram',
      customRender: ({ record }) =>
        `${formatBytes(record.memory?.used)} / ${formatBytes(record.memory?.total)}`,
    },
    {
      title: '使用率',
      dataIndex: 'rate',
      width: 100,
      customRender: ({ record }) => `${Number(record.memory?.utilization_rate || 0).toFixed(1)}%`,
    },
    { title: '温度', dataIndex: 'temperature', width: 80, customRender: ({ record }) => `${record.temperature ?? '-'}°C` },
  ];

  const gpuSummary = computed(() => gpuDevices.value.slice(0, 8));
  const runningInstances = computed(() => stat.instanceCount);
  const avgVramRate = computed(() => {
    if (!gpuDevices.value.length) return 0;
    const sum = gpuDevices.value.reduce((s, g) => s + Number(g.memory?.utilization_rate || 0), 0);
    return Number((sum / gpuDevices.value.length).toFixed(1));
  });

  /** 安全取值：allSettled 结果中提取 fulfilled 的值，失败则用默认值 */
  function pick(results: PromiseSettledResult<any>[], idx: number, def: any = null) {
    const r = results[idx];
    return r && r.status === 'fulfilled' ? r.value : def;
  }

  function failure(results: PromiseSettledResult<any>[], idx: number, fallback: string) {
    const result = results[idx];
    return result?.status === 'rejected' ? normalizeGpuStackError(result.reason, fallback) : null;
  }

  async function loadAll() {
    loading.value = true;
    try {
      const hadData = gpuDevices.value.length > 0 || stat.workerCount > 0 || stat.gpuCount > 0 || stat.modelCount > 0;
      // 用 allSettled：任一接口失败不拖垮整页（修转圈卡死问题）
      const results = await Promise.allSettled([
        overviewHealth(), // 0
        overview(),       // 1 dashboard.resource_counts
        allGpuDevices({ page: 1, perPage: 100 }), // 2
        syncState(),      // 3
      ]);
      // 健康状态
      const healthRes = pick(results, 0);
      healthy.value = !!healthRes?.healthy;
      // 资源计数：优先 dashboard.resource_counts，回退 syncState
      const dash = pick(results, 1);
      const rc = dash?.resource_counts || {};
      const sync = pick(results, 3, {});
      Object.assign(syncStateData, sync);
      // GPU 明细同时是空仪表盘计数的实时回退来源。
      gpuDevices.value = pick(results, 2, []) || [];
      const counts = liveResourceCounts(rc, sync, gpuDevices.value);
      stat.workerCount = counts.workerCount;
      stat.gpuCount = counts.gpuCount;
      stat.modelCount = counts.modelCount;
      stat.instanceCount = counts.instanceCount;
      const failures = [
        failure(results, 0, 'GPUStack health check failed.'),
        failure(results, 1, 'GPUStack overview failed to load.'),
        failure(results, 2, 'GPU device inventory failed to load.'),
        failure(results, 3, 'GPUStack sync status failed to load.'),
      ].filter(Boolean) as GpuStackViewError[];
      freshness.value = nextFreshness(hadData, failures.length === 0);
      loadError.value = failures[0] ?? null;
    } finally {
      loading.value = false;
    }
  }

  onMounted(loadAll);
</script>

<style scoped lang="less">
  .gpustack-overview {
    padding: 16px;
  }
  .health-card {
    margin-bottom: 16px;
  }
  .health-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .health-item {
    display: flex;
    align-items: center;
    gap: 8px;
    .health-label {
      font-size: 15px;
      font-weight: 700;
    }
  }
  .sync-info {
    color: #888;
    font-size: 13px;
  }
  .err-alert {
    margin-top: 12px;
  }
  .stat-row {
    margin-bottom: 16px;
  }
  .vram-card {
    margin-top: 8px;
  }
  .gauge-wrap {
    background: #fff;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    padding: 8px;
  }
</style>
