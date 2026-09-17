<template>
  <div class="workbench-panel">
    <div class="market-heading">
      <div>
        <span>MY TOOLS</span>
        <h2>我的工具</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input v-model="keyword" placeholder="搜索我的工具..." />
      </div>
      <button class="market-refresh" type="button" @click="emit('reload')">刷新</button>
    </div>

    <p class="wb-panel-tip">
      工作流工具用画布声明入参出参，HTTP 工具从接口清单批量生成，MCP 工具连接外部 MCP Server；创建后自己和授权对象可直接挂载调用。
    </p>

    <div v-if="loading" class="status-box"><LoadingOutlined /> 正在加载我的工具...</div>
    <div v-else-if="loadFailed && !toolRecords.length" class="wb-unready">
      <ApiOutlined />
      <strong>工具服务暂不可用</strong>
      <p>工作流运行时（agent-api）未连接或未部署，恢复后点击刷新即可。</p>
      <a-button size="small" @click="emit('reload')">重试</a-button>
    </div>
    <div v-else class="wb-grid">
      <button class="wb-create-card" type="button" @click="openCreate">
        <span class="wb-create-plus"><PlusOutlined /></span>
        <strong>新建工具</strong>
        <small>工作流工具 · HTTP 工具 · MCP 工具</small>
      </button>

      <div
        v-for="item in filteredTools"
        :key="item.id"
        class="wb-card"
        role="button"
        tabindex="0"
        :title="`打开${primaryActionLabel(item)}`"
        @click="openPrimary(item)"
        @keydown.enter="openPrimary(item)"
      >
        <div class="wb-card-head">
          <span :class="['wb-tile', getAiAppKind(item)]">
            <span>{{ getAgentInitials(item) }}</span>
          </span>
          <div class="wb-card-ident">
            <strong class="wb-card-title">{{ item.name || item.appName }}</strong>
            <span class="wb-card-sub">{{ getAiAppKindLabel(item) }}</span>
          </div>
        </div>

        <p class="wb-card-desc">{{ item.description || '暂无描述' }}</p>

        <div class="wb-card-foot">
          <span class="wb-status published">
            <i></i>
            可用
          </span>
          <div class="wb-card-ops" @click.stop @keydown.enter.stop>
            <button class="wb-op primary" type="button" @click="openPrimary(item)">
              {{ primaryActionLabel(item) }}
            </button>
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <button class="wb-op icon-only" type="button" title="更多操作" aria-label="更多操作">
                <EllipsisOutlined />
              </button>
              <template #overlay>
                <a-menu @click="({ key }) => onMenuClick(String(key), item)">
                  <a-menu-item key="settings"><SettingOutlined /> 设置</a-menu-item>
                  <a-menu-item v-if="isOwner(item)" key="access"><TeamOutlined /> 授权</a-menu-item>
                  <a-menu-item key="delete" class="wb-menu-danger"><DeleteOutlined /> 删除</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </div>
        </div>
      </div>

      <div v-if="toolRecords.length && !filteredTools.length" class="wb-empty">没有匹配的工具，换个关键词试试</div>
      <div v-else-if="!toolRecords.length" class="wb-empty">还没有工具，新建一个工作流工具、HTTP 工具或 MCP 工具</div>
    </div>

    <WorkflowAppModal ref="formModelRef" context="tool" @success="emit('reload')" />
    <WorkflowAppAclModal ref="aclModalRef" @success="emit('reload')" />
    <HttpToolSetModal ref="httpModalRef" @success="emit('reload')" />
    <McpToolSetModal ref="mcpModalRef" @success="emit('reload')" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { Modal } from 'ant-design-vue';
import {
  ApiOutlined,
  DeleteOutlined,
  EllipsisOutlined,
  LoadingOutlined,
  PlusOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
} from '@ant-design/icons-vue';
import { getAgentInitials } from '../agentIcon';
import WorkflowAppAclModal from '../components/WorkflowAppAclModal.vue';
import WorkflowAppModal from '../components/WorkflowAppModal.vue';
import HttpToolSetModal from './HttpToolSetModal.vue';
import McpToolSetModal from './McpToolSetModal.vue';
import { TOOL_KINDS, getAiAppKind, getAiAppKindLabel } from '../../workflow/shared/agentApp';

const props = defineProps<{
  agents: any[];
  loading: boolean;
  loadFailed?: boolean;
}>();

const emit = defineEmits<{
  (e: 'reload'): void;
  (e: 'openDesigner', item: any): void;
  (e: 'delete', item: any): void;
}>();

const keyword = ref('');
const formModelRef = ref<InstanceType<typeof WorkflowAppModal> | null>(null);
const aclModalRef = ref<InstanceType<typeof WorkflowAppAclModal> | null>(null);
const httpModalRef = ref<InstanceType<typeof HttpToolSetModal> | null>(null);
const mcpModalRef = ref<InstanceType<typeof McpToolSetModal> | null>(null);

const toolRecords = computed(() => props.agents.filter((item) => TOOL_KINDS.includes(getAiAppKind(item))));

const filteredTools = computed(() => {
  const key = keyword.value.trim().toLowerCase();
  if (!key) return toolRecords.value;
  return toolRecords.value.filter(
    (item) =>
      String(item?.name || '').toLowerCase().includes(key) ||
      String(item?.description || '').toLowerCase().includes(key)
  );
});

function openCreate() {
  formModelRef.value?.init(undefined, 'workflowTool');
}

function primaryActionLabel(item: any) {
  const kind = getAiAppKind(item);
  if (kind === 'workflowTool') return '编排';
  if (kind === 'mcpToolSet') return '配置 MCP';
  return '配置接口';
}

function openPrimary(item: any) {
  const kind = getAiAppKind(item);
  if (kind === 'workflowTool') emit('openDesigner', item);
  else if (kind === 'mcpToolSet') mcpModalRef.value?.init(item);
  else httpModalRef.value?.init(item);
}

function onMenuClick(key: string, item: any) {
  if (key === 'settings') formModelRef.value?.init(item);
  else if (key === 'access') aclModalRef.value?.init(item);
  else if (key === 'delete') confirmDelete(item);
}

// 仅创建者可授权，与 AgentsPanel 的 isOwner 逻辑一致
function isOwner(item: any) {
  return String(item?.sharePermission || '').toUpperCase() === 'OWNER' || !item?.sharePermission;
}

function confirmDelete(item: any) {
  Modal.confirm({
    title: '删除工具',
    content: `确定删除「${item.name || item.appName || '未命名'}」？删除后不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: () => emit('delete', item),
  });
}

</script>
