<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <section
    class="agent-run-page is-fullpage"
    :class="{
      'is-splitting': splitting,
      'sidebar-collapsed': sidebarCollapsed,
      'has-custom-presentation': runPresentation.key !== 'default',
    }"
    :data-presentation-preset="runPresentation.key"
    :style="runPageStyle"
  >
    <RunCompactHeader
      :title="appMeta?.name || '智能体运行'"
      :sidebar-collapsed="sidebarCollapsed"
      :inspiration-collapsed="inspirationCollapsed"
      show-inspiration
      @toggle-sidebar="toggleSidebar"
      @toggle-inspiration="toggleInspiration"
    />
    <Transition name="run-history-backdrop">
      <button
        v-if="!sidebarCollapsed"
        type="button"
        class="run-mobile-sidebar-backdrop"
        aria-label="收起会话侧栏"
        @click="toggleSidebar"
      ></button>
    </Transition>
    <aside class="agent-run-sidebar">
      <span v-if="runPresentation.sidebarDecoration" class="run-presentation-rail-decoration" aria-hidden="true">
        <component
          :is="runPresentation.sidebarDecoration"
          v-bind="runPresentation.componentProps || {}"
          region="sidebar"
        />
      </span>
      <div class="run-brand">
        <span class="run-brand-avatar" aria-hidden="true">
          <span>{{ brandInitials }}</span>
          <img
            v-if="brandIconUrl"
            :src="brandIconUrl"
            :alt="appMeta?.name || '智能体'"
            @error="recoverAgentIcon($event, appMeta)"
          />
        </span>
        <span class="run-brand-copy">
          <strong>{{ appMeta?.name || '智能体运行' }}</strong>
          <!-- <span v-if="appTypeLabel" class="run-brand-type">{{ appTypeLabel }}</span> -->
        </span>
        <RunSidebarToggle :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />
      </div>

      <button class="new-session-btn" type="button" :disabled="!runnable" @click="onNewConversation">
        <svg class="compose-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
          <path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" />
        </svg>
        <span class="new-session-label">新对话</span>
      </button>

      <div v-if="!isPreview" class="session-search">
        <SearchOutlined />
        <input v-model="sessionKeyword" placeholder="搜索会话" @keyup.enter="loadSessions" />
      </div>

      <RunSessionList
        v-if="!isPreview"
        :sessions="sessions"
        :active-session-id="activeSessionId"
        :running-session-ids="runningSessionIds"
        @select="onPickSession"
        @pin="pinSession"
        @rename="renameSession"
        @remove="removeSession"
      />
      <div v-else class="session-empty">审核预览不会保存会话</div>
    </aside>
    <div
      class="run-splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="调整侧栏宽度"
      @mousedown="onSplitterDown"
    ></div>

    <main class="agent-run-main">
      <component
        :is="runPresentation.backdrop"
        v-if="runPresentation.backdrop"
        v-bind="runPresentation.componentProps || {}"
        :empty-state="isEmptyState"
      />
      <div ref="listRef" :class="['run-messages', { 'is-empty-state': isEmptyState }]" @scroll.passive="updateScrollState">
        <div v-if="appLoading" class="run-empty">正在加载应用...</div>
        <div v-else-if="!runnable" class="run-tip">
          <a-alert type="warning" show-icon :message="notRunnableReason" />
        </div>
        <div v-else-if="!messages.length" class="run-start">
          <article class="run-welcome">
            <p class="run-welcome-kicker">{{ appMeta?.name || '智能体' }}</p>
            <h1>{{ runPresentation.welcomeTitle }}</h1>
            <!-- eslint-disable-next-line vue/no-v-html --><!-- 开场白经 xss(md.render()) 白名单过滤 -->
            <div class="markdown-body run-welcome-body" v-html="renderMarkdown(welcomeText)"></div>
          </article>
        </div>

        <article
          v-for="(m, i) in messages"
          :key="m.id || i"
          :class="['run-msg', m.role, { failed: m.failed, 'has-charts': m.chartOutputs?.length, 'from-work-agent': m.senderType === 'work_agent' }]"
        >
          <div class="run-msg-col">
            <RunMessageSenderBadge v-if="m.role === 'user' && m.senderType === 'work_agent'" />
            <div v-if="m.role === 'user' && editingIndex !== i && m.attachments?.length" class="message-attachments">
              <AttachmentCard
                v-for="(file, fileIndex) in m.attachments"
                :key="`${file.filename}-${file.fileId || file.previewUrl || fileIndex}`"
                :attachment="file"
                large
                :uniform="imageAttachmentCount(m.attachments) > 1"
                @preview="lightboxSrc = $event"
              />
            </div>
            <RunUserMessageEdit
              v-if="m.role === 'user' && editingIndex === i"
              v-model="editingText"
              @cancel="cancelEdit"
              @save="saveEdit"
            />
            <div
              v-else-if="m.role === 'assistant' || m.content || m.pending"
              class="run-bubble"
            >
              <RunAssistantMarkdown v-if="m.role === 'assistant' && m.content" :content="m.content" />
              <p v-else-if="m.content" class="run-text">{{ m.content }}</p>
              <div v-if="m.role === 'assistant' && m.attachments?.length" class="message-attachments">
                <AttachmentCard
                  v-for="(file, fileIndex) in m.attachments"
                  :key="`${file.filename}-${file.fileId || file.previewUrl || fileIndex}`"
                  :attachment="file"
                  large
                  :uniform="imageAttachmentCount(m.attachments) > 1"
                  @preview="lightboxSrc = $event"
                />
              </div>
              <div v-if="m.chartOutputs?.length" class="message-chart-list">
                <EChartsOutputPreview
                  v-for="(chartOutput, chartIndex) in m.chartOutputs"
                  :key="chartIndex"
                  :output="chartOutput"
                />
              </div>
              <RunGeneratedFiles v-if="m.role === 'assistant' && !m.pending && m.generatedFiles?.length" :files="m.generatedFiles" />
              <p v-else-if="m.pending" class="run-generating">
                正在思考
              </p>
            </div>
            <RunMessageActions
              v-if="m.content && !m.pending && editingIndex !== i"
              :content="m.content"
              :role="m.role"
              :message-id="m.id"
              :feedback="m.feedback"
              :editable="m.role === 'user' && !running"
              @edit="startEdit(i)"
              @feedback="updateMessageFeedback"
            />
          </div>
        </article>

        <!-- HITL 交互卡片 -->
        <div v-if="interactive" class="run-hitl">
          <p class="hitl-desc">{{ interactive.params?.description || '请补充信息以继续' }}</p>
          <div v-if="interactive.type === 'userSelect'" class="hitl-options">
            <a-button
              v-for="opt in interactive.params?.userSelectOptions || []"
              :key="opt.key"
              size="small"
              :disabled="running"
              @click="submitInteractive(opt.value)"
            >
              {{ opt.value }}
            </a-button>
          </div>
          <!-- formInput 完整字段渲染与主对话 HITL 卡共用同一组件，杜绝字段类型支持漂移 -->
          <InteractiveFormFields
            v-else
            :fields="interactiveFormItems"
            :disabled="running"
            id-prefix="agent-run-form"
            @submit="(v) => submitInteractive(null, v)"
          />
        </div>

      </div>

      <footer :class="['run-composer', { 'is-empty-state': isEmptyState }]">
        <RunScrollToBottom v-if="messages.length && !atBottom" @click="scrollToBottom(true)" />
        <div class="composer-body">
          <RunTaskParameters
            v-if="runtimeVariableItems.length"
            :variables="runtimeVariableItems"
            :model-value="runtimeVariableValues"
            :disabled="running || !!interactive"
            @update:model-value="replaceRuntimeVariables"
          />
          <div
            :class="['composer-input-wrap', { 'drag-active': composerDragActive }]"
            @dragover.prevent="onComposerDragOver"
            @dragleave="onComposerDragLeave"
            @drop.prevent="onComposerDrop"
          >
            <component
              :is="runPresentation.composerDecoration"
              v-if="runPresentation.composerDecoration"
              v-bind="runPresentation.componentProps || {}"
              :empty-state="isEmptyState"
            />
            <div v-if="uploadedFiles.length" class="file-chip-list">
              <AttachmentCard
                v-for="file in uploadedFiles"
                :key="file.uploadId"
                :attachment="{ filename: file.name, kind: file.kind, previewUrl: file.previewUrl, uploading: file.uploading, status: file.status, note: file.note }"
                removable
                @remove="removeUploadedFile(file.uploadId)"
                @preview="lightboxSrc = $event"
              />
            </div>
            <textarea
              ref="composerInputRef"
              v-model="input"
              rows="1"
              :disabled="!runnable || !!interactive"
              :placeholder="runPresentation.composerPlaceholder"
              aria-label="输入消息"
              @keydown="onKeydown"
              @paste="onComposerPaste"
            ></textarea>
            <div class="composer-toolbar">
              <div v-if="fileUploadEnabled" class="composer-input-actions">
                <button
                  type="button"
                  class="attach-btn"
                  title="添加照片和文件"
                  aria-label="添加照片和文件"
                  :disabled="uploading || running || !runnable || !!interactive"
                  @click="openFilePicker"
                >
                  <PlusOutlined />
                </button>
              </div>
              <button
                v-if="running"
                type="button"
                class="send-btn stop"
                title="停止"
                @click="stop"
              >
                <span class="stop-square" aria-hidden="true"></span>
              </button>
              <button
                v-else
                type="button"
                class="send-btn"
                title="发送"
                :disabled="!runnable || (!input.trim() && !uploadedFiles.length) || !!interactive || uploading"
                @click="send"
              >
                <ArrowUpOutlined />
              </button>
            </div>
            <input
              v-if="fileUploadEnabled"
              ref="fileInputRef"
              class="file-input"
              type="file"
              :multiple="maxUploadFiles !== 1"
              :accept="acceptedFileTypes"
              @change="handleFileChange"
            />
          </div>
        </div>
        <AgentOutputDisclaimer v-if="messages.length" />
      </footer>
    </main>
    <RunInspirationPanel
      :collapsed="inspirationCollapsed"
      :width="inspirationWidth"
      :scenes="inspirationScenes"
      :decoration="runPresentation.inspirationDecoration"
      :decoration-props="runPresentation.componentProps"
      @toggle="toggleInspiration"
      @resize-start="onInspirationResizeStart"
      @select="fillSuggestedTask"
    />
    <ImageLightbox :src="lightboxSrc" @close="lightboxSrc = null" />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { message } from 'ant-design-vue';
import {
  ArrowUpOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import MarkdownIt from 'markdown-it';
import xss, { getDefaultWhiteList } from 'xss';
import AttachmentCard from '../../peopleCenter/components/AttachmentCard.vue';
import ImageLightbox from '../../peopleCenter/components/ImageLightbox.vue';
import InteractiveFormFields from '../../peopleCenter/components/InteractiveFormFields.vue';
import { stopProtocolLinkAtCjkPunctuation } from '../../peopleCenter/utils/markdownLinkify';
import EChartsOutputPreview from '../../workflow/editor/components/EChartsOutputPreview.vue';
import { VariableInputEnum } from '../../workflow/core/constants';
import { extractChartOutputs } from '../../workflow/shared/chartOutput';
import type { WorkflowChartOutput } from '../../workflow/shared/chartOutput';
import {
  getRunApp,
  getRunSessions,
  createRunSession,
  deleteRunSession,
  pinRunSession,
  renameRunSession,
  getRunMessages,
  appendRunMessage,
  resumeRunDefinition,
  submitRunMessageFeedback,
  type RunAppMeta,
  type RunMessageStatus,
  type RunMessageAttachment,
  type RunSession,
} from './agentRun.api';
import RunTaskParameters from './components/RunTaskParameters.vue';
import { runAgentStream, type RunInteractive, type RunResult } from './agentRunStream';
import { speakBrowserTts, stopBrowserTts } from '../../workflow/shared/browserTts';
import { resolveRunInspirationScenes, resolveRunWelcomeText } from './agentRunPresentation';
import { resolveRunPresentation } from './presentation/registry';
import {
  hydratePortableRunSkin,
  isPortableRunSkin,
  releaseHydratedPortableRunSkin,
  usePortableRunSkinDevice,
  type HydratedPortableRunSkin,
} from './presentation/portable';
import { uploadChatFile, type GeneratedFile } from '../../peopleCenter/agentApi';
import { filesFromClipboard, longTextAsPastedFile } from '../../peopleCenter/utils/composerClipboard';
import { createSmoothStreamText } from '../../peopleCenter/composables/smoothStreamText';
import RunAssistantMarkdown from './components/RunAssistantMarkdown.vue';
import RunCompactHeader from './components/RunCompactHeader.vue';
import RunGeneratedFiles from './components/RunGeneratedFiles.vue';
import RunInspirationPanel from './components/RunInspirationPanel.vue';
import RunMessageActions from './components/RunMessageActions.vue';
import RunMessageSenderBadge from './components/RunMessageSenderBadge.vue';
import RunScrollToBottom from './components/RunScrollToBottom.vue';
import RunSidebarToggle from './components/RunSidebarToggle.vue';
import RunSessionList from './components/RunSessionList.vue';
import RunUserMessageEdit from './components/RunUserMessageEdit.vue';
import { useSidebarResize } from './useSidebarResize';
import { createSessionLiveRuns } from './sessionLiveRuns';
import { getAgentFallbackIcon, getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../../peopleCenter/agentIcon';
import AgentOutputDisclaimer from '../../peopleCenter/components/AgentOutputDisclaimer.vue';

defineOptions({ name: 'AgentRunPage' });

const { sidebarWidth, sidebarCollapsed, splitting, onSplitterDown, toggleSidebar } = useSidebarResize();
const {
  sidebarWidth: inspirationWidth,
  sidebarCollapsed: inspirationCollapsed,
  onSplitterDown: onInspirationResizeStart,
  toggleSidebar: toggleInspiration,
} = useSidebarResize('agent-run:inspiration-width', 'left');
const RUN_COMPACT_SHELL_QUERY = '(max-width: 1024px)';
let runMobileQuery: MediaQueryList | null = null;
const runShellStartsCompact = typeof window !== 'undefined' && window.matchMedia(RUN_COMPACT_SHELL_QUERY).matches;
const isCompactRun = ref(runShellStartsCompact);

if (runShellStartsCompact) {
  sidebarCollapsed.value = true;
  inspirationCollapsed.value = true;
}

function syncRunMobileShell(event: MediaQueryListEvent | MediaQueryList) {
  isCompactRun.value = event.matches;
  if (!event.matches) return;
  sidebarCollapsed.value = true;
  inspirationCollapsed.value = true;
}

type MessageAttachment = {
  filename: string;
  kind?: string;
  status?: string;
  note?: string;
  fileId?: string;
  previewUrl?: string;
};
type UiMessage = {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  turnId?: string | null;
  status?: RunMessageStatus;
  feedback?: 'up' | 'down' | null;
  senderType?: 'human' | 'work_agent' | null;
  pending?: boolean;
  failed?: boolean;
  attachments?: MessageAttachment[];
  chartOutputs?: WorkflowChartOutput[];
  generatedFiles?: GeneratedFile[];
};
type UploadedFile = {
  uploadId: string;
  name: string;
  fileId?: string;
  kind?: string;
  uploading?: boolean;
  status?: string;
  note?: string;
  previewUrl?: string;
};

const route = useRoute();
const appId = String(route.params.appId || route.query.appId || '');

function firstQueryValue(value: unknown) {
  return Array.isArray(value) ? value[0] : value;
}

function queryText(value: unknown) {
  const raw = firstQueryValue(value);
  return raw == null ? '' : String(raw);
}

function truthyQueryFlag(value: unknown) {
  const raw = firstQueryValue(value);
  if (typeof raw === 'boolean') return raw;
  if (typeof raw === 'number') return raw === 1;
  return ['1', 'true', 'yes'].includes(String(raw || '').trim().toLowerCase());
}

const previewVersionId = queryText(route.query.previewVersionId);
const previewDraft = truthyQueryFlag(route.query.previewDraft);

const appMeta = ref<RunAppMeta | null>(null);
const hydratedPortableSkin = ref<HydratedPortableRunSkin | null>(null);
const runSkinDevice = usePortableRunSkinDevice();
const appLoading = ref(true);
const loadError = ref('');
const sessions = ref<RunSession[]>([]);
const activeSessionId = ref('');
const sessionKeyword = ref('');
const messages = ref<UiMessage[]>([]);
const input = ref('');
const uploading = ref(false);
const runningNodeLabel = ref('');
const interactive = ref<RunInteractive | null>(null);
const live = createSessionLiveRuns<UiMessage, RunInteractive>(activeSessionId);
const running = live.running;
const runningSessionIds = live.runningIds;
const runtimeVariableValues = reactive<Record<string, any>>({});
const uploadedFiles = ref<UploadedFile[]>([]);
let uploadSequence = 0;
const composerDragActive = ref(false);
const fileInputRef = ref<HTMLInputElement>();
const listRef = ref<HTMLElement>();
const composerInputRef = ref<HTMLTextAreaElement | null>(null);
const editingIndex = ref<number | null>(null);
const editingText = ref('');
const COMPOSER_INPUT_MAX_HEIGHT = 220;
const PREVIEW_SESSION_ID = '__review_preview__';

function autoResizeComposerInput() {
  const el = composerInputRef.value;
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, COMPOSER_INPUT_MAX_HEIGHT)}px`;
  el.style.overflowY = el.scrollHeight > COMPOSER_INPUT_MAX_HEIGHT ? 'auto' : 'hidden';
}

watch(input, () => nextTick(autoResizeComposerInput));
const lightboxSrc = ref<string | null>(null);
const atBottom = ref(true);
const stickToBottom = ref(true);

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
stopProtocolLinkAtCjkPunctuation(md);
const markdownWhiteList = {
  ...getDefaultWhiteList(),
  a: [...getDefaultWhiteList().a, 'rel'],
};

function createUiMessage(message: UiMessage) {
  return reactive<UiMessage>(message);
}

function createTurnId() {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `turn-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function normalizeRunStatus(status: string | undefined): Exclude<RunMessageStatus, 'unknown'> {
  if (status === 'partial' || status === 'failed' || status === 'cancelled' || status === 'interrupted') return status;
  return 'completed';
}

const isReviewPreview = computed(() => !!previewVersionId && !!appMeta.value?.previewMode);
const isDraftPreview = computed(() => previewDraft && !!appMeta.value?.previewMode);
// 统一预览概念：审核预览与草稿预览在 UI 行为上完全一致（不落库、隐藏会话列表、占位 sessionId），
// 差别仅在数据来源（version 快照 vs draft），由后端按 previewVersionId / previewDraft 区分
const isPreview = computed(() => isReviewPreview.value || isDraftPreview.value);
const runnable = computed(() => !!appMeta.value && (appMeta.value.status === 'published' || isPreview.value));
const isEmptyState = computed(() => !appLoading.value && runnable.value && messages.value.length === 0);
const runtimePresentationConfig = computed(() => {
  const config = appMeta.value?.presentation;
  if (!config) return config;
  if (hydratedPortableSkin.value) return { ...config, portableSkin: hydratedPortableSkin.value };
  // An unhydrated server manifest must never hand its authenticated endpoint URL to an <img>.
  return config.portableSkin ? { ...config, portableSkin: undefined } : config;
});
const runPresentation = computed(() => resolveRunPresentation(runtimePresentationConfig.value, runSkinDevice.value));
const runPageStyle = computed(() => ({
  '--run-sidebar-width': `${sidebarWidth.value}px`,
  ...runPresentation.value.styleVars,
}));
const notRunnableReason = computed(() => {
  if (loadError.value) return loadError.value;
  if (isPreview.value) return '当前版本无法预览，请确认草稿已保存或后端服务已更新。';
  if (appMeta.value && appMeta.value.status !== 'published') return '该应用尚未发布，无法运行。';
  return '应用不可用。';
});
const welcomeText = computed(() => resolveRunWelcomeText(appMeta.value?.name || '', appMeta.value?.welcomeText));
const inspirationScenes = computed(() => resolveRunInspirationScenes({
  inspirationScenes: appMeta.value?.inspirationScenes,
  quickQuestions: appMeta.value?.quickQuestions,
}));
const runtimeVariableItems = computed(() => (appMeta.value?.variables || []).filter((item) => item?.key));
/** 后端新版使用 params.inputForm，旧版预览仍可能返回 userInputForms。 */
const interactiveFormItems = computed<any[]>(() => {
  const params = interactive.value?.params as any;
  return params?.inputForm || params?.userInputForms || [];
});
const fileConfig = computed(() => appMeta.value?.fileSelectConfig || {});
const fileUploadEnabled = computed(() => {
  const config = fileConfig.value;
  return !!(
    config.canSelectFile ||
    config.canSelectImg ||
    config.canSelectVideo ||
    config.canSelectAudio ||
    config.canSelectCustomFileExtension
  );
});
const maxUploadFiles = computed(() => {
  const value = Number(fileConfig.value.maxFiles || 10);
  return Number.isFinite(value) && value > 0 ? value : 10;
});
const acceptedFileTypes = computed(() => {
  const config = fileConfig.value;
  const accepts: string[] = [];
  if (config.canSelectImg) accepts.push('image/*');
  if (config.canSelectVideo) accepts.push('video/*');
  if (config.canSelectAudio) accepts.push('audio/*');
  if (config.canSelectFile) accepts.push('.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json');
  if (config.canSelectCustomFileExtension) {
    accepts.push(
      ...(config.customFileExtensionList || [])
        .map((item) => String(item || '').trim().replace(/^\./, ''))
        .filter(Boolean)
        .map((item) => `.${item}`),
    );
  }
  return accepts.join(',');
});
const appTypeLabel = computed(() =>
  ({ chatAgent: '对话 Agent', workflow: '工作流', workflowTool: '工作流工具' }[appMeta.value?.aiAppType || ''] || appMeta.value?.aiAppType || ''),
);
const brandIconUrl = computed(() => getAgentIconUrl(appMeta.value) || getAgentFallbackIcon(appMeta.value));
const brandInitials = computed(() => getAgentInitials(appMeta.value));

function attachmentKindFromFilename(filename: string) {
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  if (['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'].includes(ext)) return 'image';
  if (['doc', 'docx'].includes(ext)) return 'docx';
  if (['ppt', 'pptx'].includes(ext)) return 'pptx';
  if (ext === 'pdf') return 'pdf';
  return 'text';
}

function isImageAttachment(attachment: { filename?: string; kind?: string }) {
  return attachment.kind === 'image' || attachmentKindFromFilename(attachment.filename || '') === 'image';
}

function imageAttachmentCount(attachments?: Array<{ filename?: string; kind?: string }>) {
  return (attachments || []).filter(isImageAttachment).length;
}

function toMessageAttachment(file: UploadedFile): MessageAttachment {
  return {
    filename: file.name,
    kind: file.kind || attachmentKindFromFilename(file.name),
    status: file.status || 'ok',
    note: file.note,
    fileId: file.fileId,
    previewUrl: file.previewUrl,
  };
}

function fromRunAttachment(attachment: RunMessageAttachment): MessageAttachment {
  return {
    filename: attachment.filename,
    kind: attachment.kind,
    status: attachment.status,
    note: attachment.note,
    fileId: attachment.file_id,
    previewUrl: attachment.preview_url,
  };
}

function toPersistedAttachment(attachment: MessageAttachment): RunMessageAttachment {
  return {
    filename: attachment.filename,
    kind: attachment.kind,
    status: attachment.status,
    note: attachment.note,
    file_id: attachment.fileId,
    preview_url: attachment.previewUrl,
  };
}

function renderMarkdown(text: string) {
  return xss(md.render(text || ''), { whiteList: markdownWhiteList });
}

function initialVariableValue(variable: { type?: string; defaultValue?: any }) {
  if (variable.defaultValue !== undefined) return variable.defaultValue;
  if (variable.type === VariableInputEnum.switch) return false;
  if (variable.type === VariableInputEnum.multipleSelect || variable.type === VariableInputEnum.timeRangeSelect) return [];
  return undefined;
}

function replaceRuntimeVariables(value: Record<string, any>) {
  Object.keys(runtimeVariableValues).forEach((key) => delete runtimeVariableValues[key]);
  Object.assign(runtimeVariableValues, value);
}

function syncRuntimeVariableValues() {
  const keys = new Set(runtimeVariableItems.value.map((item) => item.key));
  Object.keys(runtimeVariableValues).forEach((key) => {
    if (!keys.has(key)) delete runtimeVariableValues[key];
  });
  runtimeVariableItems.value.forEach((variable) => {
    runtimeVariableValues[variable.key] = initialVariableValue(variable);
  });
}

function runtimeStorageKey(sessionId = activeSessionId.value) {
  return `agent-run:variables:${appId}:${sessionId || 'new'}`;
}

function serializableRuntimeVariables() {
  return Object.fromEntries(runtimeVariableItems.value
    .filter((variable) => variable.type !== VariableInputEnum.password)
    .map((variable) => [variable.key, runtimeVariableValues[variable.key]]));
}

function restoreRuntimeVariables() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(runtimeStorageKey()) || '{}');
    runtimeVariableItems.value.forEach((variable) => {
      if (variable.type !== VariableInputEnum.password && Object.prototype.hasOwnProperty.call(saved, variable.key)) {
        runtimeVariableValues[variable.key] = saved[variable.key];
      }
    });
  } catch {
    // 会话缓存损坏不应阻塞运行页。
  }
}

watch(runtimeVariableValues, () => {
  if (!appMeta.value) return;
  try { sessionStorage.setItem(runtimeStorageKey(), JSON.stringify(serializableRuntimeVariables())); } catch { /* 可选缓存 */ }
}, { deep: true });

function isMissingVariableValue(value: any) {
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || (typeof value === 'string' && !value.trim());
}

function collectRuntimeVariables() {
  const missing = runtimeVariableItems.value.find(
    (variable) => variable.required && isMissingVariableValue(runtimeVariableValues[variable.key]),
  );
  if (missing) {
    message.warning(`请填写全局变量：${missing.label || missing.key}`);
    return null;
  }
  return Object.fromEntries(runtimeVariableItems.value.map((variable) => [variable.key, runtimeVariableValues[variable.key]]));
}

function clearComposerInput(sentText: string) {
  input.value = '';
  // Ant Textarea/输入法可能在当前 keydown 结束后再发一次 update:value，把发送前文本写回来。
  // 只清理与本次已发送文本完全一致的回写，不会误删用户已经开始输入的下一条消息。
  window.setTimeout(() => {
    if (input.value.trim() === sentText) input.value = '';
  }, 0);
}

function updateScrollState() {
  const el = listRef.value;
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (distance < 40) stickToBottom.value = true;
  else if (distance > 160) stickToBottom.value = false;
  atBottom.value = distance < 120;
}

function scrollToBottom(force = false) {
  nextTick(() => {
    const el = listRef.value;
    if (!el || (!force && !stickToBottom.value)) return;
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo({ top: el.scrollHeight, behavior: force && !reduceMotion ? 'smooth' : 'auto' });
    if (force) stickToBottom.value = true;
    atBottom.value = true;
  });
}

function startEdit(index: number) {
  const target = messages.value[index];
  if (!target || target.role !== 'user' || running.value) return;
  editingIndex.value = index;
  editingText.value = target.content || '';
}

function cancelEdit() {
  editingIndex.value = null;
  editingText.value = '';
}

async function saveEdit() {
  const index = editingIndex.value;
  const text = editingText.value.trim();
  if (index == null || !text || running.value) return;
  editingIndex.value = null;
  editingText.value = '';
  await send({ text, replaceFromIndex: index });
}

async function loadApp() {
  appLoading.value = true;
  try {
    releaseHydratedPortableRunSkin(hydratedPortableSkin.value);
    hydratedPortableSkin.value = null;
    appMeta.value = await getRunApp(appId, previewVersionId || undefined, previewDraft || undefined);
    const portableSkin = appMeta.value?.presentation?.portableSkin;
    if (isPortableRunSkin(portableSkin)) {
      try {
        hydratedPortableSkin.value = await hydratePortableRunSkin(portableSkin);
      } catch {
        message.warning('运行页皮肤素材加载失败，已安全回退为标准外观');
      }
    }
    syncRuntimeVariableValues();
    restoreRuntimeVariables();
    if (!fileUploadEnabled.value) clearUploadedFiles();
  } catch (e: any) {
    loadError.value = e?.response?.data?.detail || '无法加载该应用或没有访问权限。';
  } finally {
    appLoading.value = false;
  }
}

async function loadSessions() {
  if (isPreview.value) {
    sessions.value = [];
    return;
  }
  try {
    sessions.value = await getRunSessions(appId, sessionKeyword.value || undefined);
  } catch {
    sessions.value = [];
  }
}

function onPickSession(id: string) {
  // 手机/iPad：先关抽屉，让 0.24s 滑出和会话加载叠在一起。
  if (isCompactRun.value) sidebarCollapsed.value = true;
  void selectSession(id);
}

function onNewConversation() {
  if (isCompactRun.value) sidebarCollapsed.value = true;
  void newSession();
}

async function selectSession(id: string) {
  cancelEdit();
  const parked = live.get(id);
  activeSessionId.value = id;
  restoreRuntimeVariables();
  clearUploadedFiles();
  if (parked) {
    messages.value = parked.messages;
    runningNodeLabel.value = parked.nodeLabel;
    interactive.value = parked.interactive;
    scrollToBottom(true);
    return;
  }
  runningNodeLabel.value = '';
  interactive.value = null;
  try {
    const rows = await getRunMessages(id);
    messages.value = rows.map((r) => ({
      id: typeof r.id === 'number' ? r.id : Number(r.id) || undefined,
      role: r.role,
      content: r.content,
      turnId: r.turnId,
      status: r.status,
      feedback: r.feedback,
      senderType: r.senderType,
      attachments: r.attachments?.length ? r.attachments.map(fromRunAttachment) : undefined,
      generatedFiles: r.generatedFiles?.filter((file) => file?.id && file?.filename && file.deliverable !== false) || undefined,
    }));
  } catch {
    messages.value = [];
  }
  scrollToBottom(true);
}

async function newSession() {
  cancelEdit();
  live.nextView();
  activeSessionId.value = '';
  restoreRuntimeVariables();
  messages.value = [];
  interactive.value = null;
  runningNodeLabel.value = '';
  input.value = '';
  clearUploadedFiles();
}

async function removeSession(id: string) {
  if (live.get(id)) {
    message.warning('该会话正在运行，请先停止再删除');
    return;
  }
  await deleteRunSession(id);
  if (id === activeSessionId.value) {
    activeSessionId.value = '';
    messages.value = [];
  }
  loadSessions();
}

async function pinSession(id: string) {
  const target = sessions.value.find((session) => session.id === id);
  const next = !target?.pinned;
  await pinRunSession(id, next);
  if (target) target.pinned = next;
  sessions.value = [...sessions.value].sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
}

async function renameSession(id: string, title: string) {
  const saved = await renameRunSession(id, title);
  const nextTitle = String(saved?.title || title).trim();
  const target = sessions.value.find((session) => session.id === id);
  if (target && nextTitle) target.title = nextTitle;
}

async function ensureSession(firstText: string, viewAtStart: number, sessionAtStart: string): Promise<string> {
  if (isPreview.value) {
    // 预览不创建持久化会话，但仍必须把内部占位会话标记为当前会话。
    // 否则 HITL 结果会被写入 live 记录，却因 activeSessionId 仍为空而不显示表单。
    if (live.view.value === viewAtStart && !activeSessionId.value) {
      activeSessionId.value = PREVIEW_SESSION_ID;
    }
    return PREVIEW_SESSION_ID;
  }
  if (sessionAtStart) return sessionAtStart;
  const s = await createRunSession(appId, firstText.slice(0, 30) || '新对话');
  try { sessionStorage.setItem(runtimeStorageKey(s.id), JSON.stringify(serializableRuntimeVariables())); } catch { /* 可选缓存 */ }
  sessions.value.unshift(s);
  if (live.view.value === viewAtStart && !activeSessionId.value) {
    activeSessionId.value = s.id;
  }
  return s.id;
}

function openFilePicker() {
  if (!canAcceptComposerFiles()) return;
  fileInputRef.value?.click();
}

function canAcceptComposerFiles() {
  return fileUploadEnabled.value && runnable.value && !interactive.value && !running.value && !uploading.value;
}

function rejectComposerFiles(reason?: string) {
  message.warning(reason || (fileUploadEnabled.value ? '当前不能添加附件' : '当前智能体未开启附件上传'));
}

function onComposerDragOver(e: DragEvent) {
  if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
  e.preventDefault();
  composerDragActive.value = true;
}

function onComposerDragLeave() {
  composerDragActive.value = false;
}

function onComposerDrop(e: DragEvent) {
  composerDragActive.value = false;
  const files = Array.from(e.dataTransfer?.files || []);
  if (!files.length) return;
  if (!canAcceptComposerFiles()) {
    rejectComposerFiles();
    return;
  }
  void uploadLocalFiles(files);
}

function onComposerPaste(e: ClipboardEvent) {
  const files = filesFromClipboard(e.clipboardData);
  if (files.length) {
    e.preventDefault();
    if (!canAcceptComposerFiles()) {
      rejectComposerFiles();
      return;
    }
    void uploadLocalFiles(files);
    return;
  }
  if (!canAcceptComposerFiles()) return;
  const asFile = longTextAsPastedFile(e.clipboardData?.getData('text/plain') || '');
  if (!asFile) return;
  e.preventDefault();
  void uploadLocalFiles([asFile]);
}

function readImagePreview(file: File): Promise<string | undefined> {
  if (!file.type.startsWith('image/')) return Promise.resolve(undefined);
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : undefined);
    reader.onerror = () => resolve(undefined);
    reader.readAsDataURL(file);
  });
}

function makeImageThumbnail(dataUrl: string): Promise<string | undefined> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      try {
        const scale = Math.min(1, 640 / Math.max(image.naturalWidth || 1, image.naturalHeight || 1));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round((image.naturalWidth || 1) * scale));
        canvas.height = Math.max(1, Math.round((image.naturalHeight || 1) * scale));
        const context = canvas.getContext('2d');
        if (!context) return resolve(undefined);
        context.fillStyle = '#fff';
        context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const thumb = canvas.toDataURL('image/jpeg', 0.8);
        resolve(thumb.length <= 200_000 ? thumb : undefined);
      } catch {
        resolve(undefined);
      }
    };
    image.onerror = () => resolve(undefined);
    image.src = dataUrl;
  });
}

async function handleFileChange(event: Event) {
  const inputEl = event.target as HTMLInputElement;
  const files = Array.from(inputEl.files || []);
  inputEl.value = '';
  await uploadLocalFiles(files);
}

async function uploadLocalFiles(files: File[]) {
  if (!files.length || !fileUploadEnabled.value) return;
  const remaining = maxUploadFiles.value - uploadedFiles.value.length;
  if (remaining <= 0) {
    message.warning(`最多上传 ${maxUploadFiles.value} 个附件`);
    return;
  }
  const selectedFiles = files.slice(0, remaining);
  if (selectedFiles.length < files.length) {
    message.warning(`最多上传 ${maxUploadFiles.value} 个附件，已忽略超出部分`);
  }
  uploading.value = true;
  let successCount = 0;
  await Promise.all(selectedFiles.map(async (file) => {
    const uploadId = `local-${Date.now()}-${uploadSequence += 1}`;
    const isImage = file.type.startsWith('image/');
    const instantPreviewUrl = isImage ? URL.createObjectURL(file) : undefined;
    const placeholder: UploadedFile = {
      uploadId,
      name: file.name,
      fileId: undefined,
      kind: isImage ? 'image' : attachmentKindFromFilename(file.name),
      uploading: true,
      status: 'ok',
      note: '正在上传…',
      previewUrl: instantPreviewUrl,
    };
    // 先渲染本地缩略图，再在后台并行做上传和持久化预览；用户选图后不再面对空白等待。
    uploadedFiles.value.push(placeholder);
    const previewPromise = readImagePreview(file).then(async (previewUrl) => ({
      previewUrl,
      thumbUrl: previewUrl ? await makeImageThumbnail(previewUrl) : undefined,
    }));
    try {
      // 与主 Agent 共用 file_id 协议；视觉模型的图片上传在服务端直接保存原图，不等 OCR。
      const [uploaded, preview] = await Promise.all([
        uploadChatFile(file, appMeta.value?.chatModel),
        previewPromise,
      ]);
      const fileId = String(uploaded.file_id || '').trim();
      if (!fileId) throw new Error(`${file.name} 上传后未返回文件标识`);
      const current = uploadedFiles.value.find((item) => item.uploadId === uploadId);
      if (!current) return;
      if (instantPreviewUrl) URL.revokeObjectURL(instantPreviewUrl);
      current.fileId = fileId;
      current.kind = uploaded.kind || current.kind;
      current.uploading = false;
      current.status = uploaded.status || 'ok';
      current.note = uploaded.note;
      current.previewUrl = preview.thumbUrl || preview.previewUrl;
      successCount += 1;
    } catch (error: any) {
      if (instantPreviewUrl) URL.revokeObjectURL(instantPreviewUrl);
      uploadedFiles.value = uploadedFiles.value.filter((item) => item.uploadId !== uploadId);
      message.error(error?.message || `${file.name} 上传失败`);
    }
  }));
  uploading.value = false;
  if (successCount) message.success(successCount === 1 ? '附件已上传' : `${successCount} 个附件已上传`);
}

function releasePreview(file: UploadedFile) {
  if (file.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(file.previewUrl);
}

function clearUploadedFiles() {
  uploadedFiles.value.forEach(releasePreview);
  uploadedFiles.value = [];
}

function removeUploadedFile(uploadId: string) {
  const target = uploadedFiles.value.find((file) => file.uploadId === uploadId);
  if (target) releasePreview(target);
  uploadedFiles.value = uploadedFiles.value.filter((file) => file.uploadId !== uploadId);
}

async function updateMessageFeedback(id: number, value: 'up' | 'down' | null) {
  try {
    await submitRunMessageFeedback(id, value);
    const row = messages.value.find((item) => item.id === id);
    if (row) row.feedback = value;
  } catch (error: any) {
    message.error(error?.message || '反馈提交失败');
  }
}

function handleResult(result: RunResult, assistantMsg: UiMessage, sessionId: string) {
  const parked = live.get(sessionId);
  const thread = parked?.messages || messages.value;
  if (parked) parked.nodeLabel = '';
  if (activeSessionId.value === sessionId) runningNodeLabel.value = '';
  if (result.interactive) {
    const idx = thread.indexOf(assistantMsg);
    if (idx >= 0) thread.splice(idx, 1);
    live.setInteractive(sessionId, result.interactive);
    if (activeSessionId.value === sessionId) {
      interactive.value = result.interactive;
      scrollToBottom();
    }
    return;
  }
  const chartOutputs = extractChartOutputs(result);
  const text = resultText(result, chartOutputs);
  if (assistantMsg.content !== text) {
    assistantMsg.content = text;
  }
  assistantMsg.chartOutputs = chartOutputs;
  assistantMsg.generatedFiles = result.files?.filter((file) => file?.id && file?.filename && file.deliverable !== false) || [];
  assistantMsg.pending = false;
  assistantMsg.failed = result.status === 'failed';
  const persistedStatus = normalizeRunStatus(result.status);
  assistantMsg.status = persistedStatus;
  if (!assistantMsg.failed && text && activeSessionId.value === sessionId) {
    speakBrowserTts(text, appMeta.value?.ttsConfig);
  }
  if (!isPreview.value) {
    appendRunMessage(sessionId, 'assistant', text, undefined, {
      runId: result.runId,
      turnId: assistantMsg.turnId || undefined,
      status: persistedStatus,
      generatedFiles: assistantMsg.generatedFiles,
    }).then((saved) => {
      if (saved?.id) assistantMsg.id = saved.id;
    }).catch(() => {});
  }
  if (activeSessionId.value === sessionId) scrollToBottom();
}

function resultText(result: RunResult, chartOutputs = extractChartOutputs(result)) {
  return result.output || result.errorMessage || (chartOutputs.length ? '' : '（无输出）');
}

async function send(override?: { text?: string; replaceFromIndex?: number }) {
  const replacing = typeof override?.replaceFromIndex === 'number';
  const text = (override?.text ?? input.value).trim();
  const viewAtStart = live.view.value;
  const sessionAtStart = activeSessionId.value;
  if ((!text && !uploadedFiles.value.length) || live.isBusy(sessionAtStart, viewAtStart) || !runnable.value) return;
  const runtimeVariables = collectRuntimeVariables();
  if (!runtimeVariables) return;
  const sentFiles = replacing || !fileUploadEnabled.value ? [] : uploadedFiles.value.slice();
  const failedFile = sentFiles.find((file) => file.status === 'failed');
  if (failedFile) {
    message.error(`「${failedFile.name}」解析失败${failedFile.note ? `：${failedFile.note}` : ''}，请移除后重新上传`);
    return;
  }
  stopBrowserTts();
  if (!replacing) clearComposerInput(text);
  live.markStarting(viewAtStart);
  if (activeSessionId.value === sessionAtStart) {
    runningNodeLabel.value = '';
    interactive.value = null;
  }
  const sentAttachments = sentFiles.map(toMessageAttachment);
  const storeText = text || `[附件] ${sentFiles.map((file) => file.name).join('、')}`;
  const thread = messages.value;
  const replaceFrom = override?.replaceFromIndex;
  let truncateFromId: number | undefined;
  let removedTail: UiMessage[] = [];
  if (typeof replaceFrom === 'number' && replaceFrom >= 0 && replaceFrom < thread.length) {
    const from = thread[replaceFrom];
    if (from?.role !== 'user') {
      live.clearStarting(viewAtStart);
      return;
    }
    truncateFromId = from.id;
    removedTail = thread.splice(replaceFrom);
  }

  let sessionId = '';
  try {
    sessionId = await ensureSession(text || sentFiles[0]?.name || '附件', viewAtStart, sessionAtStart);
  } catch (e: any) {
    if (removedTail.length) thread.splice(replaceFrom ?? thread.length, 0, ...removedTail);
    message.error('创建会话失败');
    if (!replacing && !input.value.trim()) input.value = text;
    live.clearStarting(viewAtStart);
    return;
  }
  const turnId = createTurnId();
  const userMsg = createUiMessage({
    role: 'user',
    content: text,
    turnId,
    attachments: sentAttachments.length ? sentAttachments : undefined,
  });
  thread.push(userMsg);
  if (!isPreview.value) {
    appendRunMessage(
      sessionId,
      'user',
      storeText,
      sentAttachments.length ? sentAttachments.map(toPersistedAttachment) : undefined,
      { turnId, ...(truncateFromId ? { truncateFromId } : {}) },
    ).then((r) => {
      if (r?.id) userMsg.id = r.id;
      const s = sessions.value.find((x) => x.id === sessionId);
      if (s && r?.title) s.title = r.title;
    }).catch(() => {});
  }

  const histories = thread
    .filter((m) => m !== userMsg && m.content && !m.pending)
    .map((m) => ({ role: m.role, content: m.content }));
  const userFileIds = sentFiles.map((file) => file.fileId).filter((fileId): fileId is string => !!fileId);
  const assistantMsg = createUiMessage({ role: 'assistant', content: '', turnId, pending: true });
  thread.push(assistantMsg);
  if (!replacing) clearUploadedFiles();
  if (activeSessionId.value === sessionId || (!sessionAtStart && live.view.value === viewAtStart)) {
    scrollToBottom(true);
  }

  const abort = new AbortController();
  live.start(sessionId, {
    abort,
    messages: thread,
    nodeLabel: '',
    view: viewAtStart,
    interactive: null,
  });
  live.clearStarting(viewAtStart);
  let finalResult: RunResult | null = null;
  let streamContent = '';
  const streamText = createSmoothStreamText({
    initialContent: assistantMsg.content,
    commit: (content) => {
      assistantMsg.content = content;
      if (activeSessionId.value === sessionId) scrollToBottom();
    },
  });
  try {
    await runAgentStream(
      {
        appId,
        input: text,
        variables: { ...runtimeVariables, histories, ...(userFileIds.length ? { userFileIds } : {}) },
        previewVersionId: previewVersionId || undefined,
        previewDraft: previewDraft || undefined,
        sessionId: isPreview.value ? undefined : sessionId,
      },
      {
        signal: abort.signal,
        onNode: (n) => {
          const label = n.nodeLabel || n.nodeType || '运行中';
          const parked = live.get(sessionId);
          if (parked) parked.nodeLabel = label;
          if (activeSessionId.value === sessionId) runningNodeLabel.value = label;
        },
        onDelta: (chunk) => {
          if (!chunk) return;
          assistantMsg.pending = false;
          streamContent += chunk;
          streamText.push(streamContent);
        },
        onResult: (r) => {
          finalResult = r;
        },
      },
    );
    if (finalResult) {
      if (finalResult.interactive) streamText.stop();
      else await streamText.finish(resultText(finalResult));
      handleResult(finalResult, assistantMsg, sessionId);
    } else {
      const interruptedText = streamContent || '连接中断，未收到运行结果，请重试';
      await streamText.finish(interruptedText);
      assistantMsg.content = interruptedText;
      assistantMsg.failed = true;
      assistantMsg.pending = false;
      assistantMsg.status = 'interrupted';
      if (!isPreview.value) appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
        turnId,
        status: 'interrupted',
      }).then((saved) => {
        if (saved?.id) assistantMsg.id = saved.id;
      }).catch(() => {});
    }
  } catch (e: any) {
    const failedText = streamContent || (e?.name === 'AbortError' ? '（已停止）' : e?.message || '运行失败');
    await streamText.finish(failedText);
    assistantMsg.content = failedText;
    if (e?.name !== 'AbortError') {
      assistantMsg.failed = true;
    }
    assistantMsg.pending = false;
    assistantMsg.status = e?.name === 'AbortError' ? 'cancelled' : 'failed';
    if (!isPreview.value) appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
      turnId,
      status: e?.name === 'AbortError' ? 'cancelled' : 'failed',
    }).then((saved) => {
      if (saved?.id) assistantMsg.id = saved.id;
    }).catch(() => {});
  } finally {
    streamText.stop();
    if (!live.get(sessionId)?.interactive) live.finish(sessionId);
    if (activeSessionId.value === sessionId) {
      runningNodeLabel.value = '';
      scrollToBottom();
    }
  }
}

async function submitInteractive(optionValue: string | null, formValues?: Record<string, any>) {
  if (!interactive.value || running.value) return;
  const current = interactive.value;
  const sessionId = activeSessionId.value;
  if (!isPreview.value && (!sessionId || live.isBusy(sessionId))) return;
  // userSelect 传选项值；formInput 的值与必填校验由 InteractiveFormFields 组件负责
  const value: any = current.type === 'userSelect' ? optionValue : { ...(formValues || {}) };

  const viewAtStart = live.view.value;
  const thread = messages.value;
  const assistantMsg = createUiMessage({ role: 'assistant', content: '', turnId: createTurnId(), pending: true });
  thread.push(assistantMsg);
  interactive.value = null;
  scrollToBottom(true);
  const abort = new AbortController();
  if (sessionId) {
    live.start(sessionId, {
      abort,
      messages: thread,
      nodeLabel: '',
      view: viewAtStart,
      interactive: null,
    });
  }
  try {
    const result = (await resumeRunDefinition({ appId, sessionId: isPreview.value ? undefined : sessionId, previewVersionId: previewVersionId || undefined, previewDraft: previewDraft || undefined, resumeId: current.resumeId, value })) as RunResult;
    handleResult(result, assistantMsg, sessionId);
  } catch (e: any) {
    assistantMsg.content = e?.response?.data?.detail || e?.message || '恢复执行失败';
    assistantMsg.failed = true;
    assistantMsg.pending = false;
    assistantMsg.status = 'failed';
    if (!isPreview.value) appendRunMessage(sessionId, 'assistant', assistantMsg.content, undefined, {
      turnId: assistantMsg.turnId || undefined,
      status: 'failed',
    }).then((saved) => {
      if (saved?.id) assistantMsg.id = saved.id;
    }).catch(() => {});
  } finally {
    if (sessionId) live.finish(sessionId);
    if (activeSessionId.value === sessionId) scrollToBottom();
  }
}

function stop() {
  const id = activeSessionId.value;
  if (id) live.abortSession(id);
  stopBrowserTts();
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    // 中文输入法用 Enter 上屏候选时不能发送，否则 compositionend 会把旧文本重新写回输入框。
    if (e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    void send();
  }
}

async function fillSuggestedTask(task: string) {
  if (!runnable.value || interactive.value) return;
  input.value = task;
  await nextTick();
  autoResizeComposerInput();
  composerInputRef.value?.focus();
}

onMounted(async () => {
  runMobileQuery = window.matchMedia(RUN_COMPACT_SHELL_QUERY);
  syncRunMobileShell(runMobileQuery);
  runMobileQuery.addEventListener?.('change', syncRunMobileShell);
  nextTick(autoResizeComposerInput);
  if (!appId) {
    loadError.value = '缺少应用 ID';
    appLoading.value = false;
    return;
  }
  await loadApp();
  if (!isPreview.value) await loadSessions();
});

onBeforeUnmount(() => {
  runMobileQuery?.removeEventListener?.('change', syncRunMobileShell);
  runMobileQuery = null;
  live.abortAll();
  stopBrowserTts();
  releaseHydratedPortableRunSkin(hydratedPortableSkin.value);
  hydratedPortableSkin.value = null;
});
</script>

<style lang="less" scoped>
@import './agent-run-shell.less';
</style>
