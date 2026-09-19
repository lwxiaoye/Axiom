<template>
  <a-drawer v-model:open="drawerOpen" :title="canEdit ? '编辑分段' : '分段详情'" :width="width" root-class-name="knowledge-drawer chunk-editor-drawer">
    <a-form layout="vertical" class="knowledge-form">
      <a-form-item label="分段内容">
        <a-textarea
          ref="chunkTextareaRef"
          v-model:value="chunkEditorContent"
          :rows="rows"
          :maxlength="20000"
          :readonly="!canEdit"
          show-count
          placeholder="编辑分段内容，保存后会重新生成向量。"
        />
      </a-form-item>
      <!-- agent-api 的分段表没有图片存储，也没有插图上传入口；只有老数据带 images 时才展示，
           让编辑者还能把它们插回正文或删掉。 -->
      <a-form-item v-if="chunkEditorImages.length" label="分段图片">
        <div class="chunk-editor-images">
          <div v-for="(image, index) in chunkEditorImages" :key="image.imageId || image.url" class="chunk-editor-image">
            <a-image :src="imageSrc(image.url)" :alt="image.caption || image.ocrText || '分段图片'" />
            <a-button v-if="canEdit" type="link" size="small" @click="insertChunkImage(image)">插入到光标处</a-button>
            <a-button v-if="canEdit" type="text" danger size="small" @click="removeChunkImage(index)">删除图片</a-button>
          </div>
        </div>
      </a-form-item>
      <a-button v-if="canEdit" type="primary" block size="large" class="drawer-save" :loading="chunkSaving" @click="saveChunk">保存并更新向量</a-button>
    </a-form>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { useMessage } from '/@/hooks/web/useMessage';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';
import { knowledgeErrorMessage, updateChunk } from '../knowledge.api';
import type { KnowledgeChunk, KnowledgePreviewImage } from '../knowledge.types';

const props = withDefaults(defineProps<{
  open: boolean;
  chunk?: KnowledgeChunk;
  canEdit?: boolean;
  width?: number | string;
  rows?: number;
}>(), {
  canEdit: true,
  width: 620,
  rows: 15,
});

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'saved'): void;
}>();

const { createMessage } = useMessage();
const chunkEditorContent = ref('');
const chunkTextareaRef = ref<any>();
const chunkEditorImages = ref<KnowledgePreviewImage[]>([]);
const chunkSaving = ref(false);

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

watch(() => [props.open, props.chunk?.id] as const, ([open]) => {
  if (open) initEditor();
});

function initEditor() {
  chunkEditorContent.value = ensureContentWithImages(props.chunk?.contentWithImages || props.chunk?.content, props.chunk?.images || []);
  chunkEditorImages.value = [...(props.chunk?.images || [])];
}

function imageSrc(url?: string) {
  return getProxyStaticFileUrl(url || '');
}

function removeChunkImage(index: number) {
  if (!props.canEdit) return;
  const image = chunkEditorImages.value[index];
  if (image?.url) {
    removeMarkdownImageByUrl(image.url);
  }
  chunkEditorImages.value.splice(index, 1);
}

async function saveChunk() {
  if (!props.chunk || !props.canEdit) return;
  const contentWithImages = chunkEditorContent.value.trim();
  const content = stripMarkdownImages(contentWithImages);
  if (!content) {
    createMessage.warning('分段内容不能为空');
    return;
  }
  chunkSaving.value = true;
  try {
    await updateChunk({ id: props.chunk.id, content, contentWithImages, images: chunkEditorImages.value });
    createMessage.success('分段和向量已更新');
    emit('update:open', false);
    emit('saved');
  } catch (error) {
    // 用户侧请求关掉了自动报错提示，失败原因（如重嵌入失败）要在这里显示出来
    createMessage.error(knowledgeErrorMessage(error, '分段保存失败'));
  } finally {
    chunkSaving.value = false;
  }
}

function markdownImage(image: KnowledgePreviewImage) {
  return `![知识库图片](<${image.url}>)`;
}

function ensureContentWithImages(content: string | undefined, images: KnowledgePreviewImage[]) {
  const base = String(content || '').trim();
  const missingImages = images.filter((image) => image.url && !hasMarkdownImage(base, image.url));
  const imageMarkdown = missingImages.map(markdownImage).join('\n\n');
  return imageMarkdown ? `${base}\n\n${imageMarkdown}`.trim() : base;
}

function hasMarkdownImage(content: string, url: string) {
  const escapedUrl = url.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`!\\[[^\\]\\n]*\\]\\((?:<${escapedUrl}>|${escapedUrl})\\)`).test(content);
}

function textareaElement(): HTMLTextAreaElement | null {
  const target = chunkTextareaRef.value;
  return target?.resizableTextArea?.textArea || target?.$el?.querySelector?.('textarea') || null;
}

function insertChunkImage(image: KnowledgePreviewImage) {
  if (!props.canEdit || !image?.url) return;
  insertTextAtCursor(`\n\n${markdownImage(image)}\n\n`);
}

function insertTextAtCursor(text: string) {
  const textarea = textareaElement();
  if (!textarea) {
    chunkEditorContent.value = `${chunkEditorContent.value || ''}${text}`.trim();
    return;
  }
  const value = chunkEditorContent.value || '';
  const start = textarea.selectionStart ?? value.length;
  const end = textarea.selectionEnd ?? start;
  chunkEditorContent.value = `${value.slice(0, start)}${text}${value.slice(end)}`;
  nextTick(() => {
    textarea.focus();
    const cursor = start + text.length;
    textarea.setSelectionRange(cursor, cursor);
  });
}

function removeMarkdownImageByUrl(url: string) {
  chunkEditorContent.value = chunkEditorContent.value
    .replace(/!\[[^\]\n]*\]\((?:<([^>\n]+)>|([^)]+))\)/g, (full, bracketUrl, plainUrl) => {
      const targetUrl = String(bracketUrl || plainUrl || '').trim();
      return targetUrl === url ? '' : full;
    })
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function stripMarkdownImages(value: string) {
  return String(value || '')
    .replace(/!\[[^\]\n]*\]\((?:<[^>\n]+>|[^)\n]+)\)/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}
</script>

<style scoped lang="less">
.chunk-editor-images {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 12px;
}

.chunk-editor-image {
  display: grid;
  width: 118px;
  gap: 5px;
}

.chunk-editor-image :deep(.ant-image),
.chunk-editor-image :deep(img) {
  width: 118px;
  height: 90px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  object-fit: contain;
}

.drawer-save {
  margin-top: 6px;
}
</style>
