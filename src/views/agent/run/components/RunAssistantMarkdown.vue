<!-- eslint-disable vue/no-v-html -->
<template>
  <div ref="rootRef" class="run-assistant-markdown markdown-body" @click="onContentClick" @keydown="onContentKeydown">
    <!-- eslint-disable-next-line vue/no-v-html -- markdown-it(html:false) + xss 清洗后的只读正文 -->
    <div v-html="html"></div>
  </div>

  <Teleport to="body">
    <Transition name="run-image-preview">
      <div
        v-if="previewImage"
        class="run-image-preview"
        role="dialog"
        aria-modal="true"
        :aria-label="previewImage.alt || '查看大图'"
        @click="closeImagePreview"
      >
        <button
          ref="previewCloseRef"
          type="button"
          class="run-image-preview-close"
          aria-label="关闭大图"
          @click.stop="closeImagePreview"
        >
          <span aria-hidden="true">×</span>
        </button>
        <div class="run-image-preview-stage">
          <img
            :src="previewImage.src"
            :alt="previewImage.alt || '学校资料图片大图'"
            @click.stop
          />
        </div>
        <span class="run-image-preview-hint">按 Esc 或点击空白处关闭</span>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import mermaid from 'mermaid';
import { FilterXSS, getDefaultWhiteList } from 'xss';
import { compileAnswerLayout } from '../../../peopleCenter/utils/compileAnswerLayout';
import { stopProtocolLinkAtCjkPunctuation } from '../../../peopleCenter/utils/markdownLinkify';
import 'highlight.js/styles/github.css';

const props = defineProps<{ content: string }>();
const rootRef = ref<HTMLElement | null>(null);
const previewCloseRef = ref<HTMLButtonElement | null>(null);
const previewImage = ref<{ src: string; alt: string } | null>(null);
let previousBodyOverflow: string | null = null;
let previewTrigger: HTMLElement | null = null;

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'strict',
  suppressErrorRendering: true,
});

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(code, language) {
    const requestedLanguage = String(language || '').trim().toLowerCase();
    const lang = /^[a-z0-9_+-]+$/.test(requestedLanguage) ? requestedLanguage : '';
    if (lang === 'mermaid') {
      // 先放置安全编码后的源码，DOM 更新完成后再由 Mermaid 生成 SVG。
      return `<pre class="run-mermaid-placeholder" data-src="${encodeURIComponent(code)}"></pre>`;
    }
    const escaped = lang && hljs.getLanguage(lang)
      ? hljs.highlight(code, { language: lang, ignoreIllegals: true }).value
      : markdown.utils.escapeHtml(code);
    return `<pre class="hljs"><code${lang ? ` class="language-${lang}"` : ''}>${escaped}</code></pre>`;
  },
});
markdown.linkify.set({ fuzzyLink: false });
stopProtocolLinkAtCjkPunctuation(markdown);
markdown.enable('table');

// markdown-it 已禁止原始 HTML；这里只放行语法高亮和 Mermaid 占位所需的属性，
// 否则 xss 的默认白名单会移除 hljs/language-* 并让代码工具栏无法识别。
const defaultMarkdownWhiteList = getDefaultWhiteList();
const markdownWhiteList = {
  ...defaultMarkdownWhiteList,
  a: [...defaultMarkdownWhiteList.a, 'rel'],
  span: ['class'],
  pre: ['class', 'data-src'],
  code: ['class'],
};
const markdownXss = new FilterXSS({ whiteList: markdownWhiteList });

function render(content: string) {
  return markdownXss.process(markdown.render(compileAnswerLayout(content || '').markdown));
}
const html = ref(render(props.content));

function enhanceCodeBlocks() {
  const root = rootRef.value;
  if (!root) return;
  root.querySelectorAll<HTMLElement>('pre.hljs:not([data-run-code])').forEach((pre) => {
    pre.dataset.runCode = '1';
    const code = pre.querySelector('code');
    const className = Array.from(code?.classList || []).find((item) => item.startsWith('language-')) || '';
    const lang = className.replace('language-', '') || 'text';
    const wrap = document.createElement('div');
    wrap.className = 'run-code-block';
    const head = document.createElement('div');
    head.className = 'run-code-head';
    head.innerHTML = '<span class="run-code-language"></span><span><button type="button" data-run-code-action="copy">复制</button><button type="button" data-run-code-action="download">下载</button></span>';
    head.querySelector('.run-code-language')!.textContent = lang;
    pre.parentNode?.insertBefore(wrap, pre);
    wrap.append(head, pre);
  });
}

function classifyImage(image: HTMLImageElement) {
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height) return;
  const ratio = width / height;
  image.classList.remove('run-image-landscape', 'run-image-square', 'run-image-portrait');
  image.classList.add(
    ratio >= 1.2 ? 'run-image-landscape' : ratio >= 0.82 ? 'run-image-square' : 'run-image-portrait',
  );
}

function enhanceImages() {
  const root = rootRef.value;
  if (!root) return;
  root.querySelectorAll<HTMLImageElement>('img:not([data-run-image])').forEach((image) => {
    image.dataset.runImage = '1';
    image.loading = 'lazy';
    image.decoding = 'async';
    image.tabIndex = 0;
    image.setAttribute('role', 'button');
    image.setAttribute('aria-label', `${image.alt || '图片'}，点击查看大图`);
    image.closest('p')?.classList.add('run-image-paragraph');
    if (image.complete) classifyImage(image);
    else image.addEventListener('load', () => classifyImage(image), { once: true });
  });
}

async function openImagePreview(image: HTMLImageElement) {
  const src = image.currentSrc || image.src;
  if (!src) return;
  previewTrigger = image;
  if (previousBodyOverflow === null) {
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  previewImage.value = { src, alt: image.alt || '' };
  await nextTick();
  previewCloseRef.value?.focus();
}

function closeImagePreview() {
  if (!previewImage.value) return;
  previewImage.value = null;
  if (previousBodyOverflow !== null) {
    document.body.style.overflow = previousBodyOverflow;
    previousBodyOverflow = null;
  }
  previewTrigger?.focus();
  previewTrigger = null;
}

function onContentKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' && event.key !== ' ') return;
  const image = (event.target as HTMLElement).closest<HTMLImageElement>('img[data-run-image]');
  if (!image) return;
  event.preventDefault();
  void openImagePreview(image);
}

function onPreviewKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && previewImage.value) closeImagePreview();
}

const mermaidSvgCache = new Map<string, string>();
let mermaidRenderSeq = 0;
let mermaidRendering = false;
let mermaidDirty = false;

function buildMermaidCard(source: string, svg: string): HTMLElement {
  const card = document.createElement('section');
  card.className = 'run-mermaid-card';

  const head = document.createElement('div');
  head.className = 'run-mermaid-head';
  head.innerHTML =
    '<span class="run-mermaid-title">思维导图</span>' +
    '<span class="run-mermaid-actions">' +
    '<button type="button" data-run-mermaid-action="source">查看代码</button>' +
    '<button type="button" data-run-mermaid-action="download">下载 SVG</button>' +
    '</span>';

  const diagram = document.createElement('div');
  diagram.className = 'run-mermaid-diagram';
  // Mermaid 在 strict 模式下产生并清洗 SVG；源码本身只经 textContent 进入 DOM。
  diagram.innerHTML = svg;

  const code = document.createElement('pre');
  code.className = 'run-mermaid-code';
  code.hidden = true;
  const codeElement = document.createElement('code');
  codeElement.textContent = source;
  code.appendChild(codeElement);

  card.append(head, diagram, code);
  return card;
}

async function renderMermaidBlocks() {
  const root = rootRef.value;
  if (!root) return;
  if (mermaidRendering) {
    mermaidDirty = true;
    return;
  }

  mermaidRendering = true;
  try {
    do {
      mermaidDirty = false;
      const blocks = Array.from(root.querySelectorAll<HTMLElement>('pre.run-mermaid-placeholder[data-src]'));
      for (const block of blocks) {
        const source = decodeURIComponent(block.dataset.src || '');
        if (!source.trim()) {
          block.remove();
          continue;
        }
        const cached = mermaidSvgCache.get(source);
        if (cached) {
          block.replaceWith(buildMermaidCard(source, cached));
          continue;
        }
        try {
          const valid = await mermaid.parse(source, { suppressErrors: true });
          if (!valid) throw new Error('Invalid Mermaid syntax');
          mermaidRenderSeq += 1;
          const id = `agent-run-mermaid-${mermaidRenderSeq}`;
          let svg = '';
          try {
            ({ svg } = await mermaid.render(id, source));
          } finally {
            document.getElementById(id)?.remove();
            document.getElementById(`d${id}`)?.remove();
          }
          mermaidSvgCache.set(source, svg);
          block.replaceWith(buildMermaidCard(source, svg));
        } catch {
          // 流式输出尚未闭合或模型产出非法语法时，保留源码而非留下空白卡片。
          block.textContent = source;
        }
      }
    } while (mermaidDirty);
  } finally {
    mermaidRendering = false;
  }
}

async function copy(value: string) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // 权限或浏览器限制时退回到本地选区复制。
    }
  }
  const area = document.createElement('textarea');
  area.value = value;
  document.body.append(area);
  area.select();
  document.execCommand('copy');
  area.remove();
}

function onContentClick(event: MouseEvent) {
  const image = (event.target as HTMLElement).closest<HTMLImageElement>('img[data-run-image]');
  if (image) {
    void openImagePreview(image);
    return;
  }

  const mermaidButton = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-run-mermaid-action]');
  if (mermaidButton) {
    const card = mermaidButton.closest<HTMLElement>('.run-mermaid-card');
    const action = mermaidButton.dataset.runMermaidAction;
    if (!card) return;
    if (action === 'source') {
      const diagram = card.querySelector<HTMLElement>('.run-mermaid-diagram');
      const source = card.querySelector<HTMLElement>('.run-mermaid-code');
      const showingSource = Boolean(source && !source.hidden);
      if (diagram) diagram.hidden = !showingSource;
      if (source) source.hidden = showingSource;
      mermaidButton.textContent = showingSource ? '查看代码' : '返回导图';
      return;
    }
    const svg = card.querySelector<HTMLElement>('.run-mermaid-diagram')?.innerHTML || '';
    if (!svg) return;
    const blob = new Blob([svg], { type: 'image/svg+xml' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'mindmap.svg';
    link.click();
    URL.revokeObjectURL(url);
    return;
  }

  const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-run-code-action]');
  if (!button) return;
  const code = button.closest('.run-code-block')?.querySelector('pre code')?.textContent || '';
  const action = button.dataset.runCodeAction;
  if (action === 'copy') {
    copy(code).then(() => {
      const original = button.textContent;
      button.textContent = '已复制';
      window.setTimeout(() => { button.textContent = original; }, 1200);
    });
    return;
  }
  const language = button.closest('.run-code-block')?.querySelector('.run-code-language')?.textContent || 'text';
  const ext: Record<string, string> = { js: 'js', javascript: 'js', ts: 'ts', typescript: 'ts', python: 'py', py: 'py', json: 'json', html: 'html', css: 'css', scss: 'scss', less: 'less', vue: 'vue', bash: 'sh', shell: 'sh', sql: 'sql', markdown: 'md', md: 'md' };
  const blob = new Blob([code], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `code.${ext[language.toLowerCase()] || 'txt'}`;
  link.click();
  URL.revokeObjectURL(url);
}

watch(() => props.content, async (value) => {
  html.value = render(value);
  await nextTick();
  enhanceImages();
  enhanceCodeBlocks();
  await renderMermaidBlocks();
});
onMounted(async () => {
  window.addEventListener('keydown', onPreviewKeydown);
  enhanceImages();
  enhanceCodeBlocks();
  await renderMermaidBlocks();
});
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onPreviewKeydown);
  if (previousBodyOverflow !== null) document.body.style.overflow = previousBodyOverflow;
});
</script>

<style lang="less" scoped>
.run-assistant-markdown { color: inherit; line-height: 1.72; }
.run-assistant-markdown :deep(p) { margin: 0 0 10px; }
.run-assistant-markdown :deep(p:last-child) { margin-bottom: 0; }
.run-assistant-markdown :deep(ul), .run-assistant-markdown :deep(ol) { margin: 6px 0 10px; padding-left: 22px; }
.run-assistant-markdown :deep(li + li) { margin-top: 4px; }
.run-assistant-markdown :deep(blockquote) { margin: 10px 0; padding: 7px 11px; border-left: 3px solid #d8dade; background: #fafafa; color: #5f626a; }
.run-assistant-markdown :deep(code) { padding: 2px 5px; border-radius: 5px; background: #f1f2f3; color: #b4235b; font: 0.92em 'SFMono-Regular', Consolas, monospace; }
.run-assistant-markdown :deep(.run-code-block) { margin: 14px 0; overflow: hidden; border: 1px solid #e3e5e8; border-radius: 12px; background: #fff; }
.run-assistant-markdown :deep(.run-code-head) { display: flex; align-items: center; justify-content: space-between; height: 38px; padding: 0 8px 0 14px; border-bottom: 1px solid #eaebed; background: #f6f7f8; color: #70737b; font-size: 12px; }
.run-assistant-markdown :deep(.run-code-language) { font-family: 'SFMono-Regular', Consolas, monospace; text-transform: lowercase; }
.run-assistant-markdown :deep(.run-code-head span:last-child) { display: inline-flex; gap: 6px; }
.run-assistant-markdown :deep(.run-code-head button) { padding: 3px 10px; border: 1px solid #dfe1e5; border-radius: 6px; background: #fff; color: #5d6068; font-size: 12px; cursor: pointer; }
.run-assistant-markdown :deep(.run-code-head button:hover) { border-color: #c8cbd0; background: #f0f1f2; color: #202124; }
.run-assistant-markdown :deep(pre.hljs) { max-width: 100%; margin: 0; padding: 13px 15px; overflow: auto; border: 0; border-radius: 0; background: #fff; color: #24262b; font: 13px/1.65 'SFMono-Regular', Consolas, monospace; }
.run-assistant-markdown :deep(pre.hljs code) { padding: 0; background: transparent; color: inherit; white-space: pre; }
.run-assistant-markdown :deep(.run-mermaid-card) { margin: 14px 0; overflow: hidden; border: 1px solid #dedfe3; border-radius: 12px; background: #fff; box-shadow: 0 8px 24px rgba(20, 24, 34, 0.05); }
.run-assistant-markdown :deep(.run-mermaid-head) { display: flex; align-items: center; justify-content: space-between; min-height: 42px; padding: 0 10px 0 15px; border-bottom: 1px solid #e8e9ec; background: #f8f9fa; }
.run-assistant-markdown :deep(.run-mermaid-title) { color: #2c3038; font-size: 13px; font-weight: 650; letter-spacing: 0.02em; }
.run-assistant-markdown :deep(.run-mermaid-actions) { display: inline-flex; gap: 6px; }
.run-assistant-markdown :deep(.run-mermaid-actions button) { padding: 3px 9px; border: 1px solid #dfe1e5; border-radius: 6px; background: #fff; color: #5d6068; font-size: 12px; cursor: pointer; }
.run-assistant-markdown :deep(.run-mermaid-actions button:hover) { border-color: #aeb3bb; color: #202124; }
.run-assistant-markdown :deep(.run-mermaid-diagram) { display: grid; min-height: 230px; max-height: 560px; padding: 22px; overflow: auto; place-items: center; background-color: #fff; background-image: radial-gradient(circle at 1px 1px, #e6e8ec 0.75px, transparent 0); background-size: 14px 14px; }
.run-assistant-markdown :deep(.run-mermaid-diagram svg) { display: block; width: 100%; min-width: 560px; height: auto; max-width: none; }
.run-assistant-markdown :deep(.run-mermaid-code) { max-height: 440px; margin: 0; padding: 14px 16px; overflow: auto; border: 0; border-radius: 0; background: #fff; color: #24262b; font: 12px/1.65 'SFMono-Regular', Consolas, monospace; }
.run-assistant-markdown :deep(.run-mermaid-code code) { padding: 0; background: transparent; color: inherit; white-space: pre; }
.run-assistant-markdown :deep(table) { display: block; width: 100%; max-width: 100%; margin: 10px 0; overflow-x: auto; border-collapse: collapse; }
.run-assistant-markdown :deep(th), .run-assistant-markdown :deep(td) { padding: 7px 9px; border: 1px solid #e4e5e8; text-align: left; }
.run-assistant-markdown :deep(th) { background: #fafafa; font-weight: 600; }
.run-assistant-markdown :deep(a) { color: #475569; text-decoration: underline; text-underline-offset: 2px; }
.run-assistant-markdown :deep(.run-image-paragraph) { width: 100%; margin: 14px 0 20px; }
.run-assistant-markdown :deep(img[data-run-image]) {
  display: block;
  width: auto;
  max-width: 100%;
  height: auto;
  max-height: min(62vh, 620px);
  object-fit: contain;
  border: 1px solid #e2e4e8;
  border-radius: 14px;
  background: #f7f8fa;
  box-shadow: 0 10px 30px rgba(20, 24, 34, 0.08);
  cursor: zoom-in;
  transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}
.run-assistant-markdown :deep(img[data-run-image]:hover) {
  border-color: #c6c9cf;
  box-shadow: 0 14px 36px rgba(20, 24, 34, 0.13);
  transform: translateY(-1px);
}
.run-assistant-markdown :deep(img[data-run-image]:focus-visible) {
  outline: 3px solid rgba(75, 85, 99, 0.28);
  outline-offset: 3px;
}
.run-assistant-markdown :deep(img.run-image-landscape) { width: min(100%, 640px); }
.run-assistant-markdown :deep(img.run-image-square) { width: min(100%, 320px); }
.run-assistant-markdown :deep(img.run-image-portrait) { width: min(100%, 460px); }

.run-image-preview {
  position: fixed;
  z-index: 3000;
  inset: 0;
  display: grid;
  padding: 44px 52px;
  overflow: auto;
  place-items: center;
  background: rgba(10, 12, 16, 0.88);
  backdrop-filter: blur(10px);
  cursor: zoom-out;
}
.run-image-preview-stage {
  display: grid;
  min-width: 0;
  min-height: 0;
  place-items: center;
}
.run-image-preview-stage img {
  display: block;
  width: auto;
  max-width: calc(100vw - 104px);
  height: auto;
  max-height: calc(100vh - 88px);
  object-fit: contain;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.42);
  cursor: default;
}
.run-image-preview-close {
  position: fixed;
  z-index: 1;
  top: 20px;
  right: 22px;
  display: grid;
  width: 42px;
  height: 42px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
  font: 300 30px/1 system-ui, sans-serif;
  cursor: pointer;
  place-items: center;
  transition: background 160ms ease, transform 160ms ease;
}
.run-image-preview-close:hover { background: rgba(255, 255, 255, 0.22); transform: scale(1.04); }
.run-image-preview-close:focus-visible { outline: 3px solid rgba(255, 255, 255, 0.58); outline-offset: 3px; }
.run-image-preview-hint {
  position: fixed;
  bottom: 16px;
  left: 50%;
  padding: 6px 11px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.32);
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  letter-spacing: 0.02em;
  pointer-events: none;
  transform: translateX(-50%);
}
.run-image-preview-enter-active, .run-image-preview-leave-active { transition: opacity 180ms ease; }
.run-image-preview-enter-active .run-image-preview-stage,
.run-image-preview-leave-active .run-image-preview-stage { transition: transform 180ms ease; }
.run-image-preview-enter-from, .run-image-preview-leave-to { opacity: 0; }
.run-image-preview-enter-from .run-image-preview-stage,
.run-image-preview-leave-to .run-image-preview-stage { transform: scale(0.975); }

@media (max-width: 720px) {
  .run-assistant-markdown :deep(.run-image-paragraph) { margin: 12px 0 16px; }
  .run-assistant-markdown :deep(img[data-run-image]) {
    width: 100%;
    max-height: 56vh;
    border-radius: 12px;
  }
  .run-assistant-markdown :deep(img.run-image-square) { width: min(100%, 280px); }
  .run-image-preview { padding: 36px 12px; }
  .run-image-preview-stage img {
    max-width: calc(100vw - 24px);
    max-height: calc(100vh - 72px);
    border-radius: 8px;
  }
  .run-image-preview-close { top: 12px; right: 12px; width: 40px; height: 40px; }
  .run-image-preview-hint { bottom: 10px; white-space: nowrap; }
}

@media (prefers-reduced-motion: reduce) {
  .run-assistant-markdown :deep(img[data-run-image]),
  .run-image-preview-enter-active,
  .run-image-preview-leave-active,
  .run-image-preview-enter-active .run-image-preview-stage,
  .run-image-preview-leave-active .run-image-preview-stage,
  .run-image-preview-close { transition: none; }
}
</style>
