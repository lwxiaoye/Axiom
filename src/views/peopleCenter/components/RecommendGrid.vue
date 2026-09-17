<template>
  <div class="recommend-grid">
    <div class="recommend-header">
      <div>
        <h3>推荐智能体</h3>
      </div>
    </div>
    <div v-if="loading" class="recommend-loading">
      <LoadingOutlined /> 正在加载推荐...
    </div>
    <div v-else-if="agents.length === 0" class="recommend-empty">
      暂无推荐智能体
    </div>
    <div v-else class="recommend-cards">
      <div
        v-for="agent in agents.slice(0, 4)"
        :key="agent.id"
        class="recommend-card"
        @click="$emit('startChat', agent)"
      >
        <div class="recommend-card-icon">
          <img
            v-if="getAgentIconUrl(agent)"
            :src="getAgentIconUrl(agent)"
            :alt="agent.name"
            @error="recoverAgentIcon($event, agent)"
          />
          <span class="recommend-card-icon-fallback">{{ getAgentInitials(agent) }}</span>
        </div>
        <div class="recommend-card-info">
          <strong>{{ agent.name }}</strong>
          <p>{{ agent.description || '暂无描述' }}</p>
        </div>
        <PremiumChevron class="recommend-arrow" direction="right" :size="15" interactive />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { LoadingOutlined } from '@ant-design/icons-vue';
import PremiumChevron from './PremiumChevron.vue';
import type { AgentItem } from '../agentApi';
import { getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../agentIcon';

withDefaults(defineProps<{
  agents?: AgentItem[];
  loading?: boolean;
}>(), {
  agents: () => [],
  loading: false,
});

defineEmits<{
  (e: 'startChat', agent: AgentItem): void;
}>();

</script>

<style scoped>
.recommend-grid {
  width: 100%;
  max-width: 900px;
  margin: 38px auto 0;
  animation: recommend-enter 0.48s 0.16s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.recommend-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.recommend-header :deep(.ant-btn-link) {
  color: #6d7280;
}

.recommend-header :deep(.ant-btn-link:hover) {
  color: #111;
}

.recommend-header h3 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  animation: recommend-title-enter 0.42s 0.18s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.recommend-loading,
.recommend-empty {
  display: grid;
  place-items: center;
  min-height: 72px;
  color: #737b88;
}

.recommend-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.recommend-card {
  position: relative;
  display: grid;
  min-height: 126px;
  grid-template-columns: 52px minmax(0, 1fr);
  align-content: start;
  gap: 12px;
  padding: 16px;
  border: 1px solid #e1e2e7;
  border-radius: 12px;
  background: #fff;
  cursor: pointer;
  animation: recommend-card-enter 0.44s cubic-bezier(0.22, 1, 0.36, 1) both;
  transition:
    transform 0.2s ease,
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}

.recommend-card:nth-child(1) {
  animation-delay: 0.22s;
}

.recommend-card:nth-child(2) {
  animation-delay: 0.28s;
}

.recommend-card:nth-child(3) {
  animation-delay: 0.34s;
}

.recommend-card:nth-child(4) {
  animation-delay: 0.4s;
}

.recommend-card:hover {
  border-color: #bec2d4;
  box-shadow: 0 12px 28px rgba(17, 24, 39, 0.07);
  transform: translateY(-3px);
}

.recommend-card:active {
  transform: translateY(-1px) scale(0.99);
}

.recommend-card-icon {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: #f4f5f7;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 22px;
  flex-shrink: 0;
  overflow: hidden;
  transition:
    box-shadow 0.2s ease,
    transform 0.2s ease;
}

.recommend-card:hover .recommend-card-icon {
  box-shadow: 0 8px 18px rgba(17, 24, 39, 0.12);
  transform: translateY(-2px);
}

.recommend-card-icon img {
  position: relative;
  z-index: 1;
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  object-fit: contain;
  background: #fff;
  padding: 2px;
}

.recommend-card-icon-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 14px;
  font-weight: 800;
}

.recommend-card-info {
  flex: 1;
  min-width: 0;
}

.recommend-card-info strong {
  display: block;
  font-size: 14px;
  margin-bottom: 5px;
}

.recommend-card-info p {
  display: -webkit-box;
  height: 36px;
  margin: 0;
  overflow: hidden;
  color: #687080;
  font-size: 12px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.recommend-arrow {
  position: absolute;
  right: 14px;
  bottom: 14px;
  color: #9a9eaa;
  transition: color 0.18s ease, filter 0.18s ease;
}

.recommend-card:hover .recommend-arrow {
  color: #111;
  filter: drop-shadow(0 1px 1px rgba(17, 24, 39, 0.16));
}

@keyframes recommend-title-enter {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes recommend-enter {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes recommend-card-enter {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (max-width: 1180px) {
  .recommend-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .recommend-cards {
    grid-template-columns: 1fr;
  }
}
</style>
