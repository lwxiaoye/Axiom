<template>
  <a-modal
    :open="open"
    title="添加文档"
    wrap-class-name="knowledge-upload-modal"
    width="min(1040px, calc(100% - 64px))"
    :confirm-loading="uploading"
    :ok-text="currentStep === 3 ? '确认上传' : '下一步'"
    :cancel-text="currentStep === 0 ? '取消' : '上一步'"
    @ok="handlePrimary"
    @cancel="handleSecondary"
  >
    <a-steps :current="currentStep" class="upload-steps">
      <a-step title="选择文件" />
      <a-step title="参数设置" />
      <a-step title="数据预览" />
      <a-step title="确认上传" />
    </a-steps>
    <section v-if="currentStep === 0" class="step-panel">
      <a-upload-dragger :file-list="fileList" :accept="KNOWLEDGE_UPLOAD_ACCEPT" :multiple="true"
        :show-upload-list="false" :before-upload="beforeUpload">
        <p class="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p class="ant-upload-text">点击或拖拽文档到这里</p>
        <p class="ant-upload-hint">{{ KNOWLEDGE_UPLOAD_FORMAT_HINT }}</p>
      </a-upload-dragger>
      <div v-if="fileList.length" class="upload-file-table">
        <div class="upload-file-header"><span>文件名</span><span>文件大小</span><span>操作</span></div>
        <div v-for="item in fileList" :key="item.uid" class="upload-file-row">
          <strong :title="item.name">
            <component :is="fileIcon(item.name)" class="file-type-icon" />{{ item.name }}
          </strong>
          <span>{{ formatSize(item.size) }}</span>
          <button type="button" :aria-label="`移除 ${item.name}`" @click="removeFile(item.uid)">移除</button>
        </div>
      </div>
    </section>
    <section v-else-if="currentStep === 1" class="step-panel settings-panel">
      <div class="setting-title">数据处理方式设置</div>
      <a-form layout="vertical">
        <a-form-item label="处理方式">
          <a-radio-group v-model:value="form.processingMode" class="card-radio-group">
            <a-radio-button value="CHUNK">分块存储</a-radio-button>
            <a-radio-button value="QA">问答对提取</a-radio-button>
          </a-radio-group>
        </a-form-item>
        <a-form-item label="分块条件">
          <div class="inline-grid">
            <a-select v-model:value="form.splitConditionType">
              <a-select-option value="LENGTH_GT">原文长度大于</a-select-option>
              <a-select-option value="ALWAYS">始终分块</a-select-option>
            </a-select>
            <a-input-number v-model:value="form.splitConditionValue" :min="100" :max="50000"
              :disabled="form.splitConditionType === 'ALWAYS'" />
          </div>
        </a-form-item>
        <a-form-item label="索引增强">
          <div class="checkbox-grid">
            <a-checkbox v-model:checked="form.addTitleToIndex">将标题加入索引</a-checkbox>
          </div>
        </a-form-item>
        <a-form-item label="分块处理参数">
          <a-radio-group v-model:value="form.splitStrategy" class="strategy-list">
            <a-radio value="PARAGRAPH">
              <strong>按段落分块</strong>
              <span>模型识别段落，适合制度、手册、长文档。</span>
            </a-radio>
            <a-radio value="LENGTH">
              <strong>按长度分块</strong>
              <span>按固定字符窗口切分，适合结构不明显的文本。</span>
            </a-radio>
            <a-radio value="SEPARATOR">
              <strong>按指定分隔符分块</strong>
              <span>按换行、标题符或自定义分隔符切分。</span>
            </a-radio>
          </a-radio-group>
        </a-form-item>
        <div class="param-grid" v-if="form.splitStrategy === 'PARAGRAPH'">
          <a-form-item label="最大段落深度"><a-input-number v-model:value="form.maxParagraphDepth" :min="1"
              :max="6" /></a-form-item>
          <a-form-item label="最大分块大小"><a-input-number v-model:value="form.chunkSize" :min="200"
              :max="5000" /></a-form-item>
        </div>
        <div class="param-grid" v-else-if="form.splitStrategy === 'LENGTH'">
          <a-form-item label="分块大小"><a-input-number v-model:value="form.chunkSize" :min="200"
              :max="5000" /></a-form-item>
          <a-form-item label="重叠大小"><a-input-number v-model:value="form.overlapSize" :min="0"
              :max="1000" /></a-form-item>
        </div>
        <div class="param-grid" v-else>
          <a-form-item label="分隔符"><a-input v-model:value="form.separator" placeholder="例如：### 或空行" /></a-form-item>
          <a-form-item label="最大分块大小"><a-input-number v-model:value="form.chunkSize" :min="200"
              :max="5000" /></a-form-item>
        </div>
      </a-form>
    </section>
    <section v-else-if="currentStep === 2" class="step-panel">
      <a-alert type="info" show-icon message="系统将按当前参数在后端解析文件，并预览前 10 个分段。" class="hint" />
      <a-descriptions bordered size="small" :column="3" class="preview-config">
        <a-descriptions-item label="处理方式">{{ form.processingMode === 'CHUNK' ? '分块存储' : '问答对提取' }}</a-descriptions-item>
        <a-descriptions-item label="分块策略">{{ splitStrategyText }}</a-descriptions-item>
        <a-descriptions-item label="分块大小">{{ form.chunkSize }}</a-descriptions-item>
        <a-descriptions-item label="索引增强" :span="3">标题索引 {{ form.addTitleToIndex ? '开启' : '关闭' }}</a-descriptions-item>
      </a-descriptions>
      <div class="preview-layout">
        <aside class="preview-file-list" aria-label="预览文件列表">
          <button v-for="item in fileList" :key="item.uid" type="button"
            :class="['preview-file-item', { active: item.uid === selectedPreviewUid }]"
            @click="selectPreviewFile(item.uid)">
            <strong :title="item.name">
              <component :is="fileIcon(item.name)" class="file-type-icon" />{{ item.name }}
            </strong>
            <span v-if="getPreviewState(item.uid)?.loading">预览中</span>
            <span v-else-if="getPreviewState(item.uid)?.error">预览失败</span>
            <span v-else>{{ getPreviewState(item.uid)?.data?.totalChunks || 0 }} 个分段</span>
          </button>
        </aside>
        <div class="preview-detail-pane">
          <a-skeleton v-if="currentPreview?.loading" active :paragraph="{ rows: 8 }" />
          <a-alert v-else-if="currentPreview?.error" type="error" show-icon :message="currentPreview.error" />
          <template v-else-if="currentPreview?.data">
            <a-descriptions bordered size="small" :column="2">
              <a-descriptions-item label="文件">{{ currentPreview.data.fileName }}</a-descriptions-item>
              <a-descriptions-item label="预计分段">{{ currentPreview.data.totalChunks }}</a-descriptions-item>
            </a-descriptions>
            <div class="preview-list">
              <button v-for="(chunk, index) in currentPreview.data.chunks" :key="index" type="button"
                class="preview-item" @click="openChunkDetail(chunk, index)">
                <b>#{{ index + 1 }}<template v-if="chunk.pageNumber"> · {{ chunk.pageNumber }} 页</template></b>
                <p>{{ chunk.content }}</p>
                <span v-if="chunk.images?.length" class="chunk-image-count">含 {{ chunk.images.length }} 张图片</span>
              </button>
              <a-empty v-if="!currentPreview.data.chunks.length" description="暂无可预览的分段" />
            </div>
          </template>
          <a-empty v-else description="暂无可预览的分段" />
        </div>
      </div>
      <a-drawer :open="!!selectedChunk" :title="selectedChunk ? `分段详情 #${selectedChunk.index + 1}` : '分段详情'"
        placement="right" width="520" @close="selectedChunk = undefined">
        <p class="chunk-detail-content">{{ selectedChunk?.content }}</p>
        <div v-if="selectedChunk?.images?.length" class="chunk-images">
          <a-image v-for="image in selectedChunk.images" :key="image.imageId || image.url" :src="imageSrc(image.url)"
            :alt="image.caption || image.ocrText || '文档图片'" />
        </div>
      </a-drawer>
    </section>
    <section v-else class="step-panel confirm-panel">
      <a-result status="info" title="确认上传并开始处理" sub-title="上传后会按当前参数执行解析、切分、向量化与索引，处理进度可在文档列表查看。" />
    </section>
  </a-modal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { FileExcelOutlined, FileMarkdownOutlined, FileOutlined, FilePdfOutlined, FileTextOutlined, FileWordOutlined, InboxOutlined } from '@ant-design/icons-vue';
import { useMessage } from '/@/hooks/web/useMessage';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';
import { previewKnowledgeDocument, uploadKnowledgeDocument } from '../knowledge.api';
import { KNOWLEDGE_UPLOAD_ACCEPT, KNOWLEDGE_UPLOAD_FORMAT_HINT } from '../knowledgeUploadFormats';
import type { KnowledgeDocumentPreview, KnowledgePreviewChunk, KnowledgeUploadOptions } from '../knowledge.types';
const props = defineProps<{ open: boolean; knowledgeId: string }>();
const emit = defineEmits<{ 'update:open': [value: boolean]; success: [] }>();
const { createMessage } = useMessage();
const currentStep = ref(0);
const fileList = ref<Array<{ uid: string; name: string; size: number; originFileObj: File }>>([]);
const uploading = ref(false);
const previewLoading = ref(false);
const previewByUid = ref<Record<string, { loading: boolean; data?: KnowledgeDocumentPreview; error?: string }>>({});
const selectedPreviewUid = ref<string>();
const selectedChunk = ref<(KnowledgePreviewChunk & { index: number })>();
const form = reactive<KnowledgeUploadOptions>({
  processingMode: 'CHUNK',
  splitConditionType: 'LENGTH_GT',
  splitConditionValue: 1000,
  addTitleToIndex: true,
  imageAutoIndex: false,
  imageExtractionEnabled: true,
  splitStrategy: 'PARAGRAPH',
  maxParagraphDepth: 3,
  chunkSize: 1000,
  overlapSize: 150,
  separator: '\n\n',
});

const selectedFiles = computed(() => fileList.value.map((item) => item.originFileObj));
const splitStrategyText = computed(() => ({ PARAGRAPH: '按段落分块', LENGTH: '按长度分块', SEPARATOR: '按指定分隔符分块' }[form.splitStrategy]));
const currentPreview = computed(() => selectedPreviewUid.value ? previewByUid.value[selectedPreviewUid.value] : undefined);
const hasPreviewFailures = computed(() => fileList.value.some((item) => Boolean(previewByUid.value[item.uid]?.error)));

watch(() => props.open, (value) => {
  if (value) {
    currentStep.value = 0;
    previewByUid.value = {};
    selectedPreviewUid.value = undefined;
    selectedChunk.value = undefined;
    fileList.value = [];
  }
});

function beforeUpload(file: File) {
  if (fileList.value.some((item) => item.name === file.name && item.size === file.size)) return false;
  fileList.value = [...fileList.value, {
    uid: `${file.name}-${file.size}-${file.lastModified}`,
    name: file.name,
    size: file.size,
    originFileObj: file,
  }];
  return false;
}

function removeFile(uid: string) { fileList.value = fileList.value.filter((item) => item.uid !== uid); }

function handleSecondary() {
  if (currentStep.value > 0) {
    currentStep.value -= 1;
    return;
  }
  close();
}

async function handlePrimary() {
  if (currentStep.value === 0 && !selectedFiles.value.length) {
    createMessage.warning('请选择文档');
    return;
  }
  if (currentStep.value === 1) {
    currentStep.value += 1;
    await loadPreview();
    return;
  }
  if (currentStep.value === 2 && (previewLoading.value || hasPreviewFailures.value)) {
    createMessage.warning('请先处理预览失败的文件');
    return;
  }
  if (currentStep.value < 3) {
    currentStep.value += 1;
    return;
  }
  await upload();
}

function close() { emit('update:open', false); }

function unwrapResponse<T>(response: any): T {
  if (response?.success === false) {
    throw new Error(response?.message || '请求处理失败');
  }
  return (response?.result || response) as T;
}

function getPreviewState(uid: string) { return previewByUid.value[uid]; }
function selectPreviewFile(uid: string) { selectedPreviewUid.value = uid; }
function openChunkDetail(chunk: KnowledgePreviewChunk, index: number) { selectedChunk.value = { ...chunk, index }; }
function imageSrc(url?: string) { return getProxyStaticFileUrl(url || ''); }

function fileIcon(name: string) {
  const extension = name.split('.').pop()?.toLowerCase();
  if (extension === 'pdf') return FilePdfOutlined;
  if (extension === 'docx') return FileWordOutlined;
  if (['xls', 'xlsx', 'csv'].includes(extension || '')) return FileExcelOutlined;
  if (['md', 'markdown', 'mdx'].includes(extension || '')) return FileMarkdownOutlined;
  if (['txt', 'html', 'htm', 'properties', 'vtt'].includes(extension || '')) return FileTextOutlined;
  return FileOutlined;
}

async function loadPreview() {
  const files = [...fileList.value];
  if (!files.length) return;
  previewLoading.value = true;
  selectedPreviewUid.value = files[0].uid;
  previewByUid.value = Object.fromEntries(files.map((item) => [item.uid, { loading: true }]));
  try {
    await Promise.all(files.map(async (item) => {
      try {
        const response = await previewKnowledgeDocument(props.knowledgeId, item.originFileObj, { ...form });
        previewByUid.value[item.uid] = { loading: false, data: unwrapResponse<KnowledgeDocumentPreview>(response) };
      } catch (error) {
        previewByUid.value[item.uid] = {
          loading: false,
          error: error instanceof Error && error.message ? error.message : '请求处理失败',
        };
      }
    }));
    if (hasPreviewFailures.value) {
      createMessage.warning('请先处理预览失败的文件');
    }
  } finally {
    previewLoading.value = false;
  }
}

async function upload() {
  if (!selectedFiles.value.length) return;
  uploading.value = true;
  let completed = 0;
  try {
    for (const item of fileList.value) {
      try {
        const response = await uploadKnowledgeDocument(props.knowledgeId, item.originFileObj, { ...form });
        unwrapResponse(response);
        completed += 1;
      } catch { /* Batch summary below reports failed files. */ }
    }
    if (completed) { emit('success'); close(); }
    createMessage[completed === fileList.value.length ? 'success' : 'warning'](
      completed === fileList.value.length ? `${completed} 个文档已提交处理` : `${completed} 个文档已提交，${fileList.value.length - completed} 个失败`,
    );
  } finally {
    uploading.value = false;
  }
}

function formatSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
</script>

<style scoped lang="less">
.upload-steps {
  margin: 2px 0 24px;
  padding: 16px 24px 18px;
  border-bottom: 1px solid #edf0f3;
}

.step-panel {
  min-height: 380px;
  padding: 8px 24px 20px;
}

.settings-panel {
  min-height: 300px;
  padding-bottom: 12px;
}

.settings-panel :deep(.ant-form-item) {
  margin-bottom: 14px;
}

.hint {
  margin-bottom: 16px;
}

.upload-file-table {
  margin-top: 16px;
  overflow: hidden;
  border: 1px solid #edf0f3;
  border-radius: 8px;
}

.upload-file-header,
.upload-file-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 110px 56px;
  gap: 16px;
  align-items: center;
  padding: 10px 14px;
}

.upload-file-header {
  color: #667085;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 600;
}

.upload-file-row {
  border-top: 1px solid #edf0f3;
  font-size: 13px;
}

.upload-file-row strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-type-icon {
  margin-right: 7px;
  color: #4f6ef7;
  vertical-align: -1px;
}

.upload-file-row button {
  border: 0;
  border-radius: 4px;
  background: #f2f4f7;
  color: #475467;
  cursor: pointer;
  line-height: 28px;
}

.setting-title {
  margin-bottom: 12px;
  padding-left: 10px;
  border-left: 3px solid #111827;
  color: #1f2937;
  font-weight: 600;
}

.card-radio-group {
  display: grid;
  grid-template-columns: repeat(2, minmax(180px, 1fr));
  gap: 8px;
}

.card-radio-group :deep(.ant-radio-button-wrapper) {
  height: 44px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  line-height: 42px;
  text-align: center;
}

.card-radio-group :deep(.ant-radio-button-wrapper:not(:first-child)::before) {
  display: none;
}

.card-radio-group :deep(.ant-radio-button-wrapper-checked) {
  border-color: #111827;
  box-shadow: none;
}

.inline-grid {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 180px;
  gap: 10px;
  max-width: 580px;
}

.checkbox-grid {
  display: grid;
  grid-template-columns: minmax(150px, max-content);
  gap: 8px;
}

.strategy-list {
  display: grid;
  gap: 8px;
}

.strategy-list :deep(.ant-radio-wrapper) {
  align-items: flex-start;
  min-height: 56px;
  padding: 10px 14px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  transition: border-color .18s ease, box-shadow .18s ease;
}

.strategy-list :deep(.ant-radio-wrapper:hover) {
  border-color: #111827;
  box-shadow: 0 8px 18px rgba(15, 23, 42, .05);
}

.strategy-list strong,
.strategy-list span {
  display: block;
}

.strategy-list span {
  margin-top: 2px;
  color: #667085;
  font-size: 12px;
  line-height: 1.45;
}

.param-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(160px, 1fr));
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid #edf0f3;
  border-radius: 8px;
  background: #fbfcfe;
}

.settings-panel .param-grid :deep(.ant-form-item) {
  margin-bottom: 0;
}

.preview-config {
  margin-bottom: 14px;
}

.preview-layout {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
  min-height: 360px;
  border: 1px solid #edf0f3;
  border-radius: 8px;
  overflow: hidden;
}

.preview-file-list {
  display: grid;
  align-content: start;
  gap: 4px;
  padding: 10px;
  border-right: 1px solid #edf0f3;
  background: #fbfcfe;
  overflow: auto;
}

.preview-file-item {
  display: grid;
  gap: 4px;
  width: 100%;
  padding: 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #344054;
  cursor: pointer;
  text-align: left;
}

.preview-file-item:hover,
.preview-file-item.active {
  border-color: #c7d7fe;
  background: #eef4ff;
  color: #1d4ed8;
}

.preview-file-item strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-file-item span {
  font-size: 12px;
}

.preview-detail-pane {
  min-width: 0;
  padding: 14px;
  overflow: auto;
}

.preview-list {
  display: grid;
  max-height: 280px;
  gap: 10px;
  margin-top: 14px;
  overflow: auto;
}

.preview-item {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid #edf0f3;
  border-radius: 8px;
  background: #fbfcfe;
  cursor: pointer;
  text-align: left;
}

.preview-item:hover {
  border-color: #c7d7fe;
  background: #f8faff;
}

.preview-item b {
  color: #111827;
  font-size: 12px;
}

.preview-item p {
  display: -webkit-box;
  overflow: hidden;
  margin: 6px 0 0;
  color: #475467;
  line-height: 1.65;
  white-space: pre-wrap;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 4;
}

.chunk-image-count {
  display: block;
  margin-top: 8px;
  color: #2563eb;
  font-size: 12px;
}

.chunk-detail-content {
  margin: 0;
  color: #344054;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}

.chunk-images {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
  margin-top: 18px;
}

.chunk-images :deep(.ant-image),
.chunk-images :deep(img) {
  width: 100%;
  height: 140px;
  object-fit: contain;
  border: 1px solid #edf0f3;
  border-radius: 6px;
  background: #f8fafc;
}

.confirm-panel :deep(.ant-result) {
  padding-bottom: 18px;
}

@media (max-width: 720px) {

  .inline-grid,
  .checkbox-grid,
  .param-grid,
  .card-radio-group,
  .upload-file-header,
  .upload-file-row,
  .preview-layout {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .preview-file-list {
    max-height: 160px;
    border-right: 0;
    border-bottom: 1px solid #edf0f3;
  }
}
</style>

<!-- wrap/mask 挂到 body，必须非 scoped 才能吃到 --center-nav-width -->
<style lang="less">
.ant-modal-root:has(.knowledge-upload-modal) .ant-modal-mask,
.knowledge-upload-modal.ant-modal-wrap {
  left: var(--center-nav-width, 0px);
  transition: left 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}

.knowledge-upload-modal .ant-modal {
  top: 48px;
  padding-bottom: 24px;
  margin: 0 auto;
  max-width: 1040px;
}

.knowledge-upload-modal .ant-modal-content {
  display: flex;
  max-height: calc(100vh - 48px);
  flex-direction: column;
  overflow: hidden;
}

.knowledge-upload-modal .ant-modal-body {
  min-height: 0;
  overflow: auto;
}

@media (prefers-reduced-motion: reduce) {
  .ant-modal-root:has(.knowledge-upload-modal) .ant-modal-mask,
  .knowledge-upload-modal.ant-modal-wrap {
    transition: none;
  }
}

@media (max-width: 980px) {
  .knowledge-upload-modal .ant-modal {
    top: 12px;
    padding-bottom: 12px;
  }
}
</style>
