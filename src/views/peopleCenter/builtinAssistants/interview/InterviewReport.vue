<template>
  <section v-if="review" class="interview-report" aria-label="面试复盘">
    <p class="report-score" :aria-label="performance?.score == null ? '暂无综合评分' : `整场综合评分 ${performance.score} 分，满分 100 分`"><strong>{{ performance?.score ?? '—' }}</strong><span>/ 100</span></p>
    <p>{{ review.summary }}</p>
    <section class="report-section" aria-label="做得好的地方">
      <h3>做得好的地方</h3>
      <p v-for="point in review.strengths" :key="point">{{ point }}</p>
      <p v-if="!review.strengths.length">这次还没有足够的回答可以说明做得好的地方。</p>
    </section>
    <section class="report-section" aria-label="需要提升的地方">
      <h3>需要提升的地方</h3>
      <p v-for="point in review.improvements" :key="point">{{ point }}</p>
      <p v-if="!review.improvements.length">这次没有列出需要改进的地方。</p>
    </section>
    <InterviewDisclosure v-if="review.next_steps.length" label="下一步练习"><p v-for="point in review.next_steps" :key="point">{{ point }}</p></InterviewDisclosure>
    <InterviewDisclosure label="评分说明">
      <p>已答 {{ snapshot.progress.answered }} / {{ snapshot.progress.total }} 题<span v-if="snapshot.progress.followups">，含 {{ snapshot.progress.followups }} 次追问</span>。</p>
      <p v-if="performance?.assisted_score != null">辅导后练习 {{ performance.assisted_score }} / 100</p>
      <p>综合分依据整场实际回答与追问，按专业匹配 {{ weights.professional }}%、逻辑思维 {{ weights.logic }}%、文字表达 {{ weights.expression }}% 汇总。未作答、未考察或证据不足不计零分；首次表现与辅导后练习分别统计。</p>
    </InterviewDisclosure>
    <div class="report-toolbar"><button type="button" class="download-action" @click="downloadReport"><DownloadOutlined />下载复盘</button><span v-if="downloadNotice" role="status">{{ downloadNotice }}</span></div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue';
import { DownloadOutlined } from '@ant-design/icons-vue';
import { downloadByData } from '/@/utils/file/download';
import InterviewDisclosure from './InterviewDisclosure.vue';
import type { InterviewSnapshot } from './types';
import { buildInterviewReportMarkdown, completedInterviewReport, interviewReportFilename } from './report';

const props = defineProps<{ snapshot: InterviewSnapshot }>();
const review = computed(() => completedInterviewReport(props.snapshot));
const performance = computed(() => props.snapshot.performance);
const weights = computed(() => performance.value?.weights ?? { professional: 50, logic: 30, expression: 20 });
const downloadNotice = ref('');
function downloadReport() {
  try {
    downloadByData(buildInterviewReportMarkdown(props.snapshot), interviewReportFilename(props.snapshot.config?.job_title || ''), 'text/markdown;charset=utf-8', '\uFEFF');
    downloadNotice.value = '已开始下载';
  } catch {
    downloadNotice.value = '下载未成功，请稍后重试';
  }
}
</script>

<style scoped lang="less">
.interview-report { color: #27272a; font-size: 17px; line-height: 1.85; }
.interview-report p { margin: 0 0 20px; white-space: pre-wrap; }
.report-score { display: flex; align-items: baseline; gap: 10px; color: #52525b; }
.report-score strong { color: #27272a; font-family: 'SF Pro Display', 'Helvetica Neue', sans-serif; font-size: 48px; font-weight: 500; letter-spacing: -1.5px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.report-section { margin: 32px 0; }
.report-section h3 { margin: 0 0 20px; font-size: 20px; font-weight: 600; line-height: 1.5; }
.report-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 24px; }
.download-action { display: inline-flex; align-items: center; gap: 8px; min-height: 42px; padding: 0 16px; border: 1px solid #e4e4e7; border-radius: 8px; background: #fff; color: #27272a; font: inherit; cursor: pointer; }
.download-action:focus-visible { outline: 2px solid #71717a; outline-offset: 3px; }
</style>
