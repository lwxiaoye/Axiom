<!-- eslint-disable vue/no-v-html -->
<template>
  <div ref="messageListRef" class="message-list" :class="{ 'campus-answer-layout': answerLayout === 'campus' }" role="log" aria-live="polite" aria-atomic="false">
    <div
      v-for="message in visibleMessages"
      :key="message.id"
      :data-mid="message.id"
      :class="['message', message.role, { superseded: message.superseded }]"
    >
      <!-- 头像与「智能助手」抬头已移除（2026-07-20 用户拍板：只显示回复内容）；
           仅展开的历史版本保留「上一版」标记用于区分新旧 -->
      <div class="message-content">
        <span v-if="message.role === 'assistant' && message.superseded" class="message-role">
          上一版（已被重新生成取代）
        </span>
        <div v-if="message.compactedNote" class="context-compacted-chip" :title="message.compactedNote">
          <HistoryOutlined />
          {{ message.compactedNote }}
        </div>
        <!-- 「已更新记忆」chip 已移除（2026-07-28 用户拍板「更新记忆这里不用显示」）：
             记忆抽取是后台行为，每轮在对话里报一次账对用户没有价值，只是噪音。
             后端仍然照常下发 memory.updated 事件（记忆功能本身不受影响），只是前端不再渲染；
             要看记什么了走顶栏「记忆」入口。想恢复的话：这里加回一个 chip 读 memory.updated 即可。 -->
        <div v-if="message.routedAgent" class="routed-agent">
          <RobotOutlined />
          已为你转交「{{ message.routedAgent }}」
        </div>
        <!-- 「你在 Xs 后停止」已不展示（2026-08-21 用户拍板）：
             停键后执行头已经带时长，再写一行停止留痕是重复噪音。
             后端 runCancelled 仍照常记录；过程时间线照常保留。 -->
        <!-- 「已从上次进度继续」已不展示（2026-08-09 用户拍板）：续做是内部默默完成的，
             展示成蓝色注记只会像系统旁白/噪音。后端仍可下发 resume_meta 驱动续做逻辑；
             前端不再写入/渲染 resumeNote。同「已更新记忆」chip 的处理口径。 -->
        <!-- 首帧确认是自然对话，不是一次“执行”。普通问答尚未发生真实动作时，直接放在
             正文位置；一旦模型发送真实 commentary 或工具事件，它会原位替换/进入过程流。 -->
        <div
          v-if="showStandalonePreamble(message) || (hasResearchTeamPanel(message) && execHeadRunning(message) && hasVisiblePreamble(message))"
          class="message-bubble markdown-body preamble-body standalone-preamble"
          :class="narrativeClass(message)"
          v-html="renderMarkdown(sanitizeAssistantBody(message.preamble || ''))"
        ></div>
        <ResearchTeamPanel
          v-for="team in researchTeamPanels(message)"
          :key="`${message.id}-${team.id}`"
          :team="team"
          :settled="researchTeamSettled(message)"
          :cancelled="execHeadState(message).runCancelled"
          :failed="execHeadState(message).runFailed"
          :elapsed="execHeadTimeText(message)"
        />
        <!-- Codex 式主对话过程流：公开叙述与灰色动作回执按真实到达顺序交错展示；
             完成后由「工作过程」轻量按钮折叠，最终回答留在按钮之后。 -->
        <div v-if="showExecutionTrace(message) && !(hasResearchTeamPanel(message) && researchTeamSettled(message))" class="execution-stream" aria-label="执行过程">
          <!-- 空壳规避（2026-07-20 用户反馈）：需求确认/计划确认这类只出卡、没有实际执行步骤的
               轮次，终态后只剩一个展开也为空的壳——没有可展示内容时头部整个不渲染 -->
          <!-- 整轮一个执行头（Codex 对齐 2026-07-26）：运行中插话会把一轮切成多个分段，
               但头只挂在首段、状态取本轮末段；后续分段只出内容不出头，插话气泡因此原位
               夹在两段之间，折叠后自然外提到折叠头与最终回答之间。 -->
          <div
            v-if="showExecHead(message) && !hasResearchTeamPanel(message)"
            class="exec-head"
            :class="[
              {
                running: execHeadRunning(message),
                collapsible: execHeadCanCollapse(message),
              },
              execHeadTerminalClass(message),
            ]"
            :role="execHeadCanCollapse(message) ? 'button' : 'status'"
            :tabindex="execHeadCanCollapse(message) ? 0 : undefined"
            :aria-expanded="execHeadCanCollapse(message) ? !isExecCollapsed(message) : undefined"
            aria-live="polite"
            @click="toggleExecCollapse(message)"
            @keydown.enter.prevent="toggleExecCollapse(message)"
            @keydown.space.prevent="toggleExecCollapse(message)"
          >
            <!-- 组头只表达「本轮」生命周期，避免与下方思考/工具阶段重复。
                 具体在搜索、生成还是处理文件，由细粒度进度和真实动作行承载。 -->
            <span v-if="execHeadTerminalClass(message)" class="exec-head-state-dot" aria-hidden="true"></span>
            <span class="exec-head-title">{{ execHeadTitle(message) }}</span>
            <span v-if="execHeadTimeText(message)" class="exec-head-elapsed">{{ execHeadTimeText(message) }}</span>
            <PremiumChevron
              v-if="execHeadCanCollapse(message)"
              class="exec-head-chevron"
              :direction="isExecCollapsed(message) ? 'right' : 'down'"
              :size="14"
              interactive
            />
          </div>
          <!-- reasoning 顶部尾窗：无公开正文时贴在状态头下；有正文后撤下，时间线 thinking 行继续留着。 -->
          <div
            v-if="showTransientReasoningSummary(message)"
            class="reasoning-summary-step active"
            :class="{ initial: showInitialProgressReasoning(message) }"
          >
            <div class="reasoning-summary-body" aria-live="polite">
              <p v-for="(line, index) in reasoningSummaryLines(message)" :key="index">{{ line }}</p>
            </div>
          </div>
          <Transition name="execution-stream-collapse">
            <div
              v-if="hasExecutionStreamBody(message)"
              v-show="!isExecCollapsed(message)"
              class="execution-stream-collapse"
            >
              <div class="execution-stream-list">
            <!-- 过程开场白始终属于执行过程：与后续动作、说明统一使用灰色；只有本容器
                 之后的最终总结使用深黑正文，避免同一过程里出现黑灰混杂。 -->
            <div
              v-if="hasVisiblePreamble(message) && !(hasResearchTeamPanel(message) && execHeadRunning(message))"
              class="message-bubble markdown-body preamble-body narrative-commentary"
              v-html="renderMarkdown(sanitizeAssistantBody(message.preamble || ''))"
            ></div>
            <!-- 主对话只呈现实际发生的动作、公开叙述与交付；语义任务计划集中在顶栏
                 「任务协作」面板，避免同一批步骤在内容区重复一遍。 -->
            <!-- 统一执行步骤的进入/离开过渡：归拢成员展开与收起、协作成员显隐、流式新增
                 都由同一 TransitionGroup 驱动。稳定 key 让已有行不被重建，离场行则会保留到
                 leave 动画结束后再真正移除，避免只有展开动画、收起瞬间消失。 -->
            <TransitionGroup
              v-if="executionRows(message).length"
              name="execution-row"
              tag="div"
              class="execution-row-flow"
            >
              <div
                v-for="row in executionRows(message)"
                :key="(row.step.kind === 'runGroup' ? 'g:' : 's:') + row.stepIndex"
                class="execution-row-transition"
              >
                <div
                  :class="['agent-step', row.step.kind, { 'step-nested': row.nested && row.step.kind !== 'note', 'run-group-member': row.groupMember, 'action-running': isLiveRunningAction(message.agentSteps, row.stepIndex) }]"
                >
                  <template v-if="row.step.kind === 'thinking'">
                    <ThinkingReasoningStep
                      :text="thinkingStepText(row.step)"
                      :seconds="thinkingStepSeconds(message, row.step, row.stepIndex)"
                      :active="isThinkingActive(message, row.stepIndex)"
                      :open="isThoughtOpen(message, row.stepIndex)"
                      @toggle="toggleThought(message, row.stepIndex)"
                    />
                  </template>
                  <template v-else-if="row.step.kind === 'compaction'">
                    <CompactionStep
                      :active="row.step.status === 'running'"
                      :failed="row.step.status === 'failed'"
                      :seconds="row.step.seconds"
                      :started-at="row.step.startedAt"
                    />
                  </template>
                  <template v-else-if="row.step.kind === 'note'">
                    <!-- Codex 式公开工作叙述：它是主 Agent 对用户说的话，不是一个带圆点的
                         “状态步骤”。直接复用普通 Assistant 正文的 message-bubble 排版，
                         完整穿插在前后真实动作之间；完成后随整个 execution-stream 一起
                         折叠，最终总结仍在折叠区之后。 -->
                    <div
                      class="message-bubble markdown-body agent-step-note narrative-commentary"
                      aria-live="polite"
                      v-html="renderMarkdown(cleanNarration(row.step.text))"
                    ></div>
                  </template>
                  <div v-else-if="row.step.kind === 'read'" class="agent-step-read">
                    <!-- 兼容旧轨迹：search_web 曾另起「浏览」行。新口径已并回搜索步骤；
                         这里仍用搜索图标 +「已打开结果页」，避免地球图标看起来像另一次浏览。 -->
                    <span class="step-node"><ExecutionActionIcon kind="search" /></span>
                    <span class="ast-label" :title="`已打开 ${row.step.pages.length} 个结果页`">已打开 {{ row.step.pages.length }} 个结果页</span>
                  </div>
                  <div
                    v-else-if="row.step.kind === 'subagentGroup'"
                    :class="['agent-step-sub', 'subagent-collab', row.step.running ? 'running' : 'completed']"
                    role="button"
                    tabindex="0"
                    @click="toggleSubCollab(message)"
                    @keydown.enter="toggleSubCollab(message)"
                  >
                    <span class="step-node">
                      <ExecutionActionIcon kind="subagent" :status="row.step.running ? 'running' : 'completed'" />
                    </span>
                    <span class="ast-label" :title="`${row.step.running ? '正在与' : '已与'} ${row.step.count} 个智能体协作`">{{ row.step.running ? '正在与' : '已与' }} {{ row.step.count }} 个智能体协作</span>
                    <span class="collab-toggle">{{ row.step.expanded ? '收起' : '展开' }}</span>
                  </div>
                  <!-- Codex 的 Spawn/SendInput 只在动作真实完成后落历史项：成员胶囊因此随
                       subagent.started 插在“任务已交给它”步骤之后，不抢占消息顶部。 -->
                  <div v-else-if="row.step.kind === 'subagent'" :class="['subagent-member-row', row.step.status]">
                    <button
                      type="button"
                      class="sub-team-pill"
                      :title="subagentStepTitle(message, row.step)"
                      @click="openSubagentStep(message, row.step)"
                    >
                      <span class="pill-agent-avatar">
                        <img
                          :src="subagentStepIconUrl(message, row.step)"
                          :alt="`${row.step.name || '子智能体'}头像`"
                          @error="recoverAgentIcon($event, subagentStepItem(message, row.step))"
                        />
                      </span>
                      <span class="pill-name">{{ row.step.name || '子智能体' }}</span>
                    </button>
                    <span v-if="row.step.status === 'failed'" class="subagent-member-error">委派失败</span>
                  </div>
                  <div v-else-if="row.step.kind === 'artifact'" :class="['agent-step-artifact', row.step.status]">
                    <span class="step-node">
                      <ExecutionActionIcon kind="artifact" :status="row.step.status" />
                    </span>
                    <span class="ast-label" :title="row.step.label">{{ row.step.label }}</span>
                    <span class="artifact-file-names" :title="row.step.files.map((file) => file.filename).join('、')">
                      {{ row.step.files.map((file) => file.filename).join('、') }}
                    </span>
                  </div>
                  <!-- 同类归拢组头（2026-07-24 拍板）：点击展开/收起成员行 -->
                  <div
                    v-else-if="row.step.kind === 'runGroup'"
                    :class="['agent-step-tool', runGroupStatusClass(row.step), 'run-group', { 'is-static': row.step.expandable === false }]"
                    :role="row.step.expandable === false ? undefined : 'button'"
                    :tabindex="row.step.expandable === false ? undefined : 0"
                    :aria-expanded="row.step.expandable === false ? undefined : row.step.expanded"
                    @click="row.step.expandable !== false && toggleRunGroup(row.step.groupKey)"
                    @keydown.enter="row.step.expandable !== false && toggleRunGroup(row.step.groupKey)"
                    @keydown.space.prevent="row.step.expandable !== false && toggleRunGroup(row.step.groupKey)"
                  >
                    <span class="step-node"><ExecutionActionIcon :kind="row.step.icon || 'bash'" /></span>
                    <span class="ast-label" :title="row.step.label || '沙箱探查'">{{ row.step.label || '沙箱探查' }}</span>
                    <span
                      v-if="runGroupSummary(row.step)"
                      class="ast-target-text is-plain"
                      :title="runGroupSummary(row.step)"
                    >{{ runGroupSummary(row.step) }}</span>
                    <PremiumChevron
                      v-if="row.step.expandable !== false"
                      :class="['rg-toggle', { open: row.step.expanded }]"
                      :direction="row.step.expanded ? 'down' : 'right'"
                      :size="13"
                      interactive
                    />
                  </div>
                  <div v-else-if="row.step.kind === 'verification'" :class="['agent-step-verification', row.step.status]">
                    <span class="step-node">
                      <ExecutionActionIcon kind="review" :status="row.step.status" :review-status="row.step.reviewStatus" />
                    </span>
                    <span class="ast-label" :title="row.step.label">{{ row.step.label }}</span>
                  </div>
                  <div
                    v-else
                    :class="['agent-step-tool', row.step.status, { 'has-shell-panel': hasShellPanel(row.step) }]"
                  >
                    <span class="step-node">
                      <ExecutionActionIcon
                        v-if="row.step.name === 'search_web'"
                        kind="web"
                        :status="stepIconStatus(row.step)"
                      />
                      <ToolOutlined v-else-if="row.step.name === 'use_skill'" class="loaded-skill-tool-icon" />
                      <ExecutionActionIcon
                        v-else
                        :kind="executionIconKind(row.step)"
                        :status="stepIconStatus(row.step)"
                      />
                    </span>
                    <!-- 行标题优先用模型现写的 intent（任务语言），进行中有阶段进度时让位给
                         实时进度（如「正在阅读 X」「正在生成第 3/8 页」滚动） -->
                    <span
                      v-if="row.step.name === 'search_web'"
                      class="ast-label ast-search-label"
                      :title="searchWebStepTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex))"
                    >{{ searchWebStepTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex)) }}</span>
                    <span v-else class="ast-label" :title="toolRowTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex))">{{ toolRowTitle(row.step, isLiveRunningAction(message.agentSteps, row.stepIndex)) }}</span>
                    <button
                      v-if="hasShellPanel(row.step)"
                      type="button"
                      :class="['ast-shell-toggle', { open: shellOpen[message.id + ':' + row.stepIndex] }]"
                      :title="shellOpen[message.id + ':' + row.stepIndex] ? '收起详情' : '查看执行详情'"
                      @click="shellOpen[message.id + ':' + row.stepIndex] = !shellOpen[message.id + ':' + row.stepIndex]"
                    >
                      <PremiumChevron
                        class="ast-shell-chevron"
                        :direction="shellOpen[message.id + ':' + row.stepIndex] ? 'down' : 'right'"
                        :size="13"
                        interactive
                      />
                    </button>
                    <Transition name="ast-shell-panel">
                      <div
                        v-if="hasShellPanel(row.step) && shellOpen[message.id + ':' + row.stepIndex]"
                        class="ast-shell-panel"
                      >
                        <div class="ast-shell-panel-clip">
                          <div class="ast-shell">
                            <div v-if="row.step.command" class="ast-shell-section cmd">
                              <div class="ast-shell-head">
                                <!-- bash 的命令全文也是「跑了什么」，不是「入参」（2026-07-28） -->
                                <span>{{ shellInputTitle(row.step.name) }}</span>
                                <button
                                  type="button"
                                  class="shell-copy"
                                  @click="copyShell(message.id + ':' + row.stepIndex + ':cmd', row.step.command)"
                                >{{ shellCopied[message.id + ':' + row.stepIndex + ':cmd'] ? '已复制' : '复制' }}</button>
                              </div>
                              <pre class="ast-shell-code">{{ row.step.command }}</pre>
                            </div>
                            <div
                              v-if="visibleStepOutput(row.step)"
                              :class="['ast-shell-section', 'out', { failed: shellOutputFailed(row.step) }]"
                            >
                              <div class="ast-shell-head">
                                <span>输出</span>
                                <button
                                  type="button"
                                  class="shell-copy"
                                  @click="copyShell(message.id + ':' + row.stepIndex + ':out', visibleStepOutput(row.step))"
                                >{{ shellCopied[message.id + ':' + row.stepIndex + ':out'] ? '已复制' : '复制' }}</button>
                              </div>
                              <pre class="ast-shell-code">{{ visibleStepOutput(row.step) }}</pre>
                            </div>
                          </div>
                        </div>
                      </div>
                    </Transition>
                  </div>
<!-- 网页截图穿插在执行步骤之间（2026-07-28 用户拍板）：不是行尾挂个小角标，
                       而是抓完哪个页面就把那个页面**摊在流里**，像人一边看一边翻给你看。
                       只画成功打开的页面——打不开的网页不出图（用户明确要求）：一张登录墙或
                       报错页摊在这里，读者要先看懂"这张是失败的"，反而比一行文字更费解。
                       折叠成「查阅网页 · N 次」时组头下最多摊 3 张，收起状态也保留这份实感。 -->
                  <div v-if="stepShots(row.step).length" class="ast-shot-figures">
                    <button
                      v-for="(s, si) in stepShots(row.step)"
                      :key="si"
                      type="button"
                      class="ast-shot-figure"
                      title="点开看大图"
                      @click.stop="lightboxSrc = s"
                    >
                      <img :src="s" alt="网页截图" loading="lazy" />
                    </button>
                  </div>
                </div>
              </div>
            </TransitionGroup>
            <!-- 没有已提交的真实步骤时保持列表区留空。 -->
              </div>
            </div>
          </Transition>
          <!-- 计划报告：对齐深度研究报告白卡。思考中不出灰条；整份计划齐了才整卡跳入。
               确认动作复用原来的补充框和「开始执行」，不另做一套按钮。 -->
          <article
            v-if="hasReadyPlanDocument(message) || isPlanConfirmation(message)"
            class="research-report-card"
          >
            <template v-if="hasReadyPlanDocument(message)">
              <header class="research-report-head">
                <span class="research-report-icon" aria-hidden="true">
                  <FileTextOutlined />
                </span>
                <strong class="research-report-title" :title="planCardTitle(message)">{{ planCardTitle(message) }}</strong>
                <span class="research-report-head-actions">
                  <button
                    type="button"
                    class="copy-report-action"
                    aria-label="复制"
                    :title="copiedPlanId === message.id ? '已复制' : '复制'"
                    @click.stop="copyPlanReport(message)"
                  >
                    <svg v-if="copiedPlanId === message.id" class="report-head-ic" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                    <svg v-else class="report-head-ic" viewBox="0 0 24 24" aria-hidden="true">
                      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
                      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
                    </svg>
                  </button>
                  <button type="button" aria-label="全屏" title="全屏" @click="openPlanReportViewer(message)">
                    <ExpandOutlined />
                  </button>
                </span>
              </header>
              <div
                class="research-report-preview plan-report-preview"
                role="button"
                tabindex="0"
                title="点击查看完整计划"
                @click="openPlanReportViewer(message)"
                @keydown.enter.prevent="openPlanReportViewer(message)"
              >
                <div
                  class="markdown-body plan-report-body"
                  v-html="planCardBodyHtml(message)"
                ></div>
              </div>
            </template>
            <div v-if="isPlanConfirmation(message)" class="plan-review-card">
              <div class="plan-review-compose">
                <input
                  :value="planReviseDraft[message.id] || ''"
                  type="text"
                  placeholder="需要调整计划，直接在这里补充…"
                  :disabled="Boolean(loading && message.interactive)"
                  @input="onPlanReviseInput(message.id, $event)"
                  @keydown.enter.prevent="submitPlanRevise(message)"
                />
                <button
                  type="button"
                  :disabled="Boolean(loading && message.interactive) || !String(planReviseDraft[message.id] || '').trim()"
                  @click="submitPlanRevise(message)"
                >确定</button>
              </div>
              <footer class="plan-review-actions">
                <button
                  v-if="!message.interactive?.revision_gate"
                  type="button"
                  class="plan-review-skip"
                  :disabled="Boolean(loading && message.interactive)"
                  @click="emit('resume', message.id, '__PLAN_SKIP__')"
                >跳过</button>
                <button
                  type="button"
                  class="plan-review-go"
                  :disabled="Boolean(loading && message.interactive)"
                  @click="emit('resume', message.id, message.interactive?.revision_gate ? '批准修订' : '好，请执行此计划。')"
                >{{ message.interactive?.revision_gate ? '批准修订' : '开始执行' }}</button>
              </footer>
            </div>
          </article>
          <!-- 逐页产物直播卡（2026-07-20 对标 Manus）：生成脚本每写完一页 SVG 就地渲染，
               头部=当前页标题+页码，换页交叉淡化；刷新不回放（终稿文件卡有真预览兜底） -->
          <div v-if="apCur(message)" class="artifact-pages-card">
            <div class="apc-head">
              <span v-if="isExecutionRunning(message)" class="exec-plan-live" aria-hidden="true"></span>
              <FilePptOutlined v-else class="apc-icon" />
              <span class="apc-title">{{ apCur(message)?.title }}</span>
              <button
                type="button"
                class="apc-nav"
                :disabled="!apCanPrev(message)"
                aria-label="上一页"
                @click.stop="apNav(message, -1)"
              >
                <PremiumChevron direction="left" :size="16" interactive />
              </button>
              <span class="apc-counter"
                >{{ apCur(message)?.index }} / {{ apTotal(message) }}</span
              >
              <button
                type="button"
                class="apc-nav"
                :disabled="!apCanNext(message)"
                aria-label="下一页"
                @click.stop="apNav(message, 1)"
              >
                <PremiumChevron direction="right" :size="16" interactive />
              </button>
            </div>
            <div class="apc-body">
              <Transition name="apc-fade" mode="out-in">
                <iframe
                  v-if="apCur(message)?.html"
                  :key="'h' + apCur(message)?.index"
                  class="apc-frame"
                  :srcdoc="apSrcdoc(apCur(message)?.html || '')"
                  sandbox=""
                  title="幻灯片直播预览"
                ></iframe>
                <img
                  v-else
                  :key="apCur(message)?.index"
                  class="apc-page"
                  :src="svgDataUrl(apCur(message)?.svg || '')"
                  alt=""
                />
              </Transition>
            </div>
          </div>
          <!-- 等待用户输入的交互节点（HITL 提问/消歧/审批）：与计划、工具、产物同处一条时间线
               （P0 统一任务运行面板）。不受折叠影响——折叠只收起已完成的过程，待办必须常显。
               Plan 确认和普通补充信息都走统一 Run input。 -->
          <QuestionCard
            v-if="message.interactive && message.interactive.type === 'userQuestions'"
            :card="askQuestionsCard(message)"
            :submitting="Boolean(loading && message.interactive)"
            @submit="(answers) => emit('resume', message.id, answers)"
          />
          <ChoiceQuestionCard
            v-else-if="message.interactive && message.interactive.type === 'userSelect' && !isPlanConfirmation(message)"
            :question="message.interactive.params?.description || '需要你补充信息后继续'"
            :hint="interactiveSourceHint(message)"
            :options="askChoiceOptions(message)"
            :mode="message.interactive.params?.multiple ? 'multiple' : 'single'"
            :model-value="message.interactive.params?.multiple ? (askMultiSel[message.id] || []) : ''"
            :allow-custom="Boolean(message.interactive.ask_user)"
            :allow-skip="Boolean(message.interactive.ask_user)"
            @update:model-value="(v) => (askMultiSel[message.id] = Array.isArray(v) ? v : [v])"
            @select="(value) => emit('resume', message.id, value)"
            @confirm="() => emit('resume', message.id, askMultiSel[message.id] || [])"
            @skip="emit('resume', message.id, '__ASK_USER_SKIP__')"
          />
          <div
            v-else-if="message.interactive && !isPlanConfirmation(message)"
            class="execution-hitl hitl-card ask-card"
          >
            <div class="ask-head ask-head-form">
              <span class="ask-title">{{ message.interactive.params?.description || '需要你补充信息后继续' }}</span>
              <span v-if="interactiveSourceHint(message)" class="ask-source">{{ interactiveSourceHint(message) }}</span>
            </div>
            <!-- formInput 完整字段渲染与「我的智能体」运行窗共用同一组件：下拉/开关/日期/
                 文件等字段冒泡到主对话不再退化成纯文本框 -->
            <div class="hitl-form-wrap">
              <InteractiveFormFields
                :fields="formItems(message.interactive)"
                :id-prefix="'chat-hitl-' + message.id"
                @submit="(v) => emit('resume', message.id, v)"
              />
            </div>
          </div>
          <div v-if="message.clarification?.length" class="execution-hitl hitl-card clarify-card">
            <p class="hitl-desc">匹配到多个可用智能体，请选择要使用的：</p>
            <div class="hitl-options">
              <button
                v-for="opt in message.clarification"
                :key="opt.id"
                type="button"
                @click="emit('clarify', message.id, opt)"
              >
                {{ opt.name }}
              </button>
            </div>
          </div>
          <div v-if="hasPendingApproval(message)" class="execution-hitl hitl-card approval-card">
            <p class="hitl-desc">
              {{ message.approval?.prompt || `操作「${message.approval?.tool_name || '敏感操作'}」需要你确认后才能执行` }}
            </p>
            <div class="hitl-options">
              <button type="button" class="approve-yes" @click="emit('approve', message.id, true)">通过并允许执行</button>
              <button type="button" class="approve-no" @click="emit('approve', message.id, false)">拒绝</button>
            </div>
          </div>
        </div>
        <!-- 联网搜索到来源后，正文上方给一个「已阅读 N 个网页」入口（放大镜 + 层叠站点图标），
             点击打开右侧「搜索结果」抽屉。知识库/文件类无网页则回落到「引用来源（N）」。 -->
        <button
          v-if="message.role === 'assistant' && sourceCitations(message).length && !hideResearchNarrative(message) && !hideSourceCitations"
          type="button"
          class="read-sources"
          @click="openSources(message)"
        >
          <template v-if="webPages(message).length">
            <SearchOutlined class="rs-icon" />
            <span class="rs-label">参考了 {{ webSourceCount(message) }} 个来源</span>
            <span class="rs-stack">
              <img
                v-for="(f, fi) in faviconList(message)"
                :key="fi"
                class="rs-favicon"
                :src="f"
                alt=""
                loading="lazy"
                @error="onFaviconError"
              />
            </span>
          </template>
          <template v-else>
            <LinkOutlined class="rs-icon" />
            <span class="rs-label">引用来源（{{ sourceCitations(message).length }}）</span>
          </template>
        </button>
        <!-- 附件读取降级提示（P0 附件生命周期）：未完整读取时回答必须显性降级 + 提供重试。
             「重试本轮」只在附件原文仍在本会话内存中时出现（重新生成会重新携带附件）；
             刷新后原文已失，如实提示重新上传——不许诺做不到的「重新读取」（审查 P1 修复）。 -->
        <div
          v-if="message.role === 'assistant' && message.attachmentIssues?.length"
          class="attachment-degraded"
          role="alert"
        >
          <WarningOutlined />
          <span class="ad-text">
            附件 {{ attachmentIssueText(message) }} 未能完整读取，本轮回答可能不完整。
          </span>
          <button
            v-if="!preserveAnswers && retryAttachments && !loading && message.id === lastAssistantId"
            type="button"
            class="ad-retry"
            title="重新携带附件重新回答本轮"
            @click="emit('regenerate')"
          >
            重试本轮
          </button>
          <span v-else-if="!loading && message.id === lastAssistantId" class="ad-hint">
            重新上传附件后可重试
          </span>
        </div>
        <!-- Markdown is sanitized with xss before rendering.
             助手终答一律走 v-html；用户消息才用纯文本。深度研究对齐计划卡：
             生成中只出骨架条，报告齐了整卡入场；同一段 Markdown 不进对话气泡。
             执行过程仍保留。 -->
        <div
          v-if="message.role === 'assistant' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"
          class="message-bubble markdown-body narrative-final"
          :class="{ 'after-execution': showExecutionTrace(message) }"
          v-html="renderAssistantHtml(message)"
        ></div>
        <template v-if="message.role === 'user'">
          <div v-if="message.attachments?.length" class="user-attachments">
            <!-- 多图统一瓦片：同消息图片>1 时等大方块（大小一致），单图保持自适应大图 -->
            <AttachmentCard
              v-for="(att, ai) in message.attachments"
              :key="ai"
              :attachment="att"
              large
              :uniform="imageAttachmentCount(message.attachments) > 1"
              :clickable="canOpenSentAttachment(att)"
              @preview="lightboxSrc = $event"
              @open="openSentAttachment(att)"
            />
          </div>
          <div v-if="editingId === message.id" class="message-edit">
            <textarea
              ref="editTextareaRef"
              v-model="editingText"
              rows="3"
              @keydown.esc="cancelEdit"
              @keydown.enter.exact.prevent="saveEdit(message)"
            />
            <div class="message-edit-actions">
              <button type="button" class="edit-cancel" @click="cancelEdit">取消</button>
              <button type="button" class="edit-save" :disabled="!editingText.trim()" @click="saveEdit(message)">
                发送
              </button>
            </div>
          </div>
          <div v-else-if="message.content" class="message-bubble">{{ message.content }}</div>
        </template>
        <!-- 计量行只服务生成中的进度感：点阵动画 + 实时阶段文案。终答落定后移除。 -->
        <div
          v-if="message.role === 'assistant' && isExecutionRunning(message) && !hasResearchTeamPanel(message)"
          class="gen-meter"
        >
          <ActivityOrb class="gen-meter-orb" />
          <span class="gen-meter-status">{{ generationStatusText(message) }}</span>
        </div>
        <!-- 「本轮交付」汇总块已按用户拍板移除（2026-07-15）：交付信息由执行时间线 + 文件卡承载 -->
        <!-- 研究报告结构卡是交付物，必须在执行流之外：停止后、没有 html 落盘时也用 Markdown 出白卡。 -->
        <p v-if="showResearchStructureCard(message)" class="research-report-stats">
          研究完成情况：{{ researchCompletionStats(message) }}
        </p>
        <article
          v-if="showResearchStructureCard(message)"
          class="research-report-card"
        >
          <header class="research-report-head">
            <span class="research-report-icon" aria-hidden="true">
              <FileTextOutlined />
            </span>
            <strong class="research-report-title" :title="researchCardTitle(message)">{{ researchCardTitle(message) }}</strong>
            <span class="research-report-head-actions">
              <ResearchReportExportMenu
                v-if="researchCardPreviewHtml(message) || researchDownloadFile(message)"
                :file-id="researchDownloadFile(message)?.id"
                :filename="researchDownloadFile(message)?.filename || `${researchCardTitle(message)}.html`"
                :html="researchCardPreviewHtml(message)"
              >
                <button type="button" aria-label="下载" title="下载">
                  <svg class="report-head-ic" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <path d="m7 10 5 5 5-5" />
                    <path d="M12 15V3" />
                  </svg>
                </button>
              </ResearchReportExportMenu>
              <button type="button" aria-label="全屏" title="全屏" @click="openResearchStructureViewer(message)">
                <svg class="report-head-ic" viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M15 3h6v6" />
                  <path d="M9 21H3v-6" />
                </svg>
              </button>
            </span>
          </header>
          <div
            class="research-report-preview"
            :class="{ 'has-markdown': Boolean(researchCardMarkdown(message)) }"
            :role="researchCardMarkdown(message) ? 'button' : undefined"
            :tabindex="researchCardMarkdown(message) ? 0 : undefined"
            :title="researchCardMarkdown(message) ? '点击查看完整报告' : undefined"
            @click="onResearchStructurePreview(message, $event)"
            @keydown.enter="onResearchStructurePreview(message, $event)"
          >
            <div
              v-if="researchCardMarkdown(message)"
              class="markdown-body plan-report-body research-document-body"
              v-html="researchCardBodyHtml(message)"
            ></div>
            <InlineFilePreview
              v-else-if="researchDownloadFile(message)"
              :file="researchDownloadFile(message)!"
              :slides-file="slidesSiblingOf(message, researchDownloadFile(message)!)"
              @download="downloadGeneratedFile(researchDownloadFile(message)!)"
              @ai-edit="(p) => emit('aiEditFile', researchDownloadFile(message)!, p)"
              @save-pages="(pages, done) => onSlidesSave(message, researchDownloadFile(message)!, pages, done)"
            />
          </div>
        </article>
        <!-- 产物卡只在回合结束后出现（2026-07-15 用户拍板）：运行中产物还在被审查/重生成
             （v5→v6…），提前亮卡会误导「已交付」；过程中的生成事实由时间线「已生成并保存」行承载 -->
        <div v-if="message.role === 'assistant' && visibleGeneratedFiles(message).length && !isExecutionRunning(message)" class="generated-files">
          <template v-for="(file, fileIdx) in visibleGeneratedFiles(message)" :key="file.id">
            <article
              class="generated-file-card"
              :style="{ animationDelay: Math.min(fileIdx * 0.06, 0.3) + 's' }"
            >
            <span :class="['generated-file-icon', `k-${genFileKind(file)}`]">
              <component :is="GEN_KIND_ICON[genFileKind(file)]" />
            </span>
            <span class="generated-file-copy">
              <strong :title="generatedFileTitle(file)">{{ generatedFileTitle(file) }}</strong>
              <em :title="file.review?.summary || undefined"
                >{{ generatedFileKindLabel(file) }} · {{ formatFileSize(file.size) }} ·
                {{ file.draft ? '草稿已存入版本历史（原文件保持上一版）' : '已保存到我的文件'
                }}<template v-if="!file.draft && file.versionNo && file.versionNo > 1"
                > · v{{ file.versionNo }}</template
                ><template v-if="file.review && file.review.status !== 'passed'"
                > · <span :class="['review-badge', 'review-' + file.review.status]">{{
                  reviewBadgeText(file.review.status)
                }}</span></template></em
              >
            </span>
            <span class="generated-file-actions">
              <button v-if="filePreviewKind(file)" type="button" @click="emit('previewFile', file)">
                预览
              </button>
              <button type="button" :class="{ 'generated-file-secondary': filePreviewKind(file) }" @click="downloadGeneratedFile(file)">下载</button>
              <button type="button" class="generated-file-secondary" @click="emit('showVersions', file)">版本历史</button>
              <!-- 必须写成调用形式：裸函数名会把 MouseEvent 当 step 传进去 -->
              <button type="button" class="generated-file-secondary" @click="goMyFiles()">我的文件</button>
            </span>
            <InlineFilePreview
              :file="file"
              :slides-file="slidesSiblingOf(message, file)"
              @download="downloadGeneratedFile(file)"
              @ai-edit="(p) => emit('aiEditFile', file, p)"
              @save-pages="(pages, done) => onSlidesSave(message, file, pages, done)"
            />
            </article>
          </template>
        </div>
        <!-- 谢幕对账卡已按用户拍板移除（2026-07-21 上线当天）：真机观感重复且契约匹配
             文案生硬。task.delivery.reviewed 数据链路保留（executionTimeline/协作面板可用） -->
        <!-- 失败降噪（2026-07-20）：已交付正文/产物的轮次不再挂红色横幅——错误多半是收尾
             阶段的断流误报，交付物本身是好的；真正颗粒无收的失败照旧标红 -->
        <div
          v-if="message.role === 'assistant' && message.error && !hasDelivered(message) && !isUserClarificationMessage(message)"
          class="message-error"
          :class="{ 'message-error-policy': isSensitiveWordRejection(message) }"
          role="alert"
        >
          <span class="message-error-icon" aria-hidden="true"><WarningOutlined /></span>
          <span class="message-error-copy">
            <strong>{{ errorNoticeTitle(message) }}</strong>
            <span>{{ errorNoticeDetail(message) }}</span>
          </span>
        </div>
        <!-- 引用来源入口只保留正文上方的一处（read-sources）：产物卡下再放一颗是重复入口，
             2026-07-13 用户拍板移除 -->
        <div
          v-if="message.role === 'assistant' && hasVisibleAssistantBody(message) && !hideResearchNarrative(message)"
          class="message-actions"
        >
          <button
            type="button"
            :title="copiedId === message.id ? '已复制' : '复制'"
            @click.stop.prevent="copyMessage(message)"
          >
            <svg v-if="copiedId === message.id" class="msg-action-ic" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
            <svg v-else class="msg-action-ic" viewBox="0 0 24 24" aria-hidden="true">
              <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
              <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
            </svg>
          </button>
          <!-- 旧版本只读（第四批项 3）：操作按钮只留复制，不提供反馈/重新生成 -->
          <button
            v-if="!message.superseded"
            type="button"
            :class="{ active: message.feedback === 'up' }"
            title="有帮助"
            @click="emit('feedback', message.id, message.feedback === 'up' ? null : 'up')"
          >
            <LikeOutlined />
          </button>
          <button
            v-if="!message.superseded"
            type="button"
            :class="{ active: message.feedback === 'down' }"
            title="待改进"
            @click="emit('feedback', message.id, message.feedback === 'down' ? null : 'down')"
          >
            <DislikeOutlined />
          </button>
        </div>
        <div
          v-if="message.role === 'user' && editingId !== message.id && message.content"
          class="message-actions user-actions"
        >
          <button
            type="button"
            :title="copiedId === message.id ? '已复制' : '复制'"
            @click.stop.prevent="copyMessage(message)"
          >
            <svg v-if="copiedId === message.id" class="msg-action-ic" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
            <svg v-else class="msg-action-ic" viewBox="0 0 24 24" aria-hidden="true">
              <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
              <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
            </svg>
          </button>
          <button
            v-if="!loading && !preserveAnswers"
            type="button"
            title="编辑并重新发送"
            @click.stop="startEdit(message)"
          >
            <svg class="msg-action-ic" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 20h9" />
              <path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z" />
            </svg>
          </button>
        </div>
        <!-- HITL 提问/消歧/审批卡已并入上方执行时间线（P0 统一任务运行面板），不再独立悬挂 -->
        <!-- 内部智能体 > 外部应用：有内部推荐卡时不再叠外部应用卡（优先级：内部 > 外部 > 纯文字） -->
        <div
          v-if="message.externalRecs?.length && !message.recommendedAgents?.length"
          class="hitl-card external-rec-card"
        >
          <p class="hitl-desc">平台暂无可直接办理的能力，你可以前往以下外部应用：</p>
          <div class="external-rec-list">
            <a
              v-for="rec in message.externalRecs"
              :key="rec.id"
              class="external-rec-item"
              :href="rec.url || undefined"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span class="external-rec-name">{{ rec.name }}</span>
              <span v-if="rec.description" class="external-rec-desc">{{ rec.description }}</span>
              <span class="external-rec-action">前往使用 →</span>
            </a>
          </div>
        </div>
        <div v-if="message.recommendedAgents?.length" class="message-agent-recs">
          <p class="agent-rec-intro">
            {{ message.recommendationIntent === 'explicit_request'
              ? '这些智能体与刚才的需求比较匹配'
              : '如果你希望用更专门的流程继续，可以试试' }}
          </p>
          <button
            v-for="app in message.recommendedAgents"
            :key="app.id"
            class="agent-rec-card"
            type="button"
            @click="$emit('openAgent', app)"
          >
            <span class="agent-rec-icon">
              <img
                v-if="getAgentIconUrl(app)"
                :src="getAgentIconUrl(app)"
                :alt="app.appName"
                @error="recoverAgentIcon($event, app)"
              />
              <span class="agent-rec-icon-fallback">{{ getAgentInitials(app) }}</span>
            </span>
            <span class="agent-rec-copy">
              <span class="agent-rec-eyebrow">专业智能体</span>
              <strong>{{ app.appName }}</strong>
              <em>{{ app.recommendReason || app.appRemark || '使用该智能体继续完成当前任务' }}</em>
            </span>
            <span class="agent-rec-action">
              <span>打开智能体</span>
              <PremiumChevron direction="right" :size="15" interactive />
            </span>
          </button>
        </div>
      </div>
    </div>

    <ImageLightbox :src="lightboxSrc" @close="lightboxSrc = null" />
    <InlineFilePreview
      v-if="attachmentViewerFile"
      :file="attachmentViewerFile"
      standalone
      @ready="stopAttachmentViewerLoading"
      @close="closeAttachmentViewer"
      @failed="onAttachmentViewerFailed"
    />

    <!-- mermaid 全屏预览：Teleport 到 body 避开 v-html 孤岛与父级 overflow；点遮罩/关闭钮/Esc 关闭 -->
    <Teleport to="body">
      <div
        v-if="mermaidFullscreenSvg"
        class="mermaid-fs-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="流程图全屏预览"
        @click="mermaidFullscreenSvg = null"
      >
        <button
          type="button"
          class="mermaid-fs-close"
          title="关闭（Esc）"
          @click="mermaidFullscreenSvg = null"
        >
          <CloseOutlined />
        </button>
        <!-- eslint-disable-next-line vue/no-v-html -->
        <div class="mermaid-fs-inner" @click.stop v-html="mermaidFullscreenSvg"></div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div
        v-if="planReportViewer"
        class="plan-report-fs-overlay"
        role="dialog"
        aria-modal="true"
        :aria-label="planReportViewer.title"
        @click="planReportViewer = null"
      >
        <button
          type="button"
          class="mermaid-fs-close"
          title="关闭（Esc）"
          @click="planReportViewer = null"
        >
          <CloseOutlined />
        </button>
        <article class="plan-report-fs-paper" @click.stop>
          <h1>{{ planReportViewer.title }}</h1>
          <div class="markdown-body plan-report-body" :class="{ 'research-document-body': planReportViewer.research }" v-html="planReportViewer.html"></div>
        </article>
      </div>
    </Teleport>

    <SourcesPanel :open="sourcesOpen" :sources="activeSources" @close="sourcesOpen = false" />

    <nav v-if="userTurns.length >= 3" class="conversation-nav" aria-label="对话导航">
      <button
        v-for="turn in userTurns"
        :key="turn.id"
        type="button"
        :class="['conv-nav-dot', { active: activeTurnId === turn.id }]"
        @click="scrollToMessage(turn.id)"
      >
        <span class="conv-nav-tip">{{ turnLabel(turn) }}</span>
      </button>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import {
  RobotOutlined,
  LikeOutlined,
  DislikeOutlined,
  LinkOutlined,
  SearchOutlined,
  WarningOutlined,
  HistoryOutlined,
  FileTextOutlined,
  FileImageOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  FileExcelOutlined,
  FilePptOutlined,
  FileMarkdownOutlined,
  FileZipOutlined,
  FileOutlined,
  CloseOutlined,
  ToolOutlined,
  ExpandOutlined,
} from '@ant-design/icons-vue';
import ActivityOrb from './ActivityOrb.vue';

import PremiumChevron from './PremiumChevron.vue';
import ResearchTeamPanel from './ResearchTeamPanel.vue';
import type { ResearchTeamSnapshot } from '../utils/researchTeam';
import ResearchReportExportMenu from './ResearchReportExportMenu.vue';
import { presentResearchReport } from '../utils/researchReportPresentation';
import ThinkingReasoningStep from './ThinkingReasoningStep.vue';
import CompactionStep from './CompactionStep.vue';
import MarkdownIt from 'markdown-it';
import hljs from 'highlight.js';
import mermaid from 'mermaid';
import mdKatex from '@traptitech/markdown-it-katex';
import { FilterXSS, getDefaultWhiteList } from 'xss';
import { getAgentFallbackIcon, getAgentIconUrl, getAgentInitials, recoverAgentIcon } from '../agentIcon';
import { message as antMessage } from 'ant-design-vue';
import { copyText } from '../utils/clipboard';
import { downloadUserFile, type UserFileItem } from '../myfiles.api';
import ExecutionActionIcon from './ExecutionActionIcon.vue';
import { executionIconKind } from '../composables/executionIconKind';
import {
  buildExecutionRows,
  collapseSandboxRuns,
  execRowsSignature,
  fileTypeLabel,
  formatDuration,
  formatFileSize,
  isLiveRunningAction,
  buildPlanCardMarkdown,
  planReportOwnsBody,
  shouldHoldPlanStreamOffBody,
  shellInputTitle,
  searchWebStepTitle,
  toolStepDisplay,
  bashRowTitle,
  type AgentStep,
  type ArtifactPageView,
  type ExecutionPlan,
  type ExecutionRow,
  type SubagentRun,
  type QuestionCardView,
} from '../composables/executionTimeline';
import { stepIconStatus } from '../composables/executionIconStatus';
import {
  extractArtifactTitle,
  hashArtifact,
  preprocessArtifacts,
  type Artifact,
} from '../utils/artifactParser';
import { stripInternalScriptBlocks } from '../utils/internalScript';
import { compileAnswerLayout } from '../utils/compileAnswerLayout';
import { commentaryRepeatsFinalAnswer } from '../utils/commentaryVisibility';
import { stopProtocolLinkAtCjkPunctuation } from '../utils/markdownLinkify';
import { stripInlineSourceMarkers } from '../utils/stripInlineSourceMarkers';
import { isResearchTurn, linkResearchCites, researchCompletionStats as buildResearchCompletionStats, researchStructureMarkdown, researchStructureTitle, sanitizeResearchTitle, stripLeadingTitleHeadings, stripResearchScaffold } from '../utils/researchReport';
import type { AttachmentIssue, GeneratedFile, SkillItem, SubagentItem } from '../agentApi';
import { myFilesRouteFor, visibleDeliverables } from '../composables/deliverable';
import { fileKindOf } from '../composables/fileKind';
import { generationMeterStatus, interviewGenerationMeterStatus } from '../composables/generationMeterStatus';
import {
  AUTO_FOLLOW_BUTTON_DISTANCE,
  AUTO_FOLLOW_PAUSE_DISTANCE,
  AUTO_FOLLOW_RESUME_DISTANCE,
  canAutoFollow,
  hasAutoFollowReadingPause,
  resolveMessageScrollContainer,
  scrollDirection,
  shouldPauseAutoFollowForThought,
  shouldResumeManualAutoFollow,
  shouldSkipProgrammaticStick,
} from '../composables/messageAutoFollow';
import { isUserClarificationMessage } from '../utils/userClarification';
import AttachmentCard, { type AttachmentInfo } from './AttachmentCard.vue';
import ChoiceQuestionCard from './ChoiceQuestionCard.vue';
import InteractiveFormFields from './InteractiveFormFields.vue';
import QuestionCard from './QuestionCard.vue';
import InlineFilePreview from './InlineFilePreview.vue';
import ImageLightbox from './ImageLightbox.vue';
import { isRenderableChatImageUrl, renderChatImageFigure, renderMissingChatImage, retryChatImage, setChatImageLoadState } from '../utils/chatInlineImages';
import SourcesPanel, { type SourceItem } from './SourcesPanel.vue';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github.css';

export type { SubagentRun } from '../composables/executionTimeline';

export interface ChatMessage {
  id: number;
  dbId?: number;
  role: 'user' | 'assistant';
  content: string;
  /** 这条用户消息发送时的 Skill 快照；编辑重发必须恢复它，不读 composer 当前选择。 */
  turnSkills?: SkillItem[];
  /** 开场白（message.commentary 首段，ChatGPT 式「先答一句」）：渲染在执行时间线上方，
   *  不属于最终回答正文；刷新由 execution_trace.preamble 回放恢复 */
  preamble?: string;
  /** 实时 commentary 渐进绘制的短命前端定位键；不持久化。 */
  preambleStreamKey?: string;
  /** 流式 reducer 的临时准备提示标记；首个模型开场白会覆盖它。 */
  preambleIsInitialProgress?: boolean;
  /**
   * 对话层硬类型（P0）：ack / commentary / final / system_note。
   * 缺省兼容旧消息；有值时渲染层可区分「确认语 / 过程旁白 / 终答 / 平台注记」。
   */
  narrativeKind?: 'ack' | 'commentary' | 'final' | 'system_note';
  /** 计划报告（计划轮那份计划）：渲染成独立卡片 */
  planReport?: string;
  /** 用户已同意执行当前计划：后续助手气泡不再把正文藏成计划卡。 */
  planExecutionUnlocked?: boolean;
  recommendedAgents?: any[];
  recommendationIntent?: 'explicit_request' | 'capability_gap';
  feedback?: 'up' | 'down' | null;
  interactive?: {
    run_id?: string;
    resume_id?: string;
    type?: string;
    params?: any;
    /** ask_user_choice 消歧提问卡：打字=自由回答，发送新消息时收起本卡 */
    ask_user?: boolean;
    kind?: 'clarification' | 'plan_confirmation';
    revision_gate?: boolean;
    plan_version?: number;
    approved_version?: number;
    plan_steps?: Array<{ key?: string; title: string; status?: string; detail?: string; acceptance?: string }>;
    previous_steps?: Array<{ key?: string; title: string; status?: string; detail?: string; acceptance?: string }>;
    goal_contract?: {
      goal?: string;
      deliverable?: string;
      success_criteria?: string[];
      forbidden?: string[];
      budget_hint?: string;
    };
    /** 挂起来源子智能体身份（后端 input.required 直带，事件回放同样携带）；消歧卡不带 */
    subagent_id?: string;
    subagent_name?: string;
  } | null;
  /** 引用来源（§7.3 知识库/联网/文件三源共用），随消息事件下发并持久化 */
  citations?: Array<{
    type?: string;
    title?: string;
    url?: string;
    source?: string;
    snippet?: string;
  }>;
  /** 计量行本轮消耗（2026-07-24）：turnBaselineTokens=本轮起点上下文，liveContextTokens=最新；
   *  差值=本轮喂给模型的真实增量（含搜索/读页正文），让计量行工具期也真实增长而非恒 0 */
  turnBaselineTokens?: number;
  liveContextTokens?: number;
  /** 当前连接的 reasoning_content 尾窗；burst 完成即清空，不持久化。 */
  reasoningSummary?: string;
  /** 思考面板折叠态：答案首个 delta 到达时自动折叠，用户可点开回看 */
  executionCollapsed?: boolean;
  /** 自动折叠一次性闸（见 executionTimeline.revealAssistantOutput）：用户手动操作后自动折叠让位 */
  executionAutoCollapsed?: boolean;
  /** 多子智能体聚合行的本地展开态，仅影响展示，不写入服务端轨迹。 */
  subCollabExpanded?: boolean;
  /** 主对话内联过程流：思考、工具、沙箱、子智能体按到达时序直接交错展示 */
  agentSteps?: AgentStep[];
  /** 模型已经发出的真实工具调用计划；tool.* / subagent.* 事件驱动状态。 */
  executionPlan?: ExecutionPlan;
  /** 模型 update_plan 拆解的语义任务步骤（task.plan 事件）：顶栏「任务与协作」面板数据源，
   *  形状与 executionTimeline.ExecutionMessage.taskPlan 一致（applyTaskPlan 写入） */
  taskPlan?: Array<{
    key: string;
    title: string;
    status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'invalidated';
    detail?: string;
    acceptance?: string;
  }>;
  taskPlanPrevious?: Array<{
    key: string;
    title: string;
    status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'invalidated';
    detail?: string;
    acceptance?: string;
  }>;
  taskPlanVersion?: number;
  taskPlanApprovedVersion?: number;
  taskPlanDiverged?: boolean;
  taskPlanEverStarted?: boolean;
  taskPlanActiveKey?: string;
  taskGoalContract?: {
    goal?: string;
    deliverable?: string;
    success_criteria?: string[];
    forbidden?: string[];
    budget_hint?: string;
  };
  /** 工具调用步骤（SSE v1 tool.* 事件） */
  toolSteps?: Array<{ name: string; status: 'running' | 'completed' | 'failed' }>;
  /** 逐页产物直播（2026-07-20 对标 Manus）：生成中每页 SVG 的实时预览 */
  artifactPages?: ArtifactPageView[];
  /** 任务级服务端计时；断线重连不重置 */
  runId?: string;
  /** 权威运行状态：受理但未被 Worker 领取时不能显示模型正在分析。 */
  runStatus?: string;
  /** 同一 Run 的可见执行分段。用户插话会结束当前段并在其后创建新段，不移动旧记录。 */
  executionSegmentIndex?: number;
  executionSegmentEndedAt?: number;
  executionSegmentInputId?: string;
  /** 服务端 RunEvent 游标范围；刷新回放与实时分段对齐用，不参与用户文案。 */
  executionSegmentStartSequence?: number;
  executionSegmentEndSequence?: number;
  /** 服务端 Run 模式；research 用于将同一套执行时间线标记为研究过程。 */
  agentMode?: string;
  /** 深度研究阶段机进度，驱动计量行阶段文案。 */
  researchProgress?: {
    team?: ResearchTeamSnapshot;
    stage?: string;
    topic?: string;
    topicIndex?: number;
    topicTotal?: number;
    sourcesFound?: number;
    searchCalls?: number;
    citationCount?: number;
    label?: string;
  };
  runStartedAt?: number;
  runCompletedAt?: number;
  runDurationMs?: number;
  /** 用户主动停止：执行卡标题「已停止」，未完步骤统一收尾 */
  runCancelled?: boolean;
  /** Completion Verifier 裁定的部分完成终态。 */
  runPartial?: boolean;
  /** 整轮终态：markRunFailed（run.failed/error 终态事件）或历史 execution_trace.status
   *  ==='failed' 置位，不因某个步骤中途失败又重试成功而置位——isExecFailed 判据 */
  runFailed?: boolean;
  /** bash/write_file/edit_file/download_url 的结构化文件产物（口径见 ARTIFACT_PRODUCERS） */
  generatedFiles?: GeneratedFile[];
  loadedCapabilities?: string[];
  /** call_subagent 编排（subagent.* 事件，ADR-046）：主模型自主委派的子智能体及状态 */
  subagentCalls?: Array<{ name: string; status: 'running' | 'completed' | 'failed' }>;
  /** 子智能体「工作窗口」运行档（subagent.node/delta/reasoning 累积）：每次 call_subagent 一档，
   *  点击时间线子智能体节点在侧栏窗口展示其逐节点干活流程 + 思考 + 输出 */
  subagentRuns?: SubagentRun[];
  /** 自动路由命中的子智能体名（route.selected 事件，§8.1/ADR-045） */
  routedAgent?: string;
  /** R5 消歧：匹配到多个候选智能体，交用户选择（clarification.required 事件） */
  clarification?: Array<{ id: string; name: string }>;
  /** R5 消歧卡原轮一次性上下文快照（skill/上传附件）：点选重发精确复用该轮的，
   *  而非“最近一轮”的（出卡后用户可能又发过别的消息）；useCenterChat 写入/消费 */
  clarificationContext?: {
    skills: SkillItem[];
    attachments: Array<{ filename: string; text: string; kind?: string; image_url?: string }>;
  } | null;
  /** 敏感工具审批（approval.required 事件，§11）：用户通过后同幂等键重试即执行 */
  approval?: { call_id: string; tool_name?: string; prompt?: string } | null;
  /** R6 外部应用推荐（recommendation 事件，§8.4）：前往使用卡，不派发 */
  externalRecs?: Array<{ id: string; name: string; description?: string; url?: string }>;
  /** 自动压缩提示（context.compacted 事件，§13）：系统已整理较早对话以继续 */
  compactedNote?: string;
  /** run.failed / error 事件：单独标红提示，区别于正常回复 */
  error?: string | null;
  /** 用户消息随附的附件（图片缩略图 / 文档卡片），仅展示用 */
  attachments?: AttachmentInfo[];
  /** 附件读取降级（attachments.status 事件，P0）：本轮未完整读取的附件，回答上方提示条 */
  attachmentIssues?: AttachmentIssue[];
  /** 本消息是被「重新生成」取代的旧版本（status=superseded，第四批项 3）：只读渲染、置灰，
   *  不出现在对话流顶层（归组到当前回答的 supersededVersions，展开时才插入渲染） */
  superseded?: boolean;
  /** 归组到本条当前回答下的旧版本（useCenterChat.groupSupersededMessages 写入）：
   *  默认折叠为「已重新生成 · 查看上一版」chip，点击展开旧气泡+旧执行卡（只读） */
  supersededVersions?: ChatMessage[];
}

const props = defineProps<{
  messages: ChatMessage[];
  /** 当前用户可委派目录：仅用于把运行档 id 映射为最新智能体头像。 */
  subagents?: SubagentItem[];
  loading?: boolean;
  /** 附件原文仍在本会话内存中（上一轮带过附件且未刷新）：降级横幅才提供「重试本轮」 */
  retryAttachments?: boolean;
  /** 面试重答保留原始答案，通过场次控件创建新作答。 */
  preserveAnswers?: boolean;
  /** 校园百事通不展示「引用来源」入口；依据仍在检索步骤里。 */
  hideSourceCitations?: boolean;
  /** 表达排版差异，不改变图片引用、运行状态或工具能力。 */
  answerLayout?: 'standard' | 'campus';
  /** 面试助手使用面试相关的进行中文案；主对话保持原计量行。 */
  meterCopy?: 'standard' | 'interview';
}>();

const emit = defineEmits<{
  (e: 'openAgent', app: any): void;
  (e: 'feedback', messageId: number, value: 'up' | 'down' | null): void;
  // 普通「重新生成」（同一模型重答）。modelId 保留可选形参兼容 ChatTab 透传，当前恒为空
  // ——「换模型重答」下拉已按产品要求移除（203f6f4），只保留同模型重新生成。
  (e: 'regenerate', modelId?: string): void;
  (e: 'resume', messageId: number, resumeValue: unknown): void;
  (e: 'edit', messageId: number, content: string): void;
  (e: 'clarify', messageId: number, option: { id: string; name: string }): void;
  (e: 'approve', messageId: number, approved: boolean): void;
  (e: 'scrollState', atBottom: boolean): void;
  (e: 'openArtifact', artifact: Artifact): void;
  /** live=true：本次扫描发生在流式期间或流收尾 pass，新 id 视为「本轮新完成」 */
  (e: 'artifacts', list: Artifact[], live?: boolean): void;
  /** 生成文件卡「版本历史」：在弹窗中查看/下载/恢复该文件的历史版本（P0 交付清单） */
  (e: 'showVersions', file: GeneratedFile): void;
  /** 生成文件卡「预览」：可预览类型（html/文本）在右侧产物面板打开（产物与文件打通） */
  (e: 'previewFile', file: GeneratedFile): void;
  (e: 'aiEditFile', file: GeneratedFile, payload: { instruction: string; scope: 'page' | 'all'; page: number }): void;
  (e: 'saveSlides', slidesFile: GeneratedFile, deckFile: GeneratedFile, pages: string[], done: (ok: boolean) => void): void;
  /** 执行团队胶囊：点委派动作下方的成员名牌 → 直接弹该子智能体过程窗 */
  (e: 'openSubagent', run: SubagentRun): void;
}>();

/** 委派帧里的头像是运行档权威身份快照；当前目录仅补全存量历史。 */
function getSubagentItem(run: SubagentRun): SubagentItem {
  const current = props.subagents?.find((item) => item.id === run.id);
  return {
    id: run.id,
    name: run.name || '子智能体',
    icon: run.icon || current?.icon || '',
  };
}

function subagentRunForStep(
  message: ChatMessage,
  step: Extract<AgentStep, { kind: 'subagent' }>,
): SubagentRun | undefined {
  const runs = message.subagentRuns || [];
  return (step.runKey ? runs.find((run) => run.runKey === step.runKey) : undefined)
    || [...runs].reverse().find((run) => run.name === step.name);
}

function subagentStepItem(
  message: ChatMessage,
  step: Extract<AgentStep, { kind: 'subagent' }>,
): SubagentItem {
  const run = subagentRunForStep(message, step);
  if (run) return getSubagentItem(run);
  const current = props.subagents?.find((item) => item.name === step.name);
  return current || { id: '', name: step.name || '子智能体', icon: '' };
}

function subagentStepIconUrl(
  message: ChatMessage,
  step: Extract<AgentStep, { kind: 'subagent' }>,
): string {
  const item = subagentStepItem(message, step);
  return getAgentIconUrl(item) || getAgentFallbackIcon(item);
}

function subagentStepTitle(
  message: ChatMessage,
  step: Extract<AgentStep, { kind: 'subagent' }>,
): string {
  const run = subagentRunForStep(message, step);
  return run ? pillTitle(run) : step.name || '子智能体';
}

function openSubagentStep(
  message: ChatMessage,
  step: Extract<AgentStep, { kind: 'subagent' }>,
): void {
  const run = subagentRunForStep(message, step);
  if (run) emit('openSubagent', run);
}

/** 悬停提示：真实智能体名称 + 可选岗位名 + 终态/验收计数 + 委派任务。 */
function pillTitle(run: SubagentRun): string {
  const acc = run.review || run.acceptance;
  const state = run.status === 'running'
    ? '进行中'
    : run.interrupted
      ? '已中断'
      : run.status === 'failed'
        ? '失败'
        : acc && acc.total
          ? `验收 ${acc.passedCount}/${acc.total}`
          : '已完成';
  const name = run.name || '子智能体';
  const role = run.roleName && run.roleName !== name ? `，岗位：${run.roleName}` : '';
  return [`${name}（${state}${role}）`, run.task].filter(Boolean).join('：');
}

const messageListRef = ref<HTMLElement | null>(null);
const copiedId = ref<number | null>(null);
const copiedPlanId = ref<number | null>(null);
// 只折叠执行区；最终总结在 execution-stream 之后，永远不受该状态影响。
const execCollapse = ref<Record<number, boolean>>({});
// 多选版 ask_user_choice 选择卡（params.multiple）的暂存勾选：按消息 id 存数组，点「确认选择」才提交
const askMultiSel = ref<Record<number, string[]>>({});
const planReviseDraft = reactive<Record<number, string>>({});
function onPlanReviseInput(id: number, event: Event) {
  const target = event.target as HTMLInputElement | null;
  planReviseDraft[id] = target?.value || '';
}
function submitPlanRevise(message: ChatMessage) {
  const text = String(planReviseDraft[message.id] || '').trim();
  if (!text || (props.loading && message.interactive)) return;
  planReviseDraft[message.id] = '';
  emit('resume', message.id, text);
}
// Codex 式执行卡折叠态（按消息 id）：未手动切过时，运行/等待中展开，完成且已出答案自动收起
const lightboxSrc = ref<string | null>(null);

const RESOURCE_ATTACHMENT_KINDS = new Set(['thread_ref', 'knowledge', 'skill', 'subagent', 'web']);
const DOC_VIEWER_EXTS = new Set(['pdf', 'doc', 'docx', 'ppt', 'pptx', 'md', 'markdown', 'html', 'htm']);
const CODE_VIEWER_EXTS = new Set([
  'txt', 'json', 'log', 'xml', 'yaml', 'yml', 'csv',
  'py', 'js', 'ts', 'css', 'sh', 'sql', 'vue', 'jsx', 'tsx',
]);

function attachmentExt(filename: string): string {
  return (filename.split('.').pop() || '').toLowerCase();
}

function isImageAttachment(att: AttachmentInfo): boolean {
  if (RESOURCE_ATTACHMENT_KINDS.has(att.kind || '')) return false;
  if (att.kind === 'image') return true;
  return fileKindOf(att.filename) === 'image';
}

function canOpenSentAttachment(att: AttachmentInfo): boolean {
  if (RESOURCE_ATTACHMENT_KINDS.has(att.kind || '')) return false;
  if (isImageAttachment(att)) return false;
  if (!att.fileId) return false;
  const ext = attachmentExt(att.filename);
  return DOC_VIEWER_EXTS.has(ext) || CODE_VIEWER_EXTS.has(ext)
    || ['pdf', 'docx', 'pptx'].includes(att.kind || '');
}

const attachmentViewerFile = ref<GeneratedFile | null>(null);
let attachmentViewerLoading: { (): void } | null = null;

function stopAttachmentViewerLoading() {
  attachmentViewerLoading?.();
  attachmentViewerLoading = null;
}

function openSentAttachment(att: AttachmentInfo) {
  if (!canOpenSentAttachment(att) || !att.fileId) return;
  const ext = attachmentExt(att.filename);
  const file: GeneratedFile = { id: att.fileId, filename: att.filename, size: 0 };
  if (DOC_VIEWER_EXTS.has(ext) || ['pdf', 'docx', 'pptx'].includes(att.kind || '')) {
    stopAttachmentViewerLoading();
    attachmentViewerLoading = antMessage.loading('正在打开预览…', 0);
    attachmentViewerFile.value = file;
    return;
  }
  emit('previewFile', file);
}

function closeAttachmentViewer() {
  stopAttachmentViewerLoading();
  attachmentViewerFile.value = null;
}

function onAttachmentViewerFailed(reason?: string) {
  stopAttachmentViewerLoading();
  attachmentViewerFile.value = null;
  // 有具体原因就展示具体原因（后端 detail / 格式不支持），没有再退回泛泛的「预览失败」
  antMessage.error(reason ? `预览失败：${reason}` : '预览失败');
}

function askChoiceOptions(message: ChatMessage) {
  return (message.interactive?.params?.userSelectOptions || [])
    .map((option: any) => ({
      value: String(option.value || option.label || ''),
      label: String(option.value || option.label || ''),
      description: String(option.description || ''),
    }))
    .filter((option: { value: string }) => option.value);
}

/** ask_user_choice 多题版直接适配成现有需求卡视图：分页、回退、自定义答案、
 *  跳过和一次性提交全部复用 QuestionCard，不维护第二套交互状态机。 */
function askQuestionsCard(message: ChatMessage): QuestionCardView {
  const questions = Array.isArray(message.interactive?.params?.questions)
    ? message.interactive?.params?.questions
    : [];
  return {
    runId: String(message.interactive?.run_id || message.runId || ''),
    resumeId: message.interactive?.resume_id,
    goal: String(message.interactive?.params?.description || ''),
    questions,
  };
}

// 引用来源：点击「引用来源（N）」打开右侧「搜索结果」抽屉，列出该消息的全部来源网页
// 计划卡默认是带底部渐隐的文稿预览（对齐研究报告白卡）；点进去后看全文。
const planReportViewer = ref<{ title: string; html: string; research?: boolean } | null>(null);
function openPlanReportViewer(message: ChatMessage) {
  planReportViewer.value = {
    title: planCardTitle(message),
    html: planCardBodyHtml(message),
  };
}

const sourcesOpen = ref(false);
const activeSources = ref<SourceItem[]>([]);
const editingId = ref<number | null>(null);
const editingText = ref('');
const editTextareaRef = ref<HTMLTextAreaElement | null>(null);
// 用户是否停留在底部：决定流式期间是否自动跟随滚动（滚上去看历史时不打扰）
const stickToBottom = ref(true);
// 两种阅读意图都必须压过贴底阈值：用户主动向上滚，以及生成中主动展开 Thought。
// Thought 用 Set 记录，避免同时展开多段时收起其中一段就错误恢复跟随。
let manualReadingPause = false;
const thoughtReadingPauseKeys = new Set<string>();
let lastObservedScroller: HTMLElement | null = null;
let lastObservedScrollTop = 0;
let scrollerEl: HTMLElement | null = null;
let pointerActive = false;

function startEdit(message: ChatMessage) {
  editingId.value = message.id;
  editingText.value = message.content || '';
  nextTick(() => {
    const el = editTextareaRef.value;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  });
}

function cancelEdit() {
  editingId.value = null;
  editingText.value = '';
}

function saveEdit(message: ChatMessage) {
  const text = editingText.value.trim();
  if (!text) return;
  emit('edit', message.id, text);
  editingId.value = null;
  editingText.value = '';
}

function openSources(message: ChatMessage) {
  activeSources.value = sourceCitations(message);
  sourcesOpen.value = true;
}

// 文本类来源（知识库/网页/文件）：来源抽屉与「引用来源（N）」计数用——
// 搜索附带图片（type=image）只服务正文 [图N] 图文混排，不算阅读来源
function sourceCitations(message: ChatMessage) {
  return (message.citations || []).filter((c) => c.type !== 'image');
}

// 带 URL 的网页来源（站点图标 /「参考了 N 个来源」抽屉）。
// 正文不再渲染行内 [N] 角标（2026-08-09）；顺序仍保持稳定。
function webPages(message: ChatMessage) {
  return (message.citations || []).filter((c) => !!c.url && c.type !== 'image');
}

/** 来源计数（2026-07-26 修复）：citations 是**搜索命中**（后端每次 search_web 无条件
 *  append results[:8]，多次搜索跨调用累加且不按 URL 去重），不是真正抓取过的页面——
 *  真读的页数是时间线里的「浏览 M 个页面」（后端 scraped_pages）。此前标题写「已阅读
 *  N 个网页」并直接用未去重的长度，于是同屏出现「搜索到 8 个网页 ×2 / 浏览 2 个页面 /
 *  已阅读 16 个网页」三个互相打架的数，而下方 favicon 又是按 host 去重的、视觉也对不上。
 *  这里按 URL 去重、文案改为「参考了 N 个来源」。 */
function webSourceCount(message: ChatMessage): number {
  const seen = new Set<string>();
  for (const c of webPages(message)) seen.add(String(c.url));
  return seen.size;
}

function hasArtifactImageContext(message: ChatMessage): boolean {
  return Boolean(
    visibleGeneratedFiles(message).length
    || (message.agentSteps || []).some((step) => step.kind === 'artifact'),
  );
}

// 搜索附带图片（type=image）：仅非产物问答可进正文。旧 PPT/Word 消息可能
// 已持久化 image citation，这里以交付步骤/文件卡做历史数据的防御性分流。
function messageImages(message: ChatMessage) {
  if (hasArtifactImageContext(message)) return [];
  // [图N] uses the persisted image order. Invalid URLs must not shift later indices.
  return (message.citations || []).filter((c) => c.type === 'image');
}

function faviconList(message: ChatMessage): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of webPages(message)) {
    try {
      const host = new URL(c.url as string).hostname;
      if (seen.has(host)) continue;
      seen.add(host);
      out.push(`https://${host}/favicon.ico`);
    } catch {
      /* 非法 URL 跳过 */
    }
    if (out.length >= 5) break;
  }
  return out;
}

// 灰色地球兜底：站点没有 /favicon.ico 时不露出裂图，换成一枚中性圆形图标
const FALLBACK_FAVICON =
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="#eef0f3">' +
      '<circle cx="12" cy="12" r="12"/>' +
      '<g fill="none" stroke="#9aa0ac" stroke-width="1.4">' +
      '<circle cx="12" cy="12" r="6.2"/><path d="M5.8 12h12.4M12 5.8c2 2 2 10.4 0 12.4M12 5.8c-2 2-2 10.4 0 12.4"/>' +
      '</g></svg>',
  );

function onFaviconError(e: Event) {
  const img = e.target as HTMLImageElement;
  if (img.dataset.fallback) return; // 已回落过就别再触发 error 循环
  img.dataset.fallback = '1';
  img.src = FALLBACK_FAVICON;
}

function formItems(interactive: any): any[] {
  const params = interactive?.params || {};
  const items = params.inputForm || params.userInputForms || params.list || [];
  return Array.isArray(items) ? items : [];
}

/** HITL 卡归属语境：标注「是谁在问」；ask_user_choice 是主助手自己提问，不标注。
 *
 *  优先用后端 input.required 直带的 subagent_name（2026-07-26）：
 *  - `@` 模式整场会话就是子智能体、不发 subagent chip 事件，chip 反推恒为空 → 此前无标注；
 *  - 刷新回放时 chip 的 running 会被归一成 completed，反推只能「取最后一个」猜。
 *  旧事件没有该字段时回落到原来的 chip 反推，保证历史消息不退化。 */
function isPlanConfirmation(message: ChatMessage): boolean {
  const interactive = message.interactive;
  if (!interactive) return false;
  return interactive.kind === 'plan_confirmation'
    || Boolean(interactive.revision_gate)
    || Boolean(interactive.plan_steps?.length);
}

function planCardMarkdown(message: ChatMessage): string {
  return buildPlanCardMarkdown(message);
}

function planCardTitle(message: ChatMessage): string {
  const report = planCardMarkdown(message);
  const heading = report.match(/^\s{0,3}#{1,3}\s+(.+)$/m);
  const title = String(heading?.[1] || '').replace(/[*_`]/g, '').trim();
  return title || '执行计划';
}

function planCardBodyHtml(message: ChatMessage): string {
  const markdown = planCardMarkdown(message);
  if (!markdown) return '';
  const body = stripLeadingTitleHeadings(markdown, planCardTitle(message));
  return body ? renderMarkdown(body) : '';
}

/** 计划还在生成：不出灰条占位，整份报告齐了再整卡跳入。 */
function isPlanReportPending(message: ChatMessage): boolean {
  if (String(message.agentMode || '') !== 'plan') return false;
  if (isPlanConfirmation(message)) return false;
  if (message.planExecutionUnlocked) return false;
  return isExecutionRunning(message);
}

function hasReadyPlanDocument(message: ChatMessage): boolean {
  if (isPlanReportPending(message)) return false;
  // 方式会在用户确认后回到 standard，但已持久化的 planReport 仍是
  // 这一轮的权威计划文稿。是否可以画卡统一交给 buildPlanCardMarkdown：
  // 它会拒绝标准模式的种子 taskPlan，只放行真实计划报告/确认卡。
  return Boolean(planCardMarkdown(message));
}

function interactiveSourceHint(message: ChatMessage): string {
  if (!message.interactive || message.interactive.ask_user) return '';
  const direct = message.interactive.subagent_name;
  if (direct) return `来自子智能体「${direct}」`;
  const calls = message.subagentCalls || [];
  const active = [...calls].reverse().find((c) => c.status === 'running') || calls[calls.length - 1];
  return active?.name ? `来自子智能体「${active.name}」` : '';
}

mermaid.initialize({
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'strict',
  // 渲染失败时不要注入“语法错误炸弹”图（否则流式期间会不断堆到页面底部）
  suppressErrorRendering: true,
});
let mermaidSeq = 0;
// src → 已渲染 SVG 的进程内缓存。核心作用：助手正文是 v-html（响应式），renderMarkdown
// 又按内容缓存、永远吐出「空占位块」，因此消息任何一次重绘（引用角标/配图到达、流结束后
// 被服务端持久化版本替换等）都会用空占位覆盖命令式塞进去的 SVG。有了本缓存，重绘后的空
// 占位可**同步**从缓存复原，无需重新 parse/render，避免闪回空框。cap 后按最旧淘汰。
const mermaidSvgCache = new Map<string, string>();
const MERMAID_SVG_CACHE_MAX = 200;
// 卡片交互态（页签/缩放）按 src 记忆，重绘重建卡片时回填——否则一次响应式重绘会把
// 用户切到的「代码」页签或缩放级别打回默认，观感突兀。
type MermaidCardState = { tab: 'diagram' | 'code'; scale: number };
const mermaidCardState = new Map<string, MermaidCardState>();
// 全屏预览的 SVG（Vue 掌管的 Teleport 浮层，非 v-html 孤岛内，交互稳）
const mermaidFullscreenSvg = ref<string | null>(null);
const MERMAID_SCALE_MIN = 0.5;
const MERMAID_SCALE_MAX = 3;
const MERMAID_SCALE_STEP = 0.25;
// 方案一自适应的默认最大高度（与 CSS 变量 --mermaid-fit-max-h 保持一致）。
const MERMAID_FIT_MAX_H = 440;
// 工具栏图标（描边跟随 currentColor，contained 在卡头右侧）
const MM_ICON_ZOOM_OUT =
  '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/><path d="M13.5 13.5 17 17M6.5 9h5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const MM_ICON_ZOOM_IN =
  '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.6"/><path d="M13.5 13.5 17 17M6.5 9h5M9 6.5v5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const MM_ICON_DOWNLOAD =
  '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M10 3v9m0 0 3.2-3.2M10 12 6.8 8.8M4 15h12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const MM_ICON_FULLSCREEN =
  '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="M4 7.5V4h3.5M16 7.5V4h-3.5M4 12.5V16h3.5M16 12.5V16h-3.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/** 按当前缩放级别调整卡内 SVG 尺寸。
 *  scale=1：清除内联覆盖，交还给卡片 CSS（方案一：mermaid width:100% + 限高自适应）。
 *  放大/缩小：以「当前展示宽」为基准换算——横图基准=容器可用宽（铺满态），竖图基准=限高换算宽
 *  (最大高度 × 宽高比)，取两者较小值即 scale=1 时实际画出来的宽；再乘 scale，解除限高与宽度约束，
 *  超出容器时在 .mermaid-view 内滚动查看。基准每次实时量取（容器宽随窗口变），不再缓存。 */
function applyMermaidScale(card: HTMLElement, scale: number) {
  card.dataset.scale = String(scale);
  const svg = card.querySelector('.mermaid-diagram svg') as SVGSVGElement | null;
  if (!svg) return;
  if (scale === 1) {
    svg.style.maxWidth = '';
    svg.style.maxHeight = '';
    svg.style.width = '';
    svg.style.height = '';
    return;
  }
  const vb = svg.viewBox?.baseVal;
  const ratio = vb && vb.height ? vb.width / vb.height : 0;
  const view = card.querySelector('.mermaid-view') as HTMLElement | null;
  const avail = view ? Math.max(0, view.clientWidth - 32) : 0; // 减去 .mermaid-view 左右各 16 内边距
  const base = ratio && avail ? Math.min(avail, MERMAID_FIT_MAX_H * ratio) : avail;
  if (!base) return;
  // 放大态解除限高与宽度约束，让图随缩放整体增大
  svg.style.maxWidth = 'none';
  svg.style.maxHeight = 'none';
  svg.style.height = 'auto';
  svg.style.width = `${Math.round(base * scale)}px`;
}

/** 把卡片当前页签/缩放写回 src→state（交互后持久化，供重绘重建时回填）。 */
function saveMermaidState(card: HTMLElement) {
  const src = decodeURIComponent(card.getAttribute('data-mermaid-src') || '');
  if (!src) return;
  const activeTab =
    ((card.querySelector('.mermaid-tab.is-active') as HTMLElement | null)?.dataset.mmtab as
      | 'diagram'
      | 'code') || 'diagram';
  mermaidCardState.set(src, {
    tab: activeTab,
    scale: Number(card.dataset.scale || '1') || 1,
  });
}

/** 切换「图表/代码」页签。 */
function setMermaidTab(card: HTMLElement, tab: 'diagram' | 'code') {
  card
    .querySelectorAll<HTMLElement>('.mermaid-tab')
    .forEach((b) => b.classList.toggle('is-active', b.dataset.mmtab === tab));
  const diagram = card.querySelector('.mermaid-diagram') as HTMLElement | null;
  const code = card.querySelector('.mermaid-code') as HTMLElement | null;
  if (diagram) diagram.hidden = tab !== 'diagram';
  if (code) code.hidden = tab !== 'code';
  saveMermaidState(card);
}

/** 用已渲染 SVG + 源码构建带页签/工具栏的卡片（截图同款）。src 与 SVG 均可信：
 *  源码走 textContent 防注入，SVG 是 mermaid 自产（securityLevel:strict 已净化输入）。 */
function buildMermaidCard(src: string, svg: string): HTMLElement {
  const state = mermaidCardState.get(src) || { tab: 'diagram', scale: 1 };
  const card = document.createElement('div');
  card.className = 'mermaid-card';
  card.setAttribute('data-mermaid-src', encodeURIComponent(src));
  card.dataset.scale = String(state.scale);

  const head = document.createElement('div');
  head.className = 'mermaid-head';
  head.innerHTML =
    '<div class="mermaid-tabs">' +
    `<button type="button" class="mermaid-tab${state.tab === 'diagram' ? ' is-active' : ''}" data-mmtab="diagram">图表</button>` +
    `<button type="button" class="mermaid-tab${state.tab === 'code' ? ' is-active' : ''}" data-mmtab="code">代码</button>` +
    '</div>' +
    '<div class="mermaid-tools">' +
    `<button type="button" class="mermaid-tool mermaid-zoom-out" title="缩小">${MM_ICON_ZOOM_OUT}</button>` +
    `<button type="button" class="mermaid-tool mermaid-zoom-in" title="放大">${MM_ICON_ZOOM_IN}</button>` +
    `<button type="button" class="mermaid-tool mermaid-download" title="下载 SVG">${MM_ICON_DOWNLOAD}<span>下载</span></button>` +
    `<button type="button" class="mermaid-tool mermaid-fullscreen" title="全屏">${MM_ICON_FULLSCREEN}<span>全屏</span></button>` +
    '</div>';

  const view = document.createElement('div');
  view.className = 'mermaid-view';
  const diagram = document.createElement('div');
  diagram.className = 'mermaid-diagram';
  diagram.innerHTML = svg;
  diagram.hidden = state.tab === 'code';
  const codePre = document.createElement('pre');
  codePre.className = 'mermaid-code';
  const codeEl = document.createElement('code');
  codeEl.textContent = src;
  codePre.appendChild(codeEl);
  codePre.hidden = state.tab !== 'code';
  view.appendChild(diagram);
  view.appendChild(codePre);

  card.appendChild(head);
  card.appendChild(view);
  if (state.scale !== 1) applyMermaidScale(card, state.scale);
  return card;
}

// 全屏浮层的 Esc 关闭：仅在打开时挂 document 监听，关闭即摘（不常驻）。
function onOverlayEsc(e: KeyboardEvent) {
  if (e.key !== 'Escape') return;
  if (planReportViewer.value) {
    planReportViewer.value = null;
    return;
  }
  mermaidFullscreenSvg.value = null;
}
watch([mermaidFullscreenSvg, planReportViewer], () => {
  const open = Boolean(mermaidFullscreenSvg.value || planReportViewer.value);
  if (open) document.addEventListener('keydown', onOverlayEsc);
  else document.removeEventListener('keydown', onOverlayEsc);
});

// mermaid.render 失败/测量时会把临时节点（含错误图）直接挂到 <body>，未清理会堆在页面左下角。
function cleanupOrphanMermaid() {
  document
    .querySelectorAll(
      'body > [id^="dmermaid-svg-"], body > [id^="mermaid-svg-"], body > svg[aria-roledescription="error"]',
    )
    .forEach((el) => el.remove());
}

/**
 * Render `\`\`\`mermaid` blocks (emitted as `.mermaid-block[data-src]` placeholders)
 * into SVG. Runs after each content update; incomplete diagrams (mid-stream) throw
 * and fall back to showing the source text, then render once the block completes.
 */
// 流式期间 messages 深度 watch 每个 token 触发一次；用重入闸避免多个异步渲染并发抓到
// 同一个 data-src 块重复 render（浪费 + 闪烁）。**但重入时不能直接丢弃**：若丢弃的那次
// 恰是「重绘后补渲染」，而进行中的那次填的又是已被重绘替换掉的旧节点（detached），
// 新的空占位就再没人渲染 → 永久空框（线上「流程图显示不出来」的真因，已浏览器复现）。
// 故改为：忙时标脏，当前这轮收尾后按脏位再扫一遍，直到无脏。
let mermaidRendering = false;
let mermaidDirty = false;
async function renderMermaid() {
  const root = messageListRef.value;
  if (!root) return;
  if (mermaidRendering) {
    mermaidDirty = true; // 忙时不丢，改为标记「渲染完再扫一遍」
    return;
  }
  mermaidRendering = true;
  try {
    do {
      mermaidDirty = false;
      // 每轮重新查询「未渲染」占位（重绘会把已渲染块打回带 data-src 的空占位）
      const blocks = root.querySelectorAll<HTMLElement>('pre.mermaid-block[data-src]');
      for (const block of Array.from(blocks)) {
        const src = decodeURIComponent(block.getAttribute('data-src') || '');
        // 空图（```mermaid 无内容）没有可渲染物：移除占位，别让「正在绘制」空壳留到收尾后
        if (!src.trim()) {
          block.remove();
          continue;
        }
        // 快路径：已渲染过的图，重绘打回空占位时同步重建卡片，不再 parse/render
        const cached = mermaidSvgCache.get(src);
        if (cached) {
          block.replaceWith(buildMermaidCard(src, cached));
          continue;
        }
        let svg: string | null = null;
        // parse 与 render 全程 try/catch：懒加载分块失败等会让 parse **reject 抛异常**，
        // 若不接住会穿透整个循环、占位块既无 SVG 也无源码 → 空框。接住后统一回退为源码。
        try {
          // 先校验语法：流式未完成或非法的图直接显示源码，绝不进入 render，
          // 从根上避免 mermaid 往 <body> 注入错误“炸弹”图。
          const valid = await mermaid.parse(src, { suppressErrors: true });
          if (!valid) {
            block.textContent = src;
            continue;
          }
          mermaidSeq += 1;
          const id = `mermaid-svg-${mermaidSeq}`;
          try {
            ({ svg } = await mermaid.render(id, src));
          } finally {
            // 清理 mermaid 渲染时挂到 <body> 的临时/错误节点
            document.getElementById('d' + id)?.remove();
            document.getElementById(id)?.remove();
          }
        } catch {
          block.textContent = src; // parse/render 任一阶段抛错：回退源码，绝不留空框
          continue;
        }
        if (svg) {
          mermaidSvgCache.set(src, svg);
          while (mermaidSvgCache.size > MERMAID_SVG_CACHE_MAX) {
            const oldest = mermaidSvgCache.keys().next().value;
            if (oldest === undefined) break;
            mermaidSvgCache.delete(oldest);
          }
          // 替换成卡片；若渲染期间该节点已被重绘 detach，replaceWith 对无父节点是空操作，
          // 但 SVG 已入缓存 + 重绘那次已把 mermaidDirty 置真 → do/while 再扫一遍会命中缓存补上活节点。
          block.replaceWith(buildMermaidCard(src, svg));
        }
      }
      cleanupOrphanMermaid();
    } while (mermaidDirty);
  } finally {
    mermaidRendering = false;
  }
}

async function copyMessage(message: ChatMessage) {
  // 用户消息只复制 content；助手消息把开场白与终答拼在一起（用户看到的完整文字）。
  // copyText 含 http/权限失败兜底；失败必须有可见反馈，禁止静默。
  const text =
    message.role === 'user'
      ? String(message.content || '')
      : [message.preamble, message.content].filter(Boolean).join('\n\n');
  const ok = await copyText(text);
  if (!ok) {
    antMessage.warning('复制失败，请手动选择文本后复制');
    return;
  }
  copiedId.value = message.id;
  window.setTimeout(() => {
    if (copiedId.value === message.id) copiedId.value = null;
  }, 1500);
}

async function copyPlanReport(message: ChatMessage) {
  const text = planCardMarkdown(message);
  if (!text) {
    antMessage.warning('计划还没准备好');
    return;
  }
  const ok = await copyText(text);
  if (!ok) {
    antMessage.warning('复制失败，请手动选择文本后复制');
    return;
  }
  copiedPlanId.value = message.id;
  window.setTimeout(() => {
    if (copiedPlanId.value === message.id) copiedPlanId.value = null;
  }, 1500);
}

const markdownParser = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(str: string, lang: string): string {
    if (lang === 'mermaid') {
      // Placeholder holding the raw source; rendered to SVG post-hoc (see renderMermaid).
      return `<pre class="mermaid-block" data-src="${encodeURIComponent(str)}"></pre>`;
    }
    if (lang === 'artifact-html') {
      // 完整 HTML 产物占位：data-src 存源码，renderArtifacts 事后升级为「源码框 + 产物卡片」。
      // 内嵌转义源码——流式期间 renderArtifacts 被刻意跳过（防抖），占位若为空会露出一个空白
      // 边框（用户反馈的「中间空气泡」）；填上源码则退化成源码框，收尾再升级为卡片。
      return `<pre class="artifact-html-block" data-src="${encodeURIComponent(str)}"><code>${markdownParser.utils.escapeHtml(str)}</code></pre>`;
    }
    if (lang === 'artifact-streaming') {
      // 流式产物源码（未闭合围栏）：直接展示正在编写的 HTML 源码——限高 + 黏底
      // （样式见 artifact-streaming-block），围栏闭合后经 artifact-html 升级为产物卡片。
      return `<pre class="artifact-streaming-block"><code>${markdownParser.utils.escapeHtml(str)}</code></pre>`;
    }
    // 兜底：完整 HTML 文档（出现 </html>）即便模型没用 :::artifact、只丢了个 ```html，也当产物卡片。
    // 未完成时不拦截——落到下面普通高亮，实时显示正在生成的源码（用户要看到代码在写，像豆包）。
    if ((lang === 'html' || lang === '') && /<\/html\s*>/i.test(str) && /<!doctype html|<html[\s>]/i.test(str)) {
      return `<pre class="artifact-html-block" data-src="${encodeURIComponent(str)}"><code>${markdownParser.utils.escapeHtml(str)}</code></pre>`;
    }
    if (lang && hljs.getLanguage(lang)) {
      try {
        const highlighted = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value;
        return `<pre class="hljs"><code class="language-${lang}">${highlighted}</code></pre>`;
      } catch {
        // Fall through to escaped plain text on highlight failure.
      }
    }
    return `<pre class="hljs"><code>${markdownParser.utils.escapeHtml(str)}</code></pre>`;
  },
});
// 裸文本不自动成链（fuzzyLink）：「xxx.md」这类文件名会被当成 .md（摩尔多瓦）域名点开跳外网。
// 无协议裸域名（含 www. 开头）一并不成链——别用 linkify.add('www.','//') 找补，它输出相对
// href 会跳到 当前域/www.xxx。带协议 URL / 显式 markdown 链接照常；引用角标是 xss 后注入的不经过这里。
markdownParser.linkify.set({ fuzzyLink: false });
stopProtocolLinkAtCjkPunctuation(markdownParser);
markdownParser.enable('table');
markdownParser.use(mdKatex, { throwOnError: false, errorColor: '#c0392b' });

/**
 * XSS filter whitelist extended for KaTeX (HTML + MathML) and highlight.js
 * output. `html: false` on markdown-it already escapes raw user HTML, so the
 * rendered tree is trusted structure; this only re-permits the presentational
 * tags/attributes those two libraries emit. `style` values are still passed
 * through xss's built-in CSS sanitizer.
 */
const mathmlTags = [
  'math', 'semantics', 'annotation', 'mrow', 'mi', 'mo', 'mn', 'ms', 'mtext',
  'mspace', 'msup', 'msub', 'msubsup', 'mfrac', 'msqrt', 'mroot', 'munder',
  'mover', 'munderover', 'mtable', 'mtr', 'mtd', 'mlabeledtr', 'mstyle',
  'mpadded', 'mphantom', 'menclose',
];
const defaultWhiteList = getDefaultWhiteList();
const richWhiteList: Record<string, string[]> = {
  ...defaultWhiteList,
  a: [...defaultWhiteList.a, 'rel'],
  span: ['class', 'style', 'aria-hidden'],
  code: ['class'],
  pre: ['class', 'data-src'],
  svg: ['xmlns', 'width', 'height', 'viewbox', 'preserveaspectratio', 'style', 'class'],
  path: ['d', 'style', 'class'],
  line: ['x1', 'y1', 'x2', 'y2', 'style', 'class', 'stroke-width'],
};
for (const tag of mathmlTags) {
  richWhiteList[tag] = ['mathvariant', 'encoding', 'display', 'class', 'style', 'width', 'accent', 'stretchy'];
}
const richXss = new FilterXSS({ whiteList: richWhiteList });

// 一条消息是否有可渲染内容——决定是否进列表，也用于判断独立“正在思考”块要不要显示。
// 空正文的助手消息不能一律过滤：消歧/HITL/审批/推荐/工具步骤等都挂在消息上，且后端
// clarification 等事件本就不带正文——按 content 过滤会让整个交互流程「凭空消失」。
// 内部沙箱脚本泄漏防护已抽到 utils/internalScript.ts（可单测，jest 链路不吃 .vue）：
// 判据 2026-07-28 收窄——`/workspace/files/` 是面向用户的路径，不再算内部标记。

// 文本工具协议泄漏（DSML / tool_calls 等）：弱模型偶发把工具调用协议当正文吐出
// （如 `<｜｜DSML｜｜tool_calls>…`），后端源头闸已拦新消息；历史里的旧消息在此再兜
// 一层——从协议标记处截到结尾（与后端 text_protocol_guard 同义）。竖线数量/半全角都收。
const PROTOCOL_LEAK_RE = /<[|｜]+(?:DSML|tool)|<\/?tool_call>/;
function stripProtocolLeak(content: string): string {
  const idx = content.search(PROTOCOL_LEAK_RE);
  return idx >= 0 ? content.slice(0, idx).trim() : content;
}

// 公开过程/终答的最后一道前端兜底：压缩续接偶尔把 thinking 标签当普通文本发出，
// 工具失败回执则可能只剩一行 [stderr]。这些内容不属于用户可读动作，不能穿透到气泡。
const PUBLIC_RUNTIME_NOISE_LINE_RE = /^\s*(?:\[?\s*(?:stdout|stderr|std\s*out|std\s*err|output|result)\s*\]?|exit[_ -]?code\s*[=:：]?\s*-?\d+|(?:node|python3?|npm|pnpm|yarn)\s+v?\d+(?:\.\d+){1,3}(?:[-+][\w.-]+)?|\/workspace\/|command not found|npm:\s*command not found)\s*$/i;
const PUBLIC_RUNTIME_NOISE_FRAGMENT_RE = /(?:\/workspace\/|open-kimi-ppt|PPTD\s+manifest\s+must\s+contain|command\s+not\s+found)/i;
function stripPublicRuntimeNoise(content: string): string {
  let value = String(content || '')
    .replace(/<thinking\b[^>]*>[\s\S]*?<\/thinking\s*>/gi, '')
    .replace(/<thinking\b[^>]*>[\s\S]*$/gi, '');
  value = value
    .split(/\r?\n/)
    .map((line) => PUBLIC_RUNTIME_NOISE_LINE_RE.test(line) || PUBLIC_RUNTIME_NOISE_FRAGMENT_RE.test(line) ? '' : line)
    .join('\n');
  return value.replace(/\n{3,}/g, '\n\n').trim();
}

/** 无正文终态轮的历史锚点（ensure_terminal_anchor）。行必须在，执行卡才挂得上；
 *  文案不是回答，不能占终答气泡。只认整段等于占位句，避免误吞真回复。 */
function isTerminalAnchorPlaceholder(text: string): boolean {
  const t = String(text || '').trim();
  return t === '（已停止，未生成回复）' || t === '（已停止, 未生成回复）';
}

/** 正文渲染前的统一净化：剥任务阶段引导文案 → 截断文本工具协议泄漏 → 剥内部脚本围栏。
 *  结构整形在 renderMarkdown 内统一做（覆盖正文/preamble/planReport/叙述）。 */
function sanitizeAssistantBody(content: string): string {
  if (isTerminalAnchorPlaceholder(content)) return '';
  const cleaned = stripPublicRuntimeNoise(stripInternalScriptBlocks(stripProtocolLeak(content)));
  if (isTerminalAnchorPlaceholder(cleaned)) return '';
  // 兼容已落库的旧消息：工具轮中的 schema/脚本自纠、改计划独白曾被误存为 assistant 正文。
  // 新消息由后端事件边界拦截；这里仅做展示层兜底，不能让历史会话继续把内部约束露给用户。
  if (/(?:补充|明确|更正).{0,16}(?:契约|格式|schema|JSON)|(?:image[-_ ]?map\.json|spec\.json).{0,160}(?:必须|应当|需要|格式)|(?:不要|禁止).{0,24}(?:读|读取|cat|head|find|ls).{0,24}(?:脚本|技能目录|SKILL\.md)|(?:计划需要调整|让我先修正计划|修正计划结构)|requires\s*[:=]\s*\[|步骤\s*\d+[^\n]{0,80}\((?:investigation|productive|verify|export)\)|step-\s*\d+[^\n]{0,80}requires/is.test(cleaned)) {
    return '';
  }
  return cleaned;
}

/** 工具期纯过程残段（「先建立研究计划…」）：不当正文展示，避免假终答卡住计量行。 */
function isProcessResidueBody(content: string): boolean {
  const clean = sanitizeAssistantBody(content).trim();
  if (!clean || clean.length > 140) return false;
  // 有数字/链接/结论结构的更像真答，不拦
  if (/\d{2,}|https?:\/\/|结论|建议|来源|℃|%/.test(clean)) return false;
  return /^(?:我来|让我来|我先|接下来|先建立|先制定|先梳理|先做好|然后多角度|稍等)[\s\S]{0,120}$/.test(
    clean,
  );
}

function hasVisibleAssistantBody(message: ChatMessage): boolean {
  if (message.role !== 'assistant' || !message.content) return false;
  if (shouldHoldPlanStreamOffBody(message) && !isPlanConfirmation(message)) return false;
  const body = sanitizeAssistantBody(message.content);
  if (!body) return false;
  const report = String(message.planReport || '').trim();
  if (report && planReportOwnsBody(report, body)) return false;
  // 运行中过程语不得占正文位（与 peelCommentary 双保险）
  if (isExecutionRunning(message) && isProcessResidueBody(body)) return false;
  return true;
}

function isSystemInitialProgressPreambleText(text: string | null | undefined): boolean {
  const clean = String(text || '').trim();
  if (!clean) return false;
  return (
    clean === '正在处理…' ||
    clean === '正在处理...' ||
    clean === '仍在处理，请稍候…' ||
    clean === '正在检索可核验的资料…' ||
    clean === '正在查看你选中的知识库资料…' ||
    clean === '正在阅读你提供的材料…' ||
    clean === '正在梳理目标和现有条件…' ||
    /^正在(处理|检索|查看|阅读|梳理).{0,24}$/.test(clean)
  );
}

function hasVisiblePreamble(message: ChatMessage): boolean {
  if (!message.preamble || !message.preamble.trim()) return false;
  // 系统首帧占位（正在处理…/正在检索…）永不作为过程正文展示。
  // 统一口径：运行中只出「正在思考 Xs」，模型真实 commentary 才是「接下来干什么」。
  // 历史回放可能丢了 preambleIsInitialProgress，但文案仍是系统占位——按文案也隐藏。
  if (message.preambleIsInitialProgress || isSystemInitialProgressPreambleText(message.preamble)) {
    return false;
  }
  if (commentaryRepeatsFinalAnswer(message.preamble, message.content || '')) return false;
  return true;
}

/** 过程直接摊开：思考、公开说明和真实动作自己就是进度，不再包一层完成态标题。 */
function hasExecutionStreamBody(message: ChatMessage): boolean {
  return hasVisiblePreamble(message) || executionRows(message).length > 0;
}

/** 仅首帧确认时不包装“执行过程”标题，避免普通问答看起来像启动了一个任务。 */
/** 对话层硬类型 → CSS 修饰类（缺省不额外加类，兼容旧消息）。 */
function narrativeClass(message: ChatMessage): string {
  const k = message.narrativeKind;
  if (k === 'ack') return 'narrative-ack';
  if (k === 'commentary') return 'narrative-commentary';
  if (k === 'final') return 'narrative-final';
  if (k === 'system_note') return 'narrative-system-note';
  // 初始进度 preamble 视为 system_note
  if (message.preambleIsInitialProgress) return 'narrative-system-note';
  return '';
}

function showStandalonePreamble(message: ChatMessage): boolean {
  return Boolean(
    message.role === 'assistant' &&
      message.preambleIsInitialProgress &&
      hasVisiblePreamble(message) &&
      !(message.agentSteps?.length || message.toolSteps?.length || message.subagentCalls?.length) &&
      !message.taskPlan?.length &&
      !isExecutionWaiting(message),
  );
}

function isRenderable(message: ChatMessage): boolean {
  // POST 发出到 run.started 首帧之间，助手占位消息还没有正文、runId 或时间戳。
  // 不能把它过滤掉：鉴权/建 Run/研究预检稍慢时页面会留下整块空白，看起来像死机。
  const isActivePlaceholder = Boolean(
    props.loading &&
      message.role === 'assistant' &&
      props.messages[props.messages.length - 1] === message,
  );
  return (
    message.role === 'user' ||
    isActivePlaceholder ||
    hasVisibleAssistantBody(message) ||
    hasVisiblePreamble(message) ||
    Boolean(
        message.error ||
        message.interactive ||
        message.clarification?.length ||
        message.approval ||
        message.externalRecs?.length ||
        message.toolSteps?.length ||
        message.subagentCalls?.length ||
        message.routedAgent ||
        message.runStartedAt
    )
  );
}

const visibleMessages = computed(() => {
  return props.messages.filter(isRenderable);
});

// ===== 右侧对话导航：每个用户提问一个锚点，可跳转 + 跟随高亮 =====
const userTurns = computed(() => visibleMessages.value.filter((m) => m.role === 'user'));
const activeTurnId = ref<number | null>(null);
const detectedArtifacts = ref<Artifact[]>([]);

function turnLabel(turn: ChatMessage) {
  const t = (turn.content || '').replace(/\s+/g, ' ').trim();
  return t.length > 26 ? `${t.slice(0, 26)}…` : t || '（空提问）';
}

function scrollToMessage(id: number) {
  const el = messageListRef.value?.querySelector<HTMLElement>(`[data-mid="${id}"]`);
  if (!el) return false;
  const reduce = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
  activeTurnId.value = id;
  el.classList.add('message-flash');
  window.setTimeout(() => el.classList.remove('message-flash'), 1200);
  return true;
}

/** 跟随滚动：把视口上方判定线之上、最靠下的那轮用户提问标为“当前”。 */
function updateActiveTurn() {
  const scroller = getScroller();
  const root = messageListRef.value;
  if (!scroller || !root) return;
  const line = scroller.getBoundingClientRect().top + 110;
  let current: number | null = null;
  root.querySelectorAll<HTMLElement>('.message.user[data-mid]').forEach((el) => {
    if (el.getBoundingClientRect().top <= line) current = Number(el.dataset.mid);
  });
  if (current != null) activeTurnId.value = current;
}

/** 给代码块套一个豆包式头栏（语言名 + 复制），并浅色化（幂等）。 */
function enhanceComparisonTables() {
  const root = messageListRef.value;
  if (!root) return;
  root.querySelectorAll('.message-bubble table td, .message-bubble table th').forEach((cell) => {
    const raw = (cell.textContent || '').trim();
    if (!raw) return;
    if (raw === '✓' || raw === '✔' || raw === 'yes' || raw === 'Yes' || raw === 'true') {
      cell.classList.add('tbl-yes');
      cell.classList.remove('tbl-no');
    } else if (raw === '—' || raw === '–' || raw === '-' || raw === 'no' || raw === 'No' || raw === 'false') {
      cell.classList.add('tbl-no');
      cell.classList.remove('tbl-yes');
    }
  });
}

function enhanceCodeBlocks() {
  const root = messageListRef.value;
  if (!root) return;
  root.querySelectorAll<HTMLElement>('pre.hljs:not([data-copy])').forEach((pre) => {
    pre.setAttribute('data-copy', '1');
    const code = pre.querySelector('code');
    const langClass = Array.from(code?.classList || []).find((c) => c.startsWith('language-'));
    const lang = langClass ? langClass.replace('language-', '') : 'text';
    const wrap = document.createElement('div');
    wrap.className = 'code-block';
    const head = document.createElement('div');
    head.className = 'code-block-head';
    head.innerHTML =
      '<span class="code-lang"></span>' +
      '<span class="code-actions">' +
      '<button type="button" class="code-copy-btn">复制</button>' +
      '<button type="button" class="code-download-btn">下载</button>' +
      '</span>';
    const langEl = head.querySelector('.code-lang');
    if (langEl) langEl.textContent = lang; // textContent 防注入
    pre.parentNode?.insertBefore(wrap, pre);
    wrap.appendChild(head);
    wrap.appendChild(pre);
  });
}

// 语言 → 下载文件扩展名（未知语言回退 .txt）
const CODE_EXT: Record<string, string> = {
  html: 'html', xml: 'xml', svg: 'svg', vue: 'vue', javascript: 'js', js: 'js',
  typescript: 'ts', ts: 'ts', jsx: 'jsx', tsx: 'tsx', python: 'py', py: 'py',
  java: 'java', kotlin: 'kt', json: 'json', css: 'css', scss: 'scss', less: 'less',
  bash: 'sh', shell: 'sh', sh: 'sh', powershell: 'ps1', sql: 'sql', go: 'go',
  rust: 'rs', c: 'c', cpp: 'cpp', 'c++': 'cpp', csharp: 'cs', cs: 'cs', php: 'php',
  ruby: 'rb', swift: 'swift', dart: 'dart', yaml: 'yml', yml: 'yml', toml: 'toml',
  ini: 'ini', dockerfile: 'Dockerfile', markdown: 'md', md: 'md', text: 'txt',
};

function codeFilename(lang: string): string {
  const ext = CODE_EXT[(lang || '').trim().toLowerCase()] || 'txt';
  return ext === 'Dockerfile' ? 'Dockerfile' : `code.${ext}`;
}

// 生成内容下载：内容转 Blob 后用临时 <a download> 触发，兼容 http 非安全上下文。
function downloadText(filename: string, text: string, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 图片失败保留来源和重试；error/load 不冒泡，使用根节点捕获监听。 */
function onMediaError(e: Event) {
  setChatImageLoadState(e, true);
}

function onMediaLoad(e: Event) {
  setChatImageLoadState(e, false);
}

/** 事件委托：点击产物卡片 → 右侧面板打开；点击复制/下载 → 复制或下载源码。 */
function onListClick(e: MouseEvent) {
  const target = e.target as HTMLElement;
  const retry = target.closest?.('.msg-image-retry') as HTMLElement | null;
  if (retry) {
    retryChatImage(retry);
    return;
  }
  const imageButton = target.closest?.('.msg-image-open');
  if (imageButton) {
    const image = imageButton.querySelector('img');
    if (image) lightboxSrc.value = image.src;
    return;
  }
  // 正文图片卡：点图放大（复用附件灯箱）；点脚注链接则正常跳来源页
  if (target?.tagName === 'IMG' && target.closest?.('.msg-figure')) {
    lightboxSrc.value = (target as HTMLImageElement).src;
    return;
  }
  // ===== mermaid 卡片工具栏（事件委托，卡片在 v-html 孤岛内、重绘会重建，故不逐元素绑监听）=====
  const mmTab = target?.closest?.('.mermaid-tab') as HTMLElement | null;
  if (mmTab) {
    const mc = mmTab.closest('.mermaid-card') as HTMLElement | null;
    if (mc) setMermaidTab(mc, (mmTab.dataset.mmtab as 'diagram' | 'code') || 'diagram');
    return;
  }
  const mmZoom = target?.closest?.('.mermaid-zoom-in, .mermaid-zoom-out') as HTMLElement | null;
  if (mmZoom) {
    const mc = mmZoom.closest('.mermaid-card') as HTMLElement | null;
    if (mc) {
      const dir = mmZoom.classList.contains('mermaid-zoom-in') ? 1 : -1;
      const cur = Number(mc.dataset.scale || '1') || 1;
      const next = Math.min(
        MERMAID_SCALE_MAX,
        Math.max(MERMAID_SCALE_MIN, +(cur + dir * MERMAID_SCALE_STEP).toFixed(2)),
      );
      applyMermaidScale(mc, next);
      saveMermaidState(mc);
    }
    return;
  }
  const mmDl = target?.closest?.('.mermaid-download') as HTMLElement | null;
  if (mmDl) {
    const mc = mmDl.closest('.mermaid-card') as HTMLElement | null;
    const src = decodeURIComponent(mc?.getAttribute('data-mermaid-src') || '');
    const svg = mermaidSvgCache.get(src) || mc?.querySelector('.mermaid-diagram')?.innerHTML || '';
    if (svg) downloadText('diagram.svg', svg, 'image/svg+xml');
    return;
  }
  const mmFs = target?.closest?.('.mermaid-fullscreen') as HTMLElement | null;
  if (mmFs) {
    const mc = mmFs.closest('.mermaid-card') as HTMLElement | null;
    const src = decodeURIComponent(mc?.getAttribute('data-mermaid-src') || '');
    mermaidFullscreenSvg.value =
      mermaidSvgCache.get(src) || mc?.querySelector('.mermaid-diagram')?.innerHTML || '';
    return;
  }
  const card = target?.closest?.('.artifact-card') as HTMLElement | null;
  if (card) {
    const id = card.dataset.artifactId || '';
    const art = detectedArtifacts.value.find((a) => a.id === id);
    if (art) emit('openArtifact', art);
    return;
  }
  // 下载按钮：取同块源码，按语言命名后下载
  const dlBtn = target?.closest?.('.code-download-btn') as HTMLElement | null;
  if (dlBtn) {
    const block = dlBtn.closest('.code-block') as (HTMLElement & { __html?: string }) | null;
    // HTML 产物块：用内存里挂着的原始完整源码（与产物区 iframe 同一份），
    // 绝不从高亮后的 DOM 取 textContent——那会把 hljs 拆行/转义混进去，下载后本地打开样式全坏。
    const rawHtml = block?.__html;
    if (rawHtml) {
      downloadText('index.html', rawHtml);
    } else {
      const text = block?.querySelector('pre code')?.textContent || '';
      if (!text) return;
      const lang = block?.querySelector('.code-lang')?.textContent || 'txt';
      downloadText(codeFilename(lang), text);
    }
    dlBtn.textContent = '已下载';
    window.setTimeout(() => {
      dlBtn.textContent = '下载';
    }, 1500);
    return;
  }
  const btn = target?.closest?.('.code-copy-btn') as HTMLElement | null;
  if (!btn) return;
  const code = btn.closest('.code-block')?.querySelector('pre code') || btn.closest('pre')?.querySelector('code');
  const text = code?.textContent || '';
  if (!text) return;
  // 阻止委托层与其它冒泡路径抢事件；copyText 含 http/权限失败兜底
  e.preventDefault();
  e.stopPropagation();
  copyText(text).then((ok) => {
    if (!ok) {
      antMessage.warning('复制失败，请手动选择代码后复制');
      return;
    }
    btn.textContent = '已复制';
    window.setTimeout(() => {
      btn.textContent = '复制';
    }, 1500);
  });
}

// 最后一条助手消息 id：只有它显示「重新生成」按钮（重答的是最新一轮回复）
const lastAssistantId = computed(() => {
  for (let i = props.messages.length - 1; i >= 0; i -= 1) {
    if (props.messages[i].role === 'assistant') return props.messages[i].id;
  }
  return null;
});

// ===== 统一执行时间线（服务端时间锚点 + 本地平滑刷新）=====
const nowTick = ref(Date.now());
let liveTimer: ReturnType<typeof setInterval> | null = null;

watch(
  () => props.loading,
  (running) => {
    if (running) {
      nowTick.value = Date.now();
      if (!liveTimer) liveTimer = setInterval(() => { nowTick.value = Date.now(); }, 250);
    } else if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  if (liveTimer) clearInterval(liveTimer);
});

// 已完成/失败/停止的 Run 不能继续展示旧审批卡：历史回放可能保留 approval.required，
// 但同一轮后来已经完成时，那个操作早已没有可执行价值，不能让用户误点批准。
function hasPendingApproval(message: ChatMessage): boolean {
  return Boolean(
    message.approval
    && !message.runCompletedAt
    && !message.runFailed
    && !message.runCancelled,
  );
}

// 等待用户输入的交互态（HITL/消歧/审批）：不是「在跑」，执行卡显示等待标题、隐藏跳动计时
function isExecutionWaiting(message: ChatMessage): boolean {
  return Boolean(message.interactive || message.clarification?.length || hasPendingApproval(message));
}

// 流式期间常驻最后一条助手消息（等待用户输入的交互卡阶段除外——那不是「在跑」）
function isExecutionRunning(message: ChatMessage): boolean {
  return (
    Boolean(props.loading) &&
    message.role === 'assistant' &&
    message.id === lastAssistantId.value &&
    !message.runCompletedAt &&
    !message.runPartial &&
    !message.runFailed &&
    !message.runCancelled &&
    !isExecutionWaiting(message) &&
    !message.externalRecs?.length
  );
}

// ===== 整轮一个执行头（Codex 对齐 2026-07-26） =====
// 运行中插话会把一轮切成多个 assistant 分段（executionSegmentIndex 递增），Codex 的形态是
// **整轮只有一个** 「已处理 Xm Xs」 头：头挂首段、状态取末段，后续分段只出内容不出头。
// 这样插话气泡天然落在两段之间——展开＝原位交错回放，折叠＝气泡外提到折叠头与最终回答之间。
/** 是否为插话切出的后续分段（实时=split 递增；刷新回放=execution_trace.segments 下标） */
function isSegmentContinuation(message: ChatMessage): boolean {
  return message.role === 'assistant' && Number(message.executionSegmentIndex || 0) > 0;
}

const runSegmentAnchors = computed(() => {
  const head = new Map<string, ChatMessage>();
  const tail = new Map<string, ChatMessage>();
  for (const message of props.messages) {
    if (message.role !== 'assistant' || !message.runId) continue;
    if (isSegmentContinuation(message)) tail.set(message.runId, message);
    else if (!head.has(message.runId)) head.set(message.runId, message);
  }
  return { head, tail };
});

/** 执行头的状态源：首段的头代表整轮，运行/失败/时长一律读本轮末段。
 *  只对**确实是本轮首段**的消息改写——同一 runId 下还可能挂着别的 assistant 消息
 *  （如暂停后「正在从上次进度继续」的占位），那些既不是首段也不是插话分段，读自己就好。 */
function researchTeamSnapshot(message: ChatMessage): ResearchTeamSnapshot | undefined {
  if (!isResearchTurn(message) || isSegmentContinuation(message)) return undefined;
  return message.researchProgress?.team;
}

function researchTeamPanels(message: ChatMessage): ResearchTeamSnapshot[] {
  const team = researchTeamSnapshot(message);
  return team ? [team] : [];
}

function hasResearchTeamPanel(message: ChatMessage): boolean {
  return Boolean(researchTeamSnapshot(message));
}

function researchTeamSettled(message: ChatMessage): boolean {
  const state = execHeadState(message);
  return Boolean(state.runCompletedAt || state.runCancelled || state.runFailed || state.runPartial);
}

/** 执行头的状态源：首段的头代表整轮，运行/失败/时长一律读本轮末段。
 *  只对**确实是本轮首段**的消息改写——同一 runId 下还可能挂着别的 assistant 消息
 *  （如暂停后「正在从上次进度继续」的占位），那些既不是首段也不是插话分段，读自己就好。 */
function execHeadState(message: ChatMessage): ChatMessage {
  if (message.role !== 'assistant' || !message.runId || isSegmentContinuation(message)) return message;
  if (runSegmentAnchors.value.head.get(message.runId) !== message) return message;
  return runSegmentAnchors.value.tail.get(message.runId) || message;
}

function execHeadRunning(message: ChatMessage): boolean {
  return isExecutionRunning(execHeadState(message));
}

function execCollapseAnchor(message: ChatMessage): ChatMessage {
  if (message.role !== 'assistant' || !message.runId || !isSegmentContinuation(message)) return message;
  return runSegmentAnchors.value.head.get(message.runId) || message;
}

/** 同一 Run 只在首段渲染一条整轮状态线，避免插话分段重复报时。 */
function showExecHead(message: ChatMessage): boolean {
  if (isSegmentContinuation(message)) return false;
  const state = execHeadState(message);
  return Boolean(
    execHeadRunning(message)
    || isExecutionWaiting(state)
    || execHeadDurationMs(message) > 0
    || hasVisiblePreamble(message)
    || executionRows(message).length > 0
    || (state !== message && executionRows(state).length > 0)
    || hasVisibleAssistantBody(message)
    || (state !== message && hasVisibleAssistantBody(state)),
  );
}

function execHeadCanCollapse(message: ChatMessage): boolean {
  const state = execHeadState(message);
  if (isExecutionRunning(state) || isExecutionWaiting(state)) return false;
  return Boolean(
    hasVisiblePreamble(message)
    || executionRows(message).length > 0
    || (state !== message && executionRows(state).length > 0),
  );
}

function isExecCollapsed(message: ChatMessage): boolean {
  const anchor = execCollapseAnchor(message);
  if (!execHeadCanCollapse(anchor)) return false;
  const manual = execCollapse.value[anchor.id];
  if (manual !== undefined) return manual;
  const state = execHeadState(anchor);
  if (state.runCancelled) return false;
  return Boolean(state.executionCollapsed);
}

function toggleExecCollapse(message: ChatMessage): void {
  const anchor = execCollapseAnchor(message);
  if (!execHeadCanCollapse(anchor)) return;
  execCollapse.value = {
    ...execCollapse.value,
    [anchor.id]: !isExecCollapsed(anchor),
  };
}

// 有真实过程事件才展示过程区；普通回复不生成额外状态条。
function showExecutionTrace(message: ChatMessage): boolean {
  if (message.role !== 'assistant') return false;
  if (showStandalonePreamble(message)) return false;
  // 首帧确认会随历史轨迹持久化；若本轮最终只有一段普通回答，刷新后不能把这句确认
  // 误投影成「执行过程」。真正的动作、任务状态和等待态仍照常进入过程流。
  const hasProcess = Boolean(
    (hasVisiblePreamble(message) && !hasVisibleAssistantBody(message)) ||
      message.agentSteps?.length ||
      message.toolSteps?.length ||
      message.subagentCalls?.length ||
      showTransientReasoningSummary(message) ||
      loadedSkillNames(message).length ||
      message.taskPlan?.length ||
      message.planReport,
  );
  if (hasProcess) return true;
  // HITL 提问/消歧/审批卡挂在执行时间线内（P0 统一面板）：等待态即使还没有任何步骤也要出容器
  if (isExecutionWaiting(message)) return true;
  // 运行中从首帧就出「正在思考 Xs」；终态以持久化的时长保留「已运行」。
  return isExecutionRunning(message)
    || execHeadDurationMs(message) > 0
    || hasVisibleAssistantBody(message);
}

function isThinkingActive(message: ChatMessage, stepIndex: number): boolean {
  const steps = message.agentSteps || [];
  const step = steps[stepIndex];
  if (!step || step.kind !== 'thinking') return false;
  if (step.status === 'completed') return false;
  return isExecutionRunning(message) && stepIndex === steps.length - 1;
}

function thinkingStepText(step: AgentStep): string {
  if (step.kind !== 'thinking') return '';
  return String(step.text || '').replace(/\r/g, '');
}

function thinkingStepSeconds(message: ChatMessage, step: AgentStep, stepIndex: number): number | undefined {
  if (step.kind !== 'thinking') return undefined;
  if (typeof step.seconds === 'number' && step.seconds > 0) return step.seconds;
  if (isThinkingActive(message, stepIndex)) {
    const start = step.startedAt || message.runStartedAt;
    if (!start) return 0;
    return Math.max(0, (nowTick.value - start) / 1000);
  }
  // 已收束却没带 seconds（工具开始后才到的 completed 帧）：至少 1s，避免标题退化成光秃 Thoughts
  return 1;
}

const thoughtOpen = ref<Record<string, boolean>>({});

function thoughtKey(message: ChatMessage, stepIndex: number): string {
  return `${message.id}:${stepIndex}`;
}

function isThoughtOpen(message: ChatMessage, stepIndex: number): boolean {
  const key = thoughtKey(message, stepIndex);
  if (Object.prototype.hasOwnProperty.call(thoughtOpen.value, key)) {
    return thoughtOpen.value[key];
  }
  // 进行中 / 结束后都默认收起：只留 shimmer「Thinking」或「Thoughts for Ns」，
  // 用户点标题才展开灰色思考正文。
  return false;
}

function toggleThought(message: ChatMessage, stepIndex: number) {
  const key = thoughtKey(message, stepIndex);
  const opening = !isThoughtOpen(message, stepIndex);
  thoughtOpen.value = {
    ...thoughtOpen.value,
    [key]: opening,
  };
  if (shouldPauseAutoFollowForThought(opening, Boolean(props.loading))) {
    thoughtReadingPauseKeys.add(key);
    stickToBottom.value = false;
    emit('scrollState', false);
    return;
  }
  if (!opening && thoughtReadingPauseKeys.delete(key) && !hasAutoFollowPause()) {
    scrollToBottom(true);
  }
}

function transientReasoningText(message: ChatMessage): string {
  return String(message.reasoningSummary || '').trim();
}

function showInitialProgressReasoning(message: ChatMessage): boolean {
  return Boolean(
    transientReasoningText(message)
    && !(message.agentSteps || []).some((step) => step.kind === 'tool' || step.kind === 'subagent')
    && !message.toolSteps?.length
    && !message.subagentCalls?.length,
  );
}

/** 只在当前活跃连接、且还没有 Thought 步骤时显示旧尾窗。 */
function showTransientReasoningSummary(message: ChatMessage): boolean {
  if ((message.agentSteps || []).some((step) => step.kind === 'thinking')) return false;
  return Boolean(
    transientReasoningText(message)
    && isExecutionRunning(message)
    && !hasVisibleAssistantBody(message),
  );
}

/** 首轮最多显示 6 句；执行后只露最近 3 句，末句随 token 实时增长。 */
function reasoningSummaryLines(message: ChatMessage): string[] {
  const raw = transientReasoningText(message)
    .replace(/\r/g, '')
    .replace(/^[#>*\-\d.\s]+/gm, '')
    .trim();
  if (!raw) return [];
  const chunks = raw
    .split(/\n+/)
    .flatMap((paragraph) => paragraph.match(/[^。！？!?]+[。！？!?]?/g) || [paragraph])
    .map((part) => part.trim())
    .filter(Boolean);
  const limit = showInitialProgressReasoning(message) ? 6 : 3;
  return chunks.slice(-limit).map((part) => (part.length > 180 ? `${part.slice(0, 179)}…` : part));
}

// ===== 附件读取降级（P0 附件生命周期） =====
function attachmentIssueText(message: ChatMessage): string {
  return (message.attachmentIssues || [])
    .map((issue) => `《${issue.filename}》（${issue.note || (issue.status === 'partial' ? '内容截断' : '读取失败')}）`)
    .join('、');
}

// 产物血缘行已移除（2026-07-15 用户拍板：卡片上「由沙箱脚本生成·查看执行步骤」一行不要）——
// 执行过程本就在时间线里，卡片只留 名称/大小/审查徽标/操作，信息不重复。

/** 服务端步骤按顺序展示；网页正文和 URL 不在主时间线铺开。 */
function normalizeLoadedSkillName(value: unknown): string {
  return String(value || '')
    .trim()
    .replace(/^skill:/i, '')
    .replace(/^技能[《<「]?/, '')
    .replace(/[》>」]$/, '')
    .trim();
}

/** capability.loaded 与旧版 use_skill 回执的统一展示名；顺序按事实首次到达保留。 */
function loadedSkillNames(message: ChatMessage): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  const add = (value: unknown) => {
    const name = normalizeLoadedSkillName(value);
    if (name && !seen.has(name)) {
      seen.add(name);
      names.push(name);
    }
  };
  for (const value of message.loadedCapabilities || []) add(value);
  for (const step of message.agentSteps || []) {
    if (step.kind === 'note') {
      const match = /^已加载能力[：:]\s*(.+)$/u.exec(String(step.text || '').trim());
      if (match) {
        for (const value of match[1].split(/[、,，]/u)) add(value);
      }
    } else if (step.kind === 'tool' && step.name === 'use_skill' && step.status === 'completed') {
      add(skillPillName(step));
    }
  }
  return names;
}

function isLoadedCapabilityNote(step: AgentStep): step is Extract<AgentStep, { kind: 'note' }> {
  return step.kind === 'note' && /^已加载能力[：:]/u.test(String(step.text || '').trim());
}

function loadedSkillNamesFromStep(step: Extract<AgentStep, { kind: 'note' }>): string[] {
  const match = /^已加载能力[：:]\s*(.+)$/u.exec(String(step.text || '').trim());
  if (!match) return [];
  return match[1].split(/[、,，]/u).map(normalizeLoadedSkillName).filter(Boolean);
}

function visibleAgentSteps(message: ChatMessage): AgentStep[] {
  // commentary 是后端已确认的公开过程讲解：必须和工具动作按原始事件顺序交错保留。
  // 只丢掉清洗后为空的历史脏数据；内部协议文本已在后端 is_user_visible_commentary
  // 过滤，不能再把所有 note 一刀切掉，否则会出现“黑字闪一下随后消失”。
  return (message.agentSteps || []).filter((step) => {
    if (step.kind !== 'note') return true;
    if (isLoadedCapabilityNote(step)) return loadedSkillNamesFromStep(step).length > 0;
    const visible = Boolean(cleanNarration(String(step.text || '')));
    return visible && !commentaryRepeatsFinalAnswer(String(step.text || ''), message.content || '');
  });
}

// 执行行按结构指纹缓存（照 renderCache 的模式，2026-07-28）。
// 生成期间 nowTick 每 250ms 跳一次 → 整个渲染函数每秒重跑 4 次，而模板里 v-if 与 v-for
// 各调一次 executionRows，于是 buildExecutionRows→collapseSubagentRows→dedupeRepeatedNotes
// →collapseSandboxRuns 这条链对**全部历史消息**每秒各跑 8 遍——那些行一个字都不会变。
// 指纹只收会改变行结构的字段（execRowsSignature 有详细理由）：命中就复用同一个数组，
// label/耗时/favicon 这类展示字段照旧由模板直接读 row.step.x，实时性不受影响。
type ActivityExecutionRow = Extract<ExecutionRow, { type: 'step' }>;
const execRowsCache = new Map<number, { key: string; rows: ActivityExecutionRow[] }>();

/** 主对话只显示活动轨迹；语义计划仍由 taskPlan 供顶栏「任务协作」面板使用。 */
function executionRows(message: ChatMessage): ActivityExecutionRow[] {
  const running = isExecutionRunning(message);
  const openGroups = Object.keys(runGroupOpen.value).filter(
    (key) => runGroupOpen.value[key] && key.startsWith(`${message.id}:rg:`),
  );
  const key = execRowsSignature({
    agentSteps: message.agentSteps,
    subCollabExpanded: message.subCollabExpanded,
  }, running, openGroups);
  const cached = execRowsCache.get(message.id);
  if (cached && cached.key === key) return cached.rows;
  const rows = collapseSandboxRuns(
    dedupeRepeatedNotes(collapseSubagentRows(message, buildExecutionRows(undefined, visibleAgentSteps(message), running))),
    String(message.id),
    runGroupOpen.value,
  ).filter((row): row is ActivityExecutionRow => row.type === 'step');
  execRowsCache.set(message.id, { key, rows });
  return rows;
}

// 缓存按**存活消息**回收，不设条数上限：renderCache 那种「超了就丢最早一条」在这里会退化——
// 渲染是从头到尾遍历消息的，超上限后每渲染一条就顶掉下一条，命中率直接归零。
// 一条消息的行只是一层壳（step 仍是原对象），常驻内存可忽略；换会话/删消息时清干净即可。
// 数组换引用（切会话）与就地增删（追加/重新生成）都要收：只盯 length 会漏掉等长替换，
// 只盯引用会漏掉 push。
watch(
  [() => props.messages, () => props.messages.length],
  () => {
    if (!execRowsCache.size) return;
    const alive = new Set(props.messages.map((m) => m.id));
    for (const id of [...execRowsCache.keys()]) {
      if (!alive.has(id)) execRowsCache.delete(id);
    }
  },
);

// 归拢组展开态：按 消息id:rg:首步下标 记忆（步骤只追加，首步下标稳定）
const runGroupOpen = ref<Record<string, boolean>>({});
function toggleRunGroup(groupKey: string) {
  runGroupOpen.value = { ...runGroupOpen.value, [groupKey]: !runGroupOpen.value[groupKey] };
}

/** 叙述复读降噪：相邻 commentary 在重试循环里可能复读，信息量为零时只保留第一条。 */
function dedupeRepeatedNotes(rows: ExecutionRow[]): ExecutionRow[] {
  const out: ExecutionRow[] = [];
  let lastNote = '';
  for (const row of rows) {
    const step = row.type === 'plan' ? null : (row as { step?: { kind?: string; text?: string } }).step;
    if (step?.kind === 'note') {
      const norm = String(step.text || '').replace(/\s+/g, '');
      if (norm && norm === lastNote) continue;
      lastNote = norm;
    }
    out.push(row);
  }
  return out;
}

/** V3 批次4 多子智能体聚合（2026-07-23 拍板）：同一消息内出现 ≥2 个不同子智能体时，
 *  注入「正在与 N 个智能体协作」聚合头；未展开时各子智能体行收进聚合头之后，点击展开
 *  才逐个显示。单子智能体保持原样（普通对话/@ 模式铁律下永远单个，不受影响）。
 *  身份判据必须是 step.name：label 会随过程推进改写，
 *  同一个子智能体被调用两次时（铁律②允许多次调用）两行 label 不同，用 label 去重会
 *  把它误算成 2 个，凭空冒出「正在与 2 个智能体协作」。 */
function collapseSubagentRows(message: ChatMessage, rows: ExecutionRow[]): ExecutionRow[] {
  const names = new Set<string>();
  let running = false;
  for (const row of rows) {
    if (row.type === 'step' && row.step.kind === 'subagent') {
      names.add(String(row.step.name || ''));
      if (row.step.status === 'running') running = true;
    }
  }
  if (names.size < 2) return rows;
  const expanded = Boolean(message.subCollabExpanded);
  const out: ExecutionRow[] = [];
  let injected = false;
  for (const row of rows) {
    const isSub = row.type === 'step' && row.step.kind === 'subagent';
    if (isSub && !injected) {
      out.push({
        type: 'step',
        stepIndex: row.stepIndex,
        nested: row.nested,
        step: { kind: 'subagentGroup', count: names.size, running, expanded },
      });
      injected = true;
    }
    if (isSub && !expanded) continue;
    out.push(row);
  }
  return out;
}

function toggleSubCollab(message: ChatMessage): void {
  message.subCollabExpanded = !message.subCollabExpanded;
}

/** 同消息图片附件计数（与 AttachmentCard.isImage 同判据：kind=image 或图片扩展名）：
 *  >1 时传 uniform 让多图等大瓦片展示（用户拍板「大小要一致」） */
function imageAttachmentCount(attachments?: Array<{ filename: string; kind?: string }>): number {
  return (attachments || []).filter((a) => {
    if (a.kind === 'image') return true;
    return fileKindOf(a.filename || '') === 'image';
  }).length;
}

// Codex 式工具详情面板：展开态按 消息id:步骤下标 记忆（切换消息/刷新即收起，默认折叠）
const shellOpen = ref<Record<string, boolean>>({});
const shellCopied = ref<Record<string, boolean>>({});

function hasShellPanel(step: AgentStep): boolean {
  if (step.kind !== 'tool') return false;
  if (step.name === 'bash') return Boolean(step.command || visibleStepOutput(step));
  // 2026-07-29：browser_* 失败也要有「看看到底什么错」的入口。
  // ⚠️ 下面这句「原先」指的是**本次改动前的工作区状态**（当时已被收窄成只认 bash），
  // 不是 HEAD。HEAD（07-27）里是 `return Boolean(step.command || step.preview || step.error)`
  // ——所有工具都给面板。写清基线是因为一句没标锚点的「原先」当场就是错的史实，
  // 而注释里的断言没有任何测试会告诉你它错了（下面那条 07-24 拍板正是这么被作废的）。
  // 于是浏览器系失败时整行是个死胡同——没有失败摘要（那一行的 v-if 挂在
  // hasShellPanel 上）、没有箭头、点不开任何东西。而后端 ToolSoftError 的文案恰恰是**写给人看的**
  // （「这一步没做成：<detail>，元素可能已经变了」「这个地址连不上……」），全都存在 step.error 里，
  // 只是从来没有一处渲染它。成功态**故意**不给面板（沿用既有产品选择：抓回来的网页正文、
  // 页面元素快照动辄上千字，铺进主对话只有噪音；页面长什么样已由行下方的截图承担）。
  if (step.status === 'failed' && step.name.startsWith('browser_')) {
    return Boolean(visibleStepOutput(step));
  }
  return false;
}

/** 展开执行详情时仍保留可读结果，但不把旧轨迹里的沙箱/PPT 运行时回执带回页面。 */
function visibleStepOutput(step: Extract<AgentStep, { kind: 'tool' }>): string {
  return stripPublicRuntimeNoise(String(step.preview || step.error || ''));
}

/** 技能名气泡（2026-07-24 V1 方案）：从 use_skill 的 target「技能《x》」里取名；
 *  取不到（如启用失败没到 meta）返回空串，行回落到通用文本/错误展示 */
function skillPillName(step: Extract<AgentStep, { kind: 'tool' }>): string {
  if (step.name !== 'use_skill') return '';
  const matched = /《(.+?)》/.exec(step.target || '');
  return matched ? matched[1] : '';
}

/** 面板段落复制：轻反馈（按钮文案 1.5s 变「已复制」），copyText 自带非安全上下文兜底 */
function copyShell(key: string, text?: string) {
  if (!text) return;
  copyText(text).then((ok) => {
    if (!ok) {
      antMessage.warning('复制失败，请手动选择文本后复制');
      return;
    }
    shellCopied.value = { ...shellCopied.value, [key]: true };
    window.setTimeout(() => {
      shellCopied.value = { ...shellCopied.value, [key]: false };
    }, 1500);
  });
}

/** 输出段是否失败态（收据原则：失败才上色）：工具终态 failed，或工具协议层成功
    但命令本身非零退出（回执首行 exit_code=N）——用户截图里正是这种。 */
function shellOutputFailed(step: AgentStep): boolean {
  if (step.kind !== 'tool') return false;
  if (step.status === 'failed') return true;
  return /(^|\n)exit_code=(?!0\b)\d+/.test(step.preview || '');
}

/** 本轮是否有足以降噪终态错误的真实交付：正文和可见产物必须同时存在。
 *  单独一段“总结通道中断”兜底正文不是交付，不能掩盖 run.failed；文件仍与产物卡共用判据。 */
function hasDelivered(message: ChatMessage): boolean {
  return Boolean(String(message.content || '').trim() && visibleGeneratedFiles(message).length > 0);
}

// ===== 生成态计量行（点阵动画 + 实时阶段文案，不显示 token） =====

/** 交付卡只展示真正的交付物：
 *  ① .slides.json 是编辑器的内部编辑源（2026-07-21 用户拍板），不作为文件卡露出——
 *     数据保留在 generatedFiles 里供 slidesSiblingOf 配对、「编辑」按钮使用；
 *  ② 只留文档类产物（word/pdf/excel/ppt/md/html/图片，2026-07-27 用户拍板）：模型为了
 *     做出这份产物写的 build.py 之类的生成脚本照常落库，但它不是交给用户的东西，
 *     出成卡片只会让真正的 pptx 混在里面找不到。判据见 composables/deliverable.ts；
 *  ③ Research 正文只交付上方蓝框报告，历史自动保存的 HTML 不再重复出通用文件卡。 */
function visibleGeneratedFiles(message: ChatMessage): GeneratedFile[] {
  return visibleDeliverables(message.generatedFiles).filter((file) => !isResearchReportFile(file));
}

// 幻灯片手改（2026-07-21）：pptx 产物带同名 .slides.json 编辑源时，产物查看器本身就是编辑器
// （不再另开一个「编辑幻灯片」页面），保存后回灌对话重新编译
function slidesSiblingOf(message: ChatMessage, file: GeneratedFile): GeneratedFile | null {
  if (!/\.pptx?$/i.test(file.filename)) return null;
  const want = `${file.filename.replace(/\.pptx?$/i, '')}.slides.json`;
  const own = (message.generatedFiles || []).find((f) => f.filename === want);
  if (own) return own;
  // 同名编辑源是「整段对话级」的产物：后续轮次只重编译 pptx（改配色/加动画）时不会再吐一份
  // slides.json，本轮找不到就往前找最近的一份——否则新版本的卡片会莫名不能编辑
  for (let i = props.messages.length - 1; i >= 0; i--) {
    const hit = (props.messages[i].generatedFiles || []).find((f) => f.filename === want);
    if (hit) return hit;
  }
  return null;
}

function onSlidesSave(
  message: ChatMessage,
  file: GeneratedFile,
  pages: string[],
  done: (ok: boolean) => void,
) {
  const sib = slidesSiblingOf(message, file);
  if (sib) emit('saveSlides', sib, file, pages, done);
  else done(false); // 找不到编辑源＝没保存，查看器保持打开（手改不丢）
}

// 逐页产物直播卡（2026-07-20）：默认跟随最新到达的一页；用户点箭头后固定所选页
// （生成继续也不跳走），翻回最后一页自动恢复跟随——与消息流滚动跟随同一套心智。
const apPos = ref<Record<number, number | undefined>>({});

function apPages(message: ChatMessage) {
  return message.artifactPages || [];
}

function apCur(message: ChatMessage) {
  const pages = apPages(message);
  if (!pages.length) return null;
  const pos = apPos.value[message.id];
  return pages[pos != null && pos >= 0 && pos < pages.length ? pos : pages.length - 1];
}

function apNav(message: ChatMessage, dir: number) {
  const pages = apPages(message);
  if (!pages.length) return;
  const curPos = apPos.value[message.id] ?? pages.length - 1;
  const next = Math.min(pages.length - 1, Math.max(0, curPos + dir));
  apPos.value = { ...apPos.value, [message.id]: next === pages.length - 1 ? undefined : next };
}

function apCanPrev(message: ChatMessage): boolean {
  const pages = apPages(message);
  return (apPos.value[message.id] ?? pages.length - 1) > 0;
}

function apCanNext(message: ChatMessage): boolean {
  const pos = apPos.value[message.id];
  return pos != null && pos < apPages(message).length - 1;
}

function apTotal(message: ChatMessage): number {
  const pages = apPages(message);
  const last = pages.length ? pages[pages.length - 1] : null;
  return Math.max(last?.total || 0, pages.length);
}

// 1280×720 固定画布页在小卡里等比缩放：sandbox 禁脚本,用 container query + tan(atan2())
// 纯 CSS 算比例(Chrome 111+);不支持的浏览器退化为显示左上角,不阻断
const _APC_FIT_STYLE =
  '<style>html,body{margin:0;overflow:hidden;width:100%;height:100%}'
  + 'body{container-type:inline-size}'
  + 'body>*{transform-origin:0 0;transform:scale(tan(atan2(100cqw,1280px)))}</style>';

function apSrcdoc(html: string): string {
  if (!html) return html;
  return html.includes('</head>')
    ? html.replace('</head>', `${_APC_FIT_STYLE}</head>`)
    : _APC_FIT_STYLE + html;
}

function svgDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** 方案A 素文叙述的防御性清洗（2026-07-23 用户选定）：narrator 源头已过滤实现细节，
 *  这里兜底清理仍可能混进叙述的原始输出痕迹（分隔墙/运行回执头/标题层级/表格碎片/
 *  stdout 带出的表情符号），保证叙述永远是干净的正文段落。 */
function cleanNarration(text: string): string {
  let t = stripPublicRuntimeNoise(String(text || ''));
  if (/(?:计划需要调整|让我先修正计划|修正计划结构)|requires\s*[:=]\s*\[|步骤\s*\d+[^\n]{0,80}\((?:investigation|productive|verify|export)\)/i.test(t)) {
    return '';
  }
  // 行级：独占一行的分隔墙 / 标题 / 表格行
  t = t.replace(/^\s*(?:={4,}|-{6,}|_{6,})\s*$/gm, '');
  t = t.replace(/^\s*#{1,6}\s+/gm, '');
  t = t.replace(/^\s*\|.*\|\s*$/gm, '');
  // 行内级（2026-07-23 二轮反馈「内容还有很多杂质」）：stdout 搬进叙述时这些
  // 痕迹常嵌在句子中间，行首锚定的清洗抓不到
  t = t.replace(/exit_code\s*=\s*\d+\s*(?:stdout|stderr)?\s*[:：]?/gi, '');
  t = t.replace(/[=＝]{3,}/g, ' ');
  t = t.replace(/\|?\s*[-:：]{3,}\s*(?:\|\s*[-:：]*\s*)+/g, ' ');
  t = t.replace(/(?:\|[^\n|]{0,30}){2,}\|?/g, '，');
  t = t.replace(/(^|\s)#{1,6}(?=\s|[\u4e00-\u9fff])/g, '$1');
  t = t.replace(/[\u2705\u274c\u2714\ufe0f\u{1F4CA}\u{1F3AF}\u{1F4A1}\u{1F4C8}\u{1F4C9}\u{1F680}]/gu, '');
  // 收尾：清洗残留的连续空白与标点
  t = t.replace(/[ \t]{2,}/g, ' ');
  t = t.replace(/，{2,}/g, '，');
  t = t.replace(/，\s*。/g, '。');
  t = t.replace(/。\s*。/g, '。');
  t = t.replace(/\n{3,}/g, '\n\n');
  return t.trim();
}

function execHeadTitle(message: ChatMessage): string {
  const state = execHeadState(message);
  if (execHeadRunning(message)) return '本轮处理中';
  if (isExecutionWaiting(state)) return '本轮等待回应';
  if (state.runCancelled || isSensitiveWordRejection(state)) return '本轮已停止';
  if (state.runFailed) return '本轮未完成';
  if (state.runPartial) {
    if (isResearchTurn(message) || isResearchTurn(state)) return '本轮研究已结束';
    return '本轮部分完成';
  }
  return '本轮已完成';
}

function execHeadTerminalClass(message: ChatMessage): string {
  const state = execHeadState(message);
  if (execHeadRunning(message) || isExecutionWaiting(state)) return '';
  if (state.runCancelled || isSensitiveWordRejection(state)) return 'terminal-stopped';
  if (state.runFailed) return 'terminal-failed';
  if (state.runPartial) return 'terminal-partial';
  return '';
}

/** New API 的旧版事件只有公开文案，没有结构化错误码；保留前缀判定以兼容混合版本。 */
function isSensitiveWordRejection(message: ChatMessage): boolean {
  return /^请求触发了网关敏感词策略/u.test(String(message.error || '').trim());
}

function errorNoticeTitle(message: ChatMessage): string {
  return isSensitiveWordRejection(message) ? '触发了敏感词规则' : '本轮未能完成';
}

function errorNoticeDetail(message: ChatMessage): string {
  if (isSensitiveWordRejection(message)) {
    return '模型未生成内容。请修改输入后重试；如认为是误判，请联系管理员检查网关配置。';
  }
  return String(message.error || '').trim();
}

function execHeadTimeText(message: ChatMessage): string {
  if (execHeadRunning(message)) {
    const state = execHeadState(message);
    // 头挂在首段，所以优先使用首段起点，不能在插话后从 0 重计。
    const startedAt = message.runStartedAt || state.runStartedAt;
    return startedAt ? formatDuration(Math.max(0, nowTick.value - startedAt), true) : '';
  }
  const duration = execHeadDurationMs(message);
  const state = execHeadState(message);
  return duration > 0 || Boolean(state.runCompletedAt || state.runDurationMs != null)
    ? formatDuration(duration)
    : '';
}

function generationStatusText(message: ChatMessage): string {
  const ctx = {
    now: nowTick.value,
    hasAssistantBody: hasVisibleAssistantBody(message),
    isPlanConfirmation: isPlanConfirmation(message),
    hasGeneratedFiles: visibleGeneratedFiles(message).length > 0,
  };
  return props.meterCopy === 'interview'
    ? interviewGenerationMeterStatus(message, ctx)
    : generationMeterStatus(message, ctx);
}

/** 头部终态时长按**整轮**算：插话把一轮切成多段后，末段的 runDurationMs 只覆盖它自己那截
 *  （回放路径尤其如此），要用首段起点补上前半截，否则「工作过程 · 3s」严重少报。 */
function execHeadDurationMs(message: ChatMessage): number {
  const tail = execHeadState(message);
  const measuredDuration = (item: ChatMessage) =>
    item.runDurationMs
    || (item.runStartedAt && item.runCompletedAt
      ? Math.max(0, item.runCompletedAt - item.runStartedAt)
      : 0);
  const tailDuration = measuredDuration(tail);
  if (tail === message) return tailDuration;
  if (message.runStartedAt && tail.runStartedAt && tail.runStartedAt > message.runStartedAt) {
    return tail.runStartedAt - message.runStartedAt + tailDuration;
  }
  return tailDuration || measuredDuration(message);
}

// ── 执行流升级（2026-07-21）：intent 标题 / 思考落定句 / 呼吸点唯一 / 阶段文案 / 谢幕对账 ──

// 工具行标题：优先模型现写的 intent（任务语言一句话）；进行中有阶段进度时让位给
// 实时进度 label（「正在阅读 X」「正在生成第 3/8 页」滚动），落定后回到 intent
function looksLikeRawToolName(text?: string): boolean {
  // 模型有时把工具原名写进 intent/label（fetch_tool_result），直接摊到时间线会很廉价。
  return !!text && /^[a-z][a-z0-9_]{2,}$/.test(text.trim());
}

function isProgressPhrase(text?: string): boolean {
  return Boolean(text && /^正在/.test(String(text).trim()));
}

function toolRowTitle(step: Extract<AgentStep, { kind: 'tool' }>, live?: boolean): string {
  if (step.name === 'bash') return bashRowTitle(step);
  // 2026-07-29：这里原先有一条 `startsWith('browser_')` 的硬编码，把四个浏览器工具
  // 一律说成「查阅网页」/「正在查阅网页」——既不分 fetch/open/act/close，也不分成功与失败。
  // 而 executionTimeline.ts 的 TIMELINE_TOOL_LABELS 早就给这四个写好了三态细分文案
  // （已读取网页内容 / 已打开网页 / 已操作页面 / 已关闭网页，及各自的失败句），
  // 渲染层却把它整张表挡在门外。**不要在这里再造一套 browser 文案**：文案只有一份事实源，
  // 那份表同时供实时链路与历史回放使用（toolStepDisplay），在这里另写一套就是刷新前后两副样子。
  //
  // failed 时不让 intent 接管标题：intent 是模型**动手之前**写的一句「这次要做什么」
  // （「点击提交按钮完成支付」），当成失败行的标题等于用"我要做 X"冒充"X 做成了"，
  // 而 browser_act 的作用域正含点提交、点确认支付。失败一律用表里的失败句。
  // （其它工具的失败行仍沿用 intent 优先的既有口径，本次不顺手改全局观感。）
  const isLive = live ?? step.status === 'running';
  if (step.status === 'failed' && step.name.startsWith('browser_')) return step.label;
  if (isLive && step.stage && step.label) return step.label;
  // 搜索行由 searchWebStepTitle 使用真实结果页；查询入参和命中数量都留在
  // 工具事件/来源面板，不在执行时间线复述用户输入。
  // 但 intent/label 若退化为工具原名，或完成后仍停在「正在检索网页」这种进行态套话，
  // 改用时间线中文 label。
  if (step.intent && !looksLikeRawToolName(step.intent) && (isLive || !isProgressPhrase(step.intent))) {
    return step.intent;
  }
  if (step.label && !looksLikeRawToolName(step.label) && (isLive || !isProgressPhrase(step.label))) {
    return step.label;
  }
  if (!isLive) {
    return toolStepDisplay({
      name: step.name,
      status: step.status === 'failed' ? 'failed' : 'completed',
      operation: step.operation,
      count: step.count,
    }).label;
  }
  return step.label || step.intent || step.name;
}

/** 折叠组头的状态类与文案（2026-07-29）。
 *
 *  「查阅网页」族失败也会被折进组里（reducer 的 hit 判定放宽成 status !== 'running'），
 *  而组头此前把 class 硬写 'completed'、文案硬写「· 已完成」——3 次抓取挂 1 次时
 *  收起态显示「查阅网页 · 3 次 · 已完成」，与单行失败被伪装成成功是同一个母题：
 *  **收起来可以，谎报不行**。全挂＝failed，部分挂＝completed 类但文案点明失败次数
 *  （整组不是失败，别让它红成一片；但数字必须出现）。 */
function runGroupStatusClass(step: Extract<AgentStep, { kind: 'runGroup' }>): string {
  const failed = Number(step.failedCount || 0);
  if (failed > 0 && failed >= step.count) return 'failed';
  return 'completed';
}

function runGroupSummary(step: Extract<AgentStep, { kind: 'runGroup' }>): string {
  const unit = step.unit || '步';
  if (step.familyId === 'search') return '';
  const failed = Number(step.failedCount || 0);
  // 正常完成不再重复写「已完成」：图标和执行头已经表达终态，组头只保留可操作的步骤数量。
  if (!failed) return `${step.count} ${unit}`;
  if (failed >= step.count) return `${step.count} ${unit} · 均失败`;
  return `${step.count} ${unit} · ${step.count - failed} 成功 / ${failed} 失败`;
}

/** stdout/stderr、退出码与运行时版本属于工具详情，不是用户可读的动作对象。 */
function isInternalToolOutput(text?: string): boolean {
  const value = String(text || '').trim();
  if (!value) return false;
  const lines = value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.some((line) => /^\[?\s*(?:stdout|stderr|std\s*out|std\s*err|exit[_ -]?code|output|result)\s*\]?$/i.test(line))) {
    return true;
  }
  if (lines.some((line) => /^(?:node|python|npm|pnpm|yarn)?\s*v?\d+(?:\.\d+){1,3}(?:[-+][\w.-]+)?$/i.test(line))) {
    return true;
  }
  return lines.some((line) => (
    /^exit[_ -]?code\s*[=:：]?\s*-?\d+/i.test(line)
    || PUBLIC_RUNTIME_NOISE_FRAGMENT_RE.test(line)
  ));
}

function visibleStepTarget(step: Extract<AgentStep, { kind: 'tool' }>): string {
  const candidates = [step.target, step.detail];
  for (const candidate of candidates) {
    const value = String(candidate || '').trim();
    if (value && !isInternalToolOutput(value)) return value;
  }
  return '';
}

/** 这一步要摊出来的网页截图（2026-07-28）。
 *
 *  两个来源：单个浏览步骤自己的 `shot`，以及折叠组头从成员那里汇总的 `shots`
 *  （连抓 3 次以上会折成一行，成员行连图一起被藏起来，组头替它们摊出来）。
 *  只认 data: 开头——与 reducer、历史回放、后端轨迹**四处同一道闸**：放任意外链等于
 *  开了个由工具回执控制的外部请求通道（可用来探测内网 / 追踪用户）。 */
function stepShots(step: AgentStep): string[] {
  const raw = step.kind === 'tool'
    ? (step.shot ? [step.shot] : [])
    : (step.kind === 'runGroup' ? (step.shots || []) : []);
  return raw.filter((s) => typeof s === 'string' && s.startsWith('data:'));
}

// 可在右侧产物面板预览的文件类型：html → iframe 预览，文本类 → 源码视图；
// 其余（docx/pptx/xlsx/pdf/图片等二进制）仍走「我的文件」页的专用预览器，卡上不出预览钮。
const _PREVIEW_TEXT_EXTS = new Set([
  'txt', 'md', 'markdown', 'csv', 'json', 'log', 'xml', 'yaml', 'yml',
  'py', 'js', 'ts', 'css', 'sh', 'sql', 'vue', 'jsx', 'tsx',
]);
function filePreviewKind(file: GeneratedFile): 'html' | 'code' | null {
  const ext = (file.filename.split('.').pop() || '').toLowerCase();
  if (ext === 'html' || ext === 'htm' || (file.mime || '') === 'text/html') return 'html';
  if (_PREVIEW_TEXT_EXTS.has(ext) || (file.mime || '').startsWith('text/')) return 'code';
  return null;
}

async function downloadGeneratedFile(file: NonNullable<ChatMessage['generatedFiles']>[number]) {
  const item: UserFileItem = {
    id: file.id,
    filename: file.filename,
    mime: file.mime || '',
    size: file.size,
    source: file.source || 'generated',
    threadId: null,
    folderId: null,
    expiresAt: file.expiresAt || null,
    createdAt: file.createdAt || null,
  };
  await downloadUserFile(item);
}

// 产物卡文件类型（与「我的文件」同一套判定与低饱和 tint）：图标底片按类型着色，帮助扫读
type GenFileKind = 'image' | 'pdf' | 'word' | 'excel' | 'ppt' | 'markdown' | 'text' | 'archive' | 'html' | 'other';

const GEN_KIND_ICON: Record<GenFileKind, unknown> = {
  image: FileImageOutlined,
  pdf: FilePdfOutlined,
  word: FileWordOutlined,
  excel: FileExcelOutlined,
  ppt: FilePptOutlined,
  markdown: FileMarkdownOutlined,
  text: FileTextOutlined,
  html: FileTextOutlined,
  archive: FileZipOutlined,
  other: FileOutlined,
};

function isResearchReportFile(file: { filename?: string; source?: string; origin?: { tool?: string } | null }): boolean {
  return file.source === 'research' || file.origin?.tool === 'research';
}

function hasResearchReportFiles(message: ChatMessage): boolean {
  return researchReportFiles(message).length > 0;
}

function researchReportFiles(message: ChatMessage): GeneratedFile[] {
  return visibleDeliverables(message.generatedFiles).filter((file) => isResearchReportFile(file));
}

function hasFinishedResearchReport(message: ChatMessage): boolean {
  if (message.role !== 'assistant') return false;
  return hasResearchReportFiles(message) || showResearchStructureCard(message);
}

function researchCardMarkdown(message: ChatMessage): string {
  return researchStructureMarkdown(message);
}

function researchCardPreviewHtml(message: ChatMessage): string {
  const markdown = researchCardMarkdown(message);
  return markdown ? presentResearchReport(linkResearchCites(renderMarkdown(markdown))) : '';
}

function researchCardTitle(message: ChatMessage): string {
  const fromMd = researchStructureTitle(researchCardMarkdown(message));
  if (fromMd && fromMd !== '研究报告') return fromMd;
  const file = researchDownloadFile(message);
  return file ? generatedFileTitle(file) : '研究报告';
}

function researchCardBodyHtml(message: ChatMessage): string {
  const markdown = researchCardMarkdown(message);
  if (!markdown) return '';
  const body = stripLeadingTitleHeadings(markdown, researchCardTitle(message));
  return body ? presentResearchReport(linkResearchCites(renderMarkdown(body))) : '';
}

function researchDownloadFile(message: ChatMessage): GeneratedFile | undefined {
  const files = researchReportFiles(message);
  return files.find((file) => /\.html?$/i.test(file.filename)) || files[0];
}

function showResearchStructureCard(message: ChatMessage): boolean {
  if (message.role !== 'assistant' || isUserClarificationMessage(message)) return false;
  if (!isResearchTurn(message)) return false;
  const markdown = researchCardMarkdown(message);
  const files = hasResearchReportFiles(message);
  if (!markdown && !files) return false;
  if (isExecutionRunning(message)) return files;
  return true;
}

function openResearchStructureViewer(message: ChatMessage) {
  const markdown = researchCardMarkdown(message);
  if (markdown) {
    planReportViewer.value = {
      title: researchCardTitle(message),
      html: researchCardBodyHtml(message),
      research: true,
    };
    return;
  }
  const file = researchDownloadFile(message);
  if (file) emit('previewFile', file);
}

function onResearchStructurePreview(message: ChatMessage, event: MouseEvent | KeyboardEvent) {
  if (event.target instanceof Element && event.target.closest('a, button, .research-table-scroll')) return;
  if (event.type === 'keydown') event.preventDefault();
  if (researchCardMarkdown(message)) openResearchStructureViewer(message);
}

/** 研究报告文件已落到本条消息：立刻出白卡。不要求 loading 先落下——
 *  活订阅若漏了 run.completed，刷新前也会把 Markdown 气泡当成终态。 */
function isResearchReportPending(message: ChatMessage): boolean {
  if (!isResearchTurn(message) || isUserClarificationMessage(message)) return false;
  return isExecutionRunning(message);
}

function hideResearchNarrative(message: ChatMessage): boolean {
  if (!isResearchTurn(message) || isUserClarificationMessage(message)) return false;
  return isResearchReportPending(message) || hasFinishedResearchReport(message);
}

function researchCompletionStats(message: ChatMessage): string {
  const anchor = execCollapseAnchor(message);
  const tail = execHeadState(anchor);
  const totals = [anchor, message, tail].map(item => item.researchProgress?.sourcesFound)
    .filter((value): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
  return buildResearchCompletionStats({
    researchProgress: { sourcesFound: totals.length ? Math.max(...totals) : undefined },
  }, execHeadTimeText(anchor));
}

function generatedFileTitle(file: { filename: string; source?: string; origin?: { tool?: string } | null }): string {
  if (!isResearchReportFile(file)) return file.filename;
  const stem = file.filename.replace(/\.(html?|research\.md)$/i, '') || file.filename;
  return sanitizeResearchTitle(stem);
}

function generatedFileKindLabel(file: { filename: string; mime?: string; source?: string; origin?: { tool?: string } | null }): string {
  if (isResearchReportFile(file)) return '研究报告';
  return fileTypeLabel(file.filename, file.mime);
}

function genFileKind(file: { filename: string; mime?: string; source?: string; origin?: { tool?: string } | null }): GenFileKind {
  if (isResearchReportFile(file)) return 'html';
  const name = (file.filename || '').toLowerCase();
  const mime = file.mime || '';
  if (mime.startsWith('image/')) return 'image';
  if (name.endsWith('.pdf')) return 'pdf';
  if (name.endsWith('.doc') || name.endsWith('.docx')) return 'word';
  if (name.endsWith('.xls') || name.endsWith('.xlsx') || name.endsWith('.xlsm') || name.endsWith('.csv')) return 'excel';
  if (name.endsWith('.ppt') || name.endsWith('.pptx')) return 'ppt';
  if (name.endsWith('.html') || name.endsWith('.htm') || mime === 'text/html') return 'html';
  if (name.endsWith('.md') || name.endsWith('.markdown')) return 'markdown';
  if (mime.startsWith('text/') || name.endsWith('.txt') || name.endsWith('.json')) return 'text';
  if (/\.(zip|rar|7z|gz|tar|dmg)$/.test(name)) return 'archive';
  return 'other';
}

// 产物审查徽标（Phase C v1）：文件卡如实标注结构化硬校验结论；unknown=审查未能完成。
// 降噪（2026-07-20 用户拍板，与失败降噪同一原则）：文件都已交付，审查结论是参考信息——
// failed 不再写「未通过」的红字吓人，收成「建议复查」的提醒级；详情在悬停 summary 里。
function reviewBadgeText(status: string): string {
  return (
    ({ passed: '审查通过', warning: '审查提醒', failed: '建议复查', unknown: '未完成审查' } as Record<string, string>)[
      status
    ] || ''
  );
}

// 文件卡「我的文件」入口：跳到文件工作区板块（/center/files），预览/管理都在那边。
// 时间线的文件按钮额外传入 step：`已下载 材料.pdf`（source=material）、`已写入 build.py`
// 这些目标在默认清单里被 deliverables_only 滤掉，落点要带 `?all=1`（2026-07-28），
// 否则按钮上明明写着文件名、点下去却是一片找不到它的列表。产物卡走的是交付物，无参数。
const router = useRouter();
function goMyFiles(step?: Extract<AgentStep, { kind: 'tool' }>) {
  if (!step) {
    router.push('/center/files');
    return;
  }
  router.push(myFilesRouteFor({ ...step, target: visibleStepTarget(step) }));
}

// 渲染结果按内容缓存：流式时只有当前这条消息内容在变，其余历史消息命中缓存直接返回，
// 避免每来一个 token 就对整屏消息重跑 markdown-it + katex + xss（长会话流式会明显卡顿）。
// 缓存 key 用原始入参；value 是「无损版式编译 → 产物预处理 → markdown → xss」后的 HTML。
// 正常 Markdown 逐字直通；只有模型把长答案压成单行时，编译器才在原字符之间插入版式标记。
// 它用 source fragments 反向校验原文，不能删除、替换或重排 82.7、日期、版本号等正文。
const renderCache = new Map<string, string>();
const RENDER_CACHE_MAX = 120;
function renderMarkdown(content: string) {
  const key = content || '';
  const cached = renderCache.get(key);
  if (cached !== undefined) return cached;
  const presentation = compileAnswerLayout(key);
  const html = richXss.process(markdownParser.render(preprocessArtifacts(presentation.markdown)));
  renderCache.set(key, html);
  if (renderCache.size > RENDER_CACHE_MAX) {
    const oldest = renderCache.keys().next().value;
    if (oldest !== undefined) renderCache.delete(oldest);
  }
  return html;
}

// 普通问答剥掉 [1][12] 来源编号（来源只走「参考了 N 个来源」）。
// 深度研究保留 [n]，编译成与报告卡相同的 cite-chip 角标。
function renderAssistantHtml(message: ChatMessage): string {
  const research = isResearchTurn(message);
  const source = research
    ? stripResearchScaffold(sanitizeAssistantBody(message.content))
    : sanitizeAssistantBody(message.content);
  const html = renderMarkdown(source);
  if (message.role !== 'assistant') return html;
  const artifactImagesStayInFile = hasArtifactImageContext(message);
  const images = messageImages(message);

  const usedImages = new Set<number>();
  let hasFloatImage = false;
  const figureFor = (n: number): string | null => {
    if (!Number.isInteger(n) || n < 1) return null;
    if (usedImages.has(n)) return ''; // 同一张图重复引用：去掉多余标记
    usedImages.add(n);
    const img = images[n - 1];
    if (!img?.url || !isRenderableChatImageUrl(img.url)) return renderMissingChatImage(n);
    const floating = props.answerLayout !== 'campus' && !hasFloatImage;
    hasFloatImage = true;
    return renderChatImageFigure(img, floating);
  };

  return html
    .split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/g)
    .map((seg, i) => {
      if (i % 2 === 1) return seg;
      let out = research ? linkResearchCites(seg) : stripInlineSourceMarkers(seg);
      if (artifactImagesStayInFile) {
        // 旧产物消息可能既有 [图N] 又有模型直写的 Markdown <img>。产物卡、
        // 用户附件与 PPT 预览都在正文 DOM 之外，这里删除不会伤到它们。
        out = out.replace(/<p>\s*\[图\d{1,2}\]\s*<\/p>/g, '');
        out = out.replace(/\[图\d{1,2}\]/g, '');
        out = out.replace(/<p>\s*(?:<img\b[^>]*>\s*)+<\/p>/gi, '');
        out = out.replace(/<img\b[^>]*>/gi, '');
      }
      if (!artifactImagesStayInFile) {
        // 独立成段的 [图N]（<p>[图N]</p>）先整段替换成块级图卡，避免留下空段落；
        // 行内出现的随后替换（浏览器会就地闭合段落，视觉可接受）
        out = out.replace(/<p>\s*\[图(\d{1,2})\]\s*<\/p>/g, (m, n) => figureFor(Number(n)) ?? m);
        out = out.replace(/\[图(\d{1,2})\]/g, (m, n) => figureFor(Number(n)) ?? m);
      }
      return out;
    })
    .join('');
}

/**
 * 完整 HTML 产物：正文中只保留紧凑产物卡，源码放在右侧面板的「源码」页签。
 * live=true 表示本次扫描发生在流式期间或流收尾 pass——只有这时新出现的产物
 * 才算「本轮新完成」，父组件据此决定桌面端自动打开；历史加载/重扫不打扰。
 */
function renderArtifacts(live = false) {
  const root = messageListRef.value;
  if (!root) return;
  root.querySelectorAll<HTMLElement>('pre.artifact-html-block[data-src]').forEach((block) => {
    const html = decodeURIComponent(block.getAttribute('data-src') || '');
    const id = hashArtifact(html);
    const title = extractArtifactTitle(html);

    // 旧「HTML 页面 · 点击预览」内嵌卡已废弃（2026-07-23 用户拍板去除干净）：
    // 同一产物会经 persistArtifact 自动存「我的文件」并渲染成新版文件卡（预览/下载/
    // 版本历史），正文中只留隐藏标记承载数据——供自动存档、「产物 N」入口与查看器对账。
    const card = document.createElement('div');
    card.className = 'artifact-card';
    card.style.display = 'none';
    card.dataset.artifactId = id;
    card.dataset.artifactTitle = title;
    (card as unknown as { __html: string }).__html = html;

    block.replaceWith(card);
  });
  const seen = new Set<string>();
  const list: Artifact[] = [];
  root.querySelectorAll<HTMLElement>('.artifact-card[data-artifact-id]').forEach((card) => {
    const id = card.dataset.artifactId || '';
    if (!id || seen.has(id)) return;
    seen.add(id);
    list.push({
      id,
      type: 'html',
      title: card.dataset.artifactTitle || 'HTML 页面',
      html: (card as unknown as { __html?: string }).__html || '',
    });
  });
  detectedArtifacts.value = list;
  emit('artifacts', list, live);
}

/** 使用当前布局的真实滚动区，主对话和内置应用外壳沿用同一跟随逻辑。 */
function getScroller(): HTMLElement | null {
  return resolveMessageScrollContainer(messageListRef.value);
}

function hasAutoFollowPause(): boolean {
  return hasAutoFollowReadingPause(manualReadingPause, thoughtReadingPauseKeys.size);
}

function clearAutoFollowPauses() {
  manualReadingPause = false;
  thoughtReadingPauseKeys.clear();
}

/** 更新“是否在底部/是否自动跟随”并上报（驱动回到底部按钮）。带迟滞，避免小幅抖动反复切换。 */
function updateScrollState() {
  const el = getScroller();
  if (!el) return;
  const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
  const direction = el === lastObservedScroller
    ? scrollDirection(el.scrollTop, lastObservedScrollTop)
    : 'stationary';
  lastObservedScroller = el;
  lastObservedScrollTop = el.scrollTop;

  // 滚轮、触控板和触摸已在输入事件中先置阅读锁；这里只识别用户主动朝底部滚回。
  // 不能仅凭 scrollTop 变小判定“用户向上滚”：终态折叠等布局收缩也会让它变小。
  if (shouldResumeManualAutoFollow(manualReadingPause, dist, direction, pointerActive)) {
    // 只有用户明确朝底部滚回阈值内才解锁；内容增长、过拉回弹或手指未离开都不能解锁。
    manualReadingPause = false;
  }

  if (dist < AUTO_FOLLOW_RESUME_DISTANCE && !hasAutoFollowPause()) {
    stickToBottom.value = true;
  } else if (dist > AUTO_FOLLOW_PAUSE_DISTANCE) {
    stickToBottom.value = false;
  }
  emit('scrollState', dist < AUTO_FOLLOW_BUTTON_DISTANCE && !hasAutoFollowPause());
  updateActiveTurn();
}

// 触屏方向判定基准：记录本次触摸开始的位置，touchmove 据此换算净位移方向
// （TouchEvent 不带 deltaY，得自己算）
let touchTrackStartY = 0;

function onTouchTrackStart(e: TouchEvent) {
  pointerActive = true;
  touchTrackStartY = e.touches[0]?.clientY ?? 0;
}

function onPointerRelease() {
  if (!pointerActive) return;
  pointerActive = false;
  if (canAutoFollow(stickToBottom.value, manualReadingPause, thoughtReadingPauseKeys.size)) {
    scrollToBottom(false);
  }
}

/**
 * 用户主动向上滚（滚轮/触摸）→ 立刻停止自动跟随。
 * 同步在用户输入事件里置位，早于下一个流式 token 的异步更新，避免被反复拽回底部、看不了上文。
 */
function onUserScrollUp(e: Event) {
  if (e.type === 'wheel') {
    if ((e as WheelEvent).deltaY >= 0) return; // 向下滚（贴近底部）不触发
  } else if (e.type === 'touchmove') {
    const touch = (e as TouchEvent).touches[0];
    // 手指向下拖（clientY 变大）＝内容离开底部，等价 wheel 的向上滚，才该停止跟随；
    // 手指向上拖（clientY 变小）＝趋向底部，和 wheel 向下滚一样不该打断——此前没做方向判断，
    // 任何触摸滑动（不论方向）都会误关自动跟随
    if (!touch || touch.clientY - touchTrackStartY <= 0) return;
  }
  manualReadingPause = true;
  stickToBottom.value = false;
  emit('scrollState', false);
}

/** force=true（按钮/新消息）平滑滚并恢复跟随；否则（流式）瞬时滚，不与用户滚动打架。 */
function scrollToBottom(force = false) {
  if (force) clearAutoFollowPauses();
  nextTick(() => {
    const scrollTarget = getScroller();
    if (!scrollTarget) return;
    const dist = scrollTarget.scrollHeight - scrollTarget.scrollTop - scrollTarget.clientHeight;
    if (!force && shouldSkipProgrammaticStick(pointerActive, dist)) return;
    if (force) {
      scrollTarget.scrollTo({ top: scrollTarget.scrollHeight, behavior: 'smooth' });
      stickToBottom.value = true;
      emit('scrollState', true);
      return;
    }
    // 直接写 scrollTop：iOS 上 scrollTo 会打断橡皮筋，运行中贴底再上拉就会抖。
    scrollTarget.scrollTop = scrollTarget.scrollHeight;
  });
}

// 外层滚动区与列表均监听，兼容短内容变长及不同入口的响应式布局。
function bindScrollListeners() {
  const list = messageListRef.value;
  const outerScroller = resolveMessageScrollContainer(list?.parentElement ?? null);
  scrollerEl = outerScroller;
  outerScroller?.addEventListener('scroll', updateScrollState, { passive: true });
  list?.addEventListener('scroll', updateScrollState, { passive: true });
  // 滚轮/触摸向上滚：立即停止自动跟随（同步早于下一个 token）
  outerScroller?.addEventListener('wheel', onUserScrollUp, { passive: true });
  list?.addEventListener('wheel', onUserScrollUp, { passive: true });
  outerScroller?.addEventListener('touchstart', onTouchTrackStart, { passive: true });
  list?.addEventListener('touchstart', onTouchTrackStart, { passive: true });
  outerScroller?.addEventListener('touchmove', onUserScrollUp, { passive: true });
  list?.addEventListener('touchmove', onUserScrollUp, { passive: true });
  window.addEventListener('touchend', onPointerRelease, { passive: true });
  window.addEventListener('touchcancel', onPointerRelease, { passive: true });
}

function unbindScrollListeners() {
  const list = messageListRef.value;
  scrollerEl?.removeEventListener('scroll', updateScrollState);
  list?.removeEventListener('scroll', updateScrollState);
  scrollerEl?.removeEventListener('wheel', onUserScrollUp);
  list?.removeEventListener('wheel', onUserScrollUp);
  scrollerEl?.removeEventListener('touchstart', onTouchTrackStart);
  list?.removeEventListener('touchstart', onTouchTrackStart);
  scrollerEl?.removeEventListener('touchmove', onUserScrollUp);
  list?.removeEventListener('touchmove', onUserScrollUp);
  window.removeEventListener('touchend', onPointerRelease);
  window.removeEventListener('touchcancel', onPointerRelease);
}

defineExpose({ scrollToBottom, scrollToMessage });

let prevLen = 0;
let prevLastId: number | null = null;
let prevFirstId: number | null = null;

// 清掉此前遗留在 <body> 的错误图；卸载时也扫一遍，避免离开页面后残留
onMounted(() => {
  cleanupOrphanMermaid();
  prevLen = props.messages.length;
  prevLastId = props.messages[props.messages.length - 1]?.id ?? null;
  prevFirstId = props.messages[0]?.id ?? null;
  messageListRef.value?.addEventListener('click', onListClick);
  // 图片成功/失败都只更新自己的卡片，不吞掉正文或真实来源。
  messageListRef.value?.addEventListener('error', onMediaError, true);
  messageListRef.value?.addEventListener('load', onMediaLoad, true);
  nextTick(() => {
    bindScrollListeners();
    // 刷新后重进会话：消息在挂载时已就位，messages 的 watch 不会为初始内容触发，
    // 必须在此对已还原的历史消息补跑产物/图/代码块后处理，否则 :::artifact 占位块
    // 停留在空框、mermaid 不渲染（此前只调了 enhanceCodeBlocks，产物/图漏了）。
    renderMermaid();
    renderArtifacts();
    enhanceCodeBlocks();
    enhanceComparisonTables();
    scrollToBottom(true);
  });
});
onBeforeUnmount(() => {
  cleanupOrphanMermaid();
  unbindScrollListeners();
  messageListRef.value?.removeEventListener('click', onListClick);
  messageListRef.value?.removeEventListener('error', onMediaError, true);
  messageListRef.value?.removeEventListener('load', onMediaLoad, true);
  document.removeEventListener('keydown', onOverlayEsc); // 全屏浮层若开着，卸载时兜底摘监听
});

// 依赖面收窄（性能）：原先直接 `deep: true` 盯整个 messages 数组——每个 token 触发都要
// 递归遍历**全部**历史消息的 agentSteps/taskPlan/citations/generatedFiles，长会话流式时
// 主线程开销随历史长度线性恶化。这里把 getter 换成「结构签名 + 末条消息」的元组：
//  · 字符串签名读到 数组本身 / length / 首条 id / 末条 id+role → 切会话、整组替换、
//    加载历史、增删消息一律照旧触发（deep 对字符串是叶子，零递归成本）；
//  · 元组第二项是末条消息本身，深遍历只落在它身上 → 流式增量（正文、agentSteps 追加、
//    taskPlan 状态翻转…）照旧逐帧触发，收尾帧一帧不丢。
// 唯一收窄掉的是「非末条消息的原地深层改写」：流式写入、分段 preamble、打字机整条替换
// 的目标恒为末条（分段 split 后新段即末条），故无行为差异。
watch(
  () => {
    const list = props.messages;
    const tail = list[list.length - 1];
    return [
      `${list.length}:${list[0]?.id ?? ''}:${tail?.id ?? ''}:${tail?.role ?? ''}`,
      tail,
    ] as const;
  },
  () => {
    const msgs = props.messages;
    const grew = msgs.length > prevLen;
    prevLen = msgs.length;
    const last = msgs[msgs.length - 1];
    // 「最后一条消息 id 变了」= 结构变化（切会话/新消息），区别于「同一条助手消息流式增长」。
    const structural = (last?.id ?? null) !== prevLastId;
    prevLastId = last?.id ?? null;
    // 「第一条消息 id 也变了」= 整组替换（切会话/加载历史）：此时 loading 可能还是上一个
    // 会话的残留 true，产物扫描不得按 live 计，否则 B 会话的历史产物会被当成「本轮新完成」。
    const wholesale = (msgs[0]?.id ?? null) !== prevFirstId;
    prevFirstId = msgs[0]?.id ?? null;
    // 用户自己发出的新消息：无论此前是否滚上去都跟随到底；
    // 助手流式增量：仅当用户停留在底部时才自动跟随，看历史时不打扰。
    if (grew && last?.role === 'user') scrollToBottom(true);
    else if (canAutoFollow(stickToBottom.value, manualReadingPause, thoughtReadingPauseKeys.size)) {
      scrollToBottom(false);
    }
    nextTick(() => {
      // 纯 token 增长（同一助手消息、loading 中）跳过 mermaid/产物/代码块重后处理：否则每个 token
      // 都全屏 querySelectorAll 重扫、artifact 卡随半成品 HTML 反复拆建，高度来回跳 → 画面抖动。
      // 但**结构变化**（切会话/新消息）或非流式时要渲染一次——否则切到「另一个也在生成的会话」时，
      // 该会话历史里的 mermaid/产物停在裸代码框（loading 恒真，两个 watch 都不触发）。
      if (!props.loading || structural) {
        renderMermaid();
        // 流式期间追加的新消息算 live 扫描；整组替换（切会话/历史加载）不算
        renderArtifacts(Boolean(props.loading) && !wholesale);
        enhanceCodeBlocks();
        enhanceComparisonTables();
      }
      updateScrollState();
    });
  },
  { deep: true }
);

watch(
  () => props.loading,
  (loading) => {
    if (loading && canAutoFollow(stickToBottom.value, manualReadingPause, thoughtReadingPauseKeys.size)) {
      scrollToBottom(false);
    }
    // 生成结束：内容已定型，一次性做重后处理，不再抖动。
    // live=true：这是流收尾 pass，此刻升级出的产物卡就是「本轮新完成的产物」，
    // 父组件据此触发桌面端自动打开（流式中 loading 恒真，等到这里 loading 已翻 false，
    // 不能再用父组件的 loading 判断）。
    if (!loading) {
      nextTick(() => {
        renderMermaid();
        renderArtifacts(true);
        enhanceCodeBlocks();
        enhanceComparisonTables();
      });
    }
  }
);
</script>

<style scoped>
.message-list {
  width: 100%;
  /* 消息列表占满 chat-home 的内容盒；正文和执行区再由共享阅读轨道统一限宽。 */
  max-width: 1080px;
  flex: 1;
  /* 对话输出字体：系统 UI 栈 + Codex 式阅读密度（略紧行高、近黑正文、灰度抗锯齿）。
     中文优先苹方/雅黑；不拉字重到 450，避免 PingFang Medium 发黑。 */
  font-family:
    ui-sans-serif,
    -apple-system,
    system-ui,
    'Segoe UI',
    'PingFang SC',
    'Hiragino Sans GB',
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    'Helvetica Neue',
    Arial,
    sans-serif;
  color: #111;
  text-rendering: optimizeLegibility;
  /* 不设 overflow-y:auto——真正滚动的是外层 .workspace（grid 单元 overflow:auto），
     本列表自身从不滚动（父级非 flex，flex:1 失效、随内容长高）。若这里设了 auto/scroll，
     它会成为代码块头栏 position:sticky 的容器却又不滚动，导致吸顶失效（滚动时按钮消失）。
     改为 visible，让 sticky 冒泡到会滚动的 .workspace。 */
  overflow-y: visible;
  margin: 0 auto;
  /* 底部预留 = 对话态悬浮输入框占位（bottom 16 + 单行高约 100 + 输入框下提示约 26）+少量呼吸。
     滚到底时最后一条输出因此贴近输入框，不会在屏幕中间留出大片空白；
     输入框多行长高时会临时盖住少许，仍可下滚查看。 */
  /* chat-home 已提供 44px 页面安全边距。这里不再重复叠 28px 横向 padding，
     否则 820px 的共享阅读轨道会被二次压缩成约 764px，无法与输入框对齐。 */
  padding: 36px 0 148px;
}

/* 头像列已移除（2026-07-20）：单列布局，内容顶满 */
.message {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  width: 100%;
  margin-bottom: 28px;
  animation: message-enter 0.28s cubic-bezier(0.22, 1, 0.36, 1) both;
}

/* 最后一条消息不再额外撑 30px：底部让位统一由 .message-list 的 padding-bottom 负责 */
.message:last-child {
  margin-bottom: 12px;
}

.message.user {
  display: flex;
  justify-content: flex-end;
  width: min(100%, var(--chat-content-max-width, 820px));
  margin-inline: auto;
  margin-bottom: 24px;
}

.message-content {
  min-width: 0;
}

.message:not(.user) .message-content {
  /* 与底部输入框共用同一条居中阅读轨道。width:min 保证窄屏自然收缩，
     不再由 720px 正文限宽制造“正文左偏、输入框更宽”的比例错位。 */
  width: min(100%, var(--chat-content-max-width, 820px));
  margin-inline: auto;
}

.message.user .message-content {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  max-width: min(72%, 620px);
}

.user-attachments {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  margin-bottom: 8px;
}

/* 编辑态：内联多行输入 + 操作按钮，宽度撑满用户消息列 */
.message-edit {
  width: min(72vw, 560px);
}

.message-edit textarea {
  width: 100%;
  min-height: 72px;
  resize: vertical;
  border: 1px solid #d7d8dd;
  border-radius: 14px;
  background: #fff;
  padding: 10px 14px;
  color: #0d0d0d;
  font-size: 16px;
  line-height: 1.65;
  outline: none;
}

.message-edit textarea:focus {
  border-color: #818cf8;
  box-shadow: none;
}

.message-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

.message-edit-actions button {
  border: 1px solid #d7d8dd;
  border-radius: 8px;
  background: #fff;
  padding: 6px 16px;
  font-size: 13px;
  color: #4b5059;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.message-edit-actions .edit-cancel:hover {
  border-color: #c7c9d0;
  color: #202228;
}

.message-edit-actions .edit-save {
  border-color: #111;
  background: #111;
  color: #fff;
}

.message-edit-actions .edit-save:hover {
  background: #303035;
}

.message-edit-actions .edit-save:disabled {
  border-color: #d0d0d4;
  background: #c8c8cc;
  cursor: not-allowed;
}

/* 用户消息操作：悬停才露出；样式贴近轻量线框图标 */
.message-actions.user-actions {
  justify-content: flex-end;
  gap: 2px;
  margin-top: 6px;
}

.message-actions.user-actions button {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  color: #8b919a;
  background: transparent;
}

.message-actions.user-actions button:hover {
  background: transparent;
  color: #3f3f46;
}

.message-actions.user-actions .msg-action-ic {
  width: 15px;
  height: 15px;
}

/* 助手侧也统一：默认无底，悬停极淡，避免复制钮单独像「选中块」 */
.message-actions:not(.user-actions) button {
  background: transparent;
}

.message-actions:not(.user-actions) button:hover {
  background: #f3f4f6;
  color: #111;
}


/* 现仅用于展开的历史版本「上一版」标记（抬头已删）：轻量灰字即可 */
.message-role {
  display: block;
  margin: 1px 0 8px;
  color: #a3a7b0;
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.01em;
}

/* 正文输出：Codex 式阅读感——近黑、常规字重、略松行高，中文更稳、不发空。
   字号 16px；用 #111 与 1.7 行高补“厚实感”，不靠把字重拉到 Medium。 */
.message-bubble {
  color: #111;
  font-size: 16px;
  line-height: 1.7;
  letter-spacing: 0;
  font-weight: 400;
}

.message:not(.user) .message-bubble {
  width: 100%;
  max-width: 100%;
}


.message.user .message-bubble {
  max-width: 100%;
  border: 0;
  border-radius: 20px;
  background: var(--main-chat-skin-user-bubble, #f4f4f5);
  padding: 11px 16px;
  color: var(--main-chat-skin-user-bubble-text, #111);
  font-size: 16px;
  line-height: 1.6;
  letter-spacing: 0;
  box-shadow: none;
}

.message-bubble.muted {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #777d88;
}

.message-bubble :deep(p) {
  margin: 0 0 14px;
}

.message-bubble :deep(p:last-child) {
  margin-bottom: 0;
}

.message-bubble :deep(pre) {
  margin: 14px 0;
  overflow-x: auto;
  border: 1px solid #e6e8eb;
  border-radius: 12px;
  background: #fff;
  padding: 14px 16px;
  color: #1f2328;
}

/* 代码：ChatGPT 同款等宽栈（ui-monospace 优先取系统原生），0.875em 随正文缩放 */
.message-bubble :deep(code) {
  border-radius: 5px;
  background: #f0f0f2;
  padding: 2px 5px;
  font-family: ui-monospace, 'SFMono-Regular', 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 0.875em;
}

.message-bubble :deep(pre code) {
  background: transparent;
  padding: 0;
}

.message-bubble :deep(ul),
.message-bubble :deep(ol) {
  margin: 14px 0;
  padding-left: 24px;
}

.message-bubble :deep(blockquote) {
  margin: 12px 0;
  border-left: 3px solid #c7c9d0;
  padding-left: 14px;
  color: #656b76;
}

.message-bubble :deep(li) {
  margin: 7px 0;
}

/* 标题：给回复以层级感（模型常用 ## / ### 分段），克制的墨黑而非高饱和 */
.message-bubble :deep(h1),
.message-bubble :deep(h2),
.message-bubble :deep(h3),
.message-bubble :deep(h4) {
  margin: 24px 0 12px;
  font-weight: 700;
  line-height: 1.4;
  color: #0d0d0d;
}

/* 标题梯度随 16px 正文整体上移（对齐 ChatGPT 的 1.375/1.25/1.125/1em 比率） */
.message-bubble :deep(h1) {
  font-size: 22px;
}

.message-bubble :deep(h2) {
  padding-bottom: 6px;
  border-bottom: 1px solid #eceef1;
  font-size: 20px;
}

.message-bubble :deep(h3) {
  font-size: 18px;
}

.message-bubble :deep(h4) {
  font-size: 16px;
}

.message-bubble :deep(h1:first-child),
.message-bubble :deep(h2:first-child),
.message-bubble :deep(h3:first-child),
.message-bubble :deep(h4:first-child) {
  margin-top: 0;
}

.message-bubble :deep(strong) {
  font-weight: 700;
  color: #0d0d0d;
}

.message-bubble :deep(a) {
  color: #1f2328;
  text-decoration: underline;
  text-decoration-color: #c3c6cd;
  text-underline-offset: 2px;
  transition: text-decoration-color 0.15s ease;
}

.message-bubble :deep(a:hover) {
  text-decoration-color: #1f2328;
}

.message-bubble :deep(hr) {
  margin: 18px 0;
  border: 0;
  border-top: 1px solid #eceef1;
}

/* 表格：对齐 AIcss Comparison Table（2026-08-09） */
.message-bubble :deep(table) {
  width: 100%;
  max-width: 100%;
  margin: 14px 0;
  border: 1px solid #e6e8ec;
  border-radius: 12px;
  border-collapse: separate;
  border-spacing: 0;
  overflow: hidden;
  background: #fafafa;
  font-size: 13px;
  display: table;
}
.message-bubble :deep(thead th) {
  background: #fafafa;
  color: #a1a1a1;
  font-weight: 500;
  padding: 7px 12px;
  border-bottom: 1px solid #e6e8ec;
  border-right: 1px solid #e6e8ec;
  text-align: left;
  white-space: nowrap;
  line-height: 1.5;
}
.message-bubble :deep(tbody) { background: #fff; }
.message-bubble :deep(td) {
  background: #fff;
  color: #1a1a1a;
  padding: 9px 12px;
  border-bottom: 1px solid #e6e8ec;
  border-right: 1px solid #e6e8ec;
  text-align: left;
  line-height: 1.5;
  white-space: normal;
  overflow: visible;
  word-break: break-word;
}
.message-bubble :deep(th:last-child),
.message-bubble :deep(td:last-child) { border-right: 0; }
.message-bubble :deep(tbody tr:last-child td) { border-bottom: 0; }
.message-bubble :deep(table:not(:has(thead)) tr:first-child th),
.message-bubble :deep(table:not(:has(thead)) tr:first-child td) {
  background: #fafafa;
  color: #a1a1a1;
  font-weight: 500;
  padding-top: 7px;
  padding-bottom: 7px;
}
.message-bubble :deep(.tbl-yes) { color: #15a06a; font-weight: 500; }
.message-bubble :deep(.tbl-no) { color: #a1a1a1; }


.message-bubble :deep(pre.mermaid-block) {
  display: flex;
  justify-content: center;
  margin: 14px 0;
  border: 1px solid #e6e6ea;
  border-radius: 12px;
  background: #fff;
  padding: 16px;
  overflow-x: auto;
}

.message-bubble :deep(pre.mermaid-block:not(.mermaid-rendered)) {
  display: block;
  color: #656b76;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 13px;
  white-space: pre-wrap;
}

/* 流式期间占位恒为空（renderMermaid 每 token 重扫被刻意跳过防抖动）：
   空框很难看（2026-07-15 用户反馈），给一行「正在绘制流程图」微光文案，
   与执行头「正在生成回答」同一 shimmer 语言；流收尾后原地升级为图表卡片。 */
.message-bubble :deep(pre.mermaid-block:empty)::before {
  content: '正在绘制流程图…';
  display: inline-block;
  background: linear-gradient(90deg, #9aa0ab 0%, #9aa0ab 38%, #23272e 50%, #9aa0ab 62%, #9aa0ab 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 12.5px;
  font-weight: 500;
  line-height: 18px;
  animation: exec-head-shimmer 1.7s linear infinite;
}

.message-bubble :deep(pre.mermaid-block svg) {
  max-width: 100%;
  height: auto;
}

/* ===== mermaid 卡片（图表/代码页签 + 缩放/下载/全屏工具栏）===== */
.message-bubble :deep(.mermaid-card) {
  margin: 14px 0;
  border: 1px solid #e6e6ea;
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
}

.message-bubble :deep(.mermaid-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 42px;
  padding: 0 8px 0 10px;
  border-bottom: 1px solid #eceef1;
  background: #f6f7f9;
}

.message-bubble :deep(.mermaid-tabs) {
  display: inline-flex;
  gap: 2px;
  padding: 2px;
  border-radius: 8px;
  background: #eceef1;
}

.message-bubble :deep(.mermaid-tab) {
  padding: 4px 14px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.message-bubble :deep(.mermaid-tab.is-active) {
  background: #fff;
  color: #111;
  box-shadow: 0 1px 2px rgba(17, 24, 39, 0.08);
}

.message-bubble :deep(.mermaid-tools) {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.message-bubble :deep(.mermaid-tool) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.message-bubble :deep(.mermaid-tool:hover) {
  background: #eaecef;
  color: #111;
}

.message-bubble :deep(.mermaid-tool svg) {
  width: 15px;
  height: 15px;
}

.message-bubble :deep(.mermaid-view) {
  padding: 16px;
  overflow: auto;
}

.message-bubble :deep(.mermaid-diagram) {
  display: flex;
  justify-content: center;
}

/* 方案一 · 自适应缩放：保留 mermaid 的 width:100%（横图铺满容器宽），只加一个最大高度——
 * 竖长图会靠 SVG 自带的 preserveAspectRatio(meet) 按比例缩到限高内、居中显示，横图不受影响。
 * 细节走右上角「放大 / 全屏」；放大时 applyMermaidScale 解除本限高与宽度约束。 */
.message-bubble :deep(.mermaid-diagram svg) {
  max-width: 100%;
  max-height: var(--mermaid-fit-max-h, 440px);
  height: auto;
}

.message-bubble :deep(.mermaid-code) {
  margin: 0;
  padding: 2px;
  color: #374151;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

/* mermaid 全屏浮层（Teleport 到 body，scoped 属性随渲染带出） */
.mermaid-fs-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px;
  background: rgba(17, 19, 24, 0.62);
  backdrop-filter: blur(2px);
  animation: mermaid-fs-in 0.16s ease both;
}

@keyframes mermaid-fs-in {
  from {
    opacity: 0;
  }
}

.mermaid-fs-inner {
  max-width: 94vw;
  max-height: 90vh;
  padding: 24px;
  border-radius: 12px;
  background: #fff;
  overflow: auto;
}

.mermaid-fs-inner :deep(svg) {
  width: 100%;
  height: auto;
}

.mermaid-fs-close {
  position: fixed;
  top: 20px;
  right: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: 0;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.92);
  color: #333;
  font-size: 15px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.mermaid-fs-close:hover {
  background: #fff;
}

.plan-report-fs-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 56px 24px 32px;
  background: rgba(17, 19, 24, 0.62);
  backdrop-filter: blur(2px);
  overflow: auto;
  animation: mermaid-fs-in 0.16s ease both;
}

.plan-report-fs-paper {
  width: min(820px, 100%);
  margin: 0 auto 40px;
  padding: 8px 8px 32px;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.18);
}

.plan-report-fs-paper > h1 {
  margin: 20px 32px 0;
  color: #111;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.35;
}

@media (prefers-reduced-motion: reduce) {
  .mermaid-fs-overlay,
  .plan-report-fs-overlay {
    animation: none;
  }
}

/* 产物占位（仅流式期间可见，收尾即被 renderArtifacts 替换成源码框+卡片）：
   内嵌转义源码，样式退化为源码框——不再是空白边框。 */
.message-bubble :deep(pre.artifact-html-block) {
  margin: 14px 0;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #f7f8fa;
  padding: 12px 14px;
  max-height: 260px;
  overflow: auto;
}

.message-bubble :deep(pre.artifact-html-block code) {
  font-family: 'SFMono-Regular', Consolas, Monaco, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  color: #3a3f4a;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-bubble :deep(pre.artifact-html-block .artifact-iframe) {
  display: block;
  width: 100%;
  height: 360px;
  border: 0;
}

/* 流式产物源码（2026-07-13 拍板：直接看到代码在写）：限高 + column-reverse 黏底——
   内容增长时视口自动钉在最新行，不刷屏不抖动；::before 是第一个 flex 项，
   column-reverse 下渲染在视觉底部 = 「正在编写」提示常驻在最新代码行下方。 */
.message-bubble :deep(pre.artifact-streaming-block) {
  display: flex;
  flex-direction: column-reverse;
  margin: 14px 0;
  padding: 12px 14px;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #f7f8fa;
  max-height: 300px;
  overflow: auto;
}

.message-bubble :deep(pre.artifact-streaming-block code) {
  font-family: 'SFMono-Regular', Consolas, Monaco, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  color: #3a3f4a;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-bubble :deep(pre.artifact-streaming-block)::before {
  content: '⋯ 正在编写 HTML 源码';
  display: block;
  margin-top: 8px;
  color: #8a8f99;
  font-family: inherit;
  font-size: 12px;
}

/* 旧的产物占位加载卡（已被流式源码块取代；保留样式给历史消息里可能缓存的占位兜底） */
.message-bubble :deep(pre.artifact-loading-block) {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 14px 0;
  border: 1px solid #e6e6ea;
  border-radius: 12px;
  background: #fafafb;
  padding: 14px 16px;
  color: #6b7280;
  font-family: inherit;
  font-size: 13px;
  white-space: normal;
}

.message-bubble :deep(pre.artifact-loading-block)::before {
  content: '';
  width: 14px;
  height: 14px;
  flex: none;
  border: 2px solid #d3d6de;
  border-top-color: #111827;
  border-radius: 50%;
  animation: artifact-spin 0.8s linear infinite;
}

.message-bubble :deep(pre.artifact-loading-block)::after {
  content: '正在生成产物…';
}

@keyframes artifact-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .message-bubble :deep(pre.artifact-loading-block)::before {
    animation: none;
  }
}

.routed-agent {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
  padding: 2px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  font-size: 12px;
  color: #4b5563;
  background: #f9fafb;
}

.context-compacted-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 6px;
  padding: 2px 10px;
  border: 1px dashed #d7d8dd;
  border-radius: 999px;
  font-size: 12px;
  color: #8a8f99;
  background: #fafafb;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message.superseded {
  opacity: 0.72;
}

.message.superseded .message-role {
  color: #a3a7b0;
}

/* 过程流：比正文更安静一档。灰阶收敛到 3 档，字号用整数，图标轨略宽一点。
   高级感来自「步骤是旁注、叙述/回答才是主体」，不是更大更粗。 */
.execution-stream {
  /* 过程常态统一灰色；只有可交互执行步骤在 hover 时变黑，开场说明始终保持灰色。 */
  --execution-text: #8e8e8e;
  --execution-text-hover: #111;
  --execution-text-muted: #8e8e8e;
  --execution-hairline: #ececec;
  --execution-icon-rail: 16px;
  --execution-icon-gap: 10px;
  /* 公开阐述无论在动作前还是动作之间，都是同一信息层级。 */
  --execution-narrative-font-size: 16px;
  --execution-narrative-line-height: 1.7;
  margin: 2px 0 12px;
  width: 100%;
  max-width: 100%;
  font-family: inherit;
  font-size: 14px;
  font-weight: 400;
  letter-spacing: 0;
  line-height: 1.5;
  text-rendering: optimizeLegibility;
}

/* 标题与普通回复同级：运行中是纯状态，完成后才可折叠，并只在悬停时露出箭头。 */
/* v3.0 续做注记：与停止留痕同视觉族，但用主题色区分「接着做」语义 */
/* v3.0 技能不可用警示条：琥珀色、不打断步骤行排版（inline-flex 收在行内） */
.ast-skill-warning {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  margin: 2px 0 0;
  padding: 2px 8px;
  border-radius: 4px;
  background: rgba(250, 173, 20, 0.10);
  color: #ad6800;
  font-size: 12px;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (prefers-reduced-motion: reduce) {
  .ast-skill-warning { transition: none; }
}

.exec-head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 100%;
  margin: 2px 0 14px;
  padding: 2px 0 12px;
  border: 0;
  border-bottom: 1px solid #ececec;
  border-radius: 0;
  background: transparent;
  color: var(--execution-text);
  cursor: default;
  text-align: left;
  transition: color 0.15s ease;
}

/* 终态收束行：「执行过程」作过程流次要标签，用灰色；分隔线与运行中共用发丝线 */
.exec-head.collapsible:not(.running) {
  color: var(--execution-text-muted);
  margin-bottom: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid #ececec;
}

.exec-head.collapsible {
  cursor: pointer;
}

.exec-head.collapsible:hover {
  color: var(--execution-text);
}

.exec-head.collapsible:focus-visible {
  outline: none;
  color: var(--execution-text);
}

.exec-head-state-dot {
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #8a641f;
}

.exec-head.terminal-failed .exec-head-state-dot {
  background: #8a4d3a;
}

.exec-head.terminal-partial .exec-head-state-dot {
  background: #7b8492;
}

.exec-head.terminal-stopped .exec-head-title,
.exec-head.terminal-failed .exec-head-title {
  color: #5f5546;
}

.exec-head-title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: inherit;
  font-size: 15px;
  font-weight: 400;
  line-height: 1.45;
  letter-spacing: -0.01em;
}

.exec-head.collapsible:not(.running) .exec-head-title {
  font-size: 15px;
  font-weight: 400;
  color: inherit;
  letter-spacing: -0.01em;
}


/* 运行中：标题窄光束扫描（v1 效果）+ 匀速无空扫（2026-07-15 三轮定稿）。
   关键：v1 的 120%→-120% 区间里光束只有 ~35% 时间在文字上，其余在场外空跑——
   看起来就是「扫一半停下来」。区间精确裁到光束「刚出左边→刚出右边」
   （220% 图、束宽 38-62% 时恰为 114%→-14%），一道出去下一道立刻进来，
   传送带式匀速节奏，永不停顿。 */
.exec-head.running .exec-head-title {
  /* 更克制的窄光束：灰阶差缩小，减少“演示特效”感 */
  background: linear-gradient(90deg, #8e8e8e 0%, #8e8e8e 40%, #171717 50%, #8e8e8e 60%, #8e8e8e 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: exec-head-shimmer 1.8s linear infinite;
}

.exec-head-elapsed {
  flex: none;
  color: var(--execution-text-muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

@keyframes exec-head-shimmer {
  0% {
    background-position: 114% 0;
  }
  100% {
    background-position: -14% 0;
  }
}

.exec-head-chevron {
  flex: none;
  margin-left: 2px;
  color: #777c85;
  opacity: 0.72;
  visibility: visible;
  pointer-events: none;
  transition:
    color 0.15s ease,
    opacity 0.15s ease;
}

.exec-head.collapsible:hover .exec-head-chevron,
.exec-head.collapsible:focus-visible .exec-head-chevron {
  opacity: 1;
  color: #3f444c;
}

@media (prefers-reduced-motion: reduce) {
  .exec-head.running .exec-head-title {
    animation: none;
    -webkit-text-fill-color: #4b5563;
  }

  .message-bubble :deep(pre.mermaid-block:empty)::before {
    animation: none;
    -webkit-text-fill-color: #6b7280;
  }
}

.step-duration {
  display: none !important;
}

/* 网页截图穿插在执行步骤之间（2026-07-28 用户拍板）：抓完哪个页面就把那个页面摊在流里，
   像人一边看一边翻给你看。与动作行左缘对齐（行图标占 24px，这里跟着 .agent-step 的
   padding-left 走），宽度上限 360px——摊得看得清，又不至于把叙述挤成图册。 */
.ast-shot-figures {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 6px 0 2px;
}

.ast-shot-figure {
  display: block;
  overflow: hidden;
  width: min(360px, 100%);
  padding: 0;
  border: 1px solid var(--execution-hairline, #e5e7eb);
  border-radius: 10px;
  background: #fff;
  cursor: zoom-in;
  line-height: 0;
  transition:
    border-color 0.15s ease,
    box-shadow 0.15s ease;
}

/* 一组多张（折叠的「查阅网页 N 次」）时收窄并排，避免竖着堆成一长条 */
.ast-shot-figures .ast-shot-figure:not(:only-child) {
  width: min(200px, 100%);
}

.ast-shot-figure:hover {
  border-color: #cbd0d8;
  box-shadow: none;
}

.ast-shot-figure:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 1px;
}

.ast-shot-figure img {
  display: block;
  width: 100%;
  /* 截的是 1280×800 的一屏。按比例铺满宽度，再压一个高度上限：长图不至于独占一屏，
     裁的时候对齐顶部——页面头部（标题、导航）最能说明「这是哪个网站」。 */
  max-height: 200px;
  object-fit: cover;
  object-position: top center;
}

@media (prefers-reduced-motion: reduce) {
  .ast-shot:hover {
    transform: none;
  }
}

/* 主执行区折叠：外层 grid 负责真实内容高度，避免 max-height 猜值导致长任务忽快忽慢。 */
.execution-stream-collapse {
  display: grid;
  min-width: 0;
  grid-template-rows: 1fr;
  /* 开场无步骤时不要吃掉对话列剩余高度，否则执行头和计量行之间是一块空白。 */
  height: max-content;
  opacity: 1;
  transform: translateY(0);
  transform-origin: top;
}

.execution-stream-collapse > .execution-stream-list {
  min-height: 0;
  overflow: hidden;
}

.execution-stream-collapse-enter-active,
.execution-stream-collapse-leave-active {
  transition:
    grid-template-rows 340ms cubic-bezier(0.22, 1, 0.36, 1),
    opacity 220ms ease,
    transform 340ms cubic-bezier(0.22, 1, 0.36, 1);
}

.execution-stream-collapse-enter-from,
.execution-stream-collapse-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
  transform: translateY(-6px);
}

/* 完整铺开全部步骤；消息列表自身负责黏底，不在过程区制造第二个滚动容器。
   过程是“正文叙述 ↔ 动作回执”的自然交错，不画贯穿线，避免退化成流程图/日志卡。 */
.execution-stream-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 9px;
  padding: 2px 0 2px;
}

/* 所有执行步骤共用同一层进入/离开容器。用 grid 0fr↔1fr 过渡真实内容高度，
   不猜测 max-height；离场期间才裁切内容，常态不影响按钮焦点环和浮层。 */
.execution-row-flow {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.execution-row-transition {
  display: grid;
  min-width: 0;
  grid-template-rows: 1fr;
  opacity: 1;
  transform: translateY(0);
  transform-origin: top;
}

.execution-row-transition:not(:last-child) {
  margin-bottom: 9px;
}

.execution-row-transition > .agent-step {
  min-height: 0;
}

.execution-row-enter-active,
.execution-row-leave-active {
  transition:
    grid-template-rows 220ms cubic-bezier(0.22, 1, 0.36, 1),
    opacity 160ms ease,
    transform 220ms cubic-bezier(0.22, 1, 0.36, 1),
    margin-bottom 220ms cubic-bezier(0.22, 1, 0.36, 1);
}

.execution-row-enter-active > .agent-step,
.execution-row-leave-active > .agent-step {
  overflow: hidden;
}

.execution-row-enter-from,
.execution-row-leave-to {
  grid-template-rows: 0fr;
  margin-bottom: 0;
  opacity: 0;
  transform: translateY(-4px);
}

.agent-step {
  position: relative;
  /* 图标回到 flex 流内：不再用 padding 给绝对定位图标腾位，避免图标与文案两套垂直坐标 */
  padding-left: 0;
  min-width: 0;
}

.agent-step.thinking,
.agent-step.compaction {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  padding-left: 0;
}

.agent-step.note {
  z-index: 1;
  /* 叙述外壳不再另加 9/11px（2026-07-23 间距断层修复）：呼吸交给内层 5px + 流 gap */
  margin: 1px 0;
  padding: 1px 0;
  background: #fff;
}

/* Skill 是结构化事实，与公开阐述、工具动作共用同一条事件时间线。 */
.loaded-skill-summary {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  margin: 2px 0 0;
  color: var(--execution-text-muted, #8e8e8e);
  font-size: 13px;
  line-height: 1.5;
}

.loaded-skill-icon {
  display: inline-flex;
  width: var(--execution-icon-rail);
  height: var(--execution-icon-rail);
  flex: none;
  align-items: center;
  justify-content: center;
  color: #8e8e8e;
}

.loaded-skill-icon :deep(.anticon),
.loaded-skill-tool-icon {
  color: inherit;
  font-size: 14px;
}

.loaded-skill-label {
  flex: none;
}

.loaded-skill-names {
  min-width: 0;
  overflow: hidden;
  color: #6f737b;
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 瞬时 reasoning 尾窗沿用 2026-08-15 版本的渐隐视觉；公开内容或终态到达后整块撤下。 */
.reasoning-summary-step {
  margin: 8px 0 4px;
  padding-left: calc(var(--execution-icon-rail) + var(--execution-icon-gap));
}

.reasoning-summary-body {
  position: relative;
  max-width: 620px;
  overflow: hidden;
  color: #9a9ea6;
  font-size: 13.5px;
  line-height: 21px;
}

.reasoning-summary-step.active .reasoning-summary-body {
  max-height: 84px;
  mask-image: linear-gradient(to bottom, transparent 0, #000 16px, #000 100%);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 16px, #000 100%);
}

.reasoning-summary-step.initial .reasoning-summary-body {
  max-height: none;
  mask-image: none;
  -webkit-mask-image: none;
}

.reasoning-summary-body p {
  margin: 0 0 4px;
  animation: reasoning-summary-line-in 0.42s cubic-bezier(0.22, 1, 0.36, 1) both;
}

.reasoning-summary-body p:nth-child(1) { opacity: 0.58; }
.reasoning-summary-body p:nth-child(2) { opacity: 0.78; }
.reasoning-summary-body p:nth-child(3) { color: #777c85; }
.reasoning-summary-body p:last-child {
  margin-bottom: 0;
  color: #777c85;
  opacity: 1;
}

@keyframes reasoning-summary-line-in {
  from { opacity: 0; transform: translateY(3px); }
  to { transform: translateY(0); }
}

@media (prefers-reduced-motion: reduce) {
  .reasoning-summary-body p {
    animation: none;
  }
}

/* Codex 单轨对齐（2026-07-23 用户对标反馈「人家是对齐的」）：叙述、计划组头、
   动作行全部共享同一条左轨（图标 x=0、正文 x=0），不再用整行右移表达层级——
   层级感由组头呼吸点/字重与动作行的淡色自然区分。 */
.agent-step.step-nested {
  margin-left: 0;
}

/* 展开的搜索组成员向右让出一条层级轨；只缩进派生成员行，不改变原始事件顺序。 */
.agent-step.run-group-member {
  box-sizing: border-box;
  padding-left: calc(var(--execution-icon-rail) + var(--execution-icon-gap));
}

@media (prefers-reduced-motion: reduce) {
  .execution-stream-collapse-enter-active,
  .execution-stream-collapse-leave-active,
  .execution-row-enter-active,
  .execution-row-leave-active {
    transition: none;
  }

  .execution-row-enter-from,
  .execution-row-leave-to {
    transform: none;
  }
}

/* 工具详情箭头紧跟动作文字与耗时；不再固定到执行区最右侧形成视觉断层。 */
.agent-step-tool.has-shell-panel {
  padding-right: 0;
  /* 展开的原始命令/输出是独立详情区，允许它另起一行；动作回执自身仍不换行。 */
  flex-wrap: wrap;
}

.ast-shell-toggle {
  position: relative;
  top: auto;
  right: auto;
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  align-self: flex-start;
  margin-top: 3px;
  margin-left: 2px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #b0b4bb;
  cursor: pointer;
  opacity: 0;
  pointer-events: auto;
  transition:
    color 0.15s ease,
    opacity 0.15s ease;
}

.agent-step-tool:hover .ast-shell-toggle,
.ast-shell-toggle:focus-visible,
.ast-shell-toggle.open {
  opacity: 1;
}

.ast-shell-toggle:hover { color: #111; }

/* 统一披露箭头：折叠朝右、展开朝下，方向由共享组件给定。 */
.ast-shell-chevron {
  transition: color 0.15s ease;
}

/* 展开动画：高度 grid 0fr→1fr + 淡入微位移；关闭对称收起。
   两张详情卡再错开 50ms 入场，避免整块“啪”一下贴出来。 */
.ast-shell-panel {
  flex: 1 1 100%;
  display: grid;
  grid-template-rows: 1fr;
  min-width: 0;
  margin: 0 0 4px;
  margin-left: calc(var(--execution-icon-rail) + var(--execution-icon-gap));
  max-width: calc(100% - var(--execution-icon-rail) - var(--execution-icon-gap));
}

.ast-shell-panel-clip {
  overflow: hidden;
  min-height: 0;
}

.ast-shell-panel-enter-active,
.ast-shell-panel-leave-active {
  transition:
    grid-template-rows 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    opacity 0.2s ease,
    margin 0.2s ease;
}

.ast-shell-panel-enter-active .ast-shell-section,
.ast-shell-panel-leave-active .ast-shell-section {
  transition:
    opacity 0.22s ease,
    transform 0.26s cubic-bezier(0.22, 1, 0.36, 1);
}

.ast-shell-panel-enter-active .ast-shell-section:nth-child(2) {
  transition-delay: 0.05s;
}

.ast-shell-panel-leave-active .ast-shell-section:nth-child(1) {
  transition-delay: 0.03s;
}

.ast-shell-panel-enter-from,
.ast-shell-panel-leave-to {
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: 0;
  margin-bottom: 0;
}

.ast-shell-panel-enter-from .ast-shell-section,
.ast-shell-panel-leave-to .ast-shell-section {
  opacity: 0;
  transform: translateY(-6px);
}

.ast-shell-panel-enter-to .ast-shell-section,
.ast-shell-panel-leave-from .ast-shell-section {
  opacity: 1;
  transform: none;
}

/* 详情卡与正文标准代码块卡（.code-block）同一套语言：白卡 + 浅灰头栏条。
   执行脚本与输出各自独立成卡、中间留空隙——两段贴死在一起很挤（2026-07-15 用户反馈） */
.ast-shell {
  display: grid;
  gap: 8px;
  padding-top: 8px;
}

@media (prefers-reduced-motion: reduce) {
  .ast-shell-panel-enter-active,
  .ast-shell-panel-leave-active,
  .ast-shell-panel-enter-active .ast-shell-section,
  .ast-shell-panel-leave-active .ast-shell-section,
  .ast-shell-chevron {
    transition: none !important;
  }

  .ast-shell-panel-enter-from .ast-shell-section,
  .ast-shell-panel-leave-to .ast-shell-section {
    transform: none;
  }
}

.ast-shell-section {
  border: 1px solid #ebebeb;
  border-radius: 8px;
  background: #fafafa;
  overflow: hidden;
}

/* 段头更素：弱头栏，少后台卡片感 */
.ast-shell-head {
  display: flex;
  align-items: center;
  height: 28px;
  padding: 0 8px 0 12px;
  border-bottom: 1px solid #efefef;
  background: transparent;
  color: #6b6b6b;
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}

/* 段落复制：常态隐身、悬停段落才浮现——面板保持安静 */
.shell-copy {
  margin-left: auto;
  padding: 2px 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #8e8e8e;
  font-size: 12px;
  font-weight: 400;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.ast-shell-section:hover .shell-copy { opacity: 1; }
.shell-copy:hover { background: #efefef; color: #111; }

.ast-shell-code {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 10px 12px 12px;
  color: #2f2f2f;
  background: transparent;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  tab-size: 2;
}

/* 输出段矮一截：脚本是主角，输出扫一眼即可（可滚动看全） */
.ast-shell-section.out .ast-shell-code { max-height: 160px; }

/* 细滚动条：双段各自滚动时不显笨重 */
.ast-shell-code::-webkit-scrollbar { width: 8px; height: 8px; }
.ast-shell-code::-webkit-scrollbar-track { background: transparent; }
.ast-shell-code::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 5px;
  background: #dcdfe5;
  background-clip: content-box;
}
.ast-shell-code::-webkit-scrollbar-thumb:hover { background-color: #c6cad2; }

/* 单次工具失败通常会被 Agent 继续核对或重试，不把整块输出染红制造“整轮失败”的错觉。
   错误事实和详情仍完整保留，仅用中性灰表面与失败图标表达；整轮终态失败仍由过程头负责。 */
.ast-shell-section.failed .ast-shell-head {
  border-bottom-color: #efefef;
  background: transparent;
  color: #6b6b6b;
}

.ast-shell-section.failed .ast-shell-code {
  background: transparent;
  color: #525252;
}

/* 步骤节点：进 flex 流，与步骤文字同一行；margin-top 只做光学对齐 */
.step-node {
  position: static;
  flex: none;
  width: var(--execution-icon-rail);
  height: var(--execution-icon-rail);
  /* 与 13.5/1.45 行盒光学居中；单行工具行用 align-items:center 时 margin-top 归零 */
  margin-top: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: #6b6b6b;
  font-size: 13px;
  line-height: 1;
}

/* 思考节点：占同一图标轨，避免与动作行左右漂移 */
.step-node-dot {
  margin-top: 6px;
}

.step-node-dot::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: #fff;
  border: 1.5px solid #c4c4c4;
}

/* 进行中节点上深一档：无呼吸点后，图标本身以更实的灰表示「当前这一步」 */
.agent-step.action-running .step-node {
  color: #111;
}

/* 进行中步骤的行标题 shimmer（2026-07-23 用户拍板「跟正在分析需求那行一样」）：
   与执行头 running 标题共用同一道传送带式光束——去掉呼吸点后，由这道流光承担
   「这一步正在跑」的信号，整条执行流的运行态语言统一。 */
.agent-step.action-running .ast-label {
  background: linear-gradient(90deg, #8e8e8e 0%, #8e8e8e 40%, #171717 50%, #8e8e8e 60%, #8e8e8e 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: exec-head-shimmer 1.6s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .agent-step.action-running .ast-label {
    animation: none;
    -webkit-text-fill-color: #3f4248;
  }
}

/* 任务计划（update_plan）交错时间线（2026-07-15 Codex 化二阶）：计划步骤不再是顶部
   独立块，而是作为组头行与详细步骤交错排布——每个计划步骤下缩进嵌着它执行期间的
   工具/思考/产物步骤；图标坐在左轨上（白底圆遮轨线），信息以安静文字排布。 */
.exec-plan-item {
  position: relative;
  display: flex;
  min-width: 0;
  align-items: center;
  padding-left: 0;
  gap: var(--execution-icon-gap);
  color: var(--execution-text);
  font-size: 13.5px;
  line-height: 1.45;
  letter-spacing: -0.011em;
  transition: color 0.15s ease;
}

.exec-plan-icon {
  position: static;
  flex: none;
  top: auto;
  left: auto;
  display: inline-flex;
  width: var(--execution-icon-rail);
  height: var(--execution-icon-rail);
  margin-top: 3px;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: #6b7280;
  font-size: 11.5px;
}

/* 与 ExecutionActionIcon 同一套光学参数：Lucide viewBox 24 / stroke 2 缩放成细线 */
.eplan-glyph {
  display: block;
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

/* 全灰图标（2026-07-24 用户拍板）：失败圈叉也不上红，失败语义由字形与文案承担 */
.exec-plan-item.completed .exec-plan-icon { color: #a5a9b0; }
.exec-plan-item.failed .exec-plan-icon { color: #8e939c; }
.exec-plan-item.running .exec-plan-icon { color: #4b5563; }

.exec-plan-pending {
  width: 7px;
  height: 7px;
  border: 1.5px solid #c4c8d0;
  border-radius: 50%;
  background: #fff;
}

/* 当前计划步骤标记（2026-07-23 用户拍板去呼吸点）：不再呼吸，静音成一粒实心灰点；
   「正在进行」由计划标题的 shimmer 承担，与运行中动作行统一动效语言。 */
.exec-plan-live {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9aa0ab;
}

.exec-plan-live.quiet {
  background: #a0a5ae;
  opacity: 0.7;
}

/* 运行中计划步骤标题 shimmer：与执行头 / 运行中动作行同一道传送带流光 */
.exec-plan-item.running .exec-plan-title {
  background: linear-gradient(90deg, #8e8e8e 0%, #8e8e8e 40%, #171717 50%, #8e8e8e 60%, #8e8e8e 100%);
  background-size: 220% 100%;
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: exec-head-shimmer 1.6s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .exec-plan-item.running .exec-plan-title {
    animation: none;
    -webkit-text-fill-color: #3f4248;
  }
}

/* 生成态计量行：正文流式期间挂在消息最下方，点阵动画 + 实时阶段文案 */
.gen-meter {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 12px 0 2px;
  font-size: 12px;
  color: #9a9a9a;
  letter-spacing: -0.01em;
}

.gen-meter-orb {
  margin-right: 2px;
}

.gen-meter-status {
  color: #6b6b6b;
}

/* 骨架屏预告卡（中性无文字）：灰条轻呼吸，卡片就绪即撤 */
.task-skel-card {
  max-width: 720px;
  margin: 10px 0 4px;
  padding: 14px 16px;
  border: 1px solid #e6e8ec;
  border-radius: 10px;
  background: #fff;
}

.tsk-bar {
  height: 12px;
  margin: 8px 0;
  border-radius: 4px;
  background: #eceef2;
  animation: tsk-shimmer 1.4s ease-in-out infinite;
}

.tsk-bar.tall {
  height: 32px;
}

@keyframes tsk-shimmer {
  50% {
    opacity: 0.45;
  }
}

@media (prefers-reduced-motion: reduce) {
  .tsk-bar {
    animation: none;
  }
}

@keyframes exec-plan-breathe {
  0% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 0% 0%;
  }
  27% {
    border-radius: 30%;
    transform: scale(1.18);
    background-position: 50% 50%;
  }
  55% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 80% 80%;
  }
  100% {
    border-radius: 50%;
    transform: scale(1);
    background-position: 0% 0%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .exec-plan-live {
    animation: none;
    opacity: 1;
  }
}

/* detail 小字已从卡内移除（太乱）：标题独占整行，60% 限宽随之取消 */
.exec-plan-title {
  min-width: 0;
  overflow: hidden;
  color: #737983;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 400;
}

.exec-plan-item.completed .exec-plan-title { color: var(--execution-text); }

/* HITL 卡并入执行时间线（P0 统一面板）：贴时间线左轨、上方留缝，不受折叠影响 */
.execution-hitl {
  margin: 6px 0 4px 26px;
}

/* 跳回执行步骤的短暂高亮 */
.step-flash {
  animation: step-flash-bg 1.6s ease;
  border-radius: 8px;
}

@keyframes step-flash-bg {
  0%, 60% { background: #f2f6ff; }
  100% { background: transparent; }
}

/* 对话层硬类型（P0）：过程旁白/确认语与终答在视觉上轻区分，不换信息架构 */
.message-bubble.narrative-ack,
.message-bubble.narrative-system-note {
  opacity: 0.88;
  font-size: 0.96em;
}
.message-bubble.narrative-commentary,
.agent-step-note.narrative-commentary {
  /* 流式正文位于 execution-stream 外时仍需与过程区使用同一灰色，不能回退成深灰近黑。 */
  color: var(--execution-text-muted, #8e8e8e);
  font-size: var(--execution-narrative-font-size, 16px);
  line-height: var(--execution-narrative-line-height, 1.7);
  font-weight: 400;
  opacity: 1;
}
.message-bubble.narrative-final {
  /* message.content 的事件归属已经是 answer/final；颜色不再依赖整轮运行状态，
     因此首个流式字符就以终答黑色出现，结束时也不会发生灰转黑。 */
  color: #111;
  line-height: 1.75;
}

.message-bubble.narrative-final :deep(p) {
  margin-bottom: 18px;
}

.message-bubble.narrative-final :deep(p:has(> strong:only-child)) {
  margin: 24px 0 10px;
  line-height: 1.45;
}

.message-bubble.narrative-final :deep(p:first-child:has(> strong:only-child)) {
  margin-top: 0;
}

.message-bubble.narrative-final.after-execution {
  /* 过程与答案之间多留一口气；不换布局，只避免最后一步和首段正文粘成一团。 */
  margin-top: 14px;
}

.preamble-body {
  width: 100%;
  max-width: 100%;
  word-break: break-word;
  margin: 8px 0;
  /* 过程首句从首次出现起就是灰色，不能先按正文黑色渲染再切到 commentary。 */
  color: var(--execution-text-muted, #8e8e8e);
  font-size: var(--execution-narrative-font-size, 16px);
  line-height: var(--execution-narrative-line-height, 1.7);
  letter-spacing: 0;
}

.execution-stream .preamble-body.narrative-commentary {
  color: var(--execution-text-muted, #8e8e8e);
}

/* 计划正文排进研究报告白卡；思考中不出灰条。 */
.plan-report-preview {
  max-height: 420px;
  overflow: hidden;
  cursor: pointer;
}
.plan-report-body {
  padding: 28px 32px 36px;
  color: #111;
  font-size: 16px;
  line-height: 1.75;
  word-break: break-word;
}
.plan-report-body :deep(> :first-child) {
  margin-top: 0;
}
.research-report-preview .plan-report-body :deep(> h1:first-child),
.plan-report-fs-paper .plan-report-body :deep(> h1:first-child) {
  display: none;
}
.plan-report-body :deep(> :last-child) {
  margin-bottom: 0;
}
.plan-report-body :deep(h1) {
  margin: 0 0 18px;
  color: #111;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.3;
}
.plan-report-body :deep(h2) {
  margin: 28px 0 12px;
  color: #111;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.4;
}
.plan-report-body :deep(h3) {
  margin: 22px 0 10px;
  color: #111;
  font-size: 16px;
  font-weight: 700;
}
.plan-report-body :deep(p) {
  margin: 0 0 14px;
  color: #1f2937;
}
.plan-report-body :deep(strong) {
  color: #111;
  font-weight: 650;
}
.plan-report-body :deep(code) {
  padding: 1px 5px;
  border-radius: 4px;
  background: #f3f4f6;
  font-size: 0.9em;
}
.plan-report-body :deep(table) {
  width: 100%;
  margin: 8px 0 20px;
  border-collapse: collapse;
  display: table;
  font-size: 14px;
  line-height: 1.55;
}
.plan-report-body :deep(th),
.plan-report-body :deep(td) {
  border-bottom: 1px solid #ececec;
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  white-space: normal;
  word-break: break-word;
  overflow: visible;
}
.research-document-body {
  color: #263244;
  line-height: 1.85;
  overflow-wrap: anywhere;
}
.research-document-body :deep(h2) {
  margin: 32px 0 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e9edf3;
  font-size: 19px;
}
.research-document-body :deep(blockquote) {
  margin: 12px 0;
  padding: 12px 16px;
  border-left: 3px solid #cbd9ed;
  background: #f7f9fc;
  color: #586477;
  font-size: 14px;
  line-height: 1.8;
}
.research-document-body :deep(blockquote p:last-child) { margin-bottom: 0; }
.research-document-body :deep(ul),
.research-document-body :deep(ol) { padding-left: 22px; margin: 12px 0 20px; }
.research-document-body :deep(li) { margin: 8px 0; }
.research-document-body :deep(.research-cite) { margin-left: 2px; color: #8993a2; font-size: 10px; }
.research-document-body :deep(.research-table-scroll) { max-width: 100%; overflow-x: auto; margin: 16px 0 24px; }
.research-document-body :deep(.research-table-scroll:focus-visible) { outline: 2px solid #6c91d2; outline-offset: 2px; }
.research-document-body :deep(table) { margin: 0; font-size: 14px; line-height: 1.7; }
.research-document-body :deep(th) { background: #f7f8fa; color: #253043; font-weight: 600; }
.research-document-body :deep(th), .research-document-body :deep(td) { min-width: 100px; padding: 12px 14px; }
@media (max-width: 600px) {
  .research-document-body { padding: 20px 18px 28px; font-size: 15px; }
  .research-document-body :deep(h2) { font-size: 18px; }
}
.agent-step-note :deep(p),
.preamble-body :deep(p) {
  margin: 0 0 6px;
}
.agent-step-note :deep(p:last-child),
.preamble-body :deep(p:last-child) {
  margin-bottom: 0;
}
.agent-step-tool {
  /* 每一个动作回执都是一条稳定的单行信息：图标、动作、目标/结果和箭头在同一基线。
     原始命令与大段输出留在详情面板，长文字以省略号收口并可悬停查看，不能把值掉到下一行。 */
  display: flex;
  align-items: center;
  gap: 0;
  column-gap: var(--execution-icon-gap);
  width: 100%;
  max-width: 100%;
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: 0;
  color: var(--execution-text);
  min-width: 0;
  flex-wrap: nowrap;
  transition: color 0.15s ease;
}
.agent-step-tool > .step-node {
  margin-top: 0;
  align-self: center;
  flex: none;
}
.agent-step-tool > .ast-label {
  flex: 0 1 auto;
  min-width: 0;
  max-width: min(40ch, 62%);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-step-tool > .ast-label.ast-search-label {
  flex: 1 1 auto;
  max-width: 100%;
}

/* 行级白色高光扫过（wave）已移除（2026-07-15 动效收敛）：进行中一行由节点上的
   呼吸点（ExecutionActionIcon status=running）标识，全卡动效只剩头部 shimmer + 一粒呼吸点 */

.agent-step-tool.failed {
  color: var(--execution-text);
}

/* 技能名气泡（2026-07-24 用户选定 V1 并要求「气泡好看点」）：胶囊名片=
   图标小圆座 + 等宽技能名 + 发丝边；白底浮在时间线素色上，hover 边线加深一档 */
.ast-skill-pill {
  display: inline-flex;
  align-items: center;
  flex: 0 1 24ch;
  min-width: 0;
  max-width: min(28ch, 42%);
  gap: 6px;
  padding: 2px 10px 2px 3px;
  border: 1px solid #ebebeb;
  border-radius: 999px;
  background: #fff;
  color: #3f3f46;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: border-color 0.15s ease;
}

.ast-skill-pill:hover { border-color: #d4d4d4; }

.ast-skill-pill-ic {
  display: inline-flex;
  width: 18px;
  height: 18px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: #f4f4f5;
  color: #525252;
}

.ast-skill-pill-ic :deep(.execution-action-icon),
.ast-skill-pill-ic :deep(.eai-svg) {
  width: 11px;
  height: 11px;
}

.ast-skill-note {
  flex: 1 1 12ch;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #9298a3;
  font-size: 12px;
}

/* 同类归拢组头：整行可点；箭头默认隐藏，悬停/聚焦露出，展开后保持可见并朝下 */
.agent-step-tool.run-group {
  cursor: pointer;
  user-select: none;
  /* 组头是单行信息，垂直居中避免箭头相对标题上漂 */
  align-items: center;
  flex-wrap: nowrap;
}

.agent-step-tool.run-group > .step-node {
  margin-top: 0;
  align-self: center;
}

.agent-step-tool.run-group > .ast-label {
  /* 不占满剩余行宽，组摘要和箭头紧跟标题形成一个稳定信息簇。 */
  flex: 0 1 auto;
}

.agent-step-tool.run-group > .ast-target-text {
  /* 组摘要与箭头只贴着标题，不占满剩余阅读轨。 */
  flex: 0 1 auto;
  max-width: 18ch;
}

.agent-step-tool.run-group.is-static {
  cursor: default;
  user-select: text;
}

.agent-step-tool.run-group .rg-toggle {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  margin-left: 2px;
  align-self: center;
  color: #b0b4bb;
  line-height: 1;
  opacity: 0;
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    color 0.15s ease;
}

.agent-step-tool.run-group:hover .rg-toggle,
.agent-step-tool.run-group:focus-visible .rg-toggle,
.agent-step-tool.run-group .rg-toggle.open {
  opacity: 1;
}

.agent-step-artifact {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 6px;
  column-gap: var(--execution-icon-gap);
  width: 100%;
  flex-wrap: nowrap;
  color: var(--execution-text);
  font-size: 14px;
  line-height: 1.5;
  transition: color 0.15s ease;
}

.agent-step-artifact.failed {
  color: var(--execution-text);
}

.agent-step-verification {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  column-gap: var(--execution-icon-gap);
  width: 100%;
  flex-wrap: nowrap;
  color: var(--execution-text);
  font-size: 14px;
  line-height: 1.5;
  transition: color 0.15s ease;
}

.agent-step-verification.failed {
  color: var(--execution-text);
}

.verification-note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--execution-text-muted);
  font-size: 12px;
}

.artifact-file-names,
.ast-result {
  flex: 1 1 12ch;
  min-width: 0;
  max-width: min(36ch, 48%);
  margin-left: 0;
  color: var(--execution-text-muted);
  font-size: 13px;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ast-result.failed {
  color: var(--execution-text-muted);
}

.ast-label {
  flex: 0 1 auto;
  min-width: 0;
  font-size: inherit;
  font-weight: 400;
  line-height: inherit;
  letter-spacing: inherit;
  color: var(--execution-text);
  transition: color 0.15s ease;
  max-width: min(40ch, 62%);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 动作目标 → 标签片（2026-07-13 用户设计稿）：文件名/目标以浅底小片呈现，一眼可辨 */
.ast-target,
.ast-target-text {
  display: inline-flex;
  align-items: center;
  /* 目标标签只包住自身文本；不能因为处在单行 flex 布局里被拉成一整段灰条。 */
  flex: 0 1 auto;
  min-width: 0;
  max-width: min(36ch, 48%);
  padding: 1px 7px;
  border-radius: 5px;
  background: #f3f3f3;
  color: #3f3f46;
  font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}

/* 非文件类目标（中文短语/查询词）：去片、去等宽，回归安静正文灰 */
.ast-target-text.is-plain {
  padding: 0;
  border-radius: 0;
  background: transparent;
  color: var(--execution-text-muted);
  font-family: inherit;
  font-size: 13px;
  font-weight: 400;
  letter-spacing: 0;
}

.ast-target {
  border: 0;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.ast-target:hover {
  background: #ebebeb;
  color: #111;
}

.ast-detail {
  color: var(--execution-text-muted);
  white-space: normal;
  word-break: break-word;
}

/* 附件读取降级提示条（P0，2026-07-15 Codex 化）：细线弱警示，不再整块黄底 */
.attachment-degraded {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 720px;
  margin: 8px 0 4px;
  padding: 7px 10px;
  border: 1px solid #eee7d2;
  border-radius: 10px;
  background: #fffdf7;
  color: #8a7135;
  font-size: 12px;
  line-height: 1.6;
}

.attachment-degraded .anticon {
  color: #c2a557;
  font-size: 13px;
}

.attachment-degraded .ad-text {
  min-width: 0;
  flex: 1;
}

.attachment-degraded .ad-retry {
  flex: none;
  padding: 3px 9px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #6b5514;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.attachment-degraded .ad-retry:hover {
  background: #f6efdc;
  color: #4d3d0d;
}

.attachment-degraded .ad-hint {
  flex: none;
  color: #a9946a;
  font-size: 11.5px;
}

/* 交付清单（P0，2026-07-15 Codex 化）：不再是灰底大盒——一条细分隔线下的安静清单，
   标签列固定宽、常规排布（此前 text-align-last: justify 把「完成」拉成「完 成」，观感散架）。 */
/* 逐页产物直播卡（2026-07-20 对标 Manus）：白卡+头部（当前页标题+页码）+页面画布，
   换页交叉淡化；运行中头部左侧复用 exec-plan-live 形态呼吸点 */
.artifact-pages-card {
  margin: 10px 0 4px;
  max-width: 560px;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #fff;
  overflow: hidden;
  animation: gen-card-in 0.32s cubic-bezier(0.2, 0.7, 0.3, 1) both;
}

.apc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  border-bottom: 1px solid #eceef2;
}

.apc-icon {
  color: #6b7280;
  font-size: 14px;
  flex: none;
}

.apc-title {
  min-width: 0;
  font-size: 13.5px;
  font-weight: 500;
  color: #374151;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.apc-nav {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: #9aa0ab;
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.apc-nav:first-of-type {
  margin-left: auto;
}

.apc-nav:hover:not(:disabled) {
  background: #f4f5f7;
  color: #374151;
}

.apc-nav:disabled {
  opacity: 0.35;
  cursor: default;
}

.apc-counter {
  flex: none;
  font-size: 12.5px;
  color: #9aa0ab;
  font-variant-numeric: tabular-nums;
}

.apc-body {
  background: #fff;
  line-height: 0;
}

.apc-page {
  display: block;
  width: 100%;
  height: auto;
}

/* HTML 页直播（html-ppt 技能）：完全沙箱化 iframe 只当画面，1280×720 内容按容器等比缩放 */
.apc-frame {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  border: 0;
  pointer-events: none;
}

.apc-fade-enter-active,
.apc-fade-leave-active {
  transition: opacity 0.3s ease;
}

.apc-fade-enter-from,
.apc-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .artifact-pages-card {
    animation: none;
  }

  .apc-fade-enter-active,
  .apc-fade-leave-active {
    transition: none;
  }
}

.generated-files {
  display: grid;
  max-width: 720px;
  gap: 8px;
  margin: 12px 0 4px;
}

.research-report-stats {
  max-width: 720px;
  margin: 0 0 2px;
  color: #9aa1ad;
  font-size: 12.5px;
  line-height: 1.6;
}

.research-report-card {
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 720px;
  margin: 8px 0 10px;
  overflow: hidden;
  border: 1px solid #eceef2;
  border-radius: 16px;
  background: #fff;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.06);
  animation: plan-card-in 0.55s cubic-bezier(0.22, 0.7, 0.28, 1) both;
}

.research-report-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-bottom: 1px solid #f0f1f4;
  position: relative;
  z-index: 2;
}

.research-report-icon {
  display: grid;
  flex-shrink: 0;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: #3b82f6;
  color: #fff;
  font-size: 15px;
}

.research-report-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: #111827;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.research-report-head-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.research-report-head-actions button {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #4b5563;
  cursor: pointer;
}

.research-report-head-actions button:hover {
  background: #f3f4f6;
  color: #111827;
}

.research-report-head-actions .copy-report-action {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  color: #8b919a;
}

.research-report-head-actions .copy-report-action:hover {
  background: transparent;
  color: #3f3f46;
}

.research-report-head-actions .report-head-ic {
  display: block;
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.research-report-preview {
  position: relative;
}

.research-report-preview.has-markdown {
  max-height: 420px;
  overflow: auto;
  cursor: pointer;
}

.research-report-preview :deep(.ifp),
.research-report-preview :deep(.ifp-media),
.research-report-preview :deep(.ifp-html) {
  margin: 0;
}

.research-report-preview :deep(.ifp-html) {
  height: 420px;
  border: 0;
  border-radius: 0;
}

.research-report-preview :deep(.ifp-frame) {
  height: 560px;
}

.research-report-preview::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 72px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), #fff);
  pointer-events: none;
  z-index: 2;
}

/* 文件卡（2026-07-15 Codex 化）：扁平白底 + 细描边，去渐变与投影 */
.generated-file-card {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 58px;
  padding: 10px 12px;
  border: 1px solid #e9eaee;
  border-radius: 12px;
  background: #fff;
  box-shadow: none;
  transition: border-color 0.15s ease;
  /* 入场上浮淡入（2026-07-20 产物展示升级）：替代硬弹出；多卡按 60ms 阶梯错开
     （delay 由模板内联注入），both 保证延迟期间不闪现 */
  animation: gen-card-in 0.32s cubic-bezier(0.2, 0.7, 0.3, 1) both;
}

@keyframes gen-card-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes plan-card-in {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .generated-file-card,
  .research-report-card,
  .plan-report-card,
  .plan-review-card {
    animation: none;
  }
}

.generated-file-card:hover {
  border-color: #d8dbe2;
  box-shadow: none;
}

.generated-file-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: 9px;
  background: #f0f1f4;
  color: #4b5563;
  font-size: 17px;
}

/* 低饱和类型着色（与「我的文件」同一套 tint）：仅图标底片，不做大面积高饱和 */
.generated-file-icon.k-word { background: #eef2fc; color: #3b5ba5; }
.generated-file-icon.k-excel { background: #ebf6ef; color: #2f8a5b; }
.generated-file-icon.k-pdf { background: #fceeee; color: #c0554f; }
.generated-file-icon.k-ppt { background: #fdf1e7; color: #c07a25; }
.generated-file-icon.k-image { background: #f1eefb; color: #6b52b8; }
.generated-file-icon.k-markdown,
.generated-file-icon.k-text { background: #eef1f4; color: #5b6472; }
.generated-file-icon.k-html { background: #eef2fc; color: #3b5ba5; }
.generated-file-icon.k-archive { background: #f2f0ec; color: #8a7a55; }

.generated-file-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.generated-file-copy strong {
  overflow: hidden;
  color: #202228;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.generated-file-copy em {
  overflow: hidden;
  color: #8a8f99;
  font-size: 11px;
  font-style: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 产物审查徽标：微型胶囊（与执行头状态胶囊同一配色语言），前置状态圆点 */
.review-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 7px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 17px;
  vertical-align: text-bottom;
}

.review-badge::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentcolor;
}

/* 通过=常态，收成中性灰不抢眼（2026-07-15 用户拍板去绿）；提醒/建议复查=琥珀，
   不用红——文件已交付，红色「未通过」只会吓到用户（2026-07-20 拍板降噪） */
.review-badge.review-passed {
  background: #f2f3f5;
  color: #6b7280;
}

.review-badge.review-warning,
.review-badge.review-failed {
  background: #fbf5e9;
  color: #a07a2e;
}

.review-badge.review-unknown {
  background: #f2f3f5;
  color: #8a8f99;
}

.generated-file-actions {
  display: flex;
  flex: none;
  gap: 2px;
}

/* 操作全部收成安静文字钮（Codex 式）：灰字、悬停浅底加深，不再是一排胶囊 */
.generated-file-card button {
  padding: 4px 8px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #6b7280;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.generated-file-card button:hover {
  background: #f2f3f5;
  color: #17181c;
}

.generated-file-card button.generated-file-secondary {
  color: #8a8f99;
}

.generated-file-card button.generated-file-secondary:hover {
  background: #f2f3f5;
  color: #30323a;
}

/* 搜索结果页标题摘要：挂在搜索行或旧轨迹 read 行上，不铺成第二份结果列表。 */
.agent-step-read {
  display: flex;
  align-items: center;
  gap: 5px;
  column-gap: var(--execution-icon-gap);
  font-size: 13.5px;
  color: var(--execution-text);
  line-height: 1.55;
  min-width: 0;
  width: 100%;
  flex-wrap: nowrap;
  transition: color 0.15s ease;
}

.agent-step-read .ast-page-summary,
.agent-step-tool .ast-page-summary {
  flex: 1 1 12ch;
  min-width: 0;
  max-width: min(480px, 52vw);
  overflow: hidden;
  color: var(--execution-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-step-read > .step-node,
.agent-step-artifact > .step-node,
.agent-step-verification > .step-node {
  flex: none;
}

.agent-step-read .ast-page-summary::before,
.agent-step-tool .ast-page-summary::before {
  content: '·';
  margin-right: 5px;
  color: #c2c7d0;
}

/* 子智能体协作节点：图标在卡片内与标题同行对齐 */
.agent-step-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  column-gap: var(--execution-icon-gap);
  min-width: 0;
  flex-wrap: nowrap;
  margin: 2px 0;
  padding: 9px 12px;
  border-radius: 12px;
  background: #f6f7f9;
  font-size: 14px;
  color: #1f2937;
}

.agent-step-sub > .step-node {
  margin-top: 0;
  flex: none;
}

.agent-step-sub.subagent-collab {
  cursor: pointer;
  user-select: none;
}
.agent-step-sub.subagent-collab .collab-toggle {
  margin-left: 8px;
  flex: none;
  font-size: 11.5px;
  color: var(--wf-text-tertiary, #9aa0ab);
}
.agent-step-sub.subagent-collab:hover .collab-toggle {
  color: var(--wf-text-secondary, #5a6070);
}
.agent-step-sub.failed {
  color: var(--execution-text);
  background: #f6f7f9;
}

/* 委派任务引述：左侧细线引用样式，最多两行（完整任务文本悬停 title 可见） */
.agent-step-sub .sub-task {
  display: -webkit-box;
  overflow: hidden;
  flex-basis: 100%;
  margin-top: 4px;
  padding-left: 10px;
  border-left: 2px solid #dfe2e8;
  color: #9ca3af;
  font-size: 12px;
  line-height: 1.65;
  word-break: break-word;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

/* 结果报告卡：markdown 渲染成真排版（标题/要点/段落），白底与灰容器分层；
   过长内部滚动，完整原文仍在「子智能体工作窗口」 */
.agent-step-sub .sub-report {
  flex-basis: 100%;
  margin-top: 8px;
  padding: 12px 15px;
  border: 1px solid #e8eaee;
  border-radius: 10px;
  background: #fff;
  color: #3f4450;
  font-size: 12.5px;
  line-height: 1.75;
  max-height: 420px;
  overflow-y: auto;
  word-break: break-word;
}

.agent-step-sub .sub-report.failed {
  border-color: #e8eaee;
  background: #fbfbfc;
  color: var(--execution-text);
}

/* 只有执行步骤在 hover 时变黑；过程开场说明不在此选择器内，始终保持灰色。 */
.execution-stream :is(
  .agent-step-tool,
  .agent-step-artifact,
  .agent-step-verification,
  .agent-step-read,
  .agent-step-sub,
  .exec-plan-item
):hover,
.execution-stream :is(
  .agent-step-tool,
  .agent-step-artifact,
  .agent-step-verification,
  .agent-step-read,
  .agent-step-sub,
  .exec-plan-item
):hover .ast-label,
.execution-stream .agent-step:hover .step-node,
.execution-stream .exec-plan-item:hover .exec-plan-icon {
  color: var(--execution-text-hover);
}

/* 报告卡内部排版（v-html 注入需 :deep）：紧凑层级，首元素不留头部空白 */
.agent-step-sub .sub-report :deep(h1),
.agent-step-sub .sub-report :deep(h2),
.agent-step-sub .sub-report :deep(h3),
.agent-step-sub .sub-report :deep(h4) {
  margin: 12px 0 6px;
  color: #23272e;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}

.agent-step-sub .sub-report :deep(h1:first-child),
.agent-step-sub .sub-report :deep(h2:first-child),
.agent-step-sub .sub-report :deep(h3:first-child),
.agent-step-sub .sub-report :deep(h4:first-child),
.agent-step-sub .sub-report :deep(p:first-child) {
  margin-top: 0;
}

.agent-step-sub .sub-report :deep(p) {
  margin: 6px 0;
}

.agent-step-sub .sub-report :deep(ul),
.agent-step-sub .sub-report :deep(ol) {
  margin: 6px 0;
  padding-left: 18px;
}

.agent-step-sub .sub-report :deep(li) {
  margin: 3px 0;
}

.agent-step-sub .sub-report :deep(strong) {
  color: #23272e;
  font-weight: 600;
}

.agent-step-sub .sub-report :deep(pre) {
  margin: 6px 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: #f6f7f9;
  overflow-x: auto;
}

.agent-step-sub .sub-report :deep(hr) {
  margin: 10px 0;
  border: 0;
  border-top: 1px solid #eef0f3;
}

/* 正文行内引用角标（v-html 注入，须 :deep）：小圆片，点击打开来源 */
.markdown-body :deep(.cite-chip) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  margin: 0 2px;
  border-radius: 999px;
  background: #eef0f3;
  color: #4b5563;
  font-size: 11px;
  line-height: 1;
  text-decoration: none;
  vertical-align: text-top;
}

.markdown-body :deep(.cite-chip:hover) {
  background: #dfe3e8;
  color: #111;
}

/* 开场白：正文体渲染在执行时间线上方（ChatGPT 式「先答一句再干活」） */
/* .preamble-body 的排版与 .agent-step-note 统一定义（见下方 方案A v2 注释） */

/* 图片编号来自消息引用快照；加载失败保留来源和重试，不静默吞图。 */
.markdown-body :deep(figure.msg-figure) {
  margin: 10px 0 14px;
  max-width: min(100%, 420px);
  border: 1px solid #e8eaee;
  border-radius: 14px;
  overflow: hidden;
  background: #fff;
}

.markdown-body :deep(.msg-image-open) {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: #fff;
  cursor: zoom-in;
}

.markdown-body :deep(.msg-image-open:focus-visible) {
  outline: 2px solid #7786d9;
  outline-offset: -3px;
}

.markdown-body :deep(figure.msg-figure img) {
  display: block;
  width: 100%;
  max-height: 320px;
  object-fit: cover;
  cursor: zoom-in;
}

.markdown-body :deep(.msg-image-fallback) {
  display: none;
  min-height: 96px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  background: #f7f8fa;
  color: #737984;
  font-size: 13px;
  line-height: 1.5;
}

.markdown-body :deep(.is-image-error .msg-image-open) {
  display: none;
}

.markdown-body :deep(.is-image-error .msg-image-fallback),
.markdown-body :deep(.is-image-unavailable .msg-image-fallback) {
  display: flex;
}

.markdown-body :deep(.msg-image-retry) {
  min-height: 40px;
  padding: 5px 14px;
  border: 1px solid #dfe3e8;
  border-radius: 8px;
  background: #fff;
  color: #3f4652;
  cursor: pointer;
  font: inherit;
}

.markdown-body :deep(figure.msg-figure figcaption) {
  padding: 6px 10px;
  font-size: 12px;
  line-height: 1.4;
  color: #8a8f9c;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.markdown-body :deep(figure.msg-figure figcaption a) {
  color: inherit;
  text-decoration: none;
}

.markdown-body :deep(figure.msg-figure figcaption a:hover) {
  color: #40454f;
}

.markdown-body :deep(figure.msg-figure-float) {
  float: right;
  width: 232px;
  margin: 4px 0 12px 16px;
}

.campus-answer-layout .message-bubble:not(.preamble-body) {
  line-height: 1.85;
}

.campus-answer-layout .message-bubble:not(.preamble-body) :deep(p) {
  margin-bottom: 14px;
}

.campus-answer-layout .message-bubble:not(.preamble-body) :deep(li) {
  margin-block: 6px;
}

.campus-answer-layout .markdown-body :deep(figure.msg-figure) {
  float: none;
  width: 100%;
  max-width: min(100%, 560px);
  margin: 16px 0 20px;
}

.campus-answer-layout .markdown-body :deep(figure.msg-figure img) {
  height: auto;
  max-height: none;
  object-fit: contain;
}

.campus-answer-layout .markdown-body :deep(figure.msg-figure figcaption) {
  white-space: normal;
  overflow-wrap: anywhere;
}

/* 浮动图不得溢出气泡：正文容器收尾清除浮动 */
.message-bubble.markdown-body::after {
  content: '';
  display: block;
  clear: both;
}

@media (max-width: 640px) {
  .markdown-body :deep(figure.msg-figure-float) {
    float: none;
    width: 100%;
    margin: 10px 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .rb-chevron {
    transition: none;
  }
}

.tool-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.tool-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  font-size: 12px;
  color: #4b5563;
  background: #f9fafb;
}

/* 成员身份只在任务真实交给子智能体后进入时间线；不挂消息头、不使用动画。 */
.subagent-member-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 8px;
  min-height: 28px;
}

.sub-team-pill {
  display: inline-flex;
  align-items: center;
  min-height: 28px;
  gap: 6px;
  padding: 3px 10px 3px 4px;
  border: 1px solid #e7e8eb;
  border-radius: 999px;
  background: #fff;
  font-size: 14px;
  line-height: 20px;
  color: #74777d;
  cursor: pointer;
}

.sub-team-pill:hover,
.sub-team-pill:focus-visible {
  border-color: #d9dbe0;
  color: #4d5159;
}

.sub-team-pill .pill-agent-avatar {
  display: grid;
  width: 20px;
  height: 20px;
  flex: none;
  overflow: hidden;
  place-items: center;
  border-radius: 6px;
  background: #f1f0f8;
}

.sub-team-pill .pill-agent-avatar img {
  display: block;
  width: 16px;
  height: 16px;
  object-fit: cover;
  border-radius: 4px;
}

.subagent-member-error {
  color: #8b919a;
  font-size: 12px;
}

.subagent-calls {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.subagent-call {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 999px;
  font-size: 12px;
  color: #4b5563;
  background: #f9fafb;
}

.subagent-call.completed {
  color: #374151;
  border-color: #d1d5db;
}

.subagent-call.failed {
  color: #4b5563;
  border-color: #d8dbe2;
  background: #f7f8fa;
}

/* 正文上方「已阅读 N 个网页」入口：放大镜 + 文案 + 层叠站点图标，点击开右侧搜索结果 */
.read-sources {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 0 0 12px;
  padding: 5px 12px 5px 10px;
  border: 1px solid #ebecf0;
  border-radius: 999px;
  background: #fff;
  color: #5b6069;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.read-sources:hover {
  border-color: #d4d6dc;
  background: #f7f8fa;
  color: #111827;
}

.read-sources .rs-icon {
  font-size: 14px;
  color: #9098a3;
  transition: color 0.15s ease;
}

.read-sources:hover .rs-icon {
  color: #111827;
}

.read-sources .rs-label {
  font-weight: 500;
}

/* 层叠的站点图标：负边距 + 白色描边环，像叠在一起的照片 */
.read-sources .rs-stack {
  display: inline-flex;
  align-items: center;
  margin-left: 3px;
}

.read-sources .rs-favicon {
  width: 18px;
  height: 18px;
  margin-left: -6px;
  border: 1.5px solid #fff;
  border-radius: 50%;
  background: #eef0f3;
  object-fit: cover;
  box-shadow: 0 0 0 1px rgba(17, 24, 39, 0.05);
}

.read-sources .rs-favicon:first-child {
  margin-left: 0;
}

.hitl-card {
  margin-top: 12px;
  border: 1px solid #e4e4e8;
  border-radius: 12px;
  background: #fafafb;
  padding: 14px 16px;
}

.hitl-desc {
  margin: 0 0 12px;
  color: #292c33;
  font-size: 14px;
  font-weight: 600;
}

.hitl-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.hitl-options button {
  border: 1px solid #d7d8dd;
  border-radius: 8px;
  background: #fff;
  padding: 7px 14px;
  color: #202228;
  font-size: 13px;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background 0.15s ease;
}

.hitl-options button:hover {
  border-color: #111;
  background: #111;
  color: #fff;
}

/* ===== 计划确认卡：同一张文稿白卡 + 卡内补充 +「开始执行」 ===== */
.plan-review-card {
  padding: 0 0 12px;
}

.plan-review-compose {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 14px 0;
  padding: 4px 4px 4px 12px;
  border: 1px solid #d9d9d9;
  border-radius: 10px;
}

.plan-review-compose:focus-within {
  border-color: #818cf8;
  box-shadow: none;
}

.plan-review-compose input {
  min-width: 0;
  flex: 1;
  border: 0;
  background: transparent;
  padding: 8px 0;
  color: #111;
  font-size: 14px;
  outline: none;
}

.plan-review-compose input::placeholder {
  color: #b3b3b3;
}

.plan-review-compose button {
  flex: none;
  border: 1px solid #111;
  border-radius: 9px;
  background: #111;
  padding: 7px 16px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.plan-review-compose button:disabled {
  opacity: 0.35;
  cursor: default;
}

.plan-review-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  margin: 10px 14px 0;
}

.plan-review-skip {
  margin-right: auto;
  border: 0;
  background: transparent;
  padding: 8px 10px;
  color: #888;
  font-size: 13px;
  cursor: pointer;
}

.plan-review-skip:hover {
  color: #111;
}

.plan-review-skip:disabled {
  opacity: 0.35;
  cursor: default;
}

.plan-review-go {
  border: 1px solid #111;
  border-radius: 9px;
  background: #111;
  padding: 8px 18px;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.plan-review-go:disabled {
  opacity: 0.35;
  cursor: default;
}

.ask-card {
  max-width: 560px;
  padding: 0;
  overflow: hidden;
}

.ask-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 16px;
}

.ask-title {
  color: #111;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
}

.ask-fold {
  flex: none;
  border: 0;
  background: transparent;
  color: #9096a1;
  padding: 2px 4px;
  cursor: pointer;
}

.ask-fold:hover {
  color: #111;
}

.ask-fold-icon {
  font-size: 11px;
  transition: transform 0.2s ease;
}

.ask-fold-icon.open {
  transform: rotate(180deg);
}

.ask-options {
  display: flex;
  flex-direction: column;
}

/* 整行选项：标题 + 灰色说明 + 右侧序号，悬停整行浅底 */
.ask-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  border: 0;
  border-top: 1px solid #ececf0;
  background: transparent;
  padding: 10px 16px;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease;
}

.ask-option:hover {
  background: #ededf0;
}

.ask-option-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.ask-option-copy strong {
  color: #111;
  font-size: 14px;
  font-weight: 500;
}

.ask-option-copy em {
  color: #8a8f9c;
  font-size: 12.5px;
  font-style: normal;
}

.ask-option-num {
  flex: none;
  color: #b3b7c0;
  font-size: 12px;
}

/* 自由输入 + 跳过（仅消歧卡） */
.ask-free {
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #ececf0;
  padding: 10px 16px 12px;
}

.ask-free input {
  flex: 1;
  min-width: 0;
  border: 1px solid #e0e1e6;
  border-radius: 8px;
  background: #fff;
  padding: 7px 12px;
  font-size: 13px;
  outline: none;
}

.ask-free input:focus {
  border-color: #818cf8;
  box-shadow: none;
}

.ask-free-actions {
  display: flex;
  flex: none;
  gap: 6px;
}

.ask-skip {
  border: 0;
  background: transparent;
  color: #9096a1;
  font-size: 13px;
  padding: 6px 8px;
  cursor: pointer;
}

.ask-skip:hover {
  color: #111;
}

.ask-submit {
  border: 1px solid #111;
  border-radius: 8px;
  background: #111;
  color: #fff;
  font-size: 13px;
  padding: 6px 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.ask-submit:not(:disabled):hover {
  background: #303035;
}

.ask-submit:disabled {
  border-color: #d0d0d4;
  background: #c8c8cc;
  cursor: not-allowed;
}

/* formInput 表单在新卡壳内需要自己的内边距（卡壳 padding 已归零） */
.ask-card .hitl-form-wrap {
  padding: 4px 16px 14px;
}

/* 表单卡头改纵排：标题下带一行「来自子智能体「××」」归属语境 */
.ask-head-form {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
}

.ask-source {
  color: #9096a1;
  font-size: 12px;
  font-weight: 400;
}

.approval-card {
  border-color: #f0d8a8;
  background: #fdf9f0;
}

.approval-card .approve-yes:hover {
  border-color: #1f7a3d;
  background: #1f7a3d;
}

.approval-card .approve-no:hover {
  border-color: #b23b3b;
  background: #b23b3b;
}

.external-rec-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.external-rec-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 12px;
  border: 1px solid #d7d8dd;
  border-radius: 8px;
  background: #fff;
  color: #202228;
  text-decoration: none;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.external-rec-item:hover {
  border-color: #111;
  background: #f6f6f7;
}

.external-rec-name {
  font-weight: 600;
  font-size: 13px;
}

.external-rec-desc {
  flex: 1;
  color: #6b7079;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.external-rec-action {
  margin-left: auto;
  color: #1f5fbf;
  font-size: 12px;
  white-space: nowrap;
}

.message-actions {
  display: flex;
  gap: 4px;
  margin-top: 8px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

/* Codex/Lucide 同源线框图标：stroke 2，不用 Ant 实心感 */
.message-actions .msg-action-ic {
  width: 16px;
  height: 16px;
  display: block;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}


.message:hover .message-actions,
.message-actions:focus-within {
  opacity: 1;
}

.message-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #8a8f99;
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.message-actions button:hover {
  background: #f0f0f2;
  color: #202228;
}

.message-actions button.active {
  color: #111;
}

/* ===== 生成错误提示（run.failed / error 事件）：克制、可行动，不伪装成模型回答 ===== */
.message-error {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: fit-content;
  max-width: min(680px, 100%);
  box-sizing: border-box;
  margin-top: 8px;
  padding: 12px 14px;
  border: 1px solid #dfe2e7;
  border-radius: 12px;
  background: #fafbfc;
  color: #555d69;
  font-size: 13px;
  line-height: 1.6;
}

.message-error-policy {
  border-color: #e8dfce;
  background: #fcfaf5;
}

.message-error-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #eceff3;
  color: #626b78;
  font-size: 12px;
}

.message-error-policy .message-error-icon {
  background: #f2e8d2;
  color: #79571e;
}

.message-error-copy {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.message-error-copy strong {
  color: #2e333b;
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.55;
}

.message-error-copy > span {
  overflow-wrap: anywhere;
}

@keyframes message-enter {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message-agent-recs {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 380px));
  gap: 12px;
  margin-top: 18px;
}

.agent-rec-intro {
  grid-column: 1 / -1;
  margin: 0 0 -2px;
  color: #6f7480;
  font-size: 12px;
  line-height: 1.6;
}

.agent-rec-card {
  position: relative;
  display: grid;
  grid-template-columns: 52px minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 104px;
  overflow: hidden;
  border: 1px solid #e2e3e8;
  border-radius: 16px;
  background: linear-gradient(135deg, #fff 0%, #fafafd 100%);
  padding: 15px 16px;
  color: #202228;
  cursor: pointer;
  text-align: left;
  transition:
    border-color 0.2s ease,
    box-shadow 0.2s ease,
    transform 0.2s ease;
}

.agent-rec-card::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 3px;
  background: #111;
  content: '';
  opacity: 0;
  transform: scaleY(0.45);
  transition:
    opacity 0.2s ease,
    transform 0.2s ease;
}

.agent-rec-card:hover {
  border-color: #cfd1d8;
  box-shadow: none;
  transform: translateY(-3px);
}

.agent-rec-card:hover::before {
  opacity: 1;
  transform: scaleY(1);
}

.agent-rec-icon {
  position: relative;
  display: grid;
  width: 52px;
  height: 52px;
  overflow: hidden;
  place-items: center;
  border: 1px solid #e5e7ec;
  border-radius: 14px;
  background: #f5f6f8;
}

.agent-rec-icon img {
  position: relative;
  z-index: 1;
  width: auto;
  height: auto;
  max-width: 40px;
  max-height: 40px;
  object-fit: contain;
  border-radius: 9px;
  background: transparent;
  image-rendering: auto;
}

.agent-rec-icon-fallback {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #17181c, #353841);
  color: #fff;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: -0.03em;
}

.agent-rec-copy {
  min-width: 0;
}

.agent-rec-eyebrow {
  display: block;
  margin-bottom: 4px;
  color: #8a8f99;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.12em;
}

.agent-rec-card strong {
  display: block;
  overflow: hidden;
  color: #17181c;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.agent-rec-card em {
  display: -webkit-box;
  margin-top: 5px;
  overflow: hidden;
  color: #777d88;
  font-size: 12px;
  font-style: normal;
  line-height: 1.5;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.agent-rec-action {
  display: inline-flex;
  min-height: 34px;
  align-items: center;
  gap: 6px;
  border-radius: 10px;
  background: #f0f0f2;
  padding: 0 10px;
  color: #4b5059;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  transition:
    background 0.2s ease,
    color 0.2s ease,
    transform 0.2s ease;
}

.agent-rec-card:hover .agent-rec-action {
  background: #111;
  color: #fff;
  transform: translateX(2px);
}

.agent-rec-action :deep(.anticon) {
  font-size: 11px;
}

@media (prefers-reduced-motion: reduce) {
  .message {
    animation: none;
  }

  .agent-rec-card,
  .agent-rec-card::before,
  .agent-rec-action {
    transition: none;
  }

  .agent-rec-card:hover,
  .agent-rec-card:hover::before,
  .agent-rec-card:hover .agent-rec-action {
    transform: none;
  }
}

@media (max-width: 720px) {
  .generated-file-card {
    grid-template-columns: 34px minmax(0, 1fr) auto;
    gap: 8px;
    padding: 9px;
  }

  .generated-file-icon {
    width: 34px;
    height: 34px;
  }

  .generated-file-copy em {
    max-width: 150px;
  }

  .message-list {
    padding: 18px 2px calc(176px + env(safe-area-inset-bottom));
  }

  .message {
    grid-template-columns: minmax(0, 1fr);
  }

  .message.user .message-content {
    max-width: 92%;
  }

  .message-edit {
    width: min(88vw, 560px);
  }

  .message-bubble,
  .message-content {
    overflow-wrap: anywhere;
  }

  .message-agent-recs {
    grid-template-columns: minmax(0, 1fr);
  }

  .agent-rec-card {
    grid-template-columns: 46px minmax(0, 1fr) 32px;
    gap: 11px;
    min-height: 92px;
    padding: 13px;
  }

  .agent-rec-icon {
    width: 46px;
    height: 46px;
    border-radius: 12px;
  }

  .agent-rec-action {
    width: 32px;
    min-height: 32px;
    justify-content: center;
    padding: 0;
  }

  .agent-rec-action > span {
    display: none;
  }
}

/* 触屏没有稳定 hover；复制、编辑、重新生成必须直接可见且可点。 */
@media (max-width: 720px) and (hover: none) {
  .message-actions {
    gap: 2px;
    margin-top: 4px;
    opacity: 1;
  }

  .message-actions button,
  .message-actions.user-actions button {
    width: 40px;
    height: 40px;
    border-radius: 10px;
  }

  .message-actions .msg-action-ic,
  .message-actions.user-actions .msg-action-ic {
    width: 17px;
    height: 17px;
  }
}

/* ===== 代码块复制按钮（注入到 v-html 内，故用 :deep） ===== */
/* 豆包式代码块：浅色 + 顶部头栏（语言名 + 复制） */
/* 注意：不能用 overflow: hidden——否则头栏的 position: sticky 会失效（裁剪祖先会成为
   sticky 容器）。改由头栏与 pre 各自承担上/下圆角，视觉一致但头栏可吸顶。 */
.message-bubble :deep(.code-block) {
  margin: 14px 0;
  border: 1px solid #e6e8eb;
  border-radius: 12px;
  background: #fff;
}

/* 头栏吸顶：长代码/内容滚动时钉在可视区顶部，复制/下载始终可点，无需滚回框顶。 */
.message-bubble :deep(.code-block-head) {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 38px;
  padding: 0 8px 0 14px;
  border-bottom: 1px solid #eceef1;
  border-radius: 12px 12px 0 0;
  background: #f6f7f9;
  color: #6b7280;
  font-size: 12px;
}

.message-bubble :deep(.code-lang) {
  font-family: 'SFMono-Regular', Consolas, monospace;
  text-transform: lowercase;
  letter-spacing: 0.02em;
}

/* 复制 + 下载按钮组，右上角并排 */
.message-bubble :deep(.code-actions) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* 头栏内的代码块去掉自身上边框/圆角，底部圆角接住外层 .code-block */
.message-bubble :deep(.code-block pre.hljs) {
  margin: 0;
  border: 0;
  border-radius: 0 0 12px 12px;
  background: #fff;
}

.message-bubble :deep(.code-copy-btn),
.message-bubble :deep(.code-download-btn) {
  padding: 3px 10px;
  border: 1px solid #e0e2e7;
  border-radius: 6px;
  background: #fff;
  color: #57606a;
  font-size: 12px;
  line-height: 1.5;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.message-bubble :deep(.code-copy-btn:hover),
.message-bubble :deep(.code-download-btn:hover) {
  border-color: #c9ccd3;
  background: #eef0f2;
  color: #111;
}

/* ===== 产物卡片（点击在右侧面板打开，Claude 式） ===== */
.message-bubble :deep(.artifact-card) {
  display: flex;
  align-items: center;
  gap: 13px;
  margin: 12px 0;
  padding: 13px 15px;
  border: 1px solid #e7e8ee;
  border-radius: 14px;
  background: #fff;
  white-space: normal;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}

.message-bubble :deep(.artifact-card:hover) {
  border-color: #c8cad0;
  box-shadow: none;
  transform: translateY(-1px);
}

.message-bubble :deep(.artifact-card-icon) {
  display: grid;
  width: 40px;
  height: 40px;
  flex: none;
  place-items: center;
  border: 1px solid #e4e6ea;
  border-radius: 11px;
  background: #f2f3f5;
  color: #111827;
  transition: border-color 0.18s ease;
}

.message-bubble :deep(.artifact-card:hover .artifact-card-icon) {
  border-color: #c8cad0;
}

.message-bubble :deep(.artifact-card-body) {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.message-bubble :deep(.artifact-card-body strong) {
  overflow: hidden;
  color: #14151a;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.message-bubble :deep(.artifact-card-body em) {
  color: #8c92a0;
  font-size: 12.5px;
  font-style: normal;
}

.message-bubble :deep(.artifact-card-open) {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  flex: none;
  padding: 5px 11px 5px 12px;
  border-radius: 999px;
  background: #f0f1f3;
  color: #111827;
  font-size: 12.5px;
  font-weight: 500;
  transition: background 0.18s ease, color 0.18s ease;
}

.message-bubble :deep(.artifact-card:hover .artifact-card-open) {
  background: #111827;
  color: #fff;
}

/* ===== 右侧对话导航 ===== */
.conversation-nav {
  position: fixed;
  right: 14px;
  top: 50%;
  z-index: 900;
  display: flex;
  flex-direction: column;
  gap: 9px;
  max-height: 62vh;
  padding: 8px 6px;
  overflow: hidden auto;
  transform: translateY(-50%);
  scrollbar-width: none;
}

.conversation-nav::-webkit-scrollbar {
  display: none;
}

.conv-nav-dot {
  position: relative;
  width: 8px;
  height: 8px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: #d3d5db;
  cursor: pointer;
  transition: background 0.15s ease, transform 0.15s ease;
}

.conv-nav-dot:hover {
  background: #9aa0ac;
  transform: scale(1.3);
}

.conv-nav-dot.active {
  background: #111827;
  transform: scale(1.4);
}

.conv-nav-tip {
  position: absolute;
  right: 18px;
  top: 50%;
  max-width: 260px;
  padding: 5px 9px;
  border-radius: 7px;
  background: #111;
  color: #fff;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: 0;
  pointer-events: none;
  transform: translateY(-50%);
  transition: opacity 0.15s ease;
}

.conv-nav-dot:hover .conv-nav-tip {
  opacity: 1;
}

/* 跳转后短暂高亮目标消息 */
.message.message-flash .message-bubble {
  animation: msg-flash 1.2s ease;
}

@keyframes msg-flash {
  0% {
    box-shadow: 0 0 0 3px rgba(17, 24, 39, 0.28);
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
}

/* 窄屏没有富余空间，隐藏导航条 */
@media (max-width: 1180px) {
  .conversation-nav {
    display: none;
  }
}
</style>
