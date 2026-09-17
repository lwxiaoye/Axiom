<template>
  <div class="analysis-empty">
    <a-empty description="AI 应用模块已移除" />
  </div>
</template>

<script lang="ts">
  export default {
    name: 'Analysis',
  };
</script>

<style scoped lang="less">
  .analysis-empty {
    display: flex;
    min-height: calc(100vh - 115px);
    align-items: center;
    justify-content: center;
    background: #f7f8fc;
  }
</style>
