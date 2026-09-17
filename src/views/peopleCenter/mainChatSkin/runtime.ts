import { computed, onBeforeUnmount, onMounted, ref, type ComputedRef } from 'vue';
import type {
  HydratedMainChatSkin,
  MainChatSkinDevice,
  MainChatSkinLayout,
  MainChatSkinManifest,
} from './types';

export function deviceForWidth(width: number): MainChatSkinDevice {
  if (width < 720) return 'mobile';
  if (width < 1024) return 'tablet';
  return 'desktop';
}

export function useMainChatSkinDevice(): ComputedRef<MainChatSkinDevice> {
  const width = ref(typeof window === 'undefined' ? 1440 : window.innerWidth);
  const update = () => { width.value = window.innerWidth; };
  onMounted(() => window.addEventListener('resize', update, { passive: true }));
  onBeforeUnmount(() => window.removeEventListener('resize', update));
  return computed(() => deviceForWidth(width.value));
}

export function resolveMainChatSkinLayout(
  manifest: MainChatSkinManifest | null | undefined,
  device: MainChatSkinDevice,
): MainChatSkinLayout | null {
  if (!manifest || manifest.scope !== 'main_chat' || manifest.renderer !== 'decorated-chat-v1') return null;
  return manifest.layouts?.[device] || null;
}

const SHADOWS = {
  none: 'none',
  soft: '0 18px 45px rgba(39, 89, 139, 0.13)',
  elevated: '0 24px 60px rgba(31, 72, 112, 0.19)',
} as const;

export function mainChatSkinStyle(
  skin: HydratedMainChatSkin | null | undefined,
  layout: MainChatSkinLayout | null | undefined,
): Record<string, string> {
  const theme = skin?.manifest?.theme;
  if (!theme || !layout) return {};
  const colors = theme.colors || {};
  const composer = theme.composer || {};
  return {
    '--chat-content-max-width': `${layout.content.maxWidth}px`,
    '--main-chat-skin-top-gap': `${layout.content.topGap}px`,
    '--main-chat-skin-page': colors.page || '#fff',
    '--main-chat-skin-title': colors.title || '#08090b',
    '--main-chat-skin-body': colors.body || '#858a95',
    '--main-chat-skin-accent': colors.accent || '#202124',
    '--main-chat-skin-composer': colors.composer || '#fff',
    '--main-chat-skin-composer-border': colors.composerBorder || '#e8e8e8',
    '--main-chat-skin-user-bubble': colors.userBubble || '#f4f4f5',
    '--main-chat-skin-user-bubble-text': colors.userBubbleText || colors.title || '#111',
    '--main-chat-skin-composer-radius': `${composer.radius ?? 24}px`,
    '--main-chat-skin-composer-shadow': SHADOWS[composer.shadow || 'soft'],
  };
}

export function mainChatSkinAsset(skin: HydratedMainChatSkin | null | undefined, key?: string): string {
  return key ? (skin?.assetUrls?.[key] || '') : '';
}
