<template>
  <form class="interview-setup" aria-label="准备模拟面试" @submit.prevent="startInterview">
    <div class="setup-materials">
      <section class="material-block" aria-labelledby="interview-resume-label">
        <h2 id="interview-resume-label">简历</h2>
        <input ref="resumeInput" class="file-input" type="file" accept=".pdf,.docx,.txt,.md" :disabled="busy" aria-label="选择简历文件" @change="chooseResume" />
        <div class="upload-zone" :class="{ 'drag-over': resumeDragOver, 'has-document': Boolean(resume && !resumeError) }" @dragover.prevent="resumeDragOver = !busy && !resumeUploading" @dragleave.prevent="resumeDragOver = false" @drop.prevent="dropResume">
          <button class="upload-button" type="button" :disabled="busy || resumeUploading" @click="resumeInput?.click()">
            <span class="document-symbol" aria-hidden="true"><LoadingOutlined v-if="resumeUploading" /><FileTextOutlined v-else /></span>
            <strong>{{ resumeUploading ? '正在读取简历…' : (resume && !resumeError ? resume.filename : '上传或拖入简历') }}</strong>
            <small>{{ resume && !resumeError ? '点击更换文件' : 'PDF / DOCX / TXT / MD' }}</small>
          </button>
        </div>
        <p v-if="resumeError" class="field-error" role="alert">{{ resumeError }}</p>
        <div v-if="resume?.status === 'partial' || resume?.truncated" class="partial-note">
          <p>{{ resume.note || '简历只读取到部分内容，请核对后补充缺失信息，或更换文件。' }}</p>
          <label><input v-model="resumePartialConfirmed" type="checkbox" :disabled="busy" />我已核对，将使用已读取内容开始</label>
        </div>
        <details class="resume-additions" :open="Boolean(resume?.status === 'partial' || resume?.truncated)">
          <summary>补充信息<PremiumChevron class="setup-chevron" direction="right" :size="14" interactive /></summary>
          <label class="sr-only" for="interview-resume-notes">简历补充说明</label>
          <textarea id="interview-resume-notes" v-model="resumeNotes" :disabled="busy" rows="3" maxlength="6000" placeholder="例如：项目中的个人职责、解析遗漏的实习经历…"></textarea>
        </details>
      </section>

      <section class="material-block" aria-labelledby="interview-jd-label">
        <h2 id="interview-jd-label">目标岗位</h2>
        <label class="sr-only" for="interview-job-title">岗位名称</label>
        <input id="interview-job-title" v-model="jobTitle" :disabled="busy" type="text" maxlength="120" placeholder="例如：前端开发实习生" autocomplete="off" aria-required="true" />
        <label class="field-label" for="interview-jd-text">岗位要求（JD）</label>
        <textarea id="interview-jd-text" v-model="jdText" :disabled="busy" rows="4" maxlength="30000" placeholder="粘贴岗位职责与任职要求…" aria-required="true"></textarea>
      </section>
    </div>

    <section class="practice-settings" aria-labelledby="interview-settings-title">
      <h2 id="interview-settings-title">练习题量</h2>
      <fieldset class="question-presets"><legend class="sr-only">选择本场题量</legend>
        <label v-for="option in countOptions" :key="option.value" :class="{ selected: countPreset === option.value }"><input v-model="countPreset" type="radio" name="interview-question-count" :value="option.value" :disabled="busy" @change="selectQuestionCount(option.value)" /><strong>{{ option.title }}</strong></label>
      </fieldset>
      <label v-if="countPreset === 'custom'" class="custom-count" for="interview-count">题目数量<input id="interview-count" v-model.number="questionCount" :disabled="busy" type="number" min="3" max="12" step="1" /><span>3–12 题</span></label>
    </section>
    <p v-if="submitError" class="field-error" role="alert">{{ submitError }}</p>
    <div class="setup-footer">
      <details class="more-settings">
        <summary>更多设置<PremiumChevron class="setup-chevron" direction="right" :size="14" interactive /></summary>
        <div class="setup-options">
          <label for="interview-level">求职阶段<select id="interview-level" v-model="level" :disabled="busy"><option value="intern">实习求职</option><option value="graduate">应届求职</option><option value="experienced">有工作经验</option></select></label>
          <label for="interview-pressure">压力强度<select id="interview-pressure" v-model="pressureLevel" :disabled="busy"><option value="gentle">温和练习</option><option value="normal">标准面试</option><option value="challenging">加强挑战</option></select></label>
        </div>
      </details>
      <button class="start-button" type="submit" :disabled="busy || resumeUploading"><LoadingOutlined v-if="busy" />{{ busy ? '正在准备面试…' : '开始模拟面试' }}<ArrowRightOutlined v-if="!busy" /></button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { computed, inject, ref } from 'vue';
import { ArrowRightOutlined, FileTextOutlined, LoadingOutlined } from '@ant-design/icons-vue';
import PremiumChevron from '../../components/PremiumChevron.vue';
import type { UploadedFile } from '../../agentApi';
import { InterviewSessionKey } from './context';
import { validateInterviewConfig, validateInterviewDocument, validateInterviewMaterial } from './setup';
import type { InterviewConfig, InterviewLevel, InterviewPressureLevel } from './types';

const session = inject(InterviewSessionKey, null);
const resumeInput = ref<HTMLInputElement | null>(null);
const resume = ref<UploadedFile | null>(null);
const resumeUploading = ref(false);
const resumeError = ref('');
const submitError = ref('');
const resumePartialConfirmed = ref(false);
const jobTitle = ref('');
const jdText = ref('');
const resumeNotes = ref('');
const level = ref<InterviewLevel>('graduate');
const pressureLevel = ref<InterviewPressureLevel>('normal');
const questionCount = ref(5);
const countPreset = ref<number | 'custom'>(5);
const countOptions = [
  { value: 3, title: '3 题' },
  { value: 5, title: '5 题' },
  { value: 8, title: '8 题' },
  { value: 'custom' as const, title: '自定义' },
];
const resumeDragOver = ref(false);
const busy = computed(() => session?.busy.value ?? false);
let resumeUploadVersion = 0;

function selectQuestionCount(value: number | 'custom') {
  if (typeof value === 'number') questionCount.value = value;
}

async function dropResume(event: DragEvent) {
  resumeDragOver.value = false;
  if (busy.value || resumeUploading.value) return;
  const files = event.dataTransfer?.files;
  if (!files?.length) return;
  if (files.length !== 1) {
    resumeError.value = '每次请选择一份简历文档';
    return;
  }
  await uploadResume(files[0]);
}

async function chooseResume(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = '';
  if (!file) return;
  await uploadResume(file);
}

async function uploadResume(file: File) {
  if (busy.value || resumeUploading.value) return;
  resumeError.value = validateInterviewDocument(file);
  if (resumeError.value) return;
  const version = ++resumeUploadVersion;
  resume.value = null;
  resumeUploading.value = true;
  resumePartialConfirmed.value = false;
  try {
    const { uploadChatFile } = await import('../../agentApi');
    const uploaded = await uploadChatFile(file);
    if (version !== resumeUploadVersion) return;
    resume.value = uploaded;
    resumeError.value = validateInterviewMaterial(uploaded);
  } catch (failure) {
    if (version === resumeUploadVersion) resumeError.value = failure instanceof Error ? failure.message : '文档上传失败，请重新选择';
  } finally {
    if (version === resumeUploadVersion) resumeUploading.value = false;
  }
}

async function startInterview() {
  if (!session || busy.value || resumeUploading.value) return;
  submitError.value = resumeError.value || validateInterviewMaterial(resume.value);
  if (submitError.value) return;
  if ((resume.value?.status === 'partial' || resume.value?.truncated) && !resumePartialConfirmed.value) {
    submitError.value = '请先核对简历已读取的内容，并确认使用这些内容开始';
    return;
  }
  const config: InterviewConfig = {
    job_title: jobTitle.value.trim(),
    jd_text: jdText.value.trim(),
    resume_file_id: resume.value!.file_id!,
    ...(resumeNotes.value.trim() ? { resume_notes: resumeNotes.value.trim() } : {}),
    question_count: questionCount.value,
    level: level.value,
    pressure_level: pressureLevel.value,
  };
  submitError.value = validateInterviewConfig(config);
  if (submitError.value) return;
  await session.start(config, [resume.value!]);
}
</script>

<style scoped lang="less">
.interview-setup {
  --interview-accent: #71717a;
  width: var(--interview-column, min(820px, calc(100% - 40px)));
  margin: 0 auto 28px;
  padding: 24px;
  color: var(--interview-ink);
  border: 1px solid var(--interview-line);
  border-radius: 14px;
  background: #fff;
  text-align: left;
  font-size: 14px;
}
.setup-materials { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 28px; }
.material-block { min-width: 0; }
.material-block h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; }
.field-label { display: block; margin: 12px 0 7px; font-size: 13px; font-weight: 500; }
.interview-setup input[type='text'], .interview-setup input[type='number'], .interview-setup select {
  display: block; width: 100%; min-width: 0; height: 36px; padding: 6px 12px; border: 1px solid var(--interview-line); border-radius: 8px; background: var(--interview-paper); color: var(--interview-ink); font: inherit; line-height: 22px;
}
.interview-setup input[type='text'], .interview-setup input[type='number'] { appearance: none; -webkit-appearance: none; }
.interview-setup textarea {
  display: block; width: 100%; min-width: 0; min-height: 88px; padding: 8px 12px; border: 1px solid var(--interview-line); border-radius: 8px; background: var(--interview-paper); color: var(--interview-ink); font: inherit; line-height: 1.45; resize: vertical;
}
.interview-setup input::placeholder, .interview-setup textarea::placeholder { color: #8a8e97; line-height: inherit; }
.interview-setup button { font: inherit; cursor: pointer; }
.interview-setup button:disabled, .interview-setup input:disabled, .interview-setup textarea:disabled, .interview-setup select:disabled { opacity: .55; cursor: default; }
.interview-setup :focus-visible { outline: 2px solid var(--interview-accent); outline-offset: 2px; }
.interview-setup input[type='text']:focus, .interview-setup input[type='number']:focus, .interview-setup textarea:focus, .interview-setup select:focus {
  border-color: var(--interview-accent); outline: none; box-shadow: none;
}
.interview-setup input:focus-visible, .interview-setup textarea:focus-visible, .interview-setup select:focus-visible { outline: none; }
.file-input, .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
.upload-zone { border: 1px dashed #cdd0d7; border-radius: 12px; background: var(--interview-soft); }
.upload-zone.drag-over { border-color: var(--interview-accent); background: var(--interview-soft); }
.upload-zone.has-document { border-style: solid; }
.upload-button { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; width: 100%; min-height: 176px; padding: 20px 16px; border: 0; border-radius: inherit; background: transparent; color: var(--interview-ink); }
.upload-button strong { max-width: 100%; overflow-wrap: anywhere; font-size: 14px; font-weight: 500; }
.upload-button small { color: var(--interview-muted); font-size: 12px; }
.document-symbol { display: grid; place-items: center; width: 46px; height: 52px; margin-bottom: 4px; border: 1px solid var(--interview-line); border-radius: 7px; background: var(--interview-paper); color: #6c7280; font-size: 24px; transform: rotate(-5deg); }
.resume-additions { margin-top: 12px; }
.resume-additions textarea { margin-top: 10px; }
.resume-additions summary, .more-settings summary { display: flex; align-items: center; gap: 8px; width: fit-content; padding: 6px 0; color: var(--interview-muted); font-size: 13px; cursor: pointer; line-height: 1.7; list-style: none; }
.resume-additions summary::-webkit-details-marker, .more-settings summary::-webkit-details-marker { display: none; }
details[open] > summary .setup-chevron { transform: rotate(0deg); }
.field-error { margin: 8px 0 0; color: #b42318; font-size: 13px; line-height: 1.6; }
.partial-note { margin: 8px 0 0; color: var(--interview-muted); font-size: 13px; line-height: 1.6; }
.partial-note { padding: 10px; border-radius: 8px; background: #f6f7f9; }
.partial-note p { margin: 0 0 8px; }
.partial-note label { display: flex; align-items: flex-start; gap: 6px; }
.partial-note input { margin-top: 4px; accent-color: var(--interview-ink); }
.practice-settings { margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--interview-line); }
.practice-settings h2 { margin: 0 0 12px; font-size: 15px; font-weight: 600; }
.question-presets { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; min-width: 0; margin: 0; padding: 0; border: 0; }
.question-presets label { position: relative; display: flex; align-items: center; justify-content: center; min-height: 44px; padding: 10px 8px; border: 1px solid var(--interview-line); border-radius: 9px; cursor: pointer; }
.question-presets label.selected { border-color: var(--interview-ink); background: var(--interview-soft); }
.question-presets label:has(input:focus-visible) { outline: 2px solid var(--interview-accent); outline-offset: 3px; }
.question-presets input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.question-presets strong { font-size: 14px; font-weight: 600; }
.interview-setup .custom-count { display: flex; align-items: center; gap: 10px; margin-top: 12px; font-size: 13px; }
.interview-setup .custom-count input { width: 84px; }
.custom-count > span { color: var(--interview-muted); }
.more-settings { min-width: 0; }
.more-settings summary { padding-block: 12px; }
.setup-options { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 10px; }
.setup-options label { display: flex; flex-direction: column; gap: 7px; font-size: 13px; }
.setup-footer { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 24px; margin-top: 20px; }
.start-button { display: flex; align-items: center; justify-content: center; gap: 8px; flex: none; min-height: 40px; padding: 0 18px; border: 0; border-radius: 8px; background: var(--interview-ink); color: #fff; font-weight: 500; }
.start-button:not(:disabled):hover { background: #30343c; }
@media (max-width: 640px) {
  .interview-setup { padding: 18px; border-radius: 14px; margin-bottom: 20px; }
  .setup-materials { grid-template-columns: minmax(0, 1fr); gap: 22px; }
  .material-block + .material-block { padding-top: 20px; border-top: 1px solid var(--interview-line); }
  .upload-button { min-height: 150px; }
  .setup-options { grid-template-columns: 1fr 1fr; gap: 12px; }
  .question-presets { gap: 6px; }
  .question-presets label { padding: 11px 4px; }
  .setup-footer { grid-template-columns: minmax(0, 1fr); gap: 10px; margin-top: 14px; }
  .start-button { min-height: 40px; }
  .interview-setup input[type='text'], .interview-setup input[type='number'], .interview-setup select { height: 40px; padding: 8px 12px; line-height: 22px; font-size: 16px; }
  .interview-setup textarea { font-size: 16px; line-height: 1.45; }
}
</style>
