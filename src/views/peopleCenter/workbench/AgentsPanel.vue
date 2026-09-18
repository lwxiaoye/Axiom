<template>
  <div class="workbench-panel">
    <div class="market-heading">
      <div>
        <span>MY AGENTS</span>
        <h2>我的智能体</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input v-model="searchKeyword" placeholder="搜索我的智能体..." />
      </div>
      <button class="market-refresh" type="button" @click="emit('reload')">刷新</button>
    </div>

    <div class="category-filter" aria-label="智能体范围筛选">
      <button
        v-for="item in scopeOptions"
        :key="item.value"
        type="button"
        :class="['category-filter-item', { active: scope === item.value }]"
        @click="scope = item.value"
      >
        <span>{{ item.label }}</span>
        <em>{{ item.count }}</em>
      </button>
      <button
        v-for="item in kindFilters"
        :key="item.value"
        type="button"
        :class="['category-filter-item', { active: kindFilter === item.value }]"
        @click="kindFilter = kindFilter === item.value ? '' : item.value"
      >
        <span>{{ item.label }}</span>
      </button>
    </div>

    <div v-if="loading" class="status-box">
      <LoadingOutlined /> 正在加载我的智能体...
    </div>
    <div v-else-if="loadFailed && !agentRecords.length" class="wb-unready">
      <ApiOutlined />
      <strong>智能体服务暂不可用</strong>
      <p>工作流运行时（agent-api）未连接或未部署，恢复后点击刷新即可。</p>
      <a-button size="small" @click="emit('reload')">重试</a-button>
    </div>
    <div v-else class="wb-grid">
      <button v-if="scope === 'owned'" class="wb-create-card" type="button" @click="openCreate">
        <span class="wb-create-plus"><PlusOutlined /></span>
        <strong>新建智能体</strong>
        <small>从对话 Agent 或工作流开始</small>
      </button>
      <button v-if="scope === 'owned'" class="wb-create-card" type="button" @click="importVisible = true">
        <span class="wb-create-plus"><InboxOutlined /></span>
        <strong>导入智能体</strong>
        <small>上传 JSON 配置包</small>
      </button>

      <div
        v-for="item in filteredAgents"
        :key="item.id"
        class="wb-card"
        role="button"
        tabindex="0"
        :title="`打开${getAiAppPrimaryAction(item)}`"
        @click="emit('openDesigner', item)"
        @keydown.enter="emit('openDesigner', item)"
      >
        <div class="wb-card-head">
          <span :class="['wb-tile', getAiAppKind(item)]">
            <span>{{ getAgentInitials(item) }}</span>
            <img
              v-if="getAgentIconUrl(item)"
              :src="getAgentIconUrl(item)"
              :alt="item.name || item.appName"
              @error="hideBrokenIcon"
            />
          </span>
          <div class="wb-card-ident">
            <strong class="wb-card-title">{{ item.name || item.appName }}</strong>
            <span class="wb-card-sub">
              {{ getAiAppKindLabel(item) }}
              <template v-if="!isOwner(item)"> · 共享给我 · {{ canEdit(item) ? '可编辑' : '仅查看' }}</template>
            </span>
          </div>
        </div>

        <p class="wb-card-desc">{{ item.description || item.appRemark || '暂无描述' }}</p>

        <div class="wb-card-foot">
          <span :class="['wb-status', statusClass(item)]">
            <i></i>
            {{ statusText(item) }}
          </span>
          <!-- 驳回原因入口已并入右侧「下一步」按钮；这里只给没有该按钮的人（非编辑者）留一个入口 -->
          <button
            v-if="reviewPresentation(item).reviewComment && !(isOwner(item) || canEdit(item))"
            class="wb-review-reason"
            type="button"
            @click.stop="showReviewReason(item)"
          >
            查看原因
          </button>
          <div class="wb-card-ops" @click.stop @keydown.enter.stop>
            <!-- 每个状态都给出下一步：已发布→运行；草稿→去配置；待审核→等待；驳回→看原因并重提；下架→重新发布 -->
            <button
              v-if="nextAction(item).key === 'run'"
              class="wb-op"
              type="button"
              title="运行"
              @click="emit('run', item)"
            >
              <PlayCircleOutlined />
              <span>运行</span>
            </button>
            <span v-else-if="nextAction(item).passive" class="wb-op wb-op-passive" :title="nextAction(item).label">
              <ClockCircleOutlined />
              <span>{{ nextAction(item).label }}</span>
            </span>
            <button
              v-else-if="isOwner(item) || canEdit(item)"
              class="wb-op"
              type="button"
              :title="nextAction(item).label"
              @click="runNextAction(item)"
            >
              <SettingOutlined v-if="nextAction(item).key === 'configure'" />
              <SendOutlined v-else />
              <span>{{ nextAction(item).label }}</span>
            </button>
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <button class="wb-op icon-only" type="button" title="更多操作" aria-label="更多操作">
                <EllipsisOutlined />
              </button>
              <template #overlay>
                <a-menu @click="({ key }) => onMenuClick(String(key), item)">
                  <a-menu-item v-if="canEdit(item)" key="settings"><SettingOutlined /> 设置</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="access"><TeamOutlined /> 授权</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="export"><DownloadOutlined /> 导出</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="copy"><CopyOutlined /> 复制</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="metrics"><LineChartOutlined /> 监测</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="conversationLogs"><FileTextOutlined /> 对话日志</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="versions"><HistoryOutlined /> 版本记录</a-menu-item>
                  <a-menu-item v-if="isOwner(item) && isPublished(item)" key="agentApi"><ApiOutlined /> 公开配置</a-menu-item>
                  <template v-if="isOwner(item)">
                    <a-menu-divider />
                    <a-menu-item v-if="canSubmitPublish(item)" key="publish"><SendOutlined /> {{ publishActionLabel(item) }}</a-menu-item>
                    <a-menu-item v-else-if="canWithdrawReview(item)" key="withdrawReview"><UndoOutlined /> 撤回审核</a-menu-item>
                    <template v-if="isPublished(item)">
                      <a-menu-divider />
                      <a-menu-item key="unpublish" class="wb-menu-danger"><StopOutlined /> 下架</a-menu-item>
                    </template>
                    <a-menu-item key="delete" class="wb-menu-delete"><DeleteOutlined /> 删除</a-menu-item>
                  </template>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
        </div>
      </div>

      <div v-if="agentRecords.length && !filteredAgents.length" class="wb-empty">
        没有匹配的智能体，换个关键词或类型试试
      </div>
      <div v-else-if="scope === 'shared' && !filteredAgents.length" class="wb-empty">还没有共享给你的智能体</div>
    </div>

    <a-modal v-model:open="importVisible" title="导入智能体" :width="540" :footer="null" wrap-class-name="wb-modal">
      <div class="wb-modal-form">
        <a-upload-dragger
          accept=".json,.qz-agent.json"
          :max-count="1"
          :before-upload="handleImport"
          :show-upload-list="false"
          :disabled="importing"
        >
          <p class="ant-upload-drag-icon"><InboxOutlined /></p>
          <p class="ant-upload-text">点击或拖拽智能体 JSON 配置包到此处</p>
          <p class="ant-upload-hint">最大 5MB，导入后以草稿形式出现在我的智能体</p>
        </a-upload-dragger>
        <div v-if="importing" class="import-loading">
          <LoadingOutlined /> 正在上传与校验...
        </div>
      </div>
    </a-modal>

    <WorkflowAppModal ref="formModelRef" context="agent" @success="emit('reload')" />
    <WorkflowAppAclModal ref="aclModalRef" @success="emit('reload')" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Modal, message } from 'ant-design-vue';
import {
  ApiOutlined,
  ClockCircleOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EllipsisOutlined,
  FileTextOutlined,
  HistoryOutlined,
  InboxOutlined,
  LineChartOutlined,
  LoadingOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  SearchOutlined,
  SendOutlined,
  SettingOutlined,
  StopOutlined,
  TeamOutlined,
  UndoOutlined,
} from '@ant-design/icons-vue';
import { downloadByData } from '/@/utils/file/download';
import { getAgentIconUrl, getAgentInitials } from '../agentIcon';
import WorkflowAppAclModal from '../components/WorkflowAppAclModal.vue';
import WorkflowAppModal from '../components/WorkflowAppModal.vue';
import {
  cancelPublishWorkflowApp,
  cancelReview,
  copyWorkflowApp,
  exportWorkflowAppPackage,
  importWorkflowAppPackage,
} from '../../workflow/api/workflow.api';
import { buildWorkflowAppPackageFilename, isWorkflowAppPackageFile } from '../../workflow/shared/appPackage';
import {
  AGENT_KINDS,
  getAiAppKind,
  getAiAppKindLabel,
  getAiAppKindLabelByKind,
  getAiAppPrimaryAction,
  type AiAppKind,
} from '../../workflow/shared/agentApp';
import { getWorkflowNextAction, getWorkflowReviewPresentation } from '../../workflow/shared/reviewStatus';

const props = defineProps<{
  agents: any[];
  loading: boolean;
  loadFailed?: boolean;
}>();

const emit = defineEmits<{
  (e: 'reload'): void;
  (e: 'preview', item: any): void;
  (e: 'run', item: any): void;
  (e: 'openDesigner', item: any): void;
  (e: 'open-metrics', item: any): void;
  (e: 'open-conversation-logs', item: any): void;
  (e: 'publish', item: any): void;
  (e: 'versions', item: any): void;
  (e: 'agent-api', item: any): void;
  (e: 'delete', item: any): void;
}>();

const formModelRef = ref<InstanceType<typeof WorkflowAppModal> | null>(null);
const aclModalRef = ref<InstanceType<typeof WorkflowAppAclModal> | null>(null);
const searchKeyword = ref('');
const scope = ref<'owned' | 'shared'>('owned');
const kindFilter = ref<AiAppKind | ''>('');
const importVisible = ref(false);
const importing = ref(false);

/** 工具类型的应用归「我的工具」面板管理 */
const agentRecords = computed(() => props.agents.filter((item) => AGENT_KINDS.includes(getAiAppKind(item))));

/** simple（对话 Agent V1）已排除，仅当存在旧数据时才出现筛选项 */
const kindFilters = computed(() =>
  AGENT_KINDS.filter(
    (kind) => kind !== 'simple' || agentRecords.value.some((item) => getAiAppKind(item) === 'simple')
  ).map((kind) => ({ value: kind, label: getAiAppKindLabelByKind(kind) }))
);

const scopeOptions = computed(() => [
  { label: '我创建的', value: 'owned' as const, count: ownedAgents.value.length },
  { label: '共享给我的', value: 'shared' as const, count: sharedAgents.value.length },
]);

const ownedAgents = computed(() => agentRecords.value.filter((item) => isOwner(item)));
const sharedAgents = computed(() => agentRecords.value.filter((item) => !isOwner(item)));
const scopedAgents = computed(() => (scope.value === 'owned' ? ownedAgents.value : sharedAgents.value));

const filteredAgents = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase();
  return scopedAgents.value.filter((item) => {
    if (kindFilter.value && getAiAppKind(item) !== kindFilter.value) return false;
    if (!keyword) return true;
    return (
      String(item?.name || item?.appName || '').toLowerCase().includes(keyword) ||
      String(item?.description || item?.appRemark || '').toLowerCase().includes(keyword) ||
      String(item?.appCategory_dictText || item?.appCategory || '').toLowerCase().includes(keyword) ||
      getAiAppKindLabel(item).toLowerCase().includes(keyword)
    );
  });
});

function openCreate() {
  formModelRef.value?.init();
}

function hideBrokenIcon(event: Event) {
  (event.target as HTMLImageElement).style.display = 'none';
}

function isPublished(item: any) {
  return String(item?.status) === 'published';
}

function statusClass(item: any) {
  return reviewPresentation(item).statusClass;
}

function statusText(item: any) {
  return reviewPresentation(item).text;
}

function reviewPresentation(item: any) {
  return getWorkflowReviewPresentation(item);
}

function nextAction(item: any) {
  return getWorkflowNextAction(item);
}

/** 卡片「下一步」按钮：草稿去配置、驳回看原因后重提、下架重新发布 */
function runNextAction(item: any) {
  const action = nextAction(item);
  if (action.key === 'configure') emit('openDesigner', item);
  else if (action.key === 'resubmit') showReviewReason(item, canSubmitPublish(item));
  else if (action.key === 'publish') emit('publish', item);
}

function isOwner(item: any) {
  return String(item?.sharePermission || '').toUpperCase() === 'OWNER' || !item?.sharePermission;
}

function canEdit(item: any) {
  const permission = String(item?.sharePermission || '').toUpperCase();
  return isOwner(item) || permission === 'EDITOR';
}

function canSubmitPublish(item: any) {
  return reviewPresentation(item).canSubmit;
}

function canWithdrawReview(item: any) {
  return reviewPresentation(item).canWithdraw;
}

function publishActionLabel(item: any) {
  if (isPublished(item)) {
    return item?.reviewSummary?.status === 'rejected' ? '重新提交更新' : '更新发布';
  }
  return item?.reviewSummary?.status === 'rejected' ? '重新提交发布' : '发布';
}

function onMenuClick(key: string, item: any) {
  if (key === 'metrics') emit('open-metrics', item);
  else if (key === 'conversationLogs') emit('open-conversation-logs', item);
  else if (key === 'settings') formModelRef.value?.init(item);
  else if (key === 'access') aclModalRef.value?.init(item);
  else if (key === 'export') exportAgent(item);
  else if (key === 'copy') copyAgent(item);
  else if (key === 'versions') emit('versions', item);
  else if (key === 'agentApi') emit('agent-api', item);
  else if (key === 'publish') emit('publish', item);
  else if (key === 'withdrawReview') withdrawReview(item);
  else if (key === 'unpublish') confirmUnpublish(item);
  else if (key === 'delete') confirmDelete(item);
}

async function handleImport(file: File) {
  if (!isWorkflowAppPackageFile(file)) {
    message.warning('请选择 5MB 以内的 JSON 智能体配置包');
    return false;
  }
  importing.value = true;
  try {
    await importWorkflowAppPackage(file);
    message.success('智能体导入成功');
    importVisible.value = false;
    emit('reload');
  } catch (error: any) {
    message.error(error?.message || error?.response?.data?.detail || '导入失败，请检查配置包格式');
  } finally {
    importing.value = false;
  }
  return false;
}

async function exportAgent(item: any) {
  try {
    const data = await exportWorkflowAppPackage(item.id);
    downloadByData(
      JSON.stringify(data, null, 2),
      buildWorkflowAppPackageFilename(item.name || item.appName),
      'application/json;charset=utf-8'
    );
    message.success('已导出智能体配置包');
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '导出失败');
  }
}

async function copyAgent(item: any) {
  try {
    await copyWorkflowApp(item.id);
    message.success('已复制为新的草稿智能体');
    emit('reload');
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '复制失败');
  }
}

function confirmDelete(item: any) {
  Modal.confirm({
    title: '删除智能体',
    content: `确定删除「${item.name || item.appName || '未命名'}」？删除后不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => emit('delete', item),
  });
}

function confirmUnpublish(item: any) {
  Modal.confirm({
    title: '下架智能体',
    content: `下架「${item.name || item.appName || '未命名'}」后，普通用户将无法运行它；它会从智能体广场和主对话路由候选中移除。草稿与版本记录会保留。`,
    okText: '确认下架',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => unpublish(item),
  });
}

async function unpublish(item: any) {
  try {
    await cancelPublishWorkflowApp(item.id);
    message.success('智能体已下架');
    emit('reload');
  } catch (error: any) {
    message.error(error?.response?.data?.detail || '下架失败');
  }
}

function showReviewReason(item: any, offerResubmit = false) {
  const review = reviewPresentation(item);
  const reviewer = item?.reviewSummary?.reviewedByName ? `（审核员：${item.reviewSummary.reviewedByName}）` : '';
  const content = `${review.reviewComment || '审核员未填写具体原因'}${reviewer}`;
  if (!offerResubmit) {
    Modal.info({
      title: `驳回原因 · v${item?.reviewSummary?.versionNo || '-'}`,
      content,
      okText: '知道了',
    });
    return;
  }
  // 驳回后的下一步是「改完重新提交」：看完原因直接进发布弹窗，不用再去更多操作里找；
  // 要先改配置的话点卡片本身就是进配置页。
  Modal.confirm({
    title: `驳回原因 · v${item?.reviewSummary?.versionNo || '-'}`,
    content: `${content}。修改后可直接重新提交；需要先改配置请点击卡片进入。`,
    okText: '重新提交发布',
    cancelText: '关闭',
    onOk: () => emit('publish', item),
  });
}

async function withdrawReview(item: any) {
  const versionId = item?.reviewSummary?.versionId;
  if (!versionId) return;
  await cancelReview(versionId);
  message.success('已撤回审核');
  emit('reload');
}
</script>

<style scoped lang="less">
.import-loading {
  margin-top: 12px;
  text-align: center;
  color: #64748b;
  font-size: 12px;
}

.wb-review-reason {
  margin-left: -2px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #4f6ef7;
  font-size: 12px;
  cursor: pointer;
}

.wb-review-reason:hover {
  color: #3d5ce5;
  text-decoration: underline;
}

/* 待审核：只是状态说明，不是按钮 */
.wb-op.wb-op-passive {
  cursor: default;
  color: #64748b;
  border-style: dashed;
  background: transparent;
}
</style>
