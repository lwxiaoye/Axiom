<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <a-layout :style="'height:' + pageHeight + 'px'">
    <siderVue />
    <a-layout>
      <headerVue />
      <a-layout-content class="content">
        <RouterView />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<script lang="ts" setup>
  import { ref } from 'vue';
  import headerVue from './components/header.vue';
  import siderVue from './components/sider.vue';

  let pageHeight = ref<any>('');
  pageHeight.value = document.documentElement.clientHeight;
  window.addEventListener('resize', () => {
    pageHeight.value = document.documentElement.clientHeight;
  });
</script>
<style lang="less">
  .siderStyle {
    width: 280px !important;
    min-width: 280px !important;
    max-width: 280px !important;
  }

  .searchBox {
    input {
      background: rgba(245, 245, 245, 1) !important;
    }
  }

  .content {
    background: #fff;
    // display: flex;
  }
</style>
