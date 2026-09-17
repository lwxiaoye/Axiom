<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="gpustack-page">
    <BasicTable @register="registerTable">
      <template #tableTitle>
        <span class="page-title">计算节点（Worker）</span>
      </template>
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
    </BasicTable>

    <!-- 节点详情抽屉：CPU/内存仪表盘 + GPU 列表 -->
    <BasicDrawer
      v-bind="$attrs"
      @register="registerDrawer"
      title="节点资源详情"
      width="60%"
      :showFooter="false"
    >
      <a-alert
        v-if="detailError"
        class="detail-alert"
        type="error"
        show-icon
        closable
        :message="detailError.message"
        @close="detailError = null"
      />
      <a-spin :spinning="detailLoading">
        <div v-if="detail" class="worker-detail">
          <a-descriptions :column="3" bordered size="small" class="desc-block">
            <a-descriptions-item label="节点">{{ detail.name }}</a-descriptions-item>
            <a-descriptions-item label="状态">{{ detail.state }}</a-descriptions-item>
            <a-descriptions-item label="IP">{{ detail.ip }}</a-descriptions-item>
            <a-descriptions-item label="主机名">{{ detail.hostname }}</a-descriptions-item>
            <a-descriptions-item label="系统">{{ osInfo }}</a-descriptions-item>
            <a-descriptions-item label="心跳">{{ detail.heartbeat_time }}</a-descriptions-item>
          </a-descriptions>

          <a-row :gutter="16" class="gauge-row">
            <a-col :span="8">
              <div class="gauge-card">
                <GpuGauge :value="cpuRate" name="CPU使用率" />
              </div>
            </a-col>
            <a-col :span="8">
              <div class="gauge-card">
                <GpuGauge :value="memRate" name="内存使用率" />
              </div>
            </a-col>
            <a-col :span="8">
              <div class="gauge-card stat-card">
                <a-statistic title="GPU 数量" :value="gpuCount" />
                <a-statistic title="显存总量" :value="totalVramGb" suffix="GB" class="mt-8" />
              </div>
            </a-col>
          </a-row>

          <div class="gpu-list-block">
            <div class="block-title">GPU 设备（{{ gpuCount }}）</div>
            <a-table
              :dataSource="gpuDevices"
              :columns="gpuColumns"
              :pagination="false"
              rowKey="id"
              size="small"
            />
          </div>
        </div>
      </a-spin>
    </BasicDrawer>
  </div>
</template>

<script lang="ts" name="gpustack-worker" setup>
  import { ref, computed } from 'vue';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { BasicDrawer, useDrawer } from '/@/components/Drawer';
  import { useListPage } from '/@/hooks/system/useListPage';
  import { workerList, workerDetail } from '../gpustack.api';
  import { workerColumns, workerSearchFormSchema, gpuColumns } from './worker.data';
  import GpuGauge from '../components/GpuGauge.vue';
  import { normalizeGpuStackError } from '../gpustack.state';
  import type { GpuStackViewError } from '../gpustack.types';

  const [registerDrawer, { openDrawer }] = useDrawer();
  const detail = ref<any>(null);
  const detailLoading = ref(false);
  const detailError = ref<GpuStackViewError | null>(null);

  const { tableContext } = useListPage({
    tableProps: {
      title: '计算节点',
      api: workerList,
      columns: workerColumns,
      formConfig: { schemas: workerSearchFormSchema },
      actionColumn: { width: 120 },
    },
  });
  const [registerTable] = tableContext;

  const cpuRate = computed(() => Number(detail.value?.status?.cpu?.utilization_rate ?? 0));
  const memRate = computed(() => Number(detail.value?.status?.memory?.utilization_rate ?? 0));
  const gpuDevices = computed(() => detail.value?.status?.gpu_devices || []);
  const gpuCount = computed(() => gpuDevices.value.length);
  const totalVramGb = computed(() => {
    const sum = gpuDevices.value.reduce((s: number, g: any) => s + (g.memory?.total || 0), 0);
    return Number((sum / 1024 / 1024 / 1024).toFixed(1));
  });
  const osInfo = computed(() => {
    const os = detail.value?.status?.os;
    return os ? `${os.name || ''} ${os.version || ''}`.trim() : '-';
  });

  async function handleDetail(record) {
    detail.value = null;
    detailError.value = null;
    detailLoading.value = true;
    openDrawer(true);
    try {
      detail.value = await workerDetail(record.id);
    } catch (error) {
      detailError.value = normalizeGpuStackError(error, 'Unable to load worker details.');
    } finally {
      detailLoading.value = false;
    }
  }

  function getTableAction(record) {
    return [{ label: '资源详情', onClick: handleDetail.bind(null, record) }];
  }
</script>

<style scoped lang="less">
  .gpustack-page {
    padding: 16px;
  }
  .page-title {
    font-size: 16px;
    font-weight: 700;
  }
  .worker-detail {
    padding: 8px 4px;
  }
  .detail-alert {
    margin-bottom: 12px;
  }
  .desc-block {
    margin-bottom: 16px;
  }
  .gauge-row {
    margin-bottom: 16px;
  }
  .gauge-card {
    background: #fff;
    border: 1px solid #f0f0f0;
    border-radius: 8px;
    padding: 8px;
    min-height: 240px;
  }
  .stat-card {
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
  }
  .mt-8 {
    margin-top: 16px;
  }
  .gpu-list-block {
    margin-top: 8px;
  }
  .block-title {
    font-size: 14px;
    font-weight: 700;
    margin-bottom: 8px;
  }
</style>
