<template>
  <!-- Teleport 到 body：躲开任何祖先 transform/filter 形成的 stacking context——
       否则 fixed 退化成祖先内 absolute，应用顶栏/输入框会浮到面板上面（2026-07-27 真机 bug） -->
  <Teleport to="body">
    <div
      ref="rootRef"
      :class="['exec-team', { 'is-wide': wide, resizing, floating: !docked, dragging }]"
      :style="panelStyle"
      role="dialog"
      aria-label="执行团队"
    >
      <!-- 左缘拖拽把手：调宽（停靠态改宽度；浮动态左缘移动、右缘不动） -->
      <div
        class="et-resizer et-resizer-x"
        role="separator"
        aria-orientation="vertical"
        :aria-valuenow="width"
        :aria-valuemin="MIN_W"
        :aria-valuemax="maxWidth"
        tabindex="0"
        aria-label="拖动调整执行团队面板宽度"
        @pointerdown="onResizeStart"
        @keydown="onResizeKey"
      ></div>

      <!-- 上下调高：停靠态拖上缘改高度（下缘贴底不动）；浮动态上下缘都能拖 -->
      <div
        class="et-resizer et-resizer-y et-resizer-top"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整执行团队面板高度"
        @pointerdown="(e) => onResizeStart(e, 'top')"
      ></div>
      <div
        v-if="!docked"
        class="et-resizer et-resizer-y et-resizer-bottom"
        role="separator"
        aria-orientation="horizontal"
        aria-label="拖动调整执行团队面板高度"
        @pointerdown="(e) => onResizeStart(e, 'bottom')"
      ></div>
      <!-- 右下角：同时调宽高 -->
      <div
        class="et-resizer et-resizer-corner"
        role="separator"
        aria-label="拖动调整执行团队面板大小"
        @pointerdown="(e) => onResizeStart(e, 'corner')"
      ></div>

      <header
        class="et-head"
        title="拖动可移动，双击回到右侧停靠"
        @pointerdown="onHeadDown"
        @dblclick="redock"
      >
        <strong>执行团队</strong>
        <div class="et-head-actions">
          <button
            type="button"
            :aria-label="atMax ? '还原宽度' : '放大面板'"
            :title="atMax ? '还原宽度' : '放大面板'"
            @click="toggleMax"
          >
            <CompressOutlined v-if="atMax" />
            <ExpandOutlined v-else />
          </button>
          <button type="button" aria-label="关闭执行团队" @click="$emit('close')">
            <CloseOutlined />
          </button>
        </div>
      </header>

      <div class="et-body">
        <div v-if="!members.length" class="et-blank">本轮没有组建团队</div>

        <div v-else class="et-stage">
          <!-- 主管卡：AXIOM Agent 即领导；点击回到主对话（它的对话就是左边的屏幕） -->
          <div class="et-mgr-wrap">
            <div
              class="et-card et-mgr"
              role="button"
              tabindex="0"
              @click="$emit('focus-chat')"
              @keydown.enter="$emit('focus-chat')"
            >
              <span class="et-sq"><RobotOutlined /></span>
              <div class="et-main">
                <div class="et-row">
                  <span class="et-nm">{{ managerName }}</span>
                  <span v-if="anyRunning" class="et-dot" aria-hidden="true"></span>
                </div>
                <p class="et-say">{{ managerLine }}</p>
              </div>
            </div>
          </div>

          <!-- 窄版=居中窄卡逐张下挂连线；宽版=居中流式 + 脚本按第一行卡片实际位置绘树 -->
          <div class="et-grp">
            <div v-if="wide" ref="wireRef" class="et-wire" aria-hidden="true"></div>
            <div ref="teamRef" class="et-team">
              <div
                v-for="run in members"
                :key="run.runKey"
                :ref="(el) => setCardRef(run.runKey, el)"
                :class="[
                  'et-card',
                  'et-wk',
                  run.status === 'running' ? 'running' : '',
                  focusKey === run.runKey ? 'focused' : '',
                ]"
                role="button"
                tabindex="0"
                :aria-label="`进入「${run.roleName || run.name}」的对话`"
                @click="$emit('select-run', run)"
                @keydown.enter="$emit('select-run', run)"
              >
                <span :class="['et-sq', run.status === 'running' ? 'running' : '']"><RobotOutlined /></span>
                <div class="et-main">
                  <div class="et-row">
                    <span class="et-nm">{{ run.roleName || run.name }}</span>
                    <span :class="['et-st', stateClass(run)]">{{ stateLabel(run) }}</span>
                  </div>
                  <p class="et-act" :title="actionLine(run)">{{ actionLine(run) }}</p>
                  <div v-if="metaLine(run)" class="et-meta">{{ metaLine(run) }}</div>
                  <!-- 验收明细：产品定义要求「如实标注哪几条未达标」，只给计数等于没验收 -->
                  <ul v-if="verdictList(run).length" class="et-verdicts">
                    <li v-for="v in verdictList(run)" :key="v.key" :class="v.state">
                      <span class="et-vmark">{{ v.mark }}</span>
                      <span class="et-vtext" :title="v.evidence ? `${v.criterion}（${v.evidence}）` : v.criterion">
                        {{ v.criterion }}
                      </span>
                    </li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { CloseOutlined, CompressOutlined, ExpandOutlined, RobotOutlined } from '@ant-design/icons-vue';
import type { SubagentRun } from '../composables/executionTimeline';
import { mergeTeamMembers, plainSummary } from '../composables/executionTimeline';

const props = defineProps<{
  runs: SubagentRun[];
  /** 打开时聚焦的成员（runKey）：滚动进视野 + 高亮一次 */
  focusKey?: string;
}>();

defineEmits<{
  (e: 'close'): void;
  (e: 'select-run', run: SubagentRun): void;
  (e: 'focus-chat'): void;
}>();

// ---- 宽度：默认停靠宽 / 可拖拽 / 放大按钮在「默认 ↔ 上限」之间切换 ----
// 上限 = 主内容区（2026-07-27 用户拍板）：最大化只铺满「顶栏之下 + 左侧栏之右」那块区域，
// 不覆盖应用顶栏与主导航栏。侧栏宽度实测（展开 280 / 收起 78），不写死。
const MIN_W = 320;
const MIN_H = 240;
const DEFAULT_W = 400;
const EDGE_GAP = 12; // 与 CSS 的 right/bottom 保持一致
const DOCK_TOP = 62; // 与 CSS 的 top 保持一致（应用顶栏之下）
const STORAGE_KEY = 'exec-team-panel-width';
/** 浮动态的位置与尺寸（拖动标题栏后启用）：与宽度分开存，停靠态不受影响 */
const STORAGE_BOX = 'exec-team-panel-box';

const viewportW = ref(typeof window === 'undefined' ? 1440 : window.innerWidth);
const navW = ref(280);

function measureNav() {
  if (typeof document === 'undefined') return;
  const el = document.querySelector('.primary-nav') as HTMLElement | null;
  navW.value = el ? Math.round(el.getBoundingClientRect().width) : 280;
}

const maxWidth = computed(() =>
  Math.max(MIN_W, viewportW.value - navW.value - EDGE_GAP * 2),
);

function clampWidth(v: number) {
  return Math.min(maxWidth.value, Math.max(MIN_W, Math.round(v)));
}

const stored = typeof localStorage === 'undefined' ? null : Number(localStorage.getItem(STORAGE_KEY));
const width = ref(clampWidth(stored && stored > 0 ? stored : DEFAULT_W));
const resizing = ref(false);

// ---- 停靠 / 浮动（2026-07-27 用户拍板：面板可拖动、可上下调高）----
// 停靠态：右侧贴边、上下撑满（top 62 / bottom 12），只调宽与上缘高度；
// 浮动态：拖过标题栏后变自由窗，位置尺寸都记住，双击标题栏或点「放大」回到停靠。
const docked = ref(true);
const pos = ref({ x: 0, y: DOCK_TOP });
const height = ref(0);
const dragging = ref(false);
const rootRef = ref<HTMLElement | null>(null);

const savedBox = (() => {
  try {
    const raw = localStorage.getItem(STORAGE_BOX);
    return raw ? (JSON.parse(raw) as { x: number; y: number; w: number; h: number }) : null;
  } catch {
    return null;
  }
})();
if (savedBox && savedBox.w > 0 && savedBox.h > 0) {
  docked.value = false;
  pos.value = { x: savedBox.x, y: savedBox.y };
  width.value = clampWidth(savedBox.w);
  height.value = savedBox.h;
}

const panelStyle = computed(() =>
  docked.value
    ? { width: `${width.value}px`, ...(height.value ? { top: `${pos.value.y}px` } : {}) }
    : {
        width: `${width.value}px`,
        height: `${height.value}px`,
        left: `${pos.value.x}px`,
        top: `${pos.value.y}px`,
        right: 'auto',
        bottom: 'auto',
      },
);

function persistBox() {
  try {
    if (docked.value) localStorage.removeItem(STORAGE_BOX);
    else
      localStorage.setItem(
        STORAGE_BOX,
        JSON.stringify({ x: pos.value.x, y: pos.value.y, w: width.value, h: height.value }),
      );
  } catch {
    /* 隐私模式/配额满：不持久化即可 */
  }
}

/** 保证面板始终有一块留在视口内（拖出屏幕就再也点不回来了） */
function clampPos(x: number, y: number) {
  const maxX = window.innerWidth - 120;
  const maxY = window.innerHeight - 60;
  return { x: Math.min(maxX, Math.max(-(width.value - 120), Math.round(x))), y: Math.min(maxY, Math.max(0, Math.round(y))) };
}

/** 回到右侧停靠（双击标题栏 / 放大按钮）：清掉浮动位置，恢复上下撑满 */
function redock() {
  docked.value = true;
  height.value = 0;
  pos.value = { x: 0, y: DOCK_TOP };
  persistBox();
}

function onHeadDown(ev: PointerEvent) {
  // 标题栏右侧按钮不触发拖动
  if ((ev.target as HTMLElement).closest('button')) return;
  const el = rootRef.value;
  if (!el) return;
  ev.preventDefault();
  const r = el.getBoundingClientRect();
  const offX = ev.clientX - r.left;
  const offY = ev.clientY - r.top;
  if (docked.value) {
    height.value = Math.round(r.height);
    pos.value = { x: Math.round(r.left), y: Math.round(r.top) };
  }
  let moved = false;
  const move = (e: PointerEvent) => {
    if (!moved && Math.abs(e.clientX - ev.clientX) + Math.abs(e.clientY - ev.clientY) < 3) return;
    // 首次真实位移才脱离停靠：单纯点标题栏不该把面板变成浮动窗
    if (!moved) {
      moved = true;
      dragging.value = true;
      docked.value = false;
    }
    pos.value = clampPos(e.clientX - offX, e.clientY - offY);
  };
  const up = () => {
    dragging.value = false;
    if (moved) persistBox();
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}
/** 宽到一定程度切换成全景排布（树形连线 + 卡片流式并排），窄时保持纵向清单 */
const wide = computed(() => width.value >= 620);
const atMax = computed(() => width.value >= maxWidth.value - 2);

function persistWidth() {
  try {
    localStorage.setItem(STORAGE_KEY, String(width.value));
  } catch {
    /* 隐私模式/配额满：宽度不持久化即可，不影响使用 */
  }
}

function setWidth(v: number) {
  width.value = clampWidth(v);
  persistWidth();
}

/** 放大 ↔ 还原窗口化：到顶即回默认停靠宽（用户拍板：放大后要能回到窗口化状态）。
    浮动态点放大先回停靠，再铺满主内容区——「最大」始终指主内容区，不覆盖顶栏/侧栏 */
function toggleMax() {
  measureNav();
  const wasFloating = !docked.value;
  if (wasFloating) redock();
  setWidth(wasFloating || !atMax.value ? maxWidth.value : DEFAULT_W);
}

type ResizeEdge = 'left' | 'top' | 'bottom' | 'corner';

function clampHeight(v: number) {
  return Math.max(MIN_H, Math.min(window.innerHeight - EDGE_GAP, Math.round(v)));
}

function onResizeStart(ev: PointerEvent, edge: ResizeEdge = 'left') {
  const el = rootRef.value;
  if (!el) return;
  ev.preventDefault();
  resizing.value = true;
  (ev.target as HTMLElement).setPointerCapture?.(ev.pointerId);
  const r = el.getBoundingClientRect();
  const right = r.right;
  const bottom = r.bottom;
  const move = (e: PointerEvent) => {
    if (edge === 'left' || edge === 'corner') {
      if (docked.value) {
        // 停靠态贴右缘（right: 12px）：宽度 = 视口右缘到指针的距离 - 外边距
        width.value = clampWidth(window.innerWidth - e.clientX - EDGE_GAP);
      } else if (edge === 'left') {
        // 浮动态拖左缘：右缘钉住，左缘跟手
        const w = clampWidth(right - e.clientX);
        pos.value = { ...pos.value, x: Math.round(right - w) };
        width.value = w;
      } else {
        width.value = clampWidth(e.clientX - r.left);
      }
    }
    if (edge === 'top') {
      // 上缘：停靠态改 top（下缘贴底不动）；浮动态上缘跟手、下缘钉住
      const newTop = Math.max(0, Math.min(bottom - MIN_H, e.clientY));
      height.value = clampHeight(bottom - newTop);
      pos.value = { ...pos.value, y: Math.round(newTop) };
    }
    if (edge === 'bottom' || edge === 'corner') {
      if (!docked.value) height.value = clampHeight(e.clientY - r.top);
    }
  };
  const up = (e: PointerEvent) => {
    resizing.value = false;
    persistWidth();
    persistBox();
    (ev.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}

/** 键盘可达（把手聚焦后左右方向键调宽，步进 32px） */
function onResizeKey(ev: KeyboardEvent) {
  if (ev.key === 'ArrowLeft') {
    ev.preventDefault();
    setWidth(width.value + 32);
  } else if (ev.key === 'ArrowRight') {
    ev.preventDefault();
    setWidth(width.value - 32);
  }
}

/** 团队成员 = 按身份去重后的运行档（2026-07-28）：runs 是「每次委派一条」，同一个子智能体
    被调用 3 次就会画出 3 张同名卡片，读起来像 3 个人。合并逻辑见 mergeTeamMembers。 */
const members = computed(() => mergeTeamMembers(props.runs));

const anyRunning = computed(() => members.value.some((r) => r.status === 'running'));

/** 主管身份随场景变（模型委派时自己起名，如「审阅协调人」）；未提供才回退 AXIOM Agent。
    取本轮首个带 managerRole 的委派帧——同一轮多次委派携带同一个值 */
const managerName = computed(
  () => members.value.find((r) => r.managerRole)?.managerRole || 'AXIOM Agent',
);

/** 主管动作行：真实状态推导，不表演——有成员在跑=等待它交付；全部收尾=汇总。
 *
 *  「正在协调 N 名成员」那条分支已删除（2026-07-27）：它要求同时有 ≥2 个成员处于
 *  running，而后端根本产生不了这种状态——call_subagent 是 stream_execute 且
 *  是否并发由 ToolSpec 的 parallel_safe 和资源锁决定。
 *  留着它就是本面板设计底线①「真实事件投影，绝不表演」的反例：一句永远不会出现、
 *  却让人以为多成员协作还活着的文案。
 *
 *  注意 members.length > 1 目前**不可达**（单智能体锁 + 串行执行；同一身份的多次委派已在
 *  mergeTeamMembers 里合成一张卡）。多卡布局与 drawWire 的横向连线仍保留：它们是恢复
 *  多成员协作时唯一不用重写的部分，且没有任何「表演」文案依赖它。 */
const managerLine = computed(() => {
  const running = members.value.filter((r) => r.status === 'running');
  if (running.length) {
    const r = running[0];
    return `正在等待「${r.roleName || r.name}」交付`;
  }
  if (members.value.length) return '成员交付已收齐，正在汇总';
  return '';
});

function stateClass(run: SubagentRun) {
  if (run.status === 'running') return 'running';
  if (run.status === 'failed' && !run.interrupted) return 'failed';
  return '';
}

function stateLabel(run: SubagentRun) {
  if (run.status === 'running') return '进行中';
  if (run.interrupted) return '已中断';
  if (run.status === 'failed') return '失败';
  const acc = run.review || run.acceptance;
  if (acc && acc.total) return `验收 ${acc.passedCount}/${acc.total}`;
  return '已完成';
}

/** 动作行：运行中=最新真实节点；收尾=委派任务一句话。不编内容、不留空槽 */
function actionLine(run: SubagentRun) {
  if (run.status === 'running') {
    const last = run.nodes.length ? run.nodes[run.nodes.length - 1] : null;
    if (run.reasoning) {
      const tail = run.reasoning.replace(/\s+/g, ' ').trim().slice(-48);
      return last?.label ? `正在：${last.label} · ${tail}` : tail;
    }
    return last?.label ? `正在：${last.label}` : '正在执行委派任务';
  }
  // 交付常是整篇 Markdown 报告：压成一行纯文本，别把 ###/** 语法露给用户
  return plainSummary(run.task, 90) || plainSummary(run.preview, 90) || '已交付';
}

function metaLine(run: SubagentRun) {
  const parts: string[] = [];
  if (run.files?.length) parts.push(`交付 ${run.files.length} 个文件`);
  if (run.subtasks?.length) parts.push(`子任务 ×${run.subtasks.length}`);
  if ((run.callCount || 1) > 1) parts.push(`委派 ${run.callCount} 次`);
  return parts.join(' · ');
}

/** 验收明细（2026-07-28）：此前全仓无任何组件渲染 verdicts，验收环节对用户只是一个数字。
    收尾态才展开——运行中还没有裁定。未达标/无法核验的排前面，最多 5 条（后端已封顶）。 */
function verdictList(run: SubagentRun) {
  if (run.status === 'running') return [];
  const verdicts = run.review?.verdicts || [];
  return [...verdicts]
    .sort((a, b) => Number(a.passed === true) - Number(b.passed === true))
    .slice(0, 5)
    .map((v) => ({
      key: `${v.criterion}-${String(v.passed)}`,
      criterion: v.criterion,
      evidence: v.evidence || '',
      state: v.passed === true ? 'pass' : v.passed === false ? 'fail' : 'unknown',
      mark: v.passed === true ? '达标' : v.passed === false ? '未达标' : '无法核验',
    }));
}

// ---- 聚焦滚动 + 宽版连线（按第一行卡片实际位置绘制，成员数/宽度变化都对齐） ----
const wireRef = ref<HTMLElement | null>(null);
const teamRef = ref<HTMLElement | null>(null);
const cardRefs = new Map<string, HTMLElement>();

function setCardRef(key: string, el: unknown) {
  if (el instanceof HTMLElement) cardRefs.set(key, el);
  else cardRefs.delete(key);
}

function drawWire() {
  const wire = wireRef.value;
  const team = teamRef.value;
  if (!wire || !team || !wide.value) return;
  wire.innerHTML = '';
  const cards = Array.from(team.children) as HTMLElement[];
  if (!cards.length) return;
  const minTop = Math.min(...cards.map((c) => c.offsetTop));
  const xs = cards
    .filter((c) => c.offsetTop < minTop + 5)
    .map((c) => c.offsetLeft + c.offsetWidth / 2);
  const mk = (tag: string, css: string) => {
    const el = document.createElement(tag);
    el.style.cssText = css;
    wire.appendChild(el);
  };
  mk('i', 'left:50%;top:0;height:20px');
  if (xs.length > 1) {
    const left = Math.min(...xs);
    mk('u', `left:${left}px;width:${Math.max(...xs) - left}px;top:20px`);
  }
  xs.forEach((x) => {
    mk('i', `left:${x}px;top:20px;height:20px`);
    mk('s', `left:${x - 2.5}px;top:17.5px`);
  });
}

function scrollFocusIntoView() {
  if (!props.focusKey) return;
  nextTick(() => cardRefs.get(props.focusKey!)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }));
}

function onWindowResize() {
  viewportW.value = window.innerWidth;
  measureNav();
  width.value = clampWidth(width.value);
  drawWire();
}

/** 侧栏展开/收起会改变主内容区宽度：跟随重算上限，最大化态同步贴合 */
let navObserver: ResizeObserver | null = null;

watch(() => [props.runs.length, wide.value, width.value], () => nextTick(drawWire));
watch(() => props.focusKey, scrollFocusIntoView);
onMounted(() => {
  measureNav();
  width.value = clampWidth(width.value);
  nextTick(drawWire);
  scrollFocusIntoView();
  window.addEventListener('resize', onWindowResize);
  const nav = document.querySelector('.primary-nav');
  if (nav && typeof ResizeObserver !== 'undefined') {
    const wasMax = () => width.value >= maxWidth.value - 2;
    navObserver = new ResizeObserver(() => {
      const keepMax = wasMax();
      measureNav();
      // 最大化态跟着主内容区一起变；窗口化态只做上限收敛，不擅自改用户调好的宽度
      setWidth(keepMax ? maxWidth.value : width.value);
      nextTick(drawWire);
    });
    navObserver.observe(nav);
  }
});
onBeforeUnmount(() => {
  window.removeEventListener('resize', onWindowResize);
  navObserver?.disconnect();
  navObserver = null;
});
</script>

<style scoped>
/* 令牌与「任务与协作」面板同源：克制黑白、蓝仅运行态、子智能体紫方块（2026-07-27 执行团队二期）。
   形态：右侧停靠、可拖宽的常驻面板——不做全屏覆盖（用户拍板：主对话必须始终可读） */
.exec-team {
  position: fixed;
  z-index: 900;
  top: 62px;
  right: 12px;
  bottom: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #e3e5e9;
  border-radius: 18px;
  /* 面板身体与全景同一张纸（v9 原型的浅灰底），卡片浮在纸上 */
  background: #f7f8fa;
  color: #202228;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.14), 0 2px 8px rgba(15, 23, 42, 0.05);
}

.exec-team.resizing,
.exec-team.floating.dragging {
  user-select: none;
}

/* 浮动态：拖过标题栏后脱离右侧停靠，成为自由窗（位置尺寸都由内联样式接管） */
.exec-team.floating {
  border-radius: 16px;
}

/* 拖拽把手：命中区 10px，视觉上只在悬停/拖拽时显出一条细线 */
.et-resizer {
  position: absolute;
  z-index: 2;
  background: transparent;
}

.et-resizer-x {
  top: 0;
  bottom: 0;
  left: -4px;
  width: 10px;
  cursor: col-resize;
}

.et-resizer-y {
  left: 0;
  right: 0;
  height: 10px;
  cursor: row-resize;
}

.et-resizer-top {
  top: -4px;
}

.et-resizer-bottom {
  bottom: -4px;
}

.et-resizer-corner {
  right: -3px;
  bottom: -3px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
}

.et-resizer-x::after,
.et-resizer-y::after {
  content: '';
  position: absolute;
  border-radius: 2px;
  background: transparent;
  transition: background 0.18s ease;
}

.et-resizer-x::after {
  top: 12px;
  bottom: 12px;
  left: 4px;
  width: 2px;
}

.et-resizer-y::after {
  left: 16px;
  right: 16px;
  top: 4px;
  height: 2px;
}

.et-resizer-x:hover::after,
.et-resizer-x:focus-visible::after,
.et-resizer-y:hover::after,
.resizing .et-resizer-x::after {
  background: #c7d0f7;
}

/* 右下角：两道短斜线的经典缩放握把，只在悬停时加深 */
.et-resizer-corner::after {
  content: '';
  position: absolute;
  right: 4px;
  bottom: 4px;
  width: 7px;
  height: 7px;
  border-right: 2px solid #d3d6dd;
  border-bottom: 2px solid #d3d6dd;
  border-bottom-right-radius: 3px;
  transition: border-color 0.18s ease;
}

.et-resizer-corner:hover::after {
  border-color: #4f6ef7;
}

.et-resizer:focus-visible {
  outline: none;
}

.et-head {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid #eff0f2;
  border-radius: 18px 18px 0 0;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(12px);
  /* 标题栏即拖动把手（按钮区除外，见 onHeadDown） */
  cursor: grab;
}

.dragging .et-head {
  cursor: grabbing;
}

.et-head strong {
  font-size: 13px;
  font-weight: 600;
}

.et-head-actions {
  display: flex;
  gap: 2px;
}

.et-head-actions button {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #8a8f98;
  cursor: pointer;
}

.et-head-actions button:hover {
  background: #f1f2f4;
  color: #202228;
}

.et-body {
  flex: 1 1 auto;
  overflow-y: auto;
  scrollbar-width: thin;
}

.et-blank {
  padding: 48px 16px;
  color: #a8adb6;
  font-size: 12px;
  text-align: center;
}

.et-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 18px 14px 20px;
}

.is-wide .et-stage {
  padding: 32px 24px;
}

.et-mgr-wrap {
  display: flex;
  width: 100%;
  justify-content: center;
}

/* 卡片结构（2026-07-27 用户反馈「长方形怪」修正）：头像独立成列、文字成块在右，
   卡片读作一个节点而不是被拉长的横条；文字左边界与名字对齐，视觉重心不再全压在顶行 */
.et-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 11px;
  align-items: start;
  background: #fff;
  border: 1px solid #e3e5e9;
  border-radius: 14px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
  cursor: pointer;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
  outline: none;
}

.et-main {
  min-width: 0;
}

.et-card:hover {
  border-color: #d3d6dd;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
}

.et-card:focus-visible {
  outline: 2px solid #4f6ef7;
  outline-offset: 2px;
}

.et-card.focused {
  border-color: #4f6ef7;
}

/* 层级差（2026-07-27 用户反馈）：主管不是成员的同级——卡更宽更重、头像实心紫，
   成员卡收窄一档、头像浅底，一眼读出「上级 → 下属」而不是并列 */
.et-mgr {
  width: 100%;
  /* 收窄 + 加高（不再是又宽又扁的横条）：节点感 */
  max-width: 300px;
  padding: 14px 16px;
  border-color: #dcdee3;
  box-shadow: 0 3px 12px rgba(15, 23, 42, 0.07);
}

.et-mgr .et-sq {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  background: #6d62b5;
  color: #fff;
  font-size: 15px;
}

.et-mgr .et-nm {
  font-size: 14px;
}

.et-mgr .et-say {
  font-size: 12.5px;
}

.is-wide .et-mgr {
  max-width: 400px;
}

.et-row {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 23px;
}

.et-sq {
  display: grid;
  width: 26px;
  height: 26px;
  place-items: center;
  border-radius: 9px;
  background: #f0effc;
  color: #6d62b5;
  font-size: 13px;
}

.et-sq.running {
  background: #edf3ff;
  color: #4f6ef7;
}

.et-nm {
  font-size: 12.5px;
  font-weight: 600;
  color: #2b2e34;
}

.et-st {
  margin-left: auto;
  color: #a0a5ae;
  font-size: 10.5px;
  white-space: nowrap;
}

.et-st.running {
  color: #4f6ef7;
}

.et-st.failed {
  color: #b84d4d;
}

.et-say,
.et-act {
  margin: 5px 0 0;
  overflow: hidden;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  /* 一行收敛（v9 原型的文案密度）：完整文本悬停 title 可见 */
  -webkit-line-clamp: 1;
}

.et-meta {
  margin-top: 6px;
  color: #a0a5ae;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

/* 验收明细：克制黑白，未达标只用一枚深色标签区分，不上大红横幅（用户侧风格约束） */
.et-verdicts {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
}

.et-verdicts li {
  display: flex;
  gap: 6px;
  align-items: baseline;
  margin-top: 4px;
  color: #6b7280;
  font-size: 11px;
  line-height: 1.6;
}

.et-vmark {
  flex: none;
  padding: 0 5px;
  border: 1px solid #e3e5e9;
  border-radius: 5px;
  color: #8a8f98;
  font-size: 10px;
  white-space: nowrap;
}

.et-verdicts li.fail .et-vmark {
  border-color: #e6cccc;
  background: #fbf3f3;
  color: #b84d4d;
}

.et-verdicts li.unknown .et-vmark {
  border-style: dashed;
}

.et-vtext {
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

/* 呼吸点：与任务面板 task-step-dot 同款节奏（运行态唯一动效来源） */
.et-dot {
  width: 8px;
  height: 8px;
  margin-left: auto;
  border-radius: 50%;
  background: linear-gradient(135deg, #a9b8fb 0%, #4f6ef7 45%, #2c3e97 75%, #a9b8fb 100%);
  background-size: 300% 300%;
  animation: et-breathe 1s infinite;
}

@keyframes et-breathe {
  0% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 0% 0%;
  }
  27% {
    border-radius: 30%;
    transform: scale(1.18);
    background-position: 50% 50%;
  }
  55% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 80% 80%;
  }
  100% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 0% 0%;
  }
}

.et-grp {
  position: relative;
  width: 100%;
}

/* 窄版：居中窄卡 + 每张成员卡到上级之间一段看得见的连线（带接点圆点） */
.et-team {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.et-wk {
  position: relative;
  /* 比主管卡窄一档（下属层）：连线两侧留出可见的缩进 */
  width: min(264px, 82%);
  margin-top: 22px;
  padding: 12px 14px;
}

.et-wk .et-sq {
  width: 23px;
  height: 23px;
  border-radius: 8px;
  font-size: 12px;
}

.et-wk .et-nm {
  font-size: 12px;
}

.et-wk::before {
  content: '';
  position: absolute;
  top: -24px;
  left: 50%;
  height: 24px;
  border-left: 1px solid #d3d6dd;
}

.et-wk::after {
  content: '';
  position: absolute;
  top: -14px;
  left: calc(50% - 2.5px);
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #d3d6dd;
}

/* 宽版全景：居中流式 + 脚本连线（拖到 620px 以上自动切换） */
.is-wide .et-wire {
  position: relative;
  height: 40px;
  width: 100%;
}

.is-wide .et-wire :deep(i) {
  position: absolute;
  border-left: 1px solid #d3d6dd;
}

.is-wide .et-wire :deep(u) {
  position: absolute;
  border-top: 1px solid #d3d6dd;
}

.is-wide .et-wire :deep(s) {
  position: absolute;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #d3d6dd;
}

.is-wide .et-team {
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
}

.is-wide .et-wk {
  width: 236px;
  flex: 0 0 auto;
  margin-top: 0;
  padding: 13px 14px;
}

.is-wide .et-wk::before,
.is-wide .et-wk::after {
  display: none;
}

@media (prefers-reduced-motion: reduce) {
  .et-dot {
    animation: none;
  }

  .et-card {
    transition: none;
  }
}
</style>
