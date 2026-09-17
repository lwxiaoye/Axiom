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
import { ref } from 'vue';
import { message } from 'ant-design-vue';
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
  type RouteMetadata,
} from '../../workflow/api/workflow.api';

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
  await reloadApps();
}

function openPublishModal(item: Partial<AiWorkflowApp>) {
  publishModalRef.value?.init(item);
}

function openVersionDrawer(item: Partial<AiWorkflowApp>) {
  versionDrawerRef.value?.init(item);
}

function openApiDrawer(item: Partial<AiWorkflowApp>) {
  apiDrawerRef.value?.init(item);
}

async function handleRolledBack() {
  await loadMyAgents();
  await reloadApps();
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
    const workflowJson = definition?.draftJson || definition?.publishedJson;
    if (!workflowJson) {
      message.warning('请先完成编排并保存草稿后再发布');
      return;
    }
    const result = await submitWorkflowReview({
      appId: String(payload.app.id),
      workflowJson,
      changeNote: payload.changeNote,
      visibleRoleIds: payload.visibleRoleIds,
      visibleDeptIds: payload.visibleDeptIds,
      routeMetadata: payload.routeMetadata,
    });
    publishModalRef.value?.close();
    await loadMyAgents();
    await reloadApps();
    const updating = payload.app.status === 'published';
    message.success(
      result?.approvalRequired === false
        ? (updating ? '更新已发布上线' : '已发布上线')
        : (updating ? '已提交更新审核，当前线上版本会继续运行' : '已提交发布审核，待审核通过后进入智能体广场')
    );
  } catch (error: any) {
    const detail = error?.response?.data?.detail;
    const messageText = typeof detail === 'string' ? detail : detail?.message || '提交发布失败';
    message.error(messageText);
  } finally {
    publishing.value = false;
  }
}
</script>
