<template>
  <section v-if="showPanel" class="interview-panel" aria-label="面试进度与反馈" :aria-busy="controller?.busy.value || undefined">
    <div v-if="controller?.error.value && !waiting" class="panel-error" role="alert"><span>{{ controller.error.value }}</span><button type="button" :disabled="controller.refreshing.value" @click="controller.refresh()"><ReloadOutlined />重新读取</button></div>
    <div v-if="controller?.submissionConflict.value && snapshot?.current_question" class="submission-conflict" role="alert">
      <strong>{{ controller.submissionConflict.value }}</strong>
      <p>本次输入未提交，草稿已保留。请先核对当前题目，再修改或发送草稿。</p>
      <p v-if="snapshot?.current_question" class="current-question-text">{{ snapshot.current_question.text }}</p>
      <button type="button" :disabled="actionsDisabled" @click="controller.acknowledgeCurrentQuestion()">已核对当前题目</button>
    </div>

    <div v-if="showWaitRoom" class="practice-wait" role="status">
      <WorkAgentMascot class="wait-mascot" :state="waitMascotState" variant="interview" label="面试助手正在出题" />
      <strong>正在出题</strong>
      <p>{{ waitMascotCopy }}</p>
      <button v-if="continuePrepare" type="button" :disabled="actionsDisabled" @click="controller?.act('resume')"><ReloadOutlined />继续准备</button>
    </div>
    <div v-else-if="continuePrepare" class="prepare-retry" role="status">
      <p>题目还没有准备好。</p>
      <button type="button" :disabled="actionsDisabled" @click="controller?.act('resume')"><ReloadOutlined />继续准备</button>
    </div>

    <template v-else-if="snapshot && snapshot.status !== 'not_started' && !waiting">
      <article v-if="report" class="interview-report-card" aria-label="面试报告">
        <header class="report-card-head">
          <FileTextOutlined class="report-card-icon" aria-hidden="true" />
          <h2>面试复盘报告</h2>
          <div class="report-card-actions">
            <button type="button" aria-label="下载面试报告" title="下载报告" @click="downloadReport"><DownloadOutlined /></button>
            <button type="button" aria-label="展开面试报告" title="展开报告" @click="showReport"><ExpandAltOutlined /></button>
          </div>
        </header>
        <button type="button" class="feedback-preview" aria-label="查看完整面试报告" @click="showReport">
          <span class="preview-score" :title="scoreLabel" :aria-label="scoreLabel"><Transition name="feedback-switch" mode="out-in" appear><strong :key="overallScore ?? 'empty'">{{ overallScore ?? '—' }}</strong></Transition><span>/ 100</span></span>
          <Transition name="feedback-switch" mode="out-in" appear><span :key="report.summary" class="preview-copy">{{ report.summary }}</span></Transition>
        </button>
        <p v-if="downloadError" class="report-download-error" role="alert">{{ downloadError }}</p>
      </article>
      <div v-if="showActions" class="panel-menu"><InterviewMenu @show-report="showReport" /></div>

      <AModal v-if="report" v-model:open="detailsOpen" title="面试复盘报告" :footer="null" :width="760" :destroy-on-close="true" :transition-name="reducedMotion === 'reduce' ? 'interview-no-motion' : undefined" :mask-transition-name="reducedMotion === 'reduce' ? 'interview-no-motion' : undefined" wrap-class-name="interview-feedback-dialog">
        <div class="interview-details">
          <InterviewReport :snapshot="snapshot" />
          <p v-if="overallScore == null" class="score-context">{{ scoreLabel }}</p>
        </div>
      </AModal>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue';
import { Modal as AModal } from 'ant-design-vue';
import { usePreferredReducedMotion } from '@vueuse/core';
import { DownloadOutlined, ExpandAltOutlined, FileTextOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { downloadByData } from '/@/utils/file/download';
import WorkAgentMascot from '../../components/WorkAgentMascot.vue';
import InterviewReport from './InterviewReport.vue';
import InterviewMenu from './InterviewMenu.vue';
import { InterviewSessionKey } from './context';
import { interviewRoomWaiting } from './session';
import { buildInterviewReportMarkdown, completedInterviewReport, interviewReportFilename } from './report';
import type { WorkAgentMascotState } from '../../utils/workAgentMascotState';

const props = defineProps<{ hasConversation?: boolean; showActions?: boolean }>();
const emit = defineEmits<{ (event: 'surface', open: boolean): void }>();
const controller = inject(InterviewSessionKey, null);
const snapshot = computed(() => controller?.snapshot.value ?? null);
const waiting = computed(() => {
  const err = controller?.error.value || '';
  return interviewRoomWaiting(snapshot.value, Boolean(controller?.busy.value), Boolean(err) && !/仍在执行|排队或先停止/.test(err));
});
const showWaitRoom = computed(() => waiting.value && !props.hasConversation);
const continuePrepare = computed(() => Boolean(waiting.value && snapshot.value?.status === 'preparing' && !controller?.busy.value));
const report = computed(() => completedInterviewReport(snapshot.value));
const detailsOpen = ref(false);
const downloadError = ref('');
const reducedMotion = usePreferredReducedMotion();
const overallScore = computed(() => snapshot.value?.performance?.score ?? null);
const scoreLabel = computed(() => {
  if (overallScore.value != null) return `整场综合评分 ${overallScore.value} 分，满分 100 分`;
  if (!snapshot.value?.progress.answered) return '尚无作答评分';
  if (!snapshot.value?.performance) return '综合分暂不可用';
  return '现有回答的评分依据不足，暂不生成综合分';
});
const showPanel = computed(() => Boolean(
  controller && (
    (controller.error.value && !waiting.value)
    || controller.submissionConflict.value
    || showWaitRoom.value
    || continuePrepare.value
    || report.value
    || (props.showActions && snapshot.value && snapshot.value.status !== 'not_started')
  ),
));
const actionsDisabled = computed(() => Boolean(controller?.busy.value || controller?.error.value));
watch(showPanel, (open) => emit('surface', open), { immediate: true });
const waitMascotState = ref<WorkAgentMascotState>('thinking');
const waitMascotCopy = computed(() => ({
  thinking: '根据你的简历和目标岗位准备第一问',
  searching: '正在对照岗位要求里的关键能力',
  working: '正在组织这一场的问题',
  discover: '想到一问了，接着往下排',
  waiting: '根据你的简历和目标岗位准备第一问',
  idle: '根据你的简历和目标岗位准备第一问',
  success: '根据你的简历和目标岗位准备第一问',
  error: '根据你的简历和目标岗位准备第一问',
}[waitMascotState.value]));
function showReport() { if (report.value) detailsOpen.value = true; }
function downloadReport() {
  if (!snapshot.value || !report.value) return;
  downloadError.value = '';
  try {
    downloadByData(buildInterviewReportMarkdown(snapshot.value), interviewReportFilename(snapshot.value.config?.job_title || ''), 'text/markdown;charset=utf-8', '\uFEFF');
  } catch {
    downloadError.value = '下载未成功，请稍后重试';
  }
}
watch(report, (available) => { if (!available) detailsOpen.value = false; });
watch(() => snapshot.value?.id, () => { detailsOpen.value = false; downloadError.value = ''; });

watch(showWaitRoom, (on, _previous, onCleanup) => {
  if (!on) {
    waitMascotState.value = 'thinking';
    return;
  }
  if (typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    waitMascotState.value = 'waiting';
    return;
  }
  const sequence: { state: WorkAgentMascotState; ms: number }[] = [
    { state: 'thinking', ms: 2800 },
    { state: 'searching', ms: 2600 },
    { state: 'working', ms: 2200 },
    { state: 'discover', ms: 980 },
  ];
  let index = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const play = () => {
    waitMascotState.value = sequence[index].state;
    timer = setTimeout(() => {
      index = (index + 1) % sequence.length;
      play();
    }, sequence[index].ms);
  };
  play();
  onCleanup(() => { if (timer) clearTimeout(timer); });
}, { immediate: true });

defineExpose({ showReport });
</script>

<style scoped lang="less">
.interview-panel { width: var(--interview-column, min(820px, calc(100% - 40px))); margin: 20px auto 28px; color: var(--interview-ink); font-size: 14px; text-align: left; overflow-wrap: anywhere; }
.practice-wait { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: min(48vh, 380px); padding: 48px 8px; text-align: center; }
.wait-mascot { width: 128px; margin: 0 auto 18px; overflow: visible; }
.practice-wait strong { font-size: 22px; font-weight: 650; letter-spacing: -.4px; }
.practice-wait p { margin: 8px 0 0; min-height: 22px; color: var(--interview-muted); font-size: 14px; line-height: 1.6; }
.practice-wait button, .prepare-retry button { margin-top: 24px; min-height: 40px; padding: 8px 16px; border: 1px solid var(--interview-line); border-radius: 8px; background: var(--interview-paper); color: var(--interview-ink); font: inherit; cursor: pointer; }
.prepare-retry { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-bottom: 16px; color: var(--interview-muted); font-size: 14px; }
.prepare-retry p { margin: 0; }
.prepare-retry button { margin-top: 12px; }
.interview-report-card { overflow: hidden; border: 1px solid #e4e4e7; border-radius: 16px; background: #fff; box-shadow: 0 8px 28px rgba(24, 24, 27, .04); animation: interview-report-in .28s ease-out both; }
.report-card-head { display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 12px 20px; border-bottom: 1px solid #ededee; }
.report-card-icon { display: grid; flex-shrink: 0; place-items: center; width: 30px; height: 30px; border-radius: 7px; background: #f4f4f5; color: #52525b; font-size: 17px; }
.report-card-head h2 { flex: 1; min-width: 0; margin: 0; color: #27272a; font-size: 16px; font-weight: 600; line-height: 1.5; }
.report-card-actions { display: flex; align-items: center; gap: 4px; }
.report-card-actions button { display: grid; place-items: center; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: #52525b; font-size: 17px; cursor: pointer; transition: background-color .16s ease; }
.report-card-actions button:hover { background: #f4f4f5; }
.feedback-preview { display: grid; grid-template-columns: 96px minmax(0, 1fr); align-items: center; gap: 28px; width: 100%; padding: 32px; border: 0; background: transparent; color: #27272a; text-align: left; font: inherit; cursor: pointer; }
.interview-report-card button:focus-visible { outline: 2px solid #71717a; outline-offset: -4px; border-radius: 8px; }
.report-download-error { margin: 0; padding: 0 32px 24px; color: #9b3024; font-size: 16px; line-height: 1.7; }
.preview-score { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.preview-score strong { font-family: 'SF Pro Display', 'Helvetica Neue', sans-serif; font-size: 48px; font-weight: 500; letter-spacing: -1.5px; font-variant-numeric: tabular-nums; line-height: 1; }
.preview-score > span { color: #52525b; font-size: 16px; line-height: 1.5; }
.preview-copy { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 3; font-size: 18px; line-height: 1.8; font-weight: 400; }
.panel-menu { display: flex; justify-content: flex-end; }
.interview-details { --interview-ink: #27272a; --interview-muted: #52525b; --interview-line: #e4e4e7; --interview-accent: #71717a; font-size: 16px; padding: 24px 32px 32px; overflow-wrap: anywhere; }
.score-context { margin: 0 0 20px; color: #52525b; font-size: 16px; line-height: 1.8; }
.feedback-switch-enter-active, .feedback-switch-leave-active { transition: opacity .18s ease, transform .22s ease; }
.feedback-switch-enter-from { opacity: 0; transform: translateY(8px); }
.feedback-switch-leave-to { opacity: 0; transform: translateY(-4px); }
@keyframes interview-report-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.current-question-text { margin: 0; font-size: 16px; font-weight: 500; line-height: 1.75; white-space: pre-wrap; }
.panel-error button, .submission-conflict button { display: inline-flex; align-items: center; justify-content: center; min-height: 40px; padding: 0 12px; border: 1px solid var(--interview-line); border-radius: 8px; color: var(--interview-ink); background: var(--interview-paper); font: inherit; font-size: 16px; cursor: pointer; }
.interview-panel button:disabled { cursor: default; opacity: .5; }
.interview-panel button:not(:disabled):hover { color: var(--interview-ink); }
.interview-panel :focus-visible { outline: 2px solid var(--interview-accent); outline-offset: 3px; }
.panel-error, .submission-conflict { margin-bottom: 16px; padding: 16px 20px; border: 1px solid #f0d1cc; border-radius: 14px; color: #9b3024; background: #fffbfa; line-height: 1.7; }
.panel-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.submission-conflict p { margin: 10px 0; }
@media (max-width: 600px) {
  .interview-details { padding: 24px 20px; }
  .report-card-head { gap: 10px; padding: 10px 16px; }
  .feedback-preview { grid-template-columns: 76px minmax(0, 1fr); gap: 18px; padding: 24px 20px; }
  .report-download-error { padding: 0 20px 20px; }
  .preview-score strong { font-size: 44px; }
  .practice-wait { min-height: 240px; }
}
@media (prefers-reduced-motion: reduce) {
  .interview-report-card { animation: none; }
  .feedback-switch-enter-active, .feedback-switch-leave-active, .report-card-actions button { transition: none; }
}
</style>
