<template>
  <section
    class="agent-run-page is-fullpage builtin-harness-page"
    :class="{ 'sidebar-collapsed': sidebarCollapsed }"
    :style="pageStyle"
  >
    <RunCompactHeader
      v-if="appMeta"
      :title="appMeta.appName"
      :sidebar-collapsed="sidebarCollapsed"
      show-back
      @toggle-sidebar="toggleSidebar"
      @back="goBack"
    />
    <Transition name="run-history-backdrop">
      <button
        v-if="appMeta && !sidebarCollapsed"
        type="button"
        class="run-mobile-sidebar-backdrop"
        aria-label="收起会话侧栏"
        @click="toggleSidebar"
      ></button>
    </Transition>

    <aside v-if="appMeta" class="agent-run-sidebar">
      <button
        type="button"
        class="run-back"
        :title="canGoBack ? '返回上一页' : '返回智能体广场'"
        :aria-label="canGoBack ? '返回上一页' : '返回智能体广场'"
        @click="goBack"
      >
        <ArrowLeftOutlined />
        <span class="run-back-label">{{ canGoBack ? '返回' : '智能体广场' }}</span>
      </button>
      <div class="run-brand">
        <span class="run-brand-avatar" aria-hidden="true">
          <span>{{ brandInitial }}</span>
          <img
            v-if="brandIcon && !iconLoadFailed"
            :src="brandIcon"
            :alt="appMeta.appName"
            @error="iconLoadFailed = true"
          />
        </span>
        <span class="run-brand-copy">
          <strong>{{ appMeta.appName }}</strong>
        </span>
        <RunSidebarToggle :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
      </div>

      <button class="new-session-btn" type="button" @click="newConversation">
        <svg class="compose-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" />
        </svg>
        <span class="new-session-label">新对话</span>
      </button>

      <div class="session-search">
        <SearchOutlined />
        <input
          v-model="centerChat.threadSearch.value"
          placeholder="搜索对话"
          @input="centerChat.searchThreads"
          @keyup.enter="centerChat.loadThreads()"
        />
      </div>

      <RunSessionList
        :sessions="sessions"
        :active-session-id="centerChat.currentThreadId.value"
        :running-session-ids="runningSessionIds"
        @select="selectConversation"
        @pin="centerChat.togglePin"
        @rename="centerChat.handleRenameThread"
        @remove="confirmRemoveConversation"
      />
      <button
        v-if="centerChat.threadHasMore.value"
        class="load-more-sessions"
        type="button"
        :disabled="centerChat.threadsLoading.value"
        @click="centerChat.loadMoreThreads"
      >
        {{ centerChat.threadsLoading.value ? '加载中…' : '加载更多' }}
      </button>
    </aside>

    <main ref="mainRef" class="agent-run-main builtin-harness-main" :inert="compactHistoryOpen">
      <div v-if="accessLoading" class="builtin-access-state">
        <a-spin size="large" />
        <p>正在加载系统应用…</p>
      </div>
      <div v-else-if="accessError" class="builtin-access-state is-error">
        <div class="access-error-code">{{ accessErrorCode }}</div>
        <h1>{{ accessErrorTitle }}</h1>
        <p>{{ accessError }}</p>
        <a-button type="primary" @click="goToMarketplace">返回智能体广场</a-button>
      </div>
      <ChatPage v-else-if="appMeta" />
    </main>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue';
import { Modal, message } from 'ant-design-vue';
import { ArrowLeftOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { useRoute, useRouter } from 'vue-router';
import ChatPage from './ChatPage.vue';
import RunCompactHeader from '../../agent/run/components/RunCompactHeader.vue';
import RunSessionList from '../../agent/run/components/RunSessionList.vue';
import RunSidebarToggle from '../../agent/run/components/RunSidebarToggle.vue';
import type { RunSession } from '../../agent/run/agentRun.api';
import { CenterContextKey, type CenterContext } from '../centerContext';
import { getBuiltinApp, type BuiltinAppItem } from '../agentApi';
import { getBuiltinAssistantByPreset, type AssistantPreset } from '../builtinAssistants';
import { InterviewSessionKey, useInterviewSession } from '../builtinAssistants/interview/useInterviewSession';
import { useCenterChat } from '../composables/useCenterChat';
import type { CenterSectionKey } from '../composables/useAgentMarket';
import { openAgentRunWindow } from '@/views/workflow/shared/runtimeRoute';
import { usePageBack } from '/@/hooks/web/usePageBack';

defineOptions({ name: 'BuiltinHarnessRunPage' });

const props = defineProps<{
  preset: AssistantPreset;
}>();

const route = useRoute();
const router = useRouter();
// 子智能体是从工作台点进来的独立页面，必须给一条明确的回头路；直接贴地址栏
// 打开时没有上一页，退回智能体广场。
const { goBack, canGoBack } = usePageBack('/center/agent');

const appMeta = ref<BuiltinAppItem | null>(null);
const mainRef = ref<HTMLElement | null>(null);
const accessLoading = ref(true);
const accessError = ref('');
const accessStatus = ref(0);
const iconLoadFailed = ref(false);
const brandIcon = computed(() => {
  const assistant = getBuiltinAssistantByPreset(props.preset);
  const configuredIcon = appMeta.value?.appIcon?.trim();
  return configuredIcon && configuredIcon !== assistant?.legacyIcon ? configuredIcon : assistant?.icon;
});
const viewportWidth = ref(typeof window === 'undefined' ? 1280 : window.innerWidth);
const sidebarCollapsed = ref(viewportWidth.value <= 1024);
const compactHistoryOpen = computed(() => viewportWidth.value <= 1024 && !sidebarCollapsed.value);
let sidebarReturnFocus: HTMLElement | null = null;
const activeSection = computed({
  get: (): CenterSectionKey => 'chat',
  set: (_section: CenterSectionKey) => {},
});
const marketplaceApps = computed(() => appMeta.value ? [appMeta.value] : []);
const marketplaceLoading = computed(() => accessLoading.value);

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === 'string' && error.trim()) return error;
  if (error && typeof error === 'object') {
    const payload = error as { message?: unknown; detail?: unknown; error?: unknown };
    const candidate = payload.message ?? payload.detail ?? payload.error;
    if (typeof candidate === 'string' && candidate.trim()) return candidate;
  }
  return '请稍后重试';
}

function showError(error: unknown) {
  message.error(errorMessage(error));
}

function showNotice(notice: string) {
  message.success(notice);
}

const centerChat = useCenterChat({
  activeSection,
  appList: marketplaceApps,
  reloadApps: async () => {},
  showError,
  showNotice,
  fixedAssistantPreset: props.preset,
  threadScope: props.preset,
});

if (props.preset === 'interview') {
  const settledRunKey = computed(() => {
    const latest = [...centerChat.chatMessages.value].reverse().find(
      (item) => item.role === 'assistant' && item.runCompletedAt,
    );
    return latest ? `${latest.runId || latest.id}:${latest.runCompletedAt}` : '';
  });
  const interviewSession = useInterviewSession({
    currentThreadId: centerChat.currentThreadId,
    chatLoading: centerChat.chatLoading,
    settledRunKey,
    sendInterviewMessage: centerChat.sendInterviewMessage,
  });
  centerChat.setInterviewInputProvider(interviewSession.getAnswerInput, interviewSession.onSubmissionRejected);
  provide(InterviewSessionKey, interviewSession);
  // Keep an accepted new interview addressable so refresh can recover this exact session.
  watch(centerChat.currentThreadId, (threadId) => {
    if (!threadId || accessLoading.value || route.path !== '/center/chat/interview' || route.query.thread === threadId) return;
    void router.replace({ path: route.path, query: { ...route.query, thread: threadId } });
  });
}

const agentMarket = {
  appList: marketplaceApps,
  appLoading: marketplaceLoading,
} as unknown as CenterContext['agentMarket'];

function switchSection(section: CenterSectionKey) {
  const paths: Record<CenterSectionKey, string> = {
    chat: '/center/chat',
    agent: '/center/agent',
    knowledge: '/center/knowledge',
    skill: '/center/skill',
    files: '/center/files',
  };
  void router.push(paths[section]);
}

function openCatalogApp(app: any) {
  const target = String(app?.pcUrl || '').trim();
  if (!target) {
    showError('该应用未配置访问地址');
    return;
  }
  const opened = openAgentRunWindow(target);
  if (!opened) showError('无法打开运行页，请允许浏览器弹出窗口');
}

provide(CenterContextKey, {
  agentMarket,
  centerChat,
  activeSection,
  showError,
  showNotice,
  switchSection,
  startChatWithAgent: openCatalogApp,
  openAgentFromChat: openCatalogApp,
});
provide('centerOpenMemory', () => {});

const sessions = computed<RunSession[]>(() => centerChat.threadList.value.map((thread) => ({
  id: thread.id,
  appId: appMeta.value?.id || props.preset,
  aiAppType: 'chatAgent',
  title: thread.title || '新对话',
  pinned: Boolean(thread.pinned),
  origin: props.preset,
  createTime: thread.created_at,
  updateTime: thread.updated_at,
})));

const runningSessionIds = computed(() => Object.entries(centerChat.activeRuns.value)
  .filter(([, run]) => ['queued', 'running', 'starting', 'cancelling'].includes(String(run?.status || '')))
  .map(([threadId]) => threadId));

const brandInitial = computed(() => String(appMeta.value?.appName || '系统').slice(0, 1));
const accessErrorCode = computed(() => accessStatus.value === 403 ? '403' : '—');
const accessErrorTitle = computed(() => accessStatus.value === 403
  ? '暂无权限使用该应用'
  : '系统应用暂时不可用');
const pageStyle = computed(() => {
  const compact = viewportWidth.value <= 1024;
  const navWidth = compact ? 0 : (sidebarCollapsed.value ? 64 : 286);
  return {
    '--run-sidebar-width': '286px',
    '--center-nav-width': `${navWidth}px`,
  };
});

function toggleSidebar() {
  const opening = sidebarCollapsed.value;
  if (opening && viewportWidth.value <= 1024) {
    sidebarReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }
  sidebarCollapsed.value = !sidebarCollapsed.value;
  if (!opening && viewportWidth.value <= 1024) {
    void nextTick(() => sidebarReturnFocus?.focus({ preventScroll: true }));
  }
}

function onHistoryKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && compactHistoryOpen.value) {
    event.preventDefault();
    toggleSidebar();
  }
}

async function newConversation() {
  if (viewportWidth.value <= 1024) sidebarCollapsed.value = true;
  centerChat.resetChat();
  void router.replace({ path: route.path });
  await nextTick();
  mainRef.value?.scrollTo({ top: 0 });
}

async function selectConversation(threadId: string) {
  // 手机/iPad：先关抽屉，让 0.24s 滑出和会话加载叠在一起。
  if (viewportWidth.value <= 1024) sidebarCollapsed.value = true;
  const loaded = await centerChat.loadThread(threadId);
  if (!loaded) return;
  await router.replace({ path: route.path, query: { thread: threadId } });
}

function confirmRemoveConversation(threadId: string) {
  const isCurrent = centerChat.currentThreadId.value === threadId;
  Modal.confirm({
    title: '删除对话',
    content: '删除后无法恢复，确定继续吗？',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      await centerChat.handleDeleteThread(threadId);
      if (isCurrent) await router.replace({ path: route.path });
    },
  });
}

function goToMarketplace() {
  void router.push('/center/agent');
}

function onViewportResize() {
  const wasCompact = viewportWidth.value <= 1024;
  viewportWidth.value = window.innerWidth;
  if (!wasCompact && viewportWidth.value <= 1024) sidebarCollapsed.value = true;
}

onMounted(async () => {
  window.addEventListener('resize', onViewportResize);
  window.addEventListener('keydown', onHistoryKeydown);
  try {
    appMeta.value = await getBuiltinApp(props.preset);
    iconLoadFailed.value = false;
    await centerChat.restoreCurrentThread();
    const requestedThread = Array.isArray(route.query.thread) ? route.query.thread[0] : route.query.thread;
    if (typeof requestedThread === 'string' && requestedThread.trim()) {
      await centerChat.loadThread(requestedThread.trim());
    }
  } catch (error) {
    accessStatus.value = Number((error as { status?: unknown } | null)?.status || 0);
    accessError.value = errorMessage(error);
  } finally {
    accessLoading.value = false;
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', onViewportResize);
  window.removeEventListener('keydown', onHistoryKeydown);
});
</script>

<style lang="less" src="../../agent/run/agent-run-shell.less"></style>
<style lang="less">
/* 主页面也使用这份全局样式；不能让两个 style src 共用 Vue 插件的描述符。 */
@import '../styles/centerNew.less';
</style>

<style scoped lang="less">
.builtin-harness-page {
  overflow: hidden;
}

.builtin-harness-page .run-brand-avatar {
  border-radius: 9px;
  background: #e9eaed;
  color: #34373d;
  font-size: 13px;
  font-weight: 700;
}

.builtin-harness-page .run-brand-avatar > span {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
}

.builtin-harness-page .run-brand-avatar img {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.load-more-sessions {
  flex: none;
  min-height: 34px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--run-left-muted);
  cursor: pointer;
}

.load-more-sessions:hover:not(:disabled) {
  background: var(--run-left-item-hover-bg);
  color: var(--run-left-item-title);
}

.load-more-sessions:disabled {
  cursor: wait;
  opacity: 0.65;
}

.sidebar-collapsed .load-more-sessions {
  display: none;
}

@media (min-width: 1025px) {
  // This page imports the shell globally, so its shared :deep() hiding rule does not apply.
  .builtin-harness-page.sidebar-collapsed .new-session-label,
  .builtin-harness-page.sidebar-collapsed .session-search,
  .builtin-harness-page.sidebar-collapsed :deep(.session-list),
  .builtin-harness-page.sidebar-collapsed :deep(.session-empty) {
    display: none;
  }
}

.builtin-harness-main {
  overflow: auto;
  isolation: isolate;
}

.builtin-harness-main :deep(.chat-home) {
  flex: none;
  min-height: 100%;
}

.builtin-access-state {
  display: flex;
  width: min(520px, calc(100% - 40px));
  min-height: 100%;
  margin: auto;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 14px;
  color: #747780;
  text-align: center;
}

.builtin-access-state p {
  max-width: 440px;
  margin: 0;
  line-height: 1.7;
}

.builtin-access-state h1 {
  margin: 0;
  color: #202124;
  font-size: 25px;
}

.access-error-code {
  color: #d1d3d8;
  font-size: 54px;
  font-weight: 780;
  letter-spacing: -0.04em;
}

@media (max-width: 1024px) {
  .builtin-harness-main {
    min-height: 0;
  }
}
</style>
