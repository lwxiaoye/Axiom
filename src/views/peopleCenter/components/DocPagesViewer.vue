<template>
  <Teleport to="body">
    <div ref="rootRef" class="dpv-overlay" role="dialog" aria-modal="true" :aria-label="`${filename} 预览`">
      <header v-show="!playing" class="dpv-head">
        <span :class="['dpv-file-icon', sourceKindClass]"
          ><FileTextOutlined v-if="isReport" /><Html5Outlined v-else-if="isWeb" /><FileTextOutlined v-else-if="isCode" /><component :is="customIcon" v-else-if="isCustom" /><FileExcelOutlined v-else-if="isExcel" /><FileWordOutlined v-else-if="isWord" /><FilePptOutlined v-else-if="isPpt" /><FilePdfOutlined v-else
        /></span>
        <div class="dpv-title-wrap">
          <strong class="dpv-title" :title="displayTitle">{{ displayTitle }}</strong>
          <em v-if="metaLine" class="dpv-meta">{{ metaLine }}</em>
        </div>
        <div class="dpv-actions">
          <!-- 网页模式：预览/源码切换（沿用旧右侧产物面板的能力，形态并入查看器顶栏） -->
          <div v-if="isWeb && !isReport" class="dpv-tabs">
            <button type="button" :class="{ active: webView === 'preview' }" @click="webView = 'preview'">预览</button>
            <button type="button" :class="{ active: webView === 'code' }" @click="webView = 'code'">源码</button>
          </div>
          <span v-if="closeHint" class="dpv-hint" role="status">{{ closeHint }}</span>
          <button
            v-if="isLive && liveDirty"
            type="button"
            class="dpv-btn dpv-save"
            :class="{ busy: saving }"
            :disabled="saving"
            @click="onSavePages"
          >
            {{ saving ? '保存中…' : '保存修改' }}
          </button>
          <ResearchReportExportMenu v-if="isReport" :html="htmlSrc || ''" :filename="filename">
            <button type="button" class="dpv-btn">下载</button>
          </ResearchReportExportMenu>
          <div v-else class="dpv-menu-host">
            <button type="button" class="dpv-btn" @click.stop="toggleMenu('download')">下载</button>
            <div v-if="openMenu === 'download'" class="dpv-menu" @click.stop>
              <button type="button" class="dpv-menu-item" @click="onDownloadOriginal">
                <FileOutlined /> {{ originalFormatLabel }}（原文件）
              </button>
              <button v-if="pdfUrl && !isOriginalPdf" type="button" class="dpv-menu-item" @click="onDownloadPdf()">
                <FilePdfOutlined /> PDF
              </button>
            </div>
          </div>
          <div v-if="isPaged" class="dpv-menu-host">
            <button type="button" class="dpv-btn" aria-label="放映" @click.stop="toggleMenu('play')">
              <PlayCircleOutlined />
            </button>
            <div v-if="openMenu === 'play'" class="dpv-menu" @click.stop>
              <button type="button" class="dpv-menu-item" @click="enterPlay(true)">
                <PlayCircleOutlined /> 从头开始放映
              </button>
              <button type="button" class="dpv-menu-item" @click="enterPlay(false)">
                <RightCircleOutlined /> 从当前页开始放映
              </button>
            </div>
          </div>
          <button type="button" class="dpv-btn dpv-close" aria-label="关闭预览" @click="requestClose()">
            <CloseOutlined />
          </button>
        </div>
      </header>
      <div v-show="!playing" class="dpv-body">
        <!-- 网页模式（html 产物/网页文件）：整面沙箱 iframe，顶栏可切源码。
             这是用户显式打开、要「跑起来看」的交互式网页产物，故保留 allow-scripts。
             htmlSrc 未消毒，但沙箱**不含 allow-same-origin**＝无源沙箱：帧内脚本处于隔离
             opaque origin，读不到主站 localStorage 的会话 token、发不出带凭证的同源请求，
             故非账号接管级（这正是安全渲染未信任 HTML 的标准隔离）。去掉了 allow-popups——
             它是唯一残留的实际风险（脚本 window.open 拉钓鱼窗）。
             绝对不要加 allow-same-origin：一旦同源，帧内脚本即可窃取会话 token（见 slideEditKit
             的实时编辑帧，那里同源＝必须先过 sanitizeSlideHtml 消毒才敢开脚本）。 -->
        <main v-if="isReport" class="dpv-report-wrap">
          <aside v-if="reportToc.length > 1" class="dpv-report-toc" aria-label="大纲">
            <strong>大纲</strong>
            <button
              v-for="item in reportToc"
              :key="item.id"
              type="button"
              :class="{ active: reportActivePage === item.index }"
              @click="scrollReportPage(item.index)"
            >{{ item.title }}</button>
          </aside>
          <div ref="reportStageRef" class="dpv-report-stage" @scroll.passive="onReportScroll">
            <!-- eslint-disable vue/no-v-html -- 研究报告由平台编译，正文已 html.escape -->
            <article
              ref="reportArticleRef"
              class="dpv-report-sheet dpv-report-article"
              v-html="reportArticleHtml"
            ></article>
            <!-- eslint-enable vue/no-v-html -->
          </div>
        </main>
        <main v-else-if="isWeb" class="dpv-web-wrap">
          <iframe
            v-if="webView === 'preview'"
            class="dpv-web-frame"
            sandbox="allow-scripts"
            :srcdoc="htmlSrc"
            :title="`${filename} 预览`"
          ></iframe>
          <div v-else class="dpv-code-scroll"><pre class="dpv-code">{{ htmlSrc }}</pre></div>
        </main>
        <!-- 源码模式（文本/代码文件预览）：浅灰舞台上一张等宽字白纸 -->
        <main v-else-if="isCode" class="dpv-code-scroll">
          <pre class="dpv-code">{{ codeText }}</pre>
        </main>
        <!-- 文章模式（md）：无分页概念，居中排版文章列替代缩略图轨+画布 -->
        <main v-else-if="isArticle" class="dpv-article-wrap">
          <!-- eslint-disable-next-line vue/no-v-html -- articleHtml 已经 markdown-it(html:false)+xss 清洗 -->
          <article class="dpv-article" v-html="articleHtml"></article>
        </main>
        <!-- 通用内容槽（2026-07-23 预览全量统一）：宿主自带渲染器（vue-office Excel/图片/
             渲染兜底等）装进同一个查看器壳，顶栏/下载/关闭/Esc 行为一致 -->
        <main v-else-if="isCustom" class="dpv-custom-wrap">
          <slot></slot>
        </main>
        <aside v-if="isPaged" class="dpv-rail" aria-label="页面缩略图">
          <button
            v-for="n in pageCount"
            :key="n"
            type="button"
            :class="['dpv-thumb', { active: n === activePage }]"
            :aria-label="`第 ${n} 页`"
            :aria-current="n === activePage ? 'page' : undefined"
            :draggable="isLive"
            @click="goPage(n)"
            @dragstart="onThumbDragStart(n)"
            @dragover.prevent
            @drop.prevent="onThumbDrop(n)"
          >
            <canvas v-if="!isLive" :ref="(el) => setThumbRef(el, n)"></canvas>
            <!-- 缩略图缩放放在外层壳（父页 CSS，精确 = 壳宽/1280），iframe 用原始 1280×720
                 尺寸——sandbox="" 完全隔离，父页量不到内部宽，绝不能把缩放放帧内靠假设宽度 -->
            <div v-else class="dpv-thumb-shell">
              <iframe
                class="dpv-thumb-frame"
                sandbox=""
                tabindex="-1"
                :srcdoc="pageSrc(n)"
                :title="`第 ${n} 页缩略图`"
              ></iframe>
            </div>
            <span class="dpv-thumb-no">{{ n }}</span>
          </button>
        </aside>
        <main
          v-if="isPaged"
          ref="stageRef"
          class="dpv-stage"
          :tabindex="isLive ? undefined : 0"
          @scroll.passive="onStageScroll"
        >
          <!-- 活页编辑模式（2026-07-21「进入即编辑」）：主画布=可编辑活页,界面其余保持原样 -->
          <div v-if="isLive" class="dpv-live-box">
            <iframe
              ref="liveFrame"
              class="dpv-live-frame"
              sandbox="allow-same-origin allow-scripts"
              :srcdoc="liveHtml"
              title="幻灯片编辑画布"
              @load="onLiveLoad"
            ></iframe>
          </div>
          <!-- 只读分页模式（2026-07-28）：整册纵向堆叠连续滚动，不再一次只画一页。
               每页先按真实长宽比占好位（避免渲染时抖动/滚动条跳），进入视口附近才真渲染。 -->
          <div
            v-for="n in pageCount"
            v-else
            :key="n"
            :ref="(el) => setPageBoxRef(el, n)"
            class="dpv-page"
            :data-page="n"
            :style="pageBoxStyle(n)"
          >
            <canvas :ref="(el) => setPageCanvasRef(el, n)" class="dpv-main-canvas"></canvas>
          </div>
        </main>
      </div>
      <!-- AI 编辑（2026-07-20 对标 Manus 魔棒面板）：自由指令+作用范围+快捷动作，
           提交后关闭查看器、把编辑指令交回主对话在原文件上精修（复用既有文件修改链路） -->
      <template v-if="allowAiEdit && !playing && !isReport">
        <button
          type="button"
          class="dpv-edit-fab"
          aria-label="AI 编辑"
          title="AI 编辑（手改直接在画布上进行）"
          :aria-expanded="editOpen"
          @click.stop="editOpen = !editOpen"
        >
          <ThunderboltOutlined />
        </button>
        <div v-if="editOpen" class="dpv-edit-panel" @click.stop>
          <p class="dpv-edit-title">AI 编辑</p>
          <textarea
            v-model="editText"
            class="dpv-edit-input"
            rows="3"
            :placeholder="isWhole ? '描述要修改的内容…' : '描述当前页或整份文档要怎么改…'"
            @keydown.enter.exact.prevent="submitEdit()"
          ></textarea>
          <div class="dpv-edit-row">
            <select v-if="!isWhole" v-model="editScope" class="dpv-edit-scope" aria-label="作用范围">
              <option value="page">当前第 {{ activePage }} 页</option>
              <option value="all">整份文档</option>
            </select>
            <button
              type="button"
              class="dpv-btn dpv-edit-send"
              :disabled="!editText.trim()"
              @click="submitEdit()"
            >
              让 AI 修改
            </button>
          </div>
          <p class="dpv-edit-sub">快捷操作</p>
          <div class="dpv-edit-chips">
            <button v-for="c in quickActions" :key="c" type="button" class="dpv-chip" @click="submitEdit(c)">
              {{ c }}
            </button>
          </div>
        </div>
      </template>
      <!-- 放映模式（2026-07-20 对标 Manus）：黑场全屏，点击/方向键翻页，Esc 或退出全屏结束。
           活页模式没有 PDF 可画，直接放映当页 HTML（含手改后的最新内容），按视口等比缩放。 -->
      <div v-if="playing" class="dpv-play" @click="playNext">
        <div
          v-if="isLive"
          class="dpv-play-live"
          :style="{ transform: `translate(-50%, -50%) scale(${playScale})` }"
        >
          <iframe class="dpv-play-frame" sandbox="" :srcdoc="pageSrc(playPage)" title="放映画面"></iframe>
        </div>
        <canvas v-else ref="playCanvas" class="dpv-play-canvas"></canvas>
        <span class="dpv-play-counter">{{ playPage }} / {{ pageCount }}</span>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * 文档全屏查看器（2026-07-20 产物展示升级，对标 Manus 幻灯片工作区）：
 * 左缩略图轨（当前页高亮）+ 大画布 + 顶栏（文件名/时间/下载菜单/放映/关闭）。
 * 页面由 pdf.js 画到 canvas 上——没有浏览器 PDF 查看器的深灰工具栏，纯白纸面。
 * 下载菜单：原文件 + 转换好的 PDF（office 类）；放映：浏览器全屏黑场逐页放映。
 * 键盘：Esc 关闭 / 退出放映，←→/↑↓ 翻页。打开期间锁定 body 滚动。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useSlots, watch } from 'vue';
import {
  CloseOutlined,
  FileExcelOutlined,
  FileImageOutlined,
  FileOutlined,
  FilePdfOutlined,
  FilePptOutlined,
  FileTextOutlined,
  FileWordOutlined,
  Html5Outlined,
  PlayCircleOutlined,
  RightCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons-vue';
import { Modal } from 'ant-design-vue';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import ResearchReportExportMenu from './ResearchReportExportMenu.vue';
import {
  attachSlideEditKit,
  sanitizeSlideHtml,
  slideFrameSrc,
  type SlideEditKitHandle,
} from '../utils/slideEditKit';
import { parseResearchReport } from '../utils/researchReport';

const props = defineProps<{
  /** 分页文档（pdf/office 转 pdf）；文章模式（markdown）不传 */
  doc?: PDFDocumentProxy;
  filename: string;
  createdAt?: string | null;
  /** 转换后的 PDF blob 地址（office 类原文件≠pdf 时提供「下载 PDF」入口） */
  pdfUrl?: string;
  /** 文章模式（2026-07-20 展览区支持 md）：已渲染并清洗过的 HTML，替代分页画布 */
  articleHtml?: string;
  /** AI 编辑入口（对标 Manus 魔棒）：仅主对话宿主开启——编辑指令要回灌对话精修 */
  allowAiEdit?: boolean;
  /** 活页编辑模式（2026-07-21「进入即编辑」）：传入 slides.json 的每页自包含 HTML，
   *  主画布直接渲染可编辑活页（选中/拖拽/调宽/复制删除/双击改字），改动后顶栏出「保存修改」 */
  slidesPages?: string[];
  /** 网页模式（2026-07-23 产物展示统一）：完整 HTML 源码，整面沙箱 iframe 渲染，顶栏可切源码 */
  htmlSrc?: string;
  /** 源码模式（2026-07-23 产物展示统一）：纯文本/代码内容，白纸等宽字排版 */
  codeText?: string;
  /** 顶栏 meta 行的补充说明（如「已自动存入我的文件」） */
  metaNote?: string;
  /** 宿主的 savePages 正在落库（2026-07-26 对抗校验）：期间禁关查看器——
   *  editedPages 是用户手改内容的唯一持有者，父层 v-if 卸载即销毁，
   *  关掉之后再保存失败，「修改仍在编辑器里，可直接重试」就是假话。 */
  saving?: boolean;
}>();
const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'download'): void;
  (e: 'aiEdit', payload: { instruction: string; scope: 'page' | 'all'; page: number }): void;
  /** done(ok)：宿主落库结果回执。ok=true 时本组件清脏——否则保存成功后若查看器仍开着，
   *  ✕ 还会弹「放弃未保存」（2026-08-06 用户报：保存过了还弹确认）。 */
  (e: 'savePages', pages: string[], done: (ok: boolean) => void): void;
}>();

const slots = useSlots();
const parsedReport = parseResearchReport(props.htmlSrc || '');
const isReport = Boolean(parsedReport);
const isWeb = Boolean(props.htmlSrc) && !isReport;
const isCode = !isWeb && !isReport && Boolean(props.codeText);
const isArticle = !isWeb && !isReport && !isCode && !props.doc && Boolean(props.articleHtml) && !(props.slidesPages?.length);
const isLive = !isWeb && !isReport && !isCode && (props.slidesPages?.length || 0) > 0;
/** 通用内容槽（2026-07-23 预览全量统一）：无内容 props、但宿主给了默认插槽（vue-office
 *  Excel/图片/渲染兜底等自带渲染器）——查看器只当壳（顶栏+全幅主体） */
const isCustom =
  !isWeb && !isReport && !isCode && !isArticle && !props.doc && !(props.slidesPages?.length) && Boolean(slots.default);
/** 有分页画布的形态（pdf/office/活页）：缩略图轨、放映、翻页键只在这些形态存在 */
const isPaged = !isWeb && !isReport && !isCode && !isArticle && !isCustom;
/** 无分页概念、只能整份修改的形态（AI 编辑不出「当前页」选项） */
const isWhole = isArticle || isWeb || isReport || isCode || isCustom;
const webView = ref<'preview' | 'code'>('preview');
const displayTitle = parsedReport?.title || props.filename;
const reportPages = parsedReport?.pages || [];
const reportArticleHtml = parsedReport?.articleHtml || reportPages.map((page) => page.html).join('');
const reportToc = reportPages
  .map((page, index) => ({ id: page.id, title: page.title, index }))
  .filter((page) => page.id !== 'cover' && page.id !== 'toc');
const reportStageRef = ref<HTMLElement | null>(null);
const reportArticleRef = ref<HTMLElement | null>(null);
const reportActivePage = ref(0);
function scrollReportPage(index: number) {
  const stage = reportStageRef.value;
  const id = reportToc[index]?.id;
  if (!stage || !id) return;
  const el = stage.querySelector(`#${CSS.escape(id)}`) as HTMLElement | null;
  if (!el) return;
  stage.scrollTop = Math.max(0, el.offsetTop - 24);
  reportActivePage.value = index;
}
function onReportScroll() {
  const stage = reportStageRef.value;
  if (!stage) return;
  let active = 0;
  reportToc.forEach((item) => {
    const el = stage.querySelector(`#${CSS.escape(item.id)}`) as HTMLElement | null;
    if (el && el.offsetTop - 48 <= stage.scrollTop) active = item.index;
  });
  reportActivePage.value = active;
}

/** 内容槽形态的顶栏图标：按扩展名取（壳自身不知道槽里装的是什么渲染器） */
const customIcon = (() => {
  const n = props.filename.toLowerCase();
  if (/\.(xlsx?|xlsm|csv)$/.test(n)) return FileExcelOutlined;
  if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(n)) return FileImageOutlined;
  if (/\.docx?$/.test(n)) return FileWordOutlined;
  if (/\.pptx?$/.test(n)) return FilePptOutlined;
  return FileOutlined;
})();
const isPpt = /\.pptx?$/i.test(props.filename);
const isExcel = /\.(xlsx?|xlsm|csv)$/i.test(props.filename);
const isWord = /\.docx?$/i.test(props.filename);
const isOriginalPdf = /\.pdf$/i.test(props.filename);
const sourceKindClass = isExcel ? 'k-excel' : isWord ? 'k-word' : isPpt ? 'k-ppt' : isOriginalPdf ? 'k-pdf' : '';
const originalFormatLabel = (props.filename.split('.').pop() || '文件').toUpperCase();
const pageCount = isLive ? (props.slidesPages?.length || 0) : (props.doc?.numPages ?? 0);
const activePage = ref(1);
const rootRef = ref<HTMLElement | null>(null);
const stageRef = ref<HTMLElement | null>(null);
const thumbRefs = new Map<number, HTMLCanvasElement>();

// ---- 连续滚动（2026-07-28）：整册堆叠 + 懒渲染 ----
const pageBoxRefs = new Map<number, HTMLElement>();
const pageCanvasRefs = new Map<number, HTMLCanvasElement>();
/** 每页 height/width 比，用于渲染前先占位；未知时按 A4 竖版兜底 */
const pageAspects = ref<Record<number, number>>({});
const DEFAULT_ASPECT = 1.414;
/** 舞台**内容盒**宽度（已扣掉 padding 与纵向滚动条），决定每页画布的 CSS 宽。
 *  必须用内容盒而不是 clientWidth：连续滚动后纵向滚动条一定会出现，若按滚动条出现
 *  「之前」的宽度定页宽，页就会比可用空间宽，挤出一条横向滚动条（macOS 的浮层滚动条
 *  看不出来，Windows/Linux 上必现）。口径仍与旧单画布一致：320 ~ 1040。 */
const stageWidth = ref(0);
const pageCssWidth = computed(() => Math.max(320, Math.min(stageWidth.value - 4, 1040)));

function aspectOf(n: number) {
  return pageAspects.value[n] ?? pageAspects.value[1] ?? DEFAULT_ASPECT;
}

/** 占位尺寸：渲染完成后 renderPageTo 会写死同样的 canvas 尺寸，故不会二次跳动 */
function pageBoxStyle(n: number) {
  const w = pageCssWidth.value;
  return { width: `${w}px`, height: `${Math.round(w * aspectOf(n))}px` };
}

function setPageBoxRef(el: unknown, page: number) {
  if (el instanceof HTMLElement) pageBoxRefs.set(page, el);
  else pageBoxRefs.delete(page);
}
function setPageCanvasRef(el: unknown, page: number) {
  if (el instanceof HTMLCanvasElement) pageCanvasRefs.set(page, el);
  else pageCanvasRefs.delete(page);
}

const openMenu = ref<'download' | 'play' | null>(null);

// ---- 活页编辑模式状态 ----
const liveFrame = ref<HTMLIFrameElement | null>(null);
const liveDirty = ref(false);
const editedPages = ref<Record<number, string>>({});
let liveKit: SlideEditKitHandle | null = null;

/** 页面顺序（2026-07-21 拖拽排序）：显示位 → 原始页号 */
const pageOrder = ref<number[]>((props.slidesPages || []).map((_, i) => i));

function srcIdxAt(displayNo: number): number {
  return pageOrder.value[displayNo - 1] ?? displayNo - 1;
}

const liveHtml = computed(() => {
  const idx = srcIdxAt(activePage.value);
  return sanitizeSlideHtml(editedPages.value[idx] ?? props.slidesPages?.[idx] ?? '');
});

/** 只读 srcdoc（缩略图/放映）：给原始 1280×720 内容（不缩放）——缩放由外层壳的 CSS transform 精确处理 */
function pageSrc(displayNo: number): string {
  const idx = srcIdxAt(displayNo);
  return slideFrameSrc(editedPages.value[idx] ?? props.slidesPages?.[idx] ?? '');
}

let dragFrom = 0;
function onThumbDragStart(n: number) {
  dragFrom = n;
}
function onThumbDrop(n: number) {
  if (!isLive || dragFrom === n) return;
  harvestLive();
  const order = [...pageOrder.value];
  const activeSrc = order[activePage.value - 1];
  const [moved] = order.splice(dragFrom - 1, 1);
  order.splice(n - 1, 0, moved);
  pageOrder.value = order;
  activePage.value = order.indexOf(activeSrc) + 1;
  liveDirty.value = true;
}

function onLiveLoad() {
  liveKit?.destroy();
  liveKit = liveFrame.value
    // 照单同步 kit 的脏状态：全部撤销回原样时它会回落到 false，「保存修改」按钮
    // 与关闭确认都跟着收起——不这么做，用户撤销回原样后仍会被问一次未保存（2026-07-30）
    ? attachSlideEditKit(liveFrame.value, (dirty) => { liveDirty.value = dirty; })
    : null;
}

/** 切页/放映/保存前把当前页的改动收割进缓存。
 *  未改动时**必须原样返回**：收割会剥净帧内交互，而 srcdoc 没变就不会重载重挂，
 *  白白把这一页改成不可编辑（缩略图拖拽排序时最容易撞上）。 */
function harvestLive() {
  if (!isLive || !liveKit || !liveKit.isDirty()) return;
  editedPages.value = { ...editedPages.value, [srcIdxAt(activePage.value)]: liveKit.harvest() };
  liveKit.destroy();
  liveKit = null;
}

function goPage(n: number) {
  if (isLive) {
    if (n === activePage.value) return;
    harvestLive();
    activePage.value = n;
    return;
  }
  // 只读分页模式是连续滚动：点缩略图＝滚到那一页，而不是换一张画布
  activePage.value = n;
  scrollToPage(n);
}

function markSavedClean(pages: string[]) {
  // 保存成功 = 当前内容已是新基线：收起「保存修改」、✕/Esc 不再二次确认
  const next: Record<number, string> = { ...editedPages.value };
  pageOrder.value.forEach((srcIdx, i) => {
    next[srcIdx] = pages[i] ?? next[srcIdx] ?? props.slidesPages?.[srcIdx] ?? '';
  });
  editedPages.value = next;
  liveDirty.value = false;
  // kit 若还在（仅改页序、帧内未改时 harvest 可能跳过），按当前 HTML 重挂，baseHtml 对齐
  if (liveKit) {
    liveKit.destroy();
    liveKit = null;
    // 下一帧 onLiveLoad 会重挂；若 srcdoc 没变则强制触一下
    if (liveFrame.value) onLiveLoad();
  }
}

function onSavePages() {
  if (props.saving) return;
  harvestLive();
  const arr = pageOrder.value.map((srcIdx) => editedPages.value[srcIdx] ?? props.slidesPages?.[srcIdx] ?? '');
  let settled = false;
  emit('savePages', arr, (ok) => {
    if (settled) return;
    settled = true;
    if (ok) markSavedClean(arr);
  });
}

/** 关闭闸（✕ / Esc 共用）：保存在途时拒绝关闭并给一行轻提示，2.4s 后自行消失。 */
const closeHint = ref('');
let closeHintTimer = 0;
function flashCloseHint(text: string) {
  closeHint.value = text;
  if (closeHintTimer) window.clearTimeout(closeHintTimer);
  closeHintTimer = window.setTimeout(() => {
    closeHint.value = '';
    closeHintTimer = 0;
  }, 2400);
}

/** 有未保存手改时，✕/Esc 要**先问一句**（2026-07-30 修）。
 *  此前只拦「保存在途」，改了半天的内容点一下 ✕ 就静默蒸发——手改没有任何草稿留存，
 *  关掉即永久丢失，而 ✕ 和 Esc 都太容易误触（Esc 还兼着退出选中态/关色板）。 */
function requestClose() {
  if (props.saving) {
    flashCloseHint('正在保存修改，请稍候…');
    return;
  }
  if (isLive && liveDirty.value) {
    Modal.confirm({
      title: '放弃未保存的修改？',
      content: '这一份手改还没有保存，关闭后无法恢复。',
      okText: '放弃修改并关闭',
      okType: 'danger',
      cancelText: '继续编辑',
      // 必须盖过 .dpv-overlay 的 2100：antd Modal 默认 zIndexPopupBase=1000，
      // 确认框会落在全屏查看器后面，表现就是「✕ 点了没反应」（2026-08-06 用户报：
      // 改完/保存后关不掉）。zIndex 只抬这一次确认，不动全局 token。
      zIndex: 2200,
      onOk: () => emit('close'),
    });
    return;
  }
  emit('close');
}
const editOpen = ref(false);
const editText = ref('');
const editScope = ref<'page' | 'all'>('page');
const quickActions = isPpt
  ? ['刷新布局', '简洁一点', '添加更多内容', '丰富视觉效果']
  : ['精简文字', '润色语言', '修正错别字', '丰富内容'];

function submitEdit(preset?: string) {
  const instruction = (preset || editText.value).trim();
  if (!instruction) return;
  emit('aiEdit', { instruction, scope: isWhole ? 'all' : editScope.value, page: activePage.value });
  editOpen.value = false;
  editText.value = '';
}
const playing = ref(false);
const playPage = ref(1);
const playCanvas = ref<HTMLCanvasElement | null>(null);

const metaLine = (() => {
  const parts = pageCount > 0 ? [`${pageCount} 页`] : [];
  if (props.createdAt) {
    const d = new Date(props.createdAt);
    if (!Number.isNaN(d.getTime())) {
      parts.push(
        `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`,
      );
    }
  }
  // 活页模式：顶栏这行同时充当操作提示（页面即编辑器，不再另开编辑页）
  if (isLive) parts.push('单击选中 · 双击改字 · 拖拽移动 · 缩略图可拖动排序');
  if (props.metaNote) parts.push(props.metaNote);
  return parts.join(' · ');
})();

function setThumbRef(el: unknown, page: number) {
  if (el instanceof HTMLCanvasElement) thumbRefs.set(page, el);
}

async function renderPageTo(canvas: HTMLCanvasElement, pageNo: number, cssWidth: number) {
  if (!props.doc) return;
  const page = await props.doc.getPage(pageNo);
  const base = page.getViewport({ scale: 1 });
  const dpr = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: (cssWidth / base.width) * dpr });
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
  canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  await page.render({ canvasContext: ctx, viewport, canvas }).promise;
}

/** 放映渲染：按视口宽高双向适配（宽为限则贴宽，高为限则按高折算宽） */
async function renderPageFit(canvas: HTMLCanvasElement, pageNo: number, maxW: number, maxH: number) {
  if (!props.doc) return;
  const page = await props.doc.getPage(pageNo);
  const base = page.getViewport({ scale: 1 });
  const cssWidth = Math.min(maxW, maxH * (base.width / base.height));
  await renderPageTo(canvas, pageNo, Math.max(240, cssWidth));
}

// 大画布/放映画布的渲染必须串行：同一画布并发 render 时，后到的 resize 会把先到的
// 成品清成「大白布左上角一小图」（退出放映时 activePage 回写的 watcher 与 nextTick
// 各触发一次渲染，正是这种并发）。令牌让过期的排队请求直接跳过，最新一次最终生效。
let renderTicket = 0;
let renderChain: Promise<void> = Promise.resolve();
function queueRender(run: () => Promise<void>) {
  const ticket = ++renderTicket;
  renderChain = renderChain
    .then(() => (ticket === renderTicket ? run() : undefined))
    .catch(() => undefined);
  return renderChain;
}

// 整册页面的渲染串行排队。这里**不能**复用 queueRender：那个令牌机制会让后来的请求
// 作废先前的，对单画布是对的（只要最后一次），对多页则会把先滚过的页永久留白。
let pageChain: Promise<void> = Promise.resolve();
function queuePageRender(run: () => Promise<void>) {
  pageChain = pageChain.then(run).catch(() => undefined);
  return pageChain;
}

/** 已渲染页 → 当时的 CSS 宽。宽度变了要重画，否则放大窗口后是糊的 */
const renderedAt = new Map<number, number>();

function zeroCanvas(canvas: HTMLCanvasElement) {
  canvas.width = 0;
  canvas.height = 0;
  canvas.style.width = '0px';
  canvas.style.height = '0px';
}

/** 正在渲染的页号（渲染是串行的，同时至多一页）。回收要避开它，否则会把画布从
 *  pdf.js 手里抽走。 */
let renderingPage = 0;

function renderPageLazy(n: number) {
  const width = pageCssWidth.value;
  // 放映中/刚挂载时 dpv-body 还可能是 display:none，量到 0 宽就先跳过，等可见后再来
  if (stageWidth.value <= 0) return;
  if (renderedAt.get(n) === width) return;
  renderedAt.set(n, width);
  return queuePageRender(async () => {
    // 排队期间可能已被移出保留带（renderedAt 被删）或宽度又变了，这两种都不必画了
    if (renderedAt.get(n) !== width) return;
    const canvas = pageCanvasRefs.get(n);
    if (!canvas) {
      renderedAt.delete(n);
      return;
    }
    renderingPage = n;
    try {
      await renderPageTo(canvas, n, width);
    } catch {
      renderedAt.delete(n); // 失败的页留给下次进入视口时重试
    } finally {
      renderingPage = 0;
      // 渲染途中被滚出保留带：现在补回收，别让它白占着内存
      if (!renderedAt.has(n)) zeroCanvas(canvas);
    }
  });
}

/** 预取每页真实长宽比，让占位高度一开始就准（滚动条不会边滚边变长） */
async function loadAspects() {
  if (!props.doc) return;
  const next: Record<number, number> = {};
  for (let n = 1; n <= pageCount; n++) {
    try {
      const base = (await props.doc.getPage(n)).getViewport({ scale: 1 });
      next[n] = base.height / base.width;
    } catch {
      next[n] = DEFAULT_ASPECT;
    }
    // 首页先落地，后面的边取边补——长文档不必等全部取完才出画面
    if (n === 1 || n === pageCount) pageAspects.value = { ...pageAspects.value, ...next };
  }
  pageAspects.value = { ...pageAspects.value, ...next };
}

/** 远离视口的页释放像素。不回收的话，滚过一份两百页的 PDF 会把每页的整张全分辨率
 *  画布都留在内存里（单页 ~20MB@dpr2），几十页就能把标签页拖垮。占位盒的高度不动，
 *  所以回收只是变回一张白纸，滚回来时再渲染。 */
function evictPage(n: number) {
  if (!renderedAt.has(n)) return;
  renderedAt.delete(n);
  if (renderingPage === n) return; // 正在画：交给渲染的 finally 收尾，别抽掉画布
  const canvas = pageCanvasRefs.get(n);
  if (canvas) zeroCanvas(canvas);
}

// 两条带子做迟滞：进到视口上下 600px 内开画，退出 1800px 外才回收。
// 用同一条带子会在边界反复「画了又扔」。
let pageObserver: IntersectionObserver | null = null;
let keepObserver: IntersectionObserver | null = null;
function pageNoOf(target: Element) {
  return Number((target as HTMLElement).dataset.page || 0);
}
function observePages() {
  pageObserver?.disconnect();
  keepObserver?.disconnect();
  if (!stageRef.value || isLive) return;
  pageObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const n = pageNoOf(e.target);
        if (n) renderPageLazy(n);
      }
    },
    { root: stageRef.value, rootMargin: '600px 0px' },
  );
  keepObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (e.isIntersecting) continue;
        const n = pageNoOf(e.target);
        if (n) evictPage(n);
      }
    },
    { root: stageRef.value, rootMargin: '1800px 0px' },
  );
  pageBoxRefs.forEach((el) => {
    pageObserver?.observe(el);
    keepObserver?.observe(el);
  });
}

/** 取内容盒宽：clientWidth 已扣滚动条，再扣掉左右 padding(26+26) */
function syncStageWidth() {
  const el = stageRef.value;
  if (!el) return;
  const w = el.clientWidth - 52;
  if (w > 0) stageWidth.value = w;
}

/** 视口附近的页按当前宽重画（宽度变了时用；视口外的等滚进来由观察器补） */
function rerenderNearbyPages() {
  const s = stageRef.value?.getBoundingClientRect();
  if (!s) return;
  pageBoxRefs.forEach((el, n) => {
    const r = el.getBoundingClientRect();
    if (r.bottom > s.top - 600 && r.top < s.bottom + 600) renderPageLazy(n);
  });
}

// 舞台尺寸变化（窗口缩放/侧栏变化/滚动条出现）都要重算页宽。用内容盒宽度定页宽，
// 所以不会出现「改页宽→滚动条切换→再改页宽」的来回震荡。
let stageResizeObserver: ResizeObserver | null = null;
function observeStageSize() {
  stageResizeObserver?.disconnect();
  if (!stageRef.value || isLive || typeof ResizeObserver === 'undefined') return;
  stageResizeObserver = new ResizeObserver(() => {
    const before = pageCssWidth.value;
    syncStageWidth();
    if (pageCssWidth.value !== before) rerenderNearbyPages();
  });
  stageResizeObserver.observe(stageRef.value);
}

/** 滚动位置 → 当前页（缩略图高亮 / AI 编辑作用范围 / 放映起始页都读它） */
let scrollRaf = 0;
let scrollingByCode = false;
function onStageScroll() {
  if (isLive || scrollRaf) return;
  scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0;
    const stage = stageRef.value;
    if (!stage) return;
    const stageTop = stage.getBoundingClientRect().top;
    const probe = stage.clientHeight * 0.4; // 视口上方 40% 处压住哪一页就算哪一页
    // 页是顺序排的，越过探针就可以收工——别每帧对全部页取 rect（几百页时那是几百次
    // 强制重排，滚动会明显发卡）
    let best = 1;
    for (let n = 1; n <= pageCount; n++) {
      const el = pageBoxRefs.get(n);
      if (!el) continue;
      if (el.getBoundingClientRect().top - stageTop > probe) break;
      best = n;
    }
    if (best !== activePage.value) activePage.value = best;
  });
}

/** 缩略图轨跟随当前页（仅在用户滚动时，点缩略图本身不需要再滚轨） */
function revealActiveThumb() {
  if (scrollingByCode) return;
  const thumb = thumbRefs.get(activePage.value)?.closest('.dpv-thumb');
  thumb?.scrollIntoView({ block: 'nearest' });
}

/** 滚到某页（缩略图点击 / 方向键 / 退出放映）。
 *  一律直接赋 scrollTop，**不走任何平滑路径**。原因是实测踩到的：
 *  scrollTo({behavior:'smooth'}) 在不实现平滑滚动的环境里不是「退化成瞬间跳」而是
 *  **整个变成空操作**——点缩略图纹丝不动；给舞台加 CSS scroll-behavior: smooth 之后
 *  连 scrollTop 赋值也一起被吞掉。跳页是功能不是装饰，不能拿它赌浏览器支持度。 */
function scrollToPage(n: number) {
  const stage = stageRef.value;
  const el = pageBoxRefs.get(n);
  if (!stage || !el) return;
  const top = el.getBoundingClientRect().top - stage.getBoundingClientRect().top + stage.scrollTop;
  scrollingByCode = true;
  stage.scrollTop = Math.max(0, top - 26);
  scrollingByCode = false;
}

async function renderThumbs() {
  // 顺序渲染避免同时占满主线程；页数多时后面的缩略图逐步出现即可
  for (let n = 1; n <= pageCount; n++) {
    const canvas = thumbRefs.get(n);
    if (canvas) await renderPageTo(canvas, n, 104);
  }
}

function toggleMenu(which: 'download' | 'play') {
  openMenu.value = openMenu.value === which ? null : which;
}

function onDocClick() {
  openMenu.value = null;
  editOpen.value = false;
}

function onDownloadOriginal() {
  openMenu.value = null;
  emit('download');
}

function onDownloadPdf() {
  openMenu.value = null;
  if (!props.pdfUrl) return;
  const a = document.createElement('a');
  a.href = props.pdfUrl;
  a.download = props.filename.replace(/\.[^.]+$/, '') + '.pdf';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---- 放映 ----
/** 活页放映的等比缩放：1280×720 画面塞进当前视口（留出计数器的边距） */
const playScale = ref(1);
function computePlayScale() {
  playScale.value = Math.max(
    0.05,
    Math.min((window.innerWidth - 32) / 1280, (window.innerHeight - 56) / 720),
  );
}

async function enterPlay(fromStart: boolean) {
  openMenu.value = null;
  if (isLive) harvestLive(); // 放映要放的是手改后的最新内容
  playing.value = true;
  playPage.value = fromStart ? 1 : activePage.value;
  await nextTick();
  try {
    await rootRef.value?.requestFullscreen?.();
  } catch {
    // 全屏被拒（iframe 权限等）：黑场覆盖层本身也能放映，不阻断
  }
  if (isLive) computePlayScale();
  else renderPlay();
}

function renderPlay() {
  return queueRender(async () => {
    const canvas = playCanvas.value;
    if (!canvas) return;
    await renderPageFit(canvas, playPage.value, window.innerWidth - 32, window.innerHeight - 56);
  });
}

function exitPlay() {
  playing.value = false;
  activePage.value = playPage.value; // 放映到哪页，回来就停在哪页
  if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
  if (!isLive) {
    nextTick(() => {
      syncStageWidth(); // 放映期间 dpv-body 是 display:none，量宽为 0，回来先补量
      scrollToPage(playPage.value);
      renderPageLazy(playPage.value);
    });
  }
}

function playNext() {
  if (playPage.value < pageCount) playPage.value += 1;
  else exitPlay(); // 最后一页再进一步=结束放映
}

function onFullscreenChange() {
  // 用户按 Esc 退出浏览器全屏：同步退出放映态
  if (!document.fullscreenElement && playing.value) exitPlay();
}

function onKeydown(e: KeyboardEvent) {
  if (playing.value) {
    if (e.key === 'Escape') exitPlay();
    else if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === ' ') playNext();
    else if ((e.key === 'ArrowLeft' || e.key === 'ArrowUp') && playPage.value > 1) playPage.value -= 1;
    return;
  }
  // 在 AI 编辑框里打字时方向键归输入框，不该顺手翻页
  const tag = (e.target as HTMLElement | null)?.tagName;
  const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  if (e.key === 'Escape') {
    if (openMenu.value) openMenu.value = null;
    else if (editOpen.value) editOpen.value = false;
    else requestClose(); // 保存在途时不关（手改内容只活在本组件里）
  } else if (typing) {
    // 交给输入框
  } else if (e.key === 'ArrowRight' || (isLive && e.key === 'ArrowDown')) {
    if (activePage.value < pageCount) goPage(activePage.value + 1);
  } else if (e.key === 'ArrowLeft' || (isLive && e.key === 'ArrowUp')) {
    if (activePage.value > 1) goPage(activePage.value - 1);
  }
  // 只读分页模式的 ↑/↓/空格/PageUp/PageDown 一律交给舞台原生滚动——连续滚动下
  // 再按整页跳会和原生滚动打架（舞台 tabindex=0 且挂载即聚焦，原生滚动可用）
}

watch(activePage, () => {
  if (!playing.value && !isLive) revealActiveThumb();
});
watch(playPage, () => {
  if (playing.value && !isLive) renderPlay();
});

function onWindowResize() {
  // 分页模式的页宽重算交给舞台的 ResizeObserver（它连滚动条出现这种非窗口尺寸
  // 变化也能接住），这里只剩活页放映的等比缩放
  if (playing.value && isLive) computePlayScale();
}

let prevBodyOverflow = '';
onMounted(async () => {
  prevBodyOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  window.addEventListener('keydown', onKeydown);
  document.addEventListener('click', onDocClick);
  document.addEventListener('fullscreenchange', onFullscreenChange);
  window.addEventListener('resize', onWindowResize);
  await nextTick();
  if (!isPaged || !props.doc) return; // 活页/网页/源码/文章模式没有 PDF 画布，无需 canvas 渲染
  syncStageWidth();
  stageRef.value?.focus?.({ preventScroll: true }); // 让 ↑/↓/空格/PageDown 原生滚动即刻可用
  loadAspects();
  await nextTick();
  observeStageSize();
  observePages();
  renderPageLazy(1); // 首页不等观察器回调，立刻出画面
  renderThumbs();
});

onBeforeUnmount(() => {
  liveKit?.destroy();
  pageObserver?.disconnect();
  keepObserver?.disconnect();
  stageResizeObserver?.disconnect();
  if (scrollRaf) cancelAnimationFrame(scrollRaf);
  if (closeHintTimer) window.clearTimeout(closeHintTimer);
  document.body.style.overflow = prevBodyOverflow;
  window.removeEventListener('keydown', onKeydown);
  document.removeEventListener('click', onDocClick);
  document.removeEventListener('fullscreenchange', onFullscreenChange);
  window.removeEventListener('resize', onWindowResize);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
});
</script>

<style scoped>
.dpv-overlay {
  position: fixed;
  inset: 0;
  z-index: 2100;
  display: flex;
  flex-direction: column;
  background: #fff;
  animation: dpv-in 0.22s ease both;
}

@keyframes dpv-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.dpv-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid #eceef2;
  flex: none;
}

.dpv-file-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  background: #f2f3f6;
  color: #5b6472;
  font-size: 15px;
  flex: none;
}
.dpv-file-icon.k-excel { background: #ebf6ef; color: #2f8a5b; }
.dpv-file-icon.k-word { background: #eef2fc; color: #3b5ba5; }
.dpv-file-icon.k-ppt { background: #fdf1e7; color: #c07a25; }
.dpv-file-icon.k-pdf { background: #fceeee; color: #c0554f; }

.dpv-title-wrap {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.dpv-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #23272e;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dpv-meta {
  font-style: normal;
  font-size: 12px;
  color: #9aa0ab;
}

.dpv-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.dpv-menu-host {
  position: relative;
}

.dpv-btn {
  border: 1px solid #e2e4e9;
  background: #fff;
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.dpv-btn:hover {
  background: #f6f7f9;
  border-color: #c8ccd4;
}

.dpv-close {
  padding: 5px 9px;
  font-size: 12px;
  color: #6b7280;
}

.dpv-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 176px;
  padding: 5px;
  border: 1px solid #e9eaee;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(30, 35, 44, 0.1);
  z-index: 10;
  animation: dpv-menu-in 0.16s ease both;
}

@keyframes dpv-menu-in {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.dpv-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 0;
  background: transparent;
  border-radius: 7px;
  padding: 8px 10px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
  text-align: left;
}

.dpv-menu-item:hover {
  background: #f4f5f7;
}

.dpv-menu-item :deep(.anticon) {
  font-size: 14px;
  color: #6b7280;
}

.dpv-body {
  flex: 1;
  min-height: 0;
  display: flex;
}

/* 文章模式（md）：浅灰舞台上一列白纸排版 */
.dpv-article-wrap {
  flex: 1;
  min-width: 0;
  overflow: auto;
  background: #f7f8fa;
  padding: 26px 16px;
}

.dpv-article {
  max-width: 780px;
  margin: 0 auto;
  background: #fff;
  border: 1px solid #e9eaee;
  border-radius: 6px;
  box-shadow: 0 1px 4px rgba(30, 35, 44, 0.06);
  padding: 40px 48px 48px;
  font-size: 15px;
  line-height: 1.8;
  color: #24292f;
  word-break: break-word;
}

.dpv-article :deep(h1) {
  font-size: 24px;
  margin: 0 0 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #eceef2;
}

.dpv-article :deep(h2) {
  font-size: 19px;
  margin: 26px 0 12px;
}

.dpv-article :deep(h3) {
  font-size: 16px;
  margin: 20px 0 10px;
}

.dpv-article :deep(p) {
  margin: 0 0 12px;
}

.dpv-article :deep(ul),
.dpv-article :deep(ol) {
  margin: 0 0 12px;
  padding-left: 24px;
}

.dpv-article :deep(li) {
  margin: 4px 0;
}

.dpv-article :deep(code) {
  background: #f4f5f7;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 13px;
}

.dpv-article :deep(pre) {
  background: #f7f8fa;
  border: 1px solid #eceef2;
  border-radius: 8px;
  padding: 12px 14px;
  overflow-x: auto;
  margin: 0 0 14px;
}

.dpv-article :deep(pre code) {
  background: transparent;
  padding: 0;
}

.dpv-article :deep(blockquote) {
  margin: 0 0 12px;
  padding: 2px 14px;
  border-left: 3px solid #d8dbe1;
  color: #5f6672;
}

.dpv-article :deep(table) {
  border-collapse: collapse;
  margin: 0 0 14px;
  font-size: 13.5px;
}

.dpv-article :deep(th),
.dpv-article :deep(td) {
  border: 1px solid #e5e7eb;
  padding: 6px 12px;
}

.dpv-article :deep(th) {
  background: #f7f8fa;
}

.dpv-article :deep(img) {
  max-width: 100%;
}

.dpv-article :deep(hr) {
  border: 0;
  border-top: 1px solid #eceef2;
  margin: 18px 0;
}

/* 网页模式：整面沙箱 iframe（白底，无舞台边框——网页自己就是画面） */
.dpv-web-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  background: #fff;
}

.dpv-web-frame {
  flex: 1;
  width: 100%;
  border: 0;
  background: #fff;
}

/* 源码模式（含网页模式的「源码」页签）：浅灰舞台上一张等宽字白纸 */
.dpv-code-scroll {
  flex: 1;
  min-width: 0;
  overflow: auto;
  background: #f7f8fa;
  padding: 26px 16px;
}

.dpv-code {
  max-width: 980px;
  margin: 0 auto;
  background: #fff;
  border: 1px solid #e9eaee;
  border-radius: 6px;
  box-shadow: 0 1px 4px rgba(30, 35, 44, 0.06);
  padding: 20px 24px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12.5px;
  line-height: 1.7;
  color: #1f2430;
  white-space: pre-wrap;
  word-break: break-word;
}

/* 通用内容槽：壳只负责占满与滚动，内容排版交给宿主插槽（vue-office/图片各有各的规矩） */
.dpv-custom-wrap {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: auto;
  background: #f7f8fa;
}

/* 预览/源码 切换（网页模式顶栏） */
.dpv-tabs {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: #f1f2f4;
}

.dpv-tabs button {
  padding: 4px 12px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  font-size: 12.5px;
  cursor: pointer;
}

.dpv-tabs button.active {
  background: #fff;
  color: #111;
  box-shadow: 0 1px 3px rgba(17, 24, 39, 0.1);
}

.dpv-rail {
  width: 148px;
  flex: none;
  overflow-y: auto;
  padding: 14px 0;
  border-right: 1px solid #eceef2;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.dpv-thumb {
  position: relative;
  border: 2px solid transparent;
  border-radius: 8px;
  padding: 2px;
  background: #fff;
  cursor: pointer;
  line-height: 0;
}

.dpv-thumb canvas {
  border: 1px solid #e9eaee;
  border-radius: 4px;
  background: #fff;
  min-height: 40px;
}

.dpv-thumb.active {
  border-color: #23272e;
}

.dpv-thumb-no {
  display: block;
  margin-top: 4px;
  font-size: 11.5px;
  line-height: 1.4;
  color: #9aa0ab;
  text-align: center;
}

.dpv-thumb.active .dpv-thumb-no {
  color: #23272e;
  font-weight: 600;
}

.dpv-stage {
  flex: 1;
  min-width: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  /* 纵向一律从头排，绝不能用 justify-content: center——内容超高时 flex 居中会把顶部
     溢出到滚动区之外，第一页永远滚不回去。横向居中交给 align-items。 */
  justify-content: flex-start;
  gap: 18px;
  padding: 26px;
  background: #f7f8fa;
  /* 刻意不设 scroll-behavior: smooth——见 scrollToPage 的注释，平滑路径在部分环境里
     会让整个跳转变成空操作。缩略图跳页直接到位（Chrome 内置 PDF 查看器也是这样）。 */
}

/* 连续滚动的每一页：渲染前先按真实长宽比占好位（白纸即占位/加载态），
   避免边滚边渲染时页高突变、滚动条乱跳 */
.dpv-page {
  flex: none;
  line-height: 0;
  border: 1px solid #e9eaee;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(30, 35, 44, 0.06);
  overflow: hidden;
}

.dpv-live-box {
  width: min(100%, 1040px);
  border: 1px solid #e9eaee;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 1px 4px rgba(30, 35, 44, 0.06);
  overflow: hidden;
}

.dpv-live-frame {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 0;
  background: #fff;
}

/* 缩略图壳：固定 104×58.5，裁掉溢出；缩放放这里而非帧内（sandbox="" 量不到内部宽） */
.dpv-thumb-shell {
  width: 104px;
  height: 58.5px;
  overflow: hidden;
  border: 1px solid #e9eaee;
  border-radius: 4px;
  background: #fff;
}

.dpv-thumb-frame {
  display: block;
  width: 1280px;
  height: 720px;
  border: 0;
  background: #fff;
  transform: scale(0.08125);
  transform-origin: 0 0;
  pointer-events: none;
}

.dpv-save {
  background: #23272e;
  border-color: #23272e;
  color: #fff;
}

.dpv-save:hover {
  background: #3a4048;
  border-color: #3a4048;
}

/* 保存在途：按钮锁死（避免重复落库/重复发重编译消息），文案自述状态 */
.dpv-save.busy,
.dpv-save:disabled {
  background: #8a9099;
  border-color: #8a9099;
  cursor: default;
}

/* 保存在途时按 ✕ / Esc 的轻提示（不弹全局 message，避免遮住查看器顶栏） */
.dpv-hint {
  font-size: 12px;
  color: #8a9099;
  white-space: nowrap;
  animation: dpv-in 0.18s ease both;
}

/* 纸张外观已上移到 .dpv-page（渲染前也要像张白纸），画布本身只负责像素 */
.dpv-main-canvas {
  display: block;
  background: #fff;
}

/* AI 编辑（对标 Manus 魔棒面板）：右下角墨色圆钮 + 上方浮出白卡面板 */
.dpv-edit-fab {
  position: absolute;
  right: 22px;
  bottom: 20px;
  width: 42px;
  height: 42px;
  border: 0;
  border-radius: 50%;
  background: #23272e;
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4px 14px rgba(30, 35, 44, 0.24);
  transition: transform 0.15s ease, background 0.15s ease;
  z-index: 20;
}

.dpv-edit-fab:hover {
  background: #3a4048;
  transform: scale(1.05);
}

.dpv-edit-panel {
  position: absolute;
  right: 22px;
  bottom: 74px;
  width: 320px;
  padding: 14px 16px;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 12px 32px rgba(30, 35, 44, 0.14);
  z-index: 20;
  animation: dpv-menu-in 0.18s ease both;
}

.dpv-edit-title {
  margin: 0 0 10px;
  font-size: 13.5px;
  font-weight: 600;
  color: #23272e;
}

.dpv-edit-input {
  width: 100%;
  border: 1px solid #e2e4e9;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  color: #23272e;
  resize: none;
  outline: none;
  font-family: inherit;
}

.dpv-edit-input:focus {
  border-color: #a8aeb8;
}

.dpv-edit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.dpv-edit-scope {
  flex: 1;
  border: 1px solid #e2e4e9;
  border-radius: 8px;
  padding: 5px 8px;
  font-size: 12.5px;
  color: #374151;
  background: #fff;
  outline: none;
}

.dpv-edit-send {
  flex: none;
  background: #23272e;
  color: #fff;
  border-color: #23272e;
}

.dpv-edit-send:hover:not(:disabled) {
  background: #3a4048;
  border-color: #3a4048;
}

.dpv-edit-send:disabled {
  opacity: 0.4;
  cursor: default;
}

.dpv-edit-sub {
  margin: 12px 0 6px;
  font-size: 12px;
  color: #9aa0ab;
}

.dpv-edit-chips {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.dpv-chip {
  border: 1px solid #e2e4e9;
  background: #fff;
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 12.5px;
  color: #374151;
  cursor: pointer;
  transition: background 0.15s ease;
}

.dpv-chip:hover {
  background: #f4f5f7;
}

/* 放映模式：黑场居中，点击翻页 */
.dpv-play {
  position: absolute;
  inset: 0;
  background: #0b0d10;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.dpv-play-canvas {
  max-width: 100%;
  max-height: 100%;
}

/* 活页放映：原始 1280×720 帧居中定位后整体缩放（不吃点击，整屏点击=下一页） */
.dpv-play-live {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 1280px;
  height: 720px;
  pointer-events: none;
}

.dpv-play-frame {
  display: block;
  width: 1280px;
  height: 720px;
  border: 0;
  background: #fff;
}

.dpv-play-counter {
  position: absolute;
  right: 18px;
  bottom: 14px;
  font-size: 12.5px;
  color: rgba(255, 255, 255, 0.55);
  font-variant-numeric: tabular-nums;
}

.dpv-report-wrap {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: #f4f5f7;
}

.dpv-report-toc {
  width: 220px;
  flex-shrink: 0;
  overflow: auto;
  padding: 20px 12px 28px 18px;
  border-right: 1px solid #eceef2;
  background: #fafbfc;
}

.dpv-report-toc strong {
  display: block;
  margin: 0 8px 14px;
  font-size: 12px;
  letter-spacing: 0.08em;
  color: #8a9099;
  font-weight: 600;
}

.dpv-report-toc button {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.45;
  color: #4b5563;
  cursor: pointer;
}

.dpv-report-toc button.active,
.dpv-report-toc button:hover {
  background: #fff;
  color: #16181d;
}

.dpv-report-stage {
  flex: 1;
  overflow: auto;
  padding: 20px 28px 48px;
}

.dpv-report-sheet {
  max-width: 800px;
  margin: 0 auto 18px;
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 8px 28px rgba(22, 24, 29, 0.06);
  overflow: hidden;
}

.dpv-report-article {
  padding: 56px 64px 80px;
}

@media (max-width: 980px) {
  .dpv-report-toc {
    display: none;
  }

  .dpv-report-stage {
    padding: 12px;
  }
}

@media (max-width: 720px) {
  .dpv-rail {
    display: none;
  }

  .dpv-stage {
    padding: 12px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dpv-overlay {
    animation: none;
  }

  .dpv-menu {
    animation: none;
  }
}
</style>

<style>
/* antd popup 必须盖过 .dpv-overlay(2100)：message 默认 1010，
 * 查看器开着时 message.error/success 会落在后面（保存失败等于没反馈）。
 * 全局抬到 2300 无副作用——message 本就该压在业务层之上；锁页 3000 / 页加载 10000 仍更高。 */
.ant-message {
  z-index: 2300;
}

/* 研究报告连续文稿：v-html 片段带不上 scoped 属性，样式必须落在查看器内的原始 class 上 */
.dpv-report-article {
  color: #0d0d0d;
  font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC",
    "Segoe UI", "Noto Sans SC", "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.75;
}

.dpv-report-article h1 {
  font-size: 32px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.03em;
  margin: 0 0 28px;
}

.dpv-report-article h2 {
  font-size: 22px;
  font-weight: 700;
  line-height: 1.35;
  margin: 40px 0 14px;
}

.dpv-report-article h3 {
  font-size: 17px;
  font-weight: 650;
  margin: 28px 0 10px;
}

.dpv-report-article p {
  margin: 0 0 16px;
}

.dpv-report-article ul,
.dpv-report-article ol {
  margin: 0 0 16px;
  padding-left: 1.35em;
}

.dpv-report-article li {
  margin: 0 0 6px;
}

.dpv-report-article table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0 24px;
  font-size: 14px;
}

.dpv-report-article th,
.dpv-report-article td {
  border: none;
  border-bottom: 1px solid #ececec;
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}

.dpv-report-article thead th {
  background: #f7f7f8;
  font-weight: 650;
  border-bottom: 1px solid #e5e7eb;
}

.dpv-report-article .cite,
.dpv-report-article a.cite,
.dpv-report-article sup.cite {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 15px;
  height: 15px;
  margin: 0 1px 0 2px;
  padding: 0 4px;
  border-radius: 999px;
  background: #ececec;
  color: #6b7280;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
  text-decoration: none;
  vertical-align: super;
}

.dpv-report-article .research-refs a {
  color: #2563eb;
  text-decoration: none;
}

@media (max-width: 980px) {
  .dpv-report-article {
    padding: 28px 22px 48px;
  }
}
</style>
