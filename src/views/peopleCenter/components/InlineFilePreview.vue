<!-- eslint-disable vue/no-v-html -- mdHtml 已经 markdown-it(html:false)+xss 清洗 -->
<template>
  <div v-if="mode && !failed" :class="['ifp', { 'ifp--standalone': standalone }]">
    <div v-if="!ready && !standalone" class="ifp-skeleton">
      <span class="ifp-bar" style="width: 42%" aria-hidden="true"></span>
      <span class="ifp-bar" style="width: 92%" aria-hidden="true"></span>
      <span class="ifp-bar" style="width: 64%" aria-hidden="true"></span>
      <p v-if="slow" class="ifp-slow">正在生成预览，首次转换可能需要几十秒，可先继续对话…</p>
    </div>
    <div v-else-if="!standalone" class="ifp-media">
      <div
        v-if="mode === 'html'"
        class="ifp-html"
        :class="{ 'ifp-html--report': isResearchHtml }"
        role="button"
        tabindex="0"
        title="点击全屏预览"
        @click="viewerOpen = true"
        @keydown.enter="viewerOpen = true"
      >
        <!-- 只读缩略预览（pointer-events:none，点击整块进全屏查看器）：不需要脚本，
             故 sandbox=""（禁脚本/表单/同源/弹窗）——text 是未消毒的产物/上传 HTML，
             禁脚本即令注入的 <script>/<svg onload>/javascript: 全部失活；样式与图片照常渲染。
             要「跑」交互式 HTML 产物走全屏 DocPagesViewer 网页模式（那里才开脚本、且仍为无源沙箱）。
             切勿改回 allow-scripts：会让未消毒产物在划入视口时自动执行脚本。 -->
        <iframe class="ifp-frame" :srcdoc="text" sandbox="" title="HTML 产物预览"></iframe>
      </div>
      <div
        v-else-if="mode === 'pdf' || mode === 'office'"
        class="ifp-doc"
        role="button"
        tabindex="0"
        title="点击全屏预览"
        @click="viewerOpen = true"
        @keydown.enter="viewerOpen = true"
      >
        <canvas ref="coverCanvas" class="ifp-cover"></canvas>
        <span v-if="pageCount > 1" class="ifp-pagecount">{{ pageCount }} 页</span>
      </div>
      <div
        v-else-if="mode === 'xlsx'"
        class="ifp-xlsx"
        role="button"
        tabindex="0"
        title="点击查看完整工作簿"
        @click="viewerOpen = true"
        @keydown.enter="viewerOpen = true"
      >
        <span class="ifp-xlsx-icon"><FileExcelOutlined /></span>
        <span class="ifp-xlsx-copy">
          <strong>Excel 工作簿</strong>
          <em>点击查看完整工作表</em>
        </span>
      </div>
      <!-- 活页 pptx（带 .slides.json 编辑源）：首页直接用页面 HTML 画，免 LibreOffice 转换等待，
           且永远和手改后的内容一致；点击进查看器＝可编辑画布 -->
      <div
        v-else-if="mode === 'slides'"
        ref="slidesBox"
        class="ifp-doc ifp-slides"
        role="button"
        tabindex="0"
        title="点击全屏预览并编辑"
        @click="viewerOpen = true"
        @keydown.enter="viewerOpen = true"
      >
        <iframe
          class="ifp-slide-frame"
          sandbox=""
          tabindex="-1"
          :srcdoc="coverSrc"
          :style="{ transform: `scale(${coverScale})` }"
          title="首页预览"
        ></iframe>
        <span v-if="pageCount > 1" class="ifp-pagecount">{{ pageCount }} 页</span>
      </div>
      <div
        v-else-if="mode === 'markdown'"
        class="ifp-md"
        role="button"
        tabindex="0"
        title="点击全屏阅读"
        @click="viewerOpen = true"
        @keydown.enter="viewerOpen = true"
      >
        <div class="ifp-md-body" v-html="mdHtml"></div>
      </div>
      <img v-else-if="mode === 'image'" class="ifp-img" :src="blobUrl" :alt="file.filename" />
      <div v-else-if="mode === 'table'" class="ifp-table-wrap">
        <table class="ifp-table">
          <thead v-if="tableRows.length">
            <tr>
              <th v-for="(cell, i) in tableRows[0]" :key="i">{{ cell }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, r) in tableRows.slice(1)" :key="r">
              <td v-for="(cell, c) in row" :key="c">{{ cell }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="tableMore > 0" class="ifp-more">… 还有 {{ tableMore }} 行</p>
      </div>
      <pre v-else-if="mode === 'text'" class="ifp-text">{{ text }}</pre>
    </div>
    <DocPagesViewer
      v-if="viewerOpen && (pdfDoc || mdHtml || slidesPages.length || (mode === 'xlsx' && blobUrl) || (mode === 'html' && text))"
      :doc="pdfDoc || undefined"
      :filename="file.filename"
      :created-at="file.createdAt"
      :pdf-url="mode === 'xlsx' ? undefined : blobUrl || undefined"
      :article-html="mdHtml || undefined"
      :slides-pages="slidesPages"
      :html-src="mode === 'html' ? text : undefined"
      :saving="savingPages"
      :allow-ai-edit="mode !== 'xlsx' && (mode !== 'html' || !isResearchHtml)"
      @close="onViewerClose"
      @download="emit('download')"
      @ai-edit="(p) => { viewerOpen = false; emit('aiEdit', p); }"
      @save-pages="onViewerSavePages"
    >
      <ExcelFileViewer
        v-if="mode === 'xlsx' && blobUrl"
        :src="blobUrl"
        @error="onExcelViewerError"
      />
    </DocPagesViewer>
  </div>
</template>

<script setup lang="ts">
/**
 * 对话流内的产物内联预览（2026-07-20 产物展示升级，对标 Manus「产物在对话里直接可见」）：
 * html → 沙箱 iframe 实时渲染（点击进 DocPagesViewer 网页模式全屏预览）；图片 → 原图；
 * pdf / doc / docx / ppt / pptx → pdf.js 把首页画在白底 canvas 上（不用浏览器原生 PDF
 * 查看器——深灰工具栏+黑底衬边和白卡风格打架），点击进 DocPagesViewer 全屏查看器
 * （office 类先经后端 LibreOffice /preview 端点转 PDF，按 file_id 缓存，首次可能几秒~30s）；
 * csv → 前几行表格；文本/代码 → 开头节选。xlsx/xlsm 与主 Agent「我的文件」一致，
 * 直接交给原始工作簿查看器；Word/PPT 才经 LibreOffice 转为 PDF。压缩包等仍无行内预览。
 * 内容就绪前显示骨架条（超时补等待提示），就绪后淡入；加载失败静默收起，不打扰主流程。
 */
import { FileExcelOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { GeneratedFile } from '../agentApi';
import { fetchUserFileBlobUrl, fetchUserFilePreviewPdfUrl, fetchUserFileText } from '../myfiles.api';
import { destroyPdfDoc, loadPdfjs } from '../utils/pdfDoc';
import { renderMarkdownDoc } from '../utils/mdRender';
import { slideFrameSrc } from '../utils/slideEditKit';
import { isResearchReportHtml } from '../utils/researchReport';
import DocPagesViewer from './DocPagesViewer.vue';
import ExcelFileViewer from './ExcelFileViewer.vue';

const props = defineProps<{
  file: GeneratedFile;
  /** pptx 的同名 .slides.json 编辑源（存在＝这份产物可在查看器里直接手改） */
  slidesFile?: { id: string; filename: string } | null;
  /** 用户消息附件点开：不渲染行内封面，加载完直接进全屏查看器 */
  standalone?: boolean;
}>();
const emit = defineEmits<{
  (e: 'download'): void;
  (e: 'aiEdit', payload: { instruction: string; scope: 'page' | 'all'; page: number }): void;
  (e: 'savePages', pages: string[], done: (ok: boolean) => void): void;
  (e: 'close'): void;
  /** reason：加载失败的可读原因（后端 detail 或本地判定），宿主要展示给用户、不得静默 */
  (e: 'failed', reason?: string): void;
  (e: 'ready'): void;
}>();

function onViewerClose() {
  viewerOpen.value = false;
  if (props.standalone) emit('close');
}

type OpenViewerResult = 'opened' | 'pending' | 'unavailable';

/**
 * 供交付卡的“预览”按钮复用同一个全屏查看器。
 *
 * 子智能体运行页会在 Office 文件的 PDF 转换尚未完成时就显示交付卡。
 * 早到的点击不能静默丢掉：记下请求，等权威预览内容就绪后自动打开。
 */
const viewerRequested = ref(false);

function hasViewerContent(): boolean {
  return Boolean(
    pdfDoc.value
    || mdHtml.value
    || slidesPages.value.length
    || (mode.value === 'xlsx' && blobUrl.value)
    || (mode.value === 'html' && text.value),
  );
}

function openViewer(): OpenViewerResult {
  if (!mode.value || failed.value) return 'unavailable';
  if (!ready.value) {
    viewerRequested.value = true;
    return 'pending';
  }
  if (!hasViewerContent()) return 'unavailable';
  viewerRequested.value = false;
  viewerOpen.value = true;
  return 'opened';
}

/** 保存在途：查看器顶栏进「保存中…」，✕/Esc 期间不可关（见 DocPagesViewer 的 saving prop）。 */
const savingPages = ref(false);

/** P0（2026-07-26 深扫）：pages 是用户手改内容的唯一持有者，必须等宿主落库成功再关查看器——
 *  失败时保持打开（DocPagesViewer 的 editedPages 还在），用户可直接再点「保存修改」重试。
 *  viewerDone 要把结果回传给 DocPagesViewer 清脏（保存成功后 ✕ 不应再弹放弃确认）。 */
function onViewerSavePages(pages: string[], viewerDone?: (ok: boolean) => void) {
  if (savingPages.value) {
    viewerDone?.(false);
    return;
  }
  savingPages.value = true;
  let settled = false; // 宿主若重复回调，只认第一次
  emit('savePages', pages, (ok) => {
    if (settled) return;
    settled = true;
    savingPages.value = false;
    viewerDone?.(ok);
    if (ok) viewerOpen.value = false;
  });
}

type Mode = 'html' | 'pdf' | 'office' | 'slides' | 'markdown' | 'image' | 'table' | 'text' | 'xlsx';

const TEXT_EXTS = new Set([
  'txt', 'json', 'log', 'xml', 'yaml', 'yml',
  'py', 'js', 'ts', 'css', 'sh', 'sql', 'vue', 'jsx', 'tsx',
]);
const IMAGE_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  svg: 'image/svg+xml',
};
const MAX_TEXT_BYTES = 1.5 * 1024 * 1024;
// New HTML artifacts inline local photos as data URLs so srcdoc preview and downloaded files
// share the same bytes. Base64 expands an image by roughly one third; use the backend's 15MB
// persisted-file ceiling for HTML instead of the small source-code preview ceiling.
const MAX_HTML_BYTES = 15 * 1024 * 1024;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_PDF_BYTES = 20 * 1024 * 1024;
const TEXT_EXCERPT_LINES = 14;
const TEXT_EXCERPT_CHARS = 1600;
const TABLE_ROWS = 6;

function fileExt(): string {
  return (props.file.filename.split('.').pop() || '').toLowerCase();
}

const mode = computed<Mode | null>(() => {
  const ext = fileExt();
  const mime = props.file.mime || '';
  const size = props.file.size || 0;
  if (ext === 'html' || ext === 'htm' || mime === 'text/html') {
    return size <= MAX_HTML_BYTES ? 'html' : null;
  }
  if (mime.startsWith('image/') || IMAGE_MIME[ext]) {
    return size <= MAX_IMAGE_BYTES ? 'image' : null;
  }
  if (ext === 'pdf' || mime === 'application/pdf') {
    return size <= MAX_PDF_BYTES ? 'pdf' : null;
  }
  if (ext === 'xlsx' || ext === 'xlsm') return 'xlsx';
  if (['doc', 'docx', 'ppt', 'pptx', 'xls'].includes(ext)) {
    if (props.slidesFile && (ext === 'ppt' || ext === 'pptx')) return 'slides';
    return size <= MAX_PDF_BYTES ? 'office' : null;
  }
  if (ext === 'md' || ext === 'markdown') {
    return size <= MAX_TEXT_BYTES ? 'markdown' : null;
  }
  if (ext === 'csv') return size <= MAX_TEXT_BYTES ? 'table' : null;
  if (TEXT_EXTS.has(ext) || mime.startsWith('text/')) {
    return size <= MAX_TEXT_BYTES ? 'text' : null;
  }
  return null;
});

const ready = ref(false);
const failed = ref(false);
/** 加载超过阈值仍未就绪（典型是 office 首次 LibreOffice 转换）→ 骨架下补一行等待提示 */
const slow = ref(false);
const SLOW_HINT_MS = 5000;
let slowTimer = 0;
const text = ref('');
const isResearchHtml = computed(() => isResearchReportHtml(text.value));
const blobUrl = ref('');
const tableRows = ref<string[][]>([]);
const tableMore = ref(0);
const pdfDoc = shallowRef<PDFDocumentProxy | null>(null);
const mdHtml = ref('');
const pageCount = ref(0);
const coverCanvas = ref<HTMLCanvasElement | null>(null);
const viewerOpen = ref(false);
watch(ready, (isReady) => {
  if (!isReady || !viewerRequested.value) return;
  viewerRequested.value = false;
  if (hasViewerContent()) viewerOpen.value = true;
});
/** 组件已卸载（切会话/重新生成时消息流整片重建，请求多半还挂着）：卸载钩子只清理**当时
 *  已经写进 ref 的**资源，之后才 resolve 的 blob/pdf 若直接写进作废实例就永远没人释放。
 *  所有拿到资源的分支必须先看这个旗标，已卸载就地回收。 */
let disposed = false;

defineExpose({ openViewer });

// ---- 活页 pptx（slides.json 编辑源）----
const slidesPages = ref<string[]>([]);
const slidesBox = ref<HTMLElement | null>(null);
const coverScale = ref(0);
let coverRo: ResizeObserver | null = null;

/** 首页只读 srcdoc：1280×720 原尺寸内容，缩放交给外层壳（沙箱帧内量不到父页宽） */
const coverSrc = computed(() => slideFrameSrc(slidesPages.value[0] || ''));

/** 最小 CSV 解析（带引号转义），只为取前几行做预览，不做完整方言支持 */
function parseCsvHead(raw: string): { rows: string[][]; total: number } {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;
  let total = 0;
  const pushCell = () => { row.push(cell); cell = ''; };
  const pushRow = () => {
    pushCell();
    if (row.length > 1 || (row[0] || '').trim() !== '') {
      total += 1;
      if (rows.length < TABLE_ROWS) rows.push(row.map((c) => (c.length > 40 ? c.slice(0, 40) + '…' : c)));
    }
    row = [];
  };
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (inQuotes) {
      if (ch === '"') {
        if (raw[i + 1] === '"') { cell += '"'; i++; } else inQuotes = false;
      } else cell += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ',') pushCell();
    else if (ch === '\n') pushRow();
    else if (ch !== '\r') cell += ch;
  }
  if (cell !== '' || row.length) pushRow();
  return { rows, total };
}

function excerptOf(raw: string): string {
  const lines = raw.split('\n').slice(0, TEXT_EXCERPT_LINES).join('\n');
  return lines.length > TEXT_EXCERPT_CHARS ? lines.slice(0, TEXT_EXCERPT_CHARS) + '…' : lines;
}

async function renderCover(doc: PDFDocumentProxy) {
  const canvas = coverCanvas.value;
  if (!canvas) return;
  const page = await doc.getPage(1);
  const base = page.getViewport({ scale: 1 });
  const cssWidth = canvas.parentElement?.clientWidth || 560;
  const dpr = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: (cssWidth / base.width) * dpr });
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  await page.render({ canvasContext: ctx, viewport, canvas }).promise;
}

/** 首页缩放 = 卡片实际宽 / 1280；卡片宽随窗口变，用 ResizeObserver 跟随 */
function measureCover() {
  const box = slidesBox.value;
  if (!box) return;
  coverScale.value = box.clientWidth / 1280;
  if (!coverRo && typeof ResizeObserver !== 'undefined') {
    coverRo = new ResizeObserver(() => {
      if (slidesBox.value) coverScale.value = slidesBox.value.clientWidth / 1280;
    });
    coverRo.observe(box);
  }
}

function openStandaloneViewer() {
  emit('ready');
  if (props.standalone) viewerOpen.value = true;
}

function onExcelViewerError() {
  viewerOpen.value = false;
  message.error('该文件在线渲染失败，请下载后本地查看');
}

onMounted(async () => {
  const m = mode.value;
  if (!m) {
    // 没有匹配到渲染器：说清是格式不支持还是文件过大，宿主 toast 原样展示
    if (props.standalone) {
      const ext = fileExt();
      emit('failed', ext
        ? `.${ext} 格式暂不支持在线预览，或文件过大，请下载后本地查看`
        : '该文件暂不支持在线预览，请下载后本地查看');
    }
    return;
  }
  slowTimer = window.setTimeout(() => {
    if (!ready.value && !failed.value) slow.value = true;
  }, SLOW_HINT_MS);
  try {
    if (m === 'html') {
      text.value = await fetchUserFileText(props.file.id);
    } else if (m === 'markdown') {
      mdHtml.value = renderMarkdownDoc(await fetchUserFileText(props.file.id));
    } else if (m === 'text') {
      text.value = excerptOf(await fetchUserFileText(props.file.id));
    } else if (m === 'table') {
      const parsed = parseCsvHead(await fetchUserFileText(props.file.id));
      tableRows.value = parsed.rows;
      tableMore.value = Math.max(0, parsed.total - parsed.rows.length);
    } else if (m === 'image') {
      const mime = (props.file.mime || '').startsWith('image/')
        ? props.file.mime
        : IMAGE_MIME[fileExt()];
      const url = await fetchUserFileBlobUrl(props.file.id, mime);
      if (disposed) { URL.revokeObjectURL(url); return; }
      blobUrl.value = url;
    } else if (m === 'xlsx') {
      const mime = fileExt() === 'xlsm'
        ? 'application/vnd.ms-excel.sheet.macroEnabled.12'
        : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
      const url = await fetchUserFileBlobUrl(props.file.id, props.file.mime || mime);
      if (disposed) { URL.revokeObjectURL(url); return; }
      blobUrl.value = url;
    } else if (m === 'slides') {
      const arr = JSON.parse(await fetchUserFileText(props.slidesFile!.id));
      if (!Array.isArray(arr) || !arr.length) throw new Error('编辑源为空');
      if (disposed) return;
      slidesPages.value = arr.map((x: unknown) => String(x));
      pageCount.value = slidesPages.value.length;
      ready.value = true;
      openStandaloneViewer();
      if (props.standalone) return;
      await nextTick();
      if (disposed) return;
      measureCover();
      // 「下载 → PDF」用的转换在后台慢慢跑，不挡首页展示；失败就只留原文件下载
      fetchUserFilePreviewPdfUrl(props.file.id)
        .then((url) => {
          // 后台转换通常比组件活得久：卸载后到货的 blob 必须就地释放
          if (disposed) { URL.revokeObjectURL(url); return; }
          blobUrl.value = url;
        })
        .catch(() => undefined);
      return;
    } else if (m === 'pdf' || m === 'office') {
      const url =
        m === 'pdf'
          ? await fetchUserFileBlobUrl(props.file.id, 'application/pdf')
          : await fetchUserFilePreviewPdfUrl(props.file.id);
      if (disposed) { URL.revokeObjectURL(url); return; }
      blobUrl.value = url;
      const pdfjs = await loadPdfjs();
      const doc = await pdfjs.getDocument({ url }).promise;
      if (disposed) {
        URL.revokeObjectURL(url); // 卸载钩子可能早于本次赋值跑完，重复 revoke 无副作用
        destroyPdfDoc(doc); // 先 revoke 再销毁：blob 释放绝不能被销毁路径的异常挡住
        return;
      }
      pdfDoc.value = doc;
      pageCount.value = doc.numPages;
      ready.value = true;
      openStandaloneViewer();
      if (props.standalone) return;
      await nextTick();
      if (disposed) return;
      await renderCover(doc);
      return;
    }
    if (disposed) return;
    ready.value = true;
    openStandaloneViewer();
  } catch (e) {
    if (!disposed) {
      failed.value = true;
      viewerRequested.value = false;
      // 把后端 detail（如「.xyz 格式暂不支持在线预览」）带给宿主，而不是只报一句「预览失败」
      emit('failed', e instanceof Error ? e.message : '');
    }
  }
});

onBeforeUnmount(() => {
  disposed = true;
  if (slowTimer) window.clearTimeout(slowTimer);
  coverRo?.disconnect();
  if (blobUrl.value) URL.revokeObjectURL(blobUrl.value);
  destroyPdfDoc(pdfDoc.value);
});
</script>

<style scoped>
.ifp {
  grid-column: 1 / -1;
  min-width: 0;
  margin-top: 2px;
  border-top: 1px solid #f0f1f4;
  padding-top: 10px;
}

.ifp--standalone {
  grid-column: auto;
  margin: 0;
  border: 0;
  padding: 0;
}

.ifp-skeleton {
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 4px 2px 6px;
}

.ifp-bar {
  height: 10px;
  border-radius: 5px;
  background: #f0f1f4;
  animation: ifp-shimmer 1.4s ease-in-out infinite;
}

@keyframes ifp-shimmer {
  0%,
  100% {
    opacity: 0.55;
  }
  50% {
    opacity: 1;
  }
}

.ifp-slow {
  margin: 2px 2px 0;
  font-size: 12px;
  color: #9aa0ab;
}

.ifp-media {
  animation: ifp-in 0.3s ease both;
}

@keyframes ifp-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* html 预览：iframe 只当画面用（pointer-events 关掉，滚轮不被吃），整块点击进大预览 */
.ifp-html {
  position: relative;
  height: 220px;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid #eceef2;
  cursor: zoom-in;
  background: #fff;
}

.ifp-html .ifp-frame {
  pointer-events: none;
}

.ifp-html--report {
  height: 420px;
  border: 0;
}

.ifp-html--report .ifp-frame {
  height: 560px;
}

.ifp-frame {
  display: block;
  width: 100%;
  height: 220px;
  border: 0;
  background: #fff;
}

/* markdown 预览：渲染后的排版节选（限高+底部渐隐），点击进展览区文章模式 */
.ifp-md {
  position: relative;
  max-height: 280px;
  overflow: hidden;
  border: 1px solid #eceef2;
  border-radius: 8px;
  background: #fff;
  cursor: zoom-in;
  padding: 14px 18px 4px;
}

.ifp-md::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 44px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), #fff);
  pointer-events: none;
}

.ifp-md-body {
  font-size: 13.5px;
  line-height: 1.75;
  color: #374151;
  word-break: break-word;
}

.ifp-md-body :deep(h1),
.ifp-md-body :deep(h2),
.ifp-md-body :deep(h3) {
  font-size: 15px;
  margin: 10px 0 6px;
}

.ifp-md-body :deep(h1:first-child) {
  margin-top: 0;
}

.ifp-md-body :deep(p) {
  margin: 0 0 8px;
}

.ifp-md-body :deep(pre) {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 8px 10px;
  overflow-x: hidden;
  font-size: 12px;
}

/* pdf/office 预览：pdf.js 画布首页（白纸面，无查看器铬件），角标页数，点击全屏 */
.ifp-doc {
  position: relative;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid #eceef2;
  background: #fff;
  cursor: zoom-in;
  line-height: 0;
  max-height: 340px;
}

.ifp-cover {
  display: block;
  width: 100%;
  height: auto;
}

/* Excel 与主 Agent「我的文件」一致：全屏读取原始工作簿；卡内只给轻量入口。 */
.ifp-xlsx {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 58px;
  padding: 10px 12px;
  border: 1px solid #e7ebe8;
  border-radius: 8px;
  background: #fbfdfb;
  cursor: zoom-in;
}

.ifp-xlsx:hover {
  border-color: #ccd9d0;
  background: #f7faf8;
}

.ifp-xlsx:focus-visible {
  outline: 2px solid rgba(47, 138, 91, 0.28);
  outline-offset: 2px;
}

.ifp-xlsx-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: none;
  border-radius: 8px;
  background: #eaf5ee;
  color: #2f8a5b;
  font-size: 17px;
}

.ifp-xlsx-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  line-height: 1.4;
}

.ifp-xlsx-copy strong {
  color: #2d3430;
  font-size: 13px;
  font-weight: 600;
}

.ifp-xlsx-copy em {
  color: #8a938d;
  font-size: 12px;
  font-style: normal;
}

/* 活页首页：外层壳按 16:9 定高，内层 iframe 保持 1280×720 原尺寸再整体缩放 */
.ifp-slides {
  aspect-ratio: 16 / 9;
  max-height: none;
}

.ifp-slide-frame {
  display: block;
  width: 1280px;
  height: 720px;
  border: 0;
  background: #fff;
  transform-origin: 0 0;
  pointer-events: none;
}

.ifp-pagecount {
  position: absolute;
  right: 10px;
  bottom: 10px;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(35, 39, 46, 0.72);
  color: #fff;
  font-size: 11.5px;
  line-height: 1.6;
}

.ifp-img {
  display: block;
  max-width: 100%;
  max-height: 260px;
  border-radius: 8px;
  border: 1px solid #eceef2;
  object-fit: contain;
}

.ifp-table-wrap {
  overflow-x: auto;
}

.ifp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
  font-variant-numeric: tabular-nums;
}

.ifp-table th {
  font-weight: 500;
  color: #374151;
  background: #f7f8fa;
  border-bottom: 1px solid #e9eaee;
  padding: 6px 10px;
  text-align: left;
  white-space: nowrap;
}

.ifp-table td {
  color: #4b5563;
  border-bottom: 1px solid #f0f1f4;
  padding: 6px 10px;
  white-space: nowrap;
}

.ifp-more {
  margin: 6px 2px 0;
  font-size: 12px;
  color: #9aa0ab;
}

.ifp-text {
  margin: 0;
  max-height: 200px;
  overflow: hidden;
  font-size: 12.5px;
  line-height: 1.7;
  color: #4b5563;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (prefers-reduced-motion: reduce) {
  .ifp-bar {
    animation: none;
  }

  .ifp-media {
    animation: none;
  }
}
</style>
