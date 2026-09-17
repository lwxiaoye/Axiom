import { existsSync, readFileSync } from 'fs';
import { resolve } from 'path';

const runRoot = __dirname;
const runPageSource = readFileSync(resolve(runRoot, 'index.vue'), 'utf8');
const panelSource = readFileSync(resolve(runRoot, '../../peopleCenter/components/SubagentChatPanel.vue'), 'utf8');
const mainMessageListSource = readFileSync(resolve(runRoot, '../../peopleCenter/components/MessageList.vue'), 'utf8');
const disclaimerSource = readFileSync(resolve(runRoot, '../../peopleCenter/components/AgentOutputDisclaimer.vue'), 'utf8');
const builtinHarnessSource = readFileSync(resolve(runRoot, '../../peopleCenter/pages/BuiltinHarnessRunPage.vue'), 'utf8');
const compactHeaderSource = readFileSync(resolve(runRoot, 'components/RunCompactHeader.vue'), 'utf8');
const senderBadgeSource = readFileSync(resolve(runRoot, 'components/RunMessageSenderBadge.vue'), 'utf8');
const sidebarToggleSource = readFileSync(resolve(runRoot, 'components/RunSidebarToggle.vue'), 'utf8');
const inspirationPanelSource = readFileSync(resolve(runRoot, 'components/RunInspirationPanel.vue'), 'utf8');
const sidebarResizeSource = readFileSync(resolve(runRoot, 'useSidebarResize.ts'), 'utf8');
const presentationCatalogSource = readFileSync(resolve(runRoot, 'presentation/catalog.ts'), 'utf8');
const presentationRegistrySource = readFileSync(resolve(runRoot, 'presentation/registry.ts'), 'utf8');
const portablePresentationSource = readFileSync(resolve(runRoot, 'presentation/portable.ts'), 'utf8');
const presentationPickerSource = readFileSync(resolve(runRoot, 'presentation/RunPresentationPicker.vue'), 'utf8');
const presentationStudioSource = readFileSync(resolve(runRoot, '../../workflow/manage/PresentationStudioPanel.vue'), 'utf8');
const presentationApiSource = readFileSync(resolve(runRoot, '../../workflow/api/presentation.api.ts'), 'utf8');
const campusBackdropSource = readFileSync(
  resolve(runRoot, 'presentation/presets/campus-welcome-v1/CampusWelcomeBackdrop.vue'),
  'utf8',
);
const portableBackdropSource = readFileSync(resolve(runRoot, 'presentation/portable/PortableRunBackdrop.vue'), 'utf8');
const runApiSource = readFileSync(resolve(runRoot, 'agentRun.api.ts'), 'utf8');
const runMessageActionsSource = readFileSync(resolve(runRoot, 'components/RunMessageActions.vue'), 'utf8');

describe('子智能体运行页与主对话浮窗视觉契约', () => {
  it('反馈按钮复用主对话的线框图标和 Agent API 请求契约', () => {
    expect(runMessageActionsSource).toContain('<LikeOutlined />');
    expect(runMessageActionsSource).toContain('<DislikeOutlined />');
    expect(runMessageActionsSource).not.toContain('>👍</button>');
    expect(runMessageActionsSource).not.toContain('>👎</button>');
    expect(runApiSource).toContain("import { requestAgentApi, type GeneratedFile } from '../../peopleCenter/agentApi';");
    expect(runApiSource).toContain('requestAgentApi(`/chat/messages/${messageId}/feedback`');
  });

  it('两个入口复用同一条消息流页脚提示，并贴在输入框下方', () => {
    expect(disclaimerSource).toContain('AI可能会犯错，请核查重要信息');
    expect(disclaimerSource).toContain('margin: 8px auto 0');
    expect(mainMessageListSource).not.toContain('<AgentOutputDisclaimer');

    const assertBelowComposer = (source: string, snippet: string) => {
      const footerStart = source.indexOf("<footer :class=\"['run-composer'");
      const bodyStart = source.indexOf('<div class="composer-body">', footerStart);
      const disclaimerIdx = source.indexOf(snippet, footerStart);
      const footerEnd = source.indexOf('</footer>', footerStart);
      expect(footerStart).toBeGreaterThan(-1);
      expect(bodyStart).toBeGreaterThan(footerStart);
      expect(disclaimerIdx).toBeGreaterThan(bodyStart);
      expect(footerEnd).toBeGreaterThan(disclaimerIdx);
    };
    assertBelowComposer(runPageSource, '<AgentOutputDisclaimer v-if="messages.length" />');
    assertBelowComposer(panelSource, '<AgentOutputDisclaimer v-if="messages.length || showLiveDelegation" />');

    for (const rule of ['color: #8e939d', 'font-size: 12px', 'line-height: 18px', 'pointer-events: none', 'text-align: center', 'background: transparent']) {
      expect(disclaimerSource).toContain(rule);
    }
    expect(disclaimerSource).not.toMatch(/background:\s*#fff/);
  });

  it('运行中状态文案与主对话本轮处理中共用光束扫描，不带左侧转圈', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(shellSource).toContain('animation: run-generating-shimmer 1.8s linear infinite');
    expect(shellSource).toContain('background-clip: text');
    expect(runPageSource).toContain("class=\"run-generating\"");
    expect(runPageSource).not.toMatch(/class="run-generating">\s*<LoadingOutlined/);
    expect(panelSource).not.toMatch(/class="run-generating">\s*<LoadingOutlined/);
    expect(panelSource).not.toContain('LoadingOutlined');
    expect(runPageSource).not.toContain('LoadingOutlined');
    expect(runPageSource).toMatch(/class="run-generating">\s*正在思考\s*</);
    expect(panelSource).toMatch(/class="run-generating">\s*正在思考\s*</);
    expect(runPageSource).not.toContain("runningNodeLabel || '正在思考'");
    expect(panelSource).not.toContain("runningNodeLabel || '正在思考'");
    expect(panelSource).not.toContain('latestDelegationNode');
  });

  it('浮窗复用运行页壳层且只保留右上角关闭按钮', () => {
    expect(panelSource).toContain("@import '../../agent/run/agent-run-shell.less';");
    expect(panelSource).toMatch(/class="sac-close-btn"[\s\S]*?@click\.stop="emit\('close'\)"/);
    expect(panelSource).not.toContain('class="back-btn"');
    expect(panelSource).not.toContain('ArrowLeftOutlined');
  });

  it('消息流尾部留白只容纳输入框与一行提示，不把提示顶到内容区中部', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(shellSource).toContain('--run-messages-bottom-padding: 148px');
    expect(shellSource).toContain('--run-messages-bottom-padding-mobile: 148px');
    expect(shellSource).toContain('padding: 36px 32px var(--run-messages-bottom-padding)');
    expect(shellSource).toContain(
      'padding: 26px 16px calc(var(--run-messages-bottom-padding-mobile) + env(safe-area-inset-bottom))',
    );
    expect(shellSource).toMatch(/\.run-composer > :deep\(\.agent-output-disclaimer\)[\s\S]*?margin: 8px auto 0/);
    expect(shellSource).toMatch(/\.run-composer > :deep\(\.agent-output-disclaimer\)[\s\S]*?background: transparent/);
    expect(shellSource).not.toContain('.run-composer > :deep(.agent-output-disclaimer) { display: none; }');
    expect(shellSource).not.toContain('padding: 36px 32px 220px');
  });

  it('自定义输入框插画在输出后继续显示，并由主题扩展消息底部安全区', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(runPageSource).toContain('v-if="runPresentation.composerDecoration"');
    expect(runPageSource).toContain(':empty-state="isEmptyState"');
    expect(runPageSource).not.toContain('v-if="isEmptyState && runPresentation.composerDecoration"');
    expect(shellSource).toContain('var(--run-messages-bottom-padding)');
    expect(presentationRegistrySource).toContain("'--run-messages-bottom-padding': '256px'");
    expect(presentationRegistrySource).not.toContain("'--run-messages-bottom-padding': '214px'");
    expect(portablePresentationSource).toContain('--run-messages-bottom-padding');
    expect(portablePresentationSource).toContain('PORTABLE_RUN_MESSAGE_BOTTOM_PADDING_BASE = 148');
  });

  it('发送消息后保留完整皮肤背景，不用白蒙层把素材洗掉', () => {
    expect(campusBackdropSource).toContain('opacity: 0.9');
    expect(campusBackdropSource).not.toContain('opacity: 0.28');
    expect(campusBackdropSource).not.toContain('rgba(255, 255, 255, 0.8)');
    expect(portableBackdropSource).not.toContain('opacity: 0.28');
    expect(portableBackdropSource).not.toContain('rgba(255, 255, 255, 0.72)');
    expect(portableBackdropSource).toContain('filter: saturate(0.94)');
  });

  it('子智能体皮肤包在独立外观工坊提供 CRUD 与导出', () => {
    expect(presentationApiSource).toContain('importSubAgentSkin');
    expect(presentationApiSource).toContain('getSubAgentSkin');
    expect(presentationApiSource).toContain('updateSubAgentSkinMetadata');
    expect(presentationApiSource).toContain('deleteSubAgentSkin');
    expect(presentationApiSource).toContain("method: 'PATCH'");
    expect(presentationApiSource).toContain("method: 'DELETE'");
    expect(presentationStudioSource).toContain('编辑子智能体皮肤信息');
    expect(presentationStudioSource).toContain('wrap-class-name="subagent-skin-modal"');
    expect(presentationStudioSource).toContain('@confirm="deleteSelected"');
    expect(presentationStudioSource).toContain('图片或布局变更需升级包版本后重新导入');
  });

  it('会话搜索框使用紧凑侧栏比例，并保持中性无光圈焦点', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    const searchStart = shellSource.indexOf('.session-search {');
    const searchEnd = shellSource.indexOf('.agent-run-sidebar :deep(.session-list)', searchStart);
    const searchStyles = shellSource.slice(searchStart, searchEnd);

    expect(searchStyles).toContain('height: 38px');
    expect(searchStyles).toContain('padding: 0 12px');
    expect(searchStyles).toContain('border-radius: 11px');
    expect(searchStyles).toContain('.session-search:focus-within');
    expect(searchStyles).not.toContain('box-shadow');
  });

  it('AXIOM Agent 委派消息在运行页和浮窗使用同一个发送方标识', () => {
    expect(senderBadgeSource).toContain('来自 <strong>AXIOM Agent</strong> 的消息');
    expect(senderBadgeSource).not.toContain('委派任务');
    expect(senderBadgeSource).not.toContain('run-message-sender-mark');
    expect(senderBadgeSource).not.toContain('run-message-sender-dot');
    expect(senderBadgeSource).not.toContain('RobotOutlined');
    expect(runPageSource).toContain("'from-work-agent': m.senderType === 'work_agent'");
    expect(panelSource).toContain("'from-work-agent': m.senderType === 'work_agent'");
    expect(panelSource).toContain('class="run-msg user from-work-agent"');
  });

  it('浮窗从打开完整对话跳到对应智能体运行页，不再回填主对话输入框', () => {
    expect(panelSource).toContain('打开完整对话');
    expect(panelSource).toContain('openAgentRunWindow');
    expect(panelSource).toContain('class="sac-open-full"');
    expect(panelSource).not.toContain('送回主任务');
    expect(panelSource).not.toContain("emit('sendBack'");
    expect(panelSource).not.toContain('class="sac-sendback"');
  });

  it('运行页与浮窗共用主对话同款侧栏折叠按钮和折叠状态', () => {
    expect(sidebarToggleSource).toContain('<rect width="18" height="18" x="3" y="3" rx="2" />');
    expect(sidebarToggleSource).toContain('<path d="M9 3v18" />');
    expect(sidebarToggleSource).toContain("collapsed ? '展开侧边栏' : '收起侧边栏'");
    expect(runPageSource).toContain('<RunSidebarToggle :collapsed="sidebarCollapsed" @toggle="toggleSidebar" />');
    expect(panelSource).toContain('<RunSidebarToggle');
    expect(panelSource).toContain("'sidebar-collapsed': sidebarCollapsed");
    expect(sidebarResizeSource).toContain('const sidebarCollapsed = ref(false)');
    expect(sidebarResizeSource).toContain('sidebarCollapsed.value = !sidebarCollapsed.value');
  });

  it('Agent 类型小字放在名称下方，折叠按钮保持独立在品牌行最右侧', () => {
    expect(runPageSource).toMatch(/class="run-brand-copy"[\s\S]*?<strong>[\s\S]*?class="run-brand-type"[\s\S]*?<RunSidebarToggle/);
    expect(panelSource).toMatch(/class="run-brand-copy"[\s\S]*?<strong>[\s\S]*?class="run-brand-type"[\s\S]*?<RunSidebarToggle/);
  });

  it('两个入口复用右侧场景任务栏，并移除中间的三张推荐卡片', () => {
    expect(runPageSource).toContain('<RunInspirationPanel');
    expect(panelSource).toContain('<RunInspirationPanel');
    expect(runPageSource).not.toContain('class="quick-suggestions"');
    expect(panelSource).not.toContain('class="quick-suggestions"');
    expect(inspirationPanelSource).toContain('.ant-select-item-option-selected.ant-select-item-option-active:not(.ant-select-item-option-disabled)');
    expect(inspirationPanelSource).toContain('background: #111827 !important');
    expect(inspirationPanelSource).toContain('color: #fff !important');
    expect(inspirationPanelSource).not.toContain('box-shadow: inset 2px 0 0 #25272d');
    expect(inspirationPanelSource).not.toContain('show-search');
    expect(inspirationPanelSource).toContain('问题推荐');
    expect(inspirationPanelSource).not.toContain('可以直接让它做');
    expect(inspirationPanelSource).toContain('场景与推荐');
    expect(inspirationPanelSource).toContain('按场景快速找到问题');
    expect(inspirationPanelSource).not.toContain('{{ scenes.length }} 类');
    expect(inspirationPanelSource).not.toContain('{{ activeScene.tasks.length }} 个问题');
    expect(inspirationPanelSource).toContain('暂未配置推荐内容');
    expect(inspirationPanelSource).not.toContain('可以帮你做的事情');
  });

  it('右栏折叠按钮与侧栏分离，并用宽度过渡做收起展开动画', () => {
    expect(inspirationPanelSource).toContain('class="run-inspiration-edge-toggle"');
    expect(inspirationPanelSource).toContain("'run-inspiration-shell'");
    expect(inspirationPanelSource).toContain('transform: translate(calc(-100% - 12px), -50%)');
    expect(inspirationPanelSource).toContain('class="run-inspiration-edge-chevron"');
    expect(inspirationPanelSource).toContain('border-radius: 7px');
    expect(inspirationPanelSource).toContain('width: 24px');
    expect(inspirationPanelSource).toContain('height: 24px');
    expect(inspirationPanelSource).toContain('top: 50%');
    expect(inspirationPanelSource).toContain('opacity: 0');
    expect(inspirationPanelSource).toContain('.run-inspiration-shell:hover .run-inspiration-edge-toggle');
    expect(inspirationPanelSource).toContain('transition: width 0.28s cubic-bezier(0.22, 1, 0.36, 1), flex-basis 0.28s cubic-bezier(0.22, 1, 0.36, 1)');
    expect(inspirationPanelSource).toContain("width: (collapsed ? 0 : width) + 'px'");
    expect(inspirationPanelSource).toContain("flexBasis: (collapsed ? 0 : width) + 'px'");
    expect(inspirationPanelSource).toContain("width: var(--run-inspiration-width, 272px)");
    expect(inspirationPanelSource).toContain('<div class="run-inspiration-content">');
    expect(inspirationPanelSource).not.toMatch(/v-if="!collapsed"[\s\S]{0,40}class="run-inspiration-content"/);
    expect(inspirationPanelSource).toContain('rotate(180deg)');
    expect(inspirationPanelSource).not.toContain('class="run-inspiration-head-toggle"');
    expect(inspirationPanelSource).not.toContain('RunSidebarToggle');

    const runFillStart = runPageSource.indexOf('async function fillSuggestedTask');
    const runFillEnd = runPageSource.indexOf('\nonMounted', runFillStart);
    const runFillSource = runPageSource.slice(runFillStart, runFillEnd);
    expect(runFillSource).toContain('input.value = task');
    expect(runFillSource).toContain('composerInputRef.value?.focus()');
    expect(runFillSource).not.toContain('send(');

    const panelFillStart = panelSource.indexOf('async function fillSuggestedTask');
    const panelFillEnd = panelSource.indexOf('\nasync function onSend', panelFillStart);
    const panelFillSource = panelSource.slice(panelFillStart, panelFillEnd);
    expect(panelFillSource).toContain('input.value = value');
    expect(panelFillSource).toContain('inputRef.value?.focus()');
    expect(panelFillSource).not.toContain('onSend(');
  });

  it('平板和手机端共用紧凑顶栏，普通与委派智能体保留场景入口', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(runPageSource).toContain("const RUN_COMPACT_SHELL_QUERY = '(max-width: 1024px)'");
    expect(runPageSource).toContain('window.matchMedia(RUN_COMPACT_SHELL_QUERY)');
    expect(runPageSource).toContain('if (runShellStartsCompact)');
    expect(panelSource).toContain("const SUBAGENT_COMPACT_SHELL_QUERY = '(max-width: 1024px)'");
    expect(panelSource).toContain('window.matchMedia(SUBAGENT_COMPACT_SHELL_QUERY)');
    expect(panelSource).toContain('if (subagentStartsCompact)');
    expect(runPageSource).toContain('sidebarCollapsed.value = true');
    expect(runPageSource).toContain('inspirationCollapsed.value = true');
    expect(panelSource).toContain('sidebarCollapsed.value = true');
    expect(panelSource).toContain('inspirationCollapsed.value = true');
    expect(runPageSource).toContain('<RunCompactHeader');
    expect(panelSource).toContain('<RunCompactHeader');
    expect(builtinHarnessSource).toContain('<RunCompactHeader');
    expect(runPageSource).toContain('show-inspiration');
    expect(panelSource).toContain('show-inspiration');
    expect(builtinHarnessSource).not.toContain('show-inspiration');
    expect(compactHeaderSource).toContain('class="run-compact-header"');
    expect(compactHeaderSource).toContain('aria-label="打开场景问题推荐"');
    expect(compactHeaderSource).toContain('<span>场景</span>');
    expect(runPageSource).toContain('class="run-mobile-sidebar-backdrop"');
    expect(runPageSource).not.toContain('class="run-mobile-inspiration-toggle"');
    expect(panelSource).not.toContain('class="run-mobile-inspiration-toggle"');
    expect(sidebarToggleSource).not.toContain('run-sidebar-toggle-label');
    expect(sidebarToggleSource).not.toContain("collapsed ? '会话' : '收起'");
    expect(sidebarToggleSource).toContain(":aria-label=\"collapsed ? '展开侧边栏' : '收起侧边栏'\"");
    expect(shellSource).toContain('@media (max-width: 1024px)');
    expect(shellSource).toContain('width: min(86vw, 340px)');
    expect(shellSource).toContain('.run-sidebar-toggle { width: 44px; height: 44px;');
    expect(shellSource).toContain('.send-btn { width: 44px; height: 44px; }');
    expect(shellSource).toContain('padding-top: clamp(42px, var(--run-empty-top-gap, 52px), 62px)');
    expect(shellSource).toContain('env(safe-area-inset-bottom)');
    expect(inspirationPanelSource).toContain('class="run-inspiration-mobile-backdrop"');
    expect(inspirationPanelSource).toContain('class="run-inspiration-mobile-close"');
    expect(inspirationPanelSource).toContain('width: 44px');
    expect(inspirationPanelSource).toContain('height: 44px');
    expect(inspirationPanelSource).toContain('height: min(72dvh, 620px)');
    expect(inspirationPanelSource).toContain('width: 42px');
    expect(inspirationPanelSource).toContain('height: 4px');
    expect(presentationStudioSource).toContain('grid-template-rows: 56px minmax(0, 1fr)');
    expect(presentationStudioSource).toContain('margin-top: 52px');
    expect(presentationStudioSource).not.toContain('margin-top: 112px');
  });

  it('手机与 iPad 点选会话历史后立即收起抽屉，不等消息加载完成', () => {
    const sliceFn = (source: string, marker: string) => {
      const start = source.indexOf(marker);
      expect(start).toBeGreaterThan(-1);
      const from = start + marker.length;
      const nextFn = source.indexOf('\nfunction ', from);
      const nextAsync = source.indexOf('\nasync function ', from);
      const ends = [source.length];
      if (nextFn > start) ends.push(nextFn);
      if (nextAsync > start) ends.push(nextAsync);
      return source.slice(start, Math.min(...ends));
    };
    const assertCollapseBeforeLoad = (source: string, marker: string, loadCall: string) => {
      const body = sliceFn(source, marker);
      const collapseIdx = body.indexOf('sidebarCollapsed.value = true');
      const loadIdx = body.indexOf(loadCall);
      expect(collapseIdx).toBeGreaterThan(-1);
      expect(loadIdx).toBeGreaterThan(-1);
      expect(collapseIdx).toBeLessThan(loadIdx);
    };

    expect(runPageSource).toContain('@select="onPickSession"');
    expect(runPageSource).toContain('const isCompactRun = ref(runShellStartsCompact)');
    expect(runPageSource).toContain('if (isCompactRun.value) sidebarCollapsed.value = true');
    assertCollapseBeforeLoad(runPageSource, 'function onPickSession', 'selectSession(');
    assertCollapseBeforeLoad(panelSource, 'async function onPickSession', 'selectSession(');
    assertCollapseBeforeLoad(builtinHarnessSource, 'async function selectConversation', 'loadThread(');
  });

  it('所有运行页的会话历史抽屉都按主 Agent 曲线滑入，而不是改布局直接弹出', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    const mobileShellStart = shellSource.indexOf('@media (max-width: 1024px)');
    const mobileShellEnd = shellSource.indexOf('@media (max-width: 719px)', mobileShellStart);
    const mobileShellSource = shellSource.slice(mobileShellStart, mobileShellEnd);
    for (const source of [runPageSource, panelSource, builtinHarnessSource]) {
      expect(source).toContain('<Transition name="run-history-backdrop">');
      expect(source).toContain('class="run-mobile-sidebar-backdrop"');
    }
    expect(shellSource).toContain('transform: translateX(-105%)');
    expect(shellSource).toContain('transform 0.24s cubic-bezier(0.22, 1, 0.36, 1)');
    expect(shellSource).toContain('visibility 0s linear 0.24s');
    expect(shellSource).toContain('.run-history-backdrop-enter-active');
    expect(shellSource).toContain('transition: opacity 0.18s ease');
    expect(mobileShellSource).not.toMatch(/sidebar-collapsed \.agent-run-sidebar\s*\{[\s\S]{0,240}position:\s*relative/);
  });

  it('手机与 iPad 空对话的输入框固定在底部，不再跟在欢迎文案后卡在屏幕中间', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    const mobileComposerStart = shellSource.indexOf('@media (max-width: 1024px)');
    const mobileComposerEnd = shellSource.indexOf('@media (max-width: 719px)', mobileComposerStart);
    const mobileComposerSource = shellSource.slice(mobileComposerStart, mobileComposerEnd);
    expect(mobileComposerSource).toMatch(/\.run-messages\.is-empty-state\s*\{[\s\S]*?flex:\s*1 1 auto;[\s\S]*?overflow-y:\s*auto;/);
    expect(mobileComposerSource).toMatch(/\.run-composer\.is-empty-state\s*\{[\s\S]*?position:\s*absolute;[\s\S]*?bottom:\s*calc\(12px \+ env\(safe-area-inset-bottom\)\);/);
    expect(mobileComposerSource).toContain('transform: translateX(-50%)');
    expect(mobileComposerSource).not.toContain('margin-top: var(--run-composer-empty-gap-mobile)');
  });

  it('独立运行页按受控 preset key 换装，浮窗和未配置智能体继续使用标准外观', () => {
    expect(runPageSource).toContain('resolveRunPresentation(runtimePresentationConfig.value, runSkinDevice.value)');
    expect(runPageSource).toContain('hydratePortableRunSkin(portableSkin)');
    expect(runPageSource).toContain('已安全回退为标准外观');
    expect(runPageSource).toContain(':data-presentation-preset="runPresentation.key"');
    expect(runPageSource).toContain("'has-custom-presentation': runPresentation.key !== 'default'");
    expect(panelSource).not.toContain('resolveRunPresentation');
    expect(presentationCatalogSource).toContain("CAMPUS_WELCOME_PRESENTATION_PRESET = 'campus-welcome-v1'");
    expect(presentationRegistrySource).toContain('[CAMPUS_WELCOME_PRESENTATION_PRESET]');
    expect(presentationRegistrySource).toContain('normalizeRunPresentationPreset(config?.preset)');
    expect(portablePresentationSource).toContain("skin.scope === 'sub_agent'");
    expect(presentationRegistrySource).toContain('PortableRunBackdrop');
    expect(presentationPickerSource).toContain('RUN_PRESENTATION_PRESETS');
    expect(existsSync(resolve(runRoot, 'presentation/presets/campus-welcome-v1/assets/campus-background.jpg'))).toBe(true);
    expect(existsSync(resolve(runRoot, 'presentation/presets/campus-welcome-v1/assets/backpack.png'))).toBe(true);
    expect(existsSync(resolve(runRoot, 'presentation/presets/campus-welcome-v1/assets/student-group.png'))).toBe(true);
  });

  it('关闭文件上传时不渲染加号，自定义皮肤空态欢迎区略下移', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(runPageSource).toContain('v-if="fileUploadEnabled" class="composer-input-actions"');
    expect(panelSource).toContain('v-if="fileUploadEnabled" class="composer-input-actions"');
    expect(shellSource).toContain('.send-btn { margin-left: auto;');
    expect(shellSource).toContain(
      'padding-top: calc(var(--run-empty-top-gap, 128px) + 40px)',
    );
  });
});
