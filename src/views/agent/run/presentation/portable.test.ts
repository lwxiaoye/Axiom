import type { PortableRunSkinRecord } from '../../../workflow/core/type';
import {
  portableRunComposerEmptyGapPx,
  portableRunMessagesBottomPaddingPx,
  portableRunSkinDeviceForWidth,
  portableRunSkinStyle,
  resolvePortableRunSkinLayout,
  type HydratedPortableRunSkin,
} from './portable';

const layout = {
  background: { asset: 'background', fit: 'cover' as const, position: 'center-bottom' as const, opacity: 0.9 },
  content: { maxWidth: 640, topGap: 58 },
  decorations: [],
};

const skin: PortableRunSkinRecord = {
  id: 'skin-1',
  scope: 'sub_agent',
  key: 'campus-mobile',
  assignmentKey: 'skin-campus-mobile-v1-0-0',
  version: '1.0.0',
  schemaVersion: 1,
  name: '校园移动皮肤',
  description: '',
  renderer: 'decorated-agent-run-v1',
  contentHash: 'a'.repeat(64),
  sourceType: 'imported',
  status: 'active',
  manifest: {
    kind: 'axiom-sub-agent-skin',
    scope: 'sub_agent',
    schemaVersion: 1,
    key: 'campus-mobile',
    version: '1.0.0',
    name: '校园移动皮肤',
    description: '',
    renderer: 'decorated-agent-run-v1',
    assets: [{ key: 'background', path: 'assets/bg.png', mime: 'image/png', sha256: 'b'.repeat(64) }],
    theme: {
      colors: { page: '#eef6ff', title: '#16324f', accent: '#2878d2', userBubble: '#ffffff', userBubbleText: '#16324f' },
      composer: { radius: 22, shadow: 'soft' },
    },
    layouts: { desktop: { ...layout, content: { maxWidth: 820, topGap: 128 } }, tablet: layout, mobile: layout },
  },
};

describe('portable sub-agent run skin', () => {
  it('uses the same package with deterministic desktop/tablet/mobile breakpoints', () => {
    expect(portableRunSkinDeviceForWidth(1440)).toBe('desktop');
    expect(portableRunSkinDeviceForWidth(900)).toBe('tablet');
    expect(portableRunSkinDeviceForWidth(390)).toBe('mobile');
    expect(resolvePortableRunSkinLayout(skin, 'mobile')?.content.topGap).toBe(58);
  });

  it('projects only bounded manifest values into known CSS variables', () => {
    const hydrated: HydratedPortableRunSkin = { ...skin, assetUrls: { background: 'blob:test' } };
    const style = portableRunSkinStyle(hydrated, resolvePortableRunSkinLayout(skin, 'desktop'));

    expect(style['--run-reading-width']).toBe('820px');
    expect(style['--run-empty-top-gap']).toBe('128px');
    expect(style['--run-accent']).toBe('#2878d2');
    expect(style['--run-user-bubble']).toBe('#ffffff');
    expect(style['--run-user-bubble-text']).toBe('#16324f');
    expect(style['--run-messages-bottom-padding']).toBe('148px');
    expect(style['--run-messages-bottom-padding-mobile']).toBe('148px');
    expect(Object.keys(style).some((key) => key.includes('css') || key.includes('html'))).toBe(false);
  });

  it('expands message bottom padding so composer-top cutouts do not cover the latest reply', () => {
    expect(portableRunMessagesBottomPaddingPx(layout)).toBe(148);
    expect(portableRunMessagesBottomPaddingPx({
      ...layout,
      decorations: [
        {
          asset: 'backpack',
          anchor: 'composer-top-left',
          width: 72,
          x: 28,
          y: -4,
          opacity: 1,
          visible: true,
        },
        {
          asset: 'students',
          anchor: 'composer-top-right',
          width: 178,
          x: 28,
          y: -4,
          opacity: 1,
          visible: true,
        },
      ],
    })).toBe(256);
  });

  it('empty-state composer gap clears composer-top cutouts so welcome text is not covered', () => {
    expect(portableRunComposerEmptyGapPx(layout)).toBe(34);
    expect(portableRunComposerEmptyGapPx({
      ...layout,
      decorations: [
        {
          asset: 'backpack',
          anchor: 'composer-top-left',
          width: 72,
          x: 28,
          y: -4,
          opacity: 1,
          visible: true,
        },
        {
          asset: 'students',
          anchor: 'composer-top-right',
          width: 178,
          x: 28,
          y: -4,
          opacity: 1,
          visible: true,
        },
      ],
    })).toBe(112);
  });
});
