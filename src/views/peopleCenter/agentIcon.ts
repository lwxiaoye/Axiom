const STATIC_FILE_BASE = '/api/sys/common/static/';
const GENERATED_ICON_BASE = '/agent-icons/generated/';
const GENERATED_ICON_CATEGORIES = new Set([
  'audio',
  'code',
  'design',
  'development',
  'image',
  'models',
  'other',
  'prompt',
  'top',
  'video',
  'work',
  'writing',
]);
const GENERATED_ICON_CATEGORY_ALIASES: Record<string, string> = {
  agent: 'models',
  chat: 'models',
  'content-detection': 'writing',
  office: 'work',
  programming: 'code',
  search: 'work',
};

export function isAbsoluteImageUrl(url: string) {
  return (
    url.startsWith('/') ||
    /^(https?:)?\/\//i.test(url) ||
    url.startsWith('data:') ||
    url.startsWith('blob:')
  );
}

export function getAgentIconUrl(item: any) {
  const icon = String(item?.appIcon || item?.icon || '').trim();
  if (!icon) return '';
  if (icon.includes('/sys/common/static/')) {
    return icon.startsWith('/') ? icon : `/${icon}`;
  }
  if (isAbsoluteImageUrl(icon)) return icon;
  return `${STATIC_FILE_BASE}${icon.replace(/^\/+/, '')}`;
}

export function getAgentInitials(item: any) {
  const name = String(item?.appName || item?.name || 'AI').trim();
  const latin = name.match(/[A-Za-z0-9]+/g)?.join('') || '';
  if (latin) return latin.slice(0, 2).toUpperCase();
  return Array.from(name).slice(0, 2).join('') || 'AI';
}

export function getAgentFallbackIcon(item: any) {
  const category = String(item?.appCategory || item?.category || 'other').trim().toLowerCase();
  const aliasedCategory = GENERATED_ICON_CATEGORY_ALIASES[category] || category;
  const normalizedCategory = GENERATED_ICON_CATEGORIES.has(aliasedCategory) ? aliasedCategory : 'other';
  return `${GENERATED_ICON_BASE}${normalizedCategory}.png`;
}

export function recoverAgentIcon(event: Event, item?: any) {
  const image = event.currentTarget as HTMLImageElement | null;
  if (!image) return;

  const fallbackIcon = getAgentFallbackIcon(item);
  if (image.dataset.fallbackApplied === 'true' || image.getAttribute('src') === fallbackIcon) {
    image.hidden = true;
    return;
  }

  image.dataset.fallbackApplied = 'true';
  image.dataset.iconShape = 'square';
  image.src = fallbackIcon;
}

export function classifyAgentIcon(event: Event) {
  const image = event.currentTarget as HTMLImageElement | null;
  if (!image || !image.naturalWidth || !image.naturalHeight) return;

  const ratio = image.naturalWidth / image.naturalHeight;
  const longestSide = Math.max(image.naturalWidth, image.naturalHeight);
  if (ratio >= 1.8) {
    image.dataset.iconShape = 'wordmark';
  } else if (ratio <= 0.72) {
    image.dataset.iconShape = 'portrait';
  } else if (longestSide <= 48) {
    image.dataset.iconShape = 'compact';
  } else {
    image.dataset.iconShape = 'square';
  }
}
