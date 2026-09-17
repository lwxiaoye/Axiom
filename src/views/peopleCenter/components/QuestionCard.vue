<template>
  <div class="task-requirement-wrap">
    <!-- 回答完即隐藏（2026-07-17 用户拍板，延续「做完一个隐藏一个」）：提交后 submitted=true，
         整卡不再渲染，不留只读/淡出的「已回答」残卡；权威隐藏由父级 v-if 兜底，这里再自守一层。 -->
    <ChoiceQuestionCard
      v-if="currentQuestion && !isReadonly"
      :key="currentQuestion.key"
      :question="currentQuestion.title"
      :hint="currentQuestion.why"
      :options="currentQuestion.options || []"
      :mode="questionMode(currentQuestion)"
      :model-value="answerOf(currentQuestion)"
      :index="currentIndex"
      :total="questions.length"
      :allow-custom="currentQuestion.allow_custom !== false"
      :allow-skip="true"
      :closable="true"
      :disabled="submitting"
      :submitting="submitting"
      :custom-placeholder="currentQuestion.placeholder || '请输入你的要求…'"
      :confirm-label="currentIndex === questions.length - 1 ? '提交并继续' : '确认选择'"
      @update:model-value="setAnswer(currentQuestion, $event)"
      @commit="advanceOrSubmit"
      @confirm="advanceOrSubmit"
      @previous="currentIndex = Math.max(0, currentIndex - 1)"
      @skip="skipCurrent"
      @close="skipAll"
    />
    <p v-if="displayError && !isReadonly" class="requirement-error" role="alert">{{ displayError }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import type { QuestionCardView } from '../composables/executionTimeline';
import ChoiceQuestionCard from './ChoiceQuestionCard.vue';

const AUTO = '__auto__';
const props = defineProps<{
  card: QuestionCardView;
  submitting?: boolean;
}>();
const emit = defineEmits<{ (e: 'submit', answers: Record<string, unknown>): void }>();

type Question = QuestionCardView['questions'][number];
const currentIndex = ref(0);
const answers = reactive<Record<string, string | string[]>>({});
const localError = ref('');
const questions = computed(() => props.card.questions || []);
const currentQuestion = computed(() => questions.value[currentIndex.value]);
const isReadonly = computed(() => Boolean(props.card.submitted));
const displayError = computed(() => localError.value || props.card.error || '');

function questionMode(question: Question): 'single' | 'multiple' {
  return question.kind === 'multiple' || question.kind === 'tags' ? 'multiple' : 'single';
}

function seedPrefill() {
  for (const question of questions.value) {
    if (answers[question.key] !== undefined || question.prefill == null || question.prefill === '') continue;
    answers[question.key] = questionMode(question) === 'multiple' && Array.isArray(question.prefill)
      ? question.prefill.map(String)
      : String(question.prefill);
  }
}
seedPrefill();
watch(() => props.card.questions, () => {
  seedPrefill();
  currentIndex.value = Math.min(currentIndex.value, Math.max(0, questions.value.length - 1));
});

function answerOf(question: Question): string | string[] {
  return answers[question.key] ?? (questionMode(question) === 'multiple' ? [] : '');
}

function setAnswer(question: Question, value: string | string[]) {
  localError.value = '';
  answers[question.key] = value;
}

function advanceOrSubmit() {
  const question = currentQuestion.value;
  if (!question) return;
  const value = answerOf(question);
  if (value === '' || (Array.isArray(value) && !value.length)) {
    localError.value = `请选择「${question.title}」，或点击跳过`;
    return;
  }
  if (currentIndex.value < questions.value.length - 1) {
    currentIndex.value += 1;
    return;
  }
  submit();
}

function skipCurrent() {
  const question = currentQuestion.value;
  if (!question) return;
  answers[question.key] = AUTO;
  advanceOrSubmit();
}

function skipAll() {
  for (const question of questions.value) {
    const value = answerOf(question);
    if (value === '' || (Array.isArray(value) && !value.length)) answers[question.key] = AUTO;
  }
  submit();
}

function submit() {
  localError.value = '';
  const payload: Record<string, unknown> = {};
  for (const question of questions.value) {
    const value = answerOf(question);
    if (value === '' || (Array.isArray(value) && !value.length)) {
      if (question.required !== false) {
        currentIndex.value = questions.value.findIndex((item) => item.key === question.key);
        localError.value = `请选择「${question.title}」，或点击跳过`;
        return;
      }
      continue;
    }
    payload[question.key] = value;
  }
  emit('submit', payload);
}
</script>

<style scoped>
.task-requirement-wrap { margin: 12px 0; }
.requirement-error { margin: 8px 12px 0; color: #b42318; font-size: 12px; }
</style>
