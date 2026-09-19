<template>
  <section
    :class="[
      'chat-home',
      {
        'empty-state': chatMessages.length === 0 && !(interviewMode && interview?.hasSession.value),
        'presentation-state': presentationMode,
        'campus-state': campusMode,
        'interview-state': interviewMode,
        'interview-conversation': interviewMode && chatMessages.length > 0,
        'interview-practice': interviewMode && interviewSurfaceOpen,
        'builtin-state': Boolean(builtinAssistant) && Boolean(uiPolicy?.showAvatar),
        'work-welcome': !builtinAssistant && !presentationMode && !interviewMode,
        [uiPolicy?.emptyStateClass || '']: Boolean(uiPolicy?.emptyStateClass),
      },
    ]"
  >
    <!-- 首屏：内置智能体欢迎文案/形象来自 builtinAssistants registry 的 uiPolicy。 -->
    <div v-if="chatMessages.length === 0 && !interview?.hasSession.value" class="chat-intro" :aria-busy="restoringHistory || undefined">
      <WorkAgentMascot
        v-if="uiPolicy?.showAvatar && builtinAssistant"
        class="chat-intro-agent-mascot"
        :state="workAgentMascotState"
        :variant="mascotVariant"
        :label="mascotLabel"
      />
      <h1>{{ presentationMode ? '你好，今天想制作什么演示文稿？' : (uiPolicy?.welcomeTitle || '你好，今天想完成什么？') }}</h1>
      <p v-if="!interviewMode" :class="{ 'work-welcome-description': !presentationMode && !uiPolicy?.welcomeSubtitle }">
        <template v-if="presentationMode">告诉我主题、受众和使用场景，我会帮你梳理内容、设计版式并生成可编辑的演示文稿。</template>
        <template v-else-if="uiPolicy?.welcomeSubtitle">{{ uiPolicy.welcomeSubtitle }}</template>
        <template v-else>
          <span class="work-welcome-primary">制作与优化 PPT、撰写研究报告、整理与编辑 Word 文档</span>
          <span class="work-welcome-secondary">也可以检索资料、分析 Excel 表格、提炼长文要点，或将图片整理成 PDF。</span>
        </template>
      </p>
      <p v-if="uiPolicy?.welcomeHint && !presentationMode" class="chat-intro-hint">{{ uiPolicy.welcomeHint }}</p>
      <p v-if="!presentationMode && !builtinAssistant" class="chat-intro-hint">可输入 <strong>@</strong> 使用 Skill</p>
    </div>
    <InterviewSetup v-if="interviewMode && interview && !interview.hasSession.value" v-show="!chatMessages.length || !interview.busy.value" />

    <MessageList
      v-if="chatMessages.length > 0"
      ref="messageListRef"
      :messages="chatMessages"
      :loading="loading"
      :retry-attachments="retryAttachments"
      :hide-source-citations="campusMode"
      :preserve-answers="interviewMode"
      :answer-layout="campusMode ? 'campus' : 'standard'"
      :meter-copy="interviewMode ? 'interview' : 'standard'"
      @open-agent="(app) => emit('openAgent', app)"
      @regenerate="(modelId) => emit('regenerate', modelId)"
      @feedback="(id, value) => emit('feedback', id, value)"
      @resume="(id, val) => emit('resume', id, val)"
      @edit="(id, content) => emit('edit', id, content)"
      @approve="(id, approved) => emit('approve', id, approved)"
      @scroll-state="(atBottom) => (chatAtBottom = atBottom)"
      @open-artifact="onOpenArtifact"
      @artifacts="onArtifacts"
      @preview-file="onPreviewFile"
      @show-versions="versionsFile = $event"
      @ai-edit-file="(f, p) => emit('aiEditFile', f, p)"
      @save-slides="(sf, df, pages, done) => emit('saveSlides', sf, df, pages, done)"
    />

    <InterviewPanel
      v-if="interviewMode && (chatMessages.length > 0 || interview?.hasSession.value)"
      ref="interviewPanelRef"
      :has-conversation="chatMessages.length > 0"
      :show-actions="!interviewComposerVisible"
      :style="{ marginBottom: `${interviewComposerVisible ? Math.max(160, interviewComposerHeight + 40) : 40}px` }"
      @surface="interviewSurfaceOpen = $event"
    />

    <div v-if="interviewComposerVisible" ref="interviewComposerRef" class="composer-dock">
    <div
      :class="[
        'composer',
        'chat-composer',
        {
          'has-skill': selectedSkills.length || webSearch,
          'drag-active': dragActive,
          'task-mode-active': planMode,
          'task-mode-running': planMode && loading,
          'research-mode-active': researchProfile,
          'has-message-queue': messageQueue.length || queueLoading,
        },
      ]"
      @dragover.prevent="dragActive = true"
      @dragleave="dragActive = false"
      @drop.prevent="onDrop"
    >
      <WorkAgentMascot
        v-if="!uiPolicy?.showAvatar && !uiPolicy?.hideComposerMascot && chatMessages.length === 0"
        class="composer-agent-mascot"
        :state="workAgentMascotState"
        :variant="mascotVariant"
        :label="mascotLabel"
      />
      <!-- 生成中的环绕光晕（2026-07-27 用户拍板 B 方案）：一道蓝光沿输入框边框绕行 +
           一层外发光跟随。只在**真正生成**时亮——暂停/等待补充不亮（那两个状态由执行头
           文案负责），也不跟随停止键↔发送键的切换：光晕说的是「模型在跑」，与你有没有
           草稿无关。纯装饰层，aria-hidden 且不吃指针事件。 -->
      <div v-if="loading" class="composer-glow" aria-hidden="true">
        <span class="cg-halo"></span>
        <span class="cg-ring"></span>
      </div>
      <button
        v-if="!interviewMode && chatMessages.length && !chatAtBottom"
        type="button"
        class="chat-scroll-down"
        title="回到最新消息"
        @click="messageListRef?.scrollToBottom(true)"
      >
        <ArrowDownOutlined />
      </button>

      <!-- 运行中消息队列：回车默认入队；点「调整方向」注入当前 Run。 -->
      <div v-if="messageQueue.length || queueLoading" class="message-queue" aria-label="待发送消息队列">
        <div v-if="queueLoading && !messageQueue.length" class="message-queue-loading">
          <LoadingOutlined /> 正在加载队列…
        </div>
        <template v-else>
          <!-- 中断后队列暂停：停止＝改主意，不该接着把排队消息一股脑冲出去。
               队列原样保留，由用户决定继续还是先改改。 -->
          <div v-if="queuePaused && messageQueue.length" class="message-queue-paused">
            <span>由于你中断了当前响应，队列已暂停</span>
            <button type="button" class="message-queue-resume" @click="resumeQueue">继续</button>
          </div>
          <draggable
            v-model="queueDragPreview"
            item-key="id"
            tag="div"
            :class="['message-queue-list', { 'drag-active': Boolean(queueDragId) }]"
            handle=".queue-drag-handle"
            :disabled="!queueCanReorder"
            :animation="queueSortAnimationMs"
            easing="cubic-bezier(0.22, 1, 0.36, 1)"
            :force-fallback="true"
            :fallback-on-body="true"
            :fallback-tolerance="3"
            :swap-threshold="0.55"
            :scroll="true"
            :scroll-sensitivity="42"
            :scroll-speed="10"
            ghost-class="queue-sort-ghost"
            chosen-class="queue-sort-chosen"
            drag-class="queue-sort-drag"
            fallback-class="queue-sort-fallback"
            @start="onQueueDragStart"
            @end="onQueueDragEnd"
          >
            <template #item="{ element: item }">
              <div
                :data-queue-id="item.id"
                class="queue-card"
              >
              <button
                type="button"
                class="queue-drag-handle"
                :disabled="!queueCanReorder || editingQueueId === item.id"
                title="拖动调整发送顺序"
                aria-label="拖动调整发送顺序"
                :aria-grabbed="queueDragId === item.id"
                @keydown.up.prevent="moveQueueItemByKeyboard(item.id, -1)"
                @keydown.down.prevent="moveQueueItemByKeyboard(item.id, 1)"
              >
                <HolderOutlined />
              </button>
              <template v-if="editingQueueId === item.id">
                <textarea
                  v-model="editingQueueText"
                  class="queue-edit-input"
                  rows="1"
                  :disabled="queueBusy"
                  @keydown.enter.exact="onQueueEditEnter"
                  @keydown.esc="cancelQueueEdit"
                />
                <div class="queue-card-actions">
                  <button type="button" :disabled="queueBusy || !editingQueueText.trim()" @click="commitQueueEdit">保存</button>
                  <button type="button" :disabled="queueBusy" @click="cancelQueueEdit">取消</button>
                </div>
              </template>
              <template v-else>
                <!-- 派发失败的那一条自己出警告图标（Codex isMessagePaused 逐条粒度）：
                     tooltip 两行＝是什么 + 怎么办，与 Codex 文案逐字一致 -->
                <a-tooltip v-if="item.id === failedQueueItemId" placement="top">
                  <template #title>
                    <div style="text-align: center; line-height: 1.6;">
                      <div>这条排队中的消息未能发送</div>
                      <div style="opacity: 0.72;">重试、编辑或删除该消息以继续发送排队的消息</div>
                    </div>
                  </template>
                  <span class="queue-card-warn" aria-label="这条排队中的消息未能发送">
                    <ExclamationCircleOutlined />
                  </span>
                </a-tooltip>
                <p class="queue-card-text">{{ item.content }}</p>
                <div class="queue-card-actions">
                  <span v-if="item.attachments?.length" class="queue-card-atts">附件 {{ item.attachments.length }}</span>
                  <a-tooltip placement="top">
                    <template #title>
                      <div v-if="item.id === failedQueueItemId" style="text-align: center; line-height: 1.6;">
                        <div>尝试重新发送这条排队中的消息</div>
                        <div style="opacity: 0.72;">如果重试一直失败，可编辑或删除它</div>
                      </div>
                      <span v-else>立即插入当前任务，不等本轮结束</span>
                    </template>
                    <button
                      v-if="item.id === failedQueueItemId"
                      type="button"
                      class="queue-guide-action"
                      :disabled="queueBusy"
                      @click="retryQueueDispatch"
                    >
                      <RedoOutlined />
                      <span>重试</span>
                    </button>
                    <button
                      v-else
                      type="button"
                      class="queue-guide-action"
                      :disabled="queueBusy || submittingRunInput || !canInstruct"
                      aria-label="调整方向"
                      @click="instructQueueItem(item.id)"
                    >
                      <UndoOutlined />
                      <span>调整方向</span>
                    </button>
                  </a-tooltip>
                  <button
                    type="button"
                    class="queue-icon-action"
                    :disabled="queueBusy"
                    title="删除这条排队消息"
                    aria-label="删除这条排队消息"
                    @click="removeQueueItem(item.id)"
                  >
                    <DeleteOutlined />
                  </button>
                  <a-dropdown
                    :trigger="['click']"
                    placement="bottomRight"
                    :disabled="queueBusy"
                    overlay-class-name="queue-action-dropdown"
                    :overlay-style="{ minWidth: '132px' }"
                  >
                    <button
                      type="button"
                      class="queue-icon-action queue-more-action"
                      :disabled="queueBusy"
                      title="更多排队操作"
                      aria-label="更多排队操作"
                      @click.stop
                    >
                      <EllipsisOutlined />
                    </button>
                    <template #overlay>
                      <a-menu>
                        <a-menu-item @click="startQueueEdit(item)">
                          <EditOutlined />
                          <span>编辑消息</span>
                        </a-menu-item>
                        <a-menu-item @click="moveQueueItemToInput(item.id)">
                          <RollbackOutlined />
                          <span>移回输入框</span>
                        </a-menu-item>
                        <!-- 旁路（Codex openInSideChat）：这条与主线无关，丢到独立面板单独回答，
                             主线一点不受影响。开完就把它从队列摘掉，免得本轮结束又发一次。 -->
                        <a-menu-item @click="openInSideChat(item)">
                          <CodeSandboxOutlined />
                          <span>在旁路会话中打开</span>
                        </a-menu-item>
                        <!-- 跟进行为开关（Codex 的 启用队列模式/关闭排队 就在这个菜单里）：
                             改的是「以后运行中按回车默认怎么处理」，不影响这条已排的消息 -->
                        <a-menu-item @click="setFollowUpMode(followUpMode === 'queue' ? 'steer' : 'queue')">
                          <EnterOutlined />
                          <span>{{ followUpMode === 'queue' ? '关闭排队' : '启用排队' }}</span>
                        </a-menu-item>
                      </a-menu>
                    </template>
                  </a-dropdown>
                </div>
              </template>
              </div>
            </template>
          </draggable>
        </template>
      </div>

      <div v-if="hasComposerChips" class="composer-attachments">
        <AttachmentCard
          v-for="(att, idx) in attachments"
          :key="'att-' + idx"
          :attachment="att"
          removable
          retriable
          @remove="emit('removeAttachment', idx)"
          @retry="emit('retryAttachment', idx)"
          @preview="lightboxSrc = $event"
        />
        <AttachmentCard
          v-for="thread in selectedThreadList"
          :key="'thread-' + thread.id"
          :attachment="{ filename: thread.title || '未命名对话', kind: 'thread_ref' }"
          removable
          @remove="removeSelectedThread(thread.id)"
        />
        <AttachmentCard
          v-for="file in selectedFileList"
          :key="'file-' + file.id"
          :attachment="{ filename: file.filename, kind: 'user_file', fileId: file.id }"
          removable
          @remove="removeSelectedFile(file.id)"
          @preview="lightboxSrc = $event"
        />
        <AttachmentCard
          v-for="kb in selectedKnowledgeList"
          :key="'kb-' + kb.id"
          :attachment="{ filename: kb.name, kind: 'knowledge' }"
          removable
          @remove="removeSelectedKnowledge(kb.id)"
        />
        <template v-if="!presentationMode && !uiPolicy?.hideSkillSelector">
          <AttachmentCard
            v-for="skill in selectedSkills"
            :key="'skill-' + skill.id"
            :attachment="{ filename: skill.name, kind: 'skill' }"
            removable
            @remove="emit('removeSkill', skill.id)"
          />
        </template>
        <AttachmentCard
          v-if="webSearch"
          :attachment="{ filename: '网页搜索', kind: 'web' }"
          removable
          @remove="emit('toggleWeb')"
        />
      </div>

      <div v-if="mentionOpen && !presentationMode && !uiPolicy?.hideMention" ref="mentionListRef" class="mention-picker" role="listbox">
        <div v-if="!mentionItems.length" class="mention-empty">
          {{ skills.length ? '没有匹配的 Skill' : '暂无可用的 Skill' }}
        </div>
        <template v-else>
          <div ref="mentionScrollRef" class="mention-scroll" @scroll.passive="updateMentionFade">
            <template v-if="filteredSkills.length">
              <div class="mention-group-label">Skill</div>
              <button
                v-for="(item, idx) in filteredSkills"
                :key="'skill-' + item.id"
                type="button"
                role="option"
                :aria-selected="idx === mentionActive"
                :class="['mention-item', { active: idx === mentionActive }]"
                @mousedown.prevent="pickSkill(item)"
                @mousemove="mentionActive = idx"
              >
                <span class="mention-item-icon" aria-hidden="true"><CodeSandboxOutlined /></span>
                <span class="mention-item-name"><template v-for="(p, pi) in matchParts(item.name)" :key="pi"><b v-if="p.hit" class="mention-hit">{{ p.text }}</b><template v-else>{{ p.text }}</template></template></span>
                <span v-if="item.description" class="mention-item-desc"><template v-for="(p, pi) in matchParts(item.description)" :key="'d' + pi"><b v-if="p.hit" class="mention-hit">{{ p.text }}</b><template v-else>{{ p.text }}</template></template></span>
                <span class="mention-item-tag">{{ skillTag(item) }}</span>
              </button>
            </template>
          </div>
          <div v-show="mentionFade" class="mention-fade" aria-hidden="true"></div>
        </template>
      </div>

      <div
        v-if="showPlanHint"
        class="plan-mode-hint"
        role="status"
      >
        <span>这句话适合先对齐做法再动手。要开启计划模式吗？</span>
        <button type="button" class="plan-mode-hint-go" @click="acceptPlanHint">开启</button>
        <button type="button" class="plan-mode-hint-dismiss" aria-label="关闭建议" @click="planHintDismissed = true">
          <CloseOutlined />
        </button>
      </div>

      <div class="composer-input-row">
        <textarea
          ref="textareaRef"
          :value="input"
          rows="1"
          :placeholder="typedPlaceholder"
          :disabled="interviewMode && !interview?.canAnswer.value && !loading"
          aria-label="输入消息"
          @input="onInput"
          @paste="onPaste"
          @keydown.esc="mentionOpen = false"
          @keydown.down="onMentionNav($event, 1)"
          @keydown.up="onMentionNav($event, -1)"
          @keydown.tab="onMentionTab"
          @keydown.enter="onEnter"
          @keydown.delete="onComposerBackspace"
        />
      </div>
      <div class="composer-footer">
        <!-- + 菜单（ChatGPT 式）：最左边，收纳上传与显式工作模式。
             网页搜索由后端常驻注册、模型按需调用，不再提供重复的手动入口。 -->
        <div v-if="showPlusMenu" class="plus-wrap">
          <!-- 上传进度只在每张附件卡上转圈；+ 号保持常态可用，不跟着转（用户拍板） -->
          <button
            ref="plusButtonRef"
            type="button"
            :class="['web-toggle', 'plus-btn', { active: plusOpen }]"
            :title="plusOpen ? '关闭' : '添加'"
            :aria-expanded="plusOpen"
            :aria-label="plusOpen ? '关闭菜单' : '打开添加菜单'"
            @click.stop="togglePlus"
          >
            <!-- Grok 式：+ 旋转 45° 即成 ×，再点转回；不换图标，只转字形 -->
            <span class="plus-btn-icon" aria-hidden="true">
              <PlusOutlined />
            </span>
          </button>
          <!-- 菜单形态（2026-07-28 用户拍板，对照 Manus）：**窄条 + 只有图标和名称**，
               不再每行拖一句说明——一句话的解释放在 title 里，悬停才出。三组用分隔线断开：
               ①从本地拿 ②从已有的东西里挑（都带 › 二级） ③工作模式（可勾选） -->
          <Teleport to="body" :disabled="!isCompactComposer">
            <button
              v-if="plusOpen && isCompactComposer"
              type="button"
              class="plus-mobile-backdrop"
              aria-label="关闭添加菜单"
              @click.stop="closePlusMenu(true)"
            ></button>
            <div
              v-if="plusOpen"
              ref="plusMenuRef"
              class="plus-menu"
              :class="{ 'plus-menu-image-only': imageOnlyUpload }"
              :role="isCompactComposer ? 'dialog' : undefined"
              :aria-modal="isCompactComposer ? 'true' : undefined"
              :aria-label="isCompactComposer ? '添加到对话' : undefined"
              @click.stop
              @keydown.esc.stop="closePlusMenu(true)"
            >
              <div class="plus-mobile-head">
                <span>
                  <strong>添加到对话</strong>
                  <small>{{ imageOnlyUpload ? '选择一张或多张图片' : '选择文件、上下文或工作方式' }}</small>
                </span>
                <button
                  ref="plusCloseRef"
                  type="button"
                  class="plus-mobile-close"
                  aria-label="关闭添加菜单"
                  @click="closePlusMenu(true)"
                >
                  <CloseOutlined />
                </button>
              </div>
            <button v-if="canUploadFromComputer" type="button" class="plus-item" :title="imageOnlyUpload ? '从电脑上传图片' : '从电脑上传照片和文件'" @click="pickUpload">
              <span class="plus-item-icon"><PictureOutlined v-if="imageOnlyUpload" /><PaperClipOutlined v-else /></span>
              <span class="plus-item-name">{{ imageOnlyUpload ? '添加图片' : '添加照片和文件' }}</span>
            </button>

            <div v-if="hasResourcePickerGroup" class="plus-sep" role="separator"></div>

            <!-- 「我的文件」「知识库」（2026-07-28 用户拍板：从底栏工具条收进 + 菜单，对标
                 Manus 的「最近的文件」）：一行条目 + 向右飞出的列表；文件按类型出图标，与
                 「我的文件」页同一套（composables/fileKind.ts）。已选回执改由输入框上方
                 AttachmentCard 承担。 -->
            <FileSelector
              v-if="!uiPolicy?.hideFiles"
              :model-value="selectedFileList"
              @update:model-value="emit('updateFiles', $event)"
            />
            <KnowledgeSelector
              v-if="!uiPolicy?.hideKnowledge"
              :model-value="selectedKnowledgeList"
              @update:model-value="emit('updateKnowledge', $event)"
            />
            <!-- 「最近的对话」（2026-07-28，对标 Manus 的「最近的任务」）：把之前聊过的内容
                 带进这一轮。与「我的文件」同形态，紧挨着放——两者都是"从已有的东西里挑"。 -->
            <ThreadSelector
              v-if="!uiPolicy?.hideThreads"
              :model-value="selectedThreadList"
              :current-thread-id="threadId"
              :scope="assistantPreset || 'ordinary'"
              @update:model-value="emit('updateThreads', $event)"
            />
            <!-- 「使用技能」（2026-07-28 用户拍板）：Skill 从此有**两个**对话框内入口——
                 `@` 面板与这里，共用同一套一次性语义。此前的产品决策是"@ 面板是唯一入口"，
                 已由用户在本轮推翻，CLAUDE.md 同步改了，别再照旧规则把它删掉。 -->
            <SkillSelector
              v-if="!presentationMode && !uiPolicy?.hideSkillSelector"
              :skills="skills"
              :selected="selectedSkills"
              @ensure="emit('ensureSkills')"
              @select="pickSkillFromMenu"
              @remove="emit('removeSkill', $event)"
            />

            <div
              v-if="!uiPolicy?.hideResearch || !uiPolicy?.hidePlanMode"
              class="plus-sep"
              role="separator"
            ></div>

            <button
              v-if="!uiPolicy?.hideResearch"
              type="button"
              class="plus-item"
              title="深入检索、验证并生成带引用的报告"
              :disabled="loading || hasPendingHitlCard"
              @click="pickResearchProfile"
            >
              <span class="plus-item-icon"><SearchOutlined /></span>
              <span class="plus-item-name">深度研究</span>
              <CheckOutlined v-if="researchProfile" class="plus-item-check" />
            </button>
            <!-- 计划模式（2026-07-27 用户拍板：从底栏常驻开关挪进 + 菜单，并由「任务模式」
                 改名为「计划模式」——名字要说清它做什么）：与「深度研究」同形态。
                 行为=先澄清需求、出计划、确认后再执行。不再自动路由：只有在这里点开、
                 或用户话里明确说「先给我个计划」时才进。图标为有序列表（对照 Claude/Manus）。 -->
            <button
              v-if="!uiPolicy?.hidePlanMode"
              type="button"
              class="plus-item"
              title="先做好计划，再根据计划执行"
              :disabled="loading || hasPendingHitlCard"
              @click="pickPlanMode"
            >
              <span class="plus-item-icon"><OrderedListOutlined /></span>
              <span class="plus-item-name">计划模式</span>
              <CheckOutlined v-if="planMode" class="plus-item-check" />
            </button>
            </div>
          </Teleport>
        </div>
        <WorkFolderMenu
          v-if="!assistantPreset"
          :folder="selectedWorkFolder"
          :busy="loading || uploading || uploadingWorkFolder || restoringHistory || hasPendingHitlCard"
          :has-thread="Boolean(threadId)"
          :conversation-key="threadId || currentDraftId"
          @select="selectWorkFolder"
          @uploading="uploadingWorkFolder = $event"
        />
        <!-- 连接应用（2026-07-28）：紧挨 + 号，与知识库/我的文件同级的 composer 工具条控件。
             连接后勾选的资源（GitHub 代码库等）既是给模型的可读范围，也是服务端的访问白名单。 -->
        <!-- 连接器详情弹窗里的「试用一下」/示例提示词：整句填进输入框，用户直接回车 -->
        <ConnectorMenu
          v-if="MAIN_CHAT_FEATURE_VISIBILITY.connectors && !campusMode && !uiPolicy?.hidePlusMenu"
          @use-prompt="onConnectorPrompt"
        />
        <input
          ref="fileInputRef"
          type="file"
          multiple
          :accept="uploadAccept"
          class="hidden-file-input"
          @change="onFileChange"
        />
        <!-- 模式选中态：常态只露图标，悬停/键盘聚焦时
             展开完整名称，既表达已选中又不长期挤占输入框。两个模式互斥，同时只会出现一个。 -->
        <button
          v-if="researchProfile && !uiPolicy?.hideResearch"
          type="button"
          :class="['mode-pill', 'research-pill', { locked: loading || hasPendingHitlCard }]"
          :disabled="loading || hasPendingHitlCard"
          title="深度研究已开启，点击关闭"
          aria-label="深度研究已开启，点击关闭"
          @click="pickResearchProfile"
        >
          <SearchOutlined class="mode-pill-icon" />
          <span class="mode-pill-label">深度研究</span>
        </button>
        <button
          v-if="planMode && !uiPolicy?.hidePlanMode"
          type="button"
          :class="['mode-pill', 'plan-pill', {
            locked: loading || hasPendingHitlCard,
            'has-status': Boolean(planProfileStatusText),
          }]"
          :disabled="loading || hasPendingHitlCard"
          :title="planProfileHelp"
          :aria-label="`计划模式已开启${planProfileStatusText ? '（' + planProfileStatusText + '）' : ''}，点击关闭`"
          @click="pickPlanMode"
        >
          <OrderedListOutlined class="mode-pill-icon" />
          <span class="mode-pill-label">计划模式<template v-if="planProfileStatusText"> · {{ planProfileStatusText }}</template></span>
        </button>
        <!-- 「下一轮发送 / 立即引导」按钮已移除（2026-07-28 用户拍板）：生成中一打字它就
             蹦出来，在工具条里挤掉知识库/我的文件，是纯噪音。反转跟进行为的能力保留在
             ⇧⌘Enter 上（见 onEnter），发送键 tooltip 里也写着两种动作
             各自的键帽——想要另一种行为按快捷键即可，不需要常驻一个按钮。 -->
        <!-- 右侧簇：模型选择（可隐藏）+ 发送/停止。margin-left:auto 在这一组上，
             校园百事通藏掉模型选择器后发送键仍靠右，输入框结构和主对话一致。 -->
        <div class="composer-footer-end">
        <InterviewMenu v-if="interviewMode && interview?.hasSession.value" @show-report="interviewPanelRef?.showReport()" />
        <span
          v-if="!uiPolicy?.hideModelSelector && currentRunModel && currentRunModel !== model"
          class="model-switch-state"
          :title="`当前任务继续使用 ${currentRunModel}；${model} 从下一轮开始使用`"
        >
          本轮 {{ currentRunModel }} · 下一轮 {{ model }}
        </span>
        <ModelSelector v-if="!uiPolicy?.hideModelSelector" :model-value="model" @update:model-value="emit('update:model', $event)" />
        <!-- 生成中停键始终在（有草稿也不换成发送箭头）。改向走排队卡上的「调整方向」。 -->
        <!-- (loading || stopping)：stopChat 会先清 activeRun（loading 提前变 false）再等后端
             确认取消，这段窗口必须仍显示「停止中」而不是退化成发送箭头（N-10） -->
        <button
          v-if="loading || stopping"
          type="button"
          class="composer-stop"
          :disabled="stopping"
          :title="stopping ? '停止中…' : '停止生成'"
          @click="interruptCurrentRun"
        >
          <LoadingOutlined v-if="stopping" />
          <span v-else class="stop-square" />
        </button>
        <a-tooltip v-else placement="top" overlay-class-name="send-actions-tip">
          <template #title>
            <span>发送</span>
          </template>
          <button
            type="button"
            class="composer-send"
            :disabled="submittingRunInput || !canSendComposer"
            aria-label="发送"
            @click="emit('send')"
          >
            <LoadingOutlined v-if="submittingRunInput" />
            <ArrowUpOutlined v-else />
          </button>
        </a-tooltip>
        </div>
      </div>
    </div>
      <AgentOutputDisclaimer v-if="chatMessages.length > 0 && !interviewMode" />
    </div>

    <RecommendGrid
      v-if="chatMessages.length === 0 && !presentationMode && !uiPolicy?.hideRecommendGrid"
      :agents="recommendedAgents"
      :loading="agentsLoading || restoringHistory"
      @start-chat="(agent) => emit('startAgent', agent)"
      @view-all="emit('openAgentMarket')"
    />

    <ImageLightbox :src="lightboxSrc" @close="lightboxSrc = null" />

    <!-- 旁路会话面板：可同时开多个，层叠错位摆放 -->
    <SideChatPanel
      v-for="(sc, i) in sideChats"
      :key="sc.id"
      :seed="sc.seed"
      :index="i"
      :model="model"
      @close="closeSideChat(sc.id)"
      @send-back="(text) => onSideChatSendBack(text)"
    />

    <!-- 产物全屏查看器（2026-07-23 产物展示统一）：HTML 产物走网页模式（预览/源码切换）、
         文本文件预览走源码模式，与「我的文件」的文档查看器同一形态；右侧滑出的 ArtifactPanel 已移除 -->
    <DocPagesViewer
      v-if="artifactOpen && activeArtifact"
      :key="activeArtifact.id"
      :filename="activeArtifactFilename"
      :html-src="activeArtifact.type === 'html' ? activeArtifact.html || '' : undefined"
      :code-text="activeArtifact.type === 'html' ? undefined : activeArtifact.code || ''"
      :meta-note="activeArtifactMetaNote || undefined"
      @close="artifactOpen = false"
      @download="downloadActiveArtifact"
    />

    <!-- 文件卡「版本历史」（P0 交付清单）：对话内直接查看/下载/恢复版本，不必跳「我的文件」 -->
    <FileVersionsModal :file="versionsFile" @close="versionsFile = null" />

    <!-- 队列暂停中又发新消息（Codex composer.pausedQueueSubmit 对齐 2026-07-26）：
         暂停的队列是「你刚喊过停」的产物，直接发新消息会让它们处境不明——先问清楚。 -->
    <a-modal
      :open="Boolean(pausedQueuePrompt)"
      title="发送消息？"
      :closable="true"
      :mask-closable="false"
      :footer="null"
      :width="420"
      wrap-class-name="paused-queue-modal"
      @cancel="answerPausedQueuePrompt('cancel')"
    >
      <p class="paused-queue-desc">
        你即将发送一条消息。要清除之前已排队的 {{ pausedQueuePrompt?.count || 0 }} 条消息吗？
      </p>
      <div class="paused-queue-actions">
        <button type="button" class="pq-btn" @click="answerPausedQueuePrompt('clear')">清空队列</button>
        <button type="button" class="pq-btn primary" @click="answerPausedQueuePrompt('send')">发送消息</button>
      </div>
    </a-modal>

  </section>
</template>

<script setup lang="ts">
import { ref, computed, inject, nextTick, watch, onMounted, onUnmounted } from 'vue';
import InterviewSetup from '../builtinAssistants/interview/InterviewSetup.vue';
import InterviewPanel from '../builtinAssistants/interview/InterviewPanel.vue';
import InterviewMenu from '../builtinAssistants/interview/InterviewMenu.vue';
import { InterviewSessionKey } from '../builtinAssistants/interview/useInterviewSession';
import { onClickOutside, useMediaQuery, usePreferredReducedMotion } from '@vueuse/core';
import { ArrowDownOutlined, ArrowUpOutlined, CheckOutlined, CloseOutlined, CodeSandboxOutlined, DeleteOutlined, EditOutlined, EllipsisOutlined, EnterOutlined, ExclamationCircleOutlined, HolderOutlined, LoadingOutlined, OrderedListOutlined, PaperClipOutlined, PictureOutlined, PlusOutlined, RedoOutlined, RollbackOutlined, SearchOutlined, UndoOutlined } from '@ant-design/icons-vue';
import { message } from 'ant-design-vue';
import draggable from 'vuedraggable';
import type { AgentItem, ChatQueueItem, GeneratedFile, KnowledgeSelection, SkillItem, ThreadReference, UploadedFile } from '../agentApi';
import { saveArtifactFile, fetchUserFileText, type UserFileSelection } from '../myfiles.api';
import MessageList, { type ChatMessage } from '../components/MessageList.vue';
import WorkAgentMascot from '../components/WorkAgentMascot.vue';
import AgentOutputDisclaimer from '../components/AgentOutputDisclaimer.vue';
import ModelSelector from '../components/ModelSelector.vue';
import KnowledgeSelector from '../components/KnowledgeSelector.vue';
import FileSelector from '../components/FileSelector.vue';
import SkillSelector from '../components/SkillSelector.vue';
import ThreadSelector from '../components/ThreadSelector.vue';
import ConnectorMenu from '../components/ConnectorMenu.vue';
import WorkFolderMenu from '../components/WorkFolderMenu.vue';
import RecommendGrid from '../components/RecommendGrid.vue';
import AttachmentCard from '../components/AttachmentCard.vue';
import ImageLightbox from '../components/ImageLightbox.vue';
import SideChatPanel from '../components/SideChatPanel.vue';
import DocPagesViewer from '../components/DocPagesViewer.vue';
import { type Artifact, type ArtifactSavedFile } from '../utils/artifactParser';
import FileVersionsModal from '../components/FileVersionsModal.vue';
import { useCenterContext } from '../centerContext';
import { MAIN_CHAT_FEATURE_VISIBILITY } from '../mainChatFeatureVisibility';
import { CHAT_IMAGE_UPLOAD_ACCEPT, CHAT_UPLOAD_ACCEPT, isChatImageFile } from '../utils/chatUploadTypes';
import { moveQueueEntryToIndex } from '../composables/chatQueueRules';
import {
  getBuiltinAssistantByPreset,
  getBuiltinUiFlags,
  type AssistantPreset,
} from '../utils/builtinAssistants';
import {
  latestAssistantMessage,
  selectWorkAgentMascotState,
  workAgentDiscoverySignature,
  type WorkAgentMascotState,
} from '../utils/workAgentMascotState';

// 任务模式（A）+ 运行中消息队列（B）+ 立即引导（C）：状态与编排全在 useCenterChat 里，
// 这里直接 inject 取用同一个 centerChat 单例（center.vue provide、ChatPage.vue 也是同一份），
// 不改造 ChatPage.vue 的既有 props/emit 链路，缩小本次改动的回归面。
const {
  chatMessages,
  selectedWorkFolder,
  uploadingWorkFolder,
  selectWorkFolder,
  currentDraftId,
  planMode,
  togglePlanProfile,
  researchProfile,
  toggleResearchProfile,
  messageQueue,
  queueLoading,
  queueBusy,
  editingQueueId,
  editingQueueText,
  startQueueEdit,
  cancelQueueEdit,
  commitQueueEdit,
  removeQueueItem,
  moveQueueItemToInput,
  reorderQueueItems,
  // 中断后队列暂停（Codex 对标）：横幅 + 「继续」按钮
  queuePaused,
  resumeQueue,
  // 队列暂停中又发新消息的确认闸（Codex composer.pausedQueueSubmit）
  pausedQueuePrompt,
  answerPausedQueuePrompt,
  // 队列项派发失败态 + 重试（Codex isMessagePaused / queuedMessage.retry，逐条粒度）
  failedQueueItemId,
  retryQueueDispatch,
  // 跟进行为：运行中回车＝排队，⌘Enter 单条取反
  followUpMode,
  setFollowUpMode,
  submittingRunInput,
  canInstruct,
  instructQueueItem,
  // 停止流程进行中（第四批 2a）：停止按钮禁用「停止中…」，防重复点重复软提示
  stopping,
} = useCenterContext().centerChat;

const props = defineProps<{
  messages: ChatMessage[];
  loading: boolean;
  restoringHistory?: boolean;
  input: string;
  model: string;
  currentRunModel?: string;
  placeholder: string;
  selectedSkills: SkillItem[];
  selectedKnowledgeList: KnowledgeSelection[];
  selectedFileList: UserFileSelection[];
  selectedThreadList: ThreadReference[];
  skills: SkillItem[];
  webSearch: boolean;
  attachments: UploadedFile[];
  uploading: boolean;
  /** 附件原文仍在本会话内存中：降级横幅的「重试本轮」才可用（透传 MessageList） */
  retryAttachments?: boolean;
  recommendedAgents: AgentItem[];
  agentsLoading: boolean;
  /** 当前会话 id：产物自动存「我的文件」挂到该会话（同会话同名产物迭代出新版本） */
  threadId?: string;
  assistantPreset?: AssistantPreset;
}>();

const builtinAssistant = computed(() => getBuiltinAssistantByPreset(props.assistantPreset));
const uiPolicy = computed(() => getBuiltinUiFlags(builtinAssistant.value?.uiPolicy));
const presentationMode = computed(() => props.assistantPreset === 'presentation');
const campusMode = computed(() => props.assistantPreset === 'campus_services');
const interviewMode = computed(() => props.assistantPreset === 'interview');
const interviewPanelRef = ref<InstanceType<typeof InterviewPanel> | null>(null);
const interviewSurfaceOpen = ref(false);

const interviewComposerRef = ref<HTMLElement | null>(null);
const interviewComposerHeight = ref(0);
watch([interviewComposerRef, interviewMode], ([element, enabled], _previous, onCleanup) => {
  interviewComposerHeight.value = 0;
  if (!element || !enabled || typeof ResizeObserver === 'undefined') return;
  // The feedback panel must remain reachable when a long answer grows the fixed composer.
  const measure = () => { interviewComposerHeight.value = Math.ceil(element.getBoundingClientRect().height); };
  const observer = new ResizeObserver(measure);
  observer.observe(element);
  measure();
  onCleanup(() => observer.disconnect());
}, { flush: 'post' });
const interview = inject(InterviewSessionKey, null);
const interviewComposerVisible = computed(() => {
  if (!interviewMode.value) return true;
  const status = interview?.snapshot.value?.status || '';
  if (status === 'completed' || status === 'paused') return Boolean(props.loading);
  if (interview?.hasSession.value || chatMessages.value.length > 0) return true;
  return false;
});
const imageOnlyUpload = computed(() => Boolean(uiPolicy.value?.imageOnlyUpload));
const canUploadFromComputer = computed(() => !uiPolicy.value?.hideFiles || imageOnlyUpload.value);
const showPlusMenu = computed(() => !uiPolicy.value?.hidePlusMenu || canUploadFromComputer.value);
const hasResourcePickerGroup = computed(() => Boolean(
  !uiPolicy.value?.hideFiles
  || !uiPolicy.value?.hideKnowledge
  || !uiPolicy.value?.hideThreads
  || (!presentationMode.value && !uiPolicy.value?.hideSkillSelector),
));
const uploadAccept = computed(() => (
  imageOnlyUpload.value ? CHAT_IMAGE_UPLOAD_ACCEPT : CHAT_UPLOAD_ACCEPT
));
const mascotVariant = computed(() => builtinAssistant.value?.mascotVariant || 'main');
const mascotLabel = computed(() => `和${builtinAssistant.value?.name || '主 Agent'}互动`);

// Run 等待用户输入时锁定 Profile 开关，避免本地 UI 与权威 Run 状态分叉。
const hasPendingHitlCard = computed(() =>
  (chatMessages.value || []).some((m: any) =>
    m.interactive));

const latestWorkAgentMessage = computed(() => latestAssistantMessage(chatMessages.value || []));
const workAgentMascotBaseState = computed(() => selectWorkAgentMascotState({
  loading: props.loading,
  stopping: stopping.value,
  waiting: hasPendingHitlCard.value,
  message: latestWorkAgentMessage.value,
}));
const workAgentMascotPulse = ref<Extract<WorkAgentMascotState, 'discover' | 'success'> | null>(null);
const workAgentMascotState = computed<WorkAgentMascotState>(() => {
  if (workAgentMascotBaseState.value === 'waiting' || workAgentMascotBaseState.value === 'error') {
    return workAgentMascotBaseState.value;
  }
  return workAgentMascotPulse.value || workAgentMascotBaseState.value;
});
const workAgentDiscoveryKey = computed(() => workAgentDiscoverySignature(latestWorkAgentMessage.value));
let workAgentMascotPulseTimer: ReturnType<typeof setTimeout> | undefined;

function clearWorkAgentMascotPulse() {
  if (workAgentMascotPulseTimer) clearTimeout(workAgentMascotPulseTimer);
  workAgentMascotPulseTimer = undefined;
  workAgentMascotPulse.value = null;
}

function pulseWorkAgentMascot(state: 'discover' | 'success', durationMs: number) {
  clearWorkAgentMascotPulse();
  workAgentMascotPulse.value = state;
  workAgentMascotPulseTimer = setTimeout(() => {
    workAgentMascotPulse.value = null;
    workAgentMascotPulseTimer = undefined;
  }, durationMs);
}

watch(workAgentDiscoveryKey, (next, previous) => {
  if (props.loading && next && next !== previous) pulseWorkAgentMascot('discover', 1100);
});

watch(() => props.loading, (loading, wasLoading) => {
  if (loading) {
    clearWorkAgentMascotPulse();
    return;
  }
  const message = latestWorkAgentMessage.value;
  if (wasLoading
    && message
    && !message.runFailed
    && !message.runCancelled
    && !message.error
    && !hasPendingHitlCard.value) {
    pulseWorkAgentMascot('success', 900);
  }
});

watch(() => props.threadId, clearWorkAgentMascotPulse);
onUnmounted(clearWorkAgentMascotPulse);

// 计划模式状态标签：胶囊既是选中指示也是关闭入口。
// 2026-07-24 用户拍板：生成中「工作中」不再显示（只需保持打开态、别加工作中噪声）；
// 只保留“需要你介入”的可操作态。
const planProfileStatusText = computed(() => {
  if (!planMode.value) return '';
  if (hasPendingHitlCard.value) return '等待补充';
  return '';
});


// 计划模式说明（+ 菜单那一项的描述行 + 选中态胶囊的 title）
// 说明必须跟着行为改（2026-07-27）：原文写着「系统识别到复杂任务时会**自动开启**」——
// 自动路由已按用户拍板去掉，这句话成了假话。
const planProfileHelp = '普通对话已经可以查看、创建和修改文件，多数任务直接说就行。'
  + '计划模式适合改动面大、想先对齐做法再动手的活：我会先问清需求，给出一份完整的计划报告，'
  + '你确认后再逐步执行。只在你手动开启、或明确说「先给我个计划」时才启用。';

// 停止键在任何 Profile 下都会取消当前 Run。
/** 连接器详情弹窗的「试用一下」/示例提示词：整句填进输入框并聚焦，用户直接回车即可。
 *  不自动发送——那是替用户做决定；他可能还想改两个字（比如换个仓库名）。 */
function onConnectorPrompt(text: string) {
  if (!text) return;
  emit('update:input', text);
  nextTick(() => textareaRef.value?.focus());
}

function interruptCurrentRun() {
  emit('stop');
}

// 开关本体（可拖动旋钮 + 橡皮筋阻尼 + 松手弹性吸附那一整套手势）随入口挪进 + 菜单
// 一起退休（2026-07-27）：菜单项是普通按钮，没有拖拽语义。切换逻辑见 pickPlanMode。

const composerPlaceholder = computed(() => {
  if (interviewMode.value) return interview?.composerPlaceholder.value || '请先完成面试设置';
  if (props.loading || stopping.value) return '随心输入';
  if (researchProfile.value) return '描述你想深入研究的问题…';
  if (planMode.value) return '描述你的需求，我会先问清要点、制定计划，确认后再执行';
  return props.placeholder;
});

/**
 * 占位文案的打字机（2026-07-29 用户拍板，对标参考实现逐帧实测）。
 *
 * 实测口径（0.08s 逐帧）：切到计划模式的那一刻，**旧文案是瞬间清空的**（不是逐字删除），
 * 新文案从空开始逐字长出来，19 个字约 0.24s ≈ 每字 13ms。这里照这个值。
 *
 * 用 rAF + 「按经过时间反算该显示几个字」而不是 setInterval 逐字递增：
 * ① setInterval 在浏览器里最小间隔和调度抖动都不可控，13ms 这种量级会明显不匀；
 * ② 掉帧或标签页/面板被挂起时，按时间算能在恢复后**跳到正确进度**，而逐字递增会把
 *    整段动画拖长（内置浏览器面板隐藏时 rAF 就是被挂起的，这不是假设）。
 */
const PLACEHOLDER_CHAR_MS = 13;
const typedPlaceholder = ref(composerPlaceholder.value);
let placeholderRaf = 0;

function stopPlaceholderTyping() {
  if (placeholderRaf) {
    cancelAnimationFrame(placeholderRaf);
    placeholderRaf = 0;
  }
}

watch(composerPlaceholder, (next, prev) => {
  stopPlaceholderTyping();
  const target = next || '';
  // 无动画的几种情况：文案没真变、空文案、以及用户要求减少动态效果
  const reduceMotion = typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (!target || target === prev || reduceMotion) {
    typedPlaceholder.value = target;
    return;
  }
  const startedAt = performance.now();
  typedPlaceholder.value = '';
  const step = (now: number) => {
    const shown = Math.min(target.length, Math.floor((now - startedAt) / PLACEHOLDER_CHAR_MS) + 1);
    typedPlaceholder.value = target.slice(0, shown);
    placeholderRaf = shown < target.length ? requestAnimationFrame(step) : 0;
  };
  placeholderRaf = requestAnimationFrame(step);
});

onUnmounted(stopPlaceholderTyping);

const queueDragId = ref('');
const queueDragPreview = ref<ChatQueueItem[]>([]);
const queueCanReorder = computed(() => (
  !queueBusy.value
  && messageQueue.value.length > 1
));
const preferredReducedMotion = usePreferredReducedMotion();
const queueSortAnimationMs = computed(() => (
  preferredReducedMotion.value === 'reduce' ? 0 : 180
));

type QueueSortEvent = {
  item?: HTMLElement;
};

watch(messageQueue, (items) => {
  if (!queueDragId.value) queueDragPreview.value = [...items];
}, { immediate: true });

// 队列项行内编辑的回车提交：与主输入框同口径挡输入法组字回车（组字中的回车是「确认候选词」，
// 不是提交——中文选词时会误提交并把残留文本留在框里）。
function onQueueEditEnter(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return;
  e.preventDefault();
  commitQueueEdit();
}

function onQueueDragStart(event: QueueSortEvent) {
  queueDragId.value = event.item?.dataset.queueId || '';
  document.body.classList.add('queue-order-dragging');
}

function clearQueueDragVisuals() {
  document.body.classList.remove('queue-order-dragging');
  queueDragId.value = '';
}

function onQueueDragEnd() {
  const sourceId = queueDragId.value;
  const orderedIds = queueDragPreview.value.map((item) => item.id);
  clearQueueDragVisuals();
  if (!sourceId || queueBusy.value) {
    queueDragPreview.value = [...messageQueue.value];
    return;
  }
  void reorderQueueItems(orderedIds);
}

function moveQueueItemByKeyboard(itemId: string, direction: -1 | 1) {
  if (!queueCanReorder.value) return;
  const sourceIndex = messageQueue.value.findIndex((item) => item.id === itemId);
  const targetIndex = sourceIndex + direction;
  if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= messageQueue.value.length) return;
  const next = moveQueueEntryToIndex(messageQueue.value, itemId, targetIndex);
  void reorderQueueItems(next.map((item) => item.id));
}

onUnmounted(clearQueueDragVisuals);

const emit = defineEmits<{
  (e: 'update:input', value: string): void;
  (e: 'update:model', value: string): void;
  (e: 'send', opts?: { invertFollowUp?: boolean; forceSteer?: boolean }): void;
  (e: 'aiEditFile', file: GeneratedFile, payload: { instruction: string; scope: 'page' | 'all'; page: number }): void;
  (e: 'saveSlides', slidesFile: GeneratedFile, deckFile: GeneratedFile, pages: string[], done: (ok: boolean) => void): void;
  (e: 'stop'): void;
  (e: 'regenerate', modelId?: string): void;
  (e: 'feedback', messageId: number, value: 'up' | 'down' | null): void;
  (e: 'resume', messageId: number, resumeValue: unknown): void;
  (e: 'removeSkill', skillId: string): void;
  (e: 'removeKnowledge', id: string): void;
  (e: 'updateKnowledge', list: KnowledgeSelection[]): void;
  (e: 'updateFiles', list: UserFileSelection[]): void;
  (e: 'updateThreads', list: ThreadReference[]): void;
  (e: 'selectSkill', item: SkillItem): void;
  (e: 'ensureSkills'): void;
  (e: 'toggleWeb'): void;
  (e: 'upload', file: File): void;
  (e: 'removeAttachment', index: number): void;
  (e: 'retryAttachment', index: number): void;
  (e: 'edit', messageId: number, content: string): void;
  (e: 'approve', messageId: number, approved: boolean): void;
  (e: 'startAgent', agent: AgentItem): void;
  (e: 'openAgent', app: any): void;
  /** 推荐区「查看全部」→ 智能体板块 */
  (e: 'openAgentMarket'): void;
}>();

const mentionOpen = ref(false);
const mentionQuery = ref('');
const mentionActive = ref(0);
const mentionListRef = ref<HTMLElement | null>(null);
const textareaRef = ref<HTMLTextAreaElement | null>(null);
const fileInputRef = ref<HTMLInputElement | null>(null);
const messageListRef = ref<InstanceType<typeof MessageList> | null>(null);
const chatAtBottom = ref(true);
const dragActive = ref(false);
const lightboxSrc = ref<string | null>(null);
// 文件卡「版本历史」弹窗当前查看的文件（null=关闭）
const versionsFile = ref<GeneratedFile | null>(null);

// ===== 产物查看（2026-07-23 统一到 DocPagesViewer 全屏查看器，右侧面板形态已移除） =====
const artifacts = ref<Artifact[]>([]);
const activeArtifact = ref<Artifact | null>(null);
const artifactOpen = ref(false);
const knownArtifactIds = new Set<string>();
// 产物 ↔「我的文件」打通（2026-07-13）：artifactId → 已保存文件信息（查看器 meta 行提示用）
const artifactFiles = ref<Record<string, ArtifactSavedFile>>({});
const savedArtifactKeys = new Set<string>(); // 保存请求 in-flight/已成防重（服务端 sha 幂等兜底）

function onOpenArtifact(a: Artifact) {
  activeArtifact.value = a;
  artifactOpen.value = true;
}

const activeArtifactSaved = computed(() =>
  activeArtifact.value ? artifactFiles.value[activeArtifact.value.id] || null : null,
);
const activeArtifactFilename = computed(() => {
  const a = activeArtifact.value;
  if (!a) return '';
  if (activeArtifactSaved.value) return activeArtifactSaved.value.filename;
  return a.type === 'html' ? artifactFilename(a) : a.title || '文件';
});
const activeArtifactMetaNote = computed(() => {
  const saved = activeArtifactSaved.value;
  if (!saved) return '';
  const ver = saved.versionNo && saved.versionNo > 1 ? ` · v${saved.versionNo}` : '';
  // file- 前缀=文件卡预览（文件本身就在「我的文件」）；其余=消息里的 HTML 产物自动存档
  return `${activeArtifact.value?.id.startsWith('file-') ? '已保存到我的文件' : '已自动存入我的文件'}${ver}`;
});

/** 查看器「下载原文件」：产物内容就在内存里，直接落成文件（未保存/保存失败时也可用） */
function downloadActiveArtifact() {
  const a = activeArtifact.value;
  const text = a?.html || a?.code || '';
  if (!text) return;
  const blob = new Blob([text], {
    type: a?.type === 'html' ? 'text/html;charset=utf-8' : 'text/plain;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const el = document.createElement('a');
  el.href = url;
  el.download = activeArtifactFilename.value;
  document.body.appendChild(el);
  el.click();
  el.remove();
  URL.revokeObjectURL(url);
}

/** 产物文件名：标题清洗非法字符 + 截断,兜底「HTML 页面」；服务端 _safe_name 再兜一层 */
function artifactFilename(a: Artifact): string {
  const base =
    (a.title || '').replace(/[\\/:*?"<>|]/g, '').replace(/\s+/g, ' ').trim().slice(0, 40) ||
    'HTML 页面';
  return `${base}.html`;
}

/** 流收尾的新 HTML 产物自动存「我的文件」：同会话同名内容未变服务端直接跳过（unchanged），
 *  变化则原地新版本。失败必须可见——产物仍在对话里，但不能假装已经保存。 */
async function persistArtifact(item: Artifact) {
  if (item.type !== 'html' || !item.html) return;
  if (savedArtifactKeys.has(item.id)) return;
  savedArtifactKeys.add(item.id);
  try {
    const saved = await saveArtifactFile(artifactFilename(item), item.html, props.threadId);
    artifactFiles.value = {
      ...artifactFiles.value,
      [item.id]: { id: saved.id, filename: saved.filename, versionNo: saved.versionNo },
    };
  } catch (e) {
    savedArtifactKeys.delete(item.id);
    console.warn('产物自动保存到「我的文件」失败', e);
    message.warning('产物已生成，但自动保存到「我的文件」失败；请稍后重试或先下载保存');
  }
}

/** 生成文件卡「预览」：可预览类型在全屏查看器打开（html→网页模式，文本→源码模式） */
async function onPreviewFile(file: GeneratedFile) {
  try {
    const text = await fetchUserFileText(file.id);
    const ext = (file.filename.split('.').pop() || '').toLowerCase();
    const isHtml = ext === 'html' || ext === 'htm' || (file.mime || '') === 'text/html';
    const id = `file-${file.id}`;
    activeArtifact.value = isHtml
      ? { id, type: 'html', title: file.filename, html: text }
      : { id, type: 'code', title: file.filename, code: text, lang: ext };
    // 文件预览的「已保存」信息就是文件本身 → 查看器 meta 行如实标注
    artifactFiles.value = {
      ...artifactFiles.value,
      [id]: { id: file.id, filename: file.filename, versionNo: file.versionNo },
    };
    artifactOpen.value = true;
  } catch (e: any) {
    message.error(e?.message || '文件预览加载失败');
  }
}

// 任务与协作面板（含任务步骤 + 子智能体入口）已上移到 center.vue 顶栏（对话历史 / 记忆旁），
// 数据在那里直接从 useCenterChat 的消息派生，这里不再承载。

// 切换/新建会话：产物清空、查看器关闭
watch(
  () => chatMessages.value.length,
  (len) => {
    if (len === 0) {
      knownArtifactIds.clear();
      savedArtifactKeys.clear();
      artifactFiles.value = {};
      artifacts.value = [];
      activeArtifact.value = null;
      artifactOpen.value = false;
    }
  },
);

// 产物列表随消息重扫更新时按 id（内容哈希，跨重扫稳定）对账：来源已不在（切会话/重新生成
// 删掉了带产物的消息）就关查看器——只靠 messages.length===0 挡不住「两会话消息数相同」的残留
function onArtifacts(list: Artifact[], live = false) {
  const fresh = list.filter((item) => !knownArtifactIds.has(item.id));
  list.forEach((item) => knownArtifactIds.add(item.id));
  artifacts.value = list;
  // 本轮新完成的 HTML 产物自动存「我的文件」（产物与文件打通；服务端幂等，重复触发无副作用）
  if (live) fresh.filter((a) => a.type === 'html').forEach(persistArtifact);
  // 不再自动弹开查看器（2026-07-23 用户拍板「生成完还会自动打开」是干扰）：
  // 产物入口=消息里的文件卡「预览」与顶部「产物 N」按钮，用户主动点才打开。
  const current = activeArtifact.value;
  if (!current) return;
  if (current.id.startsWith('file-')) return; // 文件预览产物不在消息重扫列表里，别被误关
  const match = list.find((a) => a.id === current.id);
  if (match) {
    activeArtifact.value = match;
  } else {
    activeArtifact.value = null;
    artifactOpen.value = false;
  }
}

// 拖拽文件到输入框：直接上传（后端随即解析）
function onDrop(e: DragEvent) {
  dragActive.value = false;
  if (!canUploadFromComputer.value) return;
  const files = e.dataTransfer?.files;
  if (files) for (const f of Array.from(files)) emitComposerUpload(f);
}

// 粘贴图片（截图直接 Ctrl/Cmd+V）：作为附件上传，避免把二进制粘进文本框
// 长文本粘贴转附件（Codex composer.queuedMessage.pastedTextAttachment 对齐）：
// 整段日志/报错/文章粘进来会把输入框撑成一堵墙，队列卡也只剩一行省略号，什么都看不出。
// 转成一枚附件后，输入框留给「你想让我拿它做什么」，正文照旧完整送到模型。
// 阈值是我们自己定的（Codex 的具体数值反编译不出来）：够得上「一堵墙」才转，
// 正常一两段话不受影响。
const PASTE_AS_FILE_MIN_CHARS = 800;
const PASTE_AS_FILE_MIN_LINES = 12;

function pastedTextFilename(text: string): string {
  const snippet = text
    .replace(/\s+/g, ' ')
    .replace(/[\\/:*?"<>|]/g, '')
    .trim()
    .slice(0, 12);
  return snippet ? `粘贴的文本-${snippet}.txt` : '粘贴的文本.txt';
}

function onPaste(e: ClipboardEvent) {
  if (uiPolicy.value && !uiPolicy.value.allowPasteUpload) {
    const items = e.clipboardData?.items;
    if (items && Array.from(items).some((item) => item.kind === 'file')) {
      e.preventDefault();
    }
    return;
  }
  const items = e.clipboardData?.items;
  if (!items) return;
  let handled = false;
  for (const item of Array.from(items)) {
    if (item.kind === 'file') {
      const file = item.getAsFile();
      if (file) {
        handled = emitComposerUpload(file) || handled;
      }
    }
  }
  if (handled) {
    e.preventDefault();
    return;
  }
  if (imageOnlyUpload.value) return;
  const text = e.clipboardData?.getData('text/plain') || '';
  if (!text) return;
  const lines = text.split('\n').length;
  if (text.length < PASTE_AS_FILE_MIN_CHARS && lines < PASTE_AS_FILE_MIN_LINES) return;
  e.preventDefault();
  emit('upload', new File([text], pastedTextFilename(text), { type: 'text/plain' }));
}

// 点击 @提及面板与输入框之外的空白处，关闭候选面板
onClickOutside(mentionListRef, () => { mentionOpen.value = false; }, { ignore: [textareaRef] });

function triggerUpload() {
  fileInputRef.value?.click();
}

function emitComposerUpload(file: File): boolean {
  if (imageOnlyUpload.value && !isChatImageFile(file)) {
    message.warning('校园百事通只支持添加图片；文档、知识库和我的文件请在主对话中使用');
    return false;
  }
  emit('upload', file);
  return true;
}

// 输入框单行起步、随内容自动长高（对齐 ChatGPT）：上限 ~8 行后内部滚动。
// 空状态大空白的根因是原来写死 rows=3——去掉后由内容驱动高度。
const INPUT_MAX_HEIGHT = 220;

function composerInputMaxHeight() {
  if (!window.matchMedia('(max-width: 719px)').matches) return INPUT_MAX_HEIGHT;
  // 键盘会改变 visualViewport 高度；短屏不让多行草稿吞掉消息阅读区。
  const viewportHeight = window.visualViewport?.height || window.innerHeight;
  return Math.max(88, Math.min(140, Math.floor(viewportHeight * 0.24)));
}

function autoResizeInput() {
  const el = textareaRef.value;
  if (!el) return;
  const maxHeight = composerInputMaxHeight();
  el.style.height = 'auto';
  el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
}

function handleComposerViewportResize() {
  autoResizeInput();
}

// 覆盖输入、发送清空、编辑重发回填等所有改动来源（onInput 之外还有程序化赋值）
watch(() => props.input, () => nextTick(autoResizeInput));

// ===== + 菜单（ChatGPT 式）：上传 / 深度研究 / 计划模式 =====
// 与 Knowledge/File/Model 选择器共用互斥事件：谁打开谁广播，其余收到即关闭
const plusOpen = ref(false);
const isCompactComposer = useMediaQuery('(max-width: 1024px)');
const plusButtonRef = ref<HTMLButtonElement | null>(null);
const plusMenuRef = ref<HTMLElement | null>(null);
const plusCloseRef = ref<HTMLButtonElement | null>(null);
const PICKER_OPEN_EVENT = 'center-chat-picker-open';

watch([plusOpen, isCompactComposer], async ([open, compact]) => {
  if (!open || !compact) return;
  await nextTick();
  plusCloseRef.value?.focus();
});

function closePlusMenu(restoreFocus = false) {
  plusOpen.value = false;
  if (restoreFocus) nextTick(() => plusButtonRef.value?.focus());
}

function togglePlus() {
  if (plusOpen.value) {
    closePlusMenu();
    return;
  }
  document.dispatchEvent(new CustomEvent(PICKER_OPEN_EVENT, { detail: 'plus' }));
  plusOpen.value = true;
}

function pickUpload() {
  plusOpen.value = false;
  triggerUpload();
}

function pickResearchProfile() {
  if (props.loading || hasPendingHitlCard.value) return;
  plusOpen.value = false;
  toggleResearchProfile();
}

// 计划模式（2026-07-27 用户拍板：入口从底栏常驻开关挪进 + 菜单）。
// 与 pickResearchProfile 同形态；生成中/有待处理卡片时不许切（沿用原开关的 disabled 口径——
// 任务生命周期内保持开启，Run 到终态由 useCenterChat 自动拨回）。
function pickPlanMode() {
  if (props.loading || hasPendingHitlCard.value) return;
  plusOpen.value = false;
  togglePlanProfile();
}

const PLAN_HINT_RE = /先给我.{0,8}计划|出个方案再做|先出个方案|先出计划|先做个计划|先规划再|给我一份计划|先制定计划|先对齐(做法|再做)/;
const planHintDismissed = ref(false);
const showPlanHint = computed(() => {
  if (uiPolicy.value?.hidePlanMode) return false;
  if (planMode.value || planHintDismissed.value) return false;
  if (props.loading || hasPendingHitlCard.value) return false;
  return PLAN_HINT_RE.test(props.input || '');
});

function acceptPlanHint() {
  planHintDismissed.value = true;
  if (!planMode.value) pickPlanMode();
}

const hasComposerChips = computed(() => Boolean(
  props.attachments.length
  || props.selectedFileList.length
  || props.selectedThreadList.length
  || props.selectedKnowledgeList.length
  || props.selectedSkills.length
  || props.webSearch,
));

const canSendComposer = computed(() => !uploadingWorkFolder.value && (!interviewMode.value || Boolean(interview?.canAnswer.value) && !props.loading) && Boolean(
  props.input.trim()
  || props.attachments.length
  || props.selectedFileList.length
  || props.selectedThreadList.length
  || props.selectedKnowledgeList.length
  || props.selectedSkills.length,
));

function removeSelectedFile(id: string) {
  emit('updateFiles', props.selectedFileList.filter((f) => f.id !== id));
}

function removeSelectedKnowledge(id: string) {
  emit('updateKnowledge', props.selectedKnowledgeList.filter((k) => k.id !== id));
}

function removeSelectedThread(id: string) {
  emit('updateThreads', props.selectedThreadList.filter((t) => t.id !== id));
}

// 旁路会话（Codex 四态的最后一态）：从队列卡开一个独立面板单独回答，主线 Run 不受影响。
// 开完立刻把该条移出队列——它已经有去处了，再留在队列里本轮结束会被当成下一轮又发一次。
const sideChats = ref<Array<{ id: number; seed: string }>>([]);
let sideChatSeq = 0;

function openInSideChat(item: { id: string; content: string }) {
  sideChatSeq += 1;
  sideChats.value = [...sideChats.value, { id: sideChatSeq, seed: item.content }];
  void removeQueueItem(item.id);
}

function closeSideChat(id: number) {
  sideChats.value = sideChats.value.filter((s) => s.id !== id);
}

/** 旁路的结论送回主对话输入框：追加而不覆盖当前草稿，发不发由用户定 */
function onSideChatSendBack(text: string) {
  const value = (text || '').trim();
  if (!value) return;
  const current = props.input.trim();
  emit('update:input', current ? `${current}\n\n${value}` : value);
  nextTick(() => textareaRef.value?.focus());
}

function handlePlusDocClick() {
  closePlusMenu();
}

function handlePlusPickerOpen(event: Event) {
  if ((event as CustomEvent<string>).detail !== 'plus') closePlusMenu();
}

onMounted(() => {
  document.addEventListener('click', handlePlusDocClick);
  document.addEventListener(PICKER_OPEN_EVENT, handlePlusPickerOpen);
  window.addEventListener('resize', handleComposerViewportResize);
  window.visualViewport?.addEventListener('resize', handleComposerViewportResize);
  autoResizeInput();
});

onUnmounted(() => {
  document.removeEventListener('click', handlePlusDocClick);
  document.removeEventListener(PICKER_OPEN_EVENT, handlePlusPickerOpen);
  window.removeEventListener('resize', handleComposerViewportResize);
  window.visualViewport?.removeEventListener('resize', handleComposerViewportResize);
});

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files) {
    for (const f of Array.from(input.files)) emitComposerUpload(f);
  }
  input.value = '';
}

const filteredSkills = computed(() => {
  const q = mentionQuery.value.toLowerCase();
  const list = q
    ? props.skills.filter(
        (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q),
      )
    : props.skills;
  return list.slice(0, 50);
});

// @ 面板的扁平候选序列（Skill），供键盘 ↑/↓/Enter 统一移动高亮
const mentionItems = computed(() => filteredSkills.value.map((item) => ({ kind: 'skill' as const, item })));

// 滚动区底部渐隐：下方还有候选时优雅淡出，替代生硬的半行截断
const mentionScrollRef = ref<HTMLElement | null>(null);
const mentionFade = ref(false);

function updateMentionFade() {
  const el = mentionScrollRef.value;
  mentionFade.value = !!el && el.scrollTop + el.clientHeight < el.scrollHeight - 6;
}

watch([mentionOpen, mentionItems], () => nextTick(updateMentionFade));

// 行尾作用域小标签（对齐 Codex：右侧灰字）。Skill 按来源。
const SKILL_SOURCE_LABEL: Record<string, string> = { builtin: '内置', upload: '上传', url: '网络' };
function skillTag(item: SkillItem) {
  return SKILL_SOURCE_LABEL[item.source || ''] || '已启用';
}

// 命中高亮：按 @ 查询词把名称/描述切段，命中段加粗加墨——让「为什么剩这几个」可见。
// 过滤是 includes 匹配（name 或 description），这里对两处都做同样的首个命中标记。
function matchParts(text?: string) {
  const s = text || '';
  const q = mentionQuery.value.trim().toLowerCase();
  if (!q) return [{ text: s, hit: false }];
  const idx = s.toLowerCase().indexOf(q);
  if (idx < 0) return [{ text: s, hit: false }];
  return [
    { text: s.slice(0, idx), hit: false },
    { text: s.slice(idx, idx + q.length), hit: true },
    { text: s.slice(idx + q.length), hit: false },
  ].filter((p) => p.text);
}

function onInput(e: Event) {
  const value = (e.target as HTMLTextAreaElement).value;
  emit('update:input', value);
  if (presentationMode.value || uiPolicy.value?.hideMention) {
    mentionOpen.value = false;
    mentionQuery.value = '';
    return;
  }
  const match = value.match(/@([^\s@]*)$/);
  if (match) {
    mentionQuery.value = match[1];
    mentionActive.value = 0; // 过滤条件变化，高亮回到第一项（Codex 式）
    if (!mentionOpen.value) {
      mentionOpen.value = true;
      emit('ensureSkills');
    }
  } else {
    mentionOpen.value = false;
    mentionQuery.value = '';
  }
}

// ↑/↓ 在候选间移动高亮；仅在面板打开时拦截，否则保持文本框默认光标移动
function onMentionNav(e: KeyboardEvent, dir: number) {
  const n = mentionItems.value.length;
  if (!mentionOpen.value || !n) return;
  e.preventDefault();
  mentionActive.value = (mentionActive.value + dir + n) % n;
  nextTick(() => {
    mentionListRef.value
      ?.querySelector('.mention-item.active')
      ?.scrollIntoView({ block: 'nearest' });
  });
}

// 选中当前高亮候选（Enter / Tab 共用）
function pickActiveMention() {
  const list = mentionItems.value;
  const chosen = list[mentionActive.value] || list[0];
  if (!chosen) return;
  pickSkill(chosen.item);
}

// Tab 补全：面板打开时等同 Enter 选中（对齐 Codex）；面板关闭时保持默认焦点行为。
// Shift+Tab 是反向切焦点的习惯键，不劫持。
function onMentionTab(e: KeyboardEvent) {
  if (e.shiftKey) return;
  if (!mentionOpen.value || !mentionItems.value.length) return;
  e.preventDefault();
  pickActiveMention();
}

function onEnter(e: KeyboardEvent) {
  // 输入法组字中按回车是“确认候选词”，不发送——否则会误发、且 compositionend 的
  // 后续 input 事件会把残留文本塞回输入框（用户反馈的 bug）。
  if (e.isComposing || e.keyCode === 229) return;
  const invertFollowUp = (e.metaKey || e.ctrlKey) && e.shiftKey;
  // Shift+Enter 换行；⇧⌘Enter 是跟行取反，要拦截。
  if (e.shiftKey && !invertFollowUp) return;
  e.preventDefault();
  if (mentionOpen.value) {
    pickActiveMention();
    return;
  }
  // 生成中回车默认入队；⇧⌘Enter 对本条取反为立刻调整方向。
  if (canSendComposer.value) {
    emit('send', { invertFollowUp });
  }
}

// 输入框为空时按 Backspace/Delete → 从右到左移除一次性选择（chip-input 常见交互）。
// 有文字时不拦截（正常删字）；输入法组字中不误删。
// 视觉顺序：上传 → 对话 → 我的文件 → 知识库 → Skill → 子智能体 → 网页搜索。
function onComposerBackspace(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return;
  if (props.input !== '') return;
  if (props.webSearch) {
    e.preventDefault();
    emit('toggleWeb');
    return;
  }
  if (props.selectedSkills.length) {
    e.preventDefault();
    emit('removeSkill', props.selectedSkills[props.selectedSkills.length - 1].id);
    return;
  }
  if (props.selectedKnowledgeList.length) {
    e.preventDefault();
    removeSelectedKnowledge(props.selectedKnowledgeList[props.selectedKnowledgeList.length - 1].id);
    return;
  }
  if (props.selectedFileList.length) {
    e.preventDefault();
    removeSelectedFile(props.selectedFileList[props.selectedFileList.length - 1].id);
    return;
  }
  if (props.selectedThreadList.length) {
    e.preventDefault();
    removeSelectedThread(props.selectedThreadList[props.selectedThreadList.length - 1].id);
    return;
  }
  if (props.attachments.length) {
    e.preventDefault();
    emit('removeAttachment', props.attachments.length - 1);
  }
}

// 选中候选后：清掉输入框里的 @查询片段、收起面板、光标回到输入框
function resetMentionInput() {
  emit('update:input', props.input.replace(/@([^\s@]*)$/, ''));
  mentionOpen.value = false;
  mentionQuery.value = '';
  mentionActive.value = 0;
  nextTick(() => textareaRef.value?.focus());
}

function pickSkill(item: SkillItem) {
  emit('selectSkill', item);
  resetMentionInput();
}

/** + 菜单里的「使用技能」：只选中，**不能**走 resetMentionInput——那个是 `@` 面板专用的，
 *  它会把输入末尾的 `@xxx` 正则删掉（用户正打着「…… @张三」跑来选技能就会被吞），
 *  还会把焦点抢回输入框，让人没法在面板里接着挑第二个。 */
function pickSkillFromMenu(item: SkillItem) {
  emit('selectSkill', item);
}
</script>

<style scoped>
.chat-home.work-welcome > .chat-intro {
  width: 100%;
  max-width: var(--chat-content-max-width);
  margin-inline: auto;
  padding-inline: 16px;
  box-sizing: border-box;
}

.chat-intro .work-welcome-description {
  max-width: 640px;
  margin: 14px auto 0;
  font-size: 14px;
  line-height: 1.8;
  text-wrap: balance;
}

.work-welcome-description > span { display: block; }
.work-welcome-primary { color: var(--muted); }
.work-welcome-secondary { margin-top: 2px; color: var(--faint); }

.chat-home.work-welcome .chat-intro-hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--faint);
}

.chat-home.work-welcome.empty-state .composer-dock { margin-top: 32px; }

@media (max-width: 600px) {
  .chat-home.work-welcome > .chat-intro { padding-inline: 8px; }
  .chat-intro .work-welcome-description { max-width: 360px; margin-top: 16px; font-size: 13px; line-height: 1.8; }
  .work-welcome-secondary { margin-top: 6px; }
  .chat-home.work-welcome.empty-state .composer-dock { margin-top: 42px; }
}

.chat-home.interview-state {
  --interview-ink: #171a20;
  --interview-muted: #6d7280;
  --interview-paper: #ffffff;
  --interview-soft: #f5f6f8;
  --interview-line: #e5e7eb;
  --interview-accent: #4f46e5;
  --interview-column: min(820px, calc(100% - 40px));
}

.chat-home.interview-state.empty-state {
  padding-top: 34px;
}

.chat-home.interview-state > .chat-intro {
  margin-top: 0;
}

.chat-home.interview-state > .chat-intro h1 {
  font-size: clamp(25px, 2.7vw, 32px);
  font-weight: 650;
  letter-spacing: -.8px;
  line-height: 1.4;
}

.chat-home.interview-state > .interview-setup {
  position: relative;
  z-index: 3;
  margin-top: 28px;
}

.chat-home.interview-state :deep(.conversation-nav) {
  display: none;
}

.chat-home.interview-state > .interview-panel {
  position: relative;
  z-index: 3;
}

.chat-home.interview-state:not(.empty-state) > .composer-dock {
  width: var(--interview-column);
}

.chat-home.interview-state.interview-conversation :deep(.message-list) {
  width: var(--interview-column);
  max-width: 100%;
}

.chat-home.interview-state :deep(.message) {
  scroll-margin-top: 32px;
}

.chat-home.interview-state.interview-practice :deep(.message-list) {
  padding-bottom: 24px;
}

.chat-intro-agent-mascot {
  width: 104px;
  margin: 0 auto 18px;
  overflow: visible;
}

.composer-agent-mascot {
  position: absolute;
  /* 形象固定在输入框左上；+ / @ 面板只盖住实际重叠区域。 */
  z-index: 4;
  top: -56px;
  left: 84px;
  width: 76px;
  overflow: visible;
}

.composer-agent-mascot.variant-campus {
  /* 512 画布内球体为 (231, 276, r130)：换算后与主 Agent 的球心、直径重合。 */
  top: -83.69px;
  left: 73.24px;
  width: 103.19px;
}

.composer-agent-mascot.variant-presentation {
  /* 512 画布内球体为 (190, 350, r158)：附属页面不参与对齐基准。 */
  top: -86.1px;
  left: 88.29px;
  width: 84.9px;
}

/* 全部输入控制与其弹层必须位于固定形象上方；未相交处的小球仍完整显示。 */
.composer-footer,
.composer-footer-end {
  z-index: 20;
}

.model-switch-state {
  min-width: 0;
  max-width: 280px;
  overflow: hidden;
  color: #777d87;
  font-size: 11px;
  line-height: 28px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-stop {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

/* 停止中禁用态（第四批 2a）：cancel 在途/pending 等收敛期间不可重复点 */
.composer-stop:disabled {
  opacity: 0.55;
  cursor: default;
}

.stop-square {
  display: block;
  width: 11px;
  height: 11px;
  border-radius: 2px;
  background: currentColor;
}

/* 生成中的环绕光晕（B 方案）：conic-gradient 绕 --composer-ang 转，靠 mask-composite
   抠成环形——不额外占布局、不改输入框尺寸。halo 的环刻意整圈落在边框**外侧**
   （inset -9px + 8px 环宽），模糊后只往外洇，不会把蓝色糊到正文上。 */
.composer-glow {
  position: absolute;
  z-index: 0;
  inset: 0;
  border-radius: 12px;
  pointer-events: none;
}

.cg-ring,
.cg-halo {
  position: absolute;
  background: conic-gradient(
    from var(--composer-ang),
    transparent 0deg,
    transparent 236deg,
    rgba(55, 138, 221, 0.2) 262deg,
    rgba(96, 168, 240, 0.95) 296deg,
    rgba(180, 218, 255, 1) 306deg,
    rgba(96, 168, 240, 0.95) 316deg,
    rgba(55, 138, 221, 0.2) 350deg,
    transparent 360deg
  );
  animation: composer-orbit 2.9s linear infinite;
  -webkit-mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  mask:
    linear-gradient(#fff 0 0) content-box,
    linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
}

.cg-ring {
  inset: 0;
  border-radius: 12px;
  padding: 1.5px;
}

.cg-halo {
  inset: -9px;
  border-radius: 20px;
  padding: 8px;
  opacity: 0.55;
  filter: blur(9px);
}

@keyframes composer-orbit {
  to {
    --composer-ang: 360deg;
  }
}

/* 减少动态效果：不转，定格成一圈静态蓝边——状态仍然可见，只是不动 */
@media (prefers-reduced-motion: reduce) {
  .cg-ring,
  .cg-halo {
    animation: none;
    background: rgba(96, 168, 240, 0.75);
  }
}

@media (max-width: 720px) {
  .composer-agent-mascot {
    top: -47px;
    left: 56px;
    width: 64px;
  }

  .composer-agent-mascot.variant-campus {
    top: -70.32px;
    left: 46.94px;
    width: 86.89px;
  }

  .composer-agent-mascot.variant-presentation {
    top: -72.35px;
    left: 59.62px;
    width: 71.5px;
  }

  .chat-intro-agent-mascot {
    width: 92px;
  }
}

.plan-mode-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 12px 8px;
  padding: 8px 10px;
  border: 1px solid #ececef;
  border-radius: 8px;
  background: #fafafa;
  color: #4d525c;
  font-size: 12px;
  line-height: 18px;
}

.plan-mode-hint span {
  flex: 1;
  min-width: 0;
}

.plan-mode-hint-go {
  flex: none;
  height: 24px;
  padding: 0 10px;
  border: 1px solid #111;
  border-radius: 6px;
  background: #111;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}

.plan-mode-hint-dismiss {
  flex: none;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  background: transparent;
  color: #9096a1;
  cursor: pointer;
}

.plan-mode-hint-dismiss:hover {
  color: #111;
}

/* 研究/计划模式共用选中态：浅蓝底、图标常显，完整名称在悬停或键盘聚焦时展开。
   width/flex 显式覆盖全局 `.composer-footer > button { width: 32px }`，否则展开文字会溢出蓝底。 */
.mode-pill {
  --mode-pill-ease: cubic-bezier(0.22, 1, 0.36, 1);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: auto;
  flex: 0 0 auto;
  height: 32px;
  padding: 0 8px;
  border: 1px solid #bfdbfe;
  border-radius: 10px;
  background: #dbeafe;
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition:
    background-color 0.16s ease,
    border-color 0.16s ease,
    padding 0.26s var(--mode-pill-ease);
}

.mode-pill-icon {
  flex: none;
  font-size: 14px;
}

.mode-pill-label {
  max-width: 0;
  margin-left: 0;
  overflow: hidden;
  opacity: 0;
  transform: translateX(-5px);
  white-space: nowrap;
  will-change: max-width, margin-left, opacity, transform;
  transition:
    max-width 0.28s var(--mode-pill-ease),
    margin-left 0.28s var(--mode-pill-ease),
    opacity 0.16s ease,
    transform 0.24s var(--mode-pill-ease);
}

.mode-pill:hover .mode-pill-label,
.mode-pill:focus-visible .mode-pill-label,
.mode-pill.has-status .mode-pill-label {
  max-width: 150px;
  margin-left: 6px;
  opacity: 1;
  transform: translateX(0);
}

.mode-pill:hover,
.mode-pill:focus-visible,
.mode-pill.has-status {
  padding: 0 12px;
}

.mode-pill:hover:not(.locked) {
  background: #c3dafc;
  border-color: #93c5fd;
}

/* 生成中/等待卡片时不可关：仍可悬停读取模式名，但如实变灰且不显示手型。 */
.mode-pill.locked {
  border-color: #e5e7eb;
  background: #f3f4f6;
  color: #9ca3af;
  cursor: default;
}

@media (max-width: 719px) {
  .mode-pill,
  .mode-pill:hover,
  .mode-pill:focus-visible,
  .mode-pill.has-status {
    width: 44px;
    height: 44px;
    padding: 0;
    border-radius: 12px;
  }

  .mode-pill:hover .mode-pill-label,
  .mode-pill:focus-visible .mode-pill-label,
  .mode-pill.has-status .mode-pill-label {
    max-width: 0;
    margin-left: 0;
    opacity: 0;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mode-pill,
  .mode-pill-label {
    transition: none;
  }
}

/* 立即引导（C）：与停止/发送键同尺寸的独立小按钮，不占用主发送键语义 */
.composer-instruct {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid #e4e4e8;
  border-radius: 9px;
  background: #fff;
  color: #6b7280;
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease,
    border-color 0.15s ease;
}

.composer-instruct:hover:not(:disabled) {
  border-color: #c8cad0;
  color: #111827;
}

.composer-instruct:disabled {
  cursor: default;
  opacity: 0.6;
}

/* 运行中排队文案放在同一输入框卡片内：上面是已发送待发的消息，「调整方向」在右上角。 */
.message-queue {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  max-height: max(220px, 30dvh);
  overflow-y: auto;
  margin: 0;
  padding: 12px 18px 0;
  border: 0;
  background: transparent;
  scrollbar-width: thin;
  scrollbar-color: #d4d6dd transparent;
}

.message-queue-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 33px;
  padding: 0 5px;
  color: #9096a1;
  font-size: 12.5px;
}

.message-queue-list {
  display: flex;
  flex-direction: column;
}

.message-queue-list.drag-active,
.message-queue-list.drag-active * {
  cursor: grabbing !important;
}

/* 中断后队列暂停横幅：克制的灰底提示条，右侧「继续」是唯一动作 */
.message-queue-paused {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
  border-radius: 8px;
  background: #f5f6f8;
  padding: 6px 8px;
  color: #6b7280;
  font-size: 12.5px;
}

.message-queue-resume {
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 2px 8px;
  color: #111827;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.message-queue-resume:hover {
  background: #e6e8ec;
}

.queue-card {
  position: relative;
  display: flex;
  min-height: 28px;
  align-items: flex-start;
  gap: 8px;
  padding: 2px 0 8px 24px;
  border-radius: 8px;
  transition:
    background 0.12s ease,
    box-shadow 0.12s ease,
    opacity 0.12s ease;
}

.queue-card + .queue-card {
  border-top: 1px solid #f2f3f5;
}

.queue-sort-chosen {
  background: #f7f7f8;
}

.queue-sort-ghost {
  background: #eef0f3;
  box-shadow: inset 0 0 0 1px #e3e5e9;
  opacity: 0.24;
}

.queue-sort-drag,
.queue-sort-fallback {
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 30px rgb(17 24 39 / 14%);
  opacity: 0.98 !important;
}

.queue-sort-fallback {
  pointer-events: none;
  transform: translateZ(0);
  will-change: transform;
}

.queue-drag-handle {
  position: absolute;
  left: 0;
  top: 4px;
  display: inline-flex;
  width: 20px;
  height: 25px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 0;
  color: #b4b8c0;
  font-size: 13px;
  cursor: grab;
  touch-action: none;
  user-select: none;
  opacity: 1;
  transition: background 160ms ease, color 160ms ease;
}

.queue-drag-handle:hover:not(:disabled) {
  background: #f5f5f6;
  color: #777d87;
}

.queue-drag-handle:active:not(:disabled) {
  cursor: grabbing;
}

.queue-drag-handle:focus-visible {
  outline: 2px solid rgb(79 70 229 / 24%);
  outline-offset: -2px;
}

.queue-drag-handle:disabled {
  cursor: default;
  opacity: 0.35;
}

@media (prefers-reduced-motion: reduce) {
  .queue-card,
  .queue-drag-handle {
    transition: none;
  }
}

/* 派发失败的那一条的警告图标（Codex 逐条失败态） */
.queue-card-warn {
  display: inline-flex;
  flex: none;
  align-items: center;
  margin-right: 2px;
  color: #d97757;
  font-size: 13px;
}

.queue-card-text {
  display: block;
  overflow: hidden;
  flex: 1 1 auto;
  min-width: 0;
  margin: 0;
  padding-top: 4px;
  color: #25272c;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
}

.queue-edit-input {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 29px;
  resize: none;
  border: 0;
  border-radius: 6px;
  background: #f7f7f8;
  padding: 4px 7px;
  color: #202228;
  font: inherit;
  outline: none;
}

.queue-edit-input:focus {
  box-shadow: inset 0 0 0 1px #d8dbe3;
}

.queue-card-actions {
  display: flex;
  flex: none;
  align-items: center;
  gap: 5px;
}

.queue-card-atts {
  margin-right: 2px;
  color: #969ba5;
  font-size: 11px;
  white-space: nowrap;
}

.queue-card-actions button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 25px;
  height: 25px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  padding: 0 4px;
  color: #777c85;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.queue-card-actions button:hover:not(:disabled) {
  background: #f3f3f4;
  color: #24262b;
}

.queue-card-actions button:disabled {
  cursor: default;
  opacity: 0.5;
}

.queue-card-actions .queue-guide-action {
  gap: 4px;
  height: 28px;
  padding-inline: 8px;
  color: #5b616b;
  font-size: 13px;
}

.queue-card-actions .queue-icon-action {
  width: 0;
  min-width: 0;
  padding: 0;
  opacity: 0;
  overflow: hidden;
  pointer-events: none;
}

.queue-card:hover .queue-icon-action,
.queue-icon-action:focus-visible {
  width: 28px;
  min-width: 28px;
  padding: 0 4px;
  opacity: 1;
  overflow: visible;
  pointer-events: auto;
}

.queue-card-actions .queue-icon-action:disabled {
  opacity: 0;
}

.queue-card:hover .queue-icon-action:disabled,
.queue-icon-action:disabled:focus-visible {
  opacity: 0.5;
}

.queue-card-actions .queue-more-action {
  height: 28px;
  border-radius: 50%;
  background: #f5f5f6;
  color: #6c717a;
  font-size: 15px;
}

:global(.queue-action-dropdown .ant-dropdown-menu) {
  min-width: 132px;
  border: 1px solid #e8e9ec;
  border-radius: 12px;
  padding: 4px;
  box-shadow: 0 10px 28px rgba(17, 24, 39, 0.13);
}

:global(.queue-action-dropdown .ant-dropdown-menu-item) {
  min-height: 29px;
  border-radius: 7px;
  padding: 4px 8px;
  color: #2c2f34;
  font-size: 13px;
}

:global(.queue-action-dropdown .ant-dropdown-menu-title-content) {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 智能体/技能命令面板：纯单色「操作台」——名称首字作标记、选中即墨块反白，
   底部键盘提示条收口。整体克制，把唯一的亮点留给标记反白这一下。 */
.mention-picker {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 12px;
  right: auto;
  display: flex;
  flex-direction: column;
  /* 与输入框等宽（对齐 Codex）：面板和输入框读作一体，描述可见更长 */
  width: calc(100% - 24px);
  max-height: 360px;
  overflow: hidden;
  border: 1px solid #ebecf0;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 14px 36px rgba(17, 24, 39, 0.12), 0 2px 6px rgba(17, 24, 39, 0.04);
  z-index: 30;
  animation: mention-rise 0.18s cubic-bezier(0.22, 1, 0.36, 1) both;
}

@keyframes mention-rise {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .mention-picker {
    animation: none;
  }
}

/* 列表滚动区（页脚提示条固定不滚动） */
.mention-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 6px;
  scrollbar-width: thin;
  scrollbar-color: #d4d6dd transparent;
}

.mention-scroll::-webkit-scrollbar {
  width: 6px;
}

.mention-scroll::-webkit-scrollbar-thumb {
  border: 2px solid #fff;
  border-radius: 999px;
  background: #d4d6dd;
}

/* 空状态：同样向上展开（对齐 Codex）。此时输入框在 .workspace(overflow:auto) 内，
   顶部到 workspace 顶的可用高度 ≈ padding-top clamp(68,9vh,110) + 欢迎语块 + 间距，
   超出会被 workspace 裁掉，故按此收紧 max-height，多余项内部滚动。 */
.chat-home.empty-state .mention-picker {
  top: auto;
  bottom: calc(100% + 8px);
  max-height: calc(clamp(68px, 9vh, 110px) + 120px);
}

.mention-empty {
  padding: 16px 14px;
  color: #9096a1;
  font-size: 12.5px;
  text-align: center;
}

/* 分组标题：克制的小号灰字 */
.mention-group-label {
  padding: 9px 11px 4px;
  color: #a2a8b4;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
}

.mention-group-label:first-child {
  padding-top: 3px;
}

/* Codex 式紧凑行：菜单密度而非卡片密度 */
.mention-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  min-height: 34px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  padding: 5px 10px;
  color: #202228;
  cursor: pointer;
  text-align: left;
  transition: background 0.12s ease;
}

.mention-item.active {
  background: #f5f6f8;
}

/* 轻线图标（对齐 Codex）：智能体=机器人，Skill=立方体；选中时微微加深 */
.mention-item-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  color: #9ca3af;
  font-size: 15px;
}

.mention-item.active .mention-item-icon {
  color: #4b5563;
}

.mention-item-name {
  flex: none;
  color: #1f2328;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}

.mention-item-desc {
  flex: 1 1 auto;
  overflow: hidden;
  min-width: 0;
  color: #9096a1;
  font-size: 12.5px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* @ 查询词命中段：名称里加粗提墨，描述里只微微加深——回答「为什么剩这几个」 */
.mention-item-name .mention-hit {
  color: #08090b;
  font-weight: 700;
}

.mention-item-desc .mention-hit {
  color: #4b5563;
  font-weight: 600;
}

/* 行尾作用域标签：右对齐灰字 */
.mention-item-tag {
  flex: none;
  margin-left: auto;
  padding-left: 10px;
  color: #aab0bb;
  font-size: 11.5px;
  white-space: nowrap;
}

/* 底部渐隐：下方还有候选时白色淡出，代替生硬的半行截断 */
.mention-fade {
  position: absolute;
  left: 1px;
  right: 8px; /* 让开滚动条 */
  bottom: 1px; /* 贴面板底边（1px 边框内） */
  height: 26px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), #fff);
  pointer-events: none;
}

/* 附件卡片行：图片缩略图 + 文档/资源卡，置于输入框顶部 */
.composer-attachments {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 14px 2px;
  overflow: visible;
}

.composer-attachments ~ .composer-input-row textarea {
  padding-top: 8px;
}

/* 拖拽悬停高亮 */
.composer.drag-active {
  border-color: #111827;
  box-shadow: none;
}

/* 回到最新消息：悬浮在输入框右上方，跟随输入框定位（含侧栏收起态） */
.chat-scroll-down {
  position: absolute;
  top: -52px;
  left: 50%;
  transform: translateX(-50%);
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid #e4e4e8;
  border-radius: 50%;
  background: #fff;
  color: #333;
  font-size: 16px;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(17, 24, 39, 0.14);
  transition:
    background 0.15s ease,
    color 0.15s ease,
    transform 0.15s ease;
}

.chat-scroll-down:hover {
  background: #111;
  color: #fff;
  transform: translateX(-50%) translateY(-1px);
}

.hidden-file-input {
  display: none;
}






/* 圆形（2026-07-28 用户拍板，对照 Manus）：+ 号与紧邻的连接应用都是正圆图标钮，
   与右侧同为圆形的发送键成一套。ConnectorMenu 的 .cn-trigger 同步改了。 */
.web-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid #e4e4e8;
  border-radius: 50%;
  background: #fff;
  color: #6b7280;
  cursor: pointer;
  transition:
    background 0.18s ease,
    color 0.18s ease,
    border-color 0.18s ease,
    box-shadow 0.18s ease;
}

.web-toggle:hover {
  border-color: #cfd1d8;
  color: #202228;
}

/* + 按钮展开态：浅灰底 + 图标旋转成叉（对标 Grok 输入框 + → ×） */
.web-toggle.active {
  border-color: #c8cad0;
  background: #f0f1f3;
  color: #111827;
}

/* 只转图标本体：+ 旋 45° 视觉上就是 ×，关菜单再转回 0° */
.plus-btn-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  transform: rotate(0deg);
  transition: transform 0.28s cubic-bezier(0.34, 1.15, 0.64, 1);
  will-change: transform;
}

.plus-btn.active .plus-btn-icon {
  transform: rotate(45deg);
}

/* ===== + 菜单（ChatGPT 式） ===== */
.plus-wrap {
  position: relative;
}

.plus-mobile-backdrop,
.plus-mobile-head {
  display: none;
}

/* 窄条菜单（2026-07-28 用户拍板，对照 Manus）：宽度只够放图标 + 名称 + › 箭头。
   每行的一句说明退到 title 里——常驻的说明文字让菜单占掉半个屏幕，而这些入口点过
   一次就记住了。分组靠 .plus-sep 的细线。 */
.plus-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: min(212px, calc(100vw - 28px));
  border: 1px solid #e5e7eb;
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.12);
  padding: 6px;
  z-index: 100;
  animation: plus-menu-enter 0.18s ease both;
}

/* 组分隔线：与菜单内边距对齐，不顶到圆角边 */
.plus-sep {
  height: 1px;
  margin: 5px 8px;
  background: #eef0f3;
}

@keyframes plus-menu-enter {
  from {
    opacity: 0;
    transform: translateY(5px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

@media (prefers-reduced-motion: reduce) {
  .plus-menu {
    animation: none;
  }

  .plus-btn-icon {
    transition: none;
  }
}

.plus-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 34px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  padding: 6px 9px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.plus-item:hover {
  background: #f5f6f8;
}

.plus-item:disabled {
  cursor: default;
  opacity: 0.45;
}

.plus-item:disabled:hover {
  background: transparent;
}

.plus-item-icon {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  color: #6b7280;
  font-size: 15px;
}

.plus-item-name {
  overflow: hidden;
  min-width: 0;
  flex: 1 1 auto;
  color: #1f2328;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.plus-item-check {
  flex: none;
  margin-left: auto;
  color: #1668dc;
  font-size: 13px;
}

/* 手机/平板不再沿用桌面窄条浮层：使用 Teleport 到 body 的底部操作面板。
   完整菜单保留二级选择器空间；仅图片入口按内容撑开。 */
@media (max-width: 1024px) {
  .plus-mobile-backdrop {
    position: fixed;
    z-index: 4090;
    inset: 0;
    display: block;
    padding: 0;
    border: 0;
    background: rgba(17, 24, 39, 0.3);
    cursor: default;
    touch-action: none;
    backdrop-filter: blur(2px);
    animation: plus-backdrop-enter 0.18s ease both;
  }

  .plus-menu {
    position: fixed;
    z-index: 4100;
    right: auto;
    bottom: calc(12px + env(safe-area-inset-bottom));
    left: 50%;
    display: block;
    width: min(520px, calc(100vw - 24px));
    height: min(70dvh, 540px);
    max-height: calc(100dvh - env(safe-area-inset-top) - env(safe-area-inset-bottom) - 24px);
    box-sizing: border-box;
    overflow-x: hidden;
    overflow-y: auto;
    border-color: #dfe1e5;
    border-radius: 22px;
    padding: 8px;
    box-shadow: 0 24px 72px rgba(15, 23, 42, 0.22), 0 4px 18px rgba(15, 23, 42, 0.08);
    overscroll-behavior: contain;
    touch-action: pan-y;
    transform: translateX(-50%);
    animation: plus-sheet-enter 0.22s cubic-bezier(0.22, 1, 0.36, 1) both;
    scrollbar-width: thin;
  }

  .plus-mobile-head {
    position: sticky;
    z-index: 2;
    top: 0;
    display: flex;
    min-height: 58px;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 4px;
    padding: 5px 5px 9px 9px;
    border-bottom: 1px solid #eceef1;
    background: #fff;
  }

  .plus-mobile-head > span {
    display: flex;
    min-width: 0;
    flex-direction: column;
    gap: 2px;
  }

  .plus-mobile-head strong {
    color: #16181d;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  .plus-mobile-head small {
    overflow: hidden;
    color: #8b919b;
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .plus-mobile-close {
    display: grid;
    width: 42px;
    height: 42px;
    flex: none;
    padding: 0;
    place-items: center;
    border: 0;
    border-radius: 13px;
    background: #f2f3f5;
    color: #4d525c;
    cursor: pointer;
    font-size: 15px;
  }

  .plus-mobile-close:hover {
    background: #e8e9ec;
    color: #111;
  }

  .plus-mobile-close:focus-visible,
  .plus-item:focus-visible {
    outline: 2px solid #7786d9;
    outline-offset: 1px;
  }

  .plus-item {
    min-height: 52px;
    gap: 12px;
    padding: 8px 11px;
    border-radius: 12px;
  }

  .plus-item-icon {
    width: 22px;
    font-size: 18px;
  }

  .plus-item-name {
    font-size: 15px;
    font-weight: 560;
  }

  .plus-item-check {
    font-size: 15px;
  }

  .plus-sep {
    margin: 7px 10px;
  }

  .plus-menu.plus-menu-image-only {
    width: min(420px, calc(100vw - 24px));
    height: auto;
    padding: 10px;
  }

  .plus-menu-image-only .plus-mobile-head {
    margin-bottom: 8px;
  }

  .plus-menu-image-only .plus-mobile-close {
    width: 44px;
    height: 44px;
  }

  .plus-menu-image-only .plus-item {
    background: #f5f6f8;
  }

  .plus-menu-image-only .plus-item:hover {
    background: #eceef1;
  }
}

@keyframes plus-sheet-enter {
  from {
    opacity: 0;
    transform: translate(-50%, 18px) scale(0.985);
  }
  to {
    opacity: 1;
    transform: translate(-50%, 0) scale(1);
  }
}

@keyframes plus-backdrop-enter {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .plus-menu,
  .plus-mobile-backdrop {
    animation: none;
  }
}

/* 一次性资源回执已改走 .composer-attachments 的 AttachmentCard，不再用 .selected-skills 行内标签。 */

/* 发送键 tooltip 的两行动作网格（Codex 同款）：左列动作、右列键帽 */
.send-actions {
  display: grid;
  align-items: center;
  gap: 4px 14px;
  grid-template-columns: auto auto;
}

.send-actions kbd {
  justify-self: end;
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.1);
  padding: 1px 6px;
  font-size: 11px;
  font-family: inherit;
  line-height: 1.5;
}

/* 队列暂停确认弹窗 */
.paused-queue-desc {
  margin: 0 0 18px;
  font-size: 14px;
  line-height: 1.7;
  color: #3c4149;
}

.paused-queue-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.pq-btn {
  height: 32px;
  padding: 0 14px;
  border: 1px solid #e2e4e9;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  color: #3c4149;
  cursor: pointer;
  transition: background 160ms ease, border-color 160ms ease;
}

.pq-btn:hover {
  border-color: #c9ccd3;
  background: #f7f8fa;
}

.pq-btn.primary {
  border-color: #1a1a1a;
  background: #1a1a1a;
  color: #fff;
}

.pq-btn.primary:hover {
  background: #333;
}
</style>

<style>
/* @property 必须注册在非 scoped 块里：scoped 只作用于选择器，但自定义属性的注册是全局的，
   放这里语义更清楚。没有它 conic-gradient 的角度无法被 CSS 动画插值（会直接跳变）。 */
@property --composer-ang {
  syntax: '<angle>';
  initial-value: 0deg;
  inherits: false;
}

/* 全局 wireframe 主题把 .ant-modal-body 的 padding 归零（原生弹窗内容贴边陷阱）：
   本弹窗自带内边距，必须在非 scoped 块里恢复，否则文案与按钮直接贴边框。 */
.paused-queue-modal .ant-modal-body {
  padding: 20px 24px 22px;
}

.paused-queue-modal .ant-modal-header {
  padding: 16px 24px;
}
</style>
