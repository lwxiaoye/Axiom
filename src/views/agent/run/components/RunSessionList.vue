<template>
  <div class="session-list">
    <template v-for="group in groups" :key="group.key">
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
        <div
          v-else
          :class="['conversation-item', { active: item.id === activeSessionId, running: isLive(item.id) }]"
          role="button"
          tabindex="0"
          @click="emit('select', item.id)"
          @keydown.enter.self.prevent="emit('select', item.id)"
          @keydown.space.self.prevent="emit('select', item.id)"
        >
          <span class="conversation-title">{{ item.title || '新对话' }}</span>
          <span :class="['conversation-meta', { live: isLive(item.id) }]">
            {{ isLive(item.id) ? '回复中' : formatSessionTime(item.updateTime || item.createTime) }}
          </span>
          <span class="conversation-actions">
            <button class="conv-act" type="button" title="重命名" @click.stop="startRename(item)">
              <EditOutlined />
            </button>
            <button
              class="conv-act"
              type="button"
              :class="{ pinned: item.pinned }"
              :title="item.pinned ? '取消置顶' : '置顶'"
              @click.stop="emit('pin', item.id)"
            >
              <PushpinFilled v-if="item.pinned" />
              <PushpinOutlined v-else />
            </button>
            <button class="conv-act danger" type="button" title="删除对话" @click.stop="emit('remove', item.id)">
              <DeleteOutlined />
            </button>
          </span>
        </div>
      </template>
    </template>
    <div v-if="!sessions.length" class="session-empty">暂无会话</div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import { DeleteOutlined, EditOutlined, PushpinFilled, PushpinOutlined } from '@ant-design/icons-vue';
import type { RunSession } from '../agentRun.api';
import { formatSessionTime, groupRunSessions } from '../sessionGroups';

const props = defineProps<{
  sessions: RunSession[];
  activeSessionId: string;
  runningSessionIds?: string[];
}>();
const emit = defineEmits<{
  (e: 'select', id: string): void;
  (e: 'pin', id: string): void;
  (e: 'rename', id: string, title: string): void;
  (e: 'remove', id: string): void;
}>();

const groups = computed(() => groupRunSessions(props.sessions));
const liveIds = computed(() => new Set(props.runningSessionIds || []));
function isLive(id: string) {
  return liveIds.value.has(id);
}
const renamingId = ref('');
const renamingText = ref('');

function startRename(item: RunSession) {
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

function confirmRename(id: string) {
  if (renamingId.value !== id) return;
  const text = renamingText.value.trim();
  renamingId.value = '';
  const current = props.sessions.find((session) => session.id === id);
  if (text && text !== current?.title) emit('rename', id, text);
  renamingText.value = '';
}
</script>

<style scoped lang="less">
.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}
.session-empty { color: var(--run-left-muted, #989aa1); text-align: center; padding: 24px 0; font-size: 13px; }
.conversation-group-label {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 15px 8px 6px;
  color: var(--run-left-muted, #b0b4bd);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  user-select: none;
}
.conversation-group-label:first-child { padding-top: 4px; }
.conversation-group-label .anticon { font-size: 10px; }
.conversation-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 38px;
  padding: 8px 8px 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--run-left-item-color, #4b5059);
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}
.conversation-item:hover { background: var(--run-left-item-hover-bg, #f5f5f7); }
.conversation-item.active { background: var(--run-left-item-active-bg, #f1f2f4); }
.conversation-item.active::before {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 2px;
  width: 3px;
  border-radius: 999px;
  background: var(--run-left-item-accent, #111827);
}
.conversation-item.active .conversation-title { color: var(--run-left-item-title, #111827); }
.conversation-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--run-left-item-title, #2b2f36);
  font-size: 14px;
  font-weight: 500;
  line-height: 1.45;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.conversation-meta {
  display: inline-flex;
  align-items: center;
  flex: none;
  margin-left: auto;
  color: var(--run-left-muted, #9aa0ac);
  font-size: 12px;
  white-space: nowrap;
}
.conversation-meta.live {
  color: var(--run-left-item-color, #4b5563);
}
.conversation-actions {
  display: none;
  align-items: center;
  gap: 1px;
  flex: none;
  margin-left: auto;
}
.conversation-item:hover .conversation-meta,
.conversation-item:focus-within .conversation-meta { display: none; }
.conversation-item:hover .conversation-actions,
.conversation-item:focus-within .conversation-actions { display: inline-flex; }
@media (hover: none) {
  .conversation-actions { display: inline-flex; }
  .conversation-meta { display: none; }
}
.conv-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #9096a1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.conv-act:hover { background: var(--run-left-action-hover-bg, #e7e8eb); color: var(--run-left-item-title, #111827); }
.conv-act.pinned { color: var(--run-left-item-title, #111827); }
.conv-act.danger:hover { background: #fde8e8; color: #dc2626; }
.conversation-item.editing {
  display: flex;
  align-items: center;
  padding: 4px 6px;
  background: transparent;
}
.conversation-rename-input {
  flex: 1;
  min-width: 0;
  height: 32px;
  padding: 0 10px;
  border: 1px solid #111827;
  border-radius: 8px;
  background: #fff;
  color: #111827;
  font-size: 14px;
  outline: none;
}
.conversation-rename-input:focus {
  border-color: #818cf8;
  box-shadow: none;
}
</style>
