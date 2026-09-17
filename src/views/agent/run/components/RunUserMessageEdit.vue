<template>
  <div class="message-edit">
    <textarea
      ref="textareaRef"
      :value="modelValue"
      rows="3"
      @input="onInput"
      @keydown.esc.prevent="$emit('cancel')"
      @keydown.enter.exact.prevent="onSave"
    />
    <div class="message-edit-actions">
      <button type="button" class="edit-cancel" @click="$emit('cancel')">取消</button>
      <button type="button" class="edit-save" :disabled="!modelValue.trim()" @click="onSave">发送</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue';

const props = defineProps<{ modelValue: string }>();
const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
  (e: 'cancel'): void;
  (e: 'save'): void;
}>();

const textareaRef = ref<HTMLTextAreaElement | null>(null);

onMounted(() => {
  nextTick(() => {
    const el = textareaRef.value;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  });
});

function onInput(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value);
}

function onSave() {
  if (!props.modelValue.trim()) return;
  emit('save');
}
</script>

<style lang="less" scoped>
.message-edit {
  width: min(72vw, 560px);
  max-width: 100%;
}

.message-edit textarea {
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 72px;
  margin: 0;
  resize: none;
  border: 1px solid #d7d8dd;
  border-radius: 14px;
  background: #fff;
  padding: 10px 14px;
  color: #0d0d0d;
  font-family:
    ui-sans-serif,
    -apple-system,
    system-ui,
    'Segoe UI',
    'PingFang SC',
    'Hiragino Sans GB',
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    'Helvetica Neue',
    Arial,
    sans-serif;
  font-size: 16px;
  font-weight: 400;
  line-height: 1.65;
  letter-spacing: 0;
  outline: none;
  box-shadow: none;
  appearance: none;
  -webkit-appearance: none;
}

.message-edit textarea:focus,
.message-edit textarea:focus-visible {
  border-color: rgba(17, 24, 39, 0.18);
  outline: none;
  box-shadow: none;
}

.message-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.message-edit-actions button {
  border: 1px solid #d7d8dd;
  border-radius: 8px;
  background: #fff;
  padding: 6px 16px;
  font-size: 13px;
  color: #4b5059;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.message-edit-actions .edit-cancel:hover {
  border-color: #c7c9d0;
  color: #202228;
}

.message-edit-actions .edit-save {
  border-color: #111;
  background: #111;
  color: #fff;
}

.message-edit-actions .edit-save:hover {
  background: #303035;
}

.message-edit-actions .edit-save:disabled {
  border-color: #d0d0d4;
  background: #c8c8cc;
  cursor: not-allowed;
}
</style>
