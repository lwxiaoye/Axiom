<template>
  <div class="node-template-panel">
    <header class="panel-header">
      <strong>添加节点</strong>
      <button type="button" class="panel-close" @click="$emit('close')"><CloseOutlined /></button>
    </header>

    <a-tabs v-model:activeKey="activeTab" size="small" class="panel-tabs" @change="handleTabChange">
      <a-tab-pane key="basic" tab="基础功能" />
      <a-tab-pane key="systemTools" tab="系统工具" />
      <a-tab-pane key="myTools" tab="我的工具" />
      <a-tab-pane key="agent" tab="Agent" />
    </a-tabs>

    <div class="panel-search">
      <SearchOutlined />
      <input v-model="searchKey" :placeholder="searchPlaceholder" />
    </div>

    <!-- 系统工具标签筛选 -->
    <div v-if="activeTab === 'systemTools' && tagOptions.length" class="panel-tags">
      <a-tag
        v-for="tag in tagOptions"
        :key="tag.id"
        :color="selectedTags.includes(tag.id) ? 'blue' : 'default'"
        class="tag-item"
        @click="toggleTag(tag.id)"
      >
        {{ tag.label }}
      </a-tag>
    </div>

    <!-- 目录面包屑（我的工具下钻） -->
    <div v-if="parentStack.length" class="panel-breadcrumb">
      <a @click="popToRoot">我的工具</a>
      <template v-for="crumb in parentStack" :key="crumb.id">
        <RightOutlined class="crumb-sep" />
        <span>{{ crumb.name }}</span>
      </template>
    </div>

    <div class="panel-body">
      <!-- 基础功能：本地模板分组（unique/联动条件隐藏）。蓝本样式：双列紧凑项，简介走 Tooltip -->
      <template v-if="activeTab === 'basic'">
        <div v-for="group in basicGroups" :key="group.type" class="menu-group">
          <div class="group-title">{{ group.label }}</div>
          <div class="grid-two">
            <a-tooltip
              v-for="template in group.templates"
              :key="template.flowNodeType"
              :title="template.intro"
              placement="right"
              :mouse-enter-delay="0.4"
            >
              <button
                type="button"
                class="template-item compact"
                draggable="true"
                @click="addLocalTemplate(template)"
                @dragstart="handleLocalDragStart($event, template)"
              >
                <span class="template-icon" :style="{ color: template.color }">
                  <component :is="iconMap[template.icon] || MessageOutlined" />
                </span>
                <span class="compact-name">{{ template.name }}</span>
              </button>
            </a-tooltip>
          </div>
        </div>
        <div v-if="!basicGroups.length" class="panel-empty">没有匹配的节点</div>
      </template>

      <!-- 动态 Tab：系统工具 / 我的工具 / Agent -->
      <template v-else>
        <div v-if="listLoading" class="panel-state"><a-spin /></div>
        <div v-else-if="listError" class="panel-state">
          <a-alert type="error" show-icon message="模板加载失败" :description="listError" />
          <a-button size="small" class="retry-btn" @click="loadDynamicList">重试</a-button>
        </div>
        <div v-else-if="!dynamicItems.length" class="panel-empty">
          {{ searchKey.trim() ? '没有匹配的结果' : emptyText }}
        </div>
        <!-- 系统工具：双列紧凑 + Tooltip；我的工具/Agent：单列（名称 + 单行简介） -->
        <div v-else-if="activeTab === 'systemTools'" class="grid-two">
          <a-tooltip
            v-for="item in dynamicItems"
            :key="item.id"
            :title="item.intro"
            placement="right"
            :mouse-enter-delay="0.4"
          >
            <button
              type="button"
              class="template-item compact"
              draggable="true"
              @click="handleDynamicClick(item)"
              @dragstart="handleDynamicDragStart($event, item)"
            >
              <span class="template-icon dynamic">
                <img
                  v-if="getAvatarUrl(item)"
                  :src="getAvatarUrl(item)"
                  alt=""
                  @error="recoverAgentIcon($event, { appIcon: item.avatar, name: item.name })"
                />
                <ApiOutlined v-else />
              </span>
              <span class="compact-name">{{ item.name }}</span>
              <LoadingOutlined v-if="previewLoadingId === item.id" class="item-suffix" />
            </button>
          </a-tooltip>
        </div>
        <div v-else class="grid-one">
          <button
            v-for="item in dynamicItems"
            :key="item.id"
            type="button"
            class="template-item row"
            :class="{ 'is-folder': item.isFolder }"
            :draggable="!item.isFolder"
            @click="handleDynamicClick(item)"
            @dragstart="handleDynamicDragStart($event, item)"
          >
            <span class="template-icon dynamic">
              <img
                v-if="getAvatarUrl(item)"
                :src="getAvatarUrl(item)"
                alt=""
                @error="recoverAgentIcon($event, { appIcon: item.avatar, name: item.name })"
              />
              <RobotOutlined v-else-if="activeTab === 'agent'" />
              <ApiOutlined v-else />
            </span>
            <span class="template-text">
              <strong>
                {{ item.name }}
                <a-tag v-if="item.status && item.status !== 'published'" class="status-tag" color="orange">
                  未发布
                </a-tag>
              </strong>
              <em v-if="item.intro">{{ item.intro }}</em>
            </span>
            <LoadingOutlined v-if="previewLoadingId === item.id" class="item-suffix" />
            <RightOutlined v-else-if="item.isFolder" class="item-suffix" />
          </button>
        </div>
      </template>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { message } from 'ant-design-vue';
import {
  ApiOutlined,
  BranchesOutlined,
  CloseOutlined,
  CommentOutlined,
  DatabaseOutlined,
  FilterOutlined,
  FontSizeOutlined,
  ForkOutlined,
  FormOutlined,
  InteractionOutlined,
  LoadingOutlined,
  MergeCellsOutlined,
  MessageOutlined,
  RightOutlined,
  RobotOutlined,
  SearchOutlined,
  SwapOutlined,
} from '@ant-design/icons-vue';
import { FlowNodeTemplateTypeLabelMap, FlowNodeTypeEnum } from '../../core/constants';
import { nodeTemplateList } from '../../core/templates';
import type { FlowNodeTemplateType } from '../../core/type';
import {
  queryNodeTemplateList,
  queryNodeTemplatePreview,
  queryNodeTemplateTags,
  type NodeTemplateSummary,
  type NodeTemplateTag,
} from '../../api/workflow.api';
import { useEditorContext } from '../composables/useEditorContext';
import { getAgentIconUrl, recoverAgentIcon } from '/@/views/peopleCenter/agentIcon';

const props = defineProps<{ excludeAppId?: string }>();

const emit = defineEmits<{
  (e: 'add', template: FlowNodeTemplateType): void;
  (e: 'close'): void;
}>();

const { graph } = useEditorContext();

const iconMap: Record<string, any> = {
  MessageOutlined,
  DatabaseOutlined,
  MergeCellsOutlined,
  CommentOutlined,
  ForkOutlined,
  FormOutlined,
  InteractionOutlined,
  FilterOutlined,
  ApiOutlined,
  BranchesOutlined,
  FontSizeOutlined,
  SwapOutlined,
};

type TabKey = 'basic' | 'systemTools' | 'myTools' | 'agent';

const activeTab = ref<TabKey>('basic');
const searchKey = ref('');
const selectedTags = ref<string[]>([]);
const tagOptions = ref<NodeTemplateTag[]>([]);
const parentStack = ref<{ id: string; name: string }[]>([]);
const dynamicItems = ref<NodeTemplateSummary[]>([]);
const listLoading = ref(false);
const listError = ref('');
const previewLoadingId = ref('');

const searchPlaceholder = computed(
  () =>
    ({
      basic: '搜索节点',
      systemTools: '搜索系统工具',
      myTools: '搜索我的工具（工作流 / HTTP / MCP）',
      agent: '搜索可调用的应用',
    })[activeTab.value]
);

const emptyText = computed(
  () =>
    ({
      basic: '没有匹配的节点',
      systemTools: '暂无系统工具',
      myTools: '暂无已创建的工具，去工作台创建工作流 / HTTP / MCP 工具',
      agent: '暂无可调用的已发布应用',
    })[activeTab.value]
);

/** 基础功能 Tab：unique 已存在隐藏；stopTool/toolParams/loopRunBreak 随依赖节点联动（蓝本规则） */
const basicGroups = computed(() => {
  const keyword = searchKey.value.trim();
  const nodeTypes = new Set(graph.value.nodes.map((node) => node.flowNodeType as string));
  const filtered = nodeTemplateList.filter((template) => {
    if (template.unique && nodeTypes.has(template.flowNodeType)) return false;
    if (
      (template.flowNodeType === 'stopTool' || template.flowNodeType === 'toolParams') &&
      !nodeTypes.has('tools')
    ) {
      return false;
    }
    if (template.flowNodeType === 'loopRunBreak' && !nodeTypes.has('loopRun')) return false;
    if (keyword && !template.name.includes(keyword) && !template.intro.includes(keyword)) return false;
    return true;
  });
  const byType = new Map<string, FlowNodeTemplateType[]>();
  filtered.forEach((template) => {
    const list = byType.get(template.templateType) || [];
    list.push(template);
    byType.set(template.templateType, list);
  });
  return Array.from(byType.entries()).map(([type, templates]) => ({
    type,
    label: FlowNodeTemplateTypeLabelMap[type] || type,
    templates,
  }));
});

// ---------- 动态 Tab 数据 ----------

function getAvatarUrl(item: NodeTemplateSummary) {
  return getAgentIconUrl({ appIcon: item.avatar, icon: item.avatar, name: item.name });
}

async function loadDynamicList() {
  if (activeTab.value === 'basic') return;
  listLoading.value = true;
  listError.value = '';
  try {
    dynamicItems.value = await queryNodeTemplateList({
      tab: activeTab.value,
      searchKey: searchKey.value.trim() || undefined,
      parentId: parentStack.value[parentStack.value.length - 1]?.id,
      tags: selectedTags.value.length ? selectedTags.value.join(',') : undefined,
      excludeAppId: props.excludeAppId,
    });
  } catch (error: any) {
    dynamicItems.value = [];
    listError.value = error?.response?.data?.detail || error?.message || '请求失败';
  } finally {
    listLoading.value = false;
  }
}

async function loadTags() {
  try {
    tagOptions.value = await queryNodeTemplateTags();
  } catch {
    tagOptions.value = [];
  }
}

function handleTabChange() {
  // 蓝本：Tab 切换清空搜索、目录与标签状态
  searchKey.value = '';
  selectedTags.value = [];
  parentStack.value = [];
  dynamicItems.value = [];
  listError.value = '';
  if (activeTab.value === 'systemTools' && !tagOptions.value.length) loadTags();
  if (activeTab.value !== 'basic') loadDynamicList();
}

function toggleTag(tagId: string) {
  selectedTags.value = selectedTags.value.includes(tagId)
    ? selectedTags.value.filter((id) => id !== tagId)
    : [...selectedTags.value, tagId];
  loadDynamicList();
}

function popToRoot() {
  parentStack.value = [];
  searchKey.value = '';
  loadDynamicList();
}

// 蓝本 300ms 防抖搜索
let searchTimer: ReturnType<typeof setTimeout> | null = null;
watch(searchKey, () => {
  if (activeTab.value === 'basic') return;
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(loadDynamicList, 300);
});
onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer);
});

// ---------- 落图 ----------

function addLocalTemplate(template: FlowNodeTemplateType) {
  emit('add', template);
}

function handleLocalDragStart(event: DragEvent, template: FlowNodeTemplateType) {
  event.dataTransfer?.setData('application/workflow-node', template.flowNodeType);
  event.dataTransfer?.setData('application/workflow-node-template', JSON.stringify(template));
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}

async function handleDynamicClick(item: NodeTemplateSummary) {
  if (item.isFolder) {
    // 工具集/文件夹进入目录（蓝本：toolSet 普通入口只用于下钻，直落须走工具锚点，P2）
    parentStack.value = [...parentStack.value, { id: item.id, name: item.name }];
    searchKey.value = '';
    loadDynamicList();
    return;
  }
  if (previewLoadingId.value) return;
  previewLoadingId.value = item.id;
  try {
    const template = await queryNodeTemplatePreview({ id: item.id, excludeAppId: props.excludeAppId });
    emit('add', template as unknown as FlowNodeTemplateType);
  } catch (error: any) {
    // 蓝本语义：获取工具详情失败，不落残缺节点
    message.error(error?.response?.data?.detail || '获取工具详情失败');
  } finally {
    previewLoadingId.value = '';
  }
}

function handleDynamicDragStart(event: DragEvent, item: NodeTemplateSummary) {
  if (item.isFolder) return;
  // 动态项拖拽只带摘要；drop 端异步取 previewNode 后落图
  event.dataTransfer?.setData('application/workflow-node-summary', JSON.stringify(item));
  if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
}

// 首次即为 basic，无需请求；导出给父组件在打开时重置
defineExpose({
  reset: () => {
    activeTab.value = 'basic';
    handleTabChange();
  },
});

// 动态节点类型引用，防止 tree-shake 掉 enum 导入（同时供模板内联使用）
void FlowNodeTypeEnum;
</script>

<style scoped lang="less">
// 蓝本 NodeTemplatesModal：460px 全高左侧面板
.node-template-panel {
  width: 460px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-right: 1px solid #e4e7ee;
  box-shadow: 8px 0 24px rgba(19, 51, 107, 0.06);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 6px;

  strong {
    font-size: 15px;
    color: #111824;
  }

  .panel-close {
    border: none;
    background: transparent;
    color: #8a95a7;
    cursor: pointer;
    font-size: 13px;
    border-radius: 6px;
    padding: 4px 6px;

    &:hover {
      background: #f0f1f6;
      color: #111824;
    }
  }
}

.panel-tabs {
  padding: 0 16px;

  :deep(.ant-tabs-nav) {
    margin-bottom: 8px;
  }
}

.panel-search {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 16px 8px;
  padding: 6px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #94a3b8;

  input {
    flex: 1;
    border: none;
    outline: none;
    font-size: 13px;
    background: transparent;
  }
}

.panel-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0 16px 8px;

  .tag-item {
    cursor: pointer;
    user-select: none;
  }
}

.panel-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 16px 8px;
  font-size: 12px;
  color: #475569;

  a {
    color: #3370ff;
    cursor: pointer;
  }

  .crumb-sep {
    font-size: 10px;
    color: #94a3b8;
  }
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 16px 12px;
}

.menu-group {
  & + .menu-group {
    margin-top: 14px;
  }

  .group-title {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: #8a95a7;
    margin: 4px 0 8px;
  }
}

.grid-two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.grid-one {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.template-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: 1px solid #eef0f5;
  border-radius: 10px;
  background: #ffffff;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.12s ease, box-shadow 0.12s ease, background 0.12s ease;

  &:hover {
    border-color: #3370ff;
    box-shadow: 0 2px 10px rgba(51, 112, 255, 0.1);
  }

  &:active {
    background: #f5f8ff;
  }

  // 双列紧凑项（基础功能/系统工具）：固定行高，图标+名称，简介走 Tooltip
  &.compact {
    height: 44px;
    padding: 0 10px;

    .compact-name {
      flex: 1;
      min-width: 0;
      font-size: 13px;
      font-weight: 500;
      color: #111824;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
  }

  // 单列行项（我的工具/Agent）：名称 + 单行简介
  &.row {
    padding: 10px 12px;
  }

  .template-icon {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: color-mix(in srgb, currentColor 10%, #ffffff);
    font-size: 15px;
    flex-shrink: 0;

    &.dynamic {
      color: #6f5dd7;
      background: #f0eeff;
      overflow: hidden;

      img {
        width: 100%;
        height: 100%;
        object-fit: cover;
      }
    }
  }

  &.row .template-icon {
    width: 34px;
    height: 34px;
    font-size: 17px;
  }

  .template-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;

    strong {
      font-size: 13px;
      font-weight: 500;
      color: #111824;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    em {
      font-size: 12px;
      font-style: normal;
      color: #94a3b8;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .status-tag {
      margin-left: 4px;
      font-size: 10px;
      line-height: 16px;
      padding: 0 4px;
    }
  }

  .item-suffix {
    color: #94a3b8;
    font-size: 12px;
    flex-shrink: 0;
  }
}

.panel-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 32px 0;

  .retry-btn {
    width: fit-content;
  }
}

.panel-empty {
  text-align: center;
  color: #94a3b8;
  font-size: 12px;
  padding: 32px 0;
}

.panel-footer {
  border-top: 1px solid #f0f1f6;
  padding: 10px 16px;
  font-size: 12px;

  a {
    color: #3370ff;
    cursor: pointer;
  }
}
</style>
