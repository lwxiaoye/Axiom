<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="library-page">
    <!-- 搜索与过滤栏 -->
    <a-card :bordered="false" class="filter-card">
      <div class="filter-row">
        <a-input-search
          v-model:value="keyword"
          placeholder="前端过滤模型名称"
          allow-clear
          style="max-width: 360px"
        />
        <a-radio-group v-model:value="categoryFilter" button-style="solid" size="small" @change="applyFilter">
          <a-radio-button value="">全部</a-radio-button>
          <a-radio-button v-for="c in categories" :key="c" :value="c">{{ categoryLabel(c) }}</a-radio-button>
        </a-radio-group>
        <span class="total-text">共 {{ total }} 个模型</span>
        <a-button type="link" @click="openBrowseDrawer">
          <Icon icon="ant-design:appstore-outlined" /> 浏览更多模型
        </a-button>
      </div>
    </a-card>

    <!-- 浏览更多模型抽屉 -->
    <BrowseModelsDrawer @register="registerBrowseDrawer" @success="onBrowseDeployed" />

    <!-- 模型卡片网格 -->
    <a-alert v-if="libraryError" class="page-alert" type="error" show-icon closable :message="libraryError" @close="libraryError = ''" />
    <div class="library-scroll" @scroll.passive="handleLibraryScroll">
      <a-spin :spinning="loading">
        <a-empty v-if="!loading && !filteredModels.length" description="未找到匹配的模型" class="empty-block" />
        <div v-else class="card-grid">
          <div v-for="m in filteredModels" :key="m.id" class="model-card">
            <!-- 头部：左图标 + 右名称/标签 -->
            <div class="card-head" @click="showSpecs(m)">
              <div class="card-icon">
                <img v-if="m.icon" :src="m.icon" :alt="m.name" @error="onImgError" />
                <Icon v-else icon="ant-design:appstore-outlined" :size="24" />
              </div>
              <div class="card-head-right">
                <div class="card-title" :title="m.name">{{ m.name }}</div>
                <div class="card-head-tags">
                  <a-tag v-for="c in (m.categories || []).slice(0, 2)" :key="c" :color="categoryColor(c)" class="mini-tag">
                    {{ categoryLabel(c) }}
                  </a-tag>
                </div>
              </div>
            </div>

            <!-- 描述 -->
            <div class="card-desc" :title="m.description" @click="showSpecs(m)">
              {{ m.description || '暂无描述' }}
            </div>

            <!-- 能力 + 尺寸 标签 -->
            <div class="card-tags">
              <a-tag v-for="cap in (m.capabilities || []).slice(0, 2)" :key="cap" color="geekblue" class="mini-tag">
                {{ cap }}
              </a-tag>
              <a-tag v-if="m.size" color="green" class="mini-tag size-tag">{{ m.size }}B</a-tag>
              <a-tag v-if="m.activated_size && m.activated_size !== m.size" color="green" class="mini-tag">
                激活 {{ m.activated_size }}B
              </a-tag>
            </div>

            <!-- 底部：元信息 + 部署按钮 -->
            <div class="card-footer">
              <div class="card-meta">
                <span v-if="m.licenses && m.licenses.length" class="meta-item">
                  <Icon icon="ant-design:safety-outlined" /> {{ m.licenses[0] }}
                </span>
                <span v-if="m.release_date" class="meta-item">
                  <Icon icon="ant-design:calendar-outlined" /> {{ m.release_date }}
                </span>
              </div>
              <a-button
                type="primary"
                size="small"
                :loading="deployingId === m.id"
                @click="showSpecs(m)"
                class="deploy-btn"
              >
                <Icon icon="ant-design:rocket-outlined" /> 部署
              </a-button>
            </div>
          </div>
        </div>
      </a-spin>
      <div v-if="loadingMore" class="load-more-state">
        <a-spin size="small" />
        <span>正在加载更多</span>
      </div>
      <div v-else-if="models.length && !hasMore" class="load-more-state">已加载全部</div>
    </div>

    <!-- 规格预览弹窗 -->
    <BasicModal
      @register="registerSpecModal"
      title="部署规格预览"
      width="640px"
      :show-ok-btn="false"
      cancel-text="关闭"
    >
      <a-alert
        v-if="compatibilityState !== 'compatible'"
        class="compatibility-alert"
        :type="compatibilityState === 'incompatible' ? 'error' : 'warning'"
        show-icon
        :message="compatibilityMessage"
      />
      <a-checkbox v-if="compatibilityState === 'unknown'" v-model:checked="compatibilityAcknowledged" class="compatibility-ack">
        I understand this deployment has not been verified.
      </a-checkbox>
      <a-spin :spinning="specLoading">
        <a-empty v-if="!specLoading && !currentSpecs.length" description="暂无规格" />
        <div v-for="(s, i) in currentSpecs" :key="i" class="spec-block">
          <a-descriptions :column="2" size="small" bordered>
            <a-descriptions-item label="来源">{{ s.source }}</a-descriptions-item>
            <a-descriptions-item label="推理后端">{{ s.backend }}</a-descriptions-item>
            <a-descriptions-item label="模型ID" :span="2">{{ s.model_scope_model_id || s.huggingface_repo_id || s.local_path || '-' }}</a-descriptions-item>
            <a-descriptions-item label="副本数">{{ s.replicas }}</a-descriptions-item>
            <a-descriptions-item label="量化">{{ s.quantization || '-' }}</a-descriptions-item>
            <a-descriptions-item label="后端参数" :span="2">
              <a-tag v-for="p in (s.backend_parameters || [])" :key="p">{{ p }}</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="GPU 厂商">{{ (s.gpu_filters && s.gpu_filters.vendor) ? s.gpu_filters.vendor.join(',') : '不限' }}</a-descriptions-item>
            <a-descriptions-item label="调度策略">{{ s.placement_strategy }}</a-descriptions-item>
          </a-descriptions>
          <a-button
            type="primary"
            size="small"
            class="deploy-in-spec"
            :loading="deployingId === currentModelId"
            :disabled="!canSubmitDeployment(compatibilityState, compatibilityAcknowledged)"
            @click="handleDeploy(currentModel)"
          >
            使用此规格部署
          </a-button>
          <a-divider v-if="i < currentSpecs.length - 1" />
        </div>
      </a-spin>
    </BasicModal>
  </div>
</template>

<script lang="ts" name="gpustack-library" setup>
  import { ref, computed, onMounted } from 'vue';
  import { useRouter } from 'vue-router';
  import { Icon } from '/@/components/Icon';
  import { BasicModal, useModal } from '/@/components/Modal';
  import { useDrawer } from '/@/components/Drawer';
  import BrowseModelsDrawer from '../components/BrowseModelsDrawer.vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { libraryModels, modelEvaluation, modelSpecs, quickDeploy } from '../gpustack.api';
  import { canSubmitDeployment, compatibilityFromEvaluation, normalizeGpuStackError } from '../gpustack.state';
  import type { CompatibilityState } from '../gpustack.types';

  const router = useRouter();

  const { createMessage, createConfirm } = useMessage();
  const [registerSpecModal, { openModal: openSpecModal }] = useModal();
  const [registerBrowseDrawer, { openDrawer: openBrowseDrawerInner }] = useDrawer();

  const LIBRARY_PAGE_SIZE = 24;
  const SCROLL_LOAD_THRESHOLD = 160;

  const loading = ref(false);
  const loadingMore = ref(false);
  const models = ref<any[]>([]);
  const total = ref(0);
  const pageNumber = ref(1);
  const keyword = ref('');
  const categoryFilter = ref('');
  const deployingId = ref<number | null>(null);
  const libraryError = ref('');

  // 规格预览
  const specLoading = ref(false);
  const currentSpecs = ref<any[]>([]);
  const currentModel = ref<any>(null);
  const currentModelId = computed(() => currentModel.value?.id ?? null);
  const compatibilityState = ref<CompatibilityState>('unknown');
  const compatibilityMessage = ref('Compatibility has not been verified.');
  const compatibilityAcknowledged = ref(false);

  // 类别配置：GPUStack model-sets 的 categories 取值映射
  const categoryMap: Record<string, { label: string; color: string }> = {
    llm: { label: '大语言模型', color: 'blue' },
    image: { label: '图像', color: 'purple' },
    text_to_speech: { label: '语音合成', color: 'cyan' },
    speech_to_text: { label: '语音识别', color: 'cyan' },
    embedding: { label: '向量', color: 'green' },
    reranker: { label: '重排', color: 'orange' },
    vision: { label: '视觉', color: 'magenta' },
  };

  function categoryLabel(c: string): string {
    return categoryMap[c]?.label || c;
  }
  function categoryColor(c: string): string {
    return categoryMap[c]?.color || 'default';
  }

  // 所有出现的类别（用于过滤栏）
  const categories = computed(() => {
    const set = new Set<string>();
    models.value.forEach((m) => (m.categories || []).forEach((c: string) => set.add(c)));
    return Array.from(set);
  });

  // 前端二次过滤（关键字 + 类别）
  const filteredModels = computed(() => {
    return models.value.filter((m) => {
      const kw = keyword.value.trim().toLowerCase();
      const matchKw = !kw || (m.name || '').toLowerCase().includes(kw) || (m.description || '').toLowerCase().includes(kw);
      const matchCat = !categoryFilter.value || (m.categories || []).includes(categoryFilter.value);
      return matchKw && matchCat;
    });
  });

  const hasMore = computed(() => models.value.length < total.value);

  function applyFilter() {
    // 仅前端过滤，无需重新请求
  }

  async function loadModels(reset = true) {
    if (reset) {
      pageNumber.value = 1;
      loading.value = true;
    } else {
      loadingMore.value = true;
    }
    try {
      const res = await libraryModels({ pageNo: pageNumber.value, pageSize: LIBRARY_PAGE_SIZE });
      const records = res?.records || [];
      models.value = reset ? records : [...models.value, ...records];
      total.value = res?.total || 0;
      libraryError.value = '';
    } catch (error) {
      if (reset) {
        models.value = [];
        total.value = 0;
      } else {
        pageNumber.value = Math.max(1, pageNumber.value - 1);
      }
      libraryError.value = normalizeGpuStackError(error, 'Unable to load the model catalog.').message;
    } finally {
      loading.value = false;
      loadingMore.value = false;
    }
  }

  async function loadMoreModels() {
    if (loading.value || loadingMore.value || !hasMore.value) return;
    pageNumber.value += 1;
    await loadModels(false);
  }

  function handleLibraryScroll(event: UIEvent) {
    const el = event.currentTarget as HTMLElement;
    if (!el) return;
    const distanceToBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceToBottom <= SCROLL_LOAD_THRESHOLD) {
      loadMoreModels();
    }
  }

  async function showSpecs(m: any) {
    currentModel.value = m;
    currentSpecs.value = [];
    compatibilityState.value = 'unknown';
    compatibilityMessage.value = 'Compatibility has not been verified.';
    compatibilityAcknowledged.value = false;
    specLoading.value = true;
    openSpecModal(true);
    try {
      const res = await modelSpecs(m.id);
      currentSpecs.value = res || [];
      await evaluateCurrentSpecs();
    } catch (error) {
      compatibilityMessage.value = normalizeGpuStackError(error, 'Unable to load deployment specifications.').message;
    } finally {
      specLoading.value = false;
    }
  }

  async function evaluateCurrentSpecs() {
    if (!currentSpecs.value.length) {
      compatibilityMessage.value = 'No deployable specification is available.';
      return;
    }
    try {
      const evaluation: any = await modelEvaluation(null, currentSpecs.value);
      const results = Array.isArray(evaluation?.results) ? evaluation.results : [];
      compatibilityState.value = compatibilityFromEvaluation(results);
      const messages = results.flatMap((result: any) => [
        ...(result?.compatibility_messages || []),
        ...(result?.scheduling_messages || []),
        ...(result?.error_message ? [result.error_message] : []),
      ]);
      compatibilityMessage.value = messages[0]
        || (compatibilityState.value === 'compatible'
          ? 'Compatible with the current cluster.'
          : 'Compatibility has not been verified.');
    } catch (error) {
      compatibilityState.value = 'unknown';
      compatibilityMessage.value = normalizeGpuStackError(error, 'Compatibility evaluation is unavailable.').message;
    }
  }

  async function handleDeploy(m: any) {
    currentModel.value = m;
    if (!canSubmitDeployment(compatibilityState.value, compatibilityAcknowledged.value)) {
      return;
    }
    // 二次确认：一键部署会按预制规格提交
    createConfirm({
      iconType: 'info',
      title: '一键部署',
      content: `将按 GPUStack 预制规格部署「${m.name}」，确认继续？`,
      onOk: async () => {
        deployingId.value = m.id;
        try {
          await quickDeploy(m.id, { replicas: 1 });
          createMessage.success('部署任务已提交，可在「模型管理」查看进度');
        } catch (e) {
          createMessage.error('部署失败：' + (e?.message || '未知错误'));
        } finally {
          deployingId.value = null;
        }
      },
    });
  }

  function onImgError(e: any) {
    e.target.style.display = 'none';
  }

  // 右上角「浏览更多模型」抽屉
  function openBrowseDrawer() {
    // 沿用当前选中的分类作为抽屉默认分类
    openBrowseDrawerInner(true, { category: categoryFilter.value });
  }

  function onBrowseDeployed(payload?: any) {
    loadModels(true);
    // 提交部署后跳转到「模型部署」页并定位该模型，进入下载/部署/进度/日志流程
    goToModelDeploy(payload?.name);
  }

  function goToModelDeploy(modelName?: string) {
    router.push({ name: 'gpustack-model', query: { modelName: modelName || '', focus: 'deploy' } }).catch(() => {});
  }

  onMounted(() => loadModels(true));
</script>

<style scoped lang="less">
  .library-page {
    padding: 16px;
  }
  .page-alert,
  .compatibility-alert {
    margin-bottom: 12px;
  }
  .compatibility-ack {
    display: block;
    margin: 0 0 12px;
  }
  .filter-card {
    margin-bottom: 16px;
    :deep(.ant-card-body) {
      padding: 14px 16px;
    }
  }
  .filter-row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .total-text {
    color: #888;
    font-size: 13px;
    margin-left: auto;
  }
  .empty-block {
    padding: 80px 0;
  }
  .library-scroll {
    height: calc(100vh - 154px);
    min-height: 360px;
    overflow-y: auto;
    overflow-x: hidden;
    padding-right: 4px;
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 14px;
  }
  .model-card {
    display: flex;
    flex-direction: column;
    background: #fff;
    border: 1px solid #ebedf0;
    border-radius: 8px;
    padding: 14px;
    transition: all 0.2s ease;
    &:hover {
      border-color: #69b1ff;
      box-shadow: 0 6px 20px rgba(0, 0, 0, 0.08);
      transform: translateY(-2px);
    }
  }
  // 头部：左图标 + 右名称
  .card-head {
    display: flex;
    align-items: center;
    gap: 10px;
    cursor: pointer;
  }
  .card-icon {
    width: 44px;
    height: 44px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f5f7fa;
    border-radius: 8px;
    color: #8c8c8c;
    img {
      max-width: 80%;
      max-height: 80%;
      object-fit: contain;
    }
  }
  .card-head-right {
    flex: 1;
    min-width: 0;
  }
  .card-title {
    font-size: 15px;
    font-weight: 700;
    color: #1f2937;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .card-head-tags {
    margin-top: 3px;
    line-height: 16px;
  }
  .mini-tag {
    font-size: 11px !important;
    line-height: 16px !important;
    padding: 0 5px !important;
    margin: 0 4px 0 0 !important;
    border-radius: 4px;
  }
  // 描述
  .card-desc {
    font-size: 12px;
    color: #8c8c8c;
    line-height: 1.5;
    margin: 10px 0;
    height: 36px;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    cursor: pointer;
  }
  // 能力 + 尺寸标签
  .card-tags {
    min-height: 20px;
    margin-bottom: 10px;
  }
  // 底部：元信息 + 部署按钮
  .card-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: auto;
    padding-top: 10px;
    border-top: 1px solid #f5f5f5;
  }
  .card-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 11px;
    color: #8c8c8c;
    .meta-item {
      display: inline-flex;
      align-items: center;
      gap: 2px;
    }
  }
  .deploy-btn {
    flex-shrink: 0;
    font-size: 12px;
  }
  .load-more-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 44px;
    color: #8c8c8c;
    font-size: 12px;
  }
  .spec-block {
    margin-bottom: 8px;
  }
  .deploy-in-spec {
    margin-top: 12px;
    width: 100%;
  }
</style>
