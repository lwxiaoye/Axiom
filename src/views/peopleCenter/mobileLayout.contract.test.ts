import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname);
const centerShell = readFileSync(resolve(root, 'center.vue'), 'utf8');
const centerStyles = readFileSync(resolve(root, 'styles/centerNew.less'), 'utf8');
const messageList = readFileSync(resolve(root, 'components/MessageList.vue'), 'utf8');
const modelSelector = readFileSync(resolve(root, 'components/ModelSelector.vue'), 'utf8');
const plusSubmenu = readFileSync(resolve(root, 'components/PlusSubmenu.vue'), 'utf8');
const runCompactHeader = readFileSync(resolve(root, 'components/run/RunCompactHeader.vue'), 'utf8');
const runShell = readFileSync(resolve(root, 'components/run/agent-run-shell.less'), 'utf8');
const skillSquare = readFileSync(resolve(root, 'components/SkillSquare.vue'), 'utf8');
const chatTab = readFileSync(resolve(root, 'tabs/ChatTab.vue'), 'utf8');
const myFilesTab = readFileSync(resolve(root, 'tabs/MyFilesTab.vue'), 'utf8');
const loginPage = readFileSync(resolve(root, '../system/loginmini/MiniLogin.vue'), 'utf8');
const indexHtml = readFileSync(resolve(root, '../../../index.html'), 'utf8');

describe('手机端登录与主对话布局契约', () => {
  it('登录页为极简单栏卡片，且保证手机端可滚动、输入框不触发 iOS 缩放', () => {
    // 登录页在 2026-09 改为极简单栏卡片（登录/注册双模式），
    // 断言随之对准新实现，保留的是同一组移动端可用性意图。
    expect(loginPage).toContain('overflow-y: auto;');
    expect(loginPage).toContain('min-height: 100dvh;');
    expect(loginPage).toContain('@media (max-width: 480px)');
    expect(loginPage).toContain('font-size: 16px;');
    expect(loginPage).toContain('aria-label="刷新图形验证码"');
    expect(loginPage).toContain(':disabled="loading"');
    // 单栏卡片不应重新引入横向分栏
    expect(loginPage).not.toContain('grid-template-columns');
  });

  it('主对话统一处理顶底安全区，空对话与已有对话共用固定输入区', () => {
    expect(centerStyles).toContain('--compact-header-height: calc(60px + env(safe-area-inset-top));');
    expect(centerStyles).toContain('--mobile-nav-height: 0px;');
    expect(centerStyles).toContain('.chat-home.empty-state .composer-dock');
    expect(centerStyles).toContain('bottom: calc(10px + env(safe-area-inset-bottom));');
    expect(centerStyles).toContain('width: calc(100vw - 20px);');
    expect(centerStyles).toContain('padding-bottom: calc(176px + env(safe-area-inset-bottom));');
    expect(chatTab).toContain("window.visualViewport?.addEventListener('resize', handleComposerViewportResize)");
    expect(chatTab).toContain('Math.min(140, Math.floor(viewportHeight * 0.24))');
  });

  it('手机端由消息列表预留输入区高度，不让外壳重复撑出一大块空白', () => {
    expect(messageList).toContain('padding: 18px 2px calc(176px + env(safe-area-inset-bottom));');
    expect(centerStyles).toContain('padding: 0 10px 24px;');
    expect(centerStyles).not.toContain('padding: 0 10px 176px;');
  });

  it('手机和 iPad 使用抽屉导航，不再常驻展示六项底栏', () => {
    const compactGroupsSource = centerShell.slice(
      centerShell.indexOf('const compactNavGroups'),
      centerShell.indexOf('const visibleNavGroups'),
    );
    expect(centerShell).toContain('class="compact-menu-trigger"');
    expect(centerShell).toContain('class="compact-nav-shortcuts"');
    expect(centerShell).toContain("label: '我的内容'");
    expect(compactGroupsSource).toContain("['files', 'models'].includes(item.key)");
    expect(compactGroupsSource).not.toContain("'myAgent'");
    expect(compactGroupsSource).not.toContain("'knowledge'");
    expect(centerStyles).toContain('@media (max-width: 1024px)');
    expect(centerStyles).toContain('transform: translateX(-105%);');
    expect(centerStyles).toContain('.primary-nav.compact-open');
    expect(centerStyles).not.toContain('grid-template-columns: repeat(6, minmax(0, 1fr));');
    expect(centerStyles).toContain('overflow-anchor: none;');
    expect(centerStyles).toContain('overscroll-behavior: none;');
    expect(centerStyles).not.toContain('overscroll-behavior: contain;');
  });

  it('手机端打开导航用侧栏面板图标，不与任务协作的三横线汉堡撞形', () => {
    expect(centerShell).toContain('aria-label="打开导航菜单"');
    expect(centerShell).toContain('<rect width="18" height="18" x="3" y="3" rx="2" />');
    expect(centerShell).toContain('<path d="M9 3v18" />');
    expect(centerShell).not.toContain('MenuOutlined');
    expect(centerStyles).toContain('.compact-menu-trigger svg');
  });

  it('手机端主对话顶栏不重复「主对话」三字，其它板块仍显示标题', () => {
    expect(centerShell).toContain('v-if="activeSection !== \'chat\'" class="compact-header-title"');
    expect(centerShell).toContain("label: '主对话'");
    expect(centerShell).toContain("label: '智能体广场'");
    expect(centerShell).toContain("label: '我的文件'");
  });

  it('窄屏只隐藏顶栏文字标签，不会把 Ant Design 图标一起隐藏', () => {
    expect(centerShell).toContain('<span class="history-trigger-label">对话历史</span>');
    expect(centerShell).toContain('<span class="history-trigger-label">记忆</span>');
    expect(centerShell).toContain('aria-label="对话历史"');
    expect(centerShell).toContain('aria-label="记忆"');
    expect(centerStyles).not.toContain('.history-trigger span');
    expect(centerStyles).toContain('.history-trigger > .anticon');
  });

  it('触控端消息操作不依赖 hover，输入工具也能在窄屏缩放', () => {
    expect(messageList).toContain('@media (max-width: 720px) and (hover: none)');
    expect(messageList).toMatch(/\.message-actions\s*\{[\s\S]*?opacity:\s*1;/);
    expect(modelSelector).toContain('max-width: min(34vw, 150px);');
    expect(chatTab).toMatch(/@media \(max-width: 719px\)[\s\S]*?\.mode-pill[\s\S]*?height:\s*44px;/);
  });

  it('添加菜单、二级资源选择和模型选择在触屏使用底部面板', () => {
    expect(chatTab).toContain('<Teleport to="body" :disabled="!isCompactComposer">');
    expect(chatTab).toContain('class="plus-mobile-backdrop"');
    expect(chatTab).toContain('height: min(70dvh, 540px);');
    expect(plusSubmenu).toContain('class="ps-mobile-head"');
    expect(plusSubmenu).toContain("useMediaQuery('(max-width: 1024px)')");
    expect(plusSubmenu).toContain('position: absolute;');
    expect(modelSelector).toContain('<Teleport to="body" :disabled="!isCompactPicker">');
    expect(modelSelector).toContain('class="model-picker-backdrop"');
    expect(modelSelector).toContain('height: min(72dvh, 620px);');
  });

  it('启用全屏安全区并使用 AXIOM Agent 小球作为标签页图标', () => {
    expect(indexHtml).toContain('viewport-fit=cover');
    expect(indexHtml).toContain('type="image/svg+xml"');
    expect(indexHtml).toContain('href="/axiom-mark.svg"');
  });

  it('我的文件全端都有缩略图/列表切换，不再放「显示全部文件」按钮', () => {
    expect(myFilesTab).toContain('class="myfiles-viewtoggle"');
    expect(myFilesTab).toContain('class="mf-filter mf-folders-entry"');
    expect(myFilesTab.indexOf('mf-folders-entry')).toBeLessThan(myFilesTab.indexOf('class="myfiles-viewtoggle"'));
    expect(myFilesTab).toContain('@pointerdown="onViewPointerDown"');
    expect(myFilesTab).toContain("cubic-bezier(0.32, 0.72, 0, 1)");
    expect(myFilesTab).toContain('translate3d');
    expect(myFilesTab).toContain('prefers-reduced-motion: reduce');
    expect(myFilesTab).toContain('ref="viewThumbRef"');
    expect(myFilesTab).not.toContain('v-if="!isPhoneFiles"');
    expect(myFilesTab).not.toContain('effectiveViewMode');
    expect(myFilesTab).not.toContain('显示全部文件');
    expect(myFilesTab).toContain('class="myfiles-filter-strip"');
    expect(myFilesTab).toContain('overlay-class-name="mf-file-actions-overlay"');
    expect(myFilesTab).toContain('@media (max-width: 1024px)');
    expect(myFilesTab).toContain('grid-template-columns: 36px minmax(0, 1fr) 44px');
    expect(myFilesTab).toContain('min-height: 44px;');
    expect(myFilesTab).toContain('env(safe-area-inset-bottom)');
    expect(myFilesTab).toContain('class="mf-filter-label"');
    expect(myFilesTab).toContain('text-overflow: ellipsis');
    expect(myFilesTab).toContain('touch-action: pan-y');
    expect(myFilesTab).not.toContain('touch-action: pan-x');
    expect(myFilesTab).toContain('.myfiles-filter-strip');
    expect(myFilesTab).toMatch(/\.myfiles-filter-strip\s*\{[^}]*overflow:\s*hidden;/);
  });

  it('内置助手页在手机和 iPad 使用全屏壳层与紧凑顶栏', () => {
    const builtinPage = readFileSync(resolve(root, 'pages/BuiltinHarnessRunPage.vue'), 'utf8');
    expect(builtinPage).toContain('class="run-mobile-sidebar-backdrop"');
    expect(builtinPage).toContain('<RunCompactHeader');
    expect(builtinPage).toContain('<Transition name="run-history-backdrop">');
    expect(runShell).toContain('@media (max-width: 1024px)');
    expect(runShell).toContain('.agent-run-page.is-fullpage { height: 100dvh;');
    expect(runShell).not.toContain('.run-composer');
    expect(runCompactHeader).toContain('aria-label="返回上一页"');
    expect(runCompactHeader).toContain('@media (max-width: 1024px)');
  });

  it('主 Agent 后台运行卡在手机端不再向左偏移或遮住广场内容', () => {
    expect(centerShell).toContain("activeSection !== 'chat' && chatTaskState !== 'idle'");
    expect(centerStyles).toContain('@keyframes background-task-enter-compact');
    expect(centerStyles).toMatch(/\.background-chat-task\s*\{[\s\S]*?right:\s*12px;[\s\S]*?left:\s*12px;[\s\S]*?width:\s*auto;[\s\S]*?transform:\s*none;/);
    expect(centerStyles).toContain('animation: background-task-enter-compact 0.22s');
    expect(centerStyles).toContain('.workspace:has(> .background-chat-task) .agent-market');
    expect(centerStyles).toContain('.workspace:has(> .background-chat-task) .skill-square-section');
    expect(centerStyles).toContain('.workspace:has(> .background-chat-task) .model-page');
    expect(centerStyles).toContain('.workspace:has(> .background-chat-task) .my-knowledge-section');
    expect(centerStyles).toMatch(/\.toast-error,[\s\S]*?\.toast-info\s*\{[\s\S]*?transform:\s*none;/);
  });

  it('智能体广场在 iPad 使用两列、手机使用紧凑单列卡片', () => {
    expect(centerStyles).toMatch(/@media \(max-width: 1024px\)[\s\S]*?\.market-heading\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) 80px;/);
    expect(centerStyles).toMatch(/@media \(max-width: 1024px\)[\s\S]*?\.app-grid\s*\{[\s\S]*?repeat\(2, minmax\(0, 1fr\)\)/);
    expect(centerStyles).toMatch(/@media \(max-width: 719px\)[\s\S]*?\.app-grid\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\);/);
    expect(centerStyles).toContain(".app-card-icon-shell:has(img[data-icon-shape='wordmark'])");
    expect(centerStyles).toContain('padding: 16px 14px calc(32px + env(safe-area-inset-bottom));');
  });

  it('Skill 广场在移动端去除重复标题，卡片、搜索和详情弹窗都可触控', () => {
    expect(skillSquare).toContain('@media (max-width: 1024px)');
    expect(skillSquare).toContain('.skill-header > div:first-child');
    expect(skillSquare).toMatch(/\.skill-grid\s*\{[\s\S]*?repeat\(2, minmax\(0, 1fr\)\)/);
    expect(skillSquare).toContain('@media (max-width: 719px)');
    expect(skillSquare).toMatch(/@media \(max-width: 719px\)[\s\S]*?\.skill-grid\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\);/);
    expect(skillSquare).toContain('min-height: 44px;');
    expect(skillSquare).toContain('width: calc(100vw - 16px) !important;');
    expect(skillSquare).toContain('env(safe-area-inset-bottom)');
  });

  it('内置应用页在手机和 iPad 点选历史后立即收起侧栏', () => {
    const builtinPage = readFileSync(resolve(root, 'pages/BuiltinHarnessRunPage.vue'), 'utf8');
    const builtinStart = builtinPage.indexOf('async function selectConversation');
    const builtinBody = builtinPage.slice(builtinStart, builtinPage.indexOf('\nfunction confirmRemoveConversation', builtinStart));
    expect(builtinBody.indexOf('sidebarCollapsed.value = true')).toBeLessThan(builtinBody.indexOf('loadThread('));
    expect(builtinBody).toContain('viewportWidth.value <= 1024');
  });

  it('手机和 iPad 点选会话历史后先收起抽屉再进入该会话', () => {
    expect(centerShell).toContain('<Transition name="history-backdrop">');
    expect(centerShell).toContain('<Transition name="history-popover">');
    expect(centerShell).toContain('function selectHistoryConversation(threadId: string)');
    expect(centerShell).toContain('function selectHistoryDraft(draftId: string)');
    expect(centerShell).toContain('if (isCompactShell.value) closeHistory()');
    expect(centerShell).toContain('void loadThread(threadId)');
    expect(centerShell).toContain('@click="selectHistoryConversation(item.id)"');
    expect(centerShell).toContain('@click="selectHistoryDraft(draft.draftId)"');
    expect(centerShell).toContain('@click.stop="startRename(item)"');
    expect(centerShell).toContain('@click.stop="togglePin(item.id)"');
    expect(centerShell).toContain('@click.stop="handleDeleteThread(item.id)"');
    expect(centerStyles).toContain('.history-popover-leave-active');
    expect(centerStyles).toContain('.history-backdrop-leave-active');
    expect(centerStyles).toContain('.history-popover-enter-from');
    expect(centerStyles).toContain('.history-popover-leave-to');
    expect(centerStyles).toContain(`  .history-popover-enter-active,
  .history-popover-leave-active {
    animation: none;
    will-change: transform;
    transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
  }`);
    expect(centerStyles).toContain(`  .history-backdrop-enter-active,
  .history-backdrop-leave-active {
    animation: none;
    transition: opacity 0.18s ease;
  }`);
  });
});
