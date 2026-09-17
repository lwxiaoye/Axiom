<template>
  <KnowledgeDetail
    v-if="knowledgeId"
    :knowledge-id="knowledgeId"
    @back="openList"
    @deleted="openList"
  />
  <KnowledgeList v-else @open="openDetail" />
</template>

<script setup lang="ts">
  import { computed } from 'vue';
  import { useRoute, useRouter } from 'vue-router';
  import KnowledgeDetail from './components/KnowledgeDetail.vue';
  import KnowledgeList from './components/KnowledgeList.vue';

  defineOptions({ name: 'KnowledgeManagement' });

  const route = useRoute();
  const router = useRouter();
  const knowledgeId = computed(() => String(route.query.id || ''));

  function openDetail(id: string) {
    router.push({ path: '/knowledge/base', query: { id } });
  }

  function openList() {
    router.push({ path: '/knowledge/base' });
  }
</script>
