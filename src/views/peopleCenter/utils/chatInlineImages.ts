export type ChatInlineImage = {
  url?: string;
  title?: string;
  source?: string;
};

export function isRenderableChatImageUrl(url: string): boolean {
  const value = String(url || '').trim();
  return /^https?:\/\//i.test(value) || value.startsWith('/api/') || value.startsWith('/upload/');
}

function escapeAttr(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function renderChatImageFigure(image: ChatInlineImage, floating = false): string {
  const url = String(image.url || '').trim();
  if (!isRenderableChatImageUrl(url)) return '';
  const title = escapeAttr(image.title || '相关图片');
  const page = image.source && /^https?:\/\//i.test(image.source) ? image.source : '';
  let host = '';
  try {
    host = page ? new URL(page).hostname : '';
  } catch {
    // A malformed source must not prevent the image itself from being shown.
  }
  const caption = page
    ? `<a href="${escapeAttr(page)}" target="_blank" rel="noopener noreferrer">${title}${host ? ` · ${escapeAttr(host)}` : ''}</a>`
    : title;
  return `<figure class="msg-figure${floating ? ' msg-figure-float' : ''}">`
    + `<button type="button" class="msg-image-open" aria-label="放大查看：${title}">`
    + `<img src="${escapeAttr(url)}" alt="${title}" loading="lazy" referrerpolicy="no-referrer" />`
    + '</button>'
    + '<div class="msg-image-fallback" role="status"><span>图片暂时加载失败</span>'
    + '<button type="button" class="msg-image-retry">重新加载</button></div>'
    + `<figcaption>${caption}</figcaption></figure>`;
}

export function renderMissingChatImage(index: number): string {
  if (!Number.isInteger(index) || index < 1) return '';
  return '<figure class="msg-figure is-image-unavailable">'
    + `<div class="msg-image-fallback" role="status">图片 ${index} 的历史引用暂不可用</div></figure>`;
}

export function setChatImageLoadState(event: Event, failed: boolean): void {
  const image = event.target as HTMLElement | null;
  if (image?.tagName !== 'IMG') return;
  image.closest('.msg-figure')?.classList.toggle('is-image-error', failed);
}

export function retryChatImage(target: HTMLElement): boolean {
  const figure = target.closest('.msg-figure');
  const image = figure?.querySelector('img');
  const src = image?.getAttribute('src');
  if (!image || !src || !isRenderableChatImageUrl(src)) return false;
  figure?.classList.remove('is-image-error');
  image.loading = 'eager';
  // Keep signed URLs intact; cache-busting query parameters can invalidate them.
  image.removeAttribute('src');
  image.setAttribute('src', src);
  return true;
}
