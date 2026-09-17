<template>
  <div :class="['run-message-actions', { 'is-user': role === 'user' }]">
    <button type="button" :aria-label="copied ? '已复制' : '复制'" :title="copied ? '已复制' : '复制'" @click="copyContent">
      <svg v-if="copied" class="message-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 6 9 17l-5-5" />
      </svg>
      <svg v-else class="message-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
        <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
      </svg>
    </button>
    <button v-if="role === 'user' && editable" type="button" aria-label="编辑并重新发送" title="编辑并重新发送" @click="emit('edit')">
      <svg class="message-action-icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 20h9" />
        <path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z" />
      </svg>
    </button>
    <template v-if="role === 'assistant' && messageId">
      <button :class="{ active: feedback === 'up' }" type="button" aria-label="点赞" title="点赞" @click="emit('feedback', messageId, feedback === 'up' ? null : 'up')">
        <LikeOutlined />
      </button>
      <button :class="{ active: feedback === 'down' }" type="button" aria-label="点踩" title="点踩" @click="emit('feedback', messageId, feedback === 'down' ? null : 'down')">
        <DislikeOutlined />
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { DislikeOutlined, LikeOutlined } from '@ant-design/icons-vue';
import { ref } from 'vue';

const props = withDefaults(defineProps<{ content: string; role: 'user' | 'assistant'; editable?: boolean; messageId?: number; feedback?: 'up' | 'down' | null }>(), {
  editable: false,
});
const emit = defineEmits<{ (event: 'edit'): void; (event: 'feedback', id: number, value: 'up' | 'down' | null): void }>();
const copied = ref(false);

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // 权限或浏览器限制时退回到本地选区复制。
    }
  }
  const area = document.createElement('textarea');
  area.value = value;
  document.body.append(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

async function copyContent() {
  await copyText(props.content);
  copied.value = true;
  window.setTimeout(() => { copied.value = false; }, 1200);
}
</script>

<style lang="less" scoped>
.run-message-actions { display: flex; gap: 4px; margin-top: 8px; opacity: 0; transition: opacity .15s ease; }
.run-message-actions.is-user { justify-content: flex-end; }
.run-message-actions:focus-within { opacity: 1; }
.run-message-actions button { display: inline-flex; width: 28px; height: 28px; align-items: center; justify-content: center; padding: 0; border: 0; border-radius: 7px; background: transparent; color: #8a8f99; cursor: pointer; transition: background .15s ease, color .15s ease; }
.run-message-actions button:hover { background: #f0f0f2; color: #202228; }
.run-message-actions button.active { color: #111; }
.run-message-actions button:focus-visible { outline: 2px solid #7d89a8; outline-offset: 1px; }
.message-action-icon { display: block; width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
@media (hover: none) { .run-message-actions { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .run-message-actions { transition: none; } }
</style>
