<template>
  <div class="workbench-panel">
    <div class="market-heading">
      <div>
        <span>SYSTEM TOOLS</span>
        <h2>系统工具</h2>
      </div>
      <div class="market-search">
        <SearchOutlined />
        <input v-model="keyword" placeholder="搜索系统工具..." />
      </div>
      <button class="market-refresh" type="button" @click="loadTools">刷新</button>
    </div>

    <p class="wb-panel-tip">平台内置工具，随编排运行时发版；可在工作流的工具调用中直接挂载，无需自行开发。</p>

    <div v-if="categories.length > 1" class="category-filter" aria-label="系统工具分类筛选">
      <button
        v-for="cat in categories"
        :key="cat.value"
        type="button"
        :class="['category-filter-item', { active: categoryFilter === cat.value }]"
        @click="categoryFilter = categoryFilter === cat.value ? '' : cat.value"
      >
        <span>{{ cat.value || '通用' }}</span>
        <em>{{ cat.count }}</em>
      </button>
    </div>

    <div v-if="loading" class="status-box"><LoadingOutlined /> 正在加载系统工具...</div>
    <div v-else-if="loadFailed && !tools.length" class="wb-unready">
      <ApiOutlined />
      <strong>系统工具目录暂不可用</strong>
      <p>工作流运行时（agent-api）未连接或未部署，恢复后点击刷新即可。</p>
      <a-button size="small" @click="loadTools">重试</a-button>
    </div>
    <div v-else class="wb-grid">
      <div
        v-for="tool in filteredTools"
        :key="tool.id"
        class="wb-card"
        role="button"
        tabindex="0"
        title="查看工具详情"
        @click="showDetail(tool)"
        @keydown.enter="showDetail(tool)"
      >
        <div class="wb-card-head">
          <span class="wb-tile system">
            <ToolOutlined />
          </span>
          <div class="wb-card-ident">
            <strong class="wb-card-title">{{ tool.name }}</strong>
            <span class="wb-card-sub">{{ tool.category || '通用' }}</span>
          </div>
        </div>

        <p class="wb-card-desc">{{ tool.description || '暂无描述' }}</p>

        <div class="wb-card-foot">
          <span class="wb-status published">
            <i></i>
            内置 · 可挂载
          </span>
          <div class="wb-card-ops" @click.stop @keydown.enter.stop>
            <button class="wb-op" type="button" @click="showDetail(tool)"><EyeOutlined /> 详情</button>
          </div>
        </div>
      </div>

      <div v-if="tools.length && !filteredTools.length" class="wb-empty">没有匹配的系统工具，换个关键词试试</div>
      <div v-else-if="!tools.length" class="wb-empty">暂无系统工具</div>
    </div>

    <a-drawer v-model:open="detailVisible" :title="activeTool?.name || '工具详情'" :width="420">
      <template v-if="activeTool">
        <div class="tool-detail">
          <p class="tool-desc">{{ activeTool.description || '暂无描述' }}</p>
          <div class="detail-item">
            <label>节点类型</label>
            <code>{{ activeTool.nodeType }}</code>
          </div>
          <div class="detail-item">
            <label>分类</label>
            <span>{{ activeTool.category || '通用' }}</span>
          </div>
          <div class="detail-item">
            <label>输入参数</label>
            <div class="key-chips">
              <code v-for="key in activeTool.inputKeys || []" :key="key">{{ key }}</code>
              <span v-if="!(activeTool.inputKeys || []).length" class="empty">无</span>
            </div>
          </div>
          <div class="detail-item">
            <label>输出参数</label>
            <div class="key-chips">
              <code v-for="key in activeTool.outputKeys || []" :key="key">{{ key }}</code>
              <span v-if="!(activeTool.outputKeys || []).length" class="empty">无</span>
            </div>
          </div>
          <p class="detail-tip">在工作流编辑器中通过「HTTP 请求 / 工具节点」挂载使用。</p>
        </div>
      </template>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { ApiOutlined, EyeOutlined, LoadingOutlined, SearchOutlined, ToolOutlined } from '@ant-design/icons-vue';
import { queryBuiltinWorkflowTools, type WorkflowToolCatalogItem } from '../../workflow/api/workflow.api';

const emit = defineEmits<{
  (e: 'count', value: number): void;
}>();

const keyword = ref('');
const loading = ref(false);
const loadFailed = ref(false);
const categoryFilter = ref('');
const tools = ref<WorkflowToolCatalogItem[]>([]);
const detailVisible = ref(false);
const activeTool = ref<WorkflowToolCatalogItem | null>(null);

const categories = computed(() => {
  const counter = new Map<string, number>();
  tools.value.forEach((tool) => {
    const cat = tool.category || '';
    counter.set(cat, (counter.get(cat) || 0) + 1);
  });
  return Array.from(counter.entries()).map(([value, count]) => ({ value, count }));
});

const filteredTools = computed(() => {
  const key = keyword.value.trim().toLowerCase();
  return tools.value.filter((tool) => {
    if (categoryFilter.value && (tool.category || '') !== categoryFilter.value) return false;
    if (!key) return true;
    return (
      tool.name.toLowerCase().includes(key) ||
      String(tool.description || '').toLowerCase().includes(key) ||
      String(tool.category || '').toLowerCase().includes(key)
    );
  });
});

async function loadTools() {
  loading.value = true;
  loadFailed.value = false;
  try {
    const list = await queryBuiltinWorkflowTools();
    tools.value = Array.isArray(list) ? list : [];
    emit('count', tools.value.length);
  } catch {
    tools.value = [];
    loadFailed.value = true;
  } finally {
    loading.value = false;
  }
}

function showDetail(tool: WorkflowToolCatalogItem) {
  activeTool.value = tool;
  detailVisible.value = true;
}

onMounted(loadTools);
</script>

<style scoped lang="less">
.tool-detail {
  display: flex;
  flex-direction: column;
  gap: 14px;

  .tool-desc {
    margin: 0;
    color: #334155;
    font-size: 13px;
  }

  .detail-item {
    label {
      display: block;
      font-size: 12px;
      color: #94a3b8;
      margin-bottom: 4px;
    }

    code {
      background: #f1f5f9;
      border-radius: 4px;
      padding: 1px 6px;
      font-size: 12px;
      color: #334155;
    }
  }

  .key-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;

    .empty {
      font-size: 12px;
      color: #cbd5e1;
    }
  }

  .detail-tip {
    margin: 0;
    font-size: 12px;
    color: #94a3b8;
  }
}
</style>
