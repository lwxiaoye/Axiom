<template>
  <button type="button" class="run-scroll-to-bottom" title="回到最新消息" aria-label="回到最新消息">
    <ArrowDownOutlined />
  </button>
</template>

<script setup lang="ts">
import { ArrowDownOutlined } from '@ant-design/icons-vue';
</script>

<style lang="less" scoped>
.run-scroll-to-bottom { display: grid; width: 40px; height: 40px; place-items: center; padding: 0; border: 1px solid #e4e4e8; border-radius: 50%; background: #fff; color: #333; font-size: 16px; cursor: pointer; box-shadow: 0 6px 18px rgba(17, 24, 39, .14); transition: background .15s ease, color .15s ease, transform .15s ease; }
.run-scroll-to-bottom:hover { background: #111; color: #fff; transform: translateY(-1px); }
.run-scroll-to-bottom:focus-visible { outline: 2px solid #7d89a8; outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { .run-scroll-to-bottom { transition: none; } }
</style>
