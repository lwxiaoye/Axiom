import { readFileSync } from 'fs';
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
    expect(shellSource).toContain('env(safe-area-inset-bottom)');
    expect(inspirationPanelSource).toContain('class="run-inspiration-mobile-backdrop"');
    expect(inspirationPanelSource).toContain('class="run-inspiration-mobile-close"');
    expect(inspirationPanelSource).toContain('width: 44px');
    expect(inspirationPanelSource).toContain('height: 44px');
    expect(inspirationPanelSource).toContain('height: min(72dvh, 620px)');
    expect(inspirationPanelSource).toContain('width: 42px');
    expect(inspirationPanelSource).toContain('height: 4px');
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

  it('独立运行页与浮窗只有一套默认外观，欢迎语和占位符是固定文案', () => {
    expect(runPageSource).toContain("const RUN_WELCOME_TITLE = '你好，有什么我可以帮你？'");
    expect(runPageSource).toContain("const RUN_COMPOSER_PLACEHOLDER = '输入你的问题...'");
    expect(runPageSource).toContain('<h1>{{ RUN_WELCOME_TITLE }}</h1>');
    expect(runPageSource).toContain(':placeholder="RUN_COMPOSER_PLACEHOLDER"');
    for (const source of [runPageSource, panelSource, inspirationPanelSource]) {
      expect(source).not.toContain('runPresentation');
      expect(source).not.toContain('presentation/');
      expect(source).not.toContain('portableSkin');
      expect(source).not.toContain('has-custom-presentation');
      expect(source).not.toContain('run-presentation-rail-decoration');
    }
    expect(runApiSource).not.toContain('RunPresentationConfig');
    expect(runApiSource).not.toContain('presentation?:');
  });

  it('关闭文件上传时不渲染加号', () => {
    const shellSource = readFileSync(resolve(runRoot, 'agent-run-shell.less'), 'utf8');
    expect(runPageSource).toContain('v-if="fileUploadEnabled" class="composer-input-actions"');
    expect(panelSource).toContain('v-if="fileUploadEnabled" class="composer-input-actions"');
    expect(shellSource).toContain('.send-btn { margin-left: auto;');
    expect(shellSource).not.toContain('has-custom-presentation');
    expect(shellSource).not.toContain('--run-empty-top-gap');
  });
});
