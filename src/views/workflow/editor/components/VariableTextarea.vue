<template>
  <div ref="fieldRef" class="variable-field">
    <component
      :is="singleLine ? 'input' : 'textarea'"
      ref="fieldInputRef"
      class="variable-textarea"
      :rows="rows"
      :value="value"
      :placeholder="placeholder"
      :maxlength="maxLength"
      @input="handleInput"
      @keydown="handleKeydown"
      @blur="handleBlur"
    />
    <div v-if="pickerVisible" class="variable-picker" @pointerdown.capture="keepPickerOpen">
      <section v-for="group in groups" :key="group.label" class="variable-picker-group">
        <h5>{{ group.label }}</h5>
        <button
          v-for="item in group.options"
          :key="item.value"
          ref="optionRefs"
          type="button"
          :class="{ active: flatVariables.indexOf(item) === activeIndex }"
          :aria-selected="flatVariables.indexOf(item) === activeIndex"
          @mouseenter="activeIndex = flatVariables.indexOf(item)"
          @mousedown.prevent="insertVariable(item.value)"
        >
          <strong>{{ item.label }}</strong>
          <span>{{ item.detail }}</span>
        </button>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import { flattenVariableGroups, formatVariableTemplate, nextVariablePickerIndex } from './variablePicker';
import type { TextVariableGroup } from '../../core/utils';

const props = withDefaults(
  defineProps<{
    value?: string;
    rows?: number;
    singleLine?: boolean;
    placeholder?: string;
    maxLength?: number;
    groups?: TextVariableGroup[];
    variables?: { label: string; value: string }[];
  }>(),
  {
    value: '',
    rows: 3,
    placeholder: '',
    maxLength: undefined,
  }
);

const emit = defineEmits<{ 'update:value': [value: string] }>();

const fieldInputRef = ref<HTMLInputElement | HTMLTextAreaElement | null>(null);
const fieldRef = ref<HTMLElement | null>(null);
const optionRefs = ref<HTMLButtonElement[]>([]);
const pickerVisible = ref(false);
const slashIndex = ref(-1);
const activeIndex = ref(-1);
const pointerInsidePicker = ref(false);
const singleLine = computed(() => props.singleLine === true);
const groups = computed<TextVariableGroup[]>(() =>
  props.groups || [
    { label: '节点变量', options: [] },
    { label: '全局变量', options: (props.variables || []).map((item) => ({ ...item, detail: item.value })) },
  ]
);
const flatVariables = computed(() => flattenVariableGroups(groups.value));

function handleInput(event: Event) {
  const target = event.target as HTMLTextAreaElement;
  emit('update:value', target.value);
  const position = target.selectionStart || 0;
  const previousChar = target.value.charAt(position - 1);
  pickerVisible.value = previousChar === '/';
  slashIndex.value = previousChar === '/' ? position - 1 : -1;
  activeIndex.value = pickerVisible.value && flatVariables.value.length ? 0 : -1;
}

function handleKeydown(event: KeyboardEvent) {
  if (pickerVisible.value && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
    event.preventDefault();
    activeIndex.value = nextVariablePickerIndex(
      activeIndex.value,
      flatVariables.value.length,
      event.key === 'ArrowDown' ? 'down' : 'up',
    );
    nextTick(() => optionRefs.value[activeIndex.value]?.scrollIntoView({ block: 'nearest' }));
    return;
  }
  if (pickerVisible.value && event.key === 'Enter' && activeIndex.value >= 0) {
    event.preventDefault();
    const item = flatVariables.value[activeIndex.value];
    if (item) insertVariable(item.value);
    return;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    pickerVisible.value = false;
    activeIndex.value = -1;
  }
}

function handleBlur() {
  window.setTimeout(() => {
    if (pointerInsidePicker.value) {
      pointerInsidePicker.value = false;
      return;
    }
    pickerVisible.value = false;
  });
}

function keepPickerOpen() {
  pointerInsidePicker.value = true;
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (pickerVisible.value && !fieldRef.value?.contains(event.target as Node)) {
    pickerVisible.value = false;
  }
}

function insertVariable(value: string) {
  const fieldInput = fieldInputRef.value;
  if (!fieldInput) return;
  const current = props.value || '';
  const start = slashIndex.value >= 0 ? slashIndex.value : fieldInput.selectionStart || 0;
  const end = fieldInput.selectionEnd || start;
  const templateValue = formatVariableTemplate(value);
  const nextValue = `${current.slice(0, start)}${templateValue}${current.slice(end)}`;
  emit('update:value', nextValue);
  pickerVisible.value = false;
  activeIndex.value = -1;
  pointerInsidePicker.value = false;
  nextTick(() => {
    const cursor = start + templateValue.length;
    fieldInput.focus();
    fieldInput.setSelectionRange(cursor, cursor);
  });
}

onMounted(() => document.addEventListener('pointerdown', handleDocumentPointerDown, true));
onBeforeUnmount(() => document.removeEventListener('pointerdown', handleDocumentPointerDown, true));
</script>

<style scoped lang="less">
.variable-field {
  position: relative;
}

.variable-textarea {
  width: 100%;
  min-height: 34px;
  padding: 7px 11px;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  color: #111827;
  font-size: 14px;
  line-height: 1.55;
  resize: vertical;
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;

  &:focus {
    border-color: #005bac;
    box-shadow: none;
  }
}

.variable-picker {
  position: absolute;
  z-index: 20;
  right: 0;
  left: 0;
  bottom: calc(100% + 6px);
  display: grid;
  gap: 6px;
  max-height: 220px;
  padding: 8px;
  overflow: auto;
  border: 1px solid #c7d8ea;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 16px 34px rgba(15, 23, 42, 0.16);

  .variable-picker-group {
    display: grid;
    gap: 3px;
  }

  h5 {
    margin: 4px 4px 1px;
    color: #64748b;
    font-size: 11px;
    font-weight: 600;
  }

  button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 8px 10px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    cursor: pointer;

    &.active,
    &:hover {
      background: #f6fbff;
    }
  }

  strong {
    color: #111827;
    font-size: 12px;
  }

  span {
    color: #005bac;
    font-family: Consolas, monospace;
    font-size: 12px;
  }
}
</style>
