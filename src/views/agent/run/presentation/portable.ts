import { computed, onBeforeUnmount, onMounted, ref, type ComputedRef } from 'vue';
import type {
  PortableRunSkinDevice,
  PortableRunSkinLayout,
  PortableRunSkinRecord,
} from '../../../workflow/core/type';

export type HydratedPortableRunSkin = PortableRunSkinRecord & {
  assetUrls: Record<string, string>;
};

export function portableRunSkinDeviceForWidth(width: number): PortableRunSkinDevice {
  if (width < 720) return 'mobile';
  if (width < 1024) return 'tablet';
  return 'desktop';
}

export function usePortableRunSkinDevice(): ComputedRef<PortableRunSkinDevice> {
  const width = ref(typeof window === 'undefined' ? 1440 : window.innerWidth);
  const update = () => { width.value = window.innerWidth; };
  onMounted(() => window.addEventListener('resize', update, { passive: true }));
  onBeforeUnmount(() => window.removeEventListener('resize', update));
  return computed(() => portableRunSkinDeviceForWidth(width.value));
}

export function isPortableRunSkin(value: unknown): value is PortableRunSkinRecord {
  if (!value || typeof value !== 'object') return false;
  const skin = value as PortableRunSkinRecord;
  return skin.scope === 'sub_agent'
    && skin.renderer === 'decorated-agent-run-v1'
    && skin.manifest?.kind === 'axiom-sub-agent-skin'
    && skin.manifest.scope === 'sub_agent'
    && skin.manifest.renderer === 'decorated-agent-run-v1';
}

export function resolvePortableRunSkinLayout(
  skin: PortableRunSkinRecord | null | undefined,
  device: PortableRunSkinDevice,
): PortableRunSkinLayout | null {
  if (!isPortableRunSkin(skin)) return null;
  return skin.manifest?.layouts?.[device] || null;
}

export function portableRunSkinAsset(
  skin: HydratedPortableRunSkin | null | undefined,
  key?: string,
): string {
  return key ? (skin?.assetUrls?.[key] || '') : '';
}

const SHADOWS = {
  none: 'none',
  soft: '0 18px 45px rgba(39, 89, 139, 0.13)',
  elevated: '0 24px 60px rgba(31, 72, 112, 0.19)',
} as const;

/** Default run-page padding only clears the composer + disclaimer, not composer-top cutouts. */
export const PORTABLE_RUN_MESSAGE_BOTTOM_PADDING_BASE = 148;
/** Manifests only store width; wide groups must not create a huge empty band. */
const PORTABLE_RUN_COMPOSER_ART_HEIGHT_CAP = 88;
const PORTABLE_RUN_COMPOSER_ART_GAP = 20;
const PORTABLE_RUN_COMPOSER_EMPTY_GAP_BASE = 34;
const PORTABLE_RUN_COMPOSER_EMPTY_ART_CAP = 88;
const PORTABLE_RUN_COMPOSER_EMPTY_ART_GAP = 24;

export function portableRunMessagesBottomPaddingPx(
  layout: PortableRunSkinLayout | null | undefined,
): number {
  if (!layout) return PORTABLE_RUN_MESSAGE_BOTTOM_PADDING_BASE;
  let art = 0;
  for (const item of layout.decorations || []) {
    if (!item.visible) continue;
    if (item.anchor !== 'composer-top-left' && item.anchor !== 'composer-top-right') continue;
    const estimatedHeight = Math.min(Math.max(item.width, 0), PORTABLE_RUN_COMPOSER_ART_HEIGHT_CAP);
    art = Math.max(art, estimatedHeight + Math.max(item.y, 0));
  }
  return art ? PORTABLE_RUN_MESSAGE_BOTTOM_PADDING_BASE + art + PORTABLE_RUN_COMPOSER_ART_GAP : PORTABLE_RUN_MESSAGE_BOTTOM_PADDING_BASE;
}

export function portableRunComposerEmptyGapPx(
  layout: PortableRunSkinLayout | null | undefined,
): number {
  if (!layout) return PORTABLE_RUN_COMPOSER_EMPTY_GAP_BASE;
  let art = 0;
  for (const item of layout.decorations || []) {
    if (!item.visible) continue;
    if (item.anchor !== 'composer-top-left' && item.anchor !== 'composer-top-right') continue;
    const estimatedHeight = Math.min(Math.max(item.width, 0), PORTABLE_RUN_COMPOSER_EMPTY_ART_CAP);
    art = Math.max(art, estimatedHeight + Math.max(item.y, 0));
  }
  return art ? art + PORTABLE_RUN_COMPOSER_EMPTY_ART_GAP : PORTABLE_RUN_COMPOSER_EMPTY_GAP_BASE;
}

function withAlpha(hex: string | undefined, alpha: number, fallback: string): string {
  const match = String(hex || '').match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})(?:[0-9a-f]{2})?$/i);
  if (!match) return fallback;
  return `rgba(${parseInt(match[1], 16)}, ${parseInt(match[2], 16)}, ${parseInt(match[3], 16)}, ${alpha})`;
}

export function portableRunSkinStyle(
  skin: HydratedPortableRunSkin | null | undefined,
  layout: PortableRunSkinLayout | null | undefined,
): Record<string, string> {
  const theme = skin?.manifest?.theme;
  if (!theme || !layout) return {};
  const colors = theme.colors || {};
  const composer = theme.composer || {};
  const page = colors.page || '#ffffff';
  const title = colors.title || '#1d1d20';
  const body = colors.body || '#5e6169';
  const accent = colors.accent || '#202124';
  const border = colors.composerBorder || '#e8e8e8';
  const messagesBottomPadding = `${portableRunMessagesBottomPaddingPx(layout)}px`;
  const composerEmptyGap = `${portableRunComposerEmptyGapPx(layout)}px`;
  return {
    '--run-reading-width': `${layout.content.maxWidth}px`,
    '--run-empty-top-gap': `${layout.content.topGap}px`,
    '--run-composer-empty-gap': composerEmptyGap,
    '--run-composer-empty-gap-mobile': composerEmptyGap,
    '--run-page-bg': page,
    '--run-main-bg': withAlpha(page, 0.9, page),
    '--run-text': title,
    '--run-welcome-title-color': title,
    '--run-welcome-kicker-color': body,
    '--run-welcome-body-color': body,
    '--run-accent': accent,
    '--run-accent-hover': accent,
    '--run-focus-ring': accent,
    '--run-user-bubble': colors.userBubble || '#f4f4f5',
    '--run-user-bubble-text': colors.userBubbleText || title,
    '--run-composer-bg': colors.composer || '#ffffff',
    '--run-composer-border': border,
    '--run-composer-radius': `${composer.radius ?? 24}px`,
    '--run-composer-shadow': SHADOWS[composer.shadow || 'soft'],
    '--run-composer-focus-border': withAlpha(accent, 0.42, border),
    '--run-messages-bottom-padding': messagesBottomPadding,
    '--run-messages-bottom-padding-mobile': messagesBottomPadding,
    '--run-left-rail-bg': withAlpha(page, 0.92, page),
    '--run-left-rail-border': withAlpha(border, 0.76, border),
    '--run-left-action-bg': withAlpha(accent, 0.09, '#f0f0f2'),
    '--run-left-action-hover-bg': withAlpha(accent, 0.15, '#e8e8ea'),
    '--run-left-control-bg': withAlpha(colors.composer || '#ffffff', 0.86, '#ffffff'),
    '--run-left-control-border': border,
    '--run-left-control-hover-border': accent,
    '--run-left-muted': body,
    '--run-left-item-color': body,
    '--run-left-item-title': title,
    '--run-left-item-hover-bg': withAlpha(accent, 0.08, '#f5f5f7'),
    '--run-left-item-active-bg': withAlpha(accent, 0.13, '#f1f2f4'),
    '--run-left-item-accent': accent,
    '--run-right-rail-bg': withAlpha(page, 0.94, page),
    '--run-right-rail-border': withAlpha(border, 0.76, border),
    '--run-right-heading': title,
    '--run-right-muted': body,
    '--run-right-divider': withAlpha(border, 0.76, border),
    '--run-right-control-bg': withAlpha(colors.composer || '#ffffff', 0.88, '#ffffff'),
    '--run-right-control-border': border,
    '--run-right-card-bg': withAlpha(colors.composer || '#ffffff', 0.82, '#ffffff'),
    '--run-right-card-border': border,
    '--run-right-card-hover-border': accent,
    '--run-right-card-hover-bg': colors.composer || '#ffffff',
    '--run-right-card-text': title,
    '--run-right-mark-bg': withAlpha(accent, 0.09, '#f4f4f6'),
    '--run-right-mark-border': border,
    '--run-right-mark-color': body,
    '--run-right-chevron': body,
  };
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = await response.json();
    const detail = payload?.detail ?? payload?.message;
    if (typeof detail === 'string' && detail.trim()) return new Error(detail);
  } catch {
    // Keep the stable fallback for non-JSON responses.
  }
  return new Error(fallback);
}

export async function hydratePortableRunSkin(
  skin: PortableRunSkinRecord,
): Promise<HydratedPortableRunSkin> {
  if (!isPortableRunSkin(skin)) throw new Error('子智能体皮肤清单不受支持');
  const { agentAuthHeaders } = await import('../../../peopleCenter/utils/agentAuthHeaders');
  const assetUrls: Record<string, string> = {};
  try {
    for (const asset of skin.manifest?.assets || []) {
      if (!asset.url) continue;
      const response = await fetch(asset.url, {
        headers: agentAuthHeaders({ Accept: asset.mime }),
      });
      if (!response.ok) throw await responseError(response, `素材加载失败：${asset.key}`);
      assetUrls[asset.key] = URL.createObjectURL(await response.blob());
    }
    return { ...skin, assetUrls };
  } catch (error) {
    for (const url of Object.values(assetUrls)) URL.revokeObjectURL(url);
    throw error;
  }
}

export function releaseHydratedPortableRunSkin(skin?: HydratedPortableRunSkin | null): void {
  if (!skin) return;
  for (const url of Object.values(skin.assetUrls)) URL.revokeObjectURL(url);
}
