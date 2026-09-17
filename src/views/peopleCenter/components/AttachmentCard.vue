<template>
  <div :class="['attachment-card', { 'is-image': isImage, 'is-large': large }]">
    <!-- 图片：缩略图，点开走大图预览。large=消息气泡里显示的已发送图，放大且保留宽高比（对齐 ChatGPT）。
         解析失败时不走缩略图分支——否则失败态/重试入口永远不可达，强制降级到下方的文件卡分支 -->
    <button
      v-if="isImage && !parseFailed && (displayPreview || previewLoading)"
      type="button"
      :class="['attachment-thumb', { large, uniform }]"
      title="点击查看大图"
      :disabled="!displayPreview"
      @click="displayPreview && emit('preview', displayPreview)"
    >
      <img v-if="displayPreview" :src="displayPreview" :alt="attachment.filename" />
    </button>

    <!-- 文档：图标 + 文件名 + 类型标签（解析失败/不完整时标注原因，可点「重试」） -->
    <div
      v-else
      :class="['attachment-file', { 'parse-failed': parseFailed, 'is-openable': clickable }]"
      :role="clickable ? 'button' : undefined"
      :tabindex="clickable ? 0 : undefined"
      :title="clickable ? '点击预览' : undefined"
      @click="onFileClick"
      @keydown.enter.prevent="onFileClick"
    >
      <span :class="['attachment-file-icon', kindClass]">
        <component :is="fileIcon" />
      </span>
      <span class="attachment-file-meta">
        <span class="attachment-file-name">{{ attachment.filename }}</span>
        <span :class="['attachment-file-type', { failed: parseFailed, partial: parsePartial }]">
          {{ statusLabel }}
        </span>
      </span>
      <button
        v-if="retriable && (parseFailed || parsePartial)"
        type="button"
        class="attachment-retry"
        title="重新上传并解析"
        @click.stop="emit('retry')"
      >
        重试
      </button>
    </div>

    <!-- 上传/解析中：缩略图或文件卡上盖一层转圈（本地预览已出、后端 OCR 仍在跑） -->
    <span
      v-if="attachment.uploading || previewLoading"
      :class="['attachment-loading', { 'on-thumb': isImage && (displayPreview || previewLoading) }]"
      aria-label="处理中"
    >
      <LoadingOutlined />
    </span>

    <button
      v-if="removable"
      type="button"
      class="attachment-remove"
      title="移除"
      aria-label="移除"
      @click.stop="emit('remove')"
    >
      <CloseOutlined v-if="isImage" />
      <span v-else aria-hidden="true">×</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { fetchUserFileBlobUrl } from '../myfiles.api';
import { fileKindOf } from '../composables/fileKind';
import {
  CloseOutlined,
  FileExcelFilled,
  FileFilled,
  FileMarkdownFilled,
  FilePdfFilled,
  FilePptFilled,
  FileTextFilled,
  FileWordFilled,
  GlobalOutlined,
  LoadingOutlined,
  MessageFilled,
  ReadFilled,
  RobotFilled,
  ToolFilled,
} from '@ant-design/icons-vue';

export type AttachmentInfo = {
  filename: string;
  kind?: string;
  previewUrl?: string;
  /** 引用卡的稳定身份（例如 Skill id），仅用于恢复原轮上下文。 */
  referenceId?: string;
  // 上传+后端解析进行中：卡片上盖转圈
  uploading?: boolean;
  /** 解析置信度（P0 附件生命周期）：failed=未提取出内容 / partial=截断或部分失败 */
  status?: string;
  note?: string;
  /** 已落库文件实体：发出去的文档卡可点开展览器 */
  fileId?: string;
};

const props = defineProps<{
  attachment: AttachmentInfo;
  removable?: boolean;
  /** 消息气泡里的已发送图：放大显示、保留宽高比（对齐 ChatGPT）；composer 待发小卡不传 */
  large?: boolean;
  /** 同消息多图统一瓦片（2026-07-15 用户拍板「大小要一致」）：等大方块 + object-fit 裁切，
   *  点开 lightbox 看原图；单图消息不传，保留自适应大图 */
  uniform?: boolean;
  /** composer 待发卡：解析失败/不完整时显示「重试」按钮（消息气泡里的历史卡不给） */
  retriable?: boolean;
  /** 已发送的文档卡：点击打开展览器。对话/知识库/Skill 不传 */
  clickable?: boolean;
}>();

const emit = defineEmits<{
  (e: 'remove'): void;
  (e: 'preview', src: string): void;
  (e: 'retry'): void;
  (e: 'open'): void;
}>();

function onFileClick() {
  if (props.clickable) emit('open');
}

const parseFailed = computed(() => !props.attachment.uploading && props.attachment.status === 'failed');
const parsePartial = computed(() => !props.attachment.uploading && props.attachment.status === 'partial');

/** 「最近的对话」引用块（后端 THREAD_REF_KIND）：它不是文件，一切按扩展名走的判定都要绕开。
 *  否则一个标题叫「帮我改 report.xlsx」的会话会被当成 Excel 附件，类型标签还会印出
 *  「XLSX（对话记录）」这种从扩展名硬切出来的字符串。 */
const isThreadRef = computed(() => props.attachment.kind === 'thread_ref');
const isKnowledge = computed(() => props.attachment.kind === 'knowledge');
const isSkill = computed(() => props.attachment.kind === 'skill');
const isSubagent = computed(() => props.attachment.kind === 'subagent');
const isWeb = computed(() => props.attachment.kind === 'web');

const ext = computed(() => {
  if (isThreadRef.value || isKnowledge.value || isSkill.value || isSubagent.value || isWeb.value) return '';
  const name = props.attachment.filename || '';
  return name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
});

const isImage = computed(() => {
  if (isThreadRef.value || isKnowledge.value || isSkill.value || isSubagent.value || isWeb.value) return false;
  if (props.attachment.kind === 'image') return true;
  return fileKindOf(props.attachment.filename) === 'image';
});

const IMAGE_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
  ico: 'image/x-icon',
  avif: 'image/avif',
  heic: 'image/heic',
  heif: 'image/heif',
  tif: 'image/tiff',
  tiff: 'image/tiff',
};

const loadedPreview = ref('');
const previewLoading = ref(false);
let objectUrl = '';
let loadGen = 0;

const displayPreview = computed(() => props.attachment.previewUrl || loadedPreview.value);

watch(
  [
    isImage,
    () => props.attachment.fileId || '',
    () => props.attachment.previewUrl || '',
    parseFailed,
  ],
  async ([image, fileId, preview, failed]) => {
    const gen = (loadGen += 1);
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
      objectUrl = '';
    }
    loadedPreview.value = '';
    if (!image || preview || failed || !fileId) {
      previewLoading.value = false;
      return;
    }
    previewLoading.value = true;
    try {
      const url = await fetchUserFileBlobUrl(fileId, IMAGE_MIME[ext.value] || 'image/png');
      if (gen !== loadGen) {
        URL.revokeObjectURL(url);
        return;
      }
      objectUrl = url;
      loadedPreview.value = url;
    } catch {
      if (gen !== loadGen) return;
      loadedPreview.value = '';
    } finally {
      if (gen === loadGen) previewLoading.value = false;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  loadGen += 1;
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    objectUrl = '';
  }
});

const fileIcon = computed(() => {
  if (isThreadRef.value) return MessageFilled;
  if (isKnowledge.value) return ReadFilled;
  if (isSkill.value) return ToolFilled;
  if (isSubagent.value) return RobotFilled;
  if (isWeb.value) return GlobalOutlined;
  if (ext.value === 'pdf' || props.attachment.kind === 'pdf') return FilePdfFilled;
  if (['docx', 'doc'].includes(ext.value) || props.attachment.kind === 'docx') return FileWordFilled;
  if (['pptx', 'ppt'].includes(ext.value) || props.attachment.kind === 'pptx') return FilePptFilled;
  if (['md', 'markdown'].includes(ext.value)) return FileMarkdownFilled;
  if (['xls', 'xlsx', 'xlsm', 'csv'].includes(ext.value) || props.attachment.kind === 'xlsx') return FileExcelFilled;
  if (['txt', 'json', 'log', 'yaml', 'yml', 'xml', 'html'].includes(ext.value)) return FileTextFilled;
  return FileFilled;
});

const typeLabel = computed(() => {
  if (isThreadRef.value) return '对话';
  if (isKnowledge.value) return '知识库';
  if (isSkill.value) return '技能';
  if (isSubagent.value) return '智能体';
  if (isWeb.value) return '搜索';
  if (ext.value === 'pdf' || props.attachment.kind === 'pdf') return 'PDF';
  if (['docx', 'doc'].includes(ext.value) || props.attachment.kind === 'docx') return '文档';
  if (['pptx', 'ppt'].includes(ext.value) || props.attachment.kind === 'pptx') return '演示文稿';
  if (['xls', 'xlsx', 'xlsm', 'csv'].includes(ext.value) || props.attachment.kind === 'xlsx') return '电子表格';
  if (['md', 'markdown'].includes(ext.value)) return '文件';
  if (ext.value === 'txt') return '文本';
  if (isImage.value) return '图片';
  return '文件';
});

const statusLabel = computed(() => {
  if (props.attachment.uploading) return '解析中…';
  if (parseFailed.value) return `解析失败${props.attachment.note ? `：${props.attachment.note}` : ''}`;
  if (parsePartial.value) return `部分读取${props.attachment.note ? `：${props.attachment.note}` : ''}`;
  return typeLabel.value;
});

const kindClass = computed(() => {
  if (isThreadRef.value) return 'k-thread';
  if (isKnowledge.value) return 'k-kb';
  if (isSkill.value) return 'k-skill';
  if (isSubagent.value) return 'k-agent';
  if (isWeb.value) return 'k-web';
  if (ext.value === 'pdf' || props.attachment.kind === 'pdf') return 'k-pdf';
  if (['docx', 'doc'].includes(ext.value) || props.attachment.kind === 'docx') return 'k-doc';
  if (['xls', 'xlsx', 'xlsm', 'csv'].includes(ext.value) || props.attachment.kind === 'xlsx') return 'k-xls';
  if (['pptx', 'ppt'].includes(ext.value) || props.attachment.kind === 'pptx') return 'k-ppt';
  if (['md', 'markdown'].includes(ext.value)) return 'k-md';
  return 'k-default';
});
</script>

<style scoped>
.attachment-card {
  position: relative;
  flex: none;
  overflow: visible;
}

/* 图片缩略卡 —— 保持原先 60×60 圆角方块，不跟文件卡一起缩 */
.attachment-thumb {
  display: block;
  width: 60px;
  height: 60px;
  padding: 0;
  border: 1px solid #e4e4e8;
  border-radius: 12px;
  overflow: hidden;
  background: #f4f4f6;
  cursor: zoom-in;
}

.attachment-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* 已发送的大图（消息气泡）：放大、保留原始宽高比、圆角更大（对齐 ChatGPT） */
.attachment-thumb.large {
  width: auto;
  height: auto;
  max-width: 280px;
  border-radius: 16px;
  cursor: zoom-in;
}

.attachment-thumb.large img {
  width: auto;
  height: auto;
  max-width: 280px;
  max-height: 360px;
  object-fit: contain;
}

/* 同消息多图统一瓦片（放 .large 之后覆盖它的 auto 尺寸）：等大方块、object-fit 裁切——
   大图小图排在一起大小一致（对齐微信/ChatGPT 多图网格），点开 lightbox 看原图 */
.attachment-thumb.uniform,
.attachment-thumb.uniform img {
  width: 160px;
  height: 160px;
  max-width: none;
  max-height: none;
}

.attachment-thumb.uniform img {
  object-fit: cover;
}

/* 文档卡 —— 对齐 ChatGPT 输入框附件：类型色图标 + 文件名/类型两行；宽度比 ChatGPT 略短 */
.attachment-file {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 216px;
  max-width: min(216px, 100%);
  height: 50px;
  box-sizing: border-box;
  border: 1px solid #ebebeb;
  border-radius: 16px;
  background: #f4f4f5;
  padding: 8px 12px 8px 10px;
}

.attachment-file-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex: none;
  place-items: center;
  border-radius: 0;
  background: transparent;
  color: #5b6472;
  font-size: 22px;
  line-height: 1;
}

.attachment-file-icon.k-pdf { color: #e11d24; }
.attachment-file-icon.k-doc { color: #2b7cd3; }
.attachment-file-icon.k-xls { color: #217346; }
.attachment-file-icon.k-ppt { color: #c43e1c; }
.attachment-file-icon.k-md { color: #4b5563; }
.attachment-file-icon.k-thread { color: #2563eb; }
.attachment-file-icon.k-kb { color: #0f766e; }
.attachment-file-icon.k-skill { color: #2563eb; }
.attachment-file-icon.k-agent { color: #1f2937; }
.attachment-file-icon.k-web { color: #2563eb; }

.attachment-file-meta {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.attachment-file-name {
  overflow: hidden;
  color: #0d0d0d;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.25;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.attachment-file-type {
  color: #8e8ea0;
  font-size: 12px;
  letter-spacing: 0;
  line-height: 1.2;
}

/* 解析失败/部分读取：状态行标色 + 卡片红描边（失败）；重试按钮贴右侧 */
.attachment-file-type.failed {
  color: #c45252;
}

.attachment-file-type.partial {
  color: #b8860b;
}

.attachment-file.parse-failed {
  border-color: #ecc8c8;
}

.attachment-retry {
  flex: none;
  padding: 2px 8px;
  border: 1px solid #e0e1e6;
  border-radius: 7px;
  background: #fff;
  color: #4d525c;
  font-size: 11px;
  cursor: pointer;
}

.attachment-retry:hover {
  border-color: #c9cbd2;
  color: #111;
}

.attachment-file.is-openable {
  cursor: pointer;
}

.attachment-file.is-openable:hover {
  background: #ececee;
}

/* 上传/解析中的转圈遮罩：图片盖在缩略图上（半透明白），文档卡贴右侧图标区 */
.attachment-loading {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.62);
  color: #111827;
  font-size: 18px;
  pointer-events: none;
}

/* 图片缩略图上：暗一点的遮罩 + 白色圈，转圈在深色照片上也清晰 */
.attachment-loading.on-thumb {
  background: rgba(17, 24, 39, 0.42);
  color: #fff;
}

/* 移除：ChatGPT 式黑底白叉，直径比它略小；悬停卡片才出现 */
.attachment-remove {
  position: absolute;
  top: -5px;
  right: -5px;
  z-index: 1;
  display: grid;
  width: 15px;
  height: 15px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: #111;
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.28);
  transition: opacity 0.12s ease;
}

.attachment-remove span {
  display: block;
  margin-top: -1px;
}

.attachment-card:hover .attachment-remove,
.attachment-remove:focus-visible {
  opacity: 1;
}

@media (hover: none) {
  .attachment-remove {
    opacity: 1;
  }
}

/* 图片仍用改前的白底灰叉，常显，不跟文件卡的黑叉混用 */
.attachment-card.is-image .attachment-remove {
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border: 1px solid #e4e4e8;
  background: #fff;
  color: #6b7280;
  font-size: 10px;
  font-weight: 400;
  opacity: 1;
  box-shadow: 0 1px 3px rgba(17, 24, 39, 0.12);
  transition: background 0.15s ease, color 0.15s ease;
}

.attachment-card.is-image .attachment-remove:hover {
  background: #111;
  color: #fff;
}
</style>
