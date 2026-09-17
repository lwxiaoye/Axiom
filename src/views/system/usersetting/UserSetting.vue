<template>
  <ScrollContainer>
    <div class="legacy-profile-page">
      <BaseSetting />
    </div>
  </ScrollContainer>
</template>

<script setup lang="ts">
import { ScrollContainer } from '/@/components/Container';
import BaseSetting from './BaseSetting.vue';
</script>

<style scoped lang="less">
.legacy-profile-page {
  width: min(680px, calc(100% - 48px));
  margin: 24px auto;
  border: 1px solid #e7e9ee;
  border-radius: 16px;
  background: #fff;
  padding: 30px;
}

@media (max-width: 640px) {
  .legacy-profile-page {
    width: calc(100% - 24px);
    margin: 12px auto;
    padding: 20px;
  }
}
</style>
