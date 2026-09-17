import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const source = fs.readFileSync(path.resolve(__dirname, 'MessageList.vue'), 'utf8');
const disclaimerSource = fs.readFileSync(path.resolve(__dirname, 'AgentOutputDisclaimer.vue'), 'utf8');
const executionTraceCondition = 'v-if="showExecutionTrace(message) && !(hasResearchTeamPanel(message) && researchTeamSettled(message))"';

describe('MessageList 终答、折叠与产物图片边界', () => {
  it('后端已完成或部分完成时，前端 loading 滞后不能继续隐藏报告', () => {
    const start = source.indexOf('function isExecutionRunning(');
    const end = source.indexOf('// ===== 整轮一个执行头', start);
    const js = ts.transpileModule(source.slice(start, end), {
      compilerOptions: { target: ts.ScriptTarget.ES2020 },
    }).outputText;
    const running = new Function('props', 'lastAssistantId', 'isExecutionWaiting',
      `${js}; return isExecutionRunning;`)({ loading: true }, { value: 2 }, () => false);
    const message = { id: 2, role: 'assistant', agentMode: 'research', content: '# 研究报告' };
    expect(running(message)).toBe(true);
    expect(running({ ...message, runCompletedAt: 123 })).toBe(false);
    expect(running({ ...message, runPartial: true })).toBe(false);
    expect(running({ ...message, runFailed: true })).toBe(false);
    expect(running({ ...message, runCancelled: true })).toBe(false);
  });
  it('终答固定在执行折叠容器之外', () => {
    const collapseStart = source.indexOf('class="execution-stream-collapse"');
    const collapseEnd = source.indexOf('<!-- 联网搜索到来源后');
    const finalBody = source.indexOf(
      'v-if="message.role === \'assistant\' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"',
    );
    expect(collapseStart).toBeGreaterThan(-1);
    expect(collapseEnd).toBeGreaterThan(collapseStart);
    expect(finalBody).toBeGreaterThan(collapseEnd);
    expect(source).toContain('<template v-if="message.role === \'user\'">');
    expect(source).toContain(executionTraceCondition);
    expect(source).toContain(
      'v-if="message.role === \'assistant\' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"',
    );
  });

  it('整轮状态头只折叠过程，不把最终总结收进去', () => {
    const streamStart = source.indexOf(executionTraceCondition);
    const streamEnd = source.indexOf('<!-- 联网搜索到来源后', streamStart);
    const body = source.slice(streamStart, streamEnd);
    expect(body).toContain('v-if="showExecHead(message) && !hasResearchTeamPanel(message)"');
    expect(body).toContain('v-if="hasExecutionStreamBody(message)"');
    expect(body).toContain('v-show="!isExecCollapsed(message)"');
    expect(source).toContain('function toggleExecCollapse');
    expect(source).not.toContain('· {{ execHeadTimeText(message) }}');
  });

  it('完成后不常驻计量行，产物消息清掉历史图标记', () => {
    expect(source).toContain("v-if=\"message.role === 'assistant' && isExecutionRunning(message) && !hasResearchTeamPanel(message)\"");
    expect(source).toContain('<ActivityOrb class="gen-meter-orb" />');
    expect(source).toContain('class="gen-meter-status"');
    expect(source).not.toContain('tokens ·');
    expect(source).not.toContain('class="gen-meter-tokens"');
    expect(source).not.toContain('function genTokenCount');
    expect(source).toContain('hasArtifactImageContext(message)');
    expect(source).toContain("out.replace(/\\[图\\d{1,2}\\]/g, '')");
  });

  it('已完成运行遗留的审批卡不再显示，真实等待审批仍保留', () => {
    const approvalStart = source.indexOf('function hasPendingApproval');
    const approvalEnd = source.indexOf('function isExecutionWaiting', approvalStart);
    const approvalBody = source.slice(approvalStart, approvalEnd);

    expect(source).toContain('v-if="hasPendingApproval(message)"');
    expect(approvalBody).toContain('message.approval');
    expect(approvalBody).toContain('!message.runCompletedAt');
    expect(approvalBody).toContain('!message.runFailed');
    expect(approvalBody).toContain('!message.runCancelled');
  });

  it('历史内部契约不占时间线，公开过程讲解按事件顺序保留', () => {
    const sanitizeStart = source.indexOf('function sanitizeAssistantBody');
    const sanitizeEnd = source.indexOf('function isProcessResidueBody', sanitizeStart);
    const sanitizeBody = source.slice(sanitizeStart, sanitizeEnd);
    const visibleStepsStart = source.indexOf('function visibleAgentSteps');
    const visibleStepsEnd = source.indexOf('function executionRows', visibleStepsStart);
    const visibleStepsBody = source.slice(visibleStepsStart, visibleStepsEnd);

    expect(sanitizeBody).toContain('image[-_ ]?map');
    expect(sanitizeBody).toContain('计划需要调整');
    expect(sanitizeBody).toContain("return ''");
    expect(sanitizeBody).toContain('isTerminalAnchorPlaceholder');
    expect(source).toContain('PUBLIC_RUNTIME_NOISE_LINE_RE');
    expect(source).toContain('<thinking');
    expect(visibleStepsBody).toContain("step.kind !== 'note'");
    expect(visibleStepsBody).toContain("Boolean(cleanNarration(String(step.text || '')))");
    expect(visibleStepsBody).toContain('黑字闪一下随后消失');
  });

  it('停止占位语不当终答展示，锚点函数仍存在', () => {
    const helperStart = source.indexOf('function isTerminalAnchorPlaceholder');
    const helperEnd = source.indexOf('function sanitizeAssistantBody', helperStart);
    const helperBody = source.slice(helperStart, helperEnd);
    expect(helperStart).toBeGreaterThan(-1);
    expect(helperBody).toContain('（已停止，未生成回复）');
    expect(helperBody).not.toContain('任务执行失败，未生成回复');
    const sanitizeStart = source.indexOf('function sanitizeAssistantBody');
    const sanitizeEnd = source.indexOf('function isProcessResidueBody', sanitizeStart);
    const sanitizeBody = source.slice(sanitizeStart, sanitizeEnd);
    expect(sanitizeBody.indexOf('isTerminalAnchorPlaceholder(content)')).toBeGreaterThan(-1);
    expect(sanitizeBody.indexOf("return ''")).toBeGreaterThan(-1);
    expect(sanitizeBody.indexOf('isTerminalAnchorPlaceholder(content)'))
      .toBeLessThan(sanitizeBody.indexOf('stripProtocolLeak'));
  });

  it('瞬时 reasoning 独占旧版渐隐样式，Thought 步骤用可展开面板', () => {
    expect(source).toContain('class="reasoning-summary-step active"');
    expect(source).toContain('class="reasoning-summary-body"');
    expect(source).toContain('reasoningSummaryLines(message)');
    expect(source).not.toContain('publicReasoningSummaryLines');
    expect(source).not.toContain("row.step.kind === 'summary'");
    expect(source).toMatch(/\.reasoning-summary-step\.active \.reasoning-summary-body\s*\{[\s\S]*?max-height:\s*84px/);
    expect(source).toContain('mask-image: linear-gradient(to bottom, transparent 0, #000 16px, #000 100%)');
    expect(source).toContain('margin: 8px 0 4px');
    expect(source).toContain('.reasoning-summary-step.initial .reasoning-summary-body');
    expect(source).toContain('.reasoning-summary-body p:nth-child(1) { opacity: 0.58; }');
    expect(source).toContain('.reasoning-summary-body p:nth-child(2) { opacity: 0.78; }');
    expect(source).toContain('@keyframes reasoning-summary-line-in');
    expect(source).not.toContain('message.reasoning_delta');
    expect(source).toContain("row.step.kind === 'thinking'");
    expect(source).toContain('<ThinkingReasoningStep');
    expect(source).toContain(':active="isThinkingActive(message, row.stepIndex)"');
    expect(source).toContain(':open="isThoughtOpen(message, row.stepIndex)"');
    expect(source).toContain('function toggleThought');
    const thoughtOpenStart = source.indexOf('function isThoughtOpen');
    const thoughtOpenEnd = source.indexOf('function toggleThought', thoughtOpenStart);
    const thoughtOpenBody = source.slice(thoughtOpenStart, thoughtOpenEnd);
    expect(thoughtOpenBody).toContain('return false');
    expect(thoughtOpenBody).not.toContain('return isThinkingActive');
    expect(source).not.toContain('class="ast-shell-panel thought-panel"');
    expect(source).not.toContain('class="thought-body"');
    expect(source).not.toContain('class="reasoning-text compact"');
    expect(source).not.toMatch(/row\.step\.kind === 'thinking'[\s\S]{0,400}reasoning-summary-step/);
  });

  it('首句过程阐述位于 Thinking 与工具步骤之前，不渲染已加载 Skill 摘要', () => {
    const streamStart = source.indexOf('class="execution-stream"');
    const reasoning = source.indexOf('class="reasoning-summary-step active"', streamStart);
    const preamble = source.indexOf(
      'class="message-bubble markdown-body preamble-body narrative-commentary"',
      streamStart,
    );
    const rows = source.indexOf('v-for="row in executionRows(message)"', streamStart);

    expect(reasoning).toBeGreaterThan(streamStart);
    expect(rows).toBeGreaterThan(preamble);
    expect(source).not.toContain('class="loaded-skill-summary"');
    expect(source).not.toContain('v-if="isLoadedCapabilityNote(row.step)"');
    expect(source).toContain('row.step.name === \'use_skill\'');
  });

  it('首句过程阐述首次出现就是灰色，内部工具回执不铺在步骤行', () => {
    const preambleStart = source.indexOf('.preamble-body {');
    const preambleEnd = source.indexOf('.execution-stream .preamble-body', preambleStart);
    const preambleStyles = source.slice(preambleStart, preambleEnd);

    expect(preambleStyles).toContain('color: var(--execution-text-muted, #8e8e8e);');
    expect(preambleStyles).not.toContain('color: #111;');
    expect(source).toContain('function visibleStepTarget');
    expect(source).toContain('function isInternalToolOutput');
    expect(source).toContain('stdout|stderr');
    expect(source).toContain("value.split(/\\r?\\n/)");
    expect(source).toContain('command not found');
    expect(source).toContain('PUBLIC_RUNTIME_NOISE_FRAGMENT_RE');
    expect(source).toContain('function visibleStepOutput');
    expect(source).not.toContain('{{ visibleStepTarget(row.step) }}');
    expect(source).not.toContain('{{ row.step.target || row.step.detail }}');
    expect(source).not.toContain('{{ errorBrief(row.step.error) }}');
  });

  it('助手输出与输入框共用一条居中阅读轨道', () => {
    const centerStyles = fs.readFileSync(path.resolve(__dirname, '../styles/centerNew.less'), 'utf8');

    expect(centerStyles).toContain('--chat-content-max-width: 820px');
    expect(centerStyles).toMatch(/\.composer\s*\{[\s\S]*?max-width:\s*var\(--chat-content-max-width\)/);
    expect(source).toMatch(
      /\.message:not\(\.user\) \.message-content\s*\{[\s\S]*?width:\s*min\(100%,\s*var\(--chat-content-max-width,\s*820px\)\)[\s\S]*?margin-inline:\s*auto/,
    );
    expect(source).toMatch(
      /\.message:not\(\.user\) \.message-bubble\s*\{[\s\S]*?width:\s*100%[\s\S]*?max-width:\s*100%/,
    );
    expect(source).toMatch(
      /\.message\.user\s*\{[\s\S]*?width:\s*min\(100%,\s*var\(--chat-content-max-width,\s*820px\)\)[\s\S]*?margin-inline:\s*auto/,
    );
    expect(source).toMatch(/\.message-list\s*\{[\s\S]*?padding:\s*36px\s+0\s+148px/);
    expect(source).toMatch(/\.execution-stream\s*\{[\s\S]*?width:\s*100%[\s\S]*?max-width:\s*100%/);
    expect(source).toMatch(/\.preamble-body\s*\{[\s\S]*?width:\s*100%[\s\S]*?max-width:\s*100%/);
  });

  it('重要信息核查提示贴在固定输入框下方，不留在消息流中间', () => {
    const chatTabSource = fs.readFileSync(path.resolve(__dirname, '../tabs/ChatTab.vue'), 'utf8');
    expect(source).not.toContain('<AgentOutputDisclaimer');
    const dockStart = chatTabSource.indexOf('class="composer-dock"');
    const disclaimerIdx = chatTabSource.indexOf('<AgentOutputDisclaimer v-if="chatMessages.length > 0');
    expect(dockStart).toBeGreaterThan(-1);
    expect(disclaimerIdx).toBeGreaterThan(dockStart);
    expect(chatTabSource.slice(dockStart, disclaimerIdx)).toContain("'composer'");
    expect(chatTabSource.slice(dockStart, disclaimerIdx)).toContain('chat-composer');
    expect(disclaimerSource).toContain('AI可能会犯错，请核查重要信息');
    expect(disclaimerSource).toContain('margin: 8px auto 0');
    expect(disclaimerSource).toContain('background: transparent');
    expect(disclaimerSource).not.toMatch(/background:\s*#fff/);
  });

  it('最终回答从首个流式字符起固定使用黑色，不随运行状态变色', () => {
    const finalBodyStart = source.indexOf(
      'v-if="message.role === \'assistant\' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"',
    );
    const finalBodyEnd = source.indexOf('v-html="renderAssistantHtml(message)"', finalBodyStart);
    const finalBody = source.slice(finalBodyStart, finalBodyEnd);

    expect(finalBody).toContain('narrative-final');
    expect(finalBody).not.toContain('isExecutionRunning(message)');
    expect(source).toMatch(/\.message-bubble\.narrative-final\s*\{[\s\S]*?color:\s*#111/);
    expect(source).toMatch(/\.message-bubble\.narrative-commentary[\s\S]*?color:\s*var\(--execution-text-muted/);
  });

  it('深度研究对齐计划卡：终态整卡入场，不先出骨架条，不先输出对话总结', () => {
    expect(source).toContain('function isResearchReportPending');
    expect(source).toContain('function hideResearchNarrative');
    expect(source).toContain('function hasResearchReportFiles');
    expect(source).toContain('function showResearchStructureCard');
    expect(source).toContain('researchCardMarkdown(message)');
    expect(source).toContain('researchCardPreviewHtml(message)');
    expect(source).toContain('v-html="researchCardBodyHtml(message)"');
    expect(source).toContain('<h1>{{ planReportViewer.title }}</h1>');
    expect(source).toContain('stripLeadingTitleHeadings');
    expect(source).not.toContain("polyline points='6 9 12 15 18 9'");
    expect(source).toContain('return visibleDeliverables(message.generatedFiles).filter((file) => !isResearchReportFile(file));');
    expect(source).not.toContain('v-if="isResearchTurn(message) && isResearchReportFile(file)');
    expect(source).not.toContain('!isExecutionRunning(message) || hasResearchReportFiles(message)');
    const streamStart = source.indexOf(executionTraceCondition);
    const streamEnd = source.indexOf('<!-- 联网搜索到来源后');
    expect(source.slice(streamStart, streamEnd)).not.toContain('showResearchStructureCard');
    expect(source).not.toContain('v-if="isResearchReportPending(message)"');
    expect(source).not.toContain('class="plan-report-skeleton"');
    expect(source).toMatch(/\.research-report-card\s*\{[\s\S]*?animation:\s*plan-card-in 0\.55s/);
    expect(source).toContain(executionTraceCondition);
    expect(source).toContain("isUserClarificationMessage(message)");
  });

  it('普通模式的报告保持正文样式，不因搜索次数或污染进度套研究蓝框', () => {
    expect(source).toContain('if (!isResearchTurn(message)) return false;');
    expect(source).toContain('v-if="message.role === \'assistant\' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"');
    expect(source).toContain('if (!isResearchTurn(message)) return false;');
  });

  it('运行头如实区分完成、失败、停止和部分完成，终答用视觉帧平滑追赶 SSE', () => {
    const chatSource = fs.readFileSync(path.resolve(__dirname, '../composables/useCenterChat.ts'), 'utf8');
    const streamSource = fs.readFileSync(path.resolve(__dirname, '../composables/smoothStreamText.ts'), 'utf8');
    const processStreamsSource = fs.readFileSync(path.resolve(__dirname, '../composables/harnessProcessStreams.ts'), 'utf8');
    const bufferStart = chatSource.indexOf('function createTypewriter');
    const bufferEnd = chatSource.indexOf('function stripRecommendMark', bufferStart);
    const bufferBody = chatSource.slice(bufferStart, bufferEnd);

    expect(source).toContain("return '本轮处理中';");
    expect(source).toContain("return '本轮等待回应';");
    const titleStart = source.indexOf('function execHeadTitle');
    const titleEnd = source.indexOf('function generationStatusText', titleStart);
    const titleBody = source.slice(titleStart, titleEnd);
    const titleJs = ts.transpileModule(titleBody, {
      compilerOptions: { target: ts.ScriptTarget.ES2020 },
    }).outputText;
    const presentation = new Function(
      'execHeadState',
      'execHeadRunning',
      'isExecutionWaiting',
      'isResearchTurn',
      `${titleJs}; return { execHeadTitle, execHeadTerminalClass, errorNoticeTitle, errorNoticeDetail };`,
    )(
      (message: unknown) => message,
      (message: { running?: boolean }) => Boolean(message.running),
      (message: { waiting?: boolean }) => Boolean(message.waiting),
      (message: { agentMode?: string }) => message.agentMode === 'research',
    );
    const policyFailure = {
      runFailed: true,
      error: '请求触发了网关敏感词策略，模型未生成内容，本轮已停止。',
    };

    expect(presentation.execHeadTitle({ running: true })).toBe('本轮处理中');
    expect(presentation.execHeadTitle({ running: true, runStatus: 'waiting_system' })).toBe('本轮处理中');
    expect(presentation.execHeadTitle({ running: true, runStatus: 'created' })).toBe('本轮处理中');
    expect(presentation.execHeadTitle({ waiting: true })).toBe('本轮等待回应');
    expect(presentation.execHeadTitle(policyFailure)).toBe('本轮已停止');
    expect(presentation.execHeadTerminalClass(policyFailure)).toBe('terminal-stopped');
    expect(presentation.errorNoticeTitle(policyFailure)).toBe('触发了敏感词规则');
    expect(presentation.errorNoticeDetail(policyFailure)).toContain('模型未生成内容');
    expect(presentation.execHeadTitle({ runFailed: true, error: '服务中断' })).toBe('本轮未完成');
    expect(presentation.execHeadTitle({ runCancelled: true })).toBe('本轮已停止');
    expect(presentation.execHeadTitle({ runPartial: true })).toBe('本轮部分完成');
    expect(presentation.execHeadTitle({ runPartial: true, agentMode: 'research' })).toBe('本轮研究已结束');
    expect(presentation.execHeadTerminalClass({ runPartial: true, agentMode: 'research' })).toBe('terminal-partial');
    expect(presentation.execHeadTitle({ runCompletedAt: 1 })).toBe('本轮已完成');
    expect(titleBody).toContain("return '本轮已停止';");
    expect(titleBody).toContain("return '本轮未完成';");
    expect(titleBody).toContain("return '本轮部分完成';");
    expect(titleBody).toContain("return '本轮已完成';");
    for (const forbidden of ['正在检索', '等待补充', '已运行']) {
      expect(titleBody).not.toContain(forbidden);
    }
    expect(titleBody).toContain('isSensitiveWordRejection(state)');
    expect(source).toContain("return /^请求触发了网关敏感词策略/u.test");
    expect(source).toContain("return isSensitiveWordRejection(message) ? '触发了敏感词规则' : '本轮未能完成';");
    expect(source).toContain('模型未生成内容。请修改输入后重试');
    expect(source).toContain("v-if=\"message.role === 'assistant' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)\"");
    expect(source).not.toContain("(hasVisibleAssistantBody(message) || message.error)");
    expect(source).toContain('execHeadTimeText(message)');
    expect(source).toContain('formatDuration(Math.max(0, nowTick.value - startedAt), true)');
    expect(source).toContain('const duration = execHeadDurationMs(message);');
    expect(source).toContain('class="exec-head-elapsed"');
    expect(source).toContain('item.runCompletedAt - item.runStartedAt');
    expect(bufferBody).toContain('createSmoothStreamText({');
    expect(bufferBody).toContain('updateChatMessageContent(messageId, content)');
    expect(chatSource).toContain("from './harnessProcessStreams'");
    expect(processStreamsSource).toContain('function createCommentaryStream');
    expect(processStreamsSource).toContain("destination: 'preamble' | 'note' | ''");
    expect(processStreamsSource).toContain('createSmoothStreamText({ commit })');
    expect(processStreamsSource).toContain('void stream.finish(clean).finally(');
    expect(processStreamsSource).not.toContain('return stream.finish(clean)');
    expect(bufferBody).toContain('commentaryStream.show(text, kind)');
    expect(streamSource).toContain('SMOOTH_STREAM_FRAME_MS = 1000 / 60');
    expect(streamSource).toContain('scheduler.requestFrame(paint)');
    expect(streamSource).toContain('BASE_CHARS_PER_SECOND + backlog * BACKLOG_RATE_GAIN');
    expect(streamSource).toContain("window.matchMedia('(prefers-reduced-motion: reduce)')");
    expect(streamSource).not.toContain('setInterval');

    const subscribeStart = chatSource.indexOf('const subscribeOnce = async');
    const subscribeEnd = chatSource.indexOf('// 断流自愈', subscribeStart);
    const subscribeBody = chatSource.slice(subscribeStart, subscribeEnd);
    expect(subscribeBody).toContain('paintAssistantStreamText(');
    expect(subscribeBody).toContain('applyCommentaryAndHidePlanBody(');
    expect(subscribeBody).toContain("capturedPlan ? '' : slicedAnswer");
    expect(chatSource).toContain('shouldBufferUnclassifiedProcessNarration(');
    expect(bufferBody).toContain('PROCESS_NARRATION_BUFFER_CHARS');
    expect(bufferBody).toContain('pending.length < PROCESS_NARRATION_BUFFER_CHARS');
  });

  it('错误兜底正文不能掩盖失败，只有正文加真实产物才允许终态降噪', () => {
    const deliveredStart = source.indexOf('function hasDelivered(');
    const deliveredEnd = source.indexOf('function isExecFailed', deliveredStart);
    const deliveredBody = source.slice(deliveredStart, deliveredEnd);

    expect(deliveredBody).toContain("String(message.content || '').trim()");
    expect(deliveredBody).toContain('visibleGeneratedFiles(message).length > 0');
    expect(deliveredBody).not.toContain('hasDeliveredContent');
  });
});
