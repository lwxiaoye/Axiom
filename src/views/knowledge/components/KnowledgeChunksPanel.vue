<template>
  <section class="knowledge-chunks-panel">
    <div class="knowledge-section-heading">
      <div>
        <component :is="titleTag" >分段</component>
        <p>{{ description }}</p>
      </div>
      <div class="knowledge-list-tools">
        <a-select
          v-model:value="selectedDocumentId"
          class="chunk-document-filter"
          allow-clear
          show-search
          option-filter-prop="label"
          placeholder="筛选文档"
          :options="documentOptions"
          @change="handleChunkDocumentFilter"
        />
        <div class="market-search compact">
          <SearchOutlined />
          <input v-model="keyword" placeholder="搜索分段内容..." @keydown.enter="loadChunks" />
        </div>
      </div>
    </div>

    <div v-if="loading" class="knowledge-panel-state"><LoadingOutlined /> 正在加载分段...</div>
    <div v-else class="knowledge-list-panel">
      <div class="knowledge-chunk-table-head">
        <span>分段内容</span>
        <span>字符数</span>
        <span>参与检索</span>
        <span>操作</span>
      </div>
      <div class="knowledge-list-scroll">
        <div class="knowledge-chunk-list">
          <article
            v-for="item in chunks"
            :key="item.id"
            :class="['knowledge-chunk-item', { interactive: true, editable: canEdit }]"
            @click="openChunk(item)"
          >
            <div class="chunk-main-content">
              <b class="chunk-title">#{{ item.chunkIndex + 1 }}<template v-if="item.title"> · {{ item.title }}</template></b>
              <p class="chunk-preview">{{ item.content }}</p>
              <span v-if="item.images?.length" class="chunk-image-count">含 {{ item.images.length }} 张图片</span>
            </div>
            <span class="chunk-char-count">{{ item.charCount || 0 }} 字符</span>
            <template v-if="canEdit">
              <span class="chunk-switch-control" @mousedown.stop @click.stop>
                <a-switch :checked="item.enabled === 1" @change="(value) => toggleChunk(item, Boolean(value))" />
              </span>
              <span class="chunk-actions" @click.stop>
                <a-popconfirm title="确定删除该分段？" @confirm="removeChunk(item)">
                  <button type="button" class="document-table-action danger">删除</button>
                </a-popconfirm>
              </span>
            </template>
            <template v-else>
              <span class="chunk-enable-readonly">—</span>
              <span aria-hidden="true"></span>
            </template>
          </article>
          <a-empty v-if="!chunks.length" :description="needsRebuild ? '文档已有分段，但分段索引尚未同步到这里' : '暂无分段'">
            <!--
              分段正本表上线前入库的文档：卡片写着「N 个分段」，这里却是空的——切片只在向量库里。
              给一个入口从向量库回填，而不是让用户重新上传（重切结果未必和当年一致）。
              只在用户侧（agent-api）提供；管理侧分段列表仍走旧路径，不在本次范围内。
            -->
            <template v-if="needsRebuild">
              <p class="chunk-rebuild-hint">这些文档是在分段管理上线前入库的，切片只存在向量库里。重建一次即可在此查看、编辑与停用，不影响检索。</p>
              <a-button v-if="canEdit" type="primary" size="small" :loading="rebuilding" @click="rebuildChunks">重建分段索引</a-button>
              <p v-else class="chunk-rebuild-hint">请联系该知识库的所有者或编辑者重建分段索引。</p>
            </template>
          </a-empty>
        </div>
      </div>
      <a-pagination
        v-if="pagination.total"
        v-model:current="pagination.current"
        class="knowledge-list-pagination"
        :page-size="pagination.pageSize"
        :total="pagination.total"
        show-size-changer
        :show-total="(total) => `共 ${total} 条`"
        @change="changeChunkPage"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { LoadingOutlined, SearchOutlined } from '@ant-design/icons-vue';
import { useMessage } from '/@/hooks/web/useMessage';
import { deleteChunk, deleteManagedChunk, getChunkList, getDocumentList, getManagedChunkList, getManagedDocumentList, knowledgeErrorMessage, rebuildKnowledgeChunks, setChunkEnabled, setManagedChunkEnabled } from '../knowledge.api';
import type { KnowledgeChunk, KnowledgeDocument } from '../knowledge.types';

const props = withDefaults(defineProps<{
  knowledgeId: string;
  canEdit?: boolean;
  management?: boolean;
  documentId?: string;
  description?: string;
  titleTag?: string;
}>(), {
  canEdit: true,
  description: '查看知识库实际参与召回的内容片段。',
  titleTag: 'h3',
});

const emit = defineEmits<{
  (e: 'edit', value: KnowledgeChunk): void;
  (e: 'changed'): void;
  (e: 'update:documentId', value?: string): void;
}>();

const { createMessage } = useMessage();
const loading = ref(false);
const keyword = ref('');
const chunks = ref<KnowledgeChunk[]>([]);
const selectedDocumentId = ref<string>();
const documents = ref<KnowledgeDocument[]>([]);
const documentOptions = ref<{ label: string; value: string }[]>([]);
const pagination = reactive({ current: 1, pageSize: 20, total: 0 });
const rebuilding = ref(false);

// 「列表空，但文档自己记着有分段」= 切片正本表没同步，才提示重建；关键词搜不到、
// 文档本来就 0 段、管理侧列表（走旧路径）都不算。
const needsRebuild = computed(() => {
  if (props.management || loading.value || chunks.value.length || keyword.value.trim()) return false;
  const filterId = props.documentId || selectedDocumentId.value;
  const candidates = filterId ? documents.value.filter((document) => document.id === filterId) : documents.value;
  return candidates.some((document) => Number(document.chunkCount) > 0);
});

onMounted(async () => {
  selectedDocumentId.value = props.documentId;
  await Promise.all([loadChunks(), loadDocumentOptions()]);
});

watch(() => props.knowledgeId, async () => {
  selectedDocumentId.value = props.documentId;
  pagination.current = 1;
  await Promise.all([loadChunks(), loadDocumentOptions()]);
});

watch(() => props.documentId, (value) => {
  if (value === selectedDocumentId.value) return;
  selectedDocumentId.value = value;
  pagination.current = 1;
  void loadChunks();
});

async function loadChunks() {
  const kid = props.knowledgeId;
  if (!kid) return;
  loading.value = true;
  try {
    const page = await (props.management ? getManagedChunkList : getChunkList)({
      knowledgeId: kid,
      documentId: props.documentId || selectedDocumentId.value,
      pageNo: pagination.current,
      pageSize: pagination.pageSize,
      keyword: keyword.value.trim() || undefined,
    });
    if (kid !== props.knowledgeId) return;
    chunks.value = page?.records || [];
    pagination.total = page?.total || 0;
  } finally {
    if (kid === props.knowledgeId) loading.value = false;
  }
}

async function loadDocumentOptions() {
  const kid = props.knowledgeId;
  if (!kid) return;
  const page = await (props.management ? getManagedDocumentList : getDocumentList)({ knowledgeId: kid, pageNo: 1, pageSize: 1000 });
  if (kid !== props.knowledgeId) return;
  documents.value = page?.records || [];
  documentOptions.value = documents.value.map((document: KnowledgeDocument) => ({ label: document.originalName, value: document.id }));
}

async function rebuildChunks() {
  const kid = props.knowledgeId;
  if (!kid || !props.canEdit || props.management || rebuilding.value) return;
  rebuilding.value = true;
  try {
    const result = await rebuildKnowledgeChunks(kid);
    if (kid !== props.knowledgeId) return;
    const rebuilt = Number(result?.rebuilt || 0);
    createMessage.success(rebuilt ? `已从向量库回填 ${rebuilt} 个分段` : '向量库里没有可回填的分段');
    await loadChunks();
    emit('changed');
  } catch (error) {
    createMessage.error(knowledgeErrorMessage(error, '重建分段索引失败'));
  } finally {
    if (kid === props.knowledgeId) rebuilding.value = false;
  }
}

function changeChunkPage(page: number, pageSize: number) {
  pagination.current = page;
  pagination.pageSize = pageSize;
  void loadChunks();
}

function handleChunkDocumentFilter() {
  pagination.current = 1;
  emit('update:documentId', selectedDocumentId.value);
  void loadChunks();
}

function openChunk(record: KnowledgeChunk) {
  emit('edit', record);
}

// 用户侧请求关掉了自动报错提示（见 knowledge.api.ts 的 KB_OPTS），停用/删除失败时
// 开关会弹回原位、列表纹丝不动，必须把原因显示出来，否则用户只看到「点了没反应」。
async function toggleChunk(record: KnowledgeChunk, enabled: boolean) {
  if (!props.canEdit) return;
  await (props.management ? setManagedChunkEnabled : setChunkEnabled)(record.id, enabled).then(
    () => { record.enabled = enabled ? 1 : 0; },
    (error) => createMessage.error(knowledgeErrorMessage(error, enabled ? '启用分段失败' : '停用分段失败')),
  );
}

async function removeChunk(record: KnowledgeChunk) {
  if (!props.canEdit) return;
  try {
    await (props.management ? deleteManagedChunk : deleteChunk)(record.id);
  } catch (error) {
    createMessage.error(knowledgeErrorMessage(error, '删除分段失败'));
    return;
  }
  createMessage.success('分段已删除');
  await loadChunks();
  emit('changed');
}

async function reload() {
  await loadChunks();
}

async function reloadDocuments() {
  await loadDocumentOptions();
}

defineExpose({ reload, reloadDocuments });
</script>

<style scoped lang="less">
.knowledge-section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.knowledge-section-title {
  display: block;
  margin: 0;
  color: #101828;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
}

.knowledge-section-heading p {
  margin: 6px 0 0;
  color: #858a95;
  font-size: 16px;
  line-height: 1.5715;
}

.knowledge-list-tools {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.chunk-document-filter {
  width: 240px;
}

.market-search.compact {
  display: flex;
  width: 280px;
  height: 34px;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 1px solid #d0d5dd;
  border-radius: 8px;
  background: #fff;
  color: #98a2b3;
}

.market-search.compact input {
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  color: #101828;
}

.knowledge-panel-state {
  display: flex;
  min-height: 220px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #667085;
}

.knowledge-list-panel {
  display: flex;
  height: min(620px, calc(100vh - 300px));
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  background: transparent;
}

.knowledge-chunk-table-head {
  display: grid;
  min-height: 34px;
  flex: 0 0 34px;
  grid-template-columns: minmax(0, 1fr) 76px 84px 132px;
  gap: 12px;
  align-items: center;
  padding: 0 14px;
  color: #667085;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
}

.knowledge-chunk-table-head span:not(:first-child) {
  text-align: center;
}

.knowledge-chunk-table-head span:last-child {
  text-align: right;
}

.knowledge-list-scroll {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
}

.knowledge-chunk-list {
  display: grid;
  gap: 8px;
}

.knowledge-chunk-item {
  display: grid;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  grid-template-columns: minmax(0, 1fr) 76px 84px 132px;
  gap: 12px;
  align-items: center;
  padding: 12px 14px;
  border: 1px solid #eceef3;
  border-radius: 12px;
  background: #fff;
  color: #101828;
  text-align: left;
  transition: border-color 0.16s ease, background-color 0.16s ease;
}

.knowledge-chunk-item.interactive {
  cursor: pointer;
}

.knowledge-chunk-item.interactive:hover {
  border-color: #d6deeb;
  background: #fbfdff;
}

.chunk-main-content {
  min-width: 0;
}

.knowledge-chunk-item b {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: #101828;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.knowledge-chunk-item .chunk-preview {
  width: min(900px, 100%);
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: #667085;
  line-height: 1.7;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chunk-char-count,
.chunk-enable-readonly {
  color: #667085;
  font-size: 12px;
  justify-self: center;
  white-space: nowrap;
}

.chunk-switch-control {
  display: inline-flex;
  justify-self: center;
}

.chunk-actions {
  display: inline-flex;
  align-items: center;
  justify-self: end;
}

.chunk-actions :deep(.document-table-action) {
  min-height: 30px;
  padding: 0 8px;
  font-size: 13px;
}

.chunk-image-count {
  display: block;
  margin-top: 6px;
  color: #2563eb;
  font-size: 12px;
}

.chunk-rebuild-hint {
  max-width: 520px;
  margin: 0 auto 12px;
  color: #667085;
  font-size: 13px;
  line-height: 1.6;
}

.knowledge-list-pagination {
  display: flex;
  min-height: 58px;
  flex: 0 0 58px;
  align-items: center;
  justify-content: flex-end;
  padding: 8px 16px;
}

@media (max-width: 900px) {
  .knowledge-section-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .knowledge-list-tools,
  .chunk-document-filter,
  .market-search.compact {
    width: 100%;
  }

  .knowledge-chunk-table-head {
    display: none;
  }

  .knowledge-chunk-item {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
}
</style>
