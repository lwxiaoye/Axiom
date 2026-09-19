<template>
  <div class="retrieval-layout">
    <section class="query-panel">
      <label>{{ t.question }}</label>
      <a-textarea v-model:value="question" :rows="6" :placeholder="t.placeholder" />
      <div class="query-options">
        <span>Top K</span>
        <a-input-number v-model:value="topK" :min="1" :max="20" />
        <span>{{ t.threshold }}</span>
        <a-input-number v-model:value="threshold" :min="0" :max="1" :step="0.05" />
      </div>
      <div class="weight-box">
        <div class="weight-row">
          <span>{{ t.semanticWeight }}</span>
          <b>{{ semanticWeight.toFixed(1) }}</b>
        </div>
        <a-slider v-model:value="semanticWeight" :min="0" :max="1" :step="0.1" @change="syncKeywordWeight" />
        <div class="weight-row">
          <span>{{ t.keywordWeight }}</span>
          <b>{{ keywordWeight.toFixed(1) }}</b>
        </div>
        <a-slider v-model:value="keywordWeight" :min="0" :max="1" :step="0.1" @change="syncSemanticWeight" />
      </div>
      <a-button type="primary" block :loading="loading" @click="run">
        <template #icon><SearchOutlined /></template>
        {{ t.test }}
      </a-button>
    </section>

    <section class="result-panel">
      <div v-if="!result && !loading" class="empty">
        <SearchOutlined />
        <strong>{{ t.waiting }}</strong>
        <span>{{ t.waitingDesc }}</span>
      </div>
      <a-skeleton v-else-if="loading" active :paragraph="{ rows: 8 }" />
      <template v-else-if="result">
        <header class="result-header">
          <span>{{ t.hit }} {{ result.items.length }} {{ t.chunks }}</span>
          <span>{{ result.latencyMs }} ms</span>
        </header>
        <a-empty v-if="!result.items.length" :description="t.noResult" />
        <div v-else class="retrieval-result-list" role="region" aria-label="召回分段列表" tabindex="0">
          <article v-for="(item, index) in result.items" :key="item.chunkId">
            <header>
              <b>#{{ index + 1 }}</b>
              <strong>{{ item.documentName }}</strong>
              <em><span>{{ (item.score * 100).toFixed(1) }}%</span><small>相关度</small></em>
            </header>
            <div class="retrieval-rich-content">
              <template v-for="(part, partIndex) in contentParts(item)" :key="`${item.chunkId}-${partIndex}`">
                <p v-if="part.type === 'text'">{{ part.value }}</p>
                <a-image v-else :src="imageSrc(part.value)" :alt="part.alt || '知识库文档图片'" />
              </template>
            </div>
            <div v-if="!item.contentWithImages && item.imageUrls?.length" class="retrieval-images">
              <a-image v-for="url in item.imageUrls" :key="url" :src="imageSrc(url)" alt="知识库文档图片" />
            </div>
            <footer>
              <span v-if="item.pageNumber">{{ t.page }} {{ item.pageNumber }}</span>
              <span>{{ t.chunkId }} {{ item.chunkId }}</span>
            </footer>
          </article>
        </div>
      </template>
    </section>
  </div>
</template>

<script setup lang="ts">
  import { ref } from 'vue';
  import { SearchOutlined } from '@ant-design/icons-vue';
  import { useMessage } from '/@/hooks/web/useMessage';
  import { getProxyStaticFileUrl } from '/@/utils/common/fileUrl';
  import { testRetrieval } from '../knowledge.api';
  import type { RetrievalItem, RetrievalResponse } from '../knowledge.types';

  type RichContentPart = { type: 'text' | 'image'; value: string; alt?: string };

  const props = defineProps<{ knowledgeId: string; defaultTopK: number; defaultThreshold: number }>();
  const { createMessage } = useMessage();
  const question = ref('');
  const topK = ref(props.defaultTopK || 5);
  const threshold = ref(props.defaultThreshold ?? 0.3);
  const semanticWeight = ref(0.8);
  const keywordWeight = ref(0.2);
  const loading = ref(false);
  const result = ref<RetrievalResponse>();

  const t = {
    question: '\u7528\u6237\u95ee\u9898', placeholder: '\u8f93\u5165\u4e00\u4e2a\u771f\u5b9e\u95ee\u9898\uff0c\u68c0\u67e5\u77e5\u8bc6\u5e93\u662f\u5426\u80fd\u53ec\u56de\u51c6\u786e\u5185\u5bb9', threshold: '\u9608\u503c',
    semanticWeight: '\u8bed\u4e49\u6743\u91cd', keywordWeight: '\u5173\u952e\u5b57\u6743\u91cd', test: '\u6d4b\u8bd5\u53ec\u56de', waiting: '\u7b49\u5f85\u6d4b\u8bd5', waitingDesc: '\u53ec\u56de\u7ed3\u679c\u5c06\u5728\u8fd9\u91cc\u663e\u793a',
    hit: '\u547d\u4e2d', chunks: '\u4e2a\u5206\u6bb5', noResult: '\u6ca1\u6709\u8fbe\u5230\u9608\u503c\u7684\u7ed3\u679c', page: '\u7b2c', chunkId: '\u5206\u6bb5', required: '\u8bf7\u8f93\u5165\u6d4b\u8bd5\u95ee\u9898',
  };

  async function run() {
    if (!question.value.trim()) {
      createMessage.warning(t.required);
      return;
    }
    loading.value = true;
    try {
      result.value = await testRetrieval({
        knowledgeIds: [props.knowledgeId], query: question.value.trim(), topK: topK.value,
        scoreThreshold: threshold.value, semanticWeight: semanticWeight.value, keywordWeight: keywordWeight.value,
      });
    } finally {
      loading.value = false;
    }
  }

  function syncKeywordWeight() { keywordWeight.value = Number((1 - semanticWeight.value).toFixed(1)); }
  function syncSemanticWeight() { semanticWeight.value = Number((1 - keywordWeight.value).toFixed(1)); }
  function imageSrc(url?: string) { return getProxyStaticFileUrl(url || ''); }
  function contentParts(item: RetrievalItem): RichContentPart[] {
    const value = item.contentWithImages || item.content || '';
    const imagePattern = /!\[([^\]\n]*)\]\((?:<([^>\n]+)>|([^)]+))\)/g;
    const parts: RichContentPart[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = imagePattern.exec(value)) !== null) {
      const text = value.slice(lastIndex, match.index).trim();
      if (text) {
        parts.push({ type: 'text', value: text });
      }
      const url = (match[2] || match[3] || '').trim();
      if (url) {
        parts.push({ type: 'image', value: url, alt: match[1] || '知识库文档图片' });
      }
      lastIndex = match.index + match[0].length;
    }
    const tail = value.slice(lastIndex).trim();
    if (tail) {
      parts.push({ type: 'text', value: tail });
    }
    return parts.length ? parts : [{ type: 'text', value: item.content || '' }];
  }
</script>

<style scoped lang="less">
  .retrieval-layout { display: grid; grid-template-columns: minmax(280px, 390px) minmax(0, 1fr); gap: 20px; }
  .query-panel, .result-panel { border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; box-shadow: 0 10px 24px rgba(15, 23, 42, .04); }
  .query-panel { height: max-content; padding: 20px; }
  .query-panel label { display: block; margin-bottom: 10px; color: #1d2939; font-weight: 600; }
  .query-options { display: grid; grid-template-columns: auto 1fr auto 1fr; align-items: center; gap: 10px; margin: 14px 0; color: #667085; }
  .weight-box { margin-bottom: 14px; padding: 12px 14px; border: 1px solid #edf0f3; border-radius: 8px; background: #fbfcfe; }
  .weight-row { display: flex; justify-content: space-between; color: #667085; font-size: 12px; }
  .weight-row b { color: #111827; }
  .result-panel { min-width: 0; min-height: 420px; padding: 18px; }
  .empty { display: flex; min-height: 380px; align-items: center; justify-content: center; flex-direction: column; color: #98a2b3; }
  .empty > span:first-child { margin-bottom: 12px; font-size: 28px; }
  .empty strong { margin-bottom: 5px; color: #475467; }
  .result-header { display: flex; justify-content: space-between; margin-bottom: 12px; color: #667085; font-size: 13px; }
  .retrieval-result-list { max-height: min(60vh, 680px); overflow-y: auto; padding-right: 6px; overscroll-behavior: contain; scrollbar-gutter: stable; }
  .retrieval-result-list article:last-child { margin-bottom: 0; }
  article { margin-bottom: 10px; padding: 15px; border: 1px solid #eaecf0; border-radius: 8px; background: #fff; }
  article header { display: flex; align-items: center; gap: 8px; }
  article b { color: #111827; font-size: 12px; }
  article em { display: flex; align-items: baseline; gap: 4px; margin-left: auto; color: #14804a; font-size: 12px; font-weight: 600; font-style: normal; }
  article em small { color: #667085; font-size: 11px; font-weight: 400; }
  .retrieval-rich-content { margin: 12px 0; }
  .retrieval-rich-content p { margin: 8px 0; color: #475467; line-height: 1.75; white-space: pre-wrap; }
  .retrieval-rich-content :deep(.ant-image) { display: block; width: fit-content; max-width: min(100%, 360px); margin: 10px 0; }
  .retrieval-rich-content :deep(.ant-image-img) { max-width: 100%; max-height: 280px; border-radius: 6px; object-fit: contain; }
  .retrieval-images { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 10px; margin: 12px 0; }
  .retrieval-images :deep(.ant-image), .retrieval-images :deep(img) { width: 100%; height: 100px; border-radius: 6px; object-fit: cover; }
  article footer { display: flex; gap: 12px; color: #98a2b3; font-size: 11px; }
  @media (max-width: 900px) { .retrieval-layout { grid-template-columns: 1fr; } }
</style>
