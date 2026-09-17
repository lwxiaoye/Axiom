<template>
  <a-drawer :open="open" width="min(1040px, 96vw)" :title="`${appTitle || '智能体'} · 对话日志`" @close="emit('update:open', false)">
    <AgentConversationLogPanel v-if="open && appId" :app-id="appId" access="owner" />
  </a-drawer>
</template>

<script setup lang="ts">
import AgentConversationLogPanel from './AgentConversationLogPanel.vue';

defineProps<{ open: boolean; appId?: string; appTitle?: string }>();
const emit = defineEmits<{ (event: 'update:open', value: boolean): void }>();
</script>
