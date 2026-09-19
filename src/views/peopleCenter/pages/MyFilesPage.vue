<template>
  <MyFilesTab @open-thread="openThread" />
</template>

<script setup lang="ts">
import MyFilesTab from '../tabs/MyFilesTab.vue';
import { useCenterContext } from '../centerContext';
import { getThreadSettings } from '../agentApi';
import { getBuiltinAssistantByPreset } from '../builtinAssistants';
import { openBuiltinAssistantPage } from '../utils/assistantRoute';

defineOptions({ name: 'CenterMyFilesPage' });

const ctx = useCenterContext();

// 点文件的「来自对话」标签：旧数据以服务端 origin 为准自动分流。
async function openThread(threadId: string) {
  if (!threadId) return;
  try {
    const settings = await getThreadSettings(threadId);
    const assistant = getBuiltinAssistantByPreset(settings.assistant_preset);
    if (assistant) {
      const separator = assistant.route.includes('?') ? '&' : '?';
      const opened = openBuiltinAssistantPage(
        `${assistant.route}${separator}thread=${encodeURIComponent(threadId)}`,
      );
      if (!opened) ctx.showError('无法打开对话页，请允许浏览器弹出窗口');
      return;
    }
    await ctx.centerChat.loadThread(threadId);
  } catch (error) {
    ctx.showError(error);
  }
}
</script>
