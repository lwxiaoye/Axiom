<template>
  <section class="agent-market">
    <div class="market-heading">
      <div>
        <span>AGENT MARKET</span>
        <h2>智能体广场</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input :value="searchKeyword" placeholder="搜索智能体应用..." @input="emit('update:searchKeyword', ($event.target as HTMLInputElement).value)" />
      </div>
      <button class="market-refresh" type="button" @click="emit('reload')">刷新</button>
    </div>

    <div v-if="categoryFilters.length > 1" class="category-filter" aria-label="能力分类筛选">
      <button
        v-for="category in categoryFilters"
        :key="category.value"
        :class="['category-filter-item', { active: selectedCategory === category.value }]"
        type="button"
        @click="emit('selectCategory', category.value)"
      >
        <span>{{ category.label }}</span>
        <em>{{ category.count }}</em>
      </button>
    </div>

    <div v-if="loading" class="status-box">
      <LoadingOutlined /> 正在加载智能体...
    </div>
    <div v-else-if="appList.length === 0" class="status-box">暂无智能体应用</div>
    <div v-else-if="filteredAppList.length === 0" class="status-box">暂无匹配的智能体应用</div>
    <div v-else class="app-grid">
      <button
        v-for="item in visibleAppList"
        :key="item.id"
        class="app-card"
        type="button"
        :aria-label="`打开${item.appName}`"
        @click="emit('openAgent', item)"
      >
        <div
          :class="['app-card-image', { 'is-neutral': getAgentCapability(item) === 'other' }]"
          :style="{ backgroundColor: getCapabilityVisual(item).tint }"
        >
          <img
            v-if="getAgentCapability(item) !== 'other'"
            class="app-card-watermark"
            :src="getCapabilityVisual(item).watermark"
            alt=""
            aria-hidden="true"
          />
          <span class="app-card-icon-shell">
            <img
              v-if="getAgentIconUrl(item)"
              :src="getAgentIconUrl(item)"
              :alt="item.appName"
              @load="classifyAgentIcon"
              @error="recoverAgentIcon($event, item)"
            />
            <span>{{ getAgentInitials(item) }}</span>
          </span>
        </div>
        <div class="app-card-body">
          <div class="app-card-heading-row">
            <strong>
              <span class="app-card-title">{{ item.appName }}</span>
            </strong>
            <a-tooltip v-if="item.missingModels?.length" :title="`缺少可用模型：${item.missingModels.join('、')}`">
              <span class="app-model-warning" :aria-label="`缺少可用模型：${item.missingModels.join('、')}`">
                <ExclamationCircleOutlined />
              </span>
            </a-tooltip>
          </div>
          <p>{{ item.appRemark || '暂无描述' }}</p>
          <div class="app-card-footer">
            <span class="app-card-creator">
              <span class="app-card-avatar" aria-hidden="true">
                <img
                  v-if="getCreatorAvatarUrl(item)"
                  :src="getCreatorAvatarUrl(item)"
                  alt=""
                  @error="markCreatorAvatarFailed(item)"
                />
                <span v-else>{{ getMarketplaceCreatorInitials(item) }}</span>
              </span>
              <span class="app-card-creator-name">
                {{ getMarketplaceCreatorName(item) }}
              </span>
            </span>
            <span class="app-card-date">
              {{ formatDate(item.createTime) }}
            </span>
            <ArrowRightOutlined class="app-card-arrow" aria-hidden="true" />
          </div>
        </div>
      </button>
    </div>
    <div ref="loadMoreRef" class="app-load-more" aria-live="polite">
      <template v-if="hasMore">继续滚动加载更多</template>
      <template v-else-if="filteredAppList.length">已加载全部应用</template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { ArrowRightOutlined, ExclamationCircleOutlined, LoadingOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { classifyAgentIcon, getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../agentIcon';
import {
  getAgentCapability,
  getAgentCapabilityDefinition,
  getAgentVisualVariant,
  getMarketplaceCreatorInitials,
  getMarketplaceCreatorName,
  type MarketplaceApp,
} from '../agentMarketCapabilities';
import { AGENT_CAPABILITY_WATERMARKS } from '../agentMarketVisuals';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';

const props = defineProps<{
  searchKeyword: string;
  categoryFilters: Array<{ value: string; label: string; count: number }>;
  selectedCategory: string;
  loading: boolean;
  appList: MarketplaceApp[];
  filteredAppList: MarketplaceApp[];
  visibleAppList: MarketplaceApp[];
  hasMore: boolean;
}>();

const emit = defineEmits<{
  (e: 'update:searchKeyword', value: string): void;
  (e: 'reload'): void;
  (e: 'selectCategory', category: string): void;
  (e: 'openAgent', item: MarketplaceApp): void;
  (e: 'loadMore'): void;
}>();

const loadMoreRef = ref<HTMLElement | null>(null);
let observer: IntersectionObserver | null = null;
const failedCreatorAvatarIds = ref<Set<string>>(new Set());

watch(
  () => props.appList.map((item) => `${item.id}:${item.createByAvatar || ''}`).join('|'),
  () => { failedCreatorAvatarIds.value = new Set(); },
);

watch(loadMoreRef, (element) => {
  observer?.disconnect();
  observer = null;
  if (!element) return;
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) emit('loadMore');
    },
    { rootMargin: '240px 0px' },
  );
  observer.observe(element);
}, { immediate: true });

onBeforeUnmount(() => {
  observer?.disconnect();
});

function getCapabilityVisual(item: MarketplaceApp) {
  const key = getAgentCapability(item);
  const definition = getAgentCapabilityDefinition(key);
  const watermarks = AGENT_CAPABILITY_WATERMARKS[key];
  const variant = getAgentVisualVariant(item);
  return {
    tint: definition.tint,
    watermark: watermarks[variant === 'a' ? 0 : 1],
  };
}

function getCreatorAvatarUrl(item: MarketplaceApp) {
  const id = String(item.id || '');
  const avatar = String(item.createByAvatar || '').trim();
  if (!avatar || failedCreatorAvatarIds.value.has(id)) return '';
  return getProxyStaticFileUrl(avatar);
}

function markCreatorAvatarFailed(item: MarketplaceApp) {
  const next = new Set(failedCreatorAvatarIds.value);
  next.add(String(item.id || ''));
  failedCreatorAvatarIds.value = next;
}

function formatDate(dateStr?: string) {
  if (!dateStr) return '未知';
  const date = new Date(dateStr);
  if (Number.isNaN(date.getTime())) return '未知';
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
</script>
