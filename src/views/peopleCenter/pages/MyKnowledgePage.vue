<template>
  <MyKnowledgeTab @use-knowledge="handleUseKnowledge" />
</template>

<script setup lang="ts">
import MyKnowledgeTab from '../tabs/MyKnowledgeTab.vue';
import { useCenterContext } from '../centerContext';

defineOptions({ name: 'CenterMyKnowledgePage' });

const ctx = useCenterContext();
const { handleUseKnowledge } = ctx.centerChat;
</script>
