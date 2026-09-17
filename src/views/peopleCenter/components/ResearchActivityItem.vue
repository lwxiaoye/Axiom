<template>
  <div v-if="item.kind === 'search'" class="search-activity" :class="{ detail }">
  <div class="team-search" :class="{ detail }">
    <GlobalOutlined />
    <span class="search-body"><span class="search-label" :title="item.error">{{ searchLabel }}<span v-if="item.cacheHit"> · 已复用</span><span v-if="item.snippetOnly"> · 仅摘要</span></span><span class="search-query">{{ item.query }}</span></span>
    <span class="search-count" :class="{ searching: item.status === 'running' && !settled }">
      <button v-if="item.status === 'succeeded'" class="results-toggle" type="button" :aria-expanded="resultsOpen" :aria-label="`${resultsOpen ? '收起' : '查看'} ${item.count} 个搜索结果`" @click="resultsOpen = !resultsOpen">{{ item.count }}</button>
      <span v-else-if="item.status === 'running' && !settled" class="search-dots" aria-label="正在搜索"><i /><i /><i /></span>
    </span>
  </div>
  <div class="results-clip" :class="{ open: resultsOpen }">
    <div class="results-clip-inner">
      <div class="search-results">
        <a v-for="result in item.results || []" :key="result.url" :href="result.url" target="_blank" rel="noopener noreferrer" class="result-card">
          <strong>{{ result.title }}</strong>
          <span class="result-domain"><GlobalOutlined />{{ domain(result.url) }}</span>
          <span v-if="result.snippet" class="result-snippet">{{ result.snippet }}</span>
        </a>
        <p v-if="item.results === undefined" class="results-note">这条历史搜索未保存结果明细。</p>
        <p v-else-if="!item.results.length" class="results-note">本次搜索没有可展示的来源。</p>
        <p v-else-if="item.results.length < item.count" class="results-note">显示已保存的 {{ item.results.length }} 条来源，共 {{ item.count }} 个结果。</p>
      </div>
    </div>
  </div>
  </div>
  <div v-else class="team-speech" :class="{ detail, 'owner-hidden': hideOwner }">
    <ResearchOrb v-if="!hideOwner" :role="role" :active="active" />
    <div class="speech-content"><div v-if="!hideOwner" class="speech-name">{{ name }}<span v-if="role === 'leader'">统筹</span></div>
      <div class="speech-bubble" :class="{ compact, clamped: !compact && !expanded && overflowing }">
        <div ref="textElement" class="speech-text" v-html="html" />
        <button v-if="!compact && overflowing" type="button" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? '收起' : '展开全文' }}<span aria-hidden="true">{{ expanded ? '−' : '+' }}</span></button>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';
import MarkdownIt from 'markdown-it';
import { GlobalOutlined } from '@ant-design/icons-vue';
import ResearchOrb from './ResearchOrb.vue';
import type { ResearchActivity, ResearchRole } from '../utils/researchTeam';
const props = defineProps<{ item: ResearchActivity; name: string; role: ResearchRole | 'leader'; active?: boolean; compact?: boolean; detail?: boolean; hideOwner?: boolean; settled: boolean }>();
const expanded = ref(false);
const resultsOpen = ref(false);
function domain(url: string) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; } }
const textElement = ref<HTMLElement>();
const overflowing = ref(false);
let resizeObserver: ResizeObserver | undefined;
function measureText() { overflowing.value = (textElement.value?.scrollHeight || 0) > 168; }
onMounted(() => {
  resizeObserver = new ResizeObserver(measureText);
  if (textElement.value) resizeObserver.observe(textElement.value);
  measureText();
});
watch(() => props.item.text, async () => { await nextTick(); measureText(); });
onBeforeUnmount(() => resizeObserver?.disconnect());
const markdown = new MarkdownIt({ html: false, linkify: false, breaks: true });
const html = computed(() => markdown.render(props.item.text));
const failureLabels: Record<string, string> = {
  engine_captcha: '引擎需要验证码', engine_cooldown: '引擎等待恢复',
  missing_structured_result: '搜索服务未返回可引用来源', rate_limited: '搜索服务限流',
  timeout: '来源访问超时', url_not_public: '网址不可访问', access_denied: '来源拒绝访问',
  not_configured: '服务尚未配置', unusable_body: '正文暂不可读', network_error: '网络连接失败',
};
const searchLabel = computed(() => {
  const reading = props.item.operation === 'read';
  if (props.item.status === 'succeeded') return reading ? '已读取网页' : props.item.count > 0 ? '已搜索网页' : '本次未找到网页';
  if (props.item.status === 'failed') return failureLabels[props.item.errorCode || ''] || (reading ? '网页读取失败' : '搜索失败');
  if (props.settled || props.item.status === 'interrupted') return reading ? '阅读已中断' : '搜索已中断';
  return reading ? '正在读取网页' : '正在搜索网页';
});
</script>
<style scoped lang="less">
.search-activity { min-width: 0; max-width: 100%; }
.team-search { display: flex; gap: 8px; align-items: flex-start; min-width: 0; min-height: 40px; padding: 4px 0; font-size: 13px; }
.team-search > .anticon { flex: none; margin-top: 3px; color: #8a8a8a; font-size: 14px; }
.search-body { flex: 1; min-width: 0; }
.search-label, .search-query { display: block; }
.search-label { color: #8a8a8a; font-size: 13px; line-height: 20px; }
.search-query { color: #171717; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-style: italic; font-size: 13px; line-height: 1.45; }
.search-count { flex: none; min-width: 32px; color: #555; font-size: 13px; line-height: 20px; }
.search-dots { display: inline-flex; }.search-dots i { width: 13px; height: 13px; margin-left: -4px; border-radius: 50%; background: #e5e5e3; border: 1px solid #fafafa; animation: search-pulse 1.5s ease-in-out infinite; }.search-dots i:nth-child(2) { animation-delay: .18s; }.search-dots i:nth-child(3) { animation-delay: .36s; }
.team-speech { display: flex; gap: 8px; width: 100%; min-width: 0; padding: 5px 0; }.team-speech > .research-orb { width: 24px; height: 24px; }
.speech-content { flex: 1; min-width: 0; }.speech-name { display: flex; gap: 6px; align-items: center; margin-bottom: 4px; color: #666; font-size: 12px; }.speech-name span { color: #999; }
.speech-bubble { background: #f0f0ee; border-radius: 4px 14px 14px 14px; padding: 8px 11px; color: #444; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.speech-text :deep(p) { margin: 0 0 6px; }.speech-text :deep(p:last-child) { margin-bottom: 0; }.speech-text :deep(ul),.speech-text :deep(ol) { margin: 4px 0; padding-left: 20px; }.speech-text :deep(a) { color: #526582; }.speech-text :deep(h1),.speech-text :deep(h2),.speech-text :deep(h3) { font-size: inherit; margin: 4px 0; }.speech-text :deep(pre) { white-space: pre-wrap; }
.speech-text { overflow: hidden; max-height: 2400px; transition: max-height .28s cubic-bezier(.22,1,.36,1); }
.compact .speech-text { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; max-height: none; }
.clamped .speech-text { max-height: 168px; }
.detail { gap: 10px; padding: 0; }
.detail > .research-orb { width: 26px; height: 26px; }
.detail .speech-name { min-height: 26px; margin-bottom: 7px; color: #344054; font-weight: 500; }
.detail .speech-name span { font-size: 11px; font-weight: 400; color: #788597; }
.detail .speech-bubble { background: #f7f7f5; border: 0; border-radius: 4px 18px 18px 18px; padding: 10px 13px; color: #171717; line-height: 1.55; }
.owner-hidden { padding-left: 30px; }
.team-search.detail { gap: 8px; min-height: 40px; padding: 2px 0 6px; }
.team-search.detail > .anticon { position: relative; z-index: 1; margin-top: 3px; }
.detail .search-label { color: #8a8a8a; font-size: 13px; line-height: 20px; }
.detail .search-query { color: #171717; font-family: inherit; }
.detail .search-count { min-width: 32px; padding-top: 0; text-align: center; }
.search-count .results-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 32px;
  margin: 0;
  padding: 1px 7px;
  border: 1px solid #e6e6e4;
  border-radius: 6px;
  background: #fff;
  color: #444;
  font-variant-numeric: tabular-nums;
  line-height: 20px;
  transition: background-color .15s, border-color .15s, color .15s;
}
.results-toggle:hover, .results-toggle[aria-expanded="true"] { background: #f4f4f2; border-color: #d9d9d6; color: #171717; }
.results-clip {
  display: grid;
  grid-template-rows: 0fr;
  min-width: 0;
  max-width: 100%;
  opacity: 0;
  pointer-events: none;
  transition: grid-template-rows .24s cubic-bezier(.22,1,.36,1), opacity .2s ease;
}
.results-clip.open { grid-template-rows: 1fr; opacity: 1; pointer-events: auto; }
.results-clip-inner { min-width: 0; min-height: 0; overflow: hidden; }
.search-results {
  margin: 3px 0 10px 32px;
  max-width: calc(100% - 32px);
  box-sizing: border-box;
  padding: 5px;
  border-radius: 10px;
  background: #efeeec;
}
.result-card { display: block; min-width: 0; padding: 9px 8px; border-radius: 6px; color: #171717; text-decoration: none; overflow-wrap: anywhere; }
.result-card:hover { background: #e5e4e1; color: #171717; }
.result-card strong { display: block; font-size: 13px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.result-domain { display: flex; align-items: center; gap: 7px; min-width: 0; margin-top: 5px; font-size: 12px; color: #777; overflow-wrap: anywhere; }
.result-snippet { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; margin-top: 6px; color: #666; font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.results-note { margin: 0; padding: 9px; color: #777; font-size: 12px; }
.result-card:focus-visible { outline: 2px solid #576ed8; outline-offset: 1px; }
.speech-bubble button { display: flex; align-items: center; justify-content: space-between; width: 100%; margin-top: 10px; padding-top: 8px; border-top: 1px solid #e5eaf1; color: #61718a; }
.speech-bubble button span { margin-left: 6px; }
button:focus-visible { outline: 2px solid #576ed8; outline-offset: 3px; }
.speech-bubble button { border: 0; background: none; padding: 5px 0 0; color: #888; cursor: pointer; font: inherit; font-size: 12px; }
.search-count .results-toggle { cursor: pointer; font: inherit; font-size: 13px; }
@keyframes search-pulse { 0%,100% { opacity: .45; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .search-dots i { animation: none; }
  .results-clip, .speech-text { transition: none; }
}
</style>
