<template>
  <main class="knowledge-analytics-page">
    <KnowledgeAnalyticsPanel scope="admin" @open-knowledge="openKnowledge" />
  </main>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router';
import KnowledgeAnalyticsPanel from '../components/KnowledgeAnalyticsPanel.vue';

defineOptions({ name: 'KnowledgeOperationsAnalytics' });

const router = useRouter();

function openKnowledge(id: string) {
  router.push({ path: '/knowledge/base', query: { id, tab: 'analytics' } });
}
</script>

<style scoped lang="less">
.knowledge-analytics-page {
  min-height: 100%;
  padding: 22px;
  background: #f7f8fb;
}

@media (max-width: 760px) {
  .knowledge-analytics-page { padding: 14px; }
}
</style>
