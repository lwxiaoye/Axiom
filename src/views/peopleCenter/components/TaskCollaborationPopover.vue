<template>
  <div ref="rootRef" class="task-collaboration">
    <button
      type="button"
      :class="['task-collaboration-trigger', { active: open, 'task-active': hasTaskActivity }]"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :aria-label="hasTaskActivity ? '查看任务计划与子智能体协作' : '查看任务步骤与子智能体协作'"
      @click="togglePanel"
    >
      <span class="tct-icon">
        <span v-if="hasTaskActivity" class="tct-plan-icon" aria-hidden="true">
          <span v-for="line in 3" :key="line" class="tct-plan-line">
            <i class="tct-plan-dot"></i>
            <i class="tct-plan-bar"></i>
          </span>
        </span>
        <UnorderedListOutlined v-else />
      </span>
      <span class="tct-label">{{ hasTaskActivity ? '任务计划' : '任务协作' }}</span>
    </button>

    <!-- Teleport 到 body：顶栏 z-index:100 低于对话态输入框 1000，留在 header 里会被
         输入框盖住底部；且入口已在左侧（对话历史/记忆旁），不能再按「头像旁」右锚定。 -->
    <Teleport to="body">
    <transition name="task-collaboration-pop">
      <div
        v-if="open"
        ref="panelRef"
        class="task-collaboration-panel"
        role="dialog"
        aria-label="任务与协作"
        :style="panelStyle"
      >
        <header class="task-collaboration-head">
          <strong>任务与协作</strong>
          <button type="button" aria-label="关闭任务与协作" @click="open = false">
            <CloseOutlined />
          </button>
        </header>

        <!-- 与消息内执行卡同一份数据（deriveRunPanel）：计划 / 委派
             全部是最新一轮 assistant 消息状态的镜像，本面板不再维护任何独立聚合。
             执行时间线只在消息内执行卡展示，面板不再重复（2026-07-15 用户拍板）。 -->

        <section v-if="!panel.settled" class="task-collaboration-section">
          <div v-if="panel.plan.length" class="aicss-todo">
            <div v-if="contractBanner" class="task-goal-contract">
              <strong>{{ contractBanner.deliverable || '目标' }}</strong>
              <span :title="contractBanner.goal">{{ contractBanner.goal }}</span>
              <small v-if="contractBanner.criteria" :title="contractBanner.criteria">验收：{{ contractBanner.criteria }}</small>
            </div>
            <button
              type="button"
              class="aicss-todo-head"
              :aria-expanded="!planCollapsed"
              aria-label="折叠任务计划"
              :title="panel.planDiverged ? '已偏离批准版本' : undefined"
              @click="planCollapsed = !planCollapsed"
            >
              <span class="aicss-todo-head-icon" aria-hidden="true">
                <svg v-if="planAllDone" class="aicss-todo-head-check" viewBox="0 0 24 24" width="16" height="16">
                  <path fill-rule="evenodd" clip-rule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12Zm13.36-1.814a.75.75 0 1 0-1.22-.872l-3.236 4.53L9.53 12.22a.75.75 0 0 0-1.06 1.06l2.25 2.25a.75.75 0 0 0 1.14-.094l3.75-5.25Z" fill="currentColor" />
                </svg>
                <span v-else-if="planRunning" class="aicss-todo-head-pie" :style="{ '--todo-pie': planPct + '%' }">
                  <svg class="aicss-todo-head-pie-ring" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-dasharray="2.2 4.4" stroke-linecap="round" />
                  </svg>
                </span>
                <svg v-else class="aicss-todo-list-icon" viewBox="0 0 24 24" width="16" height="16">
                  <path d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <PremiumChevron class="aicss-todo-chevron" :direction="planCollapsed ? 'right' : 'down'" :size="14" interactive />
              </span>
              <span class="aicss-todo-title">步骤</span>
              <span class="aicss-todo-count">{{ planCompleted }}/{{ panel.plan.length }}</span>
            </button>
            <div class="aicss-todo-collapsible" :class="{ 'is-collapsed': planCollapsed }">
              <div class="aicss-todo-inner">
                <ul class="aicss-todo-list">
                  <li
                    v-for="(step, i) in panel.plan"
                    :key="step.key"
                    :class="['aicss-todo-item', planTodoClass(step.status)]"
                    :style="{ '--i': i }"
                  >
                    <span class="aicss-todo-icon-wrap" aria-hidden="true">
                      <svg class="aicss-todo-icon" :class="{ on: planTodoClass(step.status) === 'pending' }" viewBox="0 0 24 24" width="16" height="16">
                        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8" stroke-dasharray="1.8 3.6" stroke-linecap="round" />
                      </svg>
                      <svg class="aicss-todo-icon strong" :class="{ on: planTodoClass(step.status) === 'active' }" viewBox="0 0 24 24" width="16" height="16">
                        <path d="m12.75 15 3-3m0 0-3-3m3 3h-7.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                      <svg class="aicss-todo-icon" :class="{ on: planTodoClass(step.status) === 'done' }" viewBox="0 0 24 24" width="16" height="16">
                        <path d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                      <svg class="aicss-todo-icon fail" :class="{ on: planTodoClass(step.status) === 'failed' }" viewBox="0 0 24 24" width="16" height="16">
                        <path d="m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                      </svg>
                      <MinusCircleOutlined
                        :class="['aicss-todo-icon', 'invalidated', { on: planTodoClass(step.status) === 'invalidated' }]"
                      />
                    </span>
                    <span class="aicss-todo-label" :data-label="step.title">{{ step.title }}</span>
                    <small v-if="showStepDetail(step)" class="aicss-todo-detail" :title="step.detail">{{ step.detail }}</small>
                    <small v-if="stepBlocked(step)" class="aicss-todo-blocked">等待上一步</small>
                    <small v-if="step.acceptance" class="aicss-todo-acceptance" :title="step.acceptance">验收：{{ step.acceptance }}</small>
                  </li>
                </ul>
              </div>
            </div>
          </div>
          <template v-else>
            <div class="task-collaboration-title">
              <span>{{ planSectionTitle }}</span>
            </div>
            <p class="task-collaboration-blank">
              {{ panel.running ? '正在执行，步骤就绪后会显示在这里' : '本轮没有任务计划' }}
            </p>
          </template>
        </section>

        <section v-if="teamMembers.length" class="task-collaboration-section subagent-section">
          <div class="task-collaboration-title">
            <!-- 执行团队（2026-07-27 二期）：分区即入口——标题可点进团队全景，
                 成员行点击进全景并聚焦该成员（先看位置，再决定下钻 @ 窗） -->
            <button
              type="button"
              class="team-open"
              aria-label="查看执行团队全景"
              @click="openTeam()"
            >
              <span>执行团队</span>
              <PremiumChevron direction="right" :size="14" interactive />
            </button>
            <!-- 角标 = 团队规模（去重后的成员数），不是委派次数：同一个子智能体被调用 3 次
                 时这里曾显示 3，读起来像组了 3 个人（2026-07-28 修复） -->
            <em>{{ teamMembers.length }}</em>
          </div>
          <button
            v-for="run in teamMembers"
            :key="run.runKey"
            type="button"
            class="collaboration-run"
            @click="openTeam(run)"
          >
            <span :class="['collaboration-run-icon', run.status]" aria-hidden="true">
              <RobotOutlined />
            </span>
            <span class="collaboration-run-copy">
              <strong>{{ run.roleName || run.name || '子智能体' }}</strong>
              <small>{{ runSummary(run) }}</small>
            </span>
            <span class="collaboration-run-state">
              <span>{{ run.status === 'running' ? '进行中' : run.interrupted ? '已中断' : run.status === 'failed' ? '失败' : '完成' }}</span>
              <PremiumChevron direction="right" :size="14" interactive />
            </span>
          </button>
        </section>
      </div>
    </transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { onClickOutside } from '@vueuse/core';
import {
  CloseOutlined,
  MinusCircleOutlined,
  RobotOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons-vue';
import PremiumChevron from './PremiumChevron.vue';
import type {
  PlanItemStatus,
  RunPanelModel,
  SubagentRun,
} from '../composables/executionTimeline';
import { mergeTeamMembers, plainSummary } from '../composables/executionTimeline';

const props = defineProps<{
  /** 统一任务运行面板数据（deriveRunPanel 派生）：与消息内执行卡同一份状态，无独立聚合 */
  panel: RunPanelModel;
  conversationKey?: string;
}>();

const emit = defineEmits<{
  /** 执行团队全景（2026-07-27 二期）：无参=打开全景；带 runKey=打开并聚焦该成员。
      原 selectRun（成员行直接下钻 @ 窗）已由「全景聚焦→卡片下钻」两段式取代 */
  (e: 'openTeam', runKey?: string): void;
}>();

const rootRef = ref<HTMLElement | null>(null);
const panelRef = ref<HTMLElement | null>(null);
const open = ref(false);
const panelStyle = ref<Record<string, string>>({});

const PANEL_WIDTH = 380;
const PANEL_GAP = 8;
const VIEWPORT_PAD = 12;
/** 对话态底部输入框（bottom:16 + 约 90px 高），面板停在它上方，既不被盖住也不挡输入。 */
const COMPOSER_CLEARANCE = 120;

function placePanel() {
  const trigger = rootRef.value?.querySelector('button.task-collaboration-trigger') || rootRef.value;
  if (!trigger || typeof window === 'undefined') return;
  const rect = trigger.getBoundingClientRect();
  const width = Math.min(PANEL_WIDTH, window.innerWidth - VIEWPORT_PAD * 2);
  const top = Math.round(rect.bottom + PANEL_GAP);
  const maxHeight = Math.max(240, window.innerHeight - top - COMPOSER_CLEARANCE);
  let left = rect.left;
  left = Math.max(VIEWPORT_PAD, Math.min(left, window.innerWidth - width - VIEWPORT_PAD));
  panelStyle.value = {
    top: `${top}px`,
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.round(maxHeight)}px`,
  };
}

function onWinPlace() {
  if (open.value) placePanel();
}

function togglePanel() {
  if (open.value) {
    open.value = false;
    return;
  }
  placePanel();
  open.value = true;
  nextTick(() => {
    placePanel();
    requestAnimationFrame(placePanel);
  });
}

/** 任务协作只展示语义整体计划，标题固定为「任务计划」 */
const planSectionTitle = computed(() => '任务计划');

// 禁止自动弹出。用户反馈执行一半面板自己打开打断阅读；
// 换会话时收起，任务计划与委派全部进入终态后同步撤掉顶栏动效。
watch(
  () => props.conversationKey || '',
  () => {
    open.value = false;
  },
);

/** 执行团队成员 = 按身份去重后的委派档（同一子智能体的多次委派合成一张卡，见 mergeTeamMembers） */
const teamMembers = computed(() => mergeTeamMembers(props.panel.subagentRuns));
const hasOpenPlanSteps = computed(() => !props.panel.settled && props.panel.plan.some(
  (step) => step.status === 'pending' || step.status === 'running',
));
const hasRunningTeam = computed(() => teamMembers.value.some((run) => run.status === 'running'));
const hasTaskActivity = computed(() => hasOpenPlanSteps.value || hasRunningTeam.value);

const planCompleted = computed(
  () => props.panel.plan.filter((step) => (
    step.status === 'completed' || step.status === 'skipped'
  )).length,
);

/** AIcss To-do 折叠态 */
const planCollapsed = ref(false);
watch(
  () => props.conversationKey || '',
  () => {
    planCollapsed.value = false;
  },
);

const planAllDone = computed(
  () => props.panel.plan.length > 0
    && props.panel.plan.every((s) => (
      s.status === 'completed' || s.status === 'failed'
      || s.status === 'skipped' || s.status === 'invalidated'
    )),
);
const planRunning = computed(
  () => !props.panel.settled && props.panel.plan.some((s) => s.status === 'running'),
);
const planPct = computed(() => {
  const n = props.panel.plan.length || 1;
  return Math.round((planCompleted.value / n) * 100);
});

const contractBanner = computed(() => {
  const contract = props.panel.goalContract;
  const goal = String(contract?.goal || '').trim();
  if (!goal) return null;
  return {
    goal,
    deliverable: String(contract?.deliverable || '').trim(),
    criteria: (contract?.success_criteria || []).filter(Boolean).slice(0, 2).join('；'),
  };
});

function planTodoClass(status: PlanItemStatus): 'done' | 'active' | 'failed' | 'invalidated' | 'pending' {
  if (status === 'completed' || status === 'skipped') return 'done';
  if (status === 'running') return 'active';
  if (status === 'failed') return 'failed';
  if (status === 'invalidated') return 'invalidated';
  return 'pending';
}

function showStepDetail(step: { status?: string; detail?: string }) {
  if (!step.detail) return false;
  const kind = planTodoClass((step.status || 'pending') as PlanItemStatus);
  return kind === 'active' || kind === 'failed';
}

function stepBlocked(step: { blocked?: boolean; detail?: string; status?: string }) {
  if (step.status && step.status !== 'pending') return false;
  if (String(step.detail || '').includes('等待上一步')) return false;
  return Boolean(step.blocked);
}

/** 副标题=人话一句话（执行团队口径）：不倒 Markdown 原文、不复述整篇报告。
 *  运行中=当前动作；已完成=验收计数 + 交付摘要；失败=错因。 */
function runSummary(run: SubagentRun) {
  if (run.status === 'failed' && !run.interrupted) {
    return plainSummary(run.error || run.preview) || '子智能体执行失败';
  }
  if (run.interrupted) return '委派记录已中断（未完成，并非任务失败）';
  if (run.status === 'running') {
    const last = run.nodes.length ? run.nodes[run.nodes.length - 1] : null;
    if (last?.label) return `正在：${last.label}`;
    return plainSummary(run.task) || '正在执行主对话委派的任务';
  }
  const acc = run.review || run.acceptance;
  const gist = plainSummary(run.preview || run.output, 60) || plainSummary(run.task, 60);
  const passed = acc && acc.total ? `验收 ${acc.passedCount}/${acc.total}` : '';
  return [passed, gist].filter(Boolean).join(' · ') || '已完成委派任务';
}

function openTeam(run?: SubagentRun) {
  open.value = false;
  emit('openTeam', run?.runKey);
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') open.value = false;
}

onClickOutside(rootRef, () => {
  open.value = false;
}, { ignore: [panelRef] });
watch(() => props.conversationKey, () => {
  open.value = false;
});
onMounted(() => {
  document.addEventListener('keydown', onKeydown);
  window.addEventListener('resize', onWinPlace, { passive: true });
  window.addEventListener('scroll', onWinPlace, { passive: true, capture: true });
  window.addEventListener('transitionend', onWinPlace, true);
});
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown);
  window.removeEventListener('resize', onWinPlace);
  window.removeEventListener('scroll', onWinPlace, true);
  window.removeEventListener('transitionend', onWinPlace, true);
});
</script>

<style scoped>
.task-collaboration {
  position: relative;
}

/* 与顶栏「新对话/对话历史/记忆」（.history-trigger）同款幽灵按钮：图标 + 文案，
   透明底、悬停/展开描边高亮，不再是独立的方框图标钮。 */
.task-collaboration-trigger {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: #4d525c;
  font-size: 14px;
  cursor: pointer;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.task-collaboration-trigger:hover,
.task-collaboration-trigger.active {
  border-color: #e1e2e7;
  background: #f5f5f7;
  color: #111;
}

.task-collaboration-trigger.task-active,
.task-collaboration-trigger.task-active:hover,
.task-collaboration-trigger.task-active.active {
  border-color: transparent;
  background: transparent;
  color: #4d525c;
}

.task-collaboration-trigger.task-active:hover,
.task-collaboration-trigger.task-active.active {
  border-color: #d4e8fa;
  background: #f3f8fd;
}

.tct-icon {
  position: relative;
  display: inline-flex;
  align-items: center;
  font-size: 15px;
}

.tct-label {
  line-height: 1;
}

.tct-plan-icon {
  display: inline-flex;
  width: 17px;
  height: 17px;
  flex-direction: column;
  justify-content: space-between;
  padding: 2px 0;
}

.tct-plan-line {
  display: grid;
  grid-template-columns: 3px 1fr;
  align-items: center;
  gap: 3px;
  /* 与输入框 composer-glow 同源：#b4daff / #60a8f0，不用顶栏选中靛蓝 */
  color: #b4daff;
  animation: tct-plan-line-highlight 1.9s linear infinite;
}

.tct-plan-line:nth-child(2) { animation-delay: 0.3s; }
.tct-plan-line:nth-child(3) { animation-delay: 0.6s; }

.tct-plan-dot {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
}

.tct-plan-bar {
  width: 11px;
  height: 2px;
  border-radius: 1px;
  background: currentColor;
}

@keyframes tct-plan-line-highlight {
  0%, 52.5% { color: #b4daff; }
  52.6%, 68.3% { color: #60a8f0; }
  68.4%, 100% { color: #b4daff; }
}

/* 与 .history-trigger 一致：窄屏（≤980px）只留图标 */
@media (max-width: 980px) {
  .tct-label {
    display: none;
  }
}

.task-collaboration-panel {
  position: fixed;
  z-index: 2500;
  overflow-y: auto;
  border: 1px solid #e3e5e9;
  border-radius: 18px;
  background: #fff;
  color: #202228;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.14), 0 2px 8px rgba(15, 23, 42, 0.05);
  scrollbar-width: thin;
}

.task-collaboration-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid #eff0f2;
  border-radius: 18px 18px 0 0;
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(12px);
}

.task-collaboration-head strong {
  font-size: 13px;
  font-weight: 600;
}

.task-collaboration-head button {
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

.task-collaboration-head button:hover {
  background: #f1f2f4;
  color: #202228;
}

.task-collaboration-section {
  padding: 12px 16px 14px;
  border-bottom: 1px solid #eff0f2;
}

.task-collaboration-section:last-child {
  border-bottom: 0;
}

.task-collaboration-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 7px;
  padding: 0 2px;
  color: #8a8f98;
  font-size: 12px;
}

.task-collaboration-title em {
  font-size: 11px;
  font-style: normal;
}

/* 执行团队分区标题即入口（幽灵按钮：悬停变实、箭头提示可进全景） */
.team-open {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin: -2px -6px;
  padding: 2px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #8a8f98;
  font-size: 12px;
  cursor: pointer;
}

.team-open:hover {
  background: #f1f2f4;
  color: #202228;
}

.team-open :deep(.anticon) {
  font-size: 10px;
}


/* ===== 任务步骤（克制黑白，去掉内层卡片和版本号噪音）===== */
.aicss-todo {
  font-size: 13px;
  color: #1a1a1a;
  background: transparent;
  border-radius: 0;
  padding: 0;
  box-shadow: none;
}
.aicss-todo-head {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: #1a1a1a;
  font-size: 13px;
  min-height: 22px;
  text-align: left;
}
.aicss-todo-head-icon {
  position: relative;
  width: 16px;
  height: 16px;
  flex: none;
  color: #a1a1a1;
}
.aicss-todo-list-icon,
.aicss-todo-chevron,
.aicss-todo-head-check {
  position: absolute;
  inset: 0;
  margin: auto;
  transition: opacity 140ms ease;
}
.aicss-todo-list-icon,
.aicss-todo-chevron {
  width: 13px;
  height: 13px;
}
.aicss-todo-head-check {
  width: 16px;
  height: 16px;
  color: #15a06a;
}
.aicss-todo-chevron {
  opacity: 0;
  transition: opacity 140ms ease;
}
.aicss-todo-head:hover .aicss-todo-list-icon,
.aicss-todo-head:hover .aicss-todo-head-pie,
.aicss-todo-head:hover .aicss-todo-head-check {
  opacity: 0;
}
.aicss-todo-head:hover .aicss-todo-chevron {
  opacity: 1;
}
.aicss-todo-title { font-weight: 500; }
.aicss-todo-count {
  margin-left: auto;
  color: #a1a1a1;
  font-variant-numeric: tabular-nums;
}
.aicss-todo-collapsible {
  display: grid;
  grid-template-rows: 1fr;
  opacity: 1;
  transition: grid-template-rows 280ms ease, opacity 200ms ease;
}
.aicss-todo-collapsible.is-collapsed {
  grid-template-rows: 0fr;
  opacity: 0;
  pointer-events: none;
}
.aicss-todo-inner {
  min-height: 0;
  overflow: hidden;
}
.aicss-todo-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 10px 0 0;
}
.aicss-todo-item {
  display: grid;
  grid-template-columns: 16px minmax(0, 1fr);
  column-gap: 9px;
  row-gap: 1px;
  align-items: start;
  line-height: 18px;
  color: #8a8f98;
  animation: aicss-todo-item-in 360ms ease backwards;
  animation-delay: calc(var(--i, 0) * 50ms);
}
@keyframes aicss-todo-item-in {
  from { opacity: 0; transform: translateY(-7px); }
  to { opacity: 1; transform: translateY(0); }
}
.aicss-todo-icon-wrap {
  position: relative;
  width: 16px;
  height: 16px;
  margin-top: 1px;
  grid-row: 1 / -1;
}
.aicss-todo-icon {
  position: absolute;
  inset: 0;
  width: 16px;
  height: 16px;
  color: #a1a1a1;
  opacity: 0;
  transition: opacity 320ms ease;
}
.aicss-todo-icon.on { opacity: 1; }
.aicss-todo-icon.strong { color: #1a1a1a; }
.aicss-todo-icon.fail { color: #b84d4d; }
.aicss-todo-icon.invalidated { color: #7b7f87; }
.aicss-todo-label {
  position: relative;
  font-weight: 500;
  color: #2b2e34;
  transition: color 360ms ease;
  min-width: 0;
  word-break: break-word;
}
.aicss-todo-label::before {
  content: attr(data-label);
  position: absolute;
  inset: 0;
  background: linear-gradient(
    90deg,
    #1a1a1a 0%,
    #1a1a1a 30%,
    rgba(26, 26, 26, 0.45) 45%,
    rgba(26, 26, 26, 0.45) 55%,
    #1a1a1a 70%,
    #1a1a1a 100%
  );
  background-size: 300% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-fill-color: transparent;
  opacity: 0;
  transition: opacity 360ms ease;
  pointer-events: none;
  word-break: break-word;
}
.aicss-todo-item.active .aicss-todo-label { color: transparent; }
.aicss-todo-item.active .aicss-todo-label::before {
  opacity: 1;
  animation: aicss-todo-shine 2.25s cubic-bezier(0.25, 0.1, 0.25, 1) infinite;
}
.aicss-todo-item.done .aicss-todo-label {
  color: #a1a1a1;
  font-weight: 400;
  text-decoration: line-through;
}
.aicss-todo-item.failed .aicss-todo-label { color: #b84d4d; }
.aicss-todo-item.invalidated .aicss-todo-label {
  color: #7b7f87;
  text-decoration: line-through;
  text-decoration-style: dashed;
}
.aicss-todo-detail,
.aicss-todo-blocked,
.aicss-todo-acceptance {
  grid-column: 2;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
  color: #8a8f98;
  font-size: 11.5px;
  line-height: 16px;
}
.aicss-todo-item.active .aicss-todo-detail {
  -webkit-line-clamp: 2;
}
.aicss-todo-acceptance {
  color: #9aa0a8;
}
.task-goal-contract {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin: 0 0 12px;
  padding: 0 0 12px;
  border: 0;
  border-bottom: 1px solid #eff0f2;
  border-radius: 0;
  background: transparent;
  color: #4d525c;
  font-size: 13px;
  line-height: 18px;
}
.task-goal-contract strong {
  color: #8a8f98;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.task-goal-contract span {
  color: #2b2e34;
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.task-goal-contract small {
  overflow: hidden;
  color: #9aa0a8;
  font-size: 11.5px;
  line-height: 16px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
}
@keyframes aicss-todo-shine {
  0%, 18% { background-position: 100% 0; }
  82%, 100% { background-position: 0% 0; }
}
@property --todo-pie {
  syntax: '<percentage>';
  inherits: true;
  initial-value: 0%;
}
.aicss-todo-head-pie {
  position: absolute;
  inset: 0;
  margin: auto;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  color: #1a1a1a;
  transition: opacity 140ms ease, --todo-pie 400ms ease;
}
.aicss-todo-head-pie-ring {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
  color: #a1a1a1;
}
.aicss-todo-head-pie::after {
  content: '';
  position: absolute;
  inset: 2.6px;
  border-radius: 50%;
  background: conic-gradient(currentColor var(--todo-pie, 0%), transparent 0);
}
.aicss-todo .graph-summary {
  margin: 8px 0 0;
  padding: 0;
}
.aicss-todo .graph-review {
  margin-top: 10px;
}
@media (prefers-reduced-motion: reduce) {
  .aicss-todo-item { animation: none; }
  .aicss-todo-icon,
  .aicss-todo-label,
  .aicss-todo-label::before { transition: none; }
  .aicss-todo-item.active .aicss-todo-label::before { animation: none; }
}

.task-step-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.task-step {
  position: relative;
  overflow: hidden;
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) auto;
  align-items: start;
  gap: 8px;
  min-height: 40px;
  padding: 8px 4px;
  border-radius: 9px;
}

.task-step.running::after {
  position: absolute;
  z-index: 2;
  top: 3px;
  bottom: 3px;
  left: -46%;
  width: 42%;
  content: '';
  border-radius: 8px;
  background: linear-gradient(
    90deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.48) 32%,
    rgba(255, 255, 255, 0.96) 52%,
    rgba(255, 255, 255, 0.46) 72%,
    rgba(255, 255, 255, 0) 100%
  );
  pointer-events: none;
  animation: task-step-wave 1.9s ease-in-out infinite;
}

@keyframes task-step-wave {
  0%,
  14% {
    transform: translateX(0);
  }
  78%,
  100% {
    transform: translateX(350%);
  }
}

.task-step-icon {
  display: grid;
  height: 20px;
  place-items: center;
  color: #a0a5ae;
  font-size: 14px;
}

.task-step.running .task-step-icon {
  color: #4f6ef7;
}

/* 已完成=灰勾（2026-07-15 用户拍板：不用绿色），与消息内执行卡 .exec-plan-item.completed 同灰阶 */
.task-step.completed .task-step-icon {
  color: #a0a5ae;
}

.task-step.completed .task-step-copy strong {
  color: #6b7280;
}

.task-step.failed .task-step-icon {
  color: #c45252;
}

/* 进行中步骤左侧「当前」形态呼吸点（2026-07-20 定版 B，节奏与执行卡 exec-plan-breathe
   一致：圆 → 弹成圆角方 1.18x → 融回圆，渐变流动）；颜色保持本面板原有的蓝 */
.task-step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: linear-gradient(135deg, #a9b8fb 0%, #4f6ef7 45%, #2c3e97 75%, #a9b8fb 100%);
  background-size: 300% 300%;
  animation: task-step-pulse 1s infinite;
}

@keyframes task-step-pulse {
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

.task-step-state {
  display: inline-flex;
  align-items: center;
}

.task-step-copy,
.collaboration-run-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.task-step-copy strong,
.collaboration-run-copy strong {
  color: #2b2e34;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 20px;
}

.task-step-copy small,
.collaboration-run-copy small {
  overflow: hidden;
  color: #8a8f98;
  font-size: 11.5px;
  line-height: 17px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.task-step > em {
  padding-top: 2px;
  color: #a0a5ae;
  font-size: 10.5px;
  font-style: normal;
  white-space: nowrap;
}

.task-step.running > em {
  color: #4f6ef7;
}

.task-step.failed > em {
  color: #b84d4d;
}

.task-step-more {
  padding: 5px 4px 0 32px;
  color: #a0a5ae;
  font-size: 11px;
}

/* 空态：两个分区标题常驻，无数据时给一行灰字占位而不是整段隐藏 */
.task-collaboration-blank {
  margin: 0;
  padding: 6px 2px 4px;
  color: #a8adb6;
  font-size: 12px;
}

.subagent-section {
  padding-bottom: 14px;
}

.collaboration-run {
  display: grid;
  width: 100%;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  min-height: 48px;
  padding: 7px 4px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.collaboration-run:hover {
  background: #f7f7f8;
}

.collaboration-run-icon {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 9px;
  background: #f0effc;
  color: #6d62b5;
  font-size: 14px;
}

.collaboration-run-icon.running {
  background: #edf3ff;
  color: #4f6ef7;
}

.collaboration-run-icon.failed {
  background: #fdf0f0;
  color: #b84d4d;
}

.collaboration-run-state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #a0a5ae;
  font-size: 10.5px;
  white-space: nowrap;
}

.task-collaboration-empty {
  display: flex;
  min-height: 136px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  padding: 24px;
  color: #a0a5ae;
  font-size: 12px;
  text-align: center;
}

.task-collaboration-empty :deep(.anticon) {
  font-size: 18px;
}

.task-collaboration-pop-enter-active,
.task-collaboration-pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
  transform-origin: top left;
}

.task-collaboration-pop-enter-from,
.task-collaboration-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}

@media (max-width: 640px) {
  .task-collaboration-panel {
    /* 窄屏坐标仍由 placePanel 写入；这里只兜底避免极端情况下贴边 */
    max-width: calc(100vw - 24px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .task-collaboration-trigger,
  .task-collaboration-pop-enter-active,
  .task-collaboration-pop-leave-active {
    transition: none;
  }

  .tct-plan-line {
    animation: none;
    color: #60a8f0;
  }

  .task-step.running::after {
    display: none;
    animation: none;
  }

  .task-step-dot {
    animation: none;
  }
}
</style>
