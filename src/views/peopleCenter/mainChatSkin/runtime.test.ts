import { deviceForWidth, mainChatSkinStyle, resolveMainChatSkinLayout } from './runtime';
import type { HydratedMainChatSkin, MainChatSkinManifest } from './types';

const layout = {
  background: { fit: 'cover' as const, position: 'center-bottom' as const, opacity: 1 },
  content: { maxWidth: 820, topGap: 96 },
  decorations: [],
};

const manifest: MainChatSkinManifest = {
  kind: 'axiom-main-chat-skin',
  scope: 'main_chat',
  schemaVersion: 1,
  key: 'campus-main-v1',
  version: '1.0.0',
  name: '校园主对话',
  description: '',
  renderer: 'decorated-chat-v1',
  assets: [],
  theme: {
    colors: { page: '#eef6ff', accent: '#2878d2', userBubble: '#ffffff', userBubbleText: '#16324f' },
    composer: { radius: 22, shadow: 'soft' },
  },
  layouts: { desktop: layout, tablet: { ...layout, content: { maxWidth: 720, topGap: 80 } }, mobile: { ...layout, content: { maxWidth: 640, topGap: 58 } } },
};

describe('main chat skin runtime', () => {
  it('在统一断点选择电脑、平板与手机布局', () => {
    expect(deviceForWidth(1440)).toBe('desktop');
    expect(deviceForWidth(1024)).toBe('desktop');
    expect(deviceForWidth(1023)).toBe('tablet');
    expect(deviceForWidth(720)).toBe('tablet');
    expect(deviceForWidth(719)).toBe('mobile');
    expect(deviceForWidth(390)).toBe('mobile');
  });

  it('只解析 main_chat 的声明式渲染器', () => {
    expect(resolveMainChatSkinLayout(manifest, 'mobile')?.content.topGap).toBe(58);
    expect(resolveMainChatSkinLayout({ ...manifest, scope: 'sub_agent' } as any, 'mobile')).toBeNull();
    expect(resolveMainChatSkinLayout({ ...manifest, renderer: 'unknown' } as any, 'mobile')).toBeNull();
  });

  it('把所选设备布局转成受限 CSS 变量', () => {
    const skin = { manifest, assetUrls: {} } as HydratedMainChatSkin;
    const style = mainChatSkinStyle(skin, manifest.layouts.mobile);
    expect(style['--chat-content-max-width']).toBe('640px');
    expect(style['--main-chat-skin-top-gap']).toBe('58px');
    expect(style['--main-chat-skin-accent']).toBe('#2878d2');
    expect(style['--main-chat-skin-user-bubble']).toBe('#ffffff');
    expect(style['--main-chat-skin-user-bubble-text']).toBe('#16324f');
    expect(style['--main-chat-skin-composer-radius']).toBe('22px');
  });
});
