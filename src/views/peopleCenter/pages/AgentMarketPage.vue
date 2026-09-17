<template>
  <AgentMarketTab
    :search-keyword="searchKeyword"
    :category-filters="appCategoryFilters"
    :selected-category="selectedCategory"
    :loading="appLoading"
    :app-list="appList"
    :filtered-app-list="filteredAppList"
    :visible-app-list="visibleAppList"
    :has-more="appHasMore"
    @update:search-keyword="searchKeyword = $event"
    @reload="reloadApps"
    @select-category="selectAppCategory"
    @open-agent="openAgent"
    @load-more="loadMoreApps"
  />
</template>

<script setup lang="ts">
import AgentMarketTab from '../tabs/AgentMarketTab.vue';
import { useCenterContext } from '../centerContext';

defineOptions({ name: 'CenterAgentMarketPage' });

const ctx = useCenterContext();
const {
  searchKeyword,
  appCategoryFilters,
  selectedCategory,
  appLoading,
  appList,
  filteredAppList,
  visibleAppList,
  appHasMore,
  reloadApps,
  selectAppCategory,
  openAgent,
  loadMoreApps,
} = ctx.agentMarket;

</script>
