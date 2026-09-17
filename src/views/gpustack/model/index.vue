<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="gpustack-page">
    <BasicTable @register="registerTable">
      <template #tableTitle>
        <a-space>
          <span class="page-title">模型部署</span>
          <a-button type="primary" preIcon="ant-design:plus-outlined" @click="handleDeploy">
            部署模型
          </a-button>
        </a-space>
      </template>
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'progress'">
          <div class="progress-cell">
            <a-progress
              v-if="isDeploying(record)"
              :percent="deployPercent(record)"
              :status="progressStatus(record)"
              size="small"
              :show-info="false"
            />
            <a-tag v-else-if="record.ready_replicas === record.replicas && record.replicas > 0" color="green">就绪</a-tag>
            <a-tag v-else-if="(record.ready_replicas ?? 0) > 0" color="gold">{{ record.ready_replicas }}/{{ record.replicas }} 启动中</a-tag>
            <a-tag v-else color="blue">部署中</a-tag>
          </div>
        </template>
      </template>
      <template #action="{ record }">
        <TableAction :actions="getTableAction(record)" />
      </template>
    </BasicTable>

    <DeployModal @register="registerModal" @success="handleSuccess" />

    <!-- 实例 + 日志抽屉 -->
    <BasicDrawer
      v-bind="$attrs"
      @register="registerDrawer"
      title="模型实例与部署日志"
      width="60%"
      :showFooter="false"
      @close="onDrawerClose"
    >
      <a-alert
        v-if="instanceError"
        class="drawer-alert"
        type="error"
        show-icon
        closable
        :message="instanceError.message"
        @close="instanceError = null"
      />
      <a-spin :spinning="instanceLoading">
        <a-empty v-if="!instances.length" description="暂无实例" />
        <a-table
          v-else
          :dataSource="instances"
          :columns="instanceColumns"
          :pagination="false"
          rowKey="id"
          size="small"
          :customRow="instanceRow"
          :rowClassName="instanceRowClass"
        />
      </a-spin>

      <!-- 日志区 -->
      <div class="log-section">
        <div class="log-header">
          <span class="log-title">
            <Icon icon="ant-design:file-text-outlined" /> 实时日志
            <a-tag v-if="logAutoRefresh" color="processing" class="live-tag">实时</a-tag>
          </span>
          <a-space size="small">
            <a-select v-model:value="logTail" size="small" class="tail-select" @change="onLogOptionsChange">
              <a-select-option :value="200">Recent 200</a-select-option>
              <a-select-option :value="500">Recent 500</a-select-option>
              <a-select-option :value="1000">Recent 1000</a-select-option>
            </a-select>
            <a-checkbox v-model:checked="logPrevious" @change="onLogOptionsChange">Previous container</a-checkbox>
            <a-button v-if="instances.length" size="small" @click="onToggleLogRefresh">
              {{ logAutoRefresh ? '暂停' : '继续' }}
            </a-button>
          </a-space>
        </div>
        <a-alert
          v-if="logError"
          class="drawer-alert"
          type="warning"
          show-icon
          closable
          :message="logError.message"
          @close="logError = null"
        />
        <a-spin :spinning="logLoading">
          <pre class="log-view" v-text="logContent || '（暂无日志，选择实例后将自动刷新）'"></pre>
        </a-spin>
      </div>
    </BasicDrawer>
  </div>
</template>

<script lang="ts" name="gpustack-model" setup>
  import { ref, onMounted, onUnmounted } from 'vue';
  import { useRoute } from 'vue-router';
  import { BasicTable, TableAction } from '/@/components/Table';
  import { BasicDrawer, useDrawer } from '/@/components/Drawer';
  import { useModal } from '/@/components/Modal';
  import { Icon } from '/@/components/Icon';
  import { useListPage } from '/@/hooks/system/useListPage';
  import DeployModal from './components/DeployModal.vue';
  import { modelList, modelDelete, modelInstances, instanceLogs } from '../gpustack.api';
  import { modelColumns, modelSearchFormSchema } from './model.data';
  import { MODEL_INSTANCE_STATE_MAP } from '../gpustack.enums';
  import { modelProgressFor, normalizeGpuStackError } from '../gpustack.state';
  import type { GpuStackViewError } from '../gpustack.types';

  const route = useRoute();
  const [registerModal, { openModal }] = useModal();
  const [registerDrawer, { openDrawer }] = useDrawer();
  const instances = ref<any[]>([]);
  const instanceLoading = ref(false);
  const instanceError = ref<GpuStackViewError | null>(null);

  const logContent = ref('');
  const logLoading = ref(false);
  const logAutoRefresh = ref(true);
  const logPrevious = ref(false);
  const logTail = ref(500);
  const logError = ref<GpuStackViewError | null>(null);
  const activeInstanceId = ref<number | string | null>(null);
  const activeModelId = ref<number | null>(null);
  let logTimer: any = null;
  let progressTimer: any = null;

  const { tableContext } = useListPage({
    tableProps: {
      title: '模型部署',
      api: modelList,
      columns: modelColumns,
      formConfig: { schemas: modelSearchFormSchema },
      actionColumn: { width: 240 },
      immediate: false,
    },
  });
  const [registerTable, { reload, updateTableDataRecord, getDataSource }] = tableContext;

  const instanceColumns = [
    { title: '实例名', dataIndex: 'name', width: 200 },
    {
      title: '状态',
      dataIndex: 'state',
      width: 100,
      customRender: ({ text }) => {
        const m = MODEL_INSTANCE_STATE_MAP[text] || { text, color: 'default' };
        return { children: m.text, attrs: { class: `ant-tag ant-tag-${m.color}` } };
      },
    },
    { title: '所在节点', dataIndex: 'worker_name', width: 120 },
    { title: 'GPU 型号', dataIndex: 'gpu_type', width: 160 },
    { title: 'GPU 索引', dataIndex: 'gpu_indexes', width: 100, customRender: ({ text }) => (text || []).join(',') },
    {
      title: '显存占用',
      dataIndex: 'vram',
      width: 100,
      customRender: ({ record }) => {
        const v = record?.computed_resource_claim?.vram;
        return v ? `${(v / 1024).toFixed(0)} MB` : '-';
      },
    },
    { title: '状态信息', dataIndex: 'state_message', ellipsis: true },
  ];

  /** 是否处于下载/部署阶段（需要进度条 + 轮询） */
  function isDeploying(record: any): boolean {
    // 副本未就绪或存在 downloading/starting 实例即视为部署中
    const ready = record.ready_replicas ?? 0;
    const want = record.replicas ?? 0;
    if (ready !== want && want > 0) return true;
    return false;
  }

  function deployPercent(record: any): number {
    // 有实例 download_progress 时取最大；否则按 ready/估算
    return modelProgressFor(
      Number(record.id),
      Number(record.replicas ?? 0),
      Number(record.ready_replicas ?? 0),
      instances.value,
    );
  }

  function progressStatus(record: any): 'active' | 'exception' | 'success' {
    if ((record.replicas ?? 0) === 0) return 'exception';
    return 'active';
  }

  function handleDeploy() {
    openModal(true, { isUpdate: false });
  }

  function handleEdit(record) {
    openModal(true, { id: record.id, isUpdate: true });
  }

  async function handleInstances(record) {
    activeModelId.value = record.id;
    instances.value = [];
    instanceError.value = null;
    logError.value = null;
    instanceLoading.value = true;
    logContent.value = '';
    openDrawer(true);
    try {
      instances.value = await modelInstances(record.id);
      // 自动选首个实例拉日志
      if (instances.value.length) {
        activeInstanceId.value = instances.value[0].id;
        await refreshLog();
        startLogPolling();
      }
      // 部署中：轮询实例刷新进度
      if (isDeploying(record)) {
        startProgressPolling(record);
      }
    } catch (error) {
      instanceError.value = normalizeGpuStackError(error, 'Unable to load model instances.');
    } finally {
      instanceLoading.value = false;
    }
  }

  async function refreshInstances() {
    if (!activeModelId.value) return;
    try {
      instances.value = await modelInstances(activeModelId.value);
      instanceError.value = null;
      if (!instances.value.some((instance) => instance.id === activeInstanceId.value)) {
        activeInstanceId.value = instances.value[0]?.id ?? null;
      }
    } catch (error) {
      instanceError.value = normalizeGpuStackError(error, 'Unable to refresh model instances.');
    }
  }

  async function refreshLog() {
    if (!activeInstanceId.value) return;
    logLoading.value = true;
    try {
      const res: any = await instanceLogs(activeInstanceId.value, { tail: logTail.value, previous: logPrevious.value });
      // 后端可能返回字符串或 { logs/content }
      logContent.value = typeof res === 'string' ? res : res?.logs || res?.content || '';
      logError.value = null;
    } catch (error) {
      logError.value = normalizeGpuStackError(error, 'Unable to load instance logs.');
      /* 失败静默：保留旧日志 */
    } finally {
      logLoading.value = false;
    }
  }

  function startLogPolling() {
    stopLogPolling();
    logTimer = setInterval(() => {
      if (logAutoRefresh.value) refreshLog();
    }, 3000);
  }
  function stopLogPolling() {
    if (logTimer) {
      clearInterval(logTimer);
      logTimer = null;
    }
  }
  function onToggleLogRefresh() {
    logAutoRefresh.value = !logAutoRefresh.value;
  }

  async function onLogOptionsChange() {
    await refreshLog();
  }

  async function selectInstance(instance: any) {
    if (activeInstanceId.value === instance.id) return;
    activeInstanceId.value = instance.id;
    logContent.value = '';
    logError.value = null;
    await refreshLog();
  }

  function instanceRow(instance: any) {
    return { onClick: () => selectInstance(instance) };
  }

  function instanceRowClass(instance: any) {
    return instance.id === activeInstanceId.value ? 'instance-row-active' : '';
  }

  function startProgressPolling(record: any) {
    stopProgressPolling();
    progressTimer = setInterval(async () => {
      await refreshInstances();
      // 列表层刷新该行
      const ds = getDataSource();
      const cur = (ds || []).find((r: any) => r.id === record.id);
      if (cur && !isDeploying(cur)) {
        stopProgressPolling();
      }
      // 同步刷新列表数据
      reload();
    }, 5000);
  }
  function stopProgressPolling() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }

  function onDrawerClose() {
    stopLogPolling();
    stopProgressPolling();
    activeInstanceId.value = null;
    activeModelId.value = null;
    logContent.value = '';
    logAutoRefresh.value = true;
    logPrevious.value = false;
    logTail.value = 500;
    instanceError.value = null;
    logError.value = null;
  }

  function handleDelete(record) {
    modelDelete(record, reload);
  }

  function handleSuccess({ isUpdate, values }) {
    if (isUpdate) {
      updateTableDataRecord(values.id, values);
    } else {
      reload();
    }
  }

  function getTableAction(record) {
    return [
      { label: '实例与日志', onClick: handleInstances.bind(null, record) },
      { label: '编辑', onClick: handleEdit.bind(null, record) },
      {
        label: '删除',
        color: 'error',
        popConfirm: {
          title: `确定删除模型"${record.name}"吗？`,
          confirm: handleDelete.bind(null, record),
        },
      },
    ];
  }

  // 从模型库跳转过来：focus=deploy 时自动定位并展开
  async function focusFromRoute() {
    await reload();
    const modelName = route.query.modelName as string;
    if (route.query.focus === 'deploy' && modelName) {
      // 列表加载后定位匹配模型并展开实例抽屉
      setTimeout(() => {
        const ds = getDataSource() || [];
        const target = ds.find((r: any) => r.name === modelName) || ds[0];
        if (target) handleInstances(target);
      }, 400);
    }
  }

  onMounted(focusFromRoute);
  onUnmounted(() => {
    stopLogPolling();
    stopProgressPolling();
  });
</script>

<style scoped lang="less">
  .gpustack-page {
    padding: 16px;
  }
  .page-title {
    font-size: 16px;
    font-weight: 700;
  }
  .progress-cell {
    min-width: 110px;
  }
  .drawer-alert {
    margin-bottom: 12px;
  }
  :deep(.instance-row-active > td) {
    background: #eef4ff !important;
  }
  :deep(.ant-table-tbody > tr) {
    cursor: pointer;
  }
  .log-section {
    margin-top: 16px;
    border-top: 1px solid #f0f0f0;
    padding-top: 12px;
  }
  .log-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .log-title {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 14px;
    font-weight: 600;
    color: #1f2937;
    .live-tag {
      margin: 0;
    }
  }
  .tail-select {
    width: 104px;
  }
  .log-view {
    background: #1e1e1e;
    color: #d4d4d4;
    border-radius: 6px;
    padding: 12px;
    max-height: 360px;
    overflow: auto;
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 12px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
  }
</style>

<style scoped lang="less">
  .gpustack-page {
    padding: 16px;
  }
  .page-title {
    font-size: 16px;
    font-weight: 700;
  }
</style>
