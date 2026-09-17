<template>
  <section v-if="files.length" class="generated-files" aria-label="生成的文件">
    <article
      v-for="(file, fileIdx) in files"
      :key="file.id"
      class="generated-file-card"
      :style="{ animationDelay: Math.min(fileIdx * 0.06, 0.3) + 's' }"
    >
      <span :class="['generated-file-icon', `k-${fileKindOf(file.filename, file.mime)}`]">
        <component :is="KIND_ICON[fileKindOf(file.filename, file.mime)]" />
      </span>
      <span class="generated-file-copy">
        <strong :title="file.filename">{{ file.filename }}</strong>
        <em>
          {{ fileTypeLabel(file.filename, file.mime) }} · {{ formatFileSize(file.size) }} ·
          {{ file.previewOnly ? '预览产物，未保存' : '已保存到我的文件' }}
          <template v-if="file.versionNo && file.versionNo > 1"> · v{{ file.versionNo }}</template>
        </em>
      </span>
      <span class="generated-file-actions">
        <button v-if="canPreview(file)" type="button" @click="openPreview(file)">预览</button>
        <button type="button" :class="{ 'generated-file-secondary': canPreview(file) }" @click="download(file)">
          下载
        </button>
        <button v-if="!file.previewOnly" type="button" class="generated-file-secondary" @click="versionsFile = file">
          版本历史
        </button>
        <button v-if="!file.previewOnly" type="button" class="generated-file-secondary" @click="openMyFiles">
          我的文件
        </button>
      </span>
      <InlineFilePreview
        :ref="(instance) => setPreviewRef(file.id, instance)"
        :file="file"
        @download="download(file)"
        @ready="onPreviewReady(file)"
        @failed="onPreviewFailed(file)"
      />
    </article>
    <FileVersionsModal :file="versionsFile" @close="versionsFile = null" />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import type { GeneratedFile } from '../../../peopleCenter/agentApi';
import InlineFilePreview from '../../../peopleCenter/components/InlineFilePreview.vue';
import FileVersionsModal from '../../../peopleCenter/components/FileVersionsModal.vue';
import { downloadUserFile } from '../../../peopleCenter/myfiles.api';
import { fileKindOf, KIND_ICON } from '../../../peopleCenter/composables/fileKind';
import { fileTypeLabel, formatFileSize } from '../../../peopleCenter/composables/executionTimeline';
import {
  isAgentRunEmbedded,
  requestEmbeddedAgentRunDownloadFile,
  requestEmbeddedAgentRunOpenFiles,
} from '../agentRunBack';

type RunGeneratedFile = GeneratedFile & { previewOnly?: boolean };
type PreviewOpenResult = 'opened' | 'pending' | 'unavailable';
type PreviewHandle = { openViewer?: () => PreviewOpenResult };

defineProps<{ files: RunGeneratedFile[] }>();
const router = useRouter();
const versionsFile = ref<GeneratedFile | null>(null);
const previewRefs = new Map<string, PreviewHandle>();
const pendingPreviewIds = new Set<string>();

function canPreview(file: RunGeneratedFile) {
  const ext = (file.filename.split('.').pop() || '').toLowerCase();
  return ['doc', 'docx', 'xlsx', 'xlsm', 'pdf', 'ppt', 'pptx', 'html', 'htm', 'md', 'markdown'].includes(ext);
}
function setPreviewRef(fileId: string, instance: unknown) {
  if (instance && typeof instance === 'object') previewRefs.set(fileId, instance as PreviewHandle);
  else previewRefs.delete(fileId);
}
function openPreview(file: RunGeneratedFile) {
  const result = previewRefs.get(file.id)?.openViewer?.();
  if (result === 'pending') {
    pendingPreviewIds.add(file.id);
    message.info('预览正在生成，完成后会自动打开');
  } else if (result !== 'opened') {
    message.error('暂时无法预览该文件，可先下载后查看');
  }
}
function onPreviewReady(file: RunGeneratedFile) {
  pendingPreviewIds.delete(file.id);
}
function onPreviewFailed(file: RunGeneratedFile) {
  if (!pendingPreviewIds.delete(file.id)) return;
  message.error('预览生成失败，可先下载后查看');
}
async function download(file: RunGeneratedFile) {
  const item = {
    id: file.id,
    filename: file.filename,
    mime: file.mime || '',
    size: file.size,
    source: file.source || 'generated' as const,
    expiresAt: file.expiresAt || null,
    createdAt: file.createdAt || null,
  };
  try {
    // 广场把运行页嵌在 sandbox iframe 里：即使加了 allow-downloads，await fetch 之后的
    // a.click() 也常被 Chrome 当成非用户手势静默丢掉。父页无 sandbox，下载交给它。
    if (isAgentRunEmbedded()) {
      requestEmbeddedAgentRunDownloadFile(item);
      return;
    }
    await downloadUserFile({
      ...item,
      threadId: null,
      folderId: null,
    });
  } catch (error) {
    message.error(error instanceof Error ? error.message : '下载失败');
  }
}
function openMyFiles() {
  if (isAgentRunEmbedded()) {
    requestEmbeddedAgentRunOpenFiles();
    return;
  }
  router.push('/center/files').catch(() => {});
}
</script>

<style lang="less" scoped>
.generated-files {
  display: grid;
  width: min(100%, 720px);
  gap: 8px;
  margin: 12px 0 4px;
}
.generated-file-card {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 58px;
  padding: 10px 12px;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #fff;
  transition: border-color 0.15s ease;
  animation: gen-card-in 0.32s cubic-bezier(0.2, 0.7, 0.3, 1) both;
}
.generated-file-card:hover {
  border-color: #d8dbe2;
}
@keyframes gen-card-in {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: none; }
}
@media (prefers-reduced-motion: reduce) {
  .generated-file-card { animation: none; }
}
.generated-file-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 9px;
  background: #f0f1f4;
  color: #4b5563;
  font-size: 17px;
}
.generated-file-icon.k-word { background: #eef2fc; color: #3b5ba5; }
.generated-file-icon.k-excel { background: #ebf6ef; color: #2f8a5b; }
.generated-file-icon.k-pdf { background: #fceeee; color: #c0554f; }
.generated-file-icon.k-ppt { background: #fdf1e7; color: #c07a25; }
.generated-file-icon.k-image { background: #f1eefb; color: #6b52b8; }
.generated-file-icon.k-markdown,
.generated-file-icon.k-text { background: #eef1f4; color: #5b6472; }
.generated-file-icon.k-archive { background: #f2f0ec; color: #8a7a55; }
.generated-file-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.generated-file-copy strong {
  overflow: hidden;
  color: #202228;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.generated-file-copy em {
  overflow: hidden;
  color: #8a8f99;
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.generated-file-actions {
  display: flex;
  flex: none;
  gap: 2px;
}
.generated-file-card button {
  padding: 4px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.generated-file-card button:hover {
  background: #f2f3f5;
  color: #17181c;
}
.generated-file-card button.generated-file-secondary { color: #8a8f99; }
.generated-file-card button.generated-file-secondary:hover {
  background: #f2f3f5;
  color: #30323a;
}
@media (max-width: 620px) {
  .generated-file-card {
    grid-template-columns: 34px minmax(0, 1fr) auto;
    gap: 8px;
    padding: 9px;
  }
}
</style>
