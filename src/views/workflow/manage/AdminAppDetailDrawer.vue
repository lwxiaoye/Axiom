<template>
  <a-drawer :open="open" width="min(1040px, 100vw)" class="agent-admin-drawer" @close="emit('update:open', false)">
    <template #title>
      <div class="drawer-title">
        <span>智能体管理</span>
        <small>{{ detail?.app.name || app?.name || '加载中' }}</small>
      </div>
    </template>

    <a-spin :spinning="loading">
      <a-alert v-if="loadFailed" type="error" show-icon message="管理信息暂时无法加载" class="mb-4">
        <template #action><a-button size="small" @click="load">重试</a-button></template>
      </a-alert>

      <template v-else-if="detail">
        <section class="app-hero" aria-labelledby="app-overview-heading">
          <div class="app-avatar" aria-hidden="true">
            <img
              v-if="getAgentIconUrl(detail.app)"
              :src="getAgentIconUrl(detail.app)"
              :alt="`${detail.app.name || '智能体'}图标`"
              @error="recoverAgentIcon($event, detail.app)"
            />
            <span v-else>{{ getAgentInitials(detail.app) }}</span>
          </div>
          <div class="app-hero-copy">
            <p class="section-kicker">应用概览</p>
            <h2 id="app-overview-heading">{{ detail.app.name || '-' }}</h2>
            <p class="app-description">{{ detail.app.description || '暂无应用描述' }}</p>
            <a-space wrap :size="8">
              <a-tag class="category-tag">{{ categoryLabel }}</a-tag>
              <a-tag>{{ typeLabel(detail.app.aiAppType) }}</a-tag>
              <a-tag :color="statusColor(detail.app.status)">{{ statusLabel(detail.app.status) }}</a-tag>
            </a-space>
          </div>
          <div v-if="!isBuiltin" class="live-version-card">
            <span>当前线上版本</span>
            <strong>v{{ detail.summary.publishedVersion || '-' }}</strong>
          </div>
        </section>

        <section class="app-profile" aria-label="应用基本信息">
          <div class="section-heading">
            <h3>基本信息</h3>
            <span>发布与归属信息</span>
          </div>
          <dl class="profile-grid">
            <div><dt>应用名称</dt><dd>{{ detail.app.name || '-' }}</dd></div>
            <div><dt>能力分类</dt><dd>{{ categoryLabel }}</dd></div>
            <div><dt>拥有者名称</dt><dd>{{ detail.app.ownerUsername || '-' }}</dd></div>
            <div><dt>状态</dt><dd><a-tag :color="statusColor(detail.app.status)">{{ statusLabel(detail.app.status) }}</a-tag></dd></div>
            <div><dt>应用类型</dt><dd>{{ typeLabel(detail.app.aiAppType) }}</dd></div>
            <div><dt>{{ isBuiltin ? '创建时间' : '发布时间' }}</dt><dd>{{ formatManageDateTime(isBuiltin ? detail.app.createdAt : detail.app.publishedAt) }}</dd></div>
          </dl>
        </section>

        <a-tabs v-model:active-key="activeTab" class="management-tabs">
          <a-tab-pane key="metrics" tab="运营监测">
            <div class="tab-toolbar">
              <div><h3>使用情况</h3><p>按所选时间范围查看应用的真实运营数据。</p></div>
              <a-select v-model:value="range" :options="rangeOptions" class="range-select" />
            </div>
            <a-alert v-if="metricsFailed" type="warning" show-icon message="监测数据暂时无法加载" class="mb-3">
              <template #action><a-button size="small" @click="loadMetrics">重试</a-button></template>
            </a-alert>
            <a-skeleton v-else-if="metricsLoading" active :paragraph="{ rows: 4 }" />
            <div v-else-if="metrics" class="metric-grid">
              <AgentMetricsTrendChart
                v-for="item in metricCards"
                :key="item.key"
                :daily="metrics.daily"
                :start-date="metrics.startDate"
                :end-date="metrics.endDate"
                :range-label="rangeLabel"
                :metric="item.key"
                :title="item.label"
                :value="item.value"
                :tip="item.tip"
                :available="true"
              />
            </div>
          </a-tab-pane>

          <a-tab-pane v-if="!isBuiltin" key="conversationLogs" tab="对话日志">
            <AgentConversationLogPanel v-if="props.app" :app-id="props.app.id" access="admin" />
          </a-tab-pane>

          <a-tab-pane v-if="!isBuiltin" key="versions" tab="版本历史">
            <div class="tab-toolbar">
              <div><h3>版本历史</h3><p>查看已提交版本；回滚会生成一个新的线上版本。</p></div>
              <a-tag v-if="detail.summary.publishedVersion" color="success">线上 v{{ detail.summary.publishedVersion }}</a-tag>
            </div>
            <a-table
              :columns="versionColumns"
              :data-source="detail.versions || []"
              :pagination="false"
              row-key="id"
              size="small"
              class="version-table"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'versionNo'">
                  v{{ record.versionNo }}
                  <a-tag v-if="isLiveVersion(record)" color="success">线上</a-tag>
                </template>
                <template v-else-if="column.key === 'status'">
                  <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
                </template>
                <template v-else-if="column.key === 'publishedAt'">
                  {{ formatManageDateTime(record.publishedAt) }}
                </template>
                <template v-else-if="column.key === 'action'">
                  <a-popconfirm
                    v-if="record.status === 'approved' && !isLiveVersion(record)"
                    :title="`回滚到 v${record.versionNo}？将生成新的线上版本。`"
                    @confirm="rollback(record)"
                  >
                    <a-button type="link" size="small">回滚到此版本</a-button>
                  </a-popconfirm>
                  <span v-else-if="isLiveVersion(record)" class="current-version">当前线上</span>
                  <span v-else class="current-version">不可回滚</span>
                </template>
              </template>
            </a-table>

            <section class="version-compare" aria-label="版本差异比较">
              <div class="section-heading"><h3>版本差异</h3><span>比较两个版本的治理摘要</span></div>
              <a-space wrap>
                <a-select v-model:value="baseVersionNo" placeholder="基准版本" :options="versionOptions" style="width: 160px" />
                <a-select v-model:value="targetVersionNo" placeholder="对比版本" :options="versionOptions" style="width: 160px" />
                <a-button :disabled="!baseVersionNo || !targetVersionNo || baseVersionNo === targetVersionNo" @click="loadDiff">查看差异</a-button>
              </a-space>
              <a-alert v-if="diff" class="mt-3" type="info" show-icon :message="diff.changedFields.length ? `变更字段：${diff.changedFields.join('、')}` : '两个版本的治理摘要一致'" />
            </section>
          </a-tab-pane>

          <a-tab-pane v-if="isBuiltin" key="settings" tab="应用设置">
            <div class="tab-toolbar"><div><h3>应用设置</h3><p>管理应用信息、启停状态和使用权限。</p></div></div>
            <div class="governance-grid">
              <section class="governance-card">
                <h4>信息与权限</h4>
                <p>编辑名称、图标、描述，以及可使用该智能体的角色和部门。</p>
                <a-button v-auth="'app:app_info:manager'" :loading="configuring" @click="configureBuiltin">编辑信息与权限</a-button>
              </section>
              <section class="governance-card">
                <h4>启用状态</h4>
                <p>停用后，用户无法打开或继续使用该智能体。</p>
                <a-popconfirm v-if="detail.app.status === 'published'" title="确认停用该智能体？" @confirm="unpublish">
                  <a-button v-auth="'app:app_info:manager'" danger>停用智能体</a-button>
                </a-popconfirm>
                <a-popconfirm v-else title="确认启用该智能体？" @confirm="restore">
                  <a-button v-auth="'app:app_info:manager'" type="primary">启用智能体</a-button>
                </a-popconfirm>
              </section>
              <section class="governance-card danger-zone">
                <h4>删除应用</h4>
                <p>移除应用入口及其权限配置，已有会话不会被删除。</p>
                <a-popconfirm title="确认删除该智能体的应用入口？" ok-text="删除" cancel-text="取消" @confirm="remove">
                  <a-button v-auth="'app:app_info:delete'" danger>删除应用</a-button>
                </a-popconfirm>
              </section>
            </div>
          </a-tab-pane>

          <a-tab-pane v-else key="governance" tab="治理操作">
            <div class="tab-toolbar"><div><h3>生命周期管理</h3><p>高风险操作均需二次确认，操作记录会保留在审计中。</p></div></div>
            <div class="governance-grid">
              <section class="governance-card">
                <h4>发布状态</h4>
                <p>下架会停止对外运行与主对话候选召回。</p>
                <a-popconfirm v-if="detail.app.status === 'published'" title="确认下架该应用？下架后停止对外运行。" @confirm="unpublish">
                  <a-button danger>下架应用</a-button>
                </a-popconfirm>
                <a-popconfirm v-else-if="detail.app.status === 'unpublished'" title="确认恢复当前线上版本？" @confirm="restore">
                  <a-button type="primary">恢复上线</a-button>
                </a-popconfirm>
                <span v-else class="muted-copy">当前状态不支持切换</span>
              </section>
              <section class="governance-card">
                <h4>拥有者</h4>
                <p>将管理责任交接给指定用户，可保留原拥有者为编辑者。</p>
                <a-button @click="transferOpen = true">转移负责人</a-button>
              </section>
              <section class="governance-card danger-zone">
                <h4>删除应用</h4>
                <p>删除应用及其全部版本后不可恢复，请谨慎操作。</p>
                <a-popconfirm title="确认删除该应用及其全部版本？不可恢复。" ok-text="删除" cancel-text="取消" @confirm="remove">
                  <a-button danger>删除应用</a-button>
                </a-popconfirm>
              </section>
            </div>
          </a-tab-pane>

          <a-tab-pane v-if="!isBuiltin" key="audit" tab="应用操作审计">
            <div class="tab-toolbar">
              <div><h3>应用操作审计</h3><p>记录所有者、编辑者、审核员及管理员的关键操作，展示最近 20 条。</p></div>
            </div>
            <a-empty v-if="!detail.audits.length" description="暂无应用操作记录" />
            <a-timeline v-else class="audit-timeline">
              <a-timeline-item v-for="item in detail.audits" :key="item.id">
                <strong>{{ actionLabel(item.action) }}</strong>
                <span>{{ item.actorUsername || '-' }} · {{ formatManageDateTime(item.createdAt) }}</span>
                <p v-if="item.reason">{{ item.reason }}</p>
              </a-timeline-item>
            </a-timeline>
          </a-tab-pane>
        </a-tabs>
      </template>
    </a-spin>

    <a-modal v-model:open="transferOpen" title="转移负责人" :confirm-loading="transferring" @ok="transferOwner">
      <a-form layout="vertical">
        <a-form-item label="目标用户 ID" required><a-input v-model:value="targetUserId" placeholder="输入目标负责人的用户 ID" /></a-form-item>
        <a-form-item><a-checkbox v-model:checked="retainPreviousOwnerAsEditor">保留原负责人为编辑者</a-checkbox></a-form-item>
        <a-form-item label="操作原因"><a-textarea v-model:value="transferReason" :rows="3" /></a-form-item>
      </a-form>
    </a-modal>
    <CatalogAppEditor ref="catalogEditor" @success="onCatalogSaved" />
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import CatalogAppEditor from '../../flow/app/components/chooseModel.vue';
import { list as queryCatalogApps, updateStatus as updateCatalogStatus, deleteOne as deleteCatalogApp } from '../../flow/app/AppInfo.api';
import AgentMetricsTrendChart from '../../peopleCenter/components/AgentMetricsTrendChart.vue';
import AgentConversationLogPanel from '../components/AgentConversationLogPanel.vue';
import { getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../../peopleCenter/agentIcon';
import { getAgentCapabilityDefinition } from '../../peopleCenter/agentMarketCapabilities';
import { formatManageDateTime } from './manageDate';
import {
  adminDeleteApp,
  adminRestoreApp,
  adminRollbackApp,
  adminTransferAppOwner,
  adminUnpublishApp,
  queryAdminAppDetail,
  queryAdminAppMetrics,
  queryAdminVersionDiff,
  type AdminAppDetail,
  type AdminAppItem,
  type MetricRange,
  type WorkflowAppMetrics,
  type WorkflowVersionDiff,
  type WorkflowVersionItem,
} from '../api/workflow.api';

const props = defineProps<{ open: boolean; app: AdminAppItem | null }>();
const emit = defineEmits<{ (event: 'update:open', value: boolean): void; (event: 'changed'): void }>();
const detail = ref<AdminAppDetail | null>(null);
const metrics = ref<WorkflowAppMetrics | null>(null);
const diff = ref<WorkflowVersionDiff | null>(null);
const loading = ref(false);
const loadFailed = ref(false);
const metricsFailed = ref(false);
const metricsLoading = ref(false);
const configuring = ref(false);
const catalogEditor = ref<InstanceType<typeof CatalogAppEditor>>();
const isBuiltin = computed(() => detail.value?.app.aiAppType === 'builtin');
let detailRequest = 0;
let metricsRequest = 0;
const activeTab = ref('metrics');
const range = ref<MetricRange>('last_7_days');
const baseVersionNo = ref<number>();
const targetVersionNo = ref<number>();
const transferOpen = ref(false);
const transferring = ref(false);
const targetUserId = ref('');
const transferReason = ref('');
const retainPreviousOwnerAsEditor = ref(true);

const rangeOptions = [
  { value: 'today', label: '今天' },
  { value: 'last_7_days', label: '过去 7 天' },
  { value: 'last_4_weeks', label: '过去 4 周' },
  { value: 'last_3_months', label: '过去 3 月' },
  { value: 'last_12_months', label: '过去 12 月' },
  { value: 'month_to_date', label: '本月至今' },
  { value: 'quarter_to_date', label: '本季度至今' },
  { value: 'year_to_date', label: '本年至今' },
  { value: 'all_time', label: '所有时间' },
] as Array<{ value: MetricRange; label: string }>;

const versionColumns = [
  { title: '版本', key: 'versionNo', width: 114 },
  { title: '状态', key: 'status', width: 96 },
  { title: '提交人', dataIndex: 'submittedByName', key: 'submittedByName', width: 112 },
  { title: '发布时间', key: 'publishedAt', width: 170 },
  { title: '说明', dataIndex: 'changeNote', key: 'changeNote', ellipsis: true },
  { title: '操作', key: 'action', width: 118 },
];

const rangeLabel = computed(() => rangeOptions.find((item) => item.value === range.value)?.label || '所选时间');
const versionOptions = computed(() => (detail.value?.versions || []).map((item) => ({ value: item.versionNo, label: `v${item.versionNo} · ${statusLabel(item.status)}` })));
const categoryLabel = computed(() => getAgentCapabilityDefinition(detail.value?.app.appCategory).label);
const metricCards = computed(() => {
  const totals = metrics.value?.totals;
  return [
    { key: 'sessions', label: '全部会话数', value: totals?.sessions ?? 0, tip: '每天有成功回复的去重会话数之和，同一会话跨天使用会分别计数。' },
    { key: 'activeUsers', label: '活跃用户数', value: totals?.activeUsers ?? 0, tip: '完成有效互动的唯一用户数。' },
    { key: 'newUsers', label: '新增用户数', value: totals?.newUsers ?? 0, tip: '首次完成有效互动的用户数。' },
    { key: 'returningUsers', label: '回访用户数', value: totals?.returningUsers ?? 0, tip: '此前已使用过的活跃用户数。' },
    { key: 'averageMessages', label: '平均每会话消息数', value: totals?.averageMessages ?? 0, tip: '每个有效会话的成功回复数。' },
    { key: 'messages', label: '全部消息数', value: totals?.messages ?? 0, tip: '成功回复的总数量。' },
  ] as const;
});

function typeLabel(type?: string) {
  return ({ builtin: '定制智能体', chatAgent: '对话 Agent', workflow: '工作流' }[type || ''] || type || '-');
}

function statusLabel(status?: string) {
  if (isBuiltin.value && status === 'published') return '已启用';
  if (isBuiltin.value && status === 'unpublished') return '已停用';
  return ({ published: '已发布', unpublished: '已下架', draft: '草稿', pending_review: '待审核', approved: '已通过', rejected: '已驳回', cancelled: '已撤回' }[status || ''] || status || '-');
}

function statusColor(status?: string) {
  return ({ published: 'success', pending_review: 'processing', unpublished: 'warning', rejected: 'error', draft: 'default', approved: 'success' }[status || ''] || 'default');
}

function actionLabel(action: string) {
  return ({
    create: '创建应用', import: '导入应用', copy: '复制应用', update_app: '更新应用信息', update_acl: '更新应用权限', save_draft: '保存编排草稿',
    submit_review: '提交发布审核', publish: '发布上线', approve_review: '审核通过并上线', reject_review: '驳回发布审核',
    cancel_review: '撤回发布审核', unpublish: '下架应用', restore: '恢复上线', rollback: '版本回滚',
    transfer_owner: '转移负责人', delete: '删除应用',
  }[action] || action);
}

function isLiveVersion(version: WorkflowVersionItem) {
  return Boolean(version.isLive || version.versionNo === detail.value?.summary.publishedVersion);
}

async function load() {
  if (!props.app) return;
  const appId = props.app.id;
  const request = ++detailRequest;
  loading.value = true;
  loadFailed.value = false;
  try {
    const result = await queryAdminAppDetail(appId);
    if (request !== detailRequest || !props.open || props.app?.id !== appId) return;
    detail.value = result;
    void loadMetrics();
  } catch {
    if (request === detailRequest) loadFailed.value = true;
  } finally {
    if (request === detailRequest) loading.value = false;
  }
}

async function loadMetrics() {
  if (!props.app) return;
  const appId = props.app.id;
  const request = ++metricsRequest;
  metrics.value = null;
  metricsLoading.value = true;
  metricsFailed.value = false;
  try {
    const result = await queryAdminAppMetrics(appId, range.value);
    if (request === metricsRequest && props.open && props.app?.id === appId) metrics.value = result;
  } catch {
    if (request === metricsRequest) metricsFailed.value = true;
  } finally {
    if (request === metricsRequest) metricsLoading.value = false;
  }
}

async function configureBuiltin() {
  const catalogAppId = detail.value?.app.catalogAppId;
  if (!catalogAppId) return;
  configuring.value = true;
  try {
    const result = await queryCatalogApps({ id: catalogAppId, pageNo: 1, pageSize: 1 });
    const record = result.records?.find((item: { id: string }) => String(item.id) === catalogAppId);
    if (!record) throw new Error('应用入口不存在');
    await catalogEditor.value?.init(record, false);
  } catch {
    message.error('应用配置或权限暂时无法加载，请重试');
  } finally {
    configuring.value = false;
  }
}

function onCatalogSaved() {
  emit('changed');
  void load();
}

async function loadDiff() {
  if (!props.app || !baseVersionNo.value || !targetVersionNo.value) return;
  try {
    diff.value = await queryAdminVersionDiff(props.app.id, baseVersionNo.value, targetVersionNo.value);
  } catch {
    message.error('版本差异暂时无法加载');
  }
}

async function rollback(version: WorkflowVersionItem) {
  if (!props.app) return;
  try {
    await adminRollbackApp(props.app.id, version.versionNo);
    message.success(`已回滚到 v${version.versionNo}`);
    emit('changed');
    await load();
  } catch {
    message.error('版本回滚失败');
  }
}

async function unpublish() {
  if (!props.app) return;
  try {
    if (isBuiltin.value && detail.value?.app.catalogAppId) {
      await updateCatalogStatus({ id: detail.value.app.catalogAppId, status: '0' });
    } else {
      await adminUnpublishApp(props.app.id);
    }
    message.success(isBuiltin.value ? '已停用' : '已下架');
    emit('changed');
    await load();
  } catch {
    message.error('下架失败');
  }
}

async function restore() {
  if (!props.app) return;
  try {
    if (isBuiltin.value && detail.value?.app.catalogAppId) {
      await updateCatalogStatus({ id: detail.value.app.catalogAppId, status: '1' });
    } else {
      await adminRestoreApp(props.app.id);
    }
    message.success(isBuiltin.value ? '已启用' : '已恢复上线');
    emit('changed');
    await load();
  } catch {
    message.error('恢复上线失败');
  }
}

async function remove() {
  if (!props.app) return;
  try {
    if (isBuiltin.value && detail.value?.app.catalogAppId) {
      await deleteCatalogApp({ id: detail.value.app.catalogAppId }, () => undefined);
    } else {
      await adminDeleteApp(props.app.id);
    }
    message.success('应用已删除');
    emit('changed');
    emit('update:open', false);
  } catch {
    message.error('删除失败');
  }
}

async function transferOwner() {
  if (!props.app || !targetUserId.value.trim()) {
    message.warning('请输入目标用户 ID');
    return;
  }
  transferring.value = true;
  try {
    await adminTransferAppOwner({
      appId: props.app.id,
      targetUserId: targetUserId.value.trim(),
      retainPreviousOwnerAsEditor: retainPreviousOwnerAsEditor.value,
      reason: transferReason.value,
    });
    message.success('负责人已转移');
    transferOpen.value = false;
    emit('changed');
    await load();
  } catch {
    message.error('转移负责人失败');
  } finally {
    transferring.value = false;
  }
}

watch(
  () => [props.open, props.app?.id] as const,
  ([next]) => {
    detailRequest += 1;
    metricsRequest += 1;
    if (next) {
      activeTab.value = 'metrics';
      detail.value = null;
      metrics.value = null;
      diff.value = null;
      baseVersionNo.value = undefined;
      targetVersionNo.value = undefined;
      void load();
    }
  },
  { immediate: true },
);
watch(range, () => {
  if (props.open) void loadMetrics();
});
</script>

<style scoped lang="less">
.drawer-title { display: flex; align-items: baseline; gap: 10px; }
.drawer-title span { color: #0f172a; font-weight: 650; }
.drawer-title small { overflow: hidden; color: #64748b; font-size: 12px; font-weight: 400; text-overflow: ellipsis; white-space: nowrap; }

.app-hero { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 22px; border: 1px solid #dbe4f0; border-radius: 14px; background: linear-gradient(120deg, #f7faff 0%, #fff 62%, #f3f7ff 100%); }
.app-avatar { display: grid; width: 64px; height: 64px; overflow: hidden; place-items: center; border: 1px solid #d7e1f2; border-radius: 16px; background: #eaf0ff; color: #3159a8; font-size: 18px; font-weight: 700; }
.app-avatar img { width: 100%; height: 100%; object-fit: cover; }
.section-kicker { margin: 0 0 3px; color: #5273b5; font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.app-hero h2 { margin: 0; color: #10213d; font-size: 20px; line-height: 1.35; }
.app-description { display: -webkit-box; max-width: 680px; margin: 5px 0 10px; overflow: hidden; color: #53637a; font-size: 13px; line-height: 1.6; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.category-tag { color: #305aa8; border-color: #d3e0fb; background: #edf3ff; }
.live-version-card { min-width: 108px; padding-left: 18px; border-left: 1px solid #dce5f1; text-align: right; }
.live-version-card span { display: block; color: #718096; font-size: 12px; }
.live-version-card strong { display: block; margin-top: 3px; color: #173c78; font-size: 21px; font-variant-numeric: tabular-nums; }

.app-profile, .version-compare { margin-top: 18px; padding: 18px 20px; border: 1px solid #e5ebf3; border-radius: 12px; background: #fff; }
.section-heading, .tab-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.section-heading { margin-bottom: 14px; }
.section-heading h3, .tab-toolbar h3 { margin: 0; color: #152744; font-size: 15px; }
.section-heading span, .tab-toolbar p { margin: 3px 0 0; color: #7a8799; font-size: 12px; }
.profile-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0; margin: 0; border-top: 1px solid #edf1f6; border-left: 1px solid #edf1f6; }
.profile-grid div { min-height: 70px; padding: 13px 15px; border-right: 1px solid #edf1f6; border-bottom: 1px solid #edf1f6; }
.profile-grid dt { margin-bottom: 7px; color: #78869a; font-size: 12px; }
.profile-grid dd { margin: 0; overflow: hidden; color: #27364d; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }

.management-tabs { margin-top: 18px; }
.tab-toolbar { margin-bottom: 16px; }
.range-select { width: 132px; }
.metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.version-table :deep(.ant-table) { overflow: hidden; border: 1px solid #e7edf5; border-radius: 10px; }
.current-version { color: #8a97a8; font-size: 12px; }
.governance-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.governance-card { min-height: 170px; padding: 18px; border: 1px solid #e4eaf2; border-radius: 12px; background: #fff; }
.governance-card h4 { margin: 0; color: #253750; font-size: 14px; }
.governance-card p { min-height: 42px; margin: 8px 0 18px; color: #718096; font-size: 12px; line-height: 1.7; }
.danger-zone { border-color: #f1d8d8; background: #fffafa; }
.danger-zone h4 { color: #ad3030; }
.muted-copy { color: #8a97a8; font-size: 12px; }
.audit-timeline strong { margin-right: 8px; color: #27364d; }
.audit-timeline span { color: #7a8799; font-size: 12px; }
.audit-timeline p { margin: 4px 0 0; color: #65758a; }

@media (max-width: 900px) {
  .app-hero { grid-template-columns: 56px minmax(0, 1fr); }
  .app-avatar { width: 56px; height: 56px; }
  .live-version-card { grid-column: 2; padding: 0; border: 0; text-align: left; }
  .profile-grid, .governance-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .app-hero { grid-template-columns: 1fr; padding: 18px; }
  .live-version-card { grid-column: auto; text-align: left; }
  .profile-grid, .governance-grid, .metric-grid { grid-template-columns: 1fr; }
  .tab-toolbar { align-items: flex-start; flex-direction: column; }
}
</style>
