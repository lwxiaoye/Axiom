<template>
  <Teleport to="body">
    <Transition name="profile-modal-fade">
      <div v-if="open" class="profile-modal-layer">
        <button class="profile-modal-backdrop" type="button" aria-label="关闭个人资料" @click="requestClose"></button>
        <section class="profile-modal" role="dialog" aria-modal="true" aria-label="个人资料">
          <header class="profile-modal-header">
            <button ref="closeButtonRef" type="button" title="关闭" aria-label="关闭个人资料" @click="requestClose">
              <CloseOutlined />
            </button>
          </header>
          <div class="profile-modal-body">
            <BaseSetting
              :key="profileEpoch"
              @dirty-change="profileDirty = $event"
              @saved="handleSaved"
              @error="$emit('error', $event)"
            />
          </div>
          <div v-if="discardConfirmOpen" class="profile-discard-layer">
            <button type="button" class="profile-discard-backdrop" aria-label="继续编辑" @click="discardConfirmOpen = false"></button>
            <div class="profile-discard-dialog" role="alertdialog" aria-modal="true" aria-labelledby="profile-discard-title">
              <strong id="profile-discard-title">放弃未保存的修改？</strong>
              <span>姓名、生日或性别的修改还没有保存。</span>
              <div>
                <button type="button" @click="discardConfirmOpen = false">继续编辑</button>
                <button type="button" class="danger" @click="discardAndClose">放弃修改</button>
              </div>
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { CloseOutlined } from '@ant-design/icons-vue';
import BaseSetting from '/@/views/system/usersetting/BaseSetting.vue';

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{
  (event: 'close'): void;
  (event: 'saved', value: { realname: string; avatar: string }): void;
  (event: 'error', error: unknown): void;
}>();

const closeButtonRef = ref<HTMLButtonElement | null>(null);
const profileDirty = ref(false);
const discardConfirmOpen = ref(false);
const profileEpoch = ref(0);
let previousBodyOverflow = '';

function close() {
  discardConfirmOpen.value = false;
  profileDirty.value = false;
  emit('close');
}

function requestClose() {
  if (profileDirty.value) {
    discardConfirmOpen.value = true;
    return;
  }
  close();
}

function discardAndClose() {
  close();
}

function handleSaved(value: { realname: string; avatar: string }) {
  emit('saved', value);
}

function handleKeydown(event: KeyboardEvent) {
  if (!props.open || event.key !== 'Escape') return;
  event.preventDefault();
  if (discardConfirmOpen.value) {
    discardConfirmOpen.value = false;
    return;
  }
  requestClose();
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      profileEpoch.value += 1;
      profileDirty.value = false;
      previousBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      nextTick(() => closeButtonRef.value?.focus());
    } else {
      document.body.style.overflow = previousBodyOverflow;
    }
  },
);

onMounted(() => window.addEventListener('keydown', handleKeydown));
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKeydown);
  document.body.style.overflow = previousBodyOverflow;
});
</script>

<style scoped lang="less">
.profile-modal-layer {
  position: fixed;
  z-index: 2200;
  display: grid;
  place-items: center;
  inset: 0;
  padding: 24px;
}

.profile-modal-backdrop {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  background: rgba(20, 23, 31, 0.34);
  cursor: default;
  backdrop-filter: blur(3px);
}

.profile-modal {
  position: relative;
  display: flex;
  width: min(620px, 100%);
  max-height: min(720px, calc(100vh - 16px));
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(222, 225, 232, 0.92);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 24px 72px rgba(20, 23, 31, 0.2);
}

.profile-modal-header {
  display: flex;
  min-height: 52px;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  border-bottom: 1px solid #eceef2;
  padding: 8px 16px;
}

.profile-modal-header button {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: #555b65;
  cursor: pointer;
}

.profile-modal-header button:hover { border-color: #e2e4e9; background: #f6f7f8; }
.profile-modal-header button:focus-visible { outline: 2px solid #6d7bd8; outline-offset: 2px; }

.profile-modal-body {
  flex: 1;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 14px 24px 8px;
}

.profile-discard-layer {
  position: absolute;
  z-index: 3;
  display: grid;
  place-items: center;
  inset: 0;
  padding: 22px;
}

.profile-discard-backdrop {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  background: rgba(22, 25, 32, 0.3);
  backdrop-filter: blur(2px);
}

.profile-discard-dialog {
  position: relative;
  width: min(360px, 100%);
  border: 1px solid #e1e3e8;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(20, 23, 31, 0.22);
  padding: 20px;
}

.profile-discard-dialog strong { display: block; color: #191b20; font-size: 16px; }
.profile-discard-dialog > span { display: block; margin-top: 8px; color: #747985; font-size: 13px; line-height: 1.55; }
.profile-discard-dialog > div { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
.profile-discard-dialog button {
  min-height: 36px;
  border: 1px solid #dfe2e8;
  border-radius: 9px;
  background: #fff;
  padding: 0 12px;
  color: #343840;
  cursor: pointer;
}
.profile-discard-dialog button.danger { border-color: #b84d43; background: #b84d43; color: #fff; }
.profile-discard-dialog button:focus-visible { outline: 2px solid #6d7bd8; outline-offset: 2px; }

.profile-modal-fade-enter-active,
.profile-modal-fade-leave-active { transition: opacity 0.18s ease; }
.profile-modal-fade-enter-active .profile-modal,
.profile-modal-fade-leave-active .profile-modal { transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.18s ease; }
.profile-modal-fade-enter-from,
.profile-modal-fade-leave-to { opacity: 0; }
.profile-modal-fade-enter-from .profile-modal,
.profile-modal-fade-leave-to .profile-modal { opacity: 0; transform: translateY(14px) scale(0.985); }

@media (max-width: 560px) {
  .profile-modal-layer { align-items: end; padding: 0; }
  .profile-modal { width: 100%; max-height: calc(100vh - 18px); border-radius: 18px 18px 0 0; }
  .profile-modal-header { padding: 14px 18px; }
  .profile-modal-body { padding: 16px 14px 12px; }
}

@media (prefers-reduced-motion: reduce) {
  .profile-modal-fade-enter-active,
  .profile-modal-fade-leave-active,
  .profile-modal-fade-enter-active .profile-modal,
  .profile-modal-fade-leave-active .profile-modal { transition: none; }
}
</style>
