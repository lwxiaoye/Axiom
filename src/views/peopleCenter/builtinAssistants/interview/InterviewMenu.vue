<template>
  <ADropdown v-if="snapshot && ['active', 'paused', 'completed'].includes(snapshot.status)" :trigger="['click']" placement="topRight">
    <button type="button" class="interview-menu-trigger" aria-label="面试操作" title="面试操作"><EllipsisOutlined /></button>
    <template #overlay>
      <AMenu style="font-size: 16px">
        <AMenuItem v-if="report" key="report" @click="emit('showReport')">查看面试报告</AMenuItem>
        <AMenuItem v-if="snapshot.status === 'paused'" key="resume" :disabled="busy" @click="controller?.act('resume')">继续面试</AMenuItem>
        <template v-if="snapshot.status === 'active' && snapshot.current_question">
          <AMenuItem key="hint" :disabled="busy || conflict" @click="controller?.act('hint')">提示</AMenuItem>
          <AMenuItem key="skip" :disabled="busy" @click="controller?.act('skip')">跳过本题</AMenuItem>
          <AMenuItem key="pause" :disabled="busy" @click="controller?.act('pause')">暂停面试</AMenuItem>
        </template>
        <AMenuItem v-if="snapshot.status === 'active' || snapshot.status === 'paused'" key="finish" :disabled="busy" @click="controller?.act('finish')">结束面试</AMenuItem>
      </AMenu>
    </template>
  </ADropdown>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue';
import { Dropdown as ADropdown, Menu as AMenu, MenuItem as AMenuItem } from 'ant-design-vue';
import { EllipsisOutlined } from '@ant-design/icons-vue';
import { InterviewSessionKey } from './context';
import { completedInterviewReport } from './report';

const emit = defineEmits<{ showReport: [] }>();
const controller = inject(InterviewSessionKey, null);
const snapshot = computed(() => controller?.snapshot.value ?? null);
const report = computed(() => completedInterviewReport(snapshot.value));
const busy = computed(() => Boolean(controller?.busy.value || controller?.error.value));
const conflict = computed(() => Boolean(controller?.submissionConflict.value));
</script>

<style scoped lang="less">
.interview-menu-trigger { display: inline-flex; align-items: center; justify-content: center; flex: none; width: 36px; height: 36px; padding: 0; border: 0; border-radius: 8px; background: transparent; color: #52525b; font-size: 22px; cursor: pointer; transition: background-color .18s ease; }
.interview-menu-trigger:hover { background: #f4f4f5; }
.interview-menu-trigger:focus-visible { outline: 2px solid #71717a; outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) { .interview-menu-trigger { transition: none; } }
</style>
