<template>
  <div class="excel-file-viewer">
    <VueOfficeExcel :src="src" @error="emit('error')" />
  </div>
</template>

<script setup lang="ts">
import { defineAsyncComponent } from 'vue';

defineProps<{ src: string }>();
const emit = defineEmits<{ (e: 'error'): void }>();

/**
 * 主 Agent「我的文件」与子智能体交付卡共用同一个工作簿查看器。
 * Excel 预览读取原始文件，不经过 LibreOffice 的打印分页，避免宽表被拆成左右续页。
 */
const VueOfficeExcel = defineAsyncComponent(async () => {
  await import('@vue-office/excel/lib/v3/index.css');
  return (await import('@vue-office/excel/lib/v3/vue-office-excel.mjs')).default;
});
</script>

<style scoped>
/* @vue-office/excel 按容器实际高度做内部虚拟滚动；flex 链必须给出确定高度。 */
.excel-file-viewer {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: #fff;
}

.excel-file-viewer :deep(.vue-office-excel) {
  height: 100%;
}
</style>
