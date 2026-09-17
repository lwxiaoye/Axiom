import type { Component } from 'vue';
import type { PortableRunSkinDevice, RunPresentationConfig } from '../../../workflow/core/type';
import CampusWelcomeBackdrop from './presets/campus-welcome-v1/CampusWelcomeBackdrop.vue';
import CampusWelcomeComposerDecorations from './presets/campus-welcome-v1/CampusWelcomeComposerDecorations.vue';
import CampusWelcomeRailDecoration from './presets/campus-welcome-v1/CampusWelcomeRailDecoration.vue';
import PortableRunBackdrop from './portable/PortableRunBackdrop.vue';
import PortableRunComposerDecorations from './portable/PortableRunComposerDecorations.vue';
import PortableRunRailDecoration from './portable/PortableRunRailDecoration.vue';
import {
  portableRunSkinStyle,
  resolvePortableRunSkinLayout,
  type HydratedPortableRunSkin,
} from './portable';
import {
  CAMPUS_WELCOME_PRESENTATION_PRESET,
  DEFAULT_RUN_PRESENTATION_PRESET,
  normalizeRunPresentationPreset,
} from './catalog';

export type RunPresentationRuntime = {
  key: string;
  styleVars: Record<string, string>;
  backdrop?: Component;
  composerDecoration?: Component;
  sidebarDecoration?: Component;
  inspirationDecoration?: Component;
  componentProps?: Record<string, unknown>;
  welcomeTitle: string;
  composerPlaceholder: string;
};

type RegisteredRunPresentation = Omit<
  RunPresentationRuntime,
  'key' | 'welcomeTitle' | 'composerPlaceholder'
> & {
  welcomeTitle?: string;
  composerPlaceholder?: string;
};

const DEFAULT_WELCOME_TITLE = '你好，有什么我可以帮你？';
const DEFAULT_COMPOSER_PLACEHOLDER = '输入你的问题...';

const RUN_PRESENTATION_REGISTRY: Record<string, RegisteredRunPresentation> = {
  [DEFAULT_RUN_PRESENTATION_PRESET]: {
    styleVars: {},
  },
  [CAMPUS_WELCOME_PRESENTATION_PRESET]: {
    backdrop: CampusWelcomeBackdrop,
    composerDecoration: CampusWelcomeComposerDecorations,
    sidebarDecoration: CampusWelcomeRailDecoration,
    inspirationDecoration: CampusWelcomeRailDecoration,
    welcomeTitle: DEFAULT_WELCOME_TITLE,
    composerPlaceholder: '输入你的问题，迎新助手马上帮你解答…',
    styleVars: {
      '--run-page-bg': '#eef6ff',
      '--run-main-bg': 'rgba(238, 246, 255, 0.9)',
      '--run-left-rail-bg': 'rgba(249, 252, 255, 0.92)',
      '--run-left-rail-border': 'rgba(74, 124, 170, 0.18)',
      '--run-left-action-bg': 'rgba(226, 239, 252, 0.88)',
      '--run-left-action-hover-bg': 'rgba(214, 232, 249, 0.96)',
      '--run-left-control-bg': 'rgba(255, 255, 255, 0.86)',
      '--run-left-control-border': 'rgba(86, 132, 176, 0.2)',
      '--run-left-control-hover-border': 'rgba(64, 116, 166, 0.34)',
      '--run-left-muted': '#71869b',
      '--run-left-item-color': '#38546e',
      '--run-left-item-title': '#1e405e',
      '--run-left-item-hover-bg': 'rgba(226, 239, 252, 0.72)',
      '--run-left-item-active-bg': 'rgba(214, 232, 249, 0.9)',
      '--run-left-item-accent': '#2878d2',
      '--run-right-rail-bg': 'rgba(248, 252, 255, 0.94)',
      '--run-right-rail-border': 'rgba(74, 124, 170, 0.18)',
      '--run-right-heading': '#173b5b',
      '--run-right-muted': '#71869b',
      '--run-right-divider': 'rgba(86, 132, 176, 0.16)',
      '--run-right-control-bg': 'rgba(255, 255, 255, 0.88)',
      '--run-right-control-border': 'rgba(86, 132, 176, 0.2)',
      '--run-right-control-focus-shadow': '0 0 0 3px rgba(40, 120, 210, 0.11)',
      '--run-right-card-bg': 'rgba(255, 255, 255, 0.82)',
      '--run-right-card-border': 'rgba(86, 132, 176, 0.18)',
      '--run-right-card-hover-border': 'rgba(40, 120, 210, 0.36)',
      '--run-right-card-hover-bg': 'rgba(255, 255, 255, 0.98)',
      '--run-right-card-text': '#294b68',
      '--run-right-mark-bg': 'rgba(226, 239, 252, 0.88)',
      '--run-right-mark-border': 'rgba(86, 132, 176, 0.16)',
      '--run-right-mark-color': '#47769e',
      '--run-right-chevron': '#7290a9',
      '--run-welcome-title-color': '#142f4d',
      '--run-welcome-kicker-color': '#55718e',
      '--run-welcome-body-color': '#45617d',
      '--run-accent': '#2878d2',
      '--run-accent-hover': '#1f68bc',
      '--run-user-bubble': '#ffffff',
      '--run-user-bubble-text': '#16324f',
      '--run-composer-bg': '#ffffff',
      '--run-composer-border': 'rgba(54, 111, 168, 0.16)',
      '--run-composer-radius': '20px',
      '--run-composer-shadow': '0 18px 45px rgba(39, 89, 139, 0.13)',
      '--run-composer-focus-border': 'rgba(40, 120, 210, 0.42)',
      '--run-composer-focus-shadow': '0 20px 52px rgba(39, 89, 139, 0.17)',
      '--run-composer-textarea-min-height': '58px',
      '--run-composer-textarea-padding': '18px 20px 8px',
      '--run-composer-empty-gap': '112px',
      '--run-composer-empty-gap-mobile': '72px',
      '--run-messages-bottom-padding': '256px',
      '--run-messages-bottom-padding-mobile': '244px',
      '--run-focus-ring': '#2878d2',
    },
  },
};

function safeCopy(value: unknown, fallback: string, maxLength: number) {
  const text = typeof value === 'string' ? value.trim() : '';
  return text ? text.slice(0, maxLength) : fallback;
}

/** Resolve a persisted key into an audited code renderer and declarative design tokens. */
export function resolveRunPresentation(
  config?: RunPresentationConfig | null,
  device: PortableRunSkinDevice = 'desktop',
): RunPresentationRuntime {
  const portableSkin = config?.portableSkin as HydratedPortableRunSkin | undefined;
  const portableLayout = resolvePortableRunSkinLayout(portableSkin, device);
  if (portableSkin && portableLayout && Object.keys(portableSkin.assetUrls || {}).length) {
    return {
      key: portableSkin.assignmentKey || config?.preset || portableSkin.key,
      styleVars: portableRunSkinStyle(portableSkin, portableLayout),
      backdrop: PortableRunBackdrop,
      composerDecoration: PortableRunComposerDecorations,
      sidebarDecoration: PortableRunRailDecoration,
      inspirationDecoration: PortableRunRailDecoration,
      componentProps: { skin: portableSkin, layout: portableLayout },
      welcomeTitle: safeCopy(config?.copy?.welcomeTitle, DEFAULT_WELCOME_TITLE, 80),
      composerPlaceholder: safeCopy(
        config?.copy?.composerPlaceholder,
        '输入你的问题，智能体马上帮你解答…',
        120,
      ),
    };
  }
  const key = normalizeRunPresentationPreset(config?.preset);
  const registered = RUN_PRESENTATION_REGISTRY[key] || RUN_PRESENTATION_REGISTRY[DEFAULT_RUN_PRESENTATION_PRESET];
  const welcomeTitle = registered.welcomeTitle || DEFAULT_WELCOME_TITLE;
  const composerPlaceholder = registered.composerPlaceholder || DEFAULT_COMPOSER_PLACEHOLDER;

  return {
    key,
    styleVars: registered.styleVars,
    backdrop: registered.backdrop,
    composerDecoration: registered.composerDecoration,
    sidebarDecoration: registered.sidebarDecoration,
    inspirationDecoration: registered.inspirationDecoration,
    componentProps: key === CAMPUS_WELCOME_PRESENTATION_PRESET ? { device } : undefined,
    welcomeTitle: safeCopy(config?.copy?.welcomeTitle, welcomeTitle, 80),
    composerPlaceholder: safeCopy(config?.copy?.composerPlaceholder, composerPlaceholder, 120),
  };
}
