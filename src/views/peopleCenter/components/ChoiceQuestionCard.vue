<template>
  <section class="choice-card" :class="{ disabled }" :aria-label="question">
    <header class="choice-head">
      <div class="choice-heading">
        <strong>{{ question }}</strong>
        <span v-if="hint">{{ hint }}</span>
      </div>
      <div v-if="total > 1 || closable" class="choice-pager" aria-label="问题导航">
        <button
          v-if="total > 1"
          type="button"
          aria-label="上一题"
          :disabled="disabled || index <= 0"
          @click="$emit('previous')"
        ><PremiumChevron direction="left" :size="16" interactive /></button>
        <span v-if="total > 1">{{ index + 1 }} of {{ total }}</span>
        <button
          v-if="total > 1"
          type="button"
          aria-label="下一题"
          :disabled="disabled || index >= total - 1 || !hasValue"
          @click="$emit('confirm')"
        ><PremiumChevron direction="right" :size="16" interactive /></button>
        <button
          v-if="closable"
          type="button"
          aria-label="全部跳过"
          :disabled="disabled"
          @click="$emit('close')"
        ><CloseOutlined /></button>
      </div>
    </header>

    <div
      class="choice-options"
      :role="mode === 'multiple' ? 'group' : 'radiogroup'"
      :aria-label="question"
    >
      <button
        v-for="(option, optionIndex) in options"
        :key="option.value"
        type="button"
        class="choice-option"
        :class="{ selected: isSelected(option.value) }"
        :role="mode === 'multiple' ? 'checkbox' : 'radio'"
        :aria-checked="isSelected(option.value)"
        :disabled="disabled"
        @click="choose(option.value)"
      >
        <span class="choice-index">
          <CheckOutlined v-if="mode === 'multiple' && isSelected(option.value)" />
          <template v-else>{{ optionIndex + 1 }}</template>
        </span>
        <span class="choice-copy">
          <span class="choice-label">
            {{ option.label || option.value }}
            <em v-if="option.recommended">推荐</em>
          </span>
          <span v-if="option.description" class="choice-description">{{ option.description }}</span>
        </span>
        <CheckOutlined v-if="mode === 'single' && isSelected(option.value)" class="choice-tail selected-mark" />
        <PremiumChevron v-else-if="mode === 'single'" direction="right" :size="14" class="choice-tail" interactive />
      </button>

      <div v-if="allowCustom" class="choice-custom" :class="{ open: customOpen }">
        <button
          v-if="!customOpen"
          type="button"
          class="choice-custom-trigger"
          :disabled="disabled"
          @click="customOpen = true"
        >
          <span class="choice-index"><EditOutlined /></span>
          <span>都不合适，我想自己补充</span>
        </button>
        <div v-else class="choice-custom-editor">
          <EditOutlined />
          <input
            ref="customInputRef"
            v-model="customText"
            type="text"
            :placeholder="customPlaceholder || '请输入你的要求…'"
            :disabled="disabled"
            @keydown.enter.prevent="commitCustom"
          />
          <button type="button" :disabled="disabled || !customText.trim()" @click="commitCustom">
            {{ mode === 'multiple' ? '添加' : '确定' }}
          </button>
        </div>
      </div>
    </div>

    <footer v-if="!disabled && (allowSkip || mode === 'multiple')" class="choice-actions">
      <span v-if="mode === 'multiple'" class="choice-tip">可选择多项</span>
      <button v-if="allowSkip" type="button" class="choice-skip" @click="$emit('skip')">跳过</button>
      <button
        v-if="mode === 'multiple'"
        type="button"
        class="choice-confirm"
        :disabled="!hasValue || submitting"
        @click="$emit('confirm')"
      >{{ submitting ? '提交中…' : confirmLabel }}</button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import { CheckOutlined, CloseOutlined, EditOutlined } from '@ant-design/icons-vue';
import PremiumChevron from './PremiumChevron.vue';

export interface ChoiceOption {
  value: string;
  label?: string;
  description?: string;
  recommended?: boolean;
}

const props = withDefaults(defineProps<{
  question: string;
  hint?: string;
  options: ChoiceOption[];
  mode?: 'single' | 'multiple';
  modelValue?: string | string[];
  index?: number;
  total?: number;
  allowCustom?: boolean;
  allowSkip?: boolean;
  closable?: boolean;
  disabled?: boolean;
  submitting?: boolean;
  customPlaceholder?: string;
  confirmLabel?: string;
}>(), {
  hint: '', mode: 'single', modelValue: '', index: 0, total: 1,
  allowCustom: true, allowSkip: true, closable: false, disabled: false,
  submitting: false, customPlaceholder: '', confirmLabel: '确认选择',
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | string[]): void;
  (e: 'select', value: string): void;
  (e: 'commit'): void;
  (e: 'confirm'): void;
  (e: 'previous'): void;
  (e: 'skip'): void;
  (e: 'close'): void;
}>();

const customOpen = ref(false);
const customText = ref('');
const customInputRef = ref<HTMLInputElement>();

watch(customOpen, async (open) => {
  if (!open) return;
  await nextTick();
  customInputRef.value?.focus();
});
watch(() => props.question, () => {
  customOpen.value = false;
  customText.value = '';
});

const selectedValues = computed(() => (
  Array.isArray(props.modelValue) ? props.modelValue : props.modelValue ? [props.modelValue] : []
));
const hasValue = computed(() => selectedValues.value.length > 0);

function isSelected(value: string) {
  return selectedValues.value.includes(value);
}

function choose(value: string) {
  if (props.mode === 'multiple') {
    const next = isSelected(value)
      ? selectedValues.value.filter((item) => item !== value)
      : [...selectedValues.value, value];
    emit('update:modelValue', next);
    return;
  }
  emit('update:modelValue', value);
  emit('select', value);
  emit('commit');
}

function commitCustom() {
  const value = customText.value.trim();
  if (!value) return;
  if (props.mode === 'multiple') {
    emit('update:modelValue', selectedValues.value.includes(value)
      ? selectedValues.value
      : [...selectedValues.value, value]);
    customText.value = '';
    customOpen.value = false;
    return;
  }
  emit('update:modelValue', value);
  emit('select', value);
  emit('commit');
}
</script>

<style scoped>
/* 版本 C「编辑器强调」（2026-07-17 用户选定）：左侧整条方形序号块 + 强标题层级；
   选中 = 序号块反白填墨 + 右侧出勾 + 墨色描边；「推荐」实心墨签；问题带左墨条领读；
   自定义补充 = 虚线行（同款左块）→ 成组输入框；轻量级联进场，支持窄屏 / 减少动效。 */
.choice-card {
  width: min(100%, 720px);
  overflow: hidden;
  border: 1px solid #e8e8e8;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(17, 17, 17, 0.04);
}
.choice-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 18px 12px;
}
.choice-heading {
  min-width: 0;
  border-left: 2px solid #111;
  padding-left: 12px;
}
.choice-heading strong {
  display: block;
  color: #111;
  font-size: 15.5px;
  font-weight: 600;
  letter-spacing: 0.01em;
  line-height: 1.5;
}
.choice-heading span {
  display: block;
  margin-top: 4px;
  color: #8f8f8f;
  font-size: 12.5px;
  line-height: 1.5;
}
.choice-pager {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  color: #b3b3b3;
  font-size: 12px;
}
.choice-pager span { padding: 0 2px; letter-spacing: 0.03em; font-variant-numeric: tabular-nums; }
.choice-pager button {
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #999;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.18s ease, color 0.18s ease;
}
.choice-pager button:not(:disabled):hover { background: #f2f2f2; color: #111; }
.choice-pager button:disabled { opacity: 0.3; cursor: default; }
.choice-options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 14px 6px;
}
.choice-option {
  display: flex;
  width: 100%;
  min-height: 52px;
  align-items: stretch;
  gap: 0;
  border: 1px solid #e9e9e9;
  border-radius: 10px;
  background: #fff;
  padding: 0;
  overflow: hidden;
  color: #222;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
  animation: choice-in 0.22s ease both;
}
.choice-option:nth-child(2) { animation-delay: 0.03s; }
.choice-option:nth-child(3) { animation-delay: 0.06s; }
.choice-option:nth-child(4) { animation-delay: 0.09s; }
.choice-option:nth-child(5) { animation-delay: 0.12s; }
.choice-option:nth-child(n + 6) { animation-delay: 0.15s; }
@keyframes choice-in {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
.choice-option:hover { border-color: #bfbfbf; }
.choice-option.selected {
  border-color: #111;
  box-shadow: inset 0 0 0 0.5px #111;
}
.choice-option:focus-visible,
.choice-custom-trigger:focus-visible,
.choice-actions button:focus-visible,
.choice-pager button:focus-visible { outline: 2px solid #818cf8; outline-offset: 2px; }
.choice-index {
  display: grid;
  width: 42px;
  flex: none;
  place-items: center;
  align-self: stretch;
  border: 0;
  border-right: 1px solid #ededed;
  border-radius: 0;
  background: #f7f7f7;
  color: #9a9a9a;
  font-size: 15px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  transition: background 0.18s ease, color 0.18s ease, border-color 0.18s ease;
}
.choice-option:hover .choice-index { color: #666; }
.choice-option.selected .choice-index { border-right-color: #111; background: #111; color: #fff; }
.choice-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  flex: 1;
  gap: 2px;
  padding: 9px 14px;
}
.choice-label { color: #1a1a1a; font-size: 14.5px; font-weight: 600; }
.choice-label em {
  position: relative;
  top: -1px;
  margin-left: 8px;
  border-radius: 5px;
  background: #111;
  padding: 1px 7px;
  color: #fff;
  font-size: 10.5px;
  font-style: normal;
  font-weight: 500;
  line-height: 16px;
  letter-spacing: 0.02em;
}
.choice-description { color: #999; font-size: 12.5px; line-height: 1.5; }
.choice-tail {
  flex: none;
  align-self: center;
  margin-right: 14px;
  color: #b5b5b5;
  opacity: 0;
  transition: opacity 0.18s ease, color 0.18s ease;
}
.choice-option:hover .choice-tail,
.choice-option:focus-visible .choice-tail { opacity: 1; }
.selected-mark { color: #111; opacity: 1; }
.choice-custom { min-height: 48px; }
.choice-custom-trigger {
  display: flex;
  width: 100%;
  min-height: 46px;
  align-items: stretch;
  gap: 0;
  border: 1px dashed #ddd;
  border-radius: 10px;
  background: #fff;
  padding: 0;
  overflow: hidden;
  color: #8d8d8d;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;
}
.choice-custom-trigger > span:last-child { display: flex; align-items: center; padding: 0 14px; }
.choice-custom-trigger:hover { border-color: #999; color: #333; }
.choice-custom-trigger .choice-index {
  border: 0;
  border-right: 1px dashed #ddd;
  background: #fafafa;
  color: #b0b0b0;
}
.choice-custom-trigger:hover .choice-index { border-right-color: #999; color: #777; }
.choice-custom-editor {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #d9d9d9;
  border-radius: 10px;
  padding: 4px 4px 4px 14px;
  color: #999;
  transition: border-color 0.18s ease;
}
.choice-custom-editor:focus-within { border-color: #818cf8; box-shadow: none; color: #555; }
.choice-custom-editor input {
  min-width: 0;
  flex: 1;
  border: 0;
  background: transparent;
  padding: 8px 0;
  color: #111;
  font-size: 14px;
  outline: none;
}
.choice-custom-editor input::placeholder { color: #b3b3b3; }
.choice-custom-editor button {
  flex: none;
  border: 1px solid #111;
  border-radius: 9px;
  background: #111;
  padding: 7px 16px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.18s ease;
}
.choice-custom-editor button:disabled { opacity: 0.35; cursor: default; }
.choice-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 6px 18px 14px;
}
.choice-tip { margin-right: auto; color: #a3a3a3; font-size: 12px; }
.choice-actions .choice-skip {
  border: 0;
  background: transparent;
  padding: 6px 10px;
  color: #888;
  font-size: 13px;
  cursor: pointer;
  transition: color 0.18s ease;
}
.choice-actions .choice-skip:hover { color: #111; }
.choice-actions .choice-confirm {
  border: 1px solid #111;
  border-radius: 9px;
  background: #111;
  padding: 7px 18px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: opacity 0.18s ease;
}
.choice-actions .choice-confirm:disabled { opacity: 0.35; cursor: default; }
.choice-card.disabled { box-shadow: none; opacity: 0.72; }
.choice-card.disabled button { cursor: default; }
@media (max-width: 640px) {
  .choice-card { border-radius: 14px; }
  .choice-head { padding: 14px 14px 10px; }
  .choice-options { padding: 0 10px 6px; }
  .choice-option { padding: 0; }
  .choice-index { width: 38px; font-size: 14px; }
  .choice-copy { padding: 9px 12px; }
  .choice-description { margin-top: 2px; }
}
@media (prefers-reduced-motion: reduce) {
  .choice-option { animation: none; transition: none; }
  .choice-tail { opacity: 1; }
}
</style>
