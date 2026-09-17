<!-- eslint-disable vue/no-v-html --><!-- 气泡内容经 xss(md.render()) 白名单过滤 -->
<template>
  <teleport to="body">
    <aside
      class="sac-panel"
      :class="{
        resizing,
        'compact-drawer-open': isCompactSubagent && !sidebarCollapsed,
      }"
      role="dialog"
      :aria-modal="isCompactSubagent ? 'true' : 'false'"
      aria-label="子智能体独立对话"
      :style="{ left: pos.x + 'px', top: pos.y + 'px', width: size.w + 'px', height: size.h + 'px', zIndex }"
      @mousedown="bringToFront"
      @keydown.esc="emit('close')"
    >
      <button
        type="button"
        class="sac-close-btn"
        title="关闭"
        aria-label="关闭子智能体对话"
        @mousedown.stop
        @click.stop="emit('close')"
      >
        <CloseOutlined />
      </button>
      <div
        class="agent-run-page"
        :class="{ 'is-splitting': splitting, 'sidebar-collapsed': sidebarCollapsed }"
        :style="{ '--run-sidebar-width': sidebarWidth + 'px' }"
      >
        <RunCompactHeader
          :title="subagent?.name || appMeta?.name || '智能体运行'"
          :sidebar-collapsed="sidebarCollapsed"
          :inspiration-collapsed="inspirationCollapsed"
          show-inspiration
          show-close
          @toggle-sidebar="toggleSidebar"
          @toggle-inspiration="toggleInspiration"
          @close="emit('close')"
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
        <!-- 左侧会话侧栏：与「我的智能体」运行页同一套 class / 样式 -->
        <aside class="agent-run-sidebar">
          <div class="run-brand" @mousedown="onHeaderDown">
            <span class="run-brand-avatar" aria-hidden="true">
              <span>{{ brandInitials }}</span>
              <img
                v-if="brandIconUrl"
                :src="brandIconUrl"
                :alt="subagent?.name || appMeta?.name || '智能体'"
                @error="recoverAgentIcon($event, brandItem)"
              />
            </span>
            <span class="run-brand-copy">
              <strong>{{ subagent?.name || appMeta?.name || '智能体运行' }}</strong>
              <!-- <span v-if="appTypeLabel" class="run-brand-type">{{ appTypeLabel }}</span> -->
            </span>
            <RunSidebarToggle
              :collapsed="sidebarCollapsed"
              @mousedown.stop
              @toggle="toggleSidebar"
            />
          </div>
          <button type="button" class="new-session-btn" :disabled="!runnable" @click="onNewConversation">
            <svg class="compose-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
              <path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" />
            </svg>
            <span class="new-session-label">新对话</span>
          </button>
          <div class="session-search">
            <SearchOutlined />
            <input v-model="keyword" placeholder="搜索会话" @keyup.enter="onSearch" />
          </div>
          <RunSessionList
            :sessions="sessions"
            :active-session-id="activeSessionId"
            :running-session-ids="runningSessionIds"
            @select="onPickSession"
            @pin="onPinSession"
            @rename="onRenameSession"
            @remove="onRemoveSession"
          />
        </aside>
        <div
          class="run-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整侧栏宽度"
          @mousedown="onSplitterDown"
        ></div>

        <main class="agent-run-main">
          <div
            ref="bodyRef"
            :class="['run-messages', { 'is-empty-state': isEmptyState }]"
            @scroll.passive="updateScrollState"
          >
            <div v-if="appLoading" class="run-empty">正在加载应用...</div>
            <div v-else-if="!runnable" class="run-tip">
              <a-alert type="warning" show-icon :message="notRunnableReason" />
            </div>
            <div v-else-if="isEmptyState" class="run-start">
              <article class="run-welcome">
                <p class="run-welcome-kicker">{{ subagent?.name || appMeta?.name || '智能体' }}</p>
                <h1>你好，有什么我可以帮你？</h1>
                <!-- eslint-disable-next-line vue/no-v-html --><!-- 开场白经 xss(md.render()) 白名单过滤 -->
                <div class="markdown-body run-welcome-body" v-html="renderMarkdown(displayWelcomeText)"></div>
              </article>
            </div>

            <!-- 已落库会话优先（含附件卡）。尚未落库时消费主对话 subagent.* 运行档，渲染成同一套 run-msg。 -->
            <template v-else-if="showLiveDelegation">
              <article class="run-msg user from-work-agent">
                <div class="run-msg-col">
                  <RunMessageSenderBadge />
                  <div class="run-bubble">
                    <p class="run-text">{{ delegationRun?.task || '主 Agent 已发起委派' }}</p>
                  </div>
                </div>
              </article>
              <article class="run-msg assistant">
                <div class="run-msg-col">
                  <div class="run-bubble">
                    <RunAssistantMarkdown v-if="displayDelegationOutput" :content="displayDelegationOutput" />
                    <p v-else-if="delegationRun?.error" class="run-text">{{ delegationRun.error }}</p>
                    <p v-else class="run-generating">
                      正在思考
                    </p>
                    <RunGeneratedFiles v-if="delegationFiles.length" :files="delegationFiles" />
                  </div>
                  <div v-if="displayDelegationOutput" class="sac-msg-ops">
                    <button
                      type="button"
                      class="sac-open-full"
                      title="在新标签页打开此智能体的完整对话"
                      @click="openFullAgentPage"
                    >
                      <ExportOutlined /> 打开完整对话
                    </button>
                  </div>
                </div>
              </article>
            </template>

            <template v-else>
              <article
                v-for="(m, i) in messages"
                :key="m.id || i"
                :class="['run-msg', m.role, { failed: m.failed, 'from-work-agent': m.senderType === 'work_agent' }]"
              >
                <div class="run-msg-col">
                  <RunMessageSenderBadge v-if="m.role === 'user' && m.senderType === 'work_agent'" />
                  <div v-if="m.role === 'user' && editingIndex !== i && m.attachments?.length" class="message-attachments">
                    <AttachmentCard
                      v-for="(att, ai) in m.attachments"
                      :key="ai"
                      :attachment="att"
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
                  <div v-else-if="m.role === 'assistant' || m.content || m.pending" class="run-bubble">
                    <RunAssistantMarkdown v-if="m.role === 'assistant' && m.content" :content="m.content" />
                    <p v-else-if="m.content" class="run-text">{{ m.content }}</p>
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
                  <div
                    v-if="m.role === 'assistant' && m.content && !m.pending && !m.failed"
                    class="sac-msg-ops"
                  >
                    <button
                      type="button"
                      class="sac-open-full"
                      title="在新标签页打开此智能体的完整对话"
                      @click="openFullAgentPage"
                    >
                      <ExportOutlined /> 打开完整对话
                    </button>
                  </div>
                </div>
              </article>
            </template>

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
              <InteractiveFormFields
                v-else
                :fields="interactiveFormItems"
                :disabled="running"
                id-prefix="subagent-run-form"
                @submit="onInteractiveSubmit"
              />
            </div>

          </div>

          <footer :class="['run-composer', { 'is-empty-state': isEmptyState }]">
            <RunScrollToBottom v-if="(messages.length || showLiveDelegation) && !atBottom" @click="scrollBottom(true)" />
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
                <div v-if="pendingAtts.length" class="file-chip-list">
                  <AttachmentCard
                    v-for="(att, ai) in pendingAtts"
                    :key="att.uid"
                    :attachment="att"
                    removable
                    @remove="removeAtt(ai)"
                    @preview="lightboxSrc = $event"
                  />
                </div>
                <textarea
                  ref="inputRef"
                  v-model="input"
                  rows="1"
                  :disabled="!runnable || !!interactive"
                  placeholder="输入你的问题..."
                  aria-label="输入消息"
                  @keydown.enter.exact="onEnterSend"
                  @paste="onComposerPaste"
                />
                <div class="composer-toolbar">
                  <div v-if="fileUploadEnabled" class="composer-input-actions">
                    <button
                      type="button"
                      class="attach-btn"
                      title="添加照片和文件"
                      aria-label="添加照片和文件"
                      :disabled="!runnable || !!interactive || uploading"
                      @click="triggerUpload"
                    >
                      <PlusOutlined />
                    </button>
                  </div>
                  <button
                    v-if="running"
                    type="button"
                    class="send-btn stop"
                    title="停止"
                    aria-label="停止生成"
                    @click="stop"
                  >
                    <span class="stop-square" aria-hidden="true" />
                  </button>
                  <button
                    v-else
                    type="button"
                    class="send-btn"
                    title="发送"
                    aria-label="发送消息"
                    :disabled="(!input.trim() && !pendingAtts.length) || !runnable || !!interactive || uploading"
                    @click="onSend"
                  >
                    <ArrowUpOutlined />
                  </button>
                </div>
                <input
                  v-if="fileUploadEnabled"
                  ref="fileInputRef"
                  type="file"
                  multiple
                  class="file-input"
                  :accept="acceptedFileTypes"
                  @change="onFileChange"
                />
              </div>
            </div>
            <AgentOutputDisclaimer v-if="messages.length || showLiveDelegation" />
          </footer>
        </main>
        <RunInspirationPanel
          :collapsed="inspirationCollapsed"
          :width="inspirationWidth"
          :scenes="inspirationScenes"
          @toggle="toggleInspiration"
          @resize-start="onInspirationResizeStart"
          @select="fillSuggestedTask"
        />
      </div>

      <div class="sac-rz sac-rz-n" @mousedown="onResizeDown($event, 'n')"></div>
      <div class="sac-rz sac-rz-s" @mousedown="onResizeDown($event, 's')"></div>
      <div class="sac-rz sac-rz-e" @mousedown="onResizeDown($event, 'e')"></div>
      <div class="sac-rz sac-rz-w" @mousedown="onResizeDown($event, 'w')"></div>
      <div class="sac-rz sac-rz-ne" @mousedown="onResizeDown($event, 'ne')"></div>
      <div class="sac-rz sac-rz-nw" @mousedown="onResizeDown($event, 'nw')"></div>
      <div class="sac-rz sac-rz-se" @mousedown="onResizeDown($event, 'se')"></div>
      <div class="sac-rz sac-rz-sw" @mousedown="onResizeDown($event, 'sw')"></div>
    </aside>

    <ImageLightbox :src="lightboxSrc" @close="lightboxSrc = null" />
  </teleport>
</template>

<script lang="ts">
// 置顶层级序列必须是模块级共享：每个窗口实例各自维护时，多窗的 zIndex 会撞在同一数值上，
// 点击谁都无法真正置顶另一个
let zSeq = 2400;
</script>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { message as antMessage } from 'ant-design-vue';
import {
  ArrowUpOutlined,
  CloseOutlined,
  ExportOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue';
import MarkdownIt from 'markdown-it';
import xss, { getDefaultWhiteList } from 'xss';
import { uploadChatFile, type SubagentItem } from '../agentApi';
import { openAgentRunWindow } from '../../workflow/shared/runtimeRoute';
import { useAgentRun } from '../../agent/run/useAgentRun';
import { pickInitialSession } from '../../agent/run/pickInitialSession';
import { resolveRunInspirationScenes, resolveRunWelcomeText } from '../../agent/run/agentRunPresentation';
import { compileAnswerLayout } from '../utils/compileAnswerLayout';
import { stopProtocolLinkAtCjkPunctuation } from '../utils/markdownLinkify';
import { filesFromClipboard, longTextAsPastedFile } from '../utils/composerClipboard';
import AttachmentCard from './AttachmentCard.vue';
import AgentOutputDisclaimer from './AgentOutputDisclaimer.vue';
import ImageLightbox from './ImageLightbox.vue';
import InteractiveFormFields from './InteractiveFormFields.vue';
import RunAssistantMarkdown from '../../agent/run/components/RunAssistantMarkdown.vue';
import RunCompactHeader from '../../agent/run/components/RunCompactHeader.vue';
import RunGeneratedFiles from '../../agent/run/components/RunGeneratedFiles.vue';
import RunInspirationPanel from '../../agent/run/components/RunInspirationPanel.vue';
import RunTaskParameters from '../../agent/run/components/RunTaskParameters.vue';
import RunMessageActions from '../../agent/run/components/RunMessageActions.vue';
import RunMessageSenderBadge from '../../agent/run/components/RunMessageSenderBadge.vue';
import RunScrollToBottom from '../../agent/run/components/RunScrollToBottom.vue';
import RunSidebarToggle from '../../agent/run/components/RunSidebarToggle.vue';
import RunSessionList from '../../agent/run/components/RunSessionList.vue';
import RunUserMessageEdit from '../../agent/run/components/RunUserMessageEdit.vue';
import { useSidebarResize } from '../../agent/run/useSidebarResize';
import { getAgentFallbackIcon, getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../agentIcon';
import type { SubagentRun } from '../composables/executionTimeline';
import { createSmoothStreamText } from '../composables/smoothStreamText';

const props = defineProps<{
  subagent: SubagentItem;
  index: number;
  /** 打开来源的主对话 thread_id：会话列表里优先选中该主对话产生的委派会话 */
  parentThreadId?: string;
  /** 主对话当前委派运行档：subagent.delta/reasoning/node 到达时原地响应。 */
  delegationRun?: SubagentRun;
}>();
const emit = defineEmits<{
  (e: 'close'): void;
}>();

const { sidebarWidth, sidebarCollapsed, splitting, onSplitterDown, toggleSidebar } = useSidebarResize();
const {
  sidebarWidth: inspirationWidth,
  sidebarCollapsed: inspirationCollapsed,
  onSplitterDown: onInspirationResizeStart,
  toggleSidebar: toggleInspiration,
} = useSidebarResize('agent-run:inspiration-width', 'left');
const SUBAGENT_COMPACT_SHELL_QUERY = '(max-width: 1024px)';
let subagentCompactQuery: MediaQueryList | null = null;
const subagentStartsCompact = typeof window !== 'undefined' && window.matchMedia(SUBAGENT_COMPACT_SHELL_QUERY).matches;
const isCompactSubagent = ref(subagentStartsCompact);

if (subagentStartsCompact) {
  sidebarCollapsed.value = true;
  inspirationCollapsed.value = true;
}

function syncSubagentCompactShell(event: MediaQueryListEvent | MediaQueryList) {
  isCompactSubagent.value = event.matches;
  if (!event.matches) {
    nextTick(onWindowResize);
    return;
  }
  sidebarCollapsed.value = true;
  inspirationCollapsed.value = true;
}

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
// 无协议裸文本不成链：「xxx.md」会被当 .md 域名跳外网（与 MessageList 同策略）。
md.linkify.set({ fuzzyLink: false });
stopProtocolLinkAtCjkPunctuation(md);
const markdownWhiteList = {
  ...getDefaultWhiteList(),
  a: [...getDefaultWhiteList().a, 'rel'],
};
function renderMarkdown(text: string) {
  return xss(md.render(compileAnswerLayout(text || '').markdown), { whiteList: markdownWhiteList });
}

const delegationOutput = computed(() =>
  String(props.delegationRun?.output || props.delegationRun?.preview || ''),
);
const displayDelegationOutput = ref(delegationOutput.value);
const delegationStreamText = createSmoothStreamText({
  initialContent: displayDelegationOutput.value,
  commit: (content) => { displayDelegationOutput.value = content; },
});
const liveDelegationReasoning = computed(() =>
  String((props.delegationRun && props.delegationRun.reasoning) || ''),
);

// 运行栈（与「我的智能体」/agent/run 同源）
const {
  appLoading,
  appMeta,
  runnable,
  notRunnableReason,
  sessions,
  activeSessionId,
  messages,
  running,
  runningSessionIds,
  interactive,
  formValues,
  interactiveFormItems,
  runtimeVariableItems,
  runtimeVariableValues,
  loadApp,
  loadSessions,
  selectSession,
  newSession,
  removeSession,
  pinSession,
  renameSession,
  send,
  submitInteractive,
  updateMessageFeedback,
  stop,
  stopAll,
} = useAgentRun(() => props.subagent.id);

const fileConfig = computed(() => appMeta.value?.fileSelectConfig || {});
const fileUploadEnabled = computed(() => Boolean(fileConfig.value.canSelectFile || fileConfig.value.canSelectImg || fileConfig.value.canSelectVideo || fileConfig.value.canSelectAudio || fileConfig.value.canSelectCustomFileExtension));
const maxUploadFiles = computed(() => Math.max(1, Number(fileConfig.value.maxFiles || 10)));
const acceptedFileTypes = computed(() => {
  const config = fileConfig.value;
  const types: string[] = [];
  if (config.canSelectImg) types.push('image/*');
  if (config.canSelectVideo) types.push('video/*');
  if (config.canSelectAudio) types.push('audio/*');
  if (config.canSelectFile) types.push('.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.md,.csv,.json');
  if (config.canSelectCustomFileExtension) types.push(...(config.customFileExtensionList || []).map((item) => `.${String(item || '').replace(/^\./, '')}`).filter((item) => item.length > 1));
  return types.join(',');
});
function replaceRuntimeVariables(value: Record<string, any>) {
  Object.keys(runtimeVariableValues).forEach((key) => delete runtimeVariableValues[key]);
  Object.assign(runtimeVariableValues, value);
}

const skipLiveDelegation = ref(false);
const showLiveDelegation = computed(() =>
  Boolean(props.delegationRun)
  && messages.value.length === 0
  && !activeSessionId.value
  && !skipLiveDelegation.value,
);
const delegationFiles = computed(() =>
  (props.delegationRun?.files || []).filter((file) => file.id && file.filename && file.deliverable !== false),
);
const isEmptyState = computed(
  () => !appLoading.value && runnable.value && messages.value.length === 0 && !showLiveDelegation.value,
);
const appTypeLabel = computed(() =>
  ({ chatAgent: '对话 Agent', workflow: '工作流', workflowTool: '工作流工具' }[appMeta.value?.aiAppType || ''] || appMeta.value?.aiAppType || ''),
);
const brandItem = computed(() => ({
  name: props.subagent?.name || appMeta.value?.name,
  appName: appMeta.value?.name,
  icon: props.subagent?.icon,
  appIcon: appMeta.value?.appIcon,
  appCategory: appMeta.value?.appCategory,
}));
const brandIconUrl = computed(() => getAgentIconUrl(brandItem.value) || getAgentFallbackIcon(brandItem.value));
const brandInitials = computed(() => getAgentInitials(brandItem.value));

function openFullAgentPage() {
  const opened = openAgentRunWindow({ id: props.subagent?.id });
  if (!opened) {
    antMessage.warning('无法打开完整对话，请允许浏览器弹出窗口');
  }
}

const displayWelcomeText = computed(() =>
  resolveRunWelcomeText(props.subagent?.name || appMeta.value?.name || '', appMeta.value?.welcomeText),
);
const inspirationScenes = computed(() => resolveRunInspirationScenes({
  inspirationScenes: appMeta.value?.inspirationScenes,
  quickQuestions: appMeta.value?.quickQuestions,
}));

function isImageAttachment(attachment: { filename?: string; kind?: string }) {
  const ext = (attachment.filename || '').split('.').pop()?.toLowerCase() || '';
  return attachment.kind === 'image' || ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'].includes(ext);
}
function imageAttachmentCount(attachments?: Array<{ filename?: string; kind?: string }>) {
  return (attachments || []).filter(isImageAttachment).length;
}
function onInteractiveSubmit(values: Record<string, any>) {
  Object.keys(formValues).forEach((key) => delete formValues[key]);
  Object.assign(formValues, values || {});
  submitInteractive(null);
}

const input = ref('');
const keyword = ref('');
const editingIndex = ref<number | null>(null);
const editingText = ref('');
const inputRef = ref<HTMLTextAreaElement | null>(null);
const bodyRef = ref<HTMLElement | null>(null);
const atBottom = ref(true);
const stickToBottom = ref(true);
const COMPOSER_INPUT_MAX_HEIGHT = 220;
function composerInputMaxHeight() {
  if (typeof window === 'undefined') return COMPOSER_INPUT_MAX_HEIGHT;
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  if (window.innerWidth <= 719) {
    return Math.max(88, Math.min(140, Math.round(viewportHeight * 0.24)));
  }
  if (window.innerWidth <= 1024) {
    return Math.max(112, Math.min(180, Math.round(viewportHeight * 0.28)));
  }
  return COMPOSER_INPUT_MAX_HEIGHT;
}
function autoResizeComposerInput() {
  const el = inputRef.value;
  if (!el) return;
  const maxHeight = composerInputMaxHeight();
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
}
watch(input, () => nextTick(autoResizeComposerInput));

// ===== 附件 / 照片（与主对话同链路：/chat/upload 解析文本，随消息交给子智能体）=====
type PendingAtt = {
  uid: number;
  filename: string;
  kind?: string;
  text: string;
  previewUrl?: string;
  /** 落库用压缩缩略图，避免原图 data URL 超过 attachments_json 体积上限 */
  thumbUrl?: string;
  uploading?: boolean;
  /** 解析置信度（与主对话同链路的 /chat/upload 返回）：failed 时禁止发送，AttachmentCard 靠它渲染失败态 */
  status?: 'ok' | 'partial' | 'failed';
  note?: string;
  fileId?: string;
};
const pendingAtts = ref<PendingAtt[]>([]);
const uploading = ref(false);
const composerDragActive = ref(false);
const fileInputRef = ref<HTMLInputElement | null>(null);
const lightboxSrc = ref<string | null>(null);
const MAX_FILE_MB = 15; // 与后端 /chat/upload 硬上限一致
let attSeq = 0;

function triggerUpload() {
  if (!canAcceptComposerFiles()) return;
  fileInputRef.value?.click();
}

function canAcceptComposerFiles() {
  return fileUploadEnabled.value && runnable.value && !interactive.value && !running.value && !uploading.value;
}

function rejectComposerFiles() {
  antMessage.warning(fileUploadEnabled.value ? '当前不能添加附件' : '当前智能体未开启附件上传');
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
  void addComposerFiles(files);
}

function onComposerPaste(e: ClipboardEvent) {
  const files = filesFromClipboard(e.clipboardData);
  if (files.length) {
    e.preventDefault();
    if (!canAcceptComposerFiles()) {
      rejectComposerFiles();
      return;
    }
    void addComposerFiles(files);
    return;
  }
  if (!canAcceptComposerFiles()) return;
  const asFile = longTextAsPastedFile(e.clipboardData?.getData('text/plain') || '');
  if (!asFile) return;
  e.preventDefault();
  void addComposerFiles([asFile]);
}

async function addComposerFiles(files: File[]) {
  const remaining = Math.max(0, maxUploadFiles.value - pendingAtts.value.length);
  if (!remaining) {
    antMessage.warning(`最多添加 ${maxUploadFiles.value} 个附件`);
    return;
  }
  if (files.length > remaining) antMessage.warning(`最多添加 ${maxUploadFiles.value} 个附件`);
  for (const file of files.slice(0, remaining)) await uploadOne(file);
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

async function uploadOne(file: File) {
  if (pendingAtts.value.length >= maxUploadFiles.value) {
    antMessage.error(`最多添加 ${maxUploadFiles.value} 个附件`);
    return;
  }
  if (file.size > MAX_FILE_MB * 1024 * 1024) {
    antMessage.error(`「${file.name}」超过 ${MAX_FILE_MB}MB 上限`);
    return;
  }
  // 先本地读预览 + 入列占位卡（图片缩略图秒出，转圈盖在卡上），再后台上传解析回填
  const previewUrl = await readImagePreview(file);
  const thumbUrl = previewUrl ? await makeImageThumbnail(previewUrl) : undefined;
  const uid = (attSeq += 1);
  const isImage = !!previewUrl;
  pendingAtts.value.push({
    uid,
    filename: file.name,
    kind: isImage ? 'image' : 'file',
    text: '',
    previewUrl,
    thumbUrl,
    uploading: true,
  });
  uploading.value = true;
  try {
    const up = await uploadChatFile(file, appMeta.value?.chatModel);
    const idx = pendingAtts.value.findIndex((a) => a.uid === uid);
    if (idx !== -1) {
      // status/note 必须透传：否则解析失败的附件在卡片上和正常附件毫无区别，发送前也拦不住
      pendingAtts.value[idx] = {
        uid,
        filename: up.filename,
        kind: up.kind,
        text: up.text,
        previewUrl: previewUrl ?? up.previewUrl,
        thumbUrl,
        uploading: false,
        status: up.status,
        note: up.note,
        fileId: up.file_id,
      };
      if (up.status === 'failed') {
        antMessage.error(`「${file.name}」解析失败${up.note ? `：${up.note}` : ''}，可移除后重新上传`);
      }
    }
  } catch (e) {
    pendingAtts.value = pendingAtts.value.filter((a) => a.uid !== uid);
    antMessage.error(e instanceof Error ? e.message : `「${file.name}」上传失败`);
  } finally {
    uploading.value = pendingAtts.value.some((a) => a.uploading);
  }
}

async function onFileChange(e: Event) {
  const el = e.target as HTMLInputElement;
  const files = Array.from(el.files || []);
  el.value = '';
  await addComposerFiles(files);
}

function removeAtt(index: number) {
  pendingAtts.value.splice(index, 1);
  uploading.value = pendingAtts.value.some((a) => a.uploading);
}

// ===== 浮动 + 拖动 + 缩放 + 层级 =====
const pos = ref({ x: 0, y: 0 });
const size = ref({ w: 720, h: 600 });
const zIndex = ref(2400);
const resizing = ref(false);
// 最小宽随视口收缩：固定 520 在窄屏（<576px）会横向溢出
const minW = () => Math.min(760, Math.max(360, window.innerWidth - 24));
const MIN_H = 420;
function bringToFront() {
  zSeq += 1;
  zIndex.value = zSeq;
}
let dragStart: { mx: number; my: number; x: number; y: number } | null = null;
function onHeaderDown(e: MouseEvent) {
  if (isCompactSubagent.value) return;
  bringToFront();
  dragStart = { mx: e.clientX, my: e.clientY, x: pos.value.x, y: pos.value.y };
  window.addEventListener('mousemove', onDragMove);
  window.addEventListener('mouseup', onDragEnd);
}
function onDragMove(e: MouseEvent) {
  if (!dragStart) return;
  const x = dragStart.x + (e.clientX - dragStart.mx);
  const y = dragStart.y + (e.clientY - dragStart.my);
  pos.value = {
    x: Math.min(Math.max(4, x), window.innerWidth - 120),
    y: Math.min(Math.max(4, y), window.innerHeight - 80),
  };
}
function onDragEnd() {
  dragStart = null;
  window.removeEventListener('mousemove', onDragMove);
  window.removeEventListener('mouseup', onDragEnd);
}

let rzStart:
  | { mx: number; my: number; x: number; y: number; w: number; h: number; dir: string }
  | null = null;
function onResizeDown(e: MouseEvent, dir: string) {
  if (isCompactSubagent.value) return;
  e.preventDefault();
  e.stopPropagation();
  bringToFront();
  resizing.value = true;
  rzStart = { mx: e.clientX, my: e.clientY, x: pos.value.x, y: pos.value.y, w: size.value.w, h: size.value.h, dir };
  window.addEventListener('mousemove', onResizeMove);
  window.addEventListener('mouseup', onResizeEnd);
}
function onResizeMove(e: MouseEvent) {
  if (!rzStart) return;
  const { mx, my, x, y, w, h, dir } = rzStart;
  const dx = e.clientX - mx;
  const dy = e.clientY - my;
  let nx = x;
  let ny = y;
  let nw = w;
  let nh = h;
  if (dir.includes('e')) nw = Math.min(Math.max(minW(), w + dx), window.innerWidth - x - 4);
  if (dir.includes('s')) nh = Math.min(Math.max(MIN_H, h + dy), window.innerHeight - y - 4);
  if (dir.includes('w')) {
    const right = x + w;
    nw = Math.min(Math.max(minW(), w - dx), right - 4);
    nx = right - nw;
  }
  if (dir.includes('n')) {
    const bottom = y + h;
    nh = Math.min(Math.max(MIN_H, h - dy), bottom - 4);
    ny = bottom - nh;
  }
  pos.value = { x: nx, y: ny };
  size.value = { w: nw, h: nh };
}
function onResizeEnd() {
  rzStart = null;
  resizing.value = false;
  window.removeEventListener('mousemove', onResizeMove);
  window.removeEventListener('mouseup', onResizeEnd);
}

// 浏览器窗口缩小后把面板收回屏内（不监听的话面板可能整个留在视口外，再也点不回来）
function onWindowResize() {
  autoResizeComposerInput();
  if (isCompactSubagent.value) return;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = Math.max(minW(), Math.min(size.value.w, vw - 8));
  const h = Math.max(MIN_H, Math.min(size.value.h, vh - 8));
  size.value = { w, h };
  pos.value = {
    x: Math.min(Math.max(4, pos.value.x), Math.max(4, vw - w - 4)),
    y: Math.min(Math.max(4, pos.value.y), Math.max(4, vh - 80)),
  };
}

function updateScrollState() {
  const el = bodyRef.value;
  if (!el) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  if (distance < 40) stickToBottom.value = true;
  else if (distance > 160) stickToBottom.value = false;
  atBottom.value = distance < 120;
}

function scrollBottom(force = false) {
  nextTick(() => {
    const el = bodyRef.value;
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
  scrollBottom(true);
  try {
    const accepted = await send(text, { replaceFromIndex: index });
    if (!accepted && !input.value.trim()) input.value = text;
  } catch (e) {
    antMessage.error(e instanceof Error ? e.message : '发送失败');
  }
}

// ===== 加载 / 新建 / 切换 / 搜索 / 删除会话 =====
async function init() {
  skipLiveDelegation.value = false;
  await loadApp();
  if (!runnable.value) return;
  await loadSessions();
  // 默认选中：当前主对话的委派会话 → 最新非委派会话 → 新对话（纯逻辑见 pickInitialSession）。
  // 绝不落到其他主对话的委派或孤儿委派会话（用稳定的 origin 标记判定，不靠可空 parentThreadId）。
  const pick = pickInitialSession(sessions.value, props.parentThreadId);
  if (pick) await selectSession(pick);
  else newSession();
  scrollBottom(true);
  nextTick(() => inputRef.value?.focus());
}

async function onSearch() {
  await loadSessions(keyword.value.trim() || undefined);
}

async function onPickSession(id: string) {
  cancelEdit();
  skipLiveDelegation.value = false;
  // 手机/iPad：先关抽屉，让 0.24s 滑出和会话加载叠在一起。
  if (isCompactSubagent.value) sidebarCollapsed.value = true;
  await selectSession(id);
  scrollBottom(true);
}

function onNewConversation() {
  cancelEdit();
  skipLiveDelegation.value = true;
  if (isCompactSubagent.value) sidebarCollapsed.value = true;
  newSession();
  input.value = '';
  pendingAtts.value = [];
  nextTick(() => {
    autoResizeComposerInput();
    inputRef.value?.focus();
  });
}

async function onRemoveSession(id: string) {
  try {
    await removeSession(id);
  } catch (e) {
    antMessage.error(e instanceof Error ? e.message : '删除会话失败');
  }
}

async function onPinSession(id: string) {
  try {
    await pinSession(id);
  } catch (e) {
    antMessage.error(e instanceof Error ? e.message : '置顶失败');
  }
}

async function onRenameSession(id: string, title: string) {
  try {
    await renameSession(id, title);
  } catch (e) {
    antMessage.error(e instanceof Error ? e.message : '重命名失败');
  }
}

function onEnterSend(e: KeyboardEvent) {
  // 中文输入法选词的 Enter（isComposing / keyCode 229）不算发送，否则候选未上屏就误发
  if (e.isComposing || e.keyCode === 229) return;
  e.preventDefault();
  onSend();
}

async function fillSuggestedTask(task: string) {
  const value = String(task || '').trim();
  if (!value || !runnable.value || interactive.value) return;
  input.value = value;
  await nextTick();
  autoResizeComposerInput();
  inputRef.value?.focus();
}

async function onSend() {
  const t = input.value.trim();
  const atts = pendingAtts.value.filter((a) => !a.uploading);
  if ((!t && !atts.length) || running.value || uploading.value) return;
  // 解析失败的附件不许静默发出：text 为空会被下游过滤丢弃，子智能体完全看不到用户发过文件
  const failedAtt = atts.find((a) => a.status === 'failed');
  if (failedAtt) {
    antMessage.error(`「${failedAtt.filename}」解析失败${failedAtt.note ? `：${failedAtt.note}` : ''}，请先移除该附件再发送`);
    return;
  }
  input.value = '';
  window.setTimeout(() => {
    if (input.value.trim() === t) input.value = '';
  }, 0);
  pendingAtts.value = [];
  scrollBottom(true);
  try {
    const accepted = await send(t, atts.length ? {
      attachments: atts.map((a) => ({
        filename: a.filename,
        kind: a.kind,
        text: a.text,
        previewUrl: a.thumbUrl ?? a.previewUrl,
        status: a.status,
        note: a.note,
        fileId: a.fileId,
      })),
    } : undefined);
    if (!accepted) {
      if (!input.value.trim()) input.value = t;
      if (!pendingAtts.value.length) pendingAtts.value = atts;
    }
  } catch (e) {
    antMessage.error(e instanceof Error ? e.message : '发送失败');
  }
  scrollBottom();
  nextTick(() => inputRef.value?.focus());
}

onMounted(() => {
  subagentCompactQuery = window.matchMedia(SUBAGENT_COMPACT_SHELL_QUERY);
  syncSubagentCompactShell(subagentCompactQuery);
  subagentCompactQuery.addEventListener?.('change', syncSubagentCompactShell);
  const w = Math.min(1080, Math.max(minW(), window.innerWidth - 48));
  const h = Math.min(Math.round(window.innerHeight * 0.88), 900);
  size.value = { w, h };
  pos.value = {
    x: Math.max(8, Math.round((window.innerWidth - w) / 2) + props.index * 28),
    y: Math.max(8, Math.round((window.innerHeight - h) / 2) + props.index * 20),
  };
  bringToFront();
  window.addEventListener('resize', onWindowResize);
  window.visualViewport?.addEventListener('resize', autoResizeComposerInput);
  nextTick(autoResizeComposerInput);
  init();
});

onBeforeUnmount(() => {
  // 关掉窗口才中止所有并行会话；点「新对话」只切视图，后台继续跑。
  subagentCompactQuery?.removeEventListener?.('change', syncSubagentCompactShell);
  subagentCompactQuery = null;
  delegationStreamText.stop();
  stopAll();
  window.removeEventListener('mousemove', onDragMove);
  window.removeEventListener('mouseup', onDragEnd);
  window.removeEventListener('mousemove', onResizeMove);
  window.removeEventListener('mouseup', onResizeEnd);
  window.removeEventListener('resize', onWindowResize);
  window.visualViewport?.removeEventListener('resize', autoResizeComposerInput);
});

// 换子智能体（组件复用极少，稳妥起见重载）
watch(
  () => props.subagent?.id,
  (id, old) => {
    if (id && id !== old) {
      stopAll();
      init();
    }
  },
);

watch(
  [
    () => props.delegationRun?.runKey || '',
    delegationOutput,
    () => props.delegationRun?.status || 'running',
  ],
  ([runKey, output, status], [previousRunKey]) => {
    if (runKey !== previousRunKey) {
      delegationStreamText.reset(output);
      return;
    }
    if (status === 'running') delegationStreamText.push(output);
    else delegationStreamText.finalize(output);
  },
);

// 流式增长 / 消息增减时黏底
watch(
  () => messages.value.reduce((n, m) => n + m.content.length, 0),
  scrollBottom,
);
watch(
  () => `${displayDelegationOutput.value.length}:${liveDelegationReasoning.value.length}:${props.delegationRun?.nodes.length || 0}`,
  () => scrollBottom(),
);
watch(() => messages.value.length, scrollBottom);
</script>

<style scoped lang="less">
@import '../../agent/run/agent-run-shell.less';

.sac-panel {
  position: fixed;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  box-shadow: 0 16px 44px rgba(17, 24, 39, 0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sac-panel .agent-run-page {
  flex: 1;
  min-height: 0;
  min-width: 0;
}
.sac-panel .run-brand {
  cursor: move;
  user-select: none;
}
.sac-close-btn {
  position: absolute;
  z-index: 7;
  top: 10px;
  right: 10px;
  display: inline-flex;
  width: 32px;
  height: 32px;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #686b73;
  font-size: 16px;
  cursor: pointer;
}
.sac-close-btn:hover { background: #f2f2f3; color: #1d1d20; }
.sac-close-btn:focus-visible { outline: 2px solid #111827; outline-offset: 2px; }
.sac-panel.resizing {
  user-select: none;
}
.sac-rz {
  position: absolute;
  z-index: 6;
}
.sac-rz-n,
.sac-rz-s {
  left: 8px;
  right: 8px;
  height: 6px;
  cursor: ns-resize;
}
.sac-rz-e,
.sac-rz-w {
  top: 8px;
  bottom: 8px;
  width: 6px;
  cursor: ew-resize;
}
.sac-rz-n { top: 0; }
.sac-rz-s { bottom: 0; }
.sac-rz-e { right: 0; }
.sac-rz-w { left: 0; }
.sac-rz-ne,
.sac-rz-nw,
.sac-rz-se,
.sac-rz-sw {
  width: 14px;
  height: 14px;
}
.sac-rz-ne { top: 0; right: 0; cursor: nesw-resize; }
.sac-rz-sw { bottom: 0; left: 0; cursor: nesw-resize; }
.sac-rz-nw { top: 0; left: 0; cursor: nwse-resize; }
.sac-rz-se { bottom: 0; right: 0; cursor: nwse-resize; }
.sac-msg-ops {
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}
.run-msg:hover .sac-msg-ops,
.sac-msg-ops:focus-within {
  opacity: 1;
}
.sac-open-full {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: #8a8f99;
  font-size: 11.5px;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease;
}
.sac-open-full:hover {
  border-color: #e0e2e8;
  color: #33436b;
}

@media (max-width: 1024px) {
  .sac-panel {
    inset: 0 !important;
    width: 100vw !important;
    height: 100dvh !important;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }

  .sac-panel .run-brand {
    cursor: default;
  }

  .sac-close-btn {
    display: none;
  }

  .sac-msg-ops {
    display: none;
  }

  .sac-rz {
    display: none;
  }
}

</style>
