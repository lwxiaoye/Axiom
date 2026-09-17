<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="apisix-page">
    <header class="page-hero">
      <div class="hero-copy">
        <div class="hero-kicker">
          <span class="pulse-dot"></span>
          APISIX CONTROL PLANE
        </div>
        <h1>网关路由管理</h1>
        <p>直接管理 APISIX 路由匹配、上游服务与插件策略，保存后的配置会立即生效。</p>
      </div>
      <div class="hero-actions">
        <a-button preIcon="ant-design:reload-outlined" @click="handleSuccess">刷新</a-button>
        <a-button type="primary" preIcon="ant-design:plus-outlined" @click="handleAdd">新建路由</a-button>
      </div>
    </header>

    <section class="overview-grid" aria-label="APISIX 运行概览">
      <article class="overview-card overview-card--instance">
        <div class="overview-icon"><Icon icon="ant-design:cluster-outlined" /></div>
        <div>
          <span>健康实例</span>
          <strong>{{ overview.instanceOnline }}<small>/{{ overview.instanceTotal }}</small></strong>
        </div>
        <a-tag color="success">运行正常</a-tag>
      </article>
      <article class="overview-card">
        <div class="overview-icon"><Icon icon="ant-design:thunderbolt-outlined" /></div>
        <div>
          <span>托管路由</span>
          <strong>{{ overview.totalRoutes }}</strong>
        </div>
      </article>
      <article class="overview-card">
        <div class="overview-icon overview-icon--draft"><Icon icon="ant-design:file-text-outlined" /></div>
        <div>
          <span>启用路由</span>
          <strong>{{ overview.enabledRoutes }}</strong>
        </div>
      </article>
      <article class="overview-card">
        <div class="overview-icon overview-icon--danger"><Icon icon="ant-design:warning-outlined" /></div>
        <div>
          <span>停用路由</span>
          <strong>{{ overview.disabledRoutes }}</strong>
        </div>
      </article>
    </section>

    <section class="route-workspace">
      <div class="workspace-heading">
        <div>
          <h2>路由列表</h2>
          <p>配置由 Axiom 平台直接托管</p>
        </div>
        <div class="workspace-note">
          <Icon icon="ant-design:info-circle-outlined" />
          路由数据实时读取自 APISIX
        </div>
      </div>

      <BasicTable @register="registerTable">
        <template #route="{ record }">
          <div class="route-cell">
            <div class="route-glyph"><Icon icon="ant-design:api-outlined" /></div>
            <div class="route-meta">
              <strong>{{ record.name }}</strong>
              <span>{{ record.routeId }}</span>
            </div>
          </div>
        </template>

        <template #matcher="{ record }">
          <div class="matcher-cell">
            <code>{{ record.uris?.[0] || '未配置 URI' }}</code>
            <div class="method-list">
              <span v-for="method in visibleMethods(record.methods)" :key="method" :class="['method-tag', `method-tag--${method}`]">
                {{ method }}
              </span>
              <a-tooltip v-if="record.methods?.length > 3" :title="record.methods.slice(3).join(', ')">
                <span class="more-methods">+{{ record.methods.length - 3 }}</span>
              </a-tooltip>
            </div>
          </div>
        </template>

        <template #upstream="{ record }">
          <div class="upstream-cell">
            <Icon icon="ant-design:branches-outlined" />
            <div>
              <strong>{{ record.upstreamName || record.upstreamId || '内联节点' }}</strong>
              <span v-if="record.nodes?.length">{{ record.nodes.length }} 个节点</span>
              <span v-else>独立 Upstream</span>
            </div>
          </div>
        </template>

        <template #plugins="{ record }">
          <div v-if="enabledPlugins(record).length" class="plugin-summary">
            <a-avatar-group :max-count="3" size="small">
              <a-tooltip v-for="plugin in enabledPlugins(record)" :key="plugin.name" :title="plugin.name">
                <a-avatar class="plugin-avatar">{{ plugin.name.slice(0, 2).toUpperCase() }}</a-avatar>
              </a-tooltip>
            </a-avatar-group>
            <span>{{ enabledPlugins(record).length }} 个</span>
          </div>
          <span v-else class="muted-text">未配置</span>
        </template>

        <template #status="{ record }">
          <button
            type="button"
            :class="['status-pill', record.status === 1 ? 'status-pill--enabled' : 'status-pill--disabled']"
            :aria-label="record.status === 1 ? '停用路由' : '启用路由'"
            @click="handleStatusChange(record)"
          >
            <span></span>
            {{ record.status === 1 ? '已启用' : '已停用' }}
          </button>
        </template>

        <template #updatedAt="{ record }">
          <div class="updated-cell">
            <span>{{ record.updatedAt || '-' }}</span>
            <small>APISIX</small>
          </div>
        </template>

        <template #action="{ record }">
          <TableAction :actions="getActions(record)" :dropDownActions="getDropDownActions(record)" />
        </template>
      </BasicTable>
    </section>

    <ApisixRouteDrawer @register="registerDrawer" @success="handleSuccess" />
  </div>
</template>

<script setup lang="ts">
  import { nextTick, onMounted, reactive } from 'vue';
  import { Modal, message } from 'ant-design-vue';
  import { BasicTable, TableAction, type ActionItem } from '/@/components/Table';
  import { useDrawer } from '/@/components/Drawer';
  import { useListPage } from '/@/hooks/system/useListPage';
  import ApisixRouteDrawer from './ApisixRouteDrawer.vue';
  import {
    changeApisixRouteStatus,
    copyApisixRoute,
    deleteApisixRoute,
    getApisixOverview,
    getApisixRouteList,
  } from './apisix.api';
  import { columns, searchFormSchema } from './apisix.data';
  import type { ApisixOverview, ApisixPluginConfig, ApisixRoute } from './apisix.types';

  const overview = reactive<ApisixOverview>({
    instanceOnline: 0,
    instanceTotal: 0,
    totalRoutes: 0,
    enabledRoutes: 0,
    disabledRoutes: 0,
  });

  const [registerDrawer, { openDrawer }] = useDrawer();

  const { tableContext } = useListPage({
    designScope: 'apisix-route',
    tableProps: {
      api: getApisixRouteList,
      immediate: false,
      columns,
      showIndexColumn: false,
      rowKey: 'id',
      ellipsis: true,
      canResize: false,
      actionColumn: {
        width: 190,
        title: '操作',
        dataIndex: 'action',
        fixed: 'right',
      },
      formConfig: {
        schemas: searchFormSchema,
        autoSubmitOnEnter: true,
        showAdvancedButton: false,
      },

    },
  });

  const [registerTable, { reload }] = tableContext;

  onMounted(async () => {
    await nextTick();
    await initializePage();
  });

  async function initializePage() {
    await Promise.all([reload(), loadOverview()]);
  }

  async function loadOverview() {
    try {
      Object.assign(overview, await getApisixOverview());
    } catch {
      Object.assign(overview, {
        instanceOnline: 0,
        instanceTotal: 0,
        totalRoutes: 0,
        enabledRoutes: 0,
        disabledRoutes: 0,
      });
    }
  }

  function handleAdd() {
    openDrawer(true, { isUpdate: false });
  }

  function handleEdit(record: ApisixRoute) {
    openDrawer(true, { isUpdate: true, record });
  }

  async function handleSuccess() {
    await Promise.all([reload(), loadOverview()]);
  }

  function visibleMethods(methods?: string[]) {
    return (methods?.length ? methods : ['ALL']).slice(0, 3);
  }

  function enabledPlugins(record: ApisixRoute): ApisixPluginConfig[] {
    return (record.plugins || []).filter((plugin) => plugin.enabled);
  }

  function handleStatusChange(record: ApisixRoute) {
    if (!record.id) return;
    const nextStatus = record.status === 1 ? 0 : 1;
    Modal.confirm({
      title: nextStatus === 1 ? '启用此路由？' : '停用此路由？',
      content: nextStatus === 1 ? '启用后，匹配的请求将进入配置的上游服务。' : '停用后，该路由将不再接收流量。',
      okText: nextStatus === 1 ? '确认启用' : '确认停用',
      okType: nextStatus === 1 ? 'primary' : 'danger',
      async onOk() {
        await changeApisixRouteStatus(record.id!, nextStatus);
        message.success(nextStatus === 1 ? '路由已启用' : '路由已停用');
        await handleSuccess();
      },
    });
  }

  async function handleCopy(record: ApisixRoute) {
    if (!record.id) return;
    await copyApisixRoute(record.id);
    message.success('路由已复制，新路由默认为停用状态');
    await handleSuccess();
  }

  function handleDelete(record: ApisixRoute) {
    if (!record.id) return;
    Modal.confirm({
      title: '删除此路由？',
      content: `将从 APISIX 删除“${record.name}”，此操作不可撤销。`,
      okText: '确认删除',
      okType: 'danger',
      async onOk() {
        await deleteApisixRoute(record.id!);
        await handleSuccess();
      },
    });
  }

  function getActions(record: ApisixRoute): ActionItem[] {
    return [
      {
        label: '编辑',
        onClick: handleEdit.bind(null, record),
      },
    ];
  }

  function getDropDownActions(record: ApisixRoute): ActionItem[] {
    return [
      {
        label: '复制路由',
        onClick: handleCopy.bind(null, record),
      },
      {
        label: record.status === 1 ? '停用' : '启用',
        onClick: handleStatusChange.bind(null, record),
      },
      {
        label: '删除',
        color: 'error',
        onClick: handleDelete.bind(null, record),
      },
    ];
  }
</script>

<style scoped lang="less">
  .apisix-page {
    --apisix-ink: #172033;
    --apisix-muted: #727d91;
    --apisix-border: #e7ebf2;
    --apisix-surface: #fff;
    --apisix-canvas: #f4f6f9;
    min-height: 100%;
    padding: 20px;
    color: var(--apisix-ink);
    background:
      radial-gradient(circle at 85% -20%, rgba(22, 119, 255, 0.1), transparent 32%),
      var(--apisix-canvas);
  }

  .page-hero {
    position: relative;
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 24px;
    overflow: hidden;
    padding: 26px 28px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    color: #fff;
    background:
      linear-gradient(112deg, rgba(255, 255, 255, 0.05), transparent 40%),
      #182231;
    box-shadow: 0 12px 28px rgba(25, 39, 63, 0.12);

    &::after {
      position: absolute;
      right: -60px;
      bottom: -110px;
      width: 300px;
      height: 300px;
      border: 1px solid rgba(91, 164, 255, 0.2);
      border-radius: 50%;
      box-shadow:
        0 0 0 42px rgba(91, 164, 255, 0.04),
        0 0 0 84px rgba(91, 164, 255, 0.025);
      content: '';
      pointer-events: none;
    }
  }

  .hero-copy,
  .hero-actions {
    position: relative;
    z-index: 1;
  }

  .hero-kicker {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 8px;
    color: #8fc2ff;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
  }

  .pulse-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #52c41a;
    box-shadow: 0 0 0 4px rgba(82, 196, 26, 0.13);
  }

  .hero-copy {
    h1 {
      margin: 0 0 6px;
      color: #fff;
      font-size: 28px;
      font-weight: 650;
      letter-spacing: -0.03em;
    }

    p {
      max-width: 640px;
      margin: 0;
      color: #aab5c7;
      font-size: 14px;
      line-height: 1.65;
    }
  }

  .hero-actions {
    display: flex;
    align-items: center;
    gap: 10px;

    :deep(.ant-btn-default) {
      color: #e5edf8;
      border-color: #46546a;
      background: rgba(255, 255, 255, 0.06);

      &:hover,
      &:focus-visible {
        color: #fff;
        border-color: #69b1ff;
        background: rgba(105, 177, 255, 0.12);
      }
    }
  }


  .overview-grid {
    display: grid;
    grid-template-columns: 1.2fr repeat(3, 1fr);
    gap: 14px;
    margin: 16px 0;
  }

  .overview-card {
    display: flex;
    align-items: center;
    gap: 14px;
    min-width: 0;
    padding: 17px 18px;
    border: 1px solid var(--apisix-border);
    border-radius: 11px;
    background: var(--apisix-surface);
    box-shadow: 0 3px 10px rgba(27, 43, 68, 0.035);

    > div:nth-child(2) {
      min-width: 0;
      margin-right: auto;
    }

    span {
      display: block;
      color: var(--apisix-muted);
      font-size: 12px;
    }

    strong {
      display: block;
      margin-top: 2px;
      color: var(--apisix-ink);
      font-size: 25px;
      font-weight: 650;
      font-variant-numeric: tabular-nums;
      line-height: 1.2;

      small {
        margin-left: 2px;
        color: #9ba4b4;
        font-size: 14px;
        font-weight: 500;
      }
    }
  }

  .overview-icon {
    display: grid;
    flex: 0 0 auto;
    width: 40px;
    height: 40px;
    place-items: center;
    border-radius: 10px;
    color: #0958d9;
    background: #e6f4ff;
    font-size: 19px;

    &--draft {
      color: #ad6800;
      background: #fff7e6;
    }

    &--danger {
      color: #cf1322;
      background: #fff1f0;
    }
  }

  .route-workspace {
    padding: 20px;
    border: 1px solid var(--apisix-border);
    border-radius: 12px;
    background: var(--apisix-surface);
    box-shadow: 0 5px 16px rgba(27, 43, 68, 0.04);
  }

  .workspace-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 2px;

    h2 {
      margin: 0;
      color: var(--apisix-ink);
      font-size: 18px;
      font-weight: 650;
    }

    p {
      margin: 3px 0 0;
      color: var(--apisix-muted);
      font-size: 12px;
    }
  }

  .workspace-note {
    display: flex;
    align-items: center;
    gap: 7px;
    color: #7d8798;
    font-size: 12px;
  }

  .route-cell,
  .upstream-cell,
  .plugin-summary {
    display: flex;
    align-items: center;
  }

  .route-cell {
    gap: 11px;
  }

  .route-glyph {
    display: grid;
    flex: 0 0 auto;
    width: 34px;
    height: 34px;
    place-items: center;
    border: 1px solid #d6e4ff;
    border-radius: 8px;
    color: #1677ff;
    background: #f0f5ff;
    font-size: 16px;
  }

  .route-meta,
  .upstream-cell > div,
  .updated-cell {
    min-width: 0;

    strong,
    span,
    small {
      display: block;
    }
  }

  .route-meta {
    strong {
      overflow: hidden;
      color: #243047;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      overflow: hidden;
      margin-top: 2px;
      color: #8a94a6;
      font-family: 'Cascadia Code', Consolas, monospace;
      font-size: 11px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .matcher-cell {
    code {
      display: block;
      overflow: hidden;
      color: #36445d;
      font-family: 'Cascadia Code', Consolas, monospace;
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .method-list {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: 6px;
  }

  .method-tag,
  .more-methods {
    padding: 1px 5px;
    border-radius: 4px;
    font-family: 'Cascadia Code', Consolas, monospace;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .method-tag {
    color: #0958d9;
    background: #e6f4ff;

    &--POST {
      color: #237804;
      background: #f6ffed;
    }

    &--PUT,
    &--PATCH {
      color: #ad6800;
      background: #fff7e6;
    }

    &--DELETE {
      color: #cf1322;
      background: #fff1f0;
    }
  }

  .more-methods {
    color: #68758a;
    background: #f0f2f5;
  }

  .upstream-cell {
    gap: 9px;
    color: #7b879a;

    strong {
      overflow: hidden;
      max-width: 145px;
      color: #354158;
      font-size: 13px;
      font-weight: 500;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    span {
      margin-top: 2px;
      color: #98a1b1;
      font-size: 11px;
    }
  }

  .plugin-summary {
    gap: 8px;
    color: #68758a;
    font-size: 12px;
  }

  .plugin-avatar {
    color: #0958d9;
    border-color: #fff;
    background: #d6e4ff;
    font-size: 9px;
    font-weight: 700;
  }

  .muted-text {
    color: #a2aaba;
    font-size: 12px;
  }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    min-height: 30px;
    padding: 3px 10px;
    border: 1px solid transparent;
    border-radius: 999px;
    font-size: 12px;
    cursor: pointer;
    transition: border-color 160ms ease, background-color 160ms ease;

    span {
      width: 6px;
      height: 6px;
      border-radius: 50%;
    }

    &:focus-visible {
      outline: 2px solid #1677ff;
      outline-offset: 2px;
    }

    &--enabled {
      color: #237804;
      border-color: #b7eb8f;
      background: #f6ffed;

      span {
        background: #52c41a;
      }
    }

    &--disabled {
      color: #6f7786;
      border-color: #d9d9d9;
      background: #fafafa;

      span {
        background: #8c8c8c;
      }
    }
  }

  .sync-state {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;

    &--synced {
      color: #389e0d;
    }

    &--draft {
      color: #8a5d00;
    }

    &--drifted {
      color: #d46b08;
    }

    &--failed {
      color: #cf1322;
    }
  }

  .updated-cell {
    span {
      color: #47536a;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }

    small {
      margin-top: 2px;
      color: #9aa3b2;
      font-size: 11px;
    }
  }

  :deep(.ant-table-wrapper) {
    .ant-table-thead > tr > th {
      color: #68758a;
      background: #f8f9fb;
      font-size: 12px;
      font-weight: 600;
    }

    .ant-table-tbody > tr > td {
      padding-top: 13px;
      padding-bottom: 13px;
    }
  }

  @media (max-width: 1100px) {
    .page-hero {
      align-items: flex-start;
      flex-direction: column;
    }

    .overview-grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (max-width: 680px) {
    .apisix-page {
      padding: 12px;
    }

    .page-hero {
      padding: 22px 18px;
    }

    .hero-actions {
      align-items: stretch;
      flex-direction: column;
      width: 100%;
    }


    .overview-grid {
      grid-template-columns: 1fr;
    }

    .workspace-heading {
      align-items: flex-start;
      flex-direction: column;
    }

    .workspace-note {
      display: none;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .status-pill {
      transition: none;
    }
  }
</style>
