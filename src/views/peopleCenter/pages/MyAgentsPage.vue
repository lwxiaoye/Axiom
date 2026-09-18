<template>
  <MyAgentsTab
    :agents="myAgentList"
    :loading="myAgentLoading"
    :load-failed="myAgentLoadFailed"
    @reload="loadMyAgents"
    @preview="openAiAppRunner"
    @run="openAiAppRunner"
    @open-designer="openAiAppDesigner"
    @open-metrics="openMetrics"
    @open-conversation-logs="openConversationLogs"
    @publish="openPublishModal"
    @versions="openVersionDrawer"
    @agent-api="openApiDrawer"
    @delete="handleDeleteMyAgent"
    @review-changed="handleReviewChanged"
  />
  <WorkflowAppPublishModal ref="publishModalRef" :loading="publishing" @submit="submitPublish" />
  <WorkflowAppVersionDrawer ref="versionDrawerRef" @rolled-back="handleRolledBack" />
  <WorkflowAppApiDrawer ref="apiDrawerRef" />
  <AgentConversationLogDrawer
    v-model:open="conversationLogOpen"
    :app-id="selectedConversationLogApp?.id"
    :app-title="selectedConversationLogApp?.name"
  />
</template>

<script setup lang="ts">
import { h, ref } from 'vue';
import { Modal, message } from 'ant-design-vue';
import { useRouter } from 'vue-router';
import MyAgentsTab from '../tabs/MyAgentsTab.vue';
import WorkflowAppPublishModal from '../components/WorkflowAppPublishModal.vue';
import WorkflowAppVersionDrawer from '../components/WorkflowAppVersionDrawer.vue';
import WorkflowAppApiDrawer from '../components/WorkflowAppApiDrawer.vue';
import AgentConversationLogDrawer from '../../workflow/components/AgentConversationLogDrawer.vue';
import { useCenterContext } from '../centerContext';
import {
  queryWorkflowDefinition,
  submitWorkflowReview,
  type AiWorkflowApp,
  type AiWorkflowDefinition,
  type RouteMetadata,
} from '../../workflow/api/workflow.api';
import {
  describeSubmitReviewFailure,
  getPublishNextStepLabel,
  getPublishReadiness,
} from '../../workflow/shared/publishReadiness';

defineOptions({ name: 'CenterMyAgentsPage' });

const ctx = useCenterContext();
const router = useRouter();
const {
  myAgentList,
  myAgentLoading,
  myAgentLoadFailed,
  loadMyAgents,
  openAiAppDesigner,
  openAiAppRunner,
  deleteMyAgent,
} = ctx.myAgents;
const { reloadApps } = ctx.agentMarket;

const publishModalRef = ref<InstanceType<typeof WorkflowAppPublishModal> | null>(null);
const versionDrawerRef = ref<InstanceType<typeof WorkflowAppVersionDrawer> | null>(null);
const apiDrawerRef = ref<InstanceType<typeof WorkflowAppApiDrawer> | null>(null);
const publishing = ref(false);
const conversationLogOpen = ref(false);
const selectedConversationLogApp = ref<AiWorkflowApp | null>(null);

function openMetrics(item: AiWorkflowApp) {
  if (!item?.id) {
    message.error('未找到应用ID');
    return;
  }
  router.push({ name: 'CenterAgentMetrics', params: { appId: String(item.id) } });
}

function openConversationLogs(item: AiWorkflowApp) {
  if (!item?.id) return;
  selectedConversationLogApp.value = item;
  conversationLogOpen.value = true;
}

async function handleDeleteMyAgent(record: any) {
  await deleteMyAgent(record);
  await reloadApps({ force: true });
}

/** 弹发布窗之前先看草稿能不能发：没保存过配置/没选模型的直接指到配置页，别让用户填完弹窗才吃 400 */
async function openPublishModal(item: Partial<AiWorkflowApp>) {
  if (!item?.id) {
    message.error('未找到应用ID');
    return;
  }
  let definition: AiWorkflowDefinition | null = null;
  try {
    definition = await queryWorkflowDefinition({ appId: String(item.id) });
  } catch (error) {
    // 读不到草稿也不拦：让后端校验给出最终结论
    console.warn('load definition before publish failed', error);
  }
  const readiness = getPublishReadiness(item, definition);
  if (!readiness.ready) {
    offerConfiguration(item, '还不能发布', readiness.reason, readiness.nextStepLabel);
    return;
  }
  publishModalRef.value?.init(item);
}

function offerConfiguration(item: Partial<AiWorkflowApp>, title: string, reason: string, okText = '去配置', problems: string[] = []) {
  Modal.confirm({
    title,
    content: h('div', {}, [
      h('p', { style: 'margin:0 0 8px' }, reason),
      problems.length
        ? h('ul', { style: 'margin:0;padding-left:18px;color:#64748b' }, problems.map((text) => h('li', {}, text)))
        : null,
    ]),
    okText,
    cancelText: '稍后再说',
    onOk: () => openAiAppDesigner(item),
  });
}

function openVersionDrawer(item: Partial<AiWorkflowApp>) {
  versionDrawerRef.value?.init(item);
}

function openApiDrawer(item: Partial<AiWorkflowApp>) {
  apiDrawerRef.value?.init(item);
}

async function handleRolledBack() {
  await loadMyAgents();
  await reloadApps({ force: true });
}

/** 审核台通过/驳回后：我的智能体状态和广场目录都要跟着刷新 */
async function handleReviewChanged() {
  await loadMyAgents();
  await reloadApps({ force: true });
}

async function submitPublish(payload: {
  app: Partial<AiWorkflowApp>;
  visibleRoleIds?: string[] | string;
  visibleDeptIds?: string[] | string;
  changeNote?: string;
  routeMetadata?: RouteMetadata;
}) {
  if (!payload.app?.id || publishing.value) return;
  publishing.value = true;
  try {
    const definition = await queryWorkflowDefinition({ appId: String(payload.app.id) });
    const readiness = getPublishReadiness(payload.app, definition);
    if (!readiness.ready) {
      publishModalRef.value?.close();
      offerConfiguration(payload.app, '还不能发布', readiness.reason, readiness.nextStepLabel);
      return;
    }
    const result = await submitWorkflowReview({
      appId: String(payload.app.id),
      workflowJson: readiness.workflowJson,
      changeNote: payload.changeNote,
      visibleRoleIds: payload.visibleRoleIds,
      visibleDeptIds: payload.visibleDeptIds,
      routeMetadata: payload.routeMetadata,
    });
    publishModalRef.value?.close();
    await loadMyAgents();
    await reloadApps({ force: true });
    const updating = payload.app.status === 'published';
    if (result?.autoApproved) {
      // 审核员本人提交：后端直接上线，不进待审队列
      message.success(updating ? '你是审核员，更新已自动通过并上线' : '你是审核员，已自动通过并上线，可在广场运行');
    } else {
      message.success(
        result?.approvalRequired === false
          ? (updating ? '更新已发布上线' : '已发布上线')
          : (updating ? '已提交更新审核，当前线上版本会继续运行' : '已提交发布审核，待审核通过后进入智能体广场')
      );
    }
  } catch (error: any) {
    const failure = describeSubmitReviewFailure(error);
    if (failure.needsConfiguration) {
      // 后端校验 400 不能是终点：把原因原样列出来，并给「去配置」
      publishModalRef.value?.close();
      offerConfiguration(
        payload.app,
        failure.message,
        failure.problems.length ? '请先修正以下问题再发布：' : '请先完成配置并保存草稿后再发布。',
        getPublishNextStepLabel(payload.app),
        failure.problems,
      );
      return;
    }
    message.error(failure.message);
  } finally {
    publishing.value = false;
  }
}
</script>
