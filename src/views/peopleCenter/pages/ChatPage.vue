<template>
  <ChatTab
    :messages="chatMessages"
    :loading="chatLoading"
    :restoring-history="restoringLatestThread"
    :input="chatInput"
    :model="activeModel"
    :current-run-model="currentRunModel"
    :placeholder="chatPlaceholder"
    :selected-skills="selectedSkills"
    :selected-knowledge-list="selectedKnowledgeList"
    :selected-file-list="selectedFileList"
    :selected-thread-list="selectedThreadList"
    :selected-subagent="selectedSubagent"
    :subagents="subagents"
    :skills="mentionSkills"
    :web-search="webSearchOn"
    :attachments="pendingAttachments"
    :uploading="uploadingFile"
    :recommended-agents="recommendedAgents"
    :agents-loading="recommendLoading"
    :thread-id="currentThreadId"
    :assistant-preset="assistantPreset"
    @update:input="chatInput = $event"
    @update:model="handleModelChange"
    @send="sendChat"
    @ai-edit-file="onAiEditFile"
    @save-slides="onSaveSlides"
    @stop="stopChatByUser"
    @regenerate="regenerateLast"
    @feedback="setMessageFeedback"
    @resume="submitResume"
    @edit="resendEditedMessage"
    @clarify="chooseClarifiedAgent"
    @approve="submitApproval"
    @remove-skill="removeSelectedSkill"
    @remove-knowledge="removeSelectedKnowledge"
    @update-knowledge="updateSelectedKnowledge"
    @update-files="updateSelectedFiles"
    @update-threads="updateSelectedThreads"
    @select-subagent="selectSubagent"
    @open-subagent-chat="onOpenSubagentChat"
    @remove-subagent="removeSelectedSubagent"
    @ensure-subagents="ensureSubagentsLoaded"
    @select-skill="selectSkillFromMention"
    @ensure-skills="ensureMentionSkillsLoaded"
    @toggle-web="toggleWebSearch"
    :retry-attachments="lastTurnHadAttachments"
    @upload="uploadFile"
    @remove-attachment="removeAttachment"
    @retry-attachment="retryAttachment"
    @start-agent="onStartRecommendedAgent"
    @open-agent="openAgentFromChat"
  />
  <SubagentChatPanel
    v-for="(sa, i) in openSubagents"
    :key="sa.id"
    :subagent="sa"
    :index="i"
    :parent-thread-id="currentThreadId"
    :delegation-run="findDelegationRun(sa.runKey)"
    @close="closeSubagent(sa.id)"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { message } from 'ant-design-vue';
import ChatTab from '../tabs/ChatTab.vue';
import SubagentChatPanel from '../components/SubagentChatPanel.vue';
import { useCenterContext } from '../centerContext';
import type { AgentItem } from '../agentApi';
import type { SubagentRun } from '../composables/executionTimeline';
import {
  pickPinnedRecommendedAgents,
  type PinnedRecommendedAgent,
} from '../utils/pinnedRecommendedAgents';

defineOptions({ name: 'CenterChatPage' });

const ctx = useCenterContext();
const { startChatWithAgent, openAgentFromChat } = ctx;
const { appList, appLoading } = ctx.agentMarket;
const { myAgentList, myAgentLoading, openAiAppRunner } = ctx.myAgents;
const recommendedAgents = computed(() => pickPinnedRecommendedAgents(appList.value, myAgentList.value));
const recommendLoading = computed(() =>
  (appLoading.value || myAgentLoading.value) && recommendedAgents.value.length === 0,
);

function onStartRecommendedAgent(agent: AgentItem) {
  const pinned = agent as PinnedRecommendedAgent;
  if (pinned.open === 'run' && pinned.raw) {
    openAiAppRunner(pinned.raw);
    return;
  }
  startChatWithAgent(agent);
}
const {
  chatMessages,
  chatLoading,
  restoringLatestThread,
  chatInput,
  activeModel,
  currentRunModel,
  handleModelChange,
  currentThreadId,
  assistantPreset,
  chatPlaceholder,
  selectedSkills,
  selectedKnowledgeList,
  selectedFileList,
  selectedThreadList,
  selectedSubagent,
  openSubagents,
  subagents,
  mentionSkills,
  webSearchOn,
  pendingAttachments,
  lastTurnHadAttachments,
  uploadingFile,
  sendChat,
  stopChatByUser,
  regenerateLast,
  setMessageFeedback,
  submitResume,
  resendEditedMessage,
  chooseClarifiedAgent,
  submitApproval,
  removeSelectedSkill,
  removeSelectedKnowledge,
  updateSelectedKnowledge,
  updateSelectedFiles,
  updateSelectedThreads,
  selectSubagent,
  removeSelectedSubagent,
  openSubagent,
  closeSubagent,
  ensureSubagentsLoaded,
  selectSkillFromMention,
  ensureMentionSkillsLoaded,
  toggleWebSearch,
  uploadFile,
  removeAttachment,
  retryAttachment,
} = ctx.centerChat;

function findDelegationRun(runKey?: string): SubagentRun | undefined {
  if (!runKey) return undefined;
  for (let index = chatMessages.value.length - 1; index >= 0; index -= 1) {
    const match = chatMessages.value[index].subagentRuns?.find((run) => run.runKey === runKey);
    if (match) return match;
  }
  return undefined;
}

function onOpenSubagentChat(item: Parameters<typeof openSubagent>[0], run: SubagentRun) {
  openSubagent(item, run.runKey);
}

// 保存在途：重复点「保存修改」不重复落库、不重复发重编译消息
let savingSlides = false;

/** 幻灯片手改保存：编辑源写回 slides.json（产物保存通道，原地出新版本），随即调编译端点
 *  把它重新生成成 PPTX 并覆盖「我的文件」。全程不碰主对话。
 *  P0（2026-07-26 深扫）：先落库成功再经 done(true) 关查看器；失败＝可见报错 + done(false)
 *  查看器保持打开（editedPages 还在，可直接重试）。 */
async function onSaveSlides(
  slidesFile: { id: string; filename: string },
  deckFile: { filename: string },
  pages: string[],
  done: (ok: boolean) => void,
) {
  if (savingSlides) {
    // 另一处保存在途：明确回执 false，让查看器解锁并保持打开（手改还在里面），
    // 否则 done 永不回调，那一侧的 saving 就永远悬着、✕/Esc 也被锁死。
    done(false);
    return;
  }
  // P2（2026-07-26 对抗校验）：置位必须在**任何 await 之前**。原先隔着
  // `await import('../myfiles.api')`，双击的第二次调用能在这个 microtask 窗口里穿过守卫，
  // 结果重复落库 + 重复发重编译消息（对齐 MyFilesTab.onViewerPagesSave 的写法）。
  savingSlides = true;
  // 按**文件名**指路（2026-07-27 统一文件系统）：服务端 _find_row 用同一口径消解同名歧义，
  // 而这里刚落库的正是最新那条。
  try {
    const { saveArtifactFile, compileSlidesDeck } = await import('../myfiles.api');
    // ① 编辑源先落库（秒级）——立刻 done(true) 清脏并让查看器关掉，用户不用干等编译
    await saveArtifactFile(
      slidesFile.filename, JSON.stringify(pages), currentThreadId.value || undefined,
    );
    done(true);
    savingSlides = false;
    // ② 后台重编译 PPTX（几十秒~两分钟）；失败只警告，编辑源已在不回滚脏状态
    message.loading({ content: `编辑已保存，正在生成 ${deckFile.filename}…`, key: 'slides-compile', duration: 0 });
    try {
      await compileSlidesDeck(
        slidesFile.filename, deckFile.filename, currentThreadId.value || undefined,
      );
      message.success({ content: `已保存到我的文件：${deckFile.filename}`, key: 'slides-compile' });
    } catch (ce) {
      message.warning({
        content: `编辑已保存，但生成 PPT 失败：${(ce as Error)?.message || '未知错误'}。可再点「保存修改」重试生成`,
        key: 'slides-compile',
        duration: 6,
      });
    }
  } catch (e) {
    message.error(`${(e as Error)?.message || '保存失败'}；修改仍在编辑器里，可直接重试`);
    done(false);
  } finally {
    savingSlides = false;
  }
}

/** 展览区「AI 编辑」（2026-07-20 对标 Manus 魔棒面板）：把编辑指令组装成消息直接发送——
 *  模型走既有文件精修链路（读我的文件→沙箱改→存新版本），改完的文件卡会带新预览。 */
function onAiEditFile(
  file: { filename: string },
  p: { instruction: string; scope: 'page' | 'all'; page: number },
) {
  const where = p.scope === 'page' ? `第 ${p.page} 页` : '整份文件';
  chatInput.value =
    `请修改我的文件里的《${file.filename}》（${where}）：${p.instruction}。` +
    `要求：在原文件上精修并保存，不要重做整份文件；保持其余部分不变。`;
  sendChat();
}

</script>
