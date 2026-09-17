<!-- eslint-disable vue/no-v-html --><!-- 回答内容经 xss(md.render()) 白名单过滤，与 SubagentChatPanel 同一处理 -->
<template>
  <!-- 旁路会话（Codex composer.queuedMessage.openInSideChat 对齐 2026-07-27）
       主线任务正在跑，用户又冒出一个不相干的问题——既不该打断主线（引导），也不该排到
       队尾干等（排队）。旁路＝把它丢进一个独立会话单独回答，主线全程不受影响。
       刻意做成**轻量问答面板**：没有工具流、产物、队列、引导。旁路是「顺手问一句」，
       不是第二个主对话；要完整能力就用「移回输入框」当正经一轮发。 -->
  <div class="side-chat" :style="{ right: `${24 + index * 28}px`, bottom: `${24 + index * 12}px` }">
    <header class="sc-head">
      <span class="sc-title" :title="title">{{ title }}</span>
      <button type="button" class="sc-icon" title="把这段对话移回主对话输入框" @click="emit('sendBack', lastAnswer)">
        <RollbackOutlined />
      </button>
      <button type="button" class="sc-icon" title="关闭旁路会话" aria-label="关闭旁路会话" @click="emit('close')">
        <CloseOutlined />
      </button>
    </header>

    <div ref="listRef" class="sc-body">
      <div v-for="m in messages" :key="m.id" :class="['sc-msg', m.role]">
        <div v-if="m.role === 'user'" class="sc-bubble">{{ m.content }}</div>
        <div v-else class="sc-answer markdown-body" v-html="renderMarkdown(m.content)"></div>
      </div>
      <div v-if="loading && !streamingHasText" class="sc-thinking">正在思考…</div>
      <p v-if="error" class="sc-error">{{ error }}</p>
    </div>

    <div class="sc-composer">
      <textarea
        v-model="draft"
        rows="1"
        placeholder="在旁路里继续问…"
        :disabled="loading"
        @keydown.enter.exact.prevent="send()"
      />
      <button type="button" class="sc-send" :disabled="loading || !draft.trim()" aria-label="发送" @click="send()">
        <LoadingOutlined v-if="loading" />
        <ArrowUpOutlined v-else />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, onMounted } from 'vue';
import MarkdownIt from 'markdown-it';
import xss, { getDefaultWhiteList } from 'xss';
import { ArrowUpOutlined, CloseOutlined, LoadingOutlined, RollbackOutlined } from '@ant-design/icons-vue';
import { createAgentChatCompletion } from '../agentApi';
import { compileAnswerLayout } from '../utils/compileAnswerLayout';
import { stopProtocolLinkAtCjkPunctuation } from '../utils/markdownLinkify';

defineOptions({ name: 'SideChatPanel' });

const props = defineProps<{
  /** 打开旁路时带进来的那条消息（来自队列卡），挂载后自动发出 */
  seed: string;
  /** 同时开多个旁路时的层叠序号 */
  index: number;
  /** 会话模型：跟随主对话当前选择，不另设选择器 */
  model?: string;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  /** 把旁路的结论送回主对话输入框，由用户决定要不要正式发出去 */
  (e: 'sendBack', content: string): void;
}>();

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });
// 无协议裸文本不成链：「xxx.md」会被当 .md 域名跳外网（与 MessageList 同策略）。
md.linkify.set({ fuzzyLink: false });
stopProtocolLinkAtCjkPunctuation(md);
const markdownWhiteList = {
  ...getDefaultWhiteList(),
  a: [...getDefaultWhiteList().a, 'rel'],
};
function renderMarkdown(text: string) {
  return xss(md.render(compileAnswerLayout(text || '').markdown), { whiteList: markdownWhiteList });
}

type SideMessage = { id: number; role: 'user' | 'assistant'; content: string };

let seq = 0;
const messages = ref<SideMessage[]>([]);
const draft = ref('');
const loading = ref(false);
const error = ref('');
const listRef = ref<HTMLElement | null>(null);
// 旁路自己的会话 id：首轮由后端建，之后带上，保证旁路内部有上下文、又与主线完全隔离
let sideThreadId = '';

const title = computed(() => messages.value[0]?.content || props.seed || '旁路会话');
const streamingHasText = computed(() => {
  const last = messages.value[messages.value.length - 1];
  return last?.role === 'assistant' && Boolean(last.content);
});
const lastAnswer = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    if (messages.value[i].role === 'assistant' && messages.value[i].content) return messages.value[i].content;
  }
  return '';
});

async function scrollToEnd() {
  await nextTick();
  const el = listRef.value;
  if (el) el.scrollTop = el.scrollHeight;
}

async function send(text?: string) {
  const content = (text ?? draft.value).trim();
  if (!content || loading.value) return;
  draft.value = '';
  error.value = '';
  seq += 1;
  messages.value.push({ id: seq, role: 'user', content });
  seq += 1;
  const targetId = seq;
  messages.value.push({ id: targetId, role: 'assistant', content: '' });
  loading.value = true;
  void scrollToEnd();
  const write = (full: string) => {
    const target = messages.value.find((m) => m.id === targetId);
    if (target) target.content = full;
    void scrollToEnd();
  };
  try {
    await createAgentChatCompletion({
      message: content,
      thread_id: sideThreadId || undefined,
      model: props.model,
      stream: true,
      side_chat: true,
      onRunStarted: (info: { thread_id?: string }) => {
        if (info?.thread_id) sideThreadId = String(info.thread_id);
      },
      onDelta: (_delta: string, full: string) => write(full),
      onError: (message: string) => {
        error.value = message || '旁路会话出错了，可以重试';
      },
    } as Parameters<typeof createAgentChatCompletion>[0]);
  } catch (e) {
    error.value = (e as Error)?.message || '旁路会话出错了，可以重试';
  } finally {
    loading.value = false;
    // 空答案不留一个空气泡：出错时把占位摘掉，错误行已经如实说明了
    const target = messages.value.find((m) => m.id === targetId);
    if (target && !target.content) messages.value = messages.value.filter((m) => m.id !== targetId);
    void scrollToEnd();
  }
}

onMounted(() => {
  if (props.seed?.trim()) void send(props.seed);
});
</script>

<style scoped>
.side-chat {
  position: fixed;
  z-index: 1100;
  display: flex;
  width: 380px;
  max-width: calc(100vw - 32px);
  height: 460px;
  max-height: calc(100vh - 48px);
  flex-direction: column;
  border: 1px solid #e6e8ec;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 12px 40px rgba(17, 24, 39, 0.12);
}

.sc-head {
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid #f0f1f4;
  padding: 10px 8px 10px 14px;
}

.sc-title {
  overflow: hidden;
  flex: 1;
  color: #25272c;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sc-icon {
  display: inline-flex;
  width: 26px;
  height: 26px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #8b8f98;
  font-size: 13px;
  cursor: pointer;
}

.sc-icon:hover {
  background: #f5f6f8;
  color: #4a4f5a;
}

.sc-body {
  display: flex;
  overflow-y: auto;
  flex: 1;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
}

.sc-msg.user {
  display: flex;
  justify-content: flex-end;
}

.sc-bubble {
  max-width: 84%;
  border-radius: 12px;
  background: #f5f6f8;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.sc-answer {
  color: #25272c;
  font-size: 13px;
  line-height: 1.75;
}

.sc-thinking,
.sc-error {
  margin: 0;
  color: #9096a1;
  font-size: 12.5px;
}

.sc-error {
  color: #a8433a;
}

.sc-composer {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  border-top: 1px solid #f0f1f4;
  padding: 10px 12px;
}

.sc-composer textarea {
  max-height: 96px;
  flex: 1;
  border: 0;
  background: transparent;
  padding: 6px 0;
  color: #25272c;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.6;
  outline: none;
  resize: none;
}

.sc-send {
  display: inline-flex;
  width: 28px;
  height: 28px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 50%;
  background: #1a1a1a;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.sc-send:disabled {
  background: #d4d6dd;
  cursor: default;
}

@media (max-width: 980px) {
  .side-chat {
    right: 12px !important;
    bottom: 12px !important;
    width: calc(100vw - 24px);
    height: 60vh;
  }
}
</style>
