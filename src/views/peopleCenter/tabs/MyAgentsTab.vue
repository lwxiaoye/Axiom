<template>
  <section class="my-agent-section wb-shell">
    <!-- 工作台子导航（对齐 FastGPT dashboard：智能体/技能/我的工具/系统工具，无模板市场与 MCP 服务） -->
    <nav :class="['wb-rail', { collapsed }]" aria-label="工作台导航">
      <a-tooltip
        v-for="item in navItems"
        :key="item.key"
        :title="collapsed ? item.label : ''"
        placement="right"
        :mouse-enter-delay="0.2"
      >
        <button
          type="button"
          :class="['wb-rail-item', { active: activePanel === item.key }]"
          :aria-label="item.label"
          @click="activePanel = item.key"
        >
          <component :is="item.icon" />
          <span class="wb-rail-label">{{ item.label }}</span>
          <em v-if="item.count !== undefined" class="wb-rail-count">{{ item.count }}</em>
        </button>
      </a-tooltip>

      <button
        class="wb-rail-toggle"
        type="button"
        :aria-label="collapsed ? '展开导航' : '收起导航'"
        :title="collapsed ? '展开导航' : '收起导航'"
        :aria-expanded="!collapsed"
        @click="toggleCollapsed"
      >
        <MenuUnfoldOutlined v-if="collapsed" />
        <MenuFoldOutlined v-else />
        <span v-if="!collapsed">收起</span>
      </button>
    </nav>

    <div class="wb-content">
      <AgentsPanel
        v-if="activePanel === 'agents'"
        :agents="agents"
        :loading="loading"
        :load-failed="loadFailed"
        @reload="emit('reload')"
        @preview="emit('preview', $event)"
        @run="emit('run', $event)"
        @open-designer="emit('openDesigner', $event)"
        @open-metrics="emit('openMetrics', $event)"
        @open-conversation-logs="emit('openConversationLogs', $event)"
        @publish="emit('publish', $event)"
        @versions="emit('versions', $event)"
        @agent-api="emit('agent-api', $event)"
        @delete="emit('delete', $event)"
      />
      <SkillsPanel v-else-if="activePanel === 'skills'" @count="skillCount = $event" />
      <MyToolsPanel
        v-else-if="activePanel === 'myTools'"
        :agents="agents"
        :loading="loading"
        :load-failed="loadFailed"
        @reload="emit('reload')"
        @open-designer="emit('openDesigner', $event)"
        @delete="emit('delete', $event)"
      />
      <SystemToolsPanel v-else-if="activePanel === 'systemTools'" @count="systemToolCount = $event" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import {
  ApiOutlined,
  AppstoreOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  StarOutlined,
  ToolOutlined,
} from '@ant-design/icons-vue';
import AgentsPanel from '../workbench/AgentsPanel.vue';
import SkillsPanel from '../workbench/SkillsPanel.vue';
import MyToolsPanel from '../workbench/MyToolsPanel.vue';
import SystemToolsPanel from '../workbench/SystemToolsPanel.vue';
import { AGENT_KINDS, TOOL_KINDS, getAiAppKind } from '../../workflow/shared/agentApp';
import { getAgentSkillList } from '../../workflow/api/skill.api';
import { queryBuiltinWorkflowTools } from '../../workflow/api/workflow.api';

const props = defineProps<{
  agents: any[];
  loading: boolean;
  loadFailed?: boolean;
}>();

const emit = defineEmits<{
  (e: 'reload'): void;
  (e: 'preview', item: any): void;
  (e: 'run', item: any): void;
  (e: 'openDesigner', item: any): void;
  (e: 'openMetrics', item: any): void;
  (e: 'openConversationLogs', item: any): void;
  (e: 'publish', item: any): void;
  (e: 'versions', item: any): void;
  (e: 'agent-api', item: any): void;
  (e: 'delete', item: any): void;
}>();

type PanelKey = 'agents' | 'skills' | 'myTools' | 'systemTools';

const COLLAPSE_KEY = 'wb-nav-collapsed';

const activePanel = ref<PanelKey>('agents');
const collapsed = ref(localStorage.getItem(COLLAPSE_KEY) === '1');
const skillCount = ref<number | undefined>();
const systemToolCount = ref<number | undefined>();

const agentCount = computed(() => props.agents.filter((item) => AGENT_KINDS.includes(getAiAppKind(item))).length);
const toolCount = computed(() => props.agents.filter((item) => TOOL_KINDS.includes(getAiAppKind(item))).length);

const navItems = computed(() => [
  { key: 'agents' as PanelKey, label: '智能体', icon: AppstoreOutlined, count: agentCount.value },
  { key: 'skills' as PanelKey, label: '技能', icon: StarOutlined, count: skillCount.value },
  { key: 'myTools' as PanelKey, label: '我的工具', icon: ToolOutlined, count: toolCount.value },
  { key: 'systemTools' as PanelKey, label: '系统工具', icon: ApiOutlined, count: systemToolCount.value },
]);

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0');
}

onMounted(() => {
  // 面板按需挂载，未访问过的面板不会触发 @count：预取一次计数，面板挂载后以其 @count 为准
  getAgentSkillList({ source: 'personal' })
    .then((list) => {
      if (skillCount.value === undefined && Array.isArray(list)) skillCount.value = list.length;
    })
    .catch(() => {});
  queryBuiltinWorkflowTools()
    .then((list) => {
      if (systemToolCount.value === undefined && Array.isArray(list)) systemToolCount.value = list.length;
    })
    .catch(() => {});
});
</script>
