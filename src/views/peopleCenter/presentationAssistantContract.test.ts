import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname);
const apiSource = readFileSync(resolve(root, 'agentApi.ts'), 'utf8');
const chatSource = readFileSync(resolve(root, 'composables/useCenterChat.ts'), 'utf8');
const chatTabSource = readFileSync(resolve(root, 'tabs/ChatTab.vue'), 'utf8');
const marketPageSource = readFileSync(resolve(root, 'pages/AgentMarketPage.vue'), 'utf8');
const standalonePageSource = readFileSync(resolve(root, 'pages/BuiltinHarnessRunPage.vue'), 'utf8');
const routeSource = readFileSync(resolve(root, '../../router/routes/mainOut.ts'), 'utf8');
const marketComposableSource = readFileSync(resolve(root, 'composables/useAgentMarket.ts'), 'utf8');
const marketTabSource = readFileSync(resolve(root, 'tabs/AgentMarketTab.vue'), 'utf8');
const recommendGridSource = readFileSync(resolve(root, 'components/RecommendGrid.vue'), 'utf8');
const presentationAssistantSource = readFileSync(resolve(root, 'utils/presentationAssistant.ts'), 'utf8');
const presentationModuleSource = ['definition.ts', 'ui.ts']
  .map((file) => readFileSync(resolve(root, 'builtinAssistants/presentation', file), 'utf8'))
  .join('\n');
const registrySource = readFileSync(resolve(root, 'builtinAssistants/registry.ts'), 'utf8');
const pinnedRecommendedAgentsSource = readFileSync(resolve(root, 'utils/pinnedRecommendedAgents.ts'), 'utf8');
const centerStyleSource = readFileSync(resolve(root, 'styles/centerNew.less'), 'utf8');

describe('演示文稿助手前端契约', () => {
  it('点击入口打开新页，不在主 Agent 页面替换预设', () => {
    const enterBlock = chatSource.match(
      /function enterBuiltinAssistant\([\s\S]*?\n  \}/,
    )?.[0] || '';
    expect(enterBlock).toContain('openBuiltinAssistantPage(assistant.route)');
    expect(enterBlock).not.toContain('resetChat()');
    expect(enterBlock).not.toContain('assistantPreset.value = preset');
    expect(enterBlock).not.toContain('createAgentChatCompletion');
    expect(enterBlock).not.toContain('getThreads');
    expect(chatSource).toContain('return enterBuiltinAssistant(PRESENTATION_ASSISTANT_PRESET)');
    expect(presentationModuleSource).toContain("route: '/center/chat/ppt'");
    expect(routeSource).toContain("path: '/center/chat/ppt'");
    expect(standalonePageSource).toContain('fixedAssistantPreset: props.preset');
  });

  it('广场直接渲染管理端授权的应用记录，点击统一走新页跳转', () => {
    expect(marketPageSource).toContain('@open-agent="openAgent"');
    expect(marketPageSource).not.toContain('enterPresentationAssistant');
    expect(marketComposableSource).toContain('listBuiltinApps()');
    expect(marketComposableSource).not.toContain('myAppList');
    expect(marketComposableSource).toContain('openBuiltinAssistantPage(String(item.pcUrl))');
    expect(apiSource).toContain('assistant_preset: params.assistant_preset || undefined');
    expect(apiSource).toContain('isAssistantPreset(');
    expect(marketTabSource).toContain('{{ getMarketplaceCreatorName(item) }}');
    expect(marketTabSource).toContain('{{ formatDate(item.createTime) }}');
  });

  it('智能体广场与欢迎页共用指定的演示文稿助手 Logo，并保留旧路径', () => {
    expect(presentationModuleSource).toContain(
      "icon: '/agent-icons/builtin/presentation-assistant.png'",
    );
    expect(presentationAssistantSource).toContain(
      "export const PRESENTATION_ASSISTANT_ICON = '/agent-icons/presentation-assistant.png'",
    );
    expect(pinnedRecommendedAgentsSource).toContain('administrator-filtered marketplace list');
    expect(marketTabSource).toContain('v-if="getAgentIconUrl(item)"');
    expect(recommendGridSource).toContain('v-if="getAgentIconUrl(agent)"');
    expect(marketTabSource).not.toContain('<FilePptOutlined');
    expect(recommendGridSource).not.toContain('<FilePptOutlined');

    const builtinLogo = readFileSync(resolve(root, '../../../public/agent-icons/builtin/presentation-assistant.png'));
    const legacyLogo = readFileSync(resolve(root, '../../../public/agent-icons/presentation-assistant.png'));
    expect(builtinLogo.subarray(1, 4).toString('ascii')).toBe('PNG');
    expect(legacyLogo.subarray(1, 4).toString('ascii')).toBe('PNG');
    expect(existsSync(resolve(root, '../../../public/agent-icons/presentation-assistant.png'))).toBe(true);
  });

  it('专属空态保留定制欢迎语，同时移除推荐区和 Skill 选择', () => {
    expect(presentationModuleSource).toContain('你好，今天想制作什么演示文稿？');
    expect(presentationModuleSource).toContain('生成可编辑的演示文稿');
    expect(presentationModuleSource).toContain('hideRecommendGrid: true');
    expect(presentationModuleSource).toContain('hideSkillSelector: true');
    expect(presentationModuleSource).toContain('hidePlanMode: true');
    expect(presentationModuleSource).toContain('hideResearch: true');
    expect(chatTabSource).toContain('v-if="!uiPolicy?.hideResearch"');
    expect(chatTabSource).toContain('v-if="!uiPolicy?.hidePlanMode"');
    expect(chatTabSource).toContain('uiPolicy.value?.hidePlanMode');
    expect(chatTabSource).toContain('v-if="chatMessages.length === 0 && !interview?.hasSession.value" class="chat-intro"');
    expect(chatTabSource).toContain('getBuiltinAssistantByPreset(props.assistantPreset)');
    expect(chatTabSource).toContain('!uiPolicy?.hideRecommendGrid');
    expect(chatTabSource).toContain('!uiPolicy?.hideSkillSelector');
    expect(presentationModuleSource).toContain('showAvatar: false');
    expect(presentationModuleSource).toContain('hideComposerMascot: false');
    expect(chatTabSource).toContain('!uiPolicy?.showAvatar && !uiPolicy?.hideComposerMascot');
    expect(chatTabSource).toContain('class="composer-agent-mascot"');
    expect(chatTabSource).toContain(':variant="mascotVariant"');
    expect(centerStyleSource).not.toContain('.chat-home.empty-state.presentation-state .chat-intro');
    expect(centerStyleSource).toMatch(/\.chat-home\.empty-state\.presentation-state \.composer-dock\s*\{\s*margin-top:\s*82px;/);
  });

  it('发送前再清理普通 Skill 残留', () => {
    expect(chatSource).toContain('const sendPolicy = getBuiltinUiPolicy(assistantPreset.value)');
    expect(chatSource).toContain('if (sendPolicy?.hideSkillSelector) selectedSkills.value = []');
    expect(chatSource).toContain('if (sendPolicy?.hidePlanMode) planMode.value = false');
    expect(chatSource).toContain('if (sendPolicy?.hideResearch) researchProfile.value = false');
    expect(chatSource).toContain('turnPolicy?.hidePlanMode ? false : Boolean(opts?.planMode)');
    expect(chatSource).toContain('turnPolicy?.hideResearch ? false : Boolean(opts?.researchProfile)');
    expect(chatSource).toContain('const sentSkills = sendPolicy?.hideSkillSelector ? []');
  });

  it('registry 只保存身份和 uiPolicy key，不含校园工具 allowlist', () => {
    expect(registrySource).not.toContain('search_knowledge');
    expect(registrySource).not.toContain('ppt-studio');
    expect(presentationModuleSource).toContain("uiPolicy: 'presentation_authoring'");
  });
});
