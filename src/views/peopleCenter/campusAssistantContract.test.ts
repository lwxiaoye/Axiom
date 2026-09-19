import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname);
const chatSource = readFileSync(resolve(root, 'composables/useCenterChat.ts'), 'utf8');
const chatTabSource = readFileSync(resolve(root, 'tabs/ChatTab.vue'), 'utf8');
const marketPageSource = readFileSync(resolve(root, 'pages/AgentMarketPage.vue'), 'utf8');
const chatPageSource = readFileSync(resolve(root, 'pages/ChatPage.vue'), 'utf8');
const standalonePageSource = readFileSync(resolve(root, 'pages/BuiltinHarnessRunPage.vue'), 'utf8');
const routeSource = readFileSync(resolve(root, '../../router/routes/mainOut.ts'), 'utf8');
const apiSource = readFileSync(resolve(root, 'agentApi.ts'), 'utf8');
const typesSource = readFileSync(resolve(root, 'builtinAssistants/types.ts'), 'utf8');
const campusSource = readFileSync(resolve(root, 'utils/campusAssistant.ts'), 'utf8');
const campusModuleSource = ['definition.ts', 'ui.ts']
  .map((file) => readFileSync(resolve(root, 'builtinAssistants/campusServices', file), 'utf8'))
  .join('\n');
const registrySource = readFileSync(resolve(root, 'builtinAssistants/registry.ts'), 'utf8');
const messageListSource = readFileSync(resolve(root, 'components/MessageList.vue'), 'utf8');

describe('校园百事通前端契约', () => {
  it('使用稳定 id/preset 注册，不按中文名识别', () => {
    expect(campusModuleSource).toContain("export const CAMPUS_ASSISTANT_ID = 'builtin:campus-services'");
    expect(campusModuleSource).toContain('preset: CAMPUS_ASSISTANT_PRESET');
    expect(campusSource).toContain("export const CAMPUS_ASSISTANT_ICON = '/agent-icons/campus-services.png'");
    expect(campusSource).toContain("String(item?.id || '') === CAMPUS_ASSISTANT_ID");
    expect(campusSource).not.toContain("item?.name || '').trim() === CAMPUS_ASSISTANT_NAME");
    expect(registrySource).toContain('getBuiltinAssistantByPreset');
    expect(registrySource).toContain('getBuiltinAssistantById');
    expect(registrySource).toContain('isBuiltinAssistant');
    expect(registrySource).not.toContain('search_knowledge');
    expect(registrySource).not.toContain('ppt-studio');
    expect(existsSync(resolve(root, '../../../public/agent-icons/campus-services.png'))).toBe(true);
    expect(existsSync(resolve(root, '../../../public/agent-icons/builtin/campus-services.png'))).toBe(true);
  });

  it('点击入口在新页打开独立运行页，不替换主对话', () => {
    expect(chatSource).toContain('function enterCampusAssistant()');
    expect(chatSource).toContain('return enterBuiltinAssistant(CAMPUS_ASSISTANT_PRESET)');
    expect(chatSource).toContain('openBuiltinAssistantPage(assistant.route)');
    expect(campusModuleSource).toContain("route: '/center/chat/campus'");
    expect(routeSource).toContain("path: '/center/chat/campus'");
    expect(marketPageSource).toContain('@open-agent="openAgent"');
    expect(marketPageSource).not.toContain('enterCampusAssistant');
    expect(chatPageSource).not.toContain('enterCampusAssistant');
    expect(standalonePageSource).toContain('fixedAssistantPreset: props.preset');
    expect(standalonePageSource).toContain('threadScope: props.preset');
    expect(standalonePageSource).toContain('<RunSessionList');
    expect(typesSource).toContain("export const CAMPUS_ASSISTANT_PRESET = 'campus_services' as const");
    expect(apiSource).toContain("export type { AssistantPreset } from './builtinAssistants'");
    expect(apiSource).toContain("export type ThreadScope = 'ordinary' | AssistantPreset");
  });

  it('复用主对话 composer，保留图片输入并削减问答用不到的入口', () => {
    expect(campusModuleSource).toContain('你好，有什么校园事务想了解？');
    expect(campusModuleSource).toContain('请输入校园政策、办事流程或常见问题');
    expect(campusModuleSource).toContain("uiPolicy: 'campus_readonly'");
    expect(campusModuleSource).toContain('hideComposerMascot: false');
    expect(campusModuleSource).toContain('showAvatar: false');
    expect(campusModuleSource).toContain("mascotVariant: 'campus'");
    expect(chatTabSource).toContain("builtinAssistant.value?.mascotVariant || 'main'");
    expect(chatTabSource).toContain('v-if="!uiPolicy?.showAvatar && !uiPolicy?.hideComposerMascot && chatMessages.length === 0"');
    expect(chatTabSource).not.toContain('chatMessages.length === 0 || campusMode');
    expect(existsSync(resolve(root, '../../../public/agent-icons/builtin/campus-services-mascot-v3.png'))).toBe(true);
    expect(campusModuleSource).toContain('hideModelSelector: true');
    expect(chatTabSource).toContain('!uiPolicy?.hideModelSelector && currentRunModel');
    expect(campusModuleSource).toContain('hidePlusMenu: true');
    expect(campusModuleSource).toContain('hideFiles: true');
    expect(campusModuleSource).toContain('imageOnlyUpload: true');
    expect(campusModuleSource).toContain('allowPasteUpload: true');
    expect(campusModuleSource).toContain('hideSkillSelector: true');
    expect(chatTabSource).toContain('campus-state');
    expect(chatTabSource).toContain('Boolean(builtinAssistant) && Boolean(uiPolicy?.showAvatar)');
    expect(chatTabSource).toContain('v-if="showPlusMenu"');
    expect(chatTabSource).toContain("imageOnlyUpload ? '添加图片' : '添加照片和文件'");
    expect(chatTabSource).toContain('imageOnlyUpload.value ? CHAT_IMAGE_UPLOAD_ACCEPT : CHAT_UPLOAD_ACCEPT');
    expect(chatSource).toContain('attachments: requestAttachments.length ? requestAttachments : undefined');
    expect(chatTabSource).toContain(':hide-source-citations="campusMode"');
    expect(messageListSource).toContain('hideSourceCitations?: boolean');
    expect(messageListSource).toContain('&& !hideSourceCitations');
    expect(messageListSource).toContain('isRenderableChatImageUrl');
    expect(chatTabSource).toContain(':answer-layout="campusMode ? \'campus\' : \'standard\'"');
    expect(chatTabSource).toContain('ModelSelector v-if="!uiPolicy?.hideModelSelector"');
    expect(chatTabSource).toContain('class="composer-footer-end"');
    expect(chatTabSource).toContain('class="composer-send"');
    expect(chatSource).toContain('CAMPUS_ASSISTANT_PRESET');
  });

  it('手机图片菜单按内容撑开，不挤占完整资源菜单的二级选择空间', () => {
    expect(chatTabSource).toContain(":class=\"{ 'plus-menu-image-only': imageOnlyUpload }\"");
    expect(chatTabSource).toContain('<PictureOutlined v-if="imageOnlyUpload" /><PaperClipOutlined v-else />');
    const compactMenuStyles = chatTabSource.slice(chatTabSource.indexOf('/* 手机/平板不再沿用桌面窄条浮层'));
    expect(compactMenuStyles).toContain('@media (max-width: 1024px)');
    expect(compactMenuStyles).toMatch(/\.plus-menu\s*\{[^}]*height:\s*min\(70dvh, 540px\);/);
    expect(compactMenuStyles).toMatch(/\.plus-menu\.plus-menu-image-only\s*\{[^}]*height:\s*auto;/);
    expect(compactMenuStyles).toMatch(/\.plus-menu-image-only \.plus-mobile-close\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/);
    expect(chatTabSource).toContain('@keydown.esc.stop="closePlusMenu(true)"');
    expect(chatTabSource).toContain('bottom: calc(12px + env(safe-area-inset-bottom));');
  });

});
