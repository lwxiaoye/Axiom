<template>
  <Teleport to="body">
    <transition name="lightbox-fade">
      <!-- 点击空白遮罩关闭（@click.self）；点图片本身不关闭 -->
      <div v-if="src" class="image-lightbox" @click.self="emit('close')">
        <img :src="src" class="image-lightbox-img" alt="图片预览" />
        <button type="button" class="image-lightbox-close" title="关闭" @click="emit('close')">
          <CloseOutlined />
        </button>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { watch, onBeforeUnmount } from 'vue';
import { CloseOutlined } from '@ant-design/icons-vue';

const props = defineProps<{ src: string | null }>();
const emit = defineEmits<{ (e: 'close'): void }>();

// Esc 关闭：仅在有图片时挂载监听，避免全局常驻
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close');
}

watch(
  () => props.src,
  (value) => {
    if (value) window.addEventListener('keydown', onKeydown);
    else window.removeEventListener('keydown', onKeydown);
  },
);

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));
</script>

<style scoped>
.image-lightbox {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
  background: rgba(15, 18, 24, 0.72);
  backdrop-filter: blur(4px);
  cursor: zoom-out;
}

.image-lightbox-img {
  max-width: min(92vw, 1100px);
  max-height: 88vh;
  border-radius: 12px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.45);
  object-fit: contain;
  cursor: default;
}

.image-lightbox-close {
  position: fixed;
  top: 24px;
  right: 28px;
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 18px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.image-lightbox-close:hover {
  background: rgba(255, 255, 255, 0.28);
}

.lightbox-fade-enter-active,
.lightbox-fade-leave-active {
  transition: opacity 0.18s ease;
}

.lightbox-fade-enter-from,
.lightbox-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .image-lightbox {
    backdrop-filter: none;
  }

  .lightbox-fade-enter-active,
  .lightbox-fade-leave-active {
    transition: none;
  }
}
</style>
