<template>
  <a-drawer v-model:open="drawerOpen" :title="canEdit ? '编辑分段' : '分段详情'" :width="width" root-class-name="knowledge-drawer chunk-editor-drawer">
    <a-form layout="vertical" class="knowledge-form">
      <a-form-item label="分段内容">
        <div class="chunk-editor-textarea-wrap" @contextmenu="handleChunkContextMenu">
          <a-textarea
            ref="chunkTextareaRef"
            v-model:value="chunkEditorContent"
            :rows="rows"
            :maxlength="20000"
            :readonly="!canEdit"
            show-count
            :placeholder="imageToolsEnabled ? '把光标放到需要插入图片的位置，右键可插入图片。' : '编辑分段内容，保存后会重新生成向量。'"
            @keydown.esc="closeChunkImageMenu"
          />
          <div
            v-if="canEdit && imageToolsEnabled && chunkImageMenu.open"
            class="chunk-image-context-menu"
            :style="{ left: `${chunkImageMenu.left}px`, top: `${chunkImageMenu.top}px` }"
            @click.stop
            @mousedown.prevent
          >
            <a-upload accept="image/png,image/jpeg,image/gif,image/bmp,image/webp" :show-upload-list="false" :custom-request="uploadChunkImageFromMenu">
              <button type="button" class="chunk-image-menu-item">
                <UploadOutlined />
                上传图片并插入到此处
              </button>
            </a-upload>
            <template v-if="chunkEditorImages.length">
              <button
                v-for="(image, index) in chunkEditorImages"
                :key="image.imageId || image.url"
                type="button"
                class="chunk-image-menu-item"
                @click="insertChunkImageFromMenu(image)"
              >
                插入已上传图片 {{ index + 1 }}
              </button>
            </template>
            <span v-else class="chunk-image-menu-empty">暂无已上传图片</span>
          </div>
        </div>
        <p v-if="imageToolsEnabled" class="chunk-content-tip">图片会以 Markdown 形式插入到正文中，保存后检索回复会按这里的图文顺序展示。</p>
      </a-form-item>
      <!-- 用户侧分段由 agent-api 承接，分段表没有图片存储，插图入口整体隐藏而不是留一个必然报错的按钮；
           管理侧仍走原来的接口，保持不动。 -->
      <a-form-item v-if="imageToolsEnabled || chunkEditorImages.length" label="分段图片">
        <div class="chunk-editor-images">
          <div v-for="(image, index) in chunkEditorImages" :key="image.imageId || image.url" class="chunk-editor-image">
            <a-image :src="imageSrc(image.url)" :alt="image.caption || image.ocrText || '分段图片'" />
            <a-button v-if="canEdit" type="link" size="small" @click="insertChunkImage(image)">插入到光标处</a-button>
            <a-button v-if="canEdit" type="text" danger size="small" @click="removeChunkImage(index)">删除图片</a-button>
          </div>
          <a-upload v-if="canEdit && imageToolsEnabled" accept="image/png,image/jpeg,image/gif,image/bmp,image/webp" :show-upload-list="false" :custom-request="uploadChunkImage">
            <a-button :loading="chunkImageUploading"><UploadOutlined /> 上传图片</a-button>
          </a-upload>
        </div>
        <p v-if="imageToolsEnabled" class="chunk-image-tip">支持 PNG、JPG、GIF、BMP、WEBP，单张不超过 5 MB，最多 10 张。</p>
      </a-form-item>
      <a-button v-if="canEdit" type="primary" block size="large" class="drawer-save" :loading="chunkSaving" @click="saveChunk">保存并更新向量</a-button>
    </a-form>
  </a-drawer>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { UploadOutlined } from '@ant-design/icons-vue';
import { useMessage } from '/@/hooks/web/useMessage';
import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';
import { knowledgeErrorMessage, updateChunk, updateManagedChunk, uploadKnowledgeChunkImage, uploadManagedKnowledgeChunkImage } from '../knowledge.api';
import type { KnowledgeChunk, KnowledgePreviewImage } from '../knowledge.types';

type TextSelection = { start: number; end: number };
const MAX_CHUNK_IMAGE_BYTES = 5 * 1024 * 1024;

const props = withDefaults(defineProps<{
  open: boolean;
  chunk?: KnowledgeChunk;
  canEdit?: boolean;
  management?: boolean;
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
const chunkImageUploading = ref(false);
const chunkInsertSelection = ref<TextSelection | null>(null);
const chunkImageMenu = reactive({ open: false, left: 0, top: 0 });

const drawerOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit('update:open', value),
});

// 用户侧（agent-api）的分段只有纯文本正本，没有图片存储与跨用户可读的图片地址，
// 插图入口只在管理侧（仍走原接口）显示。用 management 推断而不加新 prop，
// 是为了不改 MyKnowledgeTab 的调用处。
const imageToolsEnabled = computed(() => Boolean(props.management));

watch(() => [props.open, props.chunk?.id] as const, ([open]) => {
  if (open) initEditor();
  else closeChunkImageMenu();
});

onMounted(() => window.addEventListener('click', closeChunkImageMenu));
onBeforeUnmount(() => window.removeEventListener('click', closeChunkImageMenu));

function initEditor() {
  chunkEditorContent.value = ensureContentWithImages(props.chunk?.contentWithImages || props.chunk?.content, props.chunk?.images || []);
  chunkEditorImages.value = [...(props.chunk?.images || [])];
}

function imageSrc(url?: string) {
  return getProxyStaticFileUrl(url || '');
}

function unwrapChunkImageResponse<T>(response: any): T {
  if (response?.success === false) throw new Error(response.message || '图片上传失败');
  return (response?.result || response) as T;
}

async function uploadChunkImage(options: any) {
  await uploadAndInsertChunkImage(options, null);
}

async function uploadChunkImageFromMenu(options: any) {
  await uploadAndInsertChunkImage(options, chunkInsertSelection.value);
  closeChunkImageMenu();
}

async function uploadAndInsertChunkImage(options: any, selection: TextSelection | null) {
  if (!props.chunk || !props.canEdit) return;
  const file = options.file as File;
  if (!file || file.size > MAX_CHUNK_IMAGE_BYTES) {
    const error = new Error('图片不能超过 5 MB');
    createMessage.warning(error.message);
    options.onError?.(error);
    return;
  }
  if (chunkEditorImages.value.length >= 10) {
    createMessage.warning('一个分段最多上传 10 张图片');
    options.onError?.(new Error('图片数量已达到上限'));
    return;
  }
  chunkImageUploading.value = true;
  try {
    const response = await (props.management ? uploadManagedKnowledgeChunkImage : uploadKnowledgeChunkImage)(props.chunk.id, file);
    const image = unwrapChunkImageResponse<KnowledgePreviewImage>(response);
    chunkEditorImages.value = [...chunkEditorImages.value, image];
    await nextTick();
    insertChunkImage(image, selection);
    options.onSuccess?.({}, options.file);
  } catch (error) {
    options.onError?.(error);
    createMessage.error(error instanceof Error ? error.message : '图片上传失败');
  } finally {
    chunkImageUploading.value = false;
  }
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
    await (props.management ? updateManagedChunk : updateChunk)({ id: props.chunk.id, content, contentWithImages, images: chunkEditorImages.value });
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

function currentTextareaSelection(): TextSelection {
  const textarea = textareaElement();
  const fallback = chunkEditorContent.value.length;
  if (!textarea) return { start: fallback, end: fallback };
  return { start: textarea.selectionStart ?? fallback, end: textarea.selectionEnd ?? textarea.selectionStart ?? fallback };
}

function openChunkImageMenu(event: MouseEvent) {
  const wrapper = event.currentTarget as HTMLElement;
  const rect = wrapper.getBoundingClientRect();
  chunkInsertSelection.value = currentTextareaSelection();
  chunkImageMenu.left = Math.max(8, Math.min(event.clientX - rect.left, rect.width - 230));
  chunkImageMenu.top = Math.max(8, event.clientY - rect.top);
  chunkImageMenu.open = true;
}

function handleChunkContextMenu(event: MouseEvent) {
  // 不支持插图时放行浏览器原生右键菜单（复制/粘贴），不弹一个空的插图菜单
  if (!props.canEdit || !imageToolsEnabled.value) return;
  event.preventDefault();
  openChunkImageMenu(event);
}

function closeChunkImageMenu() {
  chunkImageMenu.open = false;
}

function insertChunkImageFromMenu(image: KnowledgePreviewImage) {
  insertChunkImage(image, chunkInsertSelection.value);
  closeChunkImageMenu();
}

function insertChunkImage(image: KnowledgePreviewImage, selection: TextSelection | null = null) {
  if (!props.canEdit || !image?.url) return;
  insertTextAtCursor(`\n\n${markdownImage(image)}\n\n`, selection);
}

function insertTextAtCursor(text: string, selection: TextSelection | null = null) {
  const textarea = textareaElement();
  if (!textarea) {
    chunkEditorContent.value = `${chunkEditorContent.value || ''}${text}`.trim();
    return;
  }
  const value = chunkEditorContent.value || '';
  const start = selection?.start ?? textarea.selectionStart ?? value.length;
  const end = selection?.end ?? textarea.selectionEnd ?? start;
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
.chunk-editor-textarea-wrap {
  position: relative;
}

.chunk-content-tip {
  margin: 8px 0 0;
  color: #667085;
  font-size: 12px;
  line-height: 1.6;
}

.chunk-image-context-menu {
  position: absolute;
  z-index: 20;
  width: 220px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.16);
}

.chunk-image-menu-item {
  display: flex;
  width: 100%;
  min-height: 38px;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 0;
  background: #fff;
  color: #1d2939;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}

.chunk-image-menu-item:hover {
  background: #f8fafc;
  color: #2563eb;
}

.chunk-image-menu-empty {
  display: block;
  padding: 9px 12px;
  color: #98a2b3;
  font-size: 12px;
}

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

.chunk-image-tip {
  margin: 10px 0 0;
  color: #667085;
  font-size: 12px;
}

.drawer-save {
  margin-top: 6px;
}
</style>
