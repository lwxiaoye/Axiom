<template>
  <template v-if="open">
    <button
      class="mem-backdrop"
      type="button"
      aria-label="关闭记忆管理"
      @click="$emit('close')"
    ></button>
    <aside class="mem-popover" role="dialog" aria-label="记忆管理">
      <div class="mem-heading">
        <strong>记忆</strong>
        <button type="button" title="关闭" @click="$emit('close')">
          <CloseOutlined />
        </button>
      </div>

      <!-- 总开关（文案对齐 ChatGPT：定制专属使用体验） -->
      <div v-if="available" class="mem-toggle-row">
        <div class="mem-toggle-text">
          <strong>启用记忆</strong>
          <span>让对话助手依据聊天记录与文件，为你定制专属使用体验。关闭后不再记录新信息，也不在对话中使用已有记忆。</span>
        </div>
        <button
          class="mem-switch"
          type="button"
          role="switch"
          :aria-checked="enabled"
          :class="{ on: enabled }"
          :disabled="savingToggle"
          @click="toggleEnabled"
        >
          <span class="mem-switch-knob"></span>
        </button>
      </div>

      <!-- 不可用（Runtime 未配置） -->
      <div v-if="!available && !loading" class="mem-empty">
        <InboxOutlined />
        <strong>记忆功能未启用</strong>
        <span>当前环境未配置记忆存储，暂无法使用。</span>
      </div>

      <div v-else-if="available && !enabled && !loading" class="mem-empty compact">
        <span>记忆已关闭。开启后可查看与管理已记录的内容。</span>
      </div>

      <template v-if="available && enabled">
        <!-- 页签：记忆库 / 个性化 -->
        <div class="mem-tabs" role="tablist">
          <button
            v-for="t in TABS"
            :key="t.key"
            type="button"
            role="tab"
            class="mem-tab"
            :class="{ active: activeTab === t.key }"
            :aria-selected="activeTab === t.key"
            @click="activeTab = t.key"
          >
            {{ t.label }}
          </button>
        </div>

        <!-- ===== 记忆库 ===== -->
        <template v-if="activeTab === 'library'">
          <div class="mem-library-intro">
            <span>长期记忆</span>
            <span>{{ displayableItems.length }} 条，会用于后续对话</span>
          </div>

          <!-- 工具栏：搜索 + 排序 + 更多（自动管理/删除全部收进菜单，页面保持干净） -->
          <div class="mem-toolbar">
            <div class="mem-search">
              <SearchOutlined />
              <input
                v-model="searchQuery"
                type="text"
                placeholder="搜索记忆"
                aria-label="搜索记忆"
              />
              <button v-if="searchQuery" type="button" title="清空搜索" @click="searchQuery = ''">
                <CloseOutlined />
              </button>
            </div>
            <button
              type="button"
              class="mem-tool-btn"
              :title="sortDesc ? '按时间排序：最新优先' : '按时间排序：最早优先'"
              :aria-label="sortDesc ? '按时间排序：最新优先' : '按时间排序：最早优先'"
              @click="sortDesc = !sortDesc"
            >
              <SortDescendingOutlined v-if="sortDesc" />
              <SortAscendingOutlined v-else />
            </button>
            <div class="mem-more">
              <button
                type="button"
                class="mem-tool-btn"
                :class="{ open: moreOpen }"
                title="更多"
                @click="moreOpen = !moreOpen"
              >
                <EllipsisOutlined />
              </button>
              <template v-if="moreOpen">
                <button class="mem-more-mask" type="button" aria-label="关闭菜单" @click="moreOpen = false"></button>
                <div class="mem-more-menu">
                  <label class="mem-more-item" title="关闭后对话结束不再自动记录，你仍可手动添加或让助手「记住…」">
                    <span>自动管理</span>
                    <button
                      class="mem-switch small"
                      type="button"
                      role="switch"
                      :aria-checked="autoManage"
                      :class="{ on: autoManage }"
                      :disabled="savingAutoManage"
                      @click="toggleAutoManage"
                    >
                      <span class="mem-switch-knob"></span>
                    </button>
                  </label>
                  <a-popconfirm
                    title="删除全部记忆？此操作不可恢复。"
                    ok-text="全部删除"
                    cancel-text="取消"
                    :ok-button-props="{ danger: true }"
                    @confirm="handleDeleteAll"
                  >
                    <button type="button" class="mem-more-item danger" :disabled="deletingAll || items.length === 0">
                      删除全部记忆
                    </button>
                  </a-popconfirm>
                </div>
              </template>
            </div>
          </div>

          <!-- 类型筛选：安静的文字筛选，不抢列表 -->
          <div class="mem-chips" role="group" aria-label="按类型筛选">
            <button
              type="button"
              class="mem-chip"
              :class="{ active: activeType === '' }"
              @click="activeType = ''"
            >
              全部
            </button>
            <button
              v-for="t in TYPE_OPTIONS"
              :key="t.value"
              type="button"
              class="mem-chip"
              :class="{ active: activeType === t.value }"
              @click="activeType = activeType === t.value ? '' : t.value"
            >
              {{ t.label }}
            </button>
          </div>

          <div v-if="loading" class="mem-empty compact">
            <LoadingOutlined />
            <strong>正在加载记忆</strong>
          </div>
          <div v-else-if="visibleItems.length === 0" class="mem-empty compact">
            <InboxOutlined />
            <strong>{{ searchQuery || activeType ? '没有匹配的记忆' : '还没有记忆' }}</strong>
            <span v-if="!searchQuery && !activeType">随着对话进行，稳定的偏好和信息会自动出现在这里。</span>
          </div>
          <ul v-else class="mem-list">
            <li v-for="m in visibleItems" :key="m.id" class="mem-item" :class="{ editing: editingId === m.id }">
              <span class="mem-badge">{{ typeLabel(m.type) }}</span>
              <template v-if="editingId === m.id">
                <input
                  :ref="(el) => el && (editInputEl = el as HTMLInputElement)"
                  v-model="editText"
                  class="mem-input mem-edit-input"
                  type="text"
                  maxlength="200"
                  :disabled="savingEdit"
                  @keydown.enter.prevent="saveEdit(m)"
                  @keydown.esc.stop.prevent="cancelEdit"
                />
                <button class="mem-op ok" type="button" title="保存（Enter）" :disabled="savingEdit || !editText.trim()" @click="saveEdit(m)">
                  <LoadingOutlined v-if="savingEdit" />
                  <CheckOutlined v-else />
                </button>
                <button class="mem-op" type="button" title="取消（Esc）" :disabled="savingEdit" @click="cancelEdit">
                  <CloseOutlined />
                </button>
              </template>
              <template v-else>
                <span class="mem-content">
                  {{ m.content }}
                  <time v-if="m.updated_at" class="mem-time">{{ fmtTime(m.updated_at) }}</time>
                </span>
                <button class="mem-op hover-only" type="button" title="编辑这条记忆" @click="startEdit(m)">
                  <EditOutlined />
                </button>
                <button
                  class="mem-op hover-only del"
                  type="button"
                  title="删除这条记忆"
                  :disabled="deletingId === m.id"
                  @click="handleDelete(m)"
                >
                  <DeleteOutlined />
                </button>
              </template>
            </li>
          </ul>

          <!-- 添加：默认一个安静入口，点开才展开表单 -->
          <div class="mem-add-zone">
            <button v-if="!addOpen" type="button" class="mem-add-entry" @click="openAdd">
              <PlusOutlined />
              添加记忆
            </button>
            <div v-else class="mem-add-row">
              <select v-model="draftType" class="mem-select" aria-label="记忆类型">
                <option v-for="t in TYPE_OPTIONS" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select>
              <input
                :ref="(el) => el && (addInputEl = el as HTMLInputElement)"
                v-model="draftContent"
                class="mem-input"
                type="text"
                maxlength="200"
                placeholder="如「回答请用中文」"
                @keydown.enter.prevent="handleAdd"
                @keydown.esc.stop.prevent="closeAdd"
              />
              <button
                class="mem-add-btn"
                type="button"
                title="添加（Enter）"
                :disabled="!draftContent.trim() || adding"
                @click="handleAdd"
              >
                <LoadingOutlined v-if="adding" />
                <PlusOutlined v-else />
              </button>
              <button class="mem-op cancel-add" type="button" title="收起（Esc）" @click="closeAdd">
                <CloseOutlined />
              </button>
            </div>
            <span v-if="addError" class="mem-add-error">{{ addError }}</span>
          </div>
        </template>

        <!-- ===== 个性化（关于你 + 自定义指令） ===== -->
        <template v-else>
          <div class="mem-profile">
            <label class="mem-field">
              <span>昵称</span>
              <input v-model="profile.nickname" class="mem-input" type="text" maxlength="40" placeholder="助手应该怎么称呼你？" />
            </label>
            <label class="mem-field">
              <span>职业</span>
              <input v-model="profile.occupation" class="mem-input" type="text" maxlength="60" placeholder="如：产品经理 / 后端工程师" />
            </label>
            <label class="mem-field">
              <span>你的详情 <i class="mem-charcount">{{ profile.about.length }}/600</i></span>
              <textarea v-model="profile.about" class="mem-textarea" rows="3" maxlength="600" placeholder="需要记住的兴趣、价值观或偏好"></textarea>
            </label>
            <label class="mem-field">
              <span>自定义指令 <i class="mem-charcount">{{ profile.customInstructions.length }}/1500</i></span>
              <textarea v-model="profile.customInstructions" class="mem-textarea" rows="4" maxlength="1500" placeholder="其他行为、风格和语调偏好设置"></textarea>
            </label>
            <div class="mem-profile-foot">
              <span v-if="profileFeedback" class="mem-profile-saved">{{ profileFeedback }}</span>
              <span v-else-if="profileDirty" class="mem-profile-dirty">有未保存的修改</span>
              <button
                type="button"
                class="mem-primary-btn"
                :disabled="savingProfile || !profileDirty"
                @click="handleSaveProfile"
              >
                {{ savingProfile ? '保存中…' : '保存' }}
              </button>
            </div>
          </div>
        </template>
      </template>
    </aside>
  </template>
</template>

<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue';
import {
  CheckOutlined,
  CloseOutlined,
  DeleteOutlined,
  EditOutlined,
  EllipsisOutlined,
  InboxOutlined,
  LoadingOutlined,
  PlusOutlined,
  SearchOutlined,
  SortAscendingOutlined,
  SortDescendingOutlined,
} from '@ant-design/icons-vue';
import {
  addMemory,
  deleteAllMemories,
  deleteMemory,
  getMemorySettings,
  getPersonalization,
  listMemories,
  savePersonalization,
  setMemorySettings,
  updateMemory,
  type UserMemoryItem,
} from '../agentApi';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: 'close'): void; (e: 'error', msg: string): void }>();

const TABS = [
  { key: 'library', label: '记忆库' },
  { key: 'profile', label: '个性化' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

const TYPE_OPTIONS = [
  { value: 'preference', label: '偏好' },
  { value: 'fact', label: '事实' },
  { value: 'skills', label: '常用技能' },
  { value: 'interests', label: '兴趣' },
  { value: 'work_info', label: '工作信息' },
  { value: 'context', label: '项目' },
];
const TYPE_LABELS: Record<string, string> = Object.fromEntries(
  TYPE_OPTIONS.map((t) => [t.value, t.label]),
);
function typeLabel(t: string): string {
  return TYPE_LABELS[t] || t;
}

const available = ref(true);
const enabled = ref(true);
const loading = ref(false);
const savingToggle = ref(false);
const items = ref<UserMemoryItem[]>([]);
const activeTab = ref<TabKey>('library');

// ---- 记忆库 ----
const searchQuery = ref('');
const activeType = ref('');
const sortDesc = ref(true);
const moreOpen = ref(false);
const addOpen = ref(false);
const draftType = ref('preference');
const draftContent = ref('');
const adding = ref(false);
const addError = ref('');
const deletingId = ref('');
const deletingAll = ref(false);
const editingId = ref('');
const editText = ref('');
const savingEdit = ref(false);
const editInputEl = ref<HTMLInputElement | null>(null);
const addInputEl = ref<HTMLInputElement | null>(null);

/**
 * 任务终局的结构化复盘会暂存在同一张表里，供相似任务的模型调用。
 * 它不是用户写下的长期记忆，且包含 run 等内部标识；管理抽屉只展示用户
 * 能读懂、能主动维护的内容，避免把运行日志伪装成个人资料。
 */
function isUserFacingMemory(memory: UserMemoryItem): boolean {
  return !memory.content.trim().startsWith('[task_lesson]');
}

const displayableItems = computed(() => items.value.filter(isUserFacingMemory));

const visibleItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  let list = displayableItems.value.filter(
    (m) =>
      (!activeType.value || m.type === activeType.value) &&
      (!q || m.content.toLowerCase().includes(q)),
  );
  if (!sortDesc.value) list = [...list].reverse();
  return list;
});

/** 后端 updated_at 是不带时区的 UTC 裸串（PG naive datetime isoformat），
 *  直接 new Date() 会被按本地时区解析、在 UTC+8 显示成"8 小时前"——补 Z 按 UTC 解析 */
function parseTs(iso: string): number {
  if (!iso) return NaN;
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  return new Date(hasTz ? iso : `${iso}Z`).getTime();
}

function fmtTime(iso: string): string {
  const ts = parseTs(iso);
  if (Number.isNaN(ts)) return '';
  const diffMin = Math.floor((Date.now() - ts) / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return `${diffMin} 分钟前`;
  if (diffMin < 24 * 60) return `${Math.floor(diffMin / 60)} 小时前`;
  const d = new Date(ts);
  const days = Math.floor(diffMin / (24 * 60));
  if (days < 7) return `${days} 天前`;
  return `${d.getMonth() + 1}-${d.getDate()}`;
}

// ---- 个性化 ----
const autoManage = ref(true);
const savingAutoManage = ref(false);
const profile = reactive({ nickname: '', occupation: '', about: '', customInstructions: '' });
const profileSnapshot = ref('');
const savingProfile = ref(false);
const profileFeedback = ref('');

const profileDirty = computed(() => JSON.stringify(profile) !== profileSnapshot.value);

function snapshotProfile() {
  profileSnapshot.value = JSON.stringify(profile);
}

async function refresh() {
  loading.value = true;
  addError.value = '';
  try {
    const settings = await getMemorySettings();
    available.value = settings.available;
    enabled.value = settings.enabled;
    if (available.value && enabled.value) {
      const [res, conf] = await Promise.all([listMemories(), getPersonalization()]);
      items.value = res.items;
      autoManage.value = conf.autoManage;
      profile.nickname = conf.nickname;
      profile.occupation = conf.occupation;
      profile.about = conf.about;
      profile.customInstructions = conf.customInstructions;
      snapshotProfile();
    } else {
      items.value = [];
    }
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '加载记忆失败');
  } finally {
    loading.value = false;
  }
}

async function refreshItems() {
  const res = await listMemories();
  items.value = res.items;
}

async function toggleEnabled() {
  if (savingToggle.value) return;
  const next = !enabled.value;
  savingToggle.value = true;
  try {
    enabled.value = await setMemorySettings(next);
    if (enabled.value) await refresh();
    else items.value = [];
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '切换记忆开关失败');
  } finally {
    savingToggle.value = false;
  }
}

async function toggleAutoManage() {
  if (savingAutoManage.value) return;
  const next = !autoManage.value;
  savingAutoManage.value = true;
  try {
    await savePersonalization({ autoManage: next });
    autoManage.value = next;
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '切换自动管理失败');
  } finally {
    savingAutoManage.value = false;
  }
}

function openAdd() {
  addOpen.value = true;
  addError.value = '';
  nextTick(() => addInputEl.value?.focus());
}

function closeAdd() {
  addOpen.value = false;
  draftContent.value = '';
  addError.value = '';
}

async function handleAdd() {
  const content = draftContent.value.trim();
  if (!content || adding.value) return;
  adding.value = true;
  addError.value = '';
  try {
    await addMemory(draftType.value, content);
    draftContent.value = '';
    await refreshItems();
  } catch (e) {
    // 后端 400（敏感/非法）会走到这里：内联提示，不弹全局错误
    addError.value = e instanceof Error ? e.message : '添加失败';
  } finally {
    adding.value = false;
  }
}

async function handleDelete(m: UserMemoryItem) {
  if (deletingId.value) return;
  deletingId.value = m.id;
  try {
    await deleteMemory(m.id);
    items.value = items.value.filter((x) => x.id !== m.id);
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '删除失败');
  } finally {
    deletingId.value = '';
  }
}

async function handleDeleteAll() {
  if (deletingAll.value) return;
  deletingAll.value = true;
  try {
    await deleteAllMemories();
    items.value = [];
    moreOpen.value = false;
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '删除失败');
  } finally {
    deletingAll.value = false;
  }
}

function startEdit(m: UserMemoryItem) {
  editingId.value = m.id;
  editText.value = m.content;
  nextTick(() => editInputEl.value?.focus());
}

function cancelEdit() {
  editingId.value = '';
  editText.value = '';
}

async function saveEdit(m: UserMemoryItem) {
  const text = editText.value.trim();
  if (!text || savingEdit.value) return;
  if (text === m.content) {
    cancelEdit();
    return;
  }
  savingEdit.value = true;
  try {
    // 真 PUT 原地改。此前的「先加后删」在编辑文本与原文相似时（小编辑的常态）会因
    // 语义去重命中旧条返回旧 id、随后删除同一条 → 整条记忆消失，故弃用。
    await updateMemory(m.id, m.type, text);
    cancelEdit();
    await refreshItems();
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '编辑失败');
  } finally {
    savingEdit.value = false;
  }
}

async function handleSaveProfile() {
  if (savingProfile.value || !profileDirty.value) return;
  savingProfile.value = true;
  profileFeedback.value = '';
  try {
    await savePersonalization({
      nickname: profile.nickname,
      occupation: profile.occupation,
      about: profile.about,
      customInstructions: profile.customInstructions,
    });
    snapshotProfile();
    profileFeedback.value = '已保存';
    window.setTimeout(() => (profileFeedback.value = ''), 2000);
  } catch (e) {
    emit('error', e instanceof Error ? e.message : '保存失败');
  } finally {
    savingProfile.value = false;
  }
}

// 打开时刷新；Esc 关闭
watch(
  () => props.open,
  (open) => {
    if (open) {
      refresh();
      window.addEventListener('keydown', onKeydown);
    } else {
      window.removeEventListener('keydown', onKeydown);
      addError.value = '';
      draftContent.value = '';
      searchQuery.value = '';
      activeType.value = '';
      moreOpen.value = false;
      addOpen.value = false;
      cancelEdit();
    }
  },
);

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    if (moreOpen.value) moreOpen.value = false;
    else if (editingId.value) cancelEdit();
    else if (addOpen.value) closeAdd();
    else emit('close');
  }
}
</script>

<style scoped lang="less">
.mem-backdrop {
  position: fixed;
  z-index: 2999;
  top: 64px;
  right: 0;
  bottom: 0;
  left: var(--center-nav-width, 280px);
  border: 0;
  background: rgba(17, 24, 39, 0.12);
  cursor: default;
  backdrop-filter: blur(1.5px);
  animation: mem-backdrop-enter 0.18s ease both;
}

.mem-popover {
  position: fixed;
  z-index: 3001;
  top: 64px;
  bottom: 0;
  left: var(--center-nav-width, 280px);
  display: flex;
  width: 400px;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #e1e2e7;
  border-bottom: 0;
  border-radius: 0 16px 0 0;
  background: #fff;
  box-shadow: 18px 0 48px rgba(15, 23, 42, 0.12);
  padding: 20px;
  animation: mem-popover-enter 0.2s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.mem-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: #202228;
  font-size: 14px;
}

.mem-heading strong {
  font-size: 15px;
}

.mem-heading button {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #777d88;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.mem-heading button:hover {
  background: #f0f0f2;
  color: #111;
}

.mem-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid #ececef;
  border-radius: 10px;
  background: #fafafb;
}

.mem-toggle-text {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.mem-toggle-text strong {
  color: #202228;
  font-size: 13px;
}

.mem-toggle-text span {
  color: #9098a3;
  font-size: 11px;
  line-height: 1.5;
}

.mem-switch {
  position: relative;
  width: 40px;
  height: 22px;
  flex-shrink: 0;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: #d5d7dd;
  cursor: pointer;
  transition: background 0.2s ease;
}

.mem-switch.on {
  background: #202228;
}

.mem-switch:disabled {
  cursor: default;
  opacity: 0.6;
}

.mem-switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform 0.2s cubic-bezier(0.22, 1, 0.36, 1);
}

.mem-switch.on .mem-switch-knob {
  transform: translateX(18px);
}

.mem-switch.small {
  width: 32px;
  height: 18px;

  .mem-switch-knob {
    width: 14px;
    height: 14px;
  }

  &.on .mem-switch-knob {
    transform: translateX(14px);
  }
}

/* 页签 */
.mem-tabs {
  display: flex;
  gap: 4px;
  padding: 3px;
  margin-bottom: 14px;
  border-radius: 10px;
  background: #f0f0f2;
}

.mem-library-intro {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: 0 2px 9px;

  > span:first-child {
    color: #202228;
    font-size: 13px;
    font-weight: 600;
  }

  > span:last-child {
    color: #9098a3;
    font-size: 11px;
  }
}

.mem-tab {
  flex: 1;
  padding: 6px 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #62687a;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;

  &.active {
    background: #fff;
    color: #202228;
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  }
}

/* 横向行里的输入框才需要 flex 撑满剩余宽度 */
.mem-add-row .mem-input,
.mem-item .mem-edit-input {
  min-width: 0;
  flex: 1 1 auto;
}

.mem-primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 16px;
  border: 0;
  border-radius: 9px;
  background: #202228;
  color: #fff;
  font-size: 12.5px;
  cursor: pointer;
  transition: opacity 0.15s ease;

  &:hover {
    opacity: 0.88;
  }

  &:disabled {
    cursor: default;
    opacity: 0.5;
  }
}

/* 工具栏：搜索 + 排序 + 更多 */
.mem-toolbar {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
  margin-bottom: 10px;
}

.mem-search {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 8px;
  height: 38px;
  padding: 0 11px;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #7b8494;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  input {
    min-width: 0;
    flex: 1;
    border: 0;
    background: transparent;
    color: #111827;
    font-size: 13px;
    outline: none;
  }

  button {
    display: grid;
    width: 20px;
    height: 20px;
    place-items: center;
    border: 0;
    border-radius: 5px;
    background: transparent;
    color: #b0b4bd;
    cursor: pointer;

    &:hover {
      background: #f0f0f2;
      color: #62687a;
    }
  }

  &:focus-within {
    border-color: #818cf8;
    box-shadow: none;
  }
}

.mem-tool-btn {
  display: grid;
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  place-items: center;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #7b8494;
  font-size: 14px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;

  &:hover,
  &.open {
    border-color: #d1d5db;
    background: #fafafb;
    color: #111827;
  }
}

/* ⋯ 菜单 */
.mem-more {
  position: relative;
}

.mem-more-mask {
  position: fixed;
  z-index: 3002;
  inset: 0;
  border: 0;
  background: transparent;
  cursor: default;
}

.mem-more-menu {
  position: absolute;
  z-index: 3003;
  top: 44px;
  right: 0;
  min-width: 176px;
  padding: 6px;
  border: 1px solid #e8e8ec;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.14);
  animation: mem-menu-enter 0.16s ease both;
}

.mem-more-item {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #374151;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;

  &:hover {
    background: #f5f5f7;
  }

  &.danger {
    color: #d4380d;

    &:hover {
      background: #fbeceb;
    }

    &:disabled {
      cursor: default;
      opacity: 0.5;
    }
  }
}

@keyframes mem-menu-enter {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 类型筛选：安静的文字筛选 */
.mem-chips {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
  margin-bottom: 10px;
}

.mem-chip {
  padding: 4px 8px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: #7b8494;
  font-size: 11.5px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;

  &:hover {
    background: #f5f5f7;
    color: #111827;
  }

  &.active {
    background: #202228;
    color: #fff;
  }
}

/* 添加区：安静入口 → 展开表单 */
.mem-add-zone {
  flex-shrink: 0;
  padding-top: 10px;
  margin-top: 8px;
  border-top: 1px solid #f1f1f3;
}

.mem-add-entry {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 0;
  border: 1px dashed #dcdee3;
  border-radius: 10px;
  background: transparent;
  color: #7b8494;
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;

  &:hover {
    border-color: #b6bac2;
    background: #fafafb;
    color: #111827;
  }
}

.mem-add-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px;
  border: 1px solid #e8e8ec;
  border-radius: 12px;
  background: #fafafb;
}

.mem-select {
  height: 34px;
  flex-shrink: 0;
  width: 72px;
  padding: 0 6px;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #202228;
  font-size: 12px;
  cursor: pointer;
  outline: none;

  &:focus {
    border-color: #818cf8;
    box-shadow: none;
  }
}

/* 注意：不要在基类上加 flex——个性化字段是纵向 flex 容器，flex-basis:0 会把高度压塌 */
.mem-input {
  height: 38px;
  padding: 0 11px;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #111827;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &::placeholder {
    color: #a6adba;
  }

  &:focus {
    border-color: #818cf8;
    box-shadow: none;
  }
}

.mem-textarea {
  width: 100%;
  padding: 9px 11px;
  border: 1px solid #e8e8ec;
  border-radius: 10px;
  background: #fff;
  color: #111827;
  font-size: 13px;
  line-height: 1.6;
  outline: none;
  resize: vertical;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;

  &::placeholder {
    color: #a6adba;
  }

  &:focus {
    border-color: #818cf8;
    box-shadow: none;
  }
}

.mem-add-btn {
  display: grid;
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  place-items: center;
  border: 0;
  border-radius: 10px;
  background: #202228;
  color: #fff;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.mem-add-btn:disabled {
  cursor: default;
  opacity: 0.4;
}

.mem-add-error {
  display: block;
  margin-top: 6px;
  color: #d4380d;
  font-size: 12px;
}

.mem-list {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 2px 2px 4px 0;
  overflow-y: auto;
  list-style: none;
  scrollbar-width: thin;
  scrollbar-color: #cbd5e1 transparent;

  &::-webkit-scrollbar {
    width: 6px;
  }

  &::-webkit-scrollbar-thumb {
    border-radius: 999px;
    background: #cbd5e1;
  }
}

.mem-item {
  position: relative;
  display: block;
  padding: 12px 14px 11px;
  border: 1px solid #ececef;
  border-radius: 13px;
  background: #fff;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;

  &.editing {
    display: flex;
    border-color: #202228;
    align-items: center;
    gap: 8px;
  }
}

.mem-item:hover {
  border-color: #d9dade;
  background: #fafafb;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.035);
}

.mem-item.editing:hover {
  border-color: #202228;
  background: #fff;
}

.mem-badge {
  display: inline-flex;
  margin: 0 0 7px;
  padding: 2px 7px;
  border-radius: 6px;
  background: #f3f4f6;
  color: #6b7280;
  font-size: 11px;
  line-height: 1.6;
}

.mem-item.editing .mem-badge {
  flex-shrink: 0;
  margin: 0;
}

.mem-content {
  display: block;
  min-width: 0;
  color: #202228;
  font-size: 13px;
  line-height: 1.6;
  word-break: break-word;
}

.mem-time {
  display: block;
  margin-top: 4px;
  color: #b0b4bd;
  font-size: 10.5px;
}

.mem-item:focus-within {
  border-color: #c7d2fe;
  background: #fafbff;
}

.mem-item:not(.editing) .mem-op.hover-only {
  position: absolute;
  top: 8px;
}

.mem-item:not(.editing) .mem-op.hover-only:not(.del) {
  right: 38px;
}

.mem-item:not(.editing) .mem-op.del {
  right: 8px;
}

.mem-edit-input {
  height: 32px;
  border-radius: 8px;
  font-size: 12.5px;
}

.mem-op {
  display: grid;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  place-items: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #b0b4bd;
  cursor: pointer;
  transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;

  &:hover {
    background: #f0f0f2;
    color: #202228;
  }

  &.ok:hover {
    background: #ebf7eb;
    color: #389e0d;
  }

  &.del:hover {
    background: #fbeceb;
    color: #d4380d;
  }

  &:disabled {
    cursor: default;
    opacity: 0.4;
  }

  &.hover-only {
    opacity: 0;
  }

  &.cancel-add {
    width: 30px;
    height: 34px;
  }
}

.mem-add-row .mem-input {
  height: 34px;
  border-color: transparent;
  background: #fff;
}

.mem-item:hover .mem-op.hover-only {
  opacity: 1;
}

/* 个性化 */
.mem-section-title {
  color: #202228;
  font-size: 13px;
  font-weight: 600;
}

.mem-profile {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 14px;
  padding: 2px 2px 8px;
  overflow-y: auto;
  scrollbar-width: none;
  -ms-overflow-style: none;

  &::-webkit-scrollbar {
    display: none;
    width: 0;
    height: 0;
  }
}

.mem-field {
  display: flex;
  flex-direction: column;
  gap: 6px;

  > span {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    color: #374151;
    font-size: 12.5px;
    font-weight: 500;
  }
}

.mem-charcount {
  color: #c3c7cf;
  font-size: 10.5px;
  font-style: normal;
  font-weight: 400;
}

.mem-profile-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.mem-profile-saved {
  color: #389e0d;
  font-size: 12px;
}

.mem-profile-dirty {
  color: #2563eb;
  font-size: 12px;
}

.mem-empty {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 16px;
  color: #9098a3;
  text-align: center;
}

.mem-empty.compact {
  flex: none;
  padding: 28px 16px;
}

.mem-empty .anticon {
  font-size: 24px;
  color: #c3c7cf;
}

.mem-empty strong {
  color: #62687a;
  font-size: 13px;
}

.mem-empty span {
  font-size: 12px;
  line-height: 1.5;
}

@keyframes mem-popover-enter {
  from {
    opacity: 0;
    transform: translateX(-12px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}

@keyframes mem-backdrop-enter {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

:global(.tox-center.sidebar-collapsed) .mem-popover,
:global(.tox-center.sidebar-collapsed) .mem-backdrop {
  left: var(--center-nav-width, 78px);
}

@media (max-width: 1024px) {
  .mem-popover {
    top: var(--compact-header-height, 64px);
    left: 0;
    width: min(420px, 100%);
    border-radius: 0 16px 0 0;
  }
  .mem-backdrop {
    top: var(--compact-header-height, 64px);
    left: 0;
  }
}

@media (max-width: 719px) {
  .mem-popover {
    width: 100%;
    border-radius: 0;
  }
}

@media (hover: none) {
  .mem-op.hover-only {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mem-popover,
  .mem-backdrop,
  .mem-more-menu {
    animation: none;
  }
}
</style>
