<template>
  <div
    :class="[
      'tox-center',
      {
        'sidebar-collapsed': modelPanelCollapsed,
        'content-mode': activeSection !== 'chat',
        'sidebar-resizing': sidebarResizing,
        'compact-nav-open': compactNavOpen,
      },
    ]"
    :style="{ '--center-nav-width': `${effectiveNavWidth}px` }"
  >
    <header class="user-header">
      <button
        ref="compactMenuButtonRef"
        class="compact-menu-trigger"
        type="button"
        :aria-expanded="compactNavOpen"
        aria-controls="center-primary-navigation"
        aria-label="打开导航菜单"
        @click="toggleCompactNav"
      >
        <!-- 与子智能体运行页 RunSidebarToggle 同款侧栏图标，避免和顶栏「任务协作」三横线撞形。 -->
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <rect width="18" height="18" x="3" y="3" rx="2" />
          <path d="M9 3v18" />
        </svg>
      </button>
      <strong v-if="activeSection !== 'chat'" class="compact-header-title">{{ compactHeaderTitle }}</strong>
      <div v-if="activeSection === 'chat'" class="chat-header-actions">
        <button
          class="history-trigger"
          type="button"
          :aria-expanded="historyOpen"
          aria-label="对话历史"
          title="对话历史"
          @click="toggleHistory"
        >
          <HistoryOutlined />
          <span class="history-trigger-label">对话历史</span>
        </button>
        <button
          class="history-trigger"
          type="button"
          :aria-expanded="memoryOpen"
          aria-label="记忆"
          title="记忆"
          @click="toggleMemory"
        >
          <BulbOutlined />
          <span class="history-trigger-label">记忆</span>
        </button>
        <TaskCollaborationPopover
          :panel="runPanel"
          :conversation-key="taskConversationKey"
          @open-team="onOpenTeam"
        />
        <button
          v-if="MAIN_CHAT_FEATURE_VISIBILITY.workspace"
          class="history-trigger"
          type="button"
          :aria-expanded="workspaceOpen"
          aria-label="工作区"
          title="工作区"
          @click="toggleWorkspace"
        >
          <FolderOpenOutlined />
          <span class="history-trigger-label">工作区</span>
        </button>
      </div>

      <button
        ref="userInfoRef"
        class="user-info"
        type="button"
        aria-haspopup="menu"
        :aria-expanded="userMenuOpen"
        @click="toggleUserMenu"
      >
        <div class="user-avatar">
          <img :src="userAvatarUrl" alt="用户头像" @error="handleUserAvatarError" />
        </div>
        <span class="user-name">{{ userInfo?.realname || '用户' }}</span>
      </button>
      <div v-if="userMenuOpen" ref="userMenuRef" class="user-menu" role="menu" @click.stop>
        <button class="menu-item" type="button" role="menuitem" @click="openProfile">
          <UserOutlined />
          <span>个人资料</span>
        </button>
        <button v-if="hasPermission('admin:manager')" class="menu-item" type="button" role="menuitem" @click="goToAdmin">
          <SettingOutlined />
          <span>管理配置</span>
        </button>
        <button class="menu-item" type="button" role="menuitem" @click="handleLogout">
          <LogoutOutlined />
          <span>退出登录</span>
        </button>
      </div>
    </header>
    <!-- 执行团队（2026-07-27 二期）：右侧伴随面板/全屏全景——实时监视属性，与已删除的
         ArtifactPanel（静态产物预览）定位不同，勿混同（产品定义 §展示容器） -->
    <ExecTeamPanel
      v-if="execTeamOpen"
      :runs="runPanel.subagentRuns"
      :focus-key="execTeamFocus"
      @close="execTeamOpen = false"
      @select-run="onTeamSelectRun"
      @focus-chat="execTeamOpen = false"
    />
    <Transition name="history-backdrop">
      <button
        v-if="historyOpen"
        class="history-backdrop"
        type="button"
        aria-label="关闭对话历史"
        @click="closeHistory"
      ></button>
    </Transition>
    <!-- dialog 语义（审计项 35）：抽屉是模态层——role/aria-modal 让读屏器进入对话框模式，
         Esc 关闭已由全局 handleHistoryKeyboard 承接 -->
    <Transition name="history-popover">
    <aside v-if="historyOpen" class="history-popover" role="dialog" aria-modal="true" aria-label="对话历史">
      <div class="history-popover-heading">
        <strong>对话历史</strong>
        <button type="button" title="关闭" @click="closeHistory">
          <CloseOutlined />
        </button>
      </div>

      <div class="history-search">
        <SearchOutlined />
        <input
          v-model="threadSearch"
          type="text"
          placeholder="搜索对话"
          @input="searchThreads"
        />
      </div>

      <div v-if="threadsLoading" class="history-empty compact">
        <LoadingOutlined />
        <strong>正在加载历史</strong>
      </div>
      <div v-else class="conversation-list">
        <template v-for="group in threadGroups" :key="group.key">
          <div class="conversation-group-label">
            <PushpinFilled v-if="group.key === 'pinned'" />
            <span>{{ group.label }}</span>
          </div>
          <template v-for="item in group.items" :key="item.id">
            <div v-if="renamingId === item.id" class="conversation-item editing">
              <input
                class="conversation-rename-input"
                :value="renamingText"
                maxlength="60"
                @input="renamingText = ($event.target as HTMLInputElement).value"
                @keydown.enter.prevent="confirmRename(item.id)"
                @keydown.esc.stop="cancelRename"
                @blur="confirmRename(item.id)"
              />
            </div>
            <!-- div[role=button] 而非 <button>（审计项 34）：行内还有重命名/置顶/删除三个
                 真按钮，button 嵌 button 是非法结构（读屏器/表单行为都不可靠）；键盘激活
                 由 enter/space 手动接上 -->
            <div
              v-else
              :class="[
                'conversation-item',
                {
                  active: currentThreadId === item.id,
                  running: conversationActivity(item)?.kind === 'running',
                  paused: conversationActivity(item)?.kind === 'paused',
                },
              ]"
              role="button"
              tabindex="0"
              @click="selectHistoryConversation(item.id)"
              @keydown.enter.self.prevent="selectHistoryConversation(item.id)"
              @keydown.space.self.prevent="selectHistoryConversation(item.id)"
            >
              <span
                class="conversation-identity"
                :data-assistant="item.assistant_preset || 'work-agent'"
              >
                <span class="conversation-identity-icon" aria-hidden="true">
                  <img :src="historyAssistantIcon(item.assistant_preset)" alt="" />
                </span>
                <span class="conversation-preset-badge">
                  {{ historyAssistantLabel(item.assistant_preset) }}
                </span>
              </span>
              <span class="conversation-utility">
                <span
                  v-if="conversationActivity(item)"
                  :class="['conversation-meta', conversationActivity(item)?.kind]"
                >
                  <span :class="conversationActivity(item)?.kind === 'running' ? 'running-dot' : 'paused-dot'"></span>
                  {{ conversationActivity(item)?.label }}
                </span>
                <span v-else class="conversation-meta">{{ formatThreadTime(item.updated_at, group.key) }}</span>
                <span class="conversation-actions">
                  <button class="conv-act" type="button" title="重命名" @click.stop="startRename(item)">
                    <EditOutlined />
                  </button>
                  <button
                    class="conv-act"
                    type="button"
                    :class="{ pinned: item.pinned }"
                    :title="item.pinned ? '取消置顶' : '置顶'"
                    @click.stop="togglePin(item.id)"
                  >
                    <PushpinFilled v-if="item.pinned" />
                    <PushpinOutlined v-else />
                  </button>
                  <button class="conv-act danger" type="button" title="删除对话" @click.stop="handleDeleteThread(item.id)">
                    <DeleteOutlined />
                  </button>
                </span>
              </span>
              <span class="conversation-title" :title="item.title">{{ item.title }}</span>
            </div>
          </template>
        </template>
        <button
          v-if="threadHasMore && threadList.length"
          class="history-load-more"
          type="button"
          :disabled="threadsLoading"
          @click="loadMoreThreads"
        >
          {{ threadsLoading ? '加载中…' : '加载更多' }}
        </button>
        <!-- 本地草稿放正式会话之后（D-04）：历史主列表先是「聊过什么」，草稿是次级恢复入口 -->
        <template v-if="draftEntries.length">
          <div class="conversation-group-label drafts">
            <EditOutlined />
            <span>本地草稿 · {{ draftEntries.length }}</span>
          </div>
          <div
            v-for="draft in draftEntries"
            :key="draft.draftId"
            class="conversation-item draft"
            role="button"
            tabindex="0"
            @click="selectHistoryDraft(draft.draftId)"
            @keydown.enter.self.prevent="selectHistoryDraft(draft.draftId)"
            @keydown.space.self.prevent="selectHistoryDraft(draft.draftId)"
          >
            <span class="conversation-identity is-draft">
              <span class="conversation-identity-icon" aria-hidden="true"><EditOutlined /></span>
              <span class="conversation-preset-badge">本地草稿</span>
            </span>
            <span class="conversation-utility">
              <span class="conversation-meta">未发送</span>
              <span class="conversation-actions">
                <button class="conv-act danger" type="button" title="删除草稿" @click.stop="discardDraft(draft.draftId)">
                  <DeleteOutlined />
                </button>
              </span>
            </span>
            <span class="conversation-title" :title="draft.preview">{{ draft.preview }}</span>
          </div>
        </template>
        <div v-if="threadList.length === 0 && draftEntries.length === 0" class="history-empty compact">
          <MessageOutlined />
          <strong>暂无对话历史</strong>
          <span>开始新的对话后会显示在这里</span>
        </div>
      </div>
    </aside>
    </Transition>
    <MemoryDrawer :open="memoryOpen" @close="memoryOpen = false" @error="showError" />
    <ProfileModal
      :open="profileOpen"
      @close="profileOpen = false"
      @saved="handleProfileSaved"
      @error="showError"
    />
    <WorkspaceOverlay
      v-if="MAIN_CHAT_FEATURE_VISIBILITY.workspace"
      :open="workspaceOpen"
      :thread-id="currentThreadId"
      @close="workspaceOpen = false"
      @error="showError"
    />
    <button
      v-if="compactNavOpen"
      class="compact-nav-backdrop"
      type="button"
      aria-label="关闭导航菜单"
      @click="closeCompactNav"
    ></button>
    <aside
      id="center-primary-navigation"
      :class="['primary-nav', { 'compact-open': compactNavOpen }]"
      :role="isCompactShell ? 'dialog' : undefined"
      :aria-modal="isCompactShell ? 'true' : undefined"
      :aria-label="isCompactShell ? '导航菜单' : undefined"
    >
      <div class="brand-row">
        <strong>{{ useGlobSetting().title }}</strong>
        <button
          ref="compactNavCloseRef"
          class="icon-button"
          type="button"
          :title="isCompactShell ? '关闭导航菜单' : (modelPanelCollapsed ? '展开侧边栏' : '收起侧边栏')"
          :aria-label="isCompactShell ? '关闭导航菜单' : (modelPanelCollapsed ? '展开侧边栏' : '收起侧边栏')"
          :aria-expanded="isCompactShell ? undefined : !modelPanelCollapsed"
          @click="isCompactShell ? closeCompactNav() : toggleModelPanel()"
        >
          <CloseOutlined v-if="isCompactShell" />
          <svg
            v-else
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <rect width="18" height="18" x="3" y="3" rx="2" />
            <path d="M9 3v18" />
          </svg>
        </button>
      </div>

      <div class="compact-nav-shortcuts" aria-label="对话工具">
        <button type="button" @click="openCompactHistory">
          <HistoryOutlined />
          <span>对话历史</span>
        </button>
        <button type="button" @click="openCompactMemory">
          <BulbOutlined />
          <span>记忆</span>
        </button>
        <button v-if="MAIN_CHAT_FEATURE_VISIBILITY.workspace" type="button" @click="openCompactWorkspace">
          <FolderOpenOutlined />
          <span>工作区</span>
        </button>
      </div>

      <nav class="nav-stack" aria-label="主导航">
        <template v-for="group in visibleNavGroups" :key="group.key">
          <span v-if="group.label" class="compact-nav-group-label">{{ group.label }}</span>
          <button
            v-for="item in group.items"
            :key="item.key"
            :class="['nav-item', { active: item.key !== 'chat' && activeSection === item.key }]"
            type="button"
            :aria-label="navItemLabel(item)"
            :title="modelPanelCollapsed && !isCompactShell ? item.label : ''"
            @click="onNavItem(item.key)"
          >
            <span class="nav-icon-shell">
              <svg
                v-if="item.key === 'chat'"
                class="nav-icon nav-compose-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z" />
              </svg>
              <component :is="item.icon" v-else class="nav-icon" />
            </span>
            <span class="nav-label">{{ navItemLabel(item) }}</span>
          </button>
        </template>
      </nav>
      <div
        v-show="!modelPanelCollapsed"
        class="nav-resizer"
        role="separator"
        aria-orientation="vertical"
        :aria-valuenow="sidebarWidth"
        :aria-valuemin="CENTER_NAV_MIN"
        :aria-valuemax="CENTER_NAV_MAX"
        tabindex="0"
        aria-label="拖动调整侧边栏宽度"
        @pointerdown="onNavResizeStart"
        @keydown="onNavResizeKey"
      ></div>
    </aside>

    <main class="workspace">
      <router-view v-slot="{ Component }">
        <!-- 对话页不进 keep-alive：热更新后 provide 换了新实例时，缓存的 ChatPage 仍拿着旧 inject，
             侧栏「新对话」清掉新实例后屏幕还停在旧会话。 -->
        <keep-alive :exclude="['CenterChatPage']">
          <component :is="Component" :key="workspacePageKey" />
        </keep-alive>
      </router-view>

      <button
        v-if="activeSection !== 'chat' && chatTaskState !== 'idle'"
        :class="['background-chat-task', { completed: chatTaskState === 'completed' }]"
        type="button"
        @click="switchSection('chat')"
      >
        <span class="background-chat-task-icon">
          <LoadingOutlined v-if="chatTaskState === 'running'" class="nav-task-spinner" />
          <CheckCircleOutlined v-else />
        </span>
        <span>
          <strong>{{ chatTaskState === 'running' ? '正在后台回复' : '回复已完成' }}</strong>
          <em>{{ chatTaskState === 'running' ? '可以继续浏览其他页面' : '点击返回查看' }}</em>
        </span>
      </button>

      <div v-if="errorText" class="toast-error">{{ errorText }}</div>
      <div v-if="noticeText" class="toast-info">{{ noticeText }}</div>
    </main>
  </div>
</template>

<script setup lang="ts">
import {
  AppstoreOutlined,
  CheckCircleOutlined,
  BulbOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  HistoryOutlined,
  LoadingOutlined,
  LogoutOutlined,
  MessageOutlined,
  PushpinFilled,
  PushpinOutlined,
  ReadOutlined,
  SearchOutlined,
  SettingOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons-vue';
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from 'vue';
import { onClickOutside } from '@vueuse/core';
import { useRoute, useRouter } from 'vue-router';
import { useUserStore } from '/@/store/modules/user';
import { useGlobSetting } from '/@/hooks/setting';
import { usePermission } from '/@/hooks/web/usePermission';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';
import type { AgentItem, SubagentItem } from './agentApi';
import { useAgentMarket, type CenterSectionKey } from './composables/useAgentMarket';
import { useCenterChat } from './composables/useCenterChat';
import MemoryDrawer from './components/MemoryDrawer.vue';
import ProfileModal from './components/ProfileModal.vue';
import WorkspaceOverlay from './components/WorkspaceOverlay.vue';
import TaskCollaborationPopover from './components/TaskCollaborationPopover.vue';
import ExecTeamPanel from './components/ExecTeamPanel.vue';
import type { SubagentRun } from './components/MessageList.vue';
import { deriveRunPanel } from './composables/executionTimeline';
import { threadConversationActivity } from './composables/threadConversationActivity';
import { CenterContextKey } from './centerContext';
import { centerRouteNameToSection, centerSectionPath } from './centerRoute';
import { MAIN_CHAT_FEATURE_VISIBILITY } from './mainChatFeatureVisibility';
import {
  CENTER_NAV_MAX,
  CENTER_NAV_MIN,
  CENTER_NAV_STORAGE_KEY,
  clampCenterNavWidth,
  effectiveCenterNavWidth,
  readStoredCenterNavWidth,
} from './utils/centerNavWidth';
import { getBuiltinAssistantByPreset } from './builtinAssistants';
import headerImg from '/@/assets/images/header.jpg';

defineOptions({ name: 'CenterShell' });

function builtinHistoryBadge(preset?: string) {
  return getBuiltinAssistantByPreset(preset)?.shortBadge || '';
}

function historyAssistantLabel(preset?: string) {
  return builtinHistoryBadge(preset) || 'AXIOM Agent';
}

function historyAssistantIcon(preset?: string) {
  return getBuiltinAssistantByPreset(preset)?.icon || '/agent-icons/work-agent-orb.svg';
}

const userStore = useUserStore();
const userInfo = computed(() => userStore.getUserInfo);
const route = useRoute();
const router = useRouter();
const { hasPermission } = usePermission();

const modelPanelCollapsed = ref(false);
const sidebarResizing = ref(false);
const viewportWidth = ref(typeof window === 'undefined' ? 1440 : window.innerWidth);
const COMPACT_SHELL_MAX_WIDTH = 1024;
const isCompactShell = computed(() => viewportWidth.value <= COMPACT_SHELL_MAX_WIDTH);
const compactNavOpen = ref(false);
const compactMenuButtonRef = ref<HTMLButtonElement | null>(null);
const compactNavCloseRef = ref<HTMLButtonElement | null>(null);
const sidebarWidth = ref(
  readStoredCenterNavWidth(
    typeof localStorage === 'undefined' ? null : localStorage.getItem(CENTER_NAV_STORAGE_KEY),
    viewportWidth.value,
  ),
);
const effectiveNavWidth = computed(() =>
  isCompactShell.value ? 0 : effectiveCenterNavWidth({
    viewportWidth: viewportWidth.value,
    collapsed: modelPanelCollapsed.value,
    expandedWidth: sidebarWidth.value,
  }),
);

function persistNavWidth() {
  try {
    localStorage.setItem(CENTER_NAV_STORAGE_KEY, String(sidebarWidth.value));
  } catch {
    /* 隐私模式/配额满：宽度不持久化即可 */
  }
}

function applyNavWidth() {
  if (typeof document === 'undefined') return;
  document.documentElement.style.setProperty('--center-nav-width', `${effectiveNavWidth.value}px`);
}

watch(effectiveNavWidth, applyNavWidth, { immediate: true });

function onNavResizeStart(ev: PointerEvent) {
  if (modelPanelCollapsed.value || isCompactShell.value) return;
  ev.preventDefault();
  sidebarResizing.value = true;
  const handle = ev.currentTarget as HTMLElement;
  handle.setPointerCapture?.(ev.pointerId);
  const startX = ev.clientX;
  const startW = sidebarWidth.value;
  const move = (e: PointerEvent) => {
    sidebarWidth.value = clampCenterNavWidth(startW + (e.clientX - startX), window.innerWidth);
  };
  const up = (e: PointerEvent) => {
    sidebarResizing.value = false;
    persistNavWidth();
    handleAppViewportResize();
    handle.releasePointerCapture?.(e.pointerId);
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

function onNavResizeKey(ev: KeyboardEvent) {
  if (modelPanelCollapsed.value || isCompactShell.value) return;
  if (ev.key === 'ArrowLeft') {
    ev.preventDefault();
    sidebarWidth.value = clampCenterNavWidth(sidebarWidth.value - 16, viewportWidth.value);
    persistNavWidth();
  } else if (ev.key === 'ArrowRight') {
    ev.preventDefault();
    sidebarWidth.value = clampCenterNavWidth(sidebarWidth.value + 16, viewportWidth.value);
    persistNavWidth();
  } else if (ev.key === 'Home') {
    ev.preventDefault();
    sidebarWidth.value = CENTER_NAV_MIN;
    persistNavWidth();
  } else if (ev.key === 'End') {
    ev.preventDefault();
    sidebarWidth.value = clampCenterNavWidth(CENTER_NAV_MAX, viewportWidth.value);
    persistNavWidth();
  }
}

function onCenterViewportResize() {
  viewportWidth.value = window.innerWidth;
  sidebarWidth.value = clampCenterNavWidth(sidebarWidth.value, viewportWidth.value);
  if (!isCompactShell.value) compactNavOpen.value = false;
  handleAppViewportResize();
}
const errorText = ref('');
const noticeText = ref('');
const memoryOpen = ref(false);
const profileOpen = ref(false);
const workspaceOpen = ref(false);
const userMenuOpen = ref(false);
const userInfoRef = ref<HTMLElement | null>(null);
const userMenuRef = ref<HTMLElement | null>(null);
const userAvatarFailed = ref(false);
const userAvatarUrl = computed(() => {
  if (userAvatarFailed.value) return headerImg;
  const avatar = String(userInfo.value?.avatar || '').trim();
  return avatar ? getProxyStaticFileUrl(avatar) : headerImg;
});

watch(
  () => userInfo.value?.avatar,
  () => { userAvatarFailed.value = false; },
);

function handleUserAvatarError() {
  userAvatarFailed.value = true;
}

onClickOutside(userMenuRef, () => { userMenuOpen.value = false; }, { ignore: [userInfoRef] });

// 产品定位（2026-09-19 拍板）：智能体全部由我们定制并预置在广场里，用户不自建。
// 「我的智能体」（自建 / 发布 / 审核）因此不再进导航；路由与代码保留待后续清理。
const navItems = [
  { key: 'chat' as const, label: '主对话', icon: MessageOutlined },
  { key: 'agent' as const, label: '智能体广场', icon: AppstoreOutlined },
  { key: 'knowledge' as const, label: '我的知识库', icon: ReadOutlined },
  { key: 'skill' as const, label: 'Skill广场', icon: ToolOutlined },
  { key: 'files' as const, label: '我的文件', icon: FolderOutlined },
  { key: 'models' as const, label: '模型配置', icon: SettingOutlined },
];

type CenterNavItem = (typeof navItems)[number];
const desktopNavGroups = [{ key: 'desktop', label: '', items: navItems }];
const compactNavGroups = [
  {
    key: 'main',
    label: '开始',
    items: navItems.filter((item) => ['chat', 'agent', 'skill'].includes(item.key)),
  },
  {
    key: 'library',
    label: '我的内容',
    // 手机/iPad 只保留文件入口；智能体与知识库仍保留在桌面端，不删除路由或权限。
    items: navItems.filter((item) => ['files', 'models'].includes(item.key)),
  },
];
const visibleNavGroups = computed(() => (isCompactShell.value ? compactNavGroups : desktopNavGroups));

function navItemLabel(item: CenterNavItem) {
  return isCompactShell.value && item.key === 'chat' ? '新对话' : item.label;
}

function showError(error: unknown) {
  // 错误归一化（任务模式 2.0 §10.5 验收）：对象错误提取 message/detail 字段，
  // 绝不把普通对象 String() 成 "[object Object]" 摆给用户。
  let message: string;
  if (error instanceof Error) {
    message = error.message;
  } else if (typeof error === 'string') {
    message = error;
  } else if (error && typeof error === 'object') {
    const payload = error as { message?: unknown; detail?: unknown; error?: unknown };
    const candidate = payload.message ?? payload.detail ?? payload.error;
    message = typeof candidate === 'string' && candidate.trim() ? candidate : '请求失败，请稍后重试';
  } else {
    message = String(error || '请求失败');
  }
  errorText.value = message;
  window.setTimeout(() => {
    if (errorText.value === message) errorText.value = '';
  }, 3600);
}

function showNotice(message: string) {
  noticeText.value = message;
  window.setTimeout(() => {
    if (noticeText.value === message) noticeText.value = '';
  }, 1800);
}

// activeSection 由当前子路由派生；写入 .value 会触发子路由跳转（供 composable 主动切板块用）
const activeSection = computed<CenterSectionKey>({
  get: () => centerRouteNameToSection[String(route.name || '')] || 'chat',
  set: (section) => switchSection(section),
});
const compactHeaderTitle = computed(() => (
  navItems.find((item) => item.key === activeSection.value)?.label || '主对话'
));

const agentMarket = useAgentMarket({
  activeSection,
  modelPanelCollapsed,
  showError,
});
const chatPageEpoch = ref(0);
const centerChat = useCenterChat({
  activeSection,
  appList: agentMarket.appList,
  reloadApps: agentMarket.reloadApps,
  showError,
  showNotice,
  threadScope: 'ordinary',
  remountChatPage: () => {
    chatPageEpoch.value += 1;
  },
});

const { appList, appLoading, reloadApps, handleAppViewportResize, clearResizeTimer } = agentMarket;
const {
  chatTaskState,
  activeRuns,
  isFinishedRun,
  currentRunPlan,
  chatMessages,
  subagents,
  openSubagent,
  threadList,
  threadsLoading,
  threadHasMore,
  currentThreadId,
  historyOpen,
  threadSearch,
  draftEntries,
  openDraft,
  discardDraft,
  loadMoreThreads,
  searchThreads,
  togglePin,
  loadThread,
  resetChat,
  handleDeleteThread,
  handleRenameThread,
  closeHistory,
  handleHistoryKeyboard,
  clearChatTaskTimer,
} = centerChat;

function toggleHistory() {
  compactNavOpen.value = false;
  workspaceOpen.value = false;
  memoryOpen.value = false;
  const opening = !historyOpen.value;
  historyOpen.value = opening;
  // 「主对话」会把当前窗口切到新对话，但原 Thread/Run 仍在后台执行。
  // 历史抽屉不能继续用切换前的旧列表；每次打开都重拉服务端会话，
  // 让用户不刷新整页也能立即点回刚才的活动会话。
  if (opening) void centerChat.loadThreads();
}

function selectHistoryConversation(threadId: string) {
  // 手机/iPad：先关抽屉，让 0.24s 滑出和会话加载叠在一起；桌面仍等 loadThread 成功后再关，失败可继续点别的会话。
  if (isCompactShell.value) closeHistory();
  void loadThread(threadId);
}

function selectHistoryDraft(draftId: string) {
  if (isCompactShell.value) closeHistory();
  void openDraft(draftId);
}

function toggleMemory() {
  compactNavOpen.value = false;
  historyOpen.value = false;
  workspaceOpen.value = false;
  memoryOpen.value = !memoryOpen.value;
}

function toggleWorkspace() {
  compactNavOpen.value = false;
  historyOpen.value = false;
  memoryOpen.value = false;
  workspaceOpen.value = !workspaceOpen.value;
}

function onNavItem(section: CenterSectionKey) {
  compactNavOpen.value = false;
  if (section === 'chat') {
    openNewChat();
    return;
  }
  switchSection(section);
}

function toggleCompactNav() {
  if (!isCompactShell.value) return;
  compactNavOpen.value = !compactNavOpen.value;
  if (compactNavOpen.value) {
    historyOpen.value = false;
    memoryOpen.value = false;
    workspaceOpen.value = false;
    nextTick(() => compactNavCloseRef.value?.focus());
  }
}

function closeCompactNav() {
  if (!compactNavOpen.value) return;
  compactNavOpen.value = false;
  nextTick(() => compactMenuButtonRef.value?.focus());
}

function openCompactHistory() {
  compactNavOpen.value = false;
  toggleHistory();
}

function openCompactMemory() {
  compactNavOpen.value = false;
  toggleMemory();
}

function openCompactWorkspace() {
  compactNavOpen.value = false;
  toggleWorkspace();
}

function handleCompactNavKeyboard(event: KeyboardEvent) {
  if (event.key === 'Escape' && compactNavOpen.value) closeCompactNav();
}

const workspacePageKey = computed(() =>
  activeSection.value === 'chat' ? `chat-${chatPageEpoch.value}` : String(route.name || activeSection.value),
);

function openNewChat() {
  closeHistory();
  errorText.value = '';
  // 与原先顶栏「新建对话」相同：当前页 resetChat 回到欢迎屏，不刷新、不开新标签。
  resetChat();
  chatPageEpoch.value += 1;
  if (activeSection.value !== 'chat') {
    router.push(centerSectionPath.chat).catch(() => {});
  }
}

// Run 级计划卡只读 Harness 的完整计划快照；工具/委派细节仍从消息时间线投影。
const runPanel = computed(() => {
  const base = deriveRunPanel(chatMessages.value);
  const plan = currentRunPlan.value;
  if (!plan) return base;
  return {
    ...base,
    plan: plan.steps.map((step, index) => ({
      key: String(step.key || `plan-${index}`),
      title: step.title,
      status: step.status,
      detail: step.detail,
      acceptance: step.acceptance,
      blocked: Boolean(step.blocked),
    })),
    goalContract: plan.goal_contract || base.goalContract,
  };
});
const taskConversationKey = computed(() => {
  const first = chatMessages.value[0]?.id || 0;
  const last = chatMessages.value[chatMessages.value.length - 1]?.id || 0;
  return currentThreadId.value || `${first}-${last}`;
});

// 执行团队全景（2026-07-27 二期）：面板分区标题/成员行 → 打开伴随面板（可升全屏）；
// 全景里点成员卡才下钻 @ 窗（先看位置再深潜，面板保持打开作返程锚点）。
const execTeamOpen = ref(false);
const execTeamFocus = ref('');

function onOpenTeam(runKey?: string) {
  execTeamFocus.value = runKey || '';
  execTeamOpen.value = true;
}

// 换会话/新建对话即关面板：团队是「本轮」的，留着上一轮的窗（或空态窗）只会误导
watch(taskConversationKey, () => {
  execTeamOpen.value = false;
  execTeamFocus.value = '';
});

function onTeamSelectRun(run: SubagentRun) {
  onSelectRun(run);
}

// 点任务面板里的子智能体 → 打开该次委派的悬浮过程窗，不改写 composer 的 @ 目标。
// 只按 id 精确匹配：按 name 兜底在重名场景下会打开完全无关的另一个子智能体（不同 id/历史/能力）。
function onSelectRun(run: SubagentRun) {
  const match = run.id ? subagents.value.find((s) => s.id === run.id) : null;
  const id = match?.id || run.id;
  if (!id) {
    showNotice('无法定位该子智能体，请先在「我的智能体」中确认它已发布');
    return;
  }
  openSubagent(match || ({ id, name: run.name } as SubagentItem), run.runKey);
}

// 会话重命名：抽屉内联编辑，Enter/失焦保存、Esc 取消
const renamingId = ref('');
const renamingText = ref('');

function startRename(item: { id: string; title: string }) {
  renamingId.value = item.id;
  renamingText.value = item.title || '';
  nextTick(() => {
    const el = document.querySelector<HTMLInputElement>('.conversation-rename-input');
    el?.focus();
    el?.select();
  });
}

function cancelRename() {
  renamingId.value = '';
  renamingText.value = '';
}

async function confirmRename(threadId: string) {
  if (renamingId.value !== threadId) return; // Esc 已取消时，随后的 blur 不再保存
  const text = renamingText.value.trim();
  renamingId.value = '';
  const current = threadList.value.find((t) => t.id === threadId);
  if (text && text !== current?.title) await handleRenameThread(threadId, text);
  renamingText.value = '';
}

function startOfDay(x: Date) {
  return new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
}

// 分组标题已经说明“今天 / 昨天 / 过去 7 天”，组内只显示钟点，避免右侧重复文案挤压会话标题。
function formatThreadTime(iso?: string, groupKey = ''): string {
  if (!iso) return '';
  const d = new Date(iso.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return iso;
  const now = new Date();
  const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000);
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  if (diffMin < 1) return '刚刚';
  if (groupKey === 'today' && diffMin < 60) return `${diffMin} 分钟前`;
  if (groupKey === 'today' || groupKey === 'yesterday' || groupKey === 'week') return hhmm;
  const dayDiff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (dayDiff <= 0) return hhmm;
  if (dayDiff === 1) return `昨天 ${hhmm}`;
  if (dayDiff < 7) return `星期${'日一二三四五六'[d.getDay()]} ${hhmm}`;
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
}

// 会话历史按时间分组（置顶优先，其余按 今天/昨天/过去7天/更早），更像日常聊天应用
const threadGroups = computed(() => {
  const now = new Date();
  const pinned: typeof threadList.value = [];
  const today: typeof threadList.value = [];
  const yesterday: typeof threadList.value = [];
  const week: typeof threadList.value = [];
  const earlier: typeof threadList.value = [];
  for (const t of threadList.value) {
    if (t.pinned) {
      pinned.push(t);
      continue;
    }
    const d = t.updated_at ? new Date(t.updated_at.replace(' ', 'T')) : null;
    if (!d || Number.isNaN(d.getTime())) {
      earlier.push(t);
      continue;
    }
    const dayDiff = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
    if (dayDiff <= 0) today.push(t);
    else if (dayDiff === 1) yesterday.push(t);
    else if (dayDiff < 7) week.push(t);
    else earlier.push(t);
  }
  const groups: { key: string; label: string; items: typeof threadList.value }[] = [];
  if (pinned.length) groups.push({ key: 'pinned', label: '置顶', items: pinned });
  if (today.length) groups.push({ key: 'today', label: '今天', items: today });
  if (yesterday.length) groups.push({ key: 'yesterday', label: '昨天', items: yesterday });
  if (week.length) groups.push({ key: 'week', label: '过去 7 天', items: week });
  if (earlier.length) groups.push({ key: 'earlier', label: '更早', items: earlier });
  return groups;
});

function conversationActivity(item: {
  id: string;
  active_run?: { id?: string; status?: string; interactive_type?: string } | null;
}) {
  return threadConversationActivity(activeRuns.value[item.id], item.active_run, isFinishedRun);
}

function toggleModelPanel() {
  modelPanelCollapsed.value = !modelPanelCollapsed.value;
  handleAppViewportResize();
}

function switchSection(section: CenterSectionKey) {
  closeHistory();
  errorText.value = '';
  if (centerSectionPath[section] && section !== activeSection.value) {
    router.push(centerSectionPath[section]).catch(() => {});
  }
}

function toggleUserMenu() {
  userMenuOpen.value = !userMenuOpen.value;
}

function openProfile() {
  userMenuOpen.value = false;
  historyOpen.value = false;
  memoryOpen.value = false;
  workspaceOpen.value = false;
  profileOpen.value = true;
}

function handleProfileSaved() {
  showNotice('个人资料已更新');
  // 广场创建人显示与 sys_user 同源；保存资料后刷新目录增强字段，避免停留在旧头像/旧名称。
  void reloadApps();
}

function goToAdmin() {
  userMenuOpen.value = false;
  // 同标签页跳转而非 window.open：新开标签页没有历史，管理页里的「返回」
  // 就无处可回，用户只能手动关标签页。
  void router.push('/admin');
}

function handleLogout() {
  userMenuOpen.value = false;
  userStore.confirmLoginOut();
}

function startChatWithAgent(agent: AgentItem) {
  agentMarket.startChatWithAgent(agent);
}

function openAgentFromChat(app: any) {
  agentMarket.openAgentFromChat(app);
}

// 板块级懒加载：覆盖导航点击 / 浏览器前进后退 / 直达 URL 三种进入方式
watch(
  () => activeSection.value,
  (section) => {
    if (section === 'agent' && !appList.value.length && !appLoading.value) reloadApps();
  },
  { immediate: false },
);

provide(CenterContextKey, {
  agentMarket,
  centerChat,
  activeSection,
  showError,
  showNotice,
  switchSection,
  startChatWithAgent,
  openAgentFromChat,
});

// 深层子组件（MessageList 记忆 chip）打开记忆抽屉：字符串键避免导入环
provide('centerOpenMemory', () => { memoryOpen.value = true; });

onMounted(() => {
  window.addEventListener('keydown', handleHistoryKeyboard);
  window.addEventListener('keydown', handleCompactNavKeyboard);
  window.addEventListener('resize', onCenterViewportResize);
  applyNavWidth();
  reloadApps();
  // 只预加载对话历史。不要自动打开上次会话，否则从子智能体/其他板块回到 /center 会被拽进最近一轮。
  void centerChat.restoreCurrentThread();
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleHistoryKeyboard);
  window.removeEventListener('keydown', handleCompactNavKeyboard);
  window.removeEventListener('resize', onCenterViewportResize);
  document.documentElement.style.removeProperty('--center-nav-width');
  clearResizeTimer();
  clearChatTaskTimer();
});
</script>

<style lang="less">
/* 共享 Less 通过各自的 SFC 样式入口编译，避免 style src 的描述符跨页面覆盖。 */
@import './styles/centerNew.less';
</style>
