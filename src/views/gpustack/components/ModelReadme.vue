<template>
  <div class="model-readme-wrap">
    <a-empty v-if="!html" description="暂无介绍" />
    <!-- eslint-disable-next-line vue/no-v-html -- markdown-it output is sanitized by DOMPurify before binding. -->
    <div v-else class="readme-body markdown-body" v-html="html"></div>
  </div>
</template>

<script lang="ts" setup>
  import { ref, watch } from 'vue';
  import MarkdownIt from 'markdown-it';
  import hljs from 'highlight.js';
  import DOMPurify from 'dompurify';
  import 'highlight.js/styles/github.css';

  const props = defineProps({
    content: { type: String, default: '' },
    fallbackDescription: { type: String, default: '' },
    homeUrl: { type: String, default: '' },
  });

  const html = ref('');

  // Render markdown and allowed HTML, then sanitize the final markup before v-html.
  const md = new MarkdownIt({
    html: true,
    linkify: true,
    breaks: true,
    highlight(str: string, lang: string): string {
      if (lang && hljs.getLanguage(lang)) {
        try {
          const highlighted = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value;
          return `<pre class="hljs"><code class="language-${lang}">${highlighted}</code></pre>`;
        } catch {
          /* fall through */
        }
      }
      return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`;
    },
  });
  // 外链强制新窗口打开
  const defaultLinkOpen =
    md.renderer.rules.link_open ||
    function (tokens, idx, options, _env, self) {
      return self.renderToken(tokens, idx, options);
    };
  md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
    const aIndex = tokens[idx].attrIndex('target');
    if (aIndex < 0) tokens[idx].attrPush(['target', '_blank']);
    else tokens[idx].attrs[aIndex][1] = '_blank';
    tokens[idx].attrSet('rel', 'noopener noreferrer');
    return defaultLinkOpen(tokens, idx, options, env, self);
  };

  function renderDescription() {
    const directContent = props.content?.trim();
    if (directContent) {
      html.value = DOMPurify.sanitize(md.render(directContent));
      return;
    }
    const description = props.fallbackDescription?.trim();
    const link = props.homeUrl ? `\n\n[查看模型主页](${props.homeUrl})` : '';
    html.value = description || props.homeUrl ? DOMPurify.sanitize(md.render(`${description}${link}`)) : '';
  }

  watch(
    () => [props.content, props.fallbackDescription, props.homeUrl],
    renderDescription,
    { immediate: true },
  );
</script>

<style scoped lang="less">
  .model-readme-wrap {
    flex: 1;
    height: 100%;
    min-height: 0;
    overflow: visible;
  }
  .readme-body {
    font-size: 13px;
    line-height: 1.65;
    color: #1f2937;
    word-break: break-word;
    :deep(h1),
    :deep(h2),
    :deep(h3),
    :deep(h4) {
      margin: 16px 0 8px;
      font-weight: 700;
      line-height: 1.3;
    }
    :deep(h1) {
      font-size: 20px;
      border-bottom: 1px solid #eaecef;
      padding-bottom: 6px;
    }
    :deep(h2) {
      font-size: 17px;
      border-bottom: 1px solid #eaecef;
      padding-bottom: 4px;
    }
    :deep(h3) {
      font-size: 15px;
    }
    :deep(p) {
      margin: 8px 0;
    }
    :deep(ul),
    :deep(ol) {
      padding-left: 22px;
      margin: 8px 0;
    }
    :deep(a) {
      color: #1677ff;
    }
    :deep(code) {
      background: rgba(175, 184, 193, 0.2);
      padding: 1px 4px;
      border-radius: 4px;
      font-size: 90%;
    }
    :deep(pre.hljs) {
      padding: 12px;
      border-radius: 6px;
      overflow: auto;
      code {
        background: transparent;
        padding: 0;
      }
    }
    :deep(blockquote) {
      margin: 8px 0;
      padding: 0 12px;
      color: #6b7280;
      border-left: 3px solid #d0d7de;
    }
    :deep(table) {
      width: 100%;
      border-collapse: collapse;
      margin: 8px 0;
      th,
      td {
        border: 1px solid #eaecef;
        padding: 6px 10px;
      }
      th {
        background: #f6f8fa;
      }
    }
    :deep(img) {
      max-width: 100%;
    }
    :deep(hr) {
      border: 0;
      border-top: 1px solid #eaecef;
      margin: 12px 0;
    }
  }
</style>
