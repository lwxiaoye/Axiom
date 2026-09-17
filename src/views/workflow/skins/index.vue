<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <main class="skin-manager-page">
    <header class="skin-manager-header">
      <h1>子智能体皮肤管理</h1>
    </header>

    <section class="skin-workshop" aria-label="子智能体皮肤包管理与三端预览">
      <PresentationStudioPanel />
    </section>
  </main>
</template>

<script setup lang="ts">
import PresentationStudioPanel from '../manage/PresentationStudioPanel.vue';
</script>

<style scoped lang="less">
.skin-manager-page {
  min-height: 100%;
  padding: 28px;
  background: #f3f6f9;
  color: #18334d;
}

.skin-manager-header {
  max-width: 1520px;
  margin: 0 auto 18px;
  padding: 4px 4px 0;
}

.skin-manager-header h1 {
  margin: 0;
  color: #16324f;
  font-size: 28px;
  font-weight: 720;
  letter-spacing: -0.03em;
}

.skin-workshop {
  max-width: 1520px;
  margin: 0 auto;
  padding: 18px;
  border: 1px solid #e1e8ee;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 12px 36px rgba(31, 66, 99, 0.06);
}

@media (max-width: 900px) {
  .skin-manager-page { padding: 18px; }
}

@media (max-width: 560px) {
  .skin-manager-page { padding: 12px; }
  .skin-manager-header h1 { font-size: 24px; }
  .skin-workshop { padding: 12px; }
}
</style>
