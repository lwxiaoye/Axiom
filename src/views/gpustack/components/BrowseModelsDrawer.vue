<template>
  <BasicDrawer
    v-bind="$attrs"
    @register="registerDrawer"
    title="浏览更多模型"
    :width="drawerWidth"
    :showFooter="false"
    :wrapClassName="'browse-models-drawer'"
  >
    <div class="bm-layout">
      <!-- 左栏：模型列表 -->
      <section class="bm-col bm-col-list">
        <div class="bm-toolbar">
          <a-input-search
            v-model:value="searchKeyword"
            placeholder="搜索模型名称"
            allow-clear
            size="small"
            @search="reloadFromFirstPage"
          />
          <a-select
            v-model:value="sortBy"
            size="small"
            placeholder="排序"
            style="width: 110px"
            :options="MODEL_LIBRARY_SORT_OPTIONS"
            @change="reloadFromFirstPage"
          />
          <a-select
            v-model:value="quantFilter"
            mode="multiple"
            size="small"
            placeholder="量化"
            style="width: 150px"
            :maxTagCount="1"
            :options="QUANTIZATION_OPTIONS"
            :allow-clear="true"
            @change="reloadFromFirstPage"
          />
          <div class="pagination-wrap" v-if="total > pageSize">
            <a-button
              class="pagination-icon-btn"
              size="small"
              :disabled="page <= 1"
              title="上一页"
              aria-label="上一页"
              @click="handlePreviousPage"
            >
              <Icon icon="ant-design:left-outlined" />
            </a-button>
            <span class="pagination-text">第 {{ page }} / {{ modelScopeTotalPages }} 页</span>
            <a-button
              class="pagination-icon-btn"
              size="small"
              :disabled="page >= modelScopeBrowsablePages"
              title="下一页"
              aria-label="下一页"
              @click="handleNextPage"
            >
              <Icon icon="ant-design:right-outlined" />
            </a-button>
          </div>
        </div>

        <a-spin :spinning="loading" size="small">
          <a-empty v-if="!loading && !list.length" description="未找到模型" class="empty" />
          <div class="model-list">
            <div
              v-for="m in sortedList"
              :key="m.id"
              class="model-row"
              :class="{ active: selectedId === m.id }"
              @click="onSelectModel(m)"
            >
              <div class="row-icon">
                <img v-if="m.icon" :src="m.icon" :alt="m.name" @error="onImgError" />
                <Icon v-else icon="ant-design:appstore-outlined" :size="20" />
              </div>
              <div class="row-main">
                <div class="row-title">
                  <span class="row-name">{{ m.name }}</span>
                  <a-tag v-for="c in (m.categories || []).slice(0, 1)" :key="c" :color="categoryColor(c)" class="row-tag">
                    {{ categoryLabel(c) }}
                  </a-tag>
                </div>
                <div class="row-stats">
                  <span title="更新时间"><Icon icon="ant-design:calendar-outlined" /> {{ metaMap[m.id]?.updatedAt || m.release_date || '-' }}</span>
                  <span title="点赞量"><Icon icon="ant-design:like-outlined" /> {{ formatCount(metaMap[m.id]?.likes) }}</span>
                  <span title="下载量"><Icon icon="ant-design:download-outlined" /> {{ formatCount(metaMap[m.id]?.downloads) }}</span>
                </div>
              </div>
              <div class="row-compat" @click.stop>
                <!-- 兼容状态 -->
                <a-tooltip v-if="compatMap[m.id]?.state === 'incompatible'" :title="(compatMap[m.id].messages || []).join('；') || '当前机器不兼容'">
                  <Icon icon="ant-design:exclamation-circle-filled" class="compat-no" />
                </a-tooltip>
                <a-tooltip v-else-if="compatMap[m.id]?.state === 'compatible'" title="当前机器兼容">
                  <Icon icon="ant-design:check-circle-filled" class="compat-ok" />
                </a-tooltip>
                <a-tooltip v-else title="兼容性未知">
                  <Icon icon="ant-design:question-circle-outlined" class="compat-unknown" />
                </a-tooltip>
              </div>
            </div>
          </div>
        </a-spin>

      </section>

      <!-- 中栏：README -->
      <section class="bm-col bm-col-readme">
        <div v-if="!selectedModel" class="col-placeholder">
          <Icon icon="ant-design:file-text-outlined" :size="40" />
          <p>从左侧选择一个模型查看介绍</p>
        </div>
        <template v-else>
          <div class="readme-head">
            <div class="readme-head-icon">
              <img v-if="selectedModel.icon" :src="selectedModel.icon" :alt="selectedModel.name" @error="onImgError" />
              <Icon v-else icon="ant-design:appstore-outlined" :size="24" />
            </div>
            <div class="readme-head-info">
              <div class="readme-head-name">{{ selectedModel.name }}</div>
              <div class="readme-head-tags">
                <a-tag v-for="c in selectedModel.categories || []" :key="c" :color="categoryColor(c)" class="mini-tag">
                  {{ categoryLabel(c) }}
                </a-tag>
              </div>
            </div>
          </div>
          <ModelReadme
            v-if="selectedReadmeReady"
            :content="selectedReadmeContent"
            :fallbackDescription="selectedModel.description"
            :homeUrl="selectedModel.home"
          />
        </template>
      </section>

      <!-- 右栏：配置表单 -->
      <section class="bm-col bm-col-config">
        <div v-if="!selectedModel" class="col-placeholder">
          <Icon icon="ant-design:setting-outlined" :size="40" />
          <p>选择模型后填写部署配置</p>
        </div>
        <template v-else>
          <a-form ref="formRef" :model="formState" layout="vertical" class="config-form">
            <a-collapse v-model:activeKey="configPanelKeys" class="config-collapse" :bordered="false">
              <!-- 基本信息 -->
              <a-collapse-panel key="basic" header="基本信息" data-config-panel="basic">
                <a-form-item label="模型名称" name="name" required>
                  <a-input v-model:value="formState.name" placeholder="推理引用名" />
                </a-form-item>
                <a-form-item label="集群">
                  <a-select
                    v-model:value="formState.cluster_id"
                    :options="clusterOptions"
                    :loading="clusterLoading"
                    placeholder="选择集群"
                    allow-clear
                    @change="evaluatePageCompat"
                  />
                </a-form-item>
                <a-row :gutter="12">
                  <a-col :span="12">
                    <a-form-item label="推理后端">
                      <a-select v-model:value="formState.backend" :options="INFERENCE_BACKEND_OPTIONS" placeholder="自动" allow-clear />
                    </a-form-item>
                  </a-col>
                  <a-col :span="12">
                    <a-form-item label="副本数" name="replicas">
                      <a-input-number v-model:value="formState.replicas" :min="0" :max="20" style="width: 100%" />
                    </a-form-item>
                  </a-col>
                </a-row>
                <a-form-item label="描述">
                  <a-textarea v-model:value="formState.description" :rows="2" placeholder="模型描述（可选）" />
                </a-form-item>
              </a-collapse-panel>

              <!-- 性能 -->
              <a-collapse-panel key="perf" header="性能" data-config-panel="perf">
                <a-alert
                  v-if="!isKvCacheSupported"
                  type="info"
                  show-icon
                  banner
                  message="扩展 KV 缓存仅在使用内置推理后端（vLLM 或 SGLang）时支持"
                  style="margin-bottom: 12px"
                />
                <a-form-item>
                  <template #label>
                    启用扩展 KV 缓存
                    <a-tooltip title="对应 LMCache，需 vLLM/SGLang 后端"><Icon icon="ant-design:info-circle-outlined" /></a-tooltip>
                  </template>
                  <a-switch
                    v-model:checked="kvEnabled"
                    :disabled="!isKvCacheSupported"
                    checked-children="开"
                    un-checked-children="关"
                  />
                </a-form-item>
                <template v-if="kvEnabled && isKvCacheSupported">
                  <a-form-item label="内存与显存比例">
                    <a-input-number v-model:value="kvCache.ram_ratio" :min="0" :step="0.1" style="width: 100%" />
                  </a-form-item>
                  <a-form-item label="内存最大占用 (GiB)">
                    <a-input-number v-model:value="kvCache.ram_size" :min="0" style="width: 100%" />
                  </a-form-item>
                  <a-form-item label="缓存分块大小">
                    <a-input-number v-model:value="kvCache.chunk_size" :min="0" style="width: 100%" />
                  </a-form-item>
                </template>
              </a-collapse-panel>

              <!-- 调度 -->
              <a-collapse-panel key="schedule" header="调度" data-config-panel="schedule">
                <a-row :gutter="12">
                  <a-col :span="12">
                    <a-form-item label="调度方式">
                      <a-select v-model:value="scheduleMode" :options="SCHEDULE_MODE_OPTIONS" />
                    </a-form-item>
                  </a-col>
                  <a-col :span="12">
                    <a-form-item label="放置策略">
                      <a-select v-model:value="formState.placement_strategy" :options="PLACEMENT_STRATEGY_SELECT_OPTIONS" />
                    </a-form-item>
                  </a-col>
                </a-row>
                <a-form-item v-if="scheduleMode === 'manual'" label="每副本 GPU 数">
                  <a-input-number v-model:value="gpusPerReplica" :min="1" :max="8" style="width: 100%" placeholder="自动" />
                </a-form-item>
                <div class="kv-block">
                  <div class="kv-block-title">
                    <span>节点选择器</span>
                    <a-button type="link" size="small" @click="addSelectorRow">+ 添加</a-button>
                  </div>
                  <div v-for="(row, i) in selectorRows" :key="i" class="kv-row">
                    <a-select
                      v-model:value="row.key"
                      size="small"
                      style="width: 130px"
                      :options="NODE_SELECTOR_KEY_OPTIONS"
                      placeholder="键"
                      @change="(v) => onSelectorKeyChange(row, v)"
                    />
                    <a-select
                      v-model:value="row.value"
                      size="small"
                      style="flex: 1"
                      show-search
                      :options="nodeValueOptions(row.key)"
                      placeholder="值"
                    />
                    <a-button type="text" size="small" danger @click="selectorRows.splice(i, 1)">
                      <Icon icon="ant-design:delete-outlined" />
                    </a-button>
                  </div>
                </div>
              </a-collapse-panel>

              <!-- 高级 -->
              <a-collapse-panel key="advanced" header="高级" data-config-panel="advanced">
                <a-form-item label="模型类别">
                  <a-select v-model:value="formState.categories" mode="multiple" :options="MODEL_CATEGORY_OPTIONS" placeholder="选择类别" />
                </a-form-item>
                <a-form-item label="后端参数（一行一个，如 --max-model-len=8192）">
                  <a-textarea v-model:value="backendParamsText" :rows="3" placeholder="--max-model-len=8192" />
                </a-form-item>
                <div class="kv-block">
                  <div class="kv-block-title">
                    <span>环境变量</span>
                    <a-button type="link" size="small" @click="addEnvRow">+ 添加</a-button>
                  </div>
                  <div v-for="(row, i) in envRows" :key="i" class="kv-row">
                    <a-input v-model:value="row.key" size="small" placeholder="键" style="width: 40%" />
                    <a-input v-model:value="row.value" size="small" placeholder="值" style="flex: 1" />
                    <a-button type="text" size="small" danger @click="envRows.splice(i, 1)">
                      <Icon icon="ant-design:delete-outlined" />
                    </a-button>
                  </div>
                </div>
                <div class="advanced-checks">
                  <a-checkbox v-model:checked="formState.distributed_inference_across_workers">允许跨节点分布式推理</a-checkbox>
                  <a-checkbox v-model:checked="formState.restart_on_error">错误时重启</a-checkbox>
                  <a-checkbox v-model:checked="enableModelRoute">启用模型路由</a-checkbox>
                  <a-checkbox v-model:checked="formState.generic_proxy">启用通用代理</a-checkbox>
                </div>
              </a-collapse-panel>
            </a-collapse>
          </a-form>

          <div class="config-footer">
            <div class="compat-warn" v-if="selectedCompat?.state === 'incompatible'">
              <Icon icon="ant-design:warning-outlined" />
              <div>
                <strong>Unable to find a schedulable worker for the model</strong>
                <p>{{ (selectedCompat.messages || []).join('；') || '当前机器可能不兼容' }}</p>
              </div>
            </div>
            <div class="footer-btns">
              <a-button size="small" @click="closeDrawer">取消</a-button>
              <a-button type="primary" size="small" :loading="deploying" @click="handleDeploy">提交部署</a-button>
            </div>
          </div>
        </template>
      </section>
    </div>
  </BasicDrawer>
</template>

<script lang="ts">
  import { defineComponent, ref, reactive, computed, onBeforeUnmount } from 'vue';
  import { BasicDrawer, useDrawerInner } from '@/components/Drawer';
  import { Icon } from '/@/components/Icon';
  import { useMessage } from '/@/hooks/web/useMessage';
  import ModelReadme from './ModelReadme.vue';
  import {
    modelScopeSearch,
    modelScopeModelDetail,
    modelSpecs,
    modelDeploy,
    modelEvaluation,
    workerList,
  } from '../gpustack.api';
  import {
    MODEL_LIBRARY_SORT_OPTIONS,
    QUANTIZATION_OPTIONS,
    INFERENCE_BACKEND_OPTIONS,
    BUILT_IN_BACKENDS,
    MODEL_CATEGORY_OPTIONS,
    NODE_SELECTOR_KEY_OPTIONS,
    NODE_SELECTOR_VALUE_OPTIONS,
    SCHEDULE_MODE_OPTIONS,
    PLACEMENT_STRATEGY_SELECT_OPTIONS,
  } from '../gpustack.enums';
  import { fetchHubMeta, formatCount } from '../utils/hub';

  // 与卡片页一致的分类映射
  const CATEGORY_MAP: Record<string, { label: string; color: string }> = {
    llm: { label: '大语言模型', color: 'blue' },
    image: { label: '图像', color: 'purple' },
    text_to_speech: { label: '语音合成', color: 'cyan' },
    speech_to_text: { label: '语音识别', color: 'cyan' },
    embedding: { label: '向量', color: 'green' },
    reranker: { label: '重排', color: 'orange' },
    vision: { label: '视觉', color: 'magenta' },
    unknown: { label: '未知', color: 'default' },
  };

  interface CompatInfo {
    state: 'compatible' | 'incompatible' | 'unknown';
    messages: string[];
  }

  type ConfigPanelKey = 'basic' | 'perf' | 'schedule' | 'advanced';

  const MODELSCOPE_MAX_PAGE = 1000;

  export default defineComponent({
    name: 'BrowseModelsDrawer',
    components: { BasicDrawer, Icon, ModelReadme },
    emits: ['success', 'register'],
    setup(_, { emit }) {
      const { createMessage } = useMessage();

      const drawerWidth = '75vw';
      const loading = ref(false);
      const list = ref<any[]>([]);
      const total = ref(0);
      const page = ref(1);
      const pageSize = ref(10);
      const searchKeyword = ref('');
      const category = ref('');
      const sortBy = ref<string>('updated');
      const quantFilter = ref<string[]>([]);
      const selectedId = ref<string | null>(null);
      const selectedReadmeContent = ref('');
      const selectedReadmeReady = ref(false);

      // HF/ModelScope 元数据缓存：modelId -> { likes, downloads, ... }
      const metaMap = reactive<Record<string, any>>({});
      // 兼容性缓存：modelId -> CompatInfo
      const compatMap = reactive<Record<string, CompatInfo>>({});
      // 规格缓存：modelId -> ModelSpec[]
      const specMap = reactive<Record<string, any[]>>({});

      // 规格与配置
      const specs = ref<any[]>([]);
      const configPanelKeys = ref<ConfigPanelKey[]>(['basic']);
      const clusterOptions = ref<any[]>([]);
      const clusterLoading = ref(false);
      const scheduleMode = ref('auto');
      const gpusPerReplica = ref<number | undefined>(undefined);
      const selectorRows = ref<{ key: string; value: string }[]>([]);
      const envRows = ref<{ key: string; value: string }[]>([]);
      const backendParamsText = ref('');
      const kvEnabled = ref(false);
      const kvCache = reactive({ ram_ratio: 1.2, ram_size: undefined as any, chunk_size: undefined as any });
      const enableModelRoute = ref(true);
      const deploying = ref(false);
      const nodeSelectorOptions = reactive<Record<string, { label: string; value: string }[]>>({
        os: [],
        arch: [],
        'worker-name': [],
      });

      const formState = reactive<any>({
        name: '',
        description: '',
        cluster_id: undefined,
        backend: undefined,
        replicas: 1,
        placement_strategy: 'spread',
        categories: [] as string[],
        distributed_inference_across_workers: true,
        restart_on_error: true,
        generic_proxy: false,
      });

      const selectedModel = computed(() => list.value.find((m) => m.id === selectedId.value) || null);

      const selectedCompat = computed<CompatInfo | null>(() =>
        selectedId.value ? compatMap[selectedId.value] || null : null,
      );

      const modelScopePaginationTotal = computed(() =>
        Math.min(total.value, MODELSCOPE_MAX_PAGE * pageSize.value),
      );

      const modelScopeTotalPages = computed(() =>
        Math.max(1, Math.ceil(total.value / pageSize.value)),
      );

      const modelScopeBrowsablePages = computed(() =>
        Math.max(1, Math.ceil(modelScopePaginationTotal.value / pageSize.value)),
      );

      const isKvCacheSupported = computed(
        () => !formState.backend || BUILT_IN_BACKENDS.includes(formState.backend),
      );

      // 列表排序（前端）：无后端榜单时，likes/downloads 取 metaMap，trending 缺失按 downloads 估
      const sortedList = computed(() => {
        const kw = searchKeyword.value.trim().toLowerCase();
        let arr = list.value.filter((m) => !kw || (m.name || '').toLowerCase().includes(kw));
        const key = sortBy.value;
        arr = [...arr].sort((a, b) => {
          switch (key) {
            case 'likes':
              return (metaMap[b.id]?.likes ?? 0) - (metaMap[a.id]?.likes ?? 0);
            case 'downloads':
              return (metaMap[b.id]?.downloads ?? 0) - (metaMap[a.id]?.downloads ?? 0);
            case 'trending':
              return Number(!!metaMap[b.id]?.trending) - Number(!!metaMap[a.id]?.trending)
                || (metaMap[b.id]?.downloads ?? 0) - (metaMap[a.id]?.downloads ?? 0);
            case 'updated':
            default:
              return String(metaMap[b.id]?.updatedAt || b.release_date || '').localeCompare(String(metaMap[a.id]?.updatedAt || a.release_date || ''));
          }
        });
        return arr;
      });

      function categoryLabel(c: string) {
        return CATEGORY_MAP[c]?.label || c;
      }
      function categoryColor(c: string) {
        return CATEGORY_MAP[c]?.color || 'default';
      }

      function nodeValueOptions(key: string) {
        if (nodeSelectorOptions[key]?.length) return nodeSelectorOptions[key];
        return NODE_SELECTOR_VALUE_OPTIONS[key] || [];
      }

      function onSelectorKeyChange(row: { key: string; value: string }, _v: string) {
        row.value = '';
      }
      function addSelectorRow() {
        selectorRows.value.push({ key: '', value: '' });
      }
      function addEnvRow() {
        envRows.value.push({ key: '', value: '' });
      }

      function openConfigPanel(key: ConfigPanelKey) {
        if (!configPanelKeys.value.includes(key)) {
          configPanelKeys.value = [...configPanelKeys.value, key];
        }
      }

      function onImgError(e: any) {
        e.target.style.display = 'none';
      }

      const [registerDrawer, { setDrawerProps, closeDrawer }] = useDrawerInner(async (data: any) => {
        setDrawerProps({ confirmLoading: false });
        category.value = data?.category || '';
        searchKeyword.value = data?.search || '';
        selectedId.value = null;
        selectedReadmeContent.value = '';
        selectedReadmeReady.value = false;
        page.value = 1;
        resetConfig();
        await loadClusters();
        await loadNodeSelectorOptions();
        await loadList();
      });

      async function loadList() {
        if (page.value > MODELSCOPE_MAX_PAGE) {
          page.value = MODELSCOPE_MAX_PAGE;
        }
        loading.value = true;
        try {
          const res = await modelScopeSearch(buildModelScopeSearchPayload());
          const data = res?.Data || res?.data || res || {};
          const modelData = data.Model || data.model || data;
          const records = modelData.Models || modelData.models || data.Models || data.models || [];
          list.value = records.map(normalizeModelScopeItem);
          total.value = Number(
            modelData.TotalCount ||
              modelData.totalCount ||
              data.TotalCount ||
              data.totalCount ||
              records.length ||
              0,
          );
          enrichModelInfo(list.value);
        } catch {
          list.value = [];
          total.value = 0;
        } finally {
          loading.value = false;
        }
      }

      function reloadFromFirstPage() {
        page.value = 1;
        loadList();
      }

      function changeModelScopePage(nextPage: number) {
        const targetPage = Math.min(Math.max(nextPage, 1), modelScopeBrowsablePages.value);
        if (targetPage === page.value) return;
        page.value = targetPage;
        selectedId.value = null;
        selectedReadmeContent.value = '';
        selectedReadmeReady.value = false;
        loadList();
      }

      function handlePreviousPage() {
        changeModelScopePage(page.value - 1);
      }

      function handleNextPage() {
        changeModelScopePage(page.value + 1);
      }

      function buildModelScopeSearchPayload() {
        const tags = quantFilter.value.map((q) => q.toLowerCase());
        return {
          PageSize: pageSize.value,
          PageNumber: Math.min(page.value, MODELSCOPE_MAX_PAGE),
          Name: searchKeyword.value || '',
          tags,
          SortBy: modelScopeSortBy(sortBy.value),
          tasks: [],
          Criterion: tags.map((tag) => ({
            category: 'tags',
            predicate: 'contains',
            values: [tag],
          })),
        };
      }

      function modelScopeSortBy(value: string) {
        const map: Record<string, string> = {
          trending: 'Default',
          likes: 'StarsCount',
          downloads: 'DownloadsCount',
          updated: 'GmtModified',
        };
        return map[value] || 'DownloadsCount';
      }

      function normalizeModelScopeItem(item: any) {
        const repo = [item.Path, item.Name].filter(Boolean).join('/');
        const updatedAt = formatModelScopeTime(item.LastUpdatedTime);
        const tags = (item.Tags || []).map((tag: string) => String(tag));
        return {
          ...item,
          id: `modelscope:${repo}`,
          name: repo || item.Name,
          repo,
          model_scope_model_id: repo,
          source: 'modelscope',
          description: item.Description || item.ChineseName || '',
          release_date: updatedAt,
          home: repo ? `https://modelscope.cn/models/${repo}` : '',
          categories: inferCategories(item),
          quantization: inferQuantization(tags),
          likes: Number(item.Stars || 0),
          downloads: Number(item.Downloads || item.DownloadsCount || 0),
          updatedAt,
        };
      }

      function formatModelScopeTime(value?: number) {
        if (!value) return '';
        const diff = Date.now() - Number(value) * 1000;
        const yearMs = 365 * 24 * 60 * 60 * 1000;
        if (diff >= yearMs) return `${Math.max(1, Math.floor(diff / yearMs))} 年前`;
        const monthMs = 30 * 24 * 60 * 60 * 1000;
        if (diff >= monthMs) return `${Math.max(1, Math.floor(diff / monthMs))} 月前`;
        const dayMs = 24 * 60 * 60 * 1000;
        return `${Math.max(1, Math.floor(diff / dayMs))} 天前`;
      }

      function inferCategories(item: any) {
        const tasks = item.Tasks || [];
        if (tasks.some((task: any) => String(task.Name || '').includes('image'))) return ['image'];
        if (tasks.some((task: any) => String(task.Name || '').includes('embedding'))) return ['embedding'];
        return ['llm'];
      }

      function inferQuantization(tags: string[]) {
        const lower = tags.map((tag) => tag.toLowerCase());
        if (lower.includes('fp8')) return 'FP8';
        if (lower.includes('awq')) return 'AWQ';
        if (lower.includes('gptq')) return 'GPTQ';
        return undefined;
      }

      // 批量补齐 specs / likes / downloads / 兼容性（失败静默）
      async function enrichModelInfo(models: any[]) {
        await Promise.all(
          models.map(async (m) => {
            try {
              const specArr = await ensureSpecs(m.id);
              const def = pickSpec(specArr);
              if (!metaMap[m.id]) {
                metaMap[m.id] = {
                  likes: m.likes,
                  downloads: m.downloads,
                  updatedAt: m.updatedAt,
                  trending: false,
                };
              }
              if (def && !metaMap[m.id]?.downloads) {
                const meta = await fetchHubMeta(def);
                metaMap[m.id] = { ...metaMap[m.id], ...(meta || {}) };
              }
              if (def && !compatMap[m.id]) {
                evaluateCompat(m.id, def);
              }
            } catch {
              metaMap[m.id] = {};
              compatMap[m.id] = { state: 'unknown', messages: [] };
            }
          }),
        );
      }

      async function ensureSpecs(modelId: string): Promise<any[]> {
        if (specMap[modelId]) return specMap[modelId];
        const model = list.value.find((m) => m.id === modelId);
        if (model?.source === 'modelscope') {
          const spec = buildSpecFromModelScope(model);
          specMap[modelId] = [spec];
          return [spec];
        }
        const sp = await modelSpecs(modelId);
        const specArr: any[] = Array.isArray(sp) ? sp : sp?.items || [];
        specMap[modelId] = specArr;
        return specArr;
      }

      function buildSpecFromModelScope(model: any) {
        return {
          source: 'model_scope',
          model_scope_model_id: model.model_scope_model_id,
          name: defaultDeployName(model),
          description: model.description || '',
          categories: model.categories || ['llm'],
          backend: pickBackend(model),
          quantization: model.quantization,
          replicas: 1,
          placement_strategy: 'spread',
          distributed_inference_across_workers: true,
          restart_on_error: true,
        };
      }

      function pickBackend(model: any) {
        const tags = (model.Tags || []).map((tag: string) => String(tag).toLowerCase());
        if (tags.includes('sglang')) return 'SGLang';
        return 'vLLM';
      }

      function defaultDeployName(model: any) {
        return String(model.Name || model.name || 'model')
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9._-]+/g, '-')
          .replace(/^-+|-+$/g, '');
      }

      function pickSpec(specArr: any[]) {
        const order: Record<string, number> = { throughput: 0, latency: 1, standard: 2 };
        return [...specArr].sort((a, b) => (order[a.mode] ?? 9) - (order[b.mode] ?? 9))[0];
      }

      async function loadClusters() {
        clusterLoading.value = true;
        try {
          const res = await workerList({ pageNo: 1, pageSize: 100 });
          // 集群信息优先从 overview；此处用 worker 聚合作降级：取首个 cluster_id
          // 简化：后端如提供独立集群接口可替换
          const items = res?.records || [];
          const map = new Map<number, string>();
          items.forEach((w: any) => {
            const cid = w.cluster_id;
            if (cid != null && !map.has(cid)) map.set(cid, w.cluster_name || `集群 ${cid}`);
          });
          clusterOptions.value = Array.from(map.entries()).map(([value, label]) => ({ value, label }));
          if (!formState.cluster_id && clusterOptions.value.length) {
            formState.cluster_id = clusterOptions.value[0].value;
          }
        } catch {
          clusterOptions.value = [];
        } finally {
          clusterLoading.value = false;
        }
      }

      async function loadNodeSelectorOptions() {
        try {
          const res = await workerList({ pageNo: 1, pageSize: 200 });
          const items = res?.records || [];
          (['os', 'arch', 'worker-name'] as const).forEach((key) => {
            const values = new Set<string>();
            items.forEach((w: any) => {
              const labelValue = w.labels?.[key] || (key === 'worker-name' ? w.name : '');
              if (labelValue) values.add(String(labelValue));
            });
            nodeSelectorOptions[key] = Array.from(values).map((value) => ({ label: value, value }));
          });
        } catch {
          nodeSelectorOptions.os = [];
          nodeSelectorOptions.arch = [];
          nodeSelectorOptions['worker-name'] = [];
        }
      }

      function resetConfig() {
        configPanelKeys.value = ['basic'];
        scheduleMode.value = 'auto';
        gpusPerReplica.value = undefined;
        selectorRows.value = [];
        envRows.value = [];
        backendParamsText.value = '';
        kvEnabled.value = false;
        kvCache.ram_ratio = 1.2;
        kvCache.ram_size = undefined;
        kvCache.chunk_size = undefined;
        enableModelRoute.value = true;
        Object.assign(formState, {
          name: '',
          description: '',
          cluster_id: clusterOptions.value[0]?.value,
          backend: undefined,
          replicas: 1,
          placement_strategy: 'spread',
          categories: [],
          distributed_inference_across_workers: true,
          restart_on_error: true,
          generic_proxy: false,
        });
      }

      // 选中模型：加载规格并预填配置 + 触发兼容评估
      let specsReqId = 0;
      let detailReqId = 0;
      async function onSelectModel(m: any) {
        selectedId.value = m.id;
        selectedReadmeContent.value = '';
        selectedReadmeReady.value = false;
        resetConfig();
        const myId = ++specsReqId;
        loadModelScopeDetail(m, myId);
        try {
          const specArr = await ensureSpecs(m.id);
          if (myId !== specsReqId) return; // 已切换
          specs.value = specArr;
          const def = pickSpec(specs.value);
          prefillFromSpec(m, def);
          evaluateCompat(m.id, def);
        } catch {
          specs.value = [];
          // 仍用模型基本信息预填
          prefillFromSpec(m, null);
        }
      }

      async function loadModelScopeDetail(model: any, selectReqId: number) {
        const repo = model?.model_scope_model_id || model?.repo;
        if (!repo) {
          if (selectReqId === specsReqId) selectedReadmeReady.value = true;
          return;
        }
        const myId = ++detailReqId;
        try {
          const res = await modelScopeModelDetail(repo);
          if (selectReqId !== specsReqId || myId !== detailReqId) return;
          const data = res?.Data || res?.data || res?.result?.Data || res?.result?.data || res || {};
          selectedReadmeContent.value =
            data.ReadMeContent ||
            data.readMeContent ||
            data.ReadmeContent ||
            data.readmeContent ||
            '';
          selectedReadmeReady.value = true;
        } catch {
          if (selectReqId === specsReqId && myId === detailReqId) {
            selectedReadmeContent.value = '';
            selectedReadmeReady.value = true;
          }
        }
      }

      function prefillFromSpec(m: any, def: any) {
        formState.name = def?.name || defaultDeployName(m);
        formState.description = m.description || '';
        formState.categories = [...(m.categories || [])];
        if (def) {
          formState.backend = def.backend || undefined;
          formState.placement_strategy = def.placement_strategy || 'spread';
          formState.replicas = def.replicas ?? 1;
          formState.distributed_inference_across_workers = def.distributed_inference_across_workers ?? true;
          formState.cluster_id = def.cluster_id || formState.cluster_id || clusterOptions.value[0]?.value;
          backendParamsText.value = (def.backend_parameters || []).join('\n');
        }
      }

      // 兼容性评估：失败则置 unknown，不阻塞
      async function evaluateCompat(modelId: string, def: any) {
        if (!def) {
          compatMap[modelId] = { state: 'unknown', messages: [] };
          return;
        }
        compatMap[modelId] = { state: 'unknown', messages: [] };
        try {
          const res = await modelEvaluation(formState.cluster_id ?? null, [def]);
          const r = res?.results?.[0] || res?.[0];
          if (r) {
            compatMap[modelId] = {
              state: r.compatible ? 'compatible' : 'incompatible',
              messages: r.compatibility_messages || [],
            };
          }
        } catch {
          compatMap[modelId] = { state: 'unknown', messages: [] };
        }
      }

      async function evaluatePageCompat() {
        await Promise.all(
          list.value.map(async (m) => {
            try {
              const def = pickSpec(await ensureSpecs(m.id));
              await evaluateCompat(m.id, def);
            } catch {
              compatMap[m.id] = { state: 'unknown', messages: [] };
            }
          }),
        );
      }

      function buildPayload() {
        const backendParams = backendParamsText.value
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean);
        const workerSelector: Record<string, string> = {};
        selectorRows.value.forEach((r) => {
          if (r.key && r.value) workerSelector[r.key] = r.value;
        });
        const env: Record<string, string> = {};
        envRows.value.forEach((r) => {
          if (r.key) env[r.key] = r.value;
        });
        const def = pickSpec(specs.value) || {};
        const payload: any = {
          ...formState,
          // 来源/规格字段合并自 spec（库模型部署需要）
          source: def.source,
          huggingface_repo_id: def.huggingface_repo_id,
          huggingface_filename: def.huggingface_filename,
          model_scope_model_id: def.model_scope_model_id,
          model_scope_file_path: def.model_scope_file_path,
          local_path: def.local_path,
          backend_version: def.backend_version,
          backend_parameters: backendParams.length ? backendParams : undefined,
          worker_selector: Object.keys(workerSelector).length ? workerSelector : undefined,
          env: Object.keys(env).length ? env : undefined,
          distributed_inference_across_workers: formState.distributed_inference_across_workers,
          enable_model_route: enableModelRoute.value,
        };
        // 扩展 KV 缓存
        if (kvEnabled.value && isKvCacheSupported.value) {
          payload.extended_kv_cache = {
            enabled: true,
            ram_ratio: kvCache.ram_ratio,
            ram_size: kvCache.ram_size,
            chunk_size: kvCache.chunk_size,
          };
        }
        // 手动调度：每副本 GPU 数
        if (scheduleMode.value === 'manual' && gpusPerReplica.value) {
          payload.gpu_selector = { gpus_per_replica: gpusPerReplica.value };
        }
        return payload;
      }

      async function handleDeploy() {
        if (!selectedId.value || !selectedModel.value) {
          createMessage.warning('请先选择一个模型');
          return;
        }
        if (!formState.name?.trim()) {
          createMessage.warning('请填写模型名称');
          openConfigPanel('basic');
          return;
        }
        deploying.value = true;
        try {
          const payload = buildPayload();
          await modelDeploy(payload);
          createMessage.success('部署任务已提交，正在跳转模型管理');
          emit('success', { id: selectedId.value, name: formState.name });
          closeDrawer();
        } catch (e: any) {
          createMessage.error('部署失败：' + (e?.message || '未知错误'));
        } finally {
          deploying.value = false;
        }
      }

      onBeforeUnmount(() => {
        specsReqId += 1;
        detailReqId += 1;
      });

      return {
        registerDrawer,
        drawerWidth,
        loading,
        list,
        sortedList,
        total,
        modelScopePaginationTotal,
        modelScopeTotalPages,
        modelScopeBrowsablePages,
        page,
        pageSize,
        searchKeyword,
        sortBy,
        quantFilter,
        selectedId,
        selectedReadmeContent,
        selectedReadmeReady,
        selectedModel,
        selectedCompat,
        compatMap,
        metaMap,
        configPanelKeys,
        clusterOptions,
        clusterLoading,
        scheduleMode,
        gpusPerReplica,
        selectorRows,
        envRows,
        backendParamsText,
        kvEnabled,
        kvCache,
        enableModelRoute,
        deploying,
        formState,
        isKvCacheSupported,
        MODEL_LIBRARY_SORT_OPTIONS,
        QUANTIZATION_OPTIONS,
        INFERENCE_BACKEND_OPTIONS,
        MODEL_CATEGORY_OPTIONS,
        NODE_SELECTOR_KEY_OPTIONS,
        SCHEDULE_MODE_OPTIONS,
        PLACEMENT_STRATEGY_SELECT_OPTIONS,
        categoryLabel,
        categoryColor,
        nodeValueOptions,
        onSelectorKeyChange,
        addSelectorRow,
        addEnvRow,
        openConfigPanel,
        reloadFromFirstPage,
        handlePreviousPage,
        handleNextPage,
        evaluatePageCompat,
        onSelectModel,
        handleDeploy,
        closeDrawer,
        onImgError,
        formatCount,
      };
    },
  });
</script>

<style scoped lang="less">
  :global(.browse-models-drawer .ant-drawer-content) {
    height: 100vh;
    overflow: hidden;
  }
  :global(.browse-models-drawer .ant-drawer-wrapper-body) {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }
  :global(.browse-models-drawer .ant-drawer-body) {
    flex: 1;
    min-height: 0;
    padding: 0;
    overflow: hidden;
  }
  :global(.browse-models-drawer .scroll-container),
  :global(.browse-models-drawer .scrollbar) {
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }
  :global(.browse-models-drawer .scrollbar__view) {
    display: flex;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }
  :global(.browse-models-drawer .scrollbar__wrap) {
    height: 100%;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
  }
  :global(.browse-models-drawer .scrollbar__bar) {
    display: none !important;
  }
  .bm-layout {
    display: flex;
    flex: 1 1 auto;
    height: calc(100vh - 60px);
    max-height: calc(100vh - 60px);
    min-height: 0;
    width: 100%;
    overflow: hidden;
    background: #fff;
  }
  .bm-col {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    overflow-x: hidden;
    overflow-y: auto;
    padding: 16px 22px;
  }
  .bm-col-list {
    flex: 0 0 25%;
    min-width: 0;
    padding-right: 24px;
    border-right: 1px solid #edf0f5;
  }
  .bm-col-readme {
    flex: 0 0 50%;
    min-width: 0;
    padding-left: 24px;
    padding-right: 24px;
    border-right: 1px solid #edf0f5;
    :deep(.model-readme-wrap) {
      flex: 1;
      min-height: 0;
    }
  }
  .bm-col-config {
    flex: 0 0 25%;
    min-width: 0;
    padding-left: 24px;
  }
  .bm-toolbar {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 16px;
    :deep(.ant-input-search) {
      grid-column: 1 / -1;
    }
  }
  .empty {
    padding: 60px 0;
  }
  .model-list {
    flex: none;
    overflow: visible;
  }
  .model-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    min-height: 74px;
    padding: 12px 12px 10px;
    border: 1px solid #d9dfe8;
    border-radius: 4px;
    margin-bottom: 12px;
    cursor: pointer;
    transition: all 0.15s;
    &:hover {
      border-color: #9db8e8;
      background: #fbfdff;
    }
    &.active {
      border-color: #1677ff;
      background: #fff;
      box-shadow: inset 3px 0 0 #1677ff;
    }
  }
  .row-icon {
    width: 18px;
    height: 24px;
    padding-top: 3px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    flex-shrink: 0;
    color: #697386;
    img {
      max-width: 80%;
      max-height: 80%;
      object-fit: contain;
    }
  }
  .row-main {
    flex: 1;
    min-width: 0;
  }
  .row-title {
    display: flex;
    align-items: center;
    gap: 6px;
    .row-name {
      font-size: 13px;
      font-weight: 500;
      color: #202124;
      white-space: normal;
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      line-height: 20px;
    }
    .row-tag {
      font-size: 11px;
      line-height: 16px;
      padding: 0 5px;
      margin: 0;
    }
  }
  .row-stats {
    margin-top: 4px;
    font-size: 11px;
    color: #8c8c8c;
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    span {
      display: inline-flex;
      align-items: center;
      gap: 3px;
    }
  }
  .row-compat {
    flex-shrink: 0;
    font-size: 18px;
    .compat-ok {
      color: #52c41a;
    }
    .compat-no {
      color: #ff4d4f;
    }
    .compat-unknown {
      color: #bfbfbf;
    }
  }
  .pagination-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    grid-column: 1 / -1;
    margin-top: 2px;
    padding-top: 2px;
  }
  .pagination-icon-btn {
    width: 28px;
    min-width: 28px;
    padding: 0;
  }
  .pagination-text {
    min-width: 88px;
    text-align: center;
    font-size: 12px;
    color: #4b5563;
  }
  .col-placeholder {
    margin: auto;
    color: #bfbfbf;
    text-align: center;
    p {
      margin-top: 12px;
      font-size: 13px;
    }
  }
  .readme-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid #edf0f5;
    flex-shrink: 0;
  }
  .readme-head-icon {
    display: none;
    align-items: center;
    justify-content: center;
    background: #f5f7fa;
    border-radius: 8px;
    color: #8c8c8c;
    flex-shrink: 0;
    img {
      max-width: 80%;
      max-height: 80%;
      object-fit: contain;
    }
  }
  .readme-head-info {
    min-width: 0;
  }
  .readme-head-name {
    font-size: 16px;
    font-weight: 700;
    color: #1f2937;
  }
  .mini-tag {
    font-size: 11px !important;
    margin-right: 4px;
  }
  .config-form {
    flex: none;
    overflow: visible;
    .config-collapse {
      background: transparent;
      :deep(.ant-collapse-item) {
        margin-bottom: 10px;
        border: 1px solid #edf0f5;
        border-radius: 6px;
        background: #fff;
      }
      :deep(.ant-collapse-header) {
        align-items: center;
        padding: 9px 12px;
        font-weight: 600;
      }
      :deep(.ant-collapse-content-box) {
        padding: 12px;
      }
    }
    :deep(.ant-form-item) {
      margin-bottom: 14px;
    }
    :deep(.ant-input),
    :deep(.ant-select-selector),
    :deep(.ant-input-number),
    :deep(textarea.ant-input) {
      border-radius: 6px;
    }
  }
  .kv-block {
    margin-bottom: 12px;
  }
  .kv-block-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 13px;
    color: #1f2937;
    margin-bottom: 8px;
  }
  .kv-row {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;
  }
  .advanced-checks {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 8px;
  }
  .config-footer {
    flex-shrink: 0;
    padding-top: 12px;
    border-top: 1px solid #edf0f5;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    justify-content: space-between;
    gap: 12px;
  }
  .compat-warn {
    font-size: 12px;
    color: #5f3b00;
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 10px 12px;
    background: #fff7e6;
    border: 1px solid #ffd591;
    border-radius: 6px;
    strong {
      display: block;
      margin-bottom: 4px;
      color: #1f1f1f;
    }
    p {
      margin: 0;
      line-height: 1.6;
    }
  }
  .footer-btns {
    display: flex;
    gap: 8px;
    margin-left: auto;
    justify-content: flex-end;
  }
  @media (prefers-reduced-motion: reduce) {
    .model-row {
      transition: none;
    }
  }
</style>
