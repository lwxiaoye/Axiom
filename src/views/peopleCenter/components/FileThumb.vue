<template>
  <div ref="rootRef" class="file-thumb" :class="{ ready: state === 'ready' }">
    <img v-if="state === 'ready' && imgUrl" :src="imgUrl" class="ft-img" :alt="file.filename" />
    <canvas v-else-if="state === 'ready'" ref="canvasRef" class="ft-canvas" />
    <!-- 文本/Markdown/表格：渲染成内容片段缩略（对齐 Manus 文档预览）-->
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-else-if="state === 'html'" class="ft-html" v-html="htmlContent" />
    <!-- 网页（html）：沙箱 iframe 按整页渲染再缩放，呈现「网站首屏」缩略。
         webSrc 是未消毒的上传 HTML，缩略只需版面（pointer-events:none，永不交互），
         故 sandbox=""（禁脚本）——注入的 <script>/<svg onload> 全部失活，样式/图片仍渲染。
         缩略图随列表滚动懒加载，绝不能开 allow-scripts 让未信任 HTML 划过即自动执行。 -->
    <div v-else-if="state === 'web'" class="ft-web">
      <iframe
        class="ft-web-frame"
        sandbox=""
        tabindex="-1"
        :srcdoc="webSrc"
        :style="{ transform: `scale(${webScale})` }"
        title="网页预览"
      ></iframe>
    </div>
    <!-- 占位：无缩略图（压缩/旧格式等）→ 大图标；失败同样退化为图标，绝不弹窗 -->
    <div v-else-if="state === 'placeholder'" class="ft-ph">
      <component :is="icon" class="ft-ph-icon" />
    </div>
    <!-- 加载中：先空白 + 极轻微光，转好即填入（对齐 Manus，全程无弹窗） -->
    <div v-else class="ft-loading" aria-label="缩略图加载中" />
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { destroyPdfDoc, loadPdfDocFromUrl } from '../utils/pdfDoc';
import { renderMarkdownDoc } from '../utils/mdRender';
import {
  fetchUserFileBlobUrl,
  fetchUserFilePreviewPdfUrl,
  fetchUserFileText,
  getUserFileContent,
  type UserFileItem,
} from '../myfiles.api';

function escapeHtml(s: string): string {
  return s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] as string));
}

function clipCell(s: string): string {
  const t = s.trim();
  return t.length > 16 ? `${t.slice(0, 16)}…` : t;
}

/** 表格数据 → 缩略 HTML 表（首行当表头，最多 6 列）。xlsx/csv 缩略共用。 */
function buildTableThumb(rows: string[][]): string {
  if (!rows.length) return '';
  const head = (rows[0] || []).slice(0, 6);
  const th = head.map((c) => `<th>${escapeHtml(clipCell(c))}</th>`).join('');
  const body = rows
    .slice(1)
    .map(
      (r) => `<tr>${r.slice(0, 6).map((c) => `<td>${escapeHtml(clipCell(c))}</td>`).join('')}</tr>`,
    )
    .join('');
  return `<table class="ft-tbl"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
}

/** xlsx 解析文本（_parse_xlsx：「【工作表：X】」+「a | b | c」行）→ 首张工作表的行 */
function parseSheetRows(text: string): string[][] {
  const rows: string[][] = [];
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    if (line.startsWith('【')) {
      if (rows.length) break; // 到第二张工作表就停（缩略只取首表）
      continue; // 跳过首张表的标题行
    }
    rows.push(line.split(' | '));
    if (rows.length >= 16) break;
  }
  return rows;
}

/** csv 原文 → 行（缩略用最小切分，不做完整引号转义） */
function parseCsvRows(raw: string): string[][] {
  const rows: string[][] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    rows.push(line.split(','));
    if (rows.length >= 16) break;
  }
  return rows;
}

/** 缩略图子组件：懒加载（进视口才转）+ 空白→填充 + 失败退化图标，永不弹窗。
 *  image → blob 直显；pdf → 首页转 canvas；doc/ppt → 后端预览 PDF 首页转 canvas
 *  （首次生成时已后台预热缓存，命中即秒出）。 */
const props = defineProps<{
  file: UserFileItem;
  kind: string; // FileKind：image/pdf/word/excel/ppt/markdown/text/archive/other
  icon: unknown; // 占位大图标组件
}>();

type ThumbState = 'idle' | 'loading' | 'ready' | 'html' | 'web' | 'placeholder';
const state = ref<ThumbState>('idle');
const imgUrl = ref('');
const htmlContent = ref('');
const webSrc = ref('');
const webScale = ref(0);
const rootRef = ref<HTMLElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
let objectUrl = '';
let pdfDoc: PDFDocumentProxy | null = null;
let disposed = false;
let observer: IntersectionObserver | null = null;

// 版式文档（LibreOffice → PDF 首页转 canvas，与查看器首页一致）
const OFFICE_PDF_EXTS = new Set(['doc', 'docx', 'ppt', 'pptx']);
// html 缩略：iframe 按此逻辑宽度整页渲染，再等比缩到卡片宽（呈现「网站首屏」）
const WEB_RENDER_W = 820;

function fileExt(): string {
  const n = props.file.filename;
  return n.includes('.') ? n.split('.').pop()!.toLowerCase() : '';
}

function cleanup() {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl);
    objectUrl = '';
  }
  destroyPdfDoc(pdfDoc);
  pdfDoc = null;
}

async function renderPdfFirstPage(url: string) {
  // 解析期间组件可能已卸载（列表滚动/切页）：cleanup() 早已跑过，此时再写进实例变量就
  // 永远没人 destroy——先落局部变量，确认没卸载才接管
  const doc = await loadPdfDocFromUrl(url);
  if (disposed) {
    destroyPdfDoc(doc);
    return;
  }
  pdfDoc = doc;
  const page = await pdfDoc.getPage(1);
  if (disposed) return;
  const box = rootRef.value;
  const targetW = Math.max(180, box?.clientWidth || 220);
  const dpr = Math.min(typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1, 2);
  const base = page.getViewport({ scale: 1 });
  const viewport = page.getViewport({ scale: (targetW / base.width) * dpr });
  state.value = 'ready';
  // 等 v-else-if 渲染出 canvas 元素再绘制
  await new Promise((r) => requestAnimationFrame(() => r(null)));
  const canvas = canvasRef.value;
  if (!canvas || disposed) return;
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = '100%'; // 物理像素按 dpr 放大，CSS 缩回容器宽，保清晰
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  // pdfjs v6：render 参数必须带 canvas（对齐 DocPagesViewer），否则运行时报错
  await page.render({ canvasContext: ctx, viewport, canvas }).promise;
}

/** 按真实扩展名给每种类型出真实缩略图（icon 仅作最终退化兜底） */
async function load() {
  if (state.value !== 'idle') return;
  state.value = 'loading';
  const ext = fileExt();
  try {
    // 三个 blob 分支统一：URL 先落局部变量，确认未卸载再交给 objectUrl（cleanup 的释放对象）。
    // 反过来先赋值再判 disposed 会漏——卸载发生在 fetch 挂起期间时 cleanup() 已经跑完，
    // 之后写进作废实例的 URL 再无人 revoke（列表滚动/切板块时必现）。
    if (props.kind === 'image') {
      const url = await fetchUserFileBlobUrl(props.file.id);
      if (disposed) { URL.revokeObjectURL(url); return; }
      objectUrl = url;
      imgUrl.value = url;
      state.value = 'ready';
      return;
    }
    if (ext === 'pdf') {
      const url = await fetchUserFileBlobUrl(props.file.id, 'application/pdf');
      if (disposed) { URL.revokeObjectURL(url); return; }
      objectUrl = url;
      await renderPdfFirstPage(url);
      return;
    }
    if (OFFICE_PDF_EXTS.has(ext)) {
      const url = await fetchUserFilePreviewPdfUrl(props.file.id);
      if (disposed) { URL.revokeObjectURL(url); return; }
      objectUrl = url;
      await renderPdfFirstPage(url);
      return;
    }
    if (ext === 'html' || ext === 'htm') {
      const raw = await fetchUserFileText(props.file.id);
      if (disposed) return;
      webSrc.value = raw;
      state.value = 'web';
      await nextTick();
      // 缩放 = 卡片宽 / 逻辑渲染宽；rootRef 已在 DOM，clientWidth 可测
      webScale.value = (rootRef.value?.clientWidth || 240) / WEB_RENDER_W;
      return;
    }
    if (ext === 'md' || ext === 'markdown') {
      const raw = await fetchUserFileText(props.file.id);
      if (disposed) return;
      htmlContent.value = renderMarkdownDoc(raw);
      state.value = 'html';
      return;
    }
    if (ext === 'xlsx' || ext === 'xlsm') {
      // 解析后的表格文本（后端 _parse_xlsx）→ 缩略表；比 LibreOffice 转 PDF 更轻更稳
      const data = await getUserFileContent(props.file.id);
      if (disposed) return;
      const rows = parseSheetRows(data.text || '');
      if (!rows.length) { state.value = 'placeholder'; return; }
      htmlContent.value = buildTableThumb(rows);
      state.value = 'html';
      return;
    }
    if (ext === 'csv') {
      const raw = await fetchUserFileText(props.file.id);
      if (disposed) return;
      const rows = parseCsvRows(raw);
      if (!rows.length) { state.value = 'placeholder'; return; }
      htmlContent.value = buildTableThumb(rows);
      state.value = 'html';
      return;
    }
    if (props.kind === 'text') {
      const raw = await fetchUserFileText(props.file.id);
      if (disposed) return;
      htmlContent.value = `<pre class="ft-pre">${escapeHtml(raw.slice(0, 4000))}</pre>`;
      state.value = 'html';
      return;
    }
    state.value = 'placeholder'; // 旧格式 xls/压缩包/未知：退化为图标
  } catch {
    // 转换失败 / 网络异常：安静退化为占位图标，绝不弹窗、不打断
    if (!disposed) state.value = 'placeholder';
    cleanup();
  }
}

onMounted(() => {
  const el = rootRef.value;
  if (!el || typeof IntersectionObserver === 'undefined') {
    load();
    return;
  }
  observer = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) {
      observer?.disconnect();
      observer = null;
      load();
    }
  }, { rootMargin: '200px' });
  observer.observe(el);
});

onBeforeUnmount(() => {
  disposed = true;
  observer?.disconnect();
  cleanup();
});
</script>

<style scoped lang="less">
.file-thumb {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: flex-start; /* 文档首页顶对齐：展示页面顶部，超出高度部分由父容器裁切 + 渐隐 */
  justify-content: center;
  overflow: hidden;
  background: #fafafb;
}
/* 文档首页：铺满宽度、按比例延伸，高于卡片的部分裁切（呈现「首页顶部」）*/
.ft-canvas {
  width: 100%;
  display: block;
}
/* 图片：完整放入卡片、居中 */
.ft-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  margin: auto;
  display: block;
}
/* 文本 / Markdown 内容片段：缩小字号呈现「文档首屏」，超出裁切 + 父层渐隐 */
.ft-html {
  width: 100%;
  height: 100%;
  padding: 12px 14px;
  overflow: hidden;
  font-size: 10.5px;
  line-height: 1.55;
  color: #3a3a42;
  text-align: left;

  :deep(h1) { font-size: 14px; font-weight: 700; margin: 0 0 6px; color: #17171c; }
  :deep(h2) { font-size: 12.5px; font-weight: 700; margin: 8px 0 4px; color: #17171c; }
  :deep(h3) { font-size: 11.5px; font-weight: 600; margin: 6px 0 3px; }
  :deep(p) { margin: 0 0 5px; }
  :deep(ul),
  :deep(ol) { margin: 0 0 5px; padding-left: 16px; }
  :deep(li) { margin: 1px 0; }
  :deep(code) { font-size: 9.5px; background: #f1f1f4; padding: 0 3px; border-radius: 3px; }
  :deep(pre) { margin: 0; white-space: pre-wrap; word-break: break-word; font-size: 9.5px; }
  :deep(.ft-pre) { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: #4a4a52; }
  :deep(img) { max-width: 100%; }
  :deep(table) { font-size: 9px; border-collapse: collapse; }
  :deep(th),
  :deep(td) { border: 1px solid #eee; padding: 1px 4px; }

  /* xlsx/csv 缩略表：轻表头 + 细网格，呈现「表格首屏」 */
  :deep(.ft-tbl) {
    width: 100%;
    border-collapse: collapse;
    font-size: 9.5px;
    line-height: 1.5;
    table-layout: fixed;
  }
  :deep(.ft-tbl th),
  :deep(.ft-tbl td) {
    border: 1px solid #e9e9ee;
    padding: 2px 5px;
    text-align: left;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  :deep(.ft-tbl th) { background: #f4f4f7; font-weight: 600; color: #2a2a30; }
  :deep(.ft-tbl td) { color: #55555e; }
}
/* html 网页缩略：整页按逻辑宽渲染后等比缩放，overflow 裁出首屏 */
.ft-web {
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.ft-web-frame {
  width: 820px;
  height: 1180px;
  border: 0;
  background: #fff;
  transform-origin: 0 0;
  pointer-events: none;
}
.ft-ph {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #c7c7cf;
  font-size: 40px;
}
.ft-loading {
  width: 100%;
  height: 100%;
  background: linear-gradient(100deg, #fafafb 30%, #f1f1f4 50%, #fafafb 70%);
  background-size: 200% 100%;
  animation: ft-shimmer 1.4s ease-in-out infinite;
}
@keyframes ft-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}
</style>
