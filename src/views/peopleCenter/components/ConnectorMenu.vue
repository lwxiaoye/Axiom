<template>
  <div ref="wrapRef" class="cn-wrap">
    <!-- 触发器随连接状态换脸：一个都没启用时是通用的「连接」图标；启用后直接显示各应用
         自己的图标（启用两个就并排两个），用户不点开也知道这轮对话手上有什么。 -->
    <button
      :class="['cn-trigger', { active: activeConnectors.length > 0 }]"
      type="button"
      :title="triggerTitle"
      :aria-label="triggerTitle"
      @click.stop="toggleOpen"
    >
      <template v-if="activeConnectors.length">
        <span v-for="item in visibleActive" :key="item.id" class="cn-trigger-icon">
          <ConnectorIcon :icon="item.icon" />
        </span>
        <span v-if="overflowCount" class="cn-count">+{{ overflowCount }}</span>
      </template>
      <ConnectorIcon v-else />
    </button>

    <div
      v-if="isOpen"
      :class="['cn-panel', `placement-${placement}`]"
      :style="panelStyle"
      @click.stop
      @keydown.esc.stop="closePanel"
    >
      <!-- ========== 一级：连接器清单 ========== -->
      <!-- 无标题、单行、已连接排前——对齐参考实现：这是个高频的工具条下拉，
           每行加一句副标题会让它胖近一倍，标题栏也纯属占地方。 -->
      <template v-if="view === 'list'">
        <div class="cn-list">
          <div v-if="loading && !connectors.length" class="cn-state">加载中...</div>
          <!-- 「加载失败」必须与「一个都没有」分开说（2026-07-29）。此前 loadConnectors 的
               catch 里只有一句 console.warn，首次失败时列表停在空 → 这里落到下面那句
               「暂无可用的连接器」——**同一句文案既盖着"你真的没连过任何应用"，也盖着
               "请求挂了"**。用户读到的是前者，于是跑去重新授权一遍已经连好的 GitHub。
               而且没有任何重试入口：面板关掉再开会重新拉，但没人知道该这么做。 -->
          <!-- 具体报错退到 title（同本面板「说明退到 title」的既有口径）：这是个 375px 的窄条，
               把 HTTP 原文摊在行内会立刻折行或被省略号吃掉，读不到也占地方。 -->
          <div v-else-if="loadError && !connectors.length" class="cn-state cn-state-error" :title="loadError">
            <span class="cn-state-text">连接器清单加载失败</span>
            <button type="button" class="cn-retry" :disabled="loading" @click.stop="loadConnectors">
              {{ loading ? '重试中…' : '重试' }}
            </button>
          </div>
          <div v-else-if="!connectors.length" class="cn-state">暂无可用的连接器</div>
          <!-- 有旧数据但这次刷新失败：**不清空**（连着的应用不会因为一次请求失败就断开，
               清空反而更假），但下面那些「已启用」可能已经不是最新的——不说这句话，用户会
               把一份过期快照当成现状去发消息。同 loadMessageQueue 失败时的处理口径。 -->
          <div v-else-if="loadError" class="cn-stale" :title="`刷新失败：${loadError}`">
            <span class="cn-state-text">状态可能不是最新</span>
            <button type="button" class="cn-retry" :disabled="loading" @click.stop="loadConnectors">
              {{ loading ? '重试中…' : '重试' }}
            </button>
          </div>
          <div
            v-for="item in sortedConnectors"
            :key="item.id"
            :class="['cn-row', { disabled: !item.available, active: flyoutId === item.id }]"
            :title="rowSubtitle(item)"
            @mouseenter="item.connected && (item.resourceKind || item.account)
              ? openFlyout(item)
              : closeFlyout()"
          >
            <span class="cn-row-icon"><ConnectorIcon :icon="item.icon" /></span>
            <span class="cn-row-name">
              {{ item.name }}
              <span v-if="item.beta" class="cn-tag">Beta</span>
            </span>
            <!-- 已连接且要选资源（GitHub）：资源数 + 向右**并排飞出**二级面板。
                 这里刻意不做成「点进去替换一级」——参考实现是一级常驻、二级贴在右侧，
                 两级同屏可见。替换式导航看着就是另一个东西（用户实测反馈）。 -->
            <!-- 连接器详情（2026-07-29 用户拍板）：悬停该行时才出现。
                 此前这里直接弹「取消授权？」确认框——一个纯危险动作，而用户点这个按钮
                 其实是想**看看这个连接器是什么、能怎么用**。现在打开详情弹窗回答那个问题，
                 断开连接收进弹窗里的「管理」下拉（仍有二次确认，只是不再当入口）。 -->
            <button
              v-if="item.connected"
              type="button"
              class="cn-row-revoke"
              :title="`${item.name} 详情`"
              @click.stop="openDetailModal(item)"
            >
              <Icon icon="lucide:sliders-horizontal" :size="15" />
            </button>
            <button
              v-if="item.connected && (item.resourceKind || item.account)"
              type="button"
              :class="['cn-row-more', { open: flyoutId === item.id }]"
              :title="`管理 ${item.name}`"
              @click.stop="toggleFlyout(item)"
              @mouseenter="openFlyout(item)"
            >
              <span v-if="item.resourceKind" class="cn-row-badge">{{ item.resourceCount }}</span>
              <PremiumChevron direction="right" :size="14" interactive />
            </button>
            <button
              v-else-if="item.connected"
              type="button"
              :class="['cn-switch', { on: item.enabled }]"
              role="switch"
              :aria-checked="item.enabled"
              :disabled="!item.available"
              :title="!item.available ? '功能开发中' : (item.enabled ? '已启用，点击关闭' : '已关闭，点击启用')"
              @click="toggleEnabledFor(item)"
            >
              <span class="cn-switch-dot" />
            </button>
            <button
              v-else
              type="button"
              class="cn-row-link"
              :disabled="!item.available"
              @click="beginConnect(item)"
            >
              连接
            </button>
          </div>
        </div>

        <!-- 二级飞出面板。 -->
        <div
          v-if="flyoutItem"
          :class="['cn-flyout', flyoutSide]"
          @click.stop
          @mouseenter="cancelFlyoutClose"
          @mouseleave="scheduleFlyoutClose"
        >
          <div class="cn-flyout-head">
            <span class="cn-row-icon">
              <ConnectorIcon :icon="flyoutItem.icon" />
            </span>
            <span class="cn-flyout-name">{{ flyoutItem.name }}</span>
            <button
              type="button"
              :class="['cn-switch', { on: flyoutItem.enabled }]"
              role="switch"
              :aria-checked="flyoutItem.enabled"
              :disabled="!flyoutItem.reposAuthorized"
              :title="flyoutItem.reposAuthorized
                ? (flyoutItem.enabled ? '已启用，点击关闭' : '已关闭，点击启用')
                : (flyoutItem.resourceKind
                  ? `还没授权${flyoutItem.resourceLabel}，无法启用`
                  : '尚未完成授权，无法启用')"
              @click="toggleEnabledFor(flyoutItem)"
            >
              <span class="cn-switch-dot" />
            </button>
          </div>

          <!-- 账户型连接器（邮箱）：这里列的是**已连接的账户**，不是可勾选的资源。
               邮箱不做文件夹级白名单（用户拍板，对标参考实现）——用户连的就是他自己
               那一个邮箱，再让他"勾选哪些文件夹才生效"是个连上了却用不了的死胡同。
               一个 provider 可以连多个账户，逐个勾选决定这一轮读哪些。 -->
          <div v-if="!flyoutItem.resourceKind" class="cn-accounts">
            <button
              v-for="acc in flyoutAccounts"
              :key="acc.login"
              type="button"
              :class="['cn-account-row', { checked: acc.selected }]"
              :title="acc.selected ? '已选中，点击取消' : '未选中，点击加入本轮读取范围'"
              :disabled="accountBusy === acc.login"
              @click="toggleAccount(acc)"
            >
              <span class="cn-account-icon"><ConnectorIcon :icon="flyoutItem.icon" /></span>
              <span class="cn-account-name" :title="acc.login || acc.name">
                {{ acc.login || acc.name || flyoutItem.name }}
              </span>
              <CheckOutlined v-if="acc.selected" class="cn-res-tick" />
            </button>
            <!-- 一个都没选中时说清楚后果：开关还开着、连接也还在，但这一轮读不到任何东西。
                 不说的话用户会以为是坏了——而这恰恰是他自己刚做的操作。 -->
            <p v-if="flyoutAccounts.length && !flyoutAccounts.some((a) => a.selected)" class="cn-hint">
              一个账户都没选中，这一轮不会读取任何邮件。
            </p>
            <!-- 再连一个账户。走的就是首次连接那条路，不用另写：OAuth 会带
                 prompt=select_account 让用户挑另一个账号，IMAP 那条会出双输入框。
                 **只在账户型连接器上出现**——GitHub 是资源型（列的是仓库不是账户），
                 给它加这个按钮语义不对。 -->
            <button type="button" class="cn-config-row" @click="beginConnect(flyoutItem)">
              <span class="cn-row-icon"><PlusOutlined /></span>
              <span class="cn-config-name">连接另一个账户</span>
            </button>
            <!-- 与一级面板底部那个「管理连接器」**同名但去处不同**：那个开的是连接器
                 列表弹窗（已添加/浏览），这个开的是**本连接器自己的详情弹窗**。
                 我一度以为两者同屏重复而不加，那个判断的前提是假的——同名不等于同去处。 -->
            <button type="button" class="cn-config-row" @click="openDetailModal(flyoutItem)">
              <span class="cn-row-icon"><SettingOutlined /></span>
              <span class="cn-config-name">管理连接器</span>
            </button>
          </div>

          <div v-if="flyoutItem.resourceKind" class="cn-search">
            <SearchOutlined />
            <input v-model="resourceKeyword" :placeholder="`搜索${flyoutItem.resourceLabel}`" />
          </div>

          <div v-if="flyoutItem.resourceKind" class="cn-list cn-flyout-list">
            <div v-if="resourceLoading && !resources.length" class="cn-state">加载中...</div>
            <div v-else-if="resourceError" class="cn-state">{{ resourceError }}</div>
            <div v-else-if="!filteredResources.length" class="cn-state">
              {{ resources.length ? '没有匹配的结果' : `未找到可用的${flyoutItem.resourceLabel}` }}
            </div>
            <button
              v-for="res in filteredResources"
              :key="res.fullName"
              type="button"
              :class="['cn-res', { checked: selected.has(res.fullName) }]"
              @click="toggleResource(res.fullName)"
            >
              <!-- GitHub 官方 repo 字形（octicon）。之前用 antd BookOutlined 是「一本书」，
                   和 GitHub 自己用的仓库图标不是一个东西。 -->
              <span class="cn-res-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" width="1em" height="1em" fill="currentColor" aria-hidden="true">
                  <path d="M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8Z" />
                  <path d="M5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z" />
                </svg>
              </span>
              <span class="cn-res-name" :title="resourceSubtitle(res)">{{ res.name || res.fullName }}</span>
              <CheckOutlined v-if="selected.has(res.fullName)" class="cn-res-tick" />
            </button>
          </div>

          <!-- 「配置 X」跳的是**该应用的安装/授权范围页**（GitHub 即 App 安装页，
               在那里增删授权仓库），不是对方账号的通用授权管理页——用户点它的意图
               是「改我给了哪些仓库」，不是「看我授权过哪些应用」。 -->
          <!-- 只给**资源型**（GitHub）：它跳的是 App 安装页，用户在那里增删授权仓库，
               有实际作用。账户型（邮箱）跳对方账号的权限页意义不大——用户要改的是
               "用哪个账户"，而那件事在上面的账户列表里就能做，何况还跳出了站外。 -->
          <button
            v-if="flyoutItem.resourceKind"
            type="button"
            class="cn-config-row"
            :disabled="installing"
            @click="beginInstall"
          >
            <span class="cn-row-icon">
              <ConnectorIcon :icon="flyoutItem.icon" />
            </span>
            <span class="cn-config-name">
              {{ installing ? '正在前往…' : `配置 ${flyoutItem.name}` }}
            </span>
            <ExportOutlined />
          </button>
          <p v-if="installError" class="cn-error cn-flyout-error">{{ installError }}</p>
        </div>

        <div class="cn-foot cn-foot-actions">
          <button type="button" class="cn-foot-link" @click="openManage">
            <PlusOutlined />
            <span>添加连接器</span>
          </button>
          <button type="button" class="cn-foot-link" @click="openManage">
            <SettingOutlined />
            <span>管理连接器</span>
          </button>
        </div>
      </template>

      <!-- ========== 授权：跳转登录 / 访问令牌 ========== -->
      <template v-else-if="view === 'auth' && active">
        <div class="cn-head">
          <button type="button" class="cn-back" title="返回" @click="backToList"><PremiumChevron direction="left" :size="16" interactive /></button>
          <span class="cn-title">连接 {{ active.name }}</span>
        </div>

        <!-- 原页跳转：整页跳去 GitHub 授权，授权完由服务端 303 跳回这一页。
             这里只在「正在请求授权地址」的一瞬间出现，所以是转圈而不是等待态。 -->
        <div v-if="authMode === 'oauth'" class="cn-body cn-waiting">
          <span v-if="!authError" class="cn-spinner" aria-hidden="true" />
          <p class="cn-wait-title">{{ authError ? '连接失败' : `正在前往 ${active.name} 授权` }}</p>
          <p class="cn-wait-text">
            {{ authError || '即将跳转到授权页面，完成后会自动回到这里。' }}
          </p>
          <div v-if="authError" class="cn-actions">
            <button
              v-if="active.authKinds.includes('token')"
              type="button"
              class="cn-alt"
              @click="switchToToken"
            >
              改用{{ active.tokenLabel }}连接
            </button>
            <button type="button" class="cn-primary" @click="beginConnect(active)">重试</button>
          </div>
        </div>

        <div v-else class="cn-body">
          <!-- 缺 OAuth 应用凭据时是**静默**退到这里的：不写这句，用户看到的是一个
               完全不同的界面却没有任何解释，只会以为跳转登录根本没做（真机走查实测） -->
          <p v-if="active.authNotice" class="cn-notice">{{ active.authNotice }}</p>

          <!-- 「去哪拿这个令牌」独立成一张卡，与下面「把令牌填进来」分开：这是两件事，
               挤成一坨时用户读完引导已经忘了自己是在填表（用户看线上实机的原话是
               「前端界面弄好一点」，症结就是这段密密麻麻的引导没有节奏）。
               ⚠️ 文案**一个字都不在前端**：步骤是把后端下发的 tokenHelp 按标点断句得到的
               （splitHelpSteps，逐字还原可验），后端改一处这里就跟着变。 -->
          <section v-if="active.tokenHelp || active.tokenLink" class="cn-guide">
            <ol v-if="tokenSteps.length" class="cn-guide-steps">
              <li v-for="(step, i) in tokenSteps" :key="i" class="cn-guide-step">
                <span class="cn-guide-num">{{ i + 1 }}</span>
                <span class="cn-guide-text"
                  ><template v-for="(crumb, ci) in helpCrumbs(step)" :key="ci"
                    ><span v-if="ci" class="cn-guide-arrow">→</span>{{ crumb }}</template
                  ></span
                >
              </li>
            </ol>
            <!-- 拆不出两段以上（本来就是一句话／英文文案／括号不配对）就照原样一整段：
                 给一句话套上「步骤 1」只是噪音。 -->
            <p v-else-if="active.tokenHelp" class="cn-help">{{ active.tokenHelp }}</p>

            <a
              v-if="active.tokenLink"
              class="cn-device-link"
              :href="active.tokenLink"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span>去生成{{ active.tokenLabel }}</span>
              <ExportOutlined />
            </a>
          </section>

          <div class="cn-fields">
            <!-- 账号型授权（QQ 邮箱走 IMAP：邮箱地址 + 授权码两样）。
                 accountLabel 为空＝老的单字段行为，GitHub 等只渲染下面那个令牌框，不要改。
                 用 <label> 裹住 <input> 走**隐式关联**：点标签即聚焦，且不必造 id ——
                 页面上可能同时挂着两个 ConnectorMenu（初始大输入框 + 对话后底部输入框），
                 静态 id 会重复，而重复 id 的 label 只会认到第一个。 -->
            <label v-if="active.accountLabel" class="cn-field">
              <span class="cn-field-label">{{ active.accountLabel }}</span>
              <input
                v-model="accountInput"
                :class="['cn-input', { invalid: !!accountHint }]"
                type="text"
                autocomplete="off"
                spellcheck="false"
                :placeholder="active.accountPlaceholder || ''"
                @keydown.enter="submitToken"
              />
              <!-- 格式不对时就地说清楚。只在**已经输入了内容**之后才出现：空着就唠叨等于
                   一进来就冲用户红一片。不说的话按钮只是变灰、不给理由——而「填了 QQ 号
                   而不是完整邮箱」正是这条校验要拦的最常见错误，默默拦掉等于白拦。 -->
              <span v-if="accountHint" class="cn-field-hint">{{ accountHint }}</span>
            </label>

            <label class="cn-field">
              <span v-if="active.accountLabel" class="cn-field-label">{{ active.tokenLabel }}</span>
              <input
                v-model="tokenInput"
                class="cn-input"
                type="password"
                autocomplete="off"
                :placeholder="`粘贴${active.tokenLabel}`"
                @keydown.enter="submitToken"
              />
            </label>
          </div>

          <p v-if="authError" class="cn-error">{{ authError }}</p>
          <div class="cn-actions">
            <button
              v-if="active.authKinds.includes('oauth')"
              type="button"
              class="cn-alt"
              @click="beginConnect(active)"
            >
              改用登录授权
            </button>
            <!-- 禁用时把「还差什么」挂到 title 上：按钮变灰但不给理由，是这个表单
                 最容易让人卡住的地方。tokenFormError 本来就是那句话，复用即可。 -->
            <button
              type="button"
              class="cn-primary"
              :disabled="submitting || !!tokenFormError"
              :title="tokenFormError || ''"
              @click="submitToken"
            >
              {{ submitting ? '连接中...' : '连接' }}
            </button>
          </div>
        </div>
      </template>

      <!-- ========== 二级：开关 + 资源勾选 ========== -->
      <template v-else-if="view === 'detail' && active">
        <div class="cn-head">
          <button type="button" class="cn-back" title="返回" @click="backToList"><PremiumChevron direction="left" :size="16" interactive /></button>
          <span class="cn-title">{{ active.name }}</span>
          <button
            type="button"
            :class="['cn-switch', { on: active.enabled }]"
            role="switch"
            :aria-checked="active.enabled"
            :title="active.enabled ? '已启用，点击关闭' : '已关闭，点击启用'"
            @click="toggleEnabled"
          >
            <span class="cn-switch-dot" />
          </button>
        </div>

        <!-- 同步失败信息可能很长（带完整 MCP 地址），整段铺开会把面板挤满——默认只露一行，
             点开才展开。用户真机截图里那个占半屏的红块就是这么来的。 -->
        <p
          v-if="active.lastError"
          :class="['cn-error', 'cn-error-row', { expanded: errorExpanded }]"
          :title="active.lastError"
          @click="errorExpanded = !errorExpanded"
        >
          {{ active.lastError }}
        </p>

        <!-- 两段授权的引导：登录只拿到身份，**能读哪些仓库要在 GitHub 上单独授权**。
             只完成第一段时连接器是「连上了却读不到任何东西」，不摆出来用户根本不知道
             还差一步。两段都完成后这条自动消失（是脚手架，不是常驻）。 -->
        <div v-if="active.accountAuthorized && !active.reposAuthorized" class="cn-steps">
          <p class="cn-steps-line">
            <span class="cn-step done"><CheckOutlined /> 授权账户</span>
            <span class="cn-step-sep">—</span>
            <span class="cn-step">○ 授权{{ active.resourceLabel }}</span>
          </p>
          <p class="cn-steps-text">
            还需要在 {{ active.name }} 上选择授权哪些{{ active.resourceLabel }}，选完才能读取。
          </p>
          <button type="button" class="cn-primary" :disabled="installing" @click="beginInstall">
            {{ installing ? '等待授权完成...' : `添加${active.resourceLabel}` }}
          </button>
          <p v-if="installError" class="cn-error">{{ installError }}</p>
          <div class="cn-foot">
            <span class="cn-foot-hint">
              {{ active.account ? `已连接 @${active.account.login}` : '已连接' }}
            </span>
            <button type="button" class="cn-danger" @click="disconnect">断开连接</button>
          </div>
        </div>

        <!-- 关闭态：不列资源，只留一条配置入口（对齐产品参考稿）。
             开着开关才谈得上「读哪些库」，关着还铺一屏列表是噪音。 -->
        <template v-else-if="!active.enabled">
          <a
            class="cn-config-row"
            :href="active.configLink || active.tokenLink"
            target="_blank"
            rel="noopener noreferrer"
          >
            <span class="cn-row-icon"><ConnectorIcon :icon="active.icon" /></span>
            <span class="cn-config-name">配置 {{ active.name }}</span>
            <ExportOutlined />
          </a>
          <div class="cn-foot">
            <span class="cn-foot-hint">
              {{ active.account ? `已连接 @${active.account.login}` : '已连接' }}
            </span>
            <button type="button" class="cn-danger" @click="disconnect">断开连接</button>
          </div>
        </template>

        <template v-else>
          <div class="cn-search">
            <SearchOutlined />
            <input v-model="resourceKeyword" :placeholder="`搜索${active.resourceLabel}`" />
          </div>

          <div class="cn-list">
            <div v-if="resourceLoading && !resources.length" class="cn-state">加载中...</div>
            <div v-else-if="resourceError" class="cn-state">{{ resourceError }}</div>
            <div v-else-if="!filteredResources.length" class="cn-state">
              {{ resources.length ? '没有匹配的结果' : `未找到可用的${active.resourceLabel}` }}
            </div>
            <button
              v-for="res in filteredResources"
              :key="res.fullName"
              type="button"
              :class="['cn-res', { checked: selected.has(res.fullName) }]"
              @click="toggleResource(res.fullName)"
            >
              <!-- 左侧是资源自己的图标、右侧一个 ✓ 表示选中（对齐参考实现）。
                   之前左边放的是方形复选框，是我看走眼了。
                   ⚠️ 仓库列表在本文件里有**两处同构渲染**（飞出面板 + 这里的详情视图），
                   改字形必须两处一起改：只改一处时被改的那处完全正确，截图一看就过，
                   另一处会静默留旧（这次更糟——我删了 BookOutlined 的导入却漏了这里，
                   会变成运行时 resolveComponent 失败、图标全空）。 -->
              <span class="cn-res-icon" aria-hidden="true">
                <svg viewBox="0 0 16 16" width="1em" height="1em" fill="currentColor" aria-hidden="true">
                  <path
                    d="M2 2.5A2.5 2.5 0 0 1 4.5 0h8.75a.75.75 0 0 1 .75.75v12.5a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-8a1 1 0 0 0-.714 1.7.75.75 0 1 1-1.072 1.05A2.495 2.495 0 0 1 2 11.5Zm10.5-1h-8a1 1 0 0 0-1 1v6.708A2.486 2.486 0 0 1 4.5 9h8Z"
                  />
                  <path
                    d="M5 12.25a.25.25 0 0 1 .25-.25h3.5a.25.25 0 0 1 .25.25v3.25a.25.25 0 0 1-.4.2l-1.45-1.087a.249.249 0 0 0-.3 0L5.4 15.7a.25.25 0 0 1-.4-.2Z"
                  />
                </svg>
              </span>
              <span class="cn-res-name" :title="resourceSubtitle(res)">{{ res.fullName }}</span>
              <CheckOutlined v-if="selected.has(res.fullName)" class="cn-res-tick" />
            </button>
          </div>

          <!-- v-if 挡空：QQ 邮箱去掉文件夹白名单后 resourceHint 是空串，
               不挡的话会渲染出一个带上边距的空 <p>，表现为详情里多出一段莫名空白。 -->
          <p v-if="active.resourceHint" class="cn-hint">{{ active.resourceHint }}</p>
          <div class="cn-foot">
            <span class="cn-foot-hint">已选 {{ selected.size }} 个</span>
            <button type="button" class="cn-alt" @click="beginInstall">改授权范围</button>
            <button type="button" class="cn-danger" @click="disconnect">断开连接</button>
          </div>
        </template>
      </template>
    </div>

    <!-- 管理是弹窗不是跳页（用户拍板）：连接器属于设置，跳走一整页会把人从对话里带出去 -->
    <!-- 弹窗里点整张卡片＝看详情（2026-07-29）：复用下面那个 ConnectorDetailModal 实例，
         不另起一套。详情的 z-index 更高，会压在这张列表之上并把它模糊掉。 -->
    <ConnectorsModal
      v-model:open="manageOpen"
      @connect="onManageConnect"
      @detail="onManageDetail"
      @changed="loadConnectors"
    />
    <ConnectorDetailModal
      :open="!!detailItem"
      :item="detailItem"
      :busy="detailBusy"
      @close="detailItem = null"
      @connect="onDetailConnect"
      @disconnect="onDetailDisconnect"
      @use-prompt="onDetailUsePrompt"
      @feedback="detailItem = null"
    />
  </div>
</template>

<script lang="ts">
/** 资源加载的请求序号。**必须放在普通 `<script>` 块**：`<script setup>` 的内容会被
 *  编译进 `setup()`，写在那里的 `let` 是**每个组件实例各自归零**的——本组件虽然
 *  目前只挂一个实例，但那种写法一旦多实例就静默失效，且完全看不出来。 */
let resourceSeq = 0;
</script>

<script setup lang="ts">
/**
 * composer 上的「连接应用」菜单（2026-07-28）。
 *
 * 三个视图共用一个浮层：清单 → 授权 → 详情（开关 + 资源勾选）。做成同一浮层而不是弹窗，
 * 是因为它属于 composer 工具条的一部分，跟知识库/我的文件选择器同级同形态。
 *
 * 资源勾选**即时保存**（不设「确定」按钮）：与知识库/文件选择器的既有语义一致，
 * 而且这个勾选同时是服务端的访问白名单，越早生效越好。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { message } from 'ant-design-vue';
import {
  CheckOutlined, ExportOutlined, PlusOutlined,
  SearchOutlined, SettingOutlined,
} from '@ant-design/icons-vue';
import PremiumChevron from './PremiumChevron.vue';
import Icon from '/@/components/Icon/src/Icon.vue';
import {
  connectWithToken, disconnectConnector, listConnectorResources, listConnectors,
  setConnectorAccountSelected,
  setConnectorEnabled, setConnectorResources, startInstall, startOAuth,
  type ConnectorItem, type ConnectorResource,
} from '../connectors.api';
import { usePickerPlacement } from '../composables/usePickerPlacement';
import ConnectorsModal from './ConnectorsModal.vue';
import ConnectorIcon from './ConnectorIcon.vue';
import ConnectorDetailModal from './ConnectorDetailModal.vue';

const PICKER_OPEN_EVENT = 'center-chat-picker-open';

// 示例提示词/试用一下 → 填进 composer。由父层（ChatTab）转成 update:input，
// 本组件不直接碰输入框状态。
const emit = defineEmits<{ (e: 'usePrompt', text: string): void }>();

const wrapRef = ref<HTMLElement | null>(null);
const isOpen = ref(false);
const { placement, panelStyle } = usePickerPlacement(wrapRef, isOpen, { preferredHeight: 420 });

const loading = ref(false);
// 清单加载失败态（2026-07-29）：**不能只 console.warn**。这份清单决定面板里「有哪些连接器、
// 哪些已启用」，失败时静默留空会与「真的没连接任何应用」显示成同一句话（见模板里那三个分支）。
// 空串=没有未处理的失败；非空=最近一次加载失败，模板据此给出可见提示 + 重试入口。
const loadError = ref('');
const manageOpen = ref(false);
// 当前展开二级面板的连接器 id（null=没展开）。做成「一级常驻 + 二级并排」而不是
// 替换式导航，是因为参考实现就是这样，替换式看着完全是另一个东西。
const flyoutId = ref<string | null>(null);
// 面板往左还是往右飞：右侧放不下就翻到左边，窄屏下不至于溢出视口
const flyoutSide = ref<'right' | 'left'>('right');
let flyoutTimer: ReturnType<typeof setTimeout> | null = null;
const connectors = ref<ConnectorItem[]>([]);
const view = ref<'list' | 'auth' | 'detail'>('list');
const active = ref<ConnectorItem | null>(null);

// —— 授权态 ——
const authMode = ref<'oauth' | 'token'>('oauth');
const authError = ref('');
const installError = ref('');
const errorExpanded = ref(false);
const installing = ref(false);
const tokenInput = ref('');
// 账号型授权的第一个字段（QQ 邮箱地址）。只有 accountLabel 非空的 provider 才用得上。
const accountInput = ref('');
const submitting = ref(false);

// —— 资源勾选态 ——
const resources = ref<ConnectorResource[]>([]);
const resourceLoading = ref(false);
const resourceError = ref('');
const resourceKeyword = ref('');
const selected = ref<Set<string>>(new Set());

/** 触发器上最多并排几个图标；再多用 +N 收口，否则工具条会被挤变形 */
const MAX_TRIGGER_ICONS = 3;
const VISIBLE_CONNECTOR_IDS = new Set(['github', 'qqmail', 'gmail']);

const activeConnectors = computed(() => connectors.value.filter((c) => c.enabled));
// 已连接的排前面（对齐参考实现）：这份清单里真正常用的就那一两个，
// 让它们每次都出现在同一个位置比字母序有用得多。
const flyoutItem = computed(() => connectors.value.find((c) => c.id === flyoutId.value) || null);
const sortedConnectors = computed(() =>
  [...connectors.value].sort((a, b) => Number(b.connected) - Number(a.connected)),
);
const visibleActive = computed(() => activeConnectors.value.slice(0, MAX_TRIGGER_ICONS));
const overflowCount = computed(() => Math.max(0, activeConnectors.value.length - MAX_TRIGGER_ICONS));
const triggerTitle = computed(() =>
  activeConnectors.value.length
    ? `已启用：${activeConnectors.value.map((c) => c.name).join('、')}`
    : '连接应用（GitHub 等）',
);
const filteredResources = computed(() => {
  const keyword = resourceKeyword.value.trim().toLowerCase();
  if (!keyword) return resources.value;
  return resources.value.filter((r) => r.fullName.toLowerCase().includes(keyword));
});

function rowSubtitle(item: ConnectorItem): string {
  if (!item.available) return item.unavailableReason || '暂不可用';
  if (!item.connected) return item.summary;
  if (item.status === 'error') return item.lastError || '连接异常，请重新连接';
  if (!item.resourceCount && item.resourceKind) return `未勾选${item.resourceLabel}，尚未生效`;
  return item.enabled ? '已启用' : '已连接（未启用）';
}

function resourceSubtitle(res: ConnectorResource): string {
  const parts = [res.private ? '私有' : '公开'];
  if (res.language) parts.push(res.language);
  if (res.description) parts.push(res.description);
  return parts.join(' · ');
}

/** 用后端刚回的最新对象替换列表里那一条，并同步 active 引用 */
function applyConnector(next: ConnectorItem) {
  const index = connectors.value.findIndex((c) => c.id === next.id);
  if (index >= 0) connectors.value[index] = next;
  else connectors.value.push(next);
  if (active.value?.id === next.id) active.value = next;
}

async function loadConnectors() {
  loading.value = true;
  try {
    const apps = await listConnectors();
    connectors.value = apps.filter((item) => VISIBLE_CONNECTOR_IDS.has(item.id));
    // 成功一次就把失败态清掉：陈旧提示条不能在数据已经刷新之后还挂着
    loadError.value = '';
  } catch (error: any) {
    console.warn('Failed to load connectors:', error);
    // 已有数据时**故意不清空** connectors：连着的应用不会因为一次请求失败就断开，
    // 清空会让触发器图标从"手上有 GitHub"突然变成通用图标，比过期更误导。
    // 代价是那些「已启用」可能不是最新的，所以必须同时立起可见的陈旧提示（模板 cn-stale）。
    loadError.value = String(error?.message || '').trim() || '加载失败';
  } finally {
    loading.value = false;
  }
}

function toggleOpen() {
  if (isOpen.value) {
    closePanel();
    return;
  }
  document.dispatchEvent(new CustomEvent(PICKER_OPEN_EVENT, { detail: 'connectors' }));
  isOpen.value = true;
  view.value = 'list';
  loadConnectors();
}

function closePanel() {
  isOpen.value = false;
  cancelFlyoutClose();
  flyoutId.value = null;
  // 故意**不停轮询**：授权是在另一个窗口里进行的，用户点回对话框把面板关掉是很自然的
  // 动作，此时停掉等于「授权成功了但界面上什么都没发生」。让它继续跑完，连上后照样
  // 弹提示、触发器图标照样变。
}

function backToList() {
  view.value = 'list';
  active.value = null;
  authError.value = '';
  tokenInput.value = '';
  accountInput.value = '';
}

// ---------------------------------------------------------------- 授权

function cancelFlyoutClose() {
  if (flyoutTimer) {
    clearTimeout(flyoutTimer);
    flyoutTimer = null;
  }
}

/** 移出后延迟收起：指针从一级行斜穿到二级面板要一点时间，立刻收会点不到 */
function scheduleFlyoutClose() {
  cancelFlyoutClose();
  flyoutTimer = setTimeout(() => {
    flyoutId.value = null;
  }, 180);
}

function closeFlyout() {
  scheduleFlyoutClose();
}

function openFlyout(item: ConnectorItem) {
  cancelFlyoutClose();
  if (flyoutId.value === item.id) return;
  flyoutId.value = item.id;
  resourceKeyword.value = '';
  selected.value = new Set(item.resources || []);
  // 右侧放不下就翻到左边（窄屏/贴右边缘时）
  // ⚠️ 面板隐藏时 window.innerWidth 是 0，`任意正数 > 0` 恒真 → 永远翻左，
  // 而且不报错。拿不到有效视口宽就按默认的右侧走，别让一个假值决定布局。
  const rect = wrapRef.value?.getBoundingClientRect();
  const vw = window.innerWidth || 0;
  flyoutSide.value = vw && rect && rect.left + rect.width + 300 > vw ? 'left' : 'right';
  // **账户型连接器不拉资源**（邮箱：授权码/OAuth 本身即整个邮箱的权限，没有可勾的东西）。
  //
  // 原先无条件拉，而模板早就按 resourceKind 分叉、拿回来的文件夹列表根本不渲染。
  // 三重代价：
  //   ① 纯属多余的请求；
  //   ② **是「飞出面板串台」的源头**——它拉回的邮件文件夹写进共享的 resources，
  //      随后落到 GitHub 的面板里（用户实测到的那个 bug）；
  //   ③ IMAP 那条是所有请求里最慢的（建 TLS + LOGIN + LIST），而腾讯官方点名
  //      「脚本频繁访问可能被判异常」——为一份不渲染的数据去撞对方的风控。
  //
  // 下面 loadResources 里的请求令牌仍然保留：它防的是**另一类**——同一个 provider
  // 快速划开又划回，先发的后到照样盖掉后发的。那个场景现在就存在，只是两次拉的是
  // 同一份数据，盖错了也长得一样，看不出来而已。
  if (item.resourceKind) {
    loadResources(item.id);
  } else {
    resources.value = [];
    resourceError.value = '';
    resourceLoading.value = false;
  }
}

function toggleFlyout(item: ConnectorItem) {
  if (flyoutId.value === item.id) flyoutId.value = null;
  else openFlyout(item);
}

// ---- 连接器详情弹窗（2026-07-29 用户拍板）----
// 旧实现是点按钮直接 Modal.confirm「取消授权？」，已整个删掉：那把一个**了解**入口
// 做成了纯危险动作。断开连接现在是详情弹窗内「管理」下拉里的一项，二次确认就地铺一层。
const detailItem = ref<ConnectorItem | null>(null);
const detailBusy = ref(false);

// 命名避让：组件里另有一个 openDetail —— 那是**面板内联**的详情视图（授权流程走完后
// 在下拉里展开），入口和形态都不同。这个是独立弹窗，两者并存不互相替代。
function openDetailModal(item: ConnectorItem) {
  detailItem.value = item;
  flyoutId.value = null;
}

/** 详情弹窗里点「连接」（2026-07-29）：关掉详情后**直接复用** onManageConnect，
 *  不自己再写一遍。它除了 beginConnect 还做了一件容易漏的事——`isOpen = true`
 *  把下拉面板重新打开，因为授权界面（view='auth'）就渲染在那个面板里；漏掉这步
 *  的话点完「连接」界面上什么都不会发生。 */
function onDetailConnect(item: ConnectorItem) {
  detailItem.value = null;
  onManageConnect(item.id);
}

async function onDetailDisconnect(item: ConnectorItem) {
  detailBusy.value = true;
  try {
    applyConnector(await disconnectConnector(item.id));
    message.success(`已断开 ${item.name} 的连接`);
    flyoutId.value = null;
    detailItem.value = null;
  } catch (error: any) {
    message.error(error?.message || '操作失败');
  } finally {
    detailBusy.value = false;
  }
}

/** 示例提示词/试用一下：把整句填进输入框并收起所有面板，用户直接回车即可 */
function onDetailUsePrompt(text: string) {
  detailItem.value = null;
  closePanel();
  if (text) emit('usePrompt', text);
}

function openManage() {
  closePanel();
  manageOpen.value = true;
}

/** 弹窗里点「+」连接：关掉弹窗、走和下拉里同一条授权路径，不另起一套 */
function onManageConnect(providerId: string) {
  const item = connectors.value.find((c) => c.id === providerId);
  if (!item) return;
  manageOpen.value = false;
  isOpen.value = true;
  beginConnect(item);
}

function onManageDetail(item: ConnectorItem) {
  detailItem.value = item;
}

function switchToToken() {
  authError.value = '';
  authMode.value = 'token';
}

async function beginConnect(item: ConnectorItem) {
  active.value = item;
  view.value = 'auth';
  authError.value = '';
  tokenInput.value = '';
  accountInput.value = '';
  // 没配 OAuth 应用凭据时后端不下发 oauth，直接落到令牌那条路
  if (!item.authKinds.includes('oauth')) {
    authMode.value = 'token';
    return;
  }
  authMode.value = 'oauth';
  try {
    // 原页跳转（用户拍板）：不开弹窗。好处是不会被浏览器拦截，也不需要轮询
    // ——授权完服务端会 303 带着结果跳回 returnTo，回来由 consumeReturnStatus 接住。
    const started = await startOAuth(item.id, currentReturnTo());
    window.location.href = started.authorizeUrl;
  } catch (error: any) {
    authError.value = error?.message || '发起授权失败';
  }
}

/** 授权完成后要回到的站内地址：带上当前查询串，回来时尽量还原用户所在的位置 */
function currentReturnTo(): string {
  return `${window.location.pathname}${window.location.search}`;
}

/** 「像不像邮箱」的唯一事实源：禁用按钮的判断与输入框下的提示共用它。
 *  两处各写一份正则，早晚会改了一处漏另一处——那种失配是静默的。 */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// ---- 授权引导的排版（**只断句，不改字**）----
//
// tokenHelp 是后端下发的一整句话，铺在 320px 面板里就是五行密密麻麻的小字，没有任何
// 层次。这里把它拆成带序号的步骤 —— 但**一个字都不在前端写死**：拆的是标点，拼回去
// 与原文逐字相同。操作步骤要是前端也抄一份，后端改文案两边必然打架，而且不会报错。

/** 成对标点。括号/引号**内部**的逗号不是步骤分隔：QQ 邮箱那条末尾的
 *  「（只显示一次，可以生成多个）」按 naive split 会被劈成两个半截步骤。
 *  刻意不收直引号 " / '：开合同字符，靠栈判断会把第二个当成闭合，反而更容易错。 */
const HELP_PAIRS: Record<string, string> = {
  '（': '）', '(': ')', '【': '】', '「': '」', '《': '》', '[': ']', '“': '”',
};

/** 断句点。**不含 ASCII 句点** —— `mail.qq.com`、`Developer settings.` 里的点会把
 *  域名劈开；英文文案因此整段落回段落渲染（见下方 length > 1 的兜底），这是有意的。 */
const HELP_BREAKS = new Set(['，', '；', '。', ';']);

/** 把一段引导文案拆成步骤；拆不出两段以上就回空数组＝交回段落渲染。
 *  括号不配对时后半段会并成一整块（而不是抛错或乱拆），退化方向始终是"少拆"。 */
function splitHelpSteps(text: string): string[] {
  const raw = (text || '').trim();
  if (!raw) return [];
  const out: string[] = [];
  const stack: string[] = [];
  let buf = '';
  for (const ch of raw) {
    if (stack.length && ch === stack[stack.length - 1]) {
      stack.pop();
      buf += ch;
    } else if (HELP_PAIRS[ch]) {
      stack.push(HELP_PAIRS[ch]);
      buf += ch;
    } else if (!stack.length && HELP_BREAKS.has(ch)) {
      const seg = buf.trim();
      if (seg) out.push(seg);
      buf = '';
    } else {
      buf += ch;
    }
  }
  const tail = buf.trim();
  if (tail) out.push(tail);
  // 只有一段＝这条文案本来就是一句话，套上「步骤 1」反而是噪音
  return out.length > 1 ? out : [];
}

/** 一步之内的 `A → B → C` 是**导航路径**，不是三个并列动作。拆出来是为了给箭头
 *  单独上次要色；文字本身原样返回。 */
function helpCrumbs(step: string): string[] {
  return step.split('→').map((s) => s.trim()).filter(Boolean);
}

/** 飞出面板里显示的账户名。邮箱取 login（就是邮箱地址），没有就退回昵称、
 *  再没有就退回连接器名——**不留空**：一行空白比一行退化文案更像坏了。 */
/** 飞出面板里的账户列表。后端没下发 accounts 时（前后端不同步部署）退回单账户，
 *  免得面板整个空掉——空面板比少一个勾选框更像坏了。 */
const flyoutAccounts = computed(() => {
  const item = flyoutItem.value;
  if (!item) return [];
  if (item.accounts?.length) return item.accounts;
  const acc = item.account;
  return acc ? [{ login: acc.login, name: acc.name, avatar: acc.avatar,
                  selected: true, status: '', lastError: '' }] : [];
});

/** 正在切换的那个账户（按 login 标记），避免连点造成的乱序回执 */
const accountBusy = ref('');

async function toggleAccount(acc: { login: string; selected: boolean }) {
  const item = flyoutItem.value;
  if (!item || accountBusy.value) return;
  accountBusy.value = acc.login;
  try {
    const next = await setConnectorAccountSelected(item.id, acc.login, !acc.selected);
    applyConnector(next);
  } catch (error: any) {
    message.error(error?.message || '操作失败');
  } finally {
    accountBusy.value = '';
  }
}

const tokenSteps = computed(() => splitHelpSteps(active.value?.tokenHelp || ''));

/** 账号格式不对时给输入框下的就地提示。**只在已经输入内容之后才出现**：
 *  空着就提示等于一进表单就先红一片。字段为空由按钮禁用去表达即可。 */
const accountHint = computed(() => {
  const item = active.value;
  if (!item?.accountLabel) return '';
  const acc = accountInput.value.trim();
  if (!acc) return '';
  if (item.accountFormat === 'email' && !EMAIL_RE.test(acc)) {
    return `请填写完整的${item.accountLabel}${item.accountPlaceholder ? `，例如 ${item.accountPlaceholder}` : ''}`;
  }
  return '';
});

/** 账号型授权（如 QQ 邮箱 IMAP）还差什么——空串＝可以提交。
 *  提交前就把话说清楚，比让用户点了「连接」再吃一个后端报错好。 */
const tokenFormError = computed(() => {
  const item = active.value;
  if (!item) return '';
  if (!tokenInput.value.trim()) return `请填写${item.tokenLabel}`;
  if (!item.accountLabel) return '';
  const acc = accountInput.value.trim();
  if (!acc) return `请填写${item.accountLabel}`;
  // 只做「像不像邮箱」这一层：真正能不能登录由后端连 IMAP 说了算，
  // 前端把格式明显不对的挡掉即可（常见错误是填 QQ 号而不是完整邮箱地址），
  // 别自作聪明限制域名——163/126 将来接进来走的是同一条路径。
  //
  // 触发条件读后端显式下发的 `accountFormat`，不猜常量也不从文案里反推。
  // 这里前后翻过两次车，都值得记着：
  //   一版写 `resourceKind === 'mailbox'`——后端实际是 'folder'（邮件文件夹），
  //     **猜了常量值没去查**，整段校验成了永不触发的死代码，而它看着像"已经有校验了"；
  //   二版改成「占位示例里有没有 @」——能跑，但是**隐性耦合**：后端哪天换个示例文案，
  //     校验就静默失效，失效的表现还是"看着像有校验"。
  // 现在这版由后端明说「这个字段是 email」，改文案不会把它带坏。
  if (item.accountFormat === 'email' && !EMAIL_RE.test(acc)) {
    return `${item.accountLabel}格式不对`;
  }
  return '';
});

async function submitToken() {
  if (!active.value || submitting.value) return;
  if (tokenFormError.value) {
    authError.value = tokenFormError.value;
    return;
  }
  submitting.value = true;
  authError.value = '';
  try {
    const next = await connectWithToken(
      active.value.id,
      tokenInput.value.trim(),
      active.value.accountLabel ? accountInput.value.trim() : undefined,
    );
    tokenInput.value = '';
  accountInput.value = '';
    accountInput.value = '';
    applyConnector(next);
    message.success(`${next.name} 已连接`);
    openDetail(next);
  } catch (error: any) {
    authError.value = error?.message || '连接失败';
  } finally {
    submitting.value = false;
  }
}

/** 第二段授权：新窗口跳 GitHub App 安装页选仓库，这里轮询等 installation 落库。
 *  与第一段共用同一套「开窗占手势 → 轮询 → 关窗」的节奏，失败形态也一致。 */
async function beginInstall() {
  // 飞出面板和「详情」视图共用这条路径：谁在前台就用谁
  const item = flyoutItem.value || active.value;
  if (!item || installing.value) return;
  installError.value = '';
  installing.value = true;
  try {
    const started = await startInstall(item.id, currentReturnTo());
    window.location.href = started.installUrl;   // 同样原页跳转
  } catch (error: any) {
    installing.value = false;
    installError.value = error?.message || '发起授权失败';
  }
}

// ---------------------------------------------------------------- 详情

function openDetail(item: ConnectorItem) {
  installing.value = false;
  installError.value = '';
  errorExpanded.value = false;
  active.value = item;
  view.value = 'detail';
  resourceKeyword.value = '';
  selected.value = new Set(item.resources || []);
  loadResources(item.id);
}

async function loadResources(providerId: string) {
  // 请求令牌：只有**最后一次**发起的请求有权写状态。
  //
  // 悬停在连接器行之间来回划时，两次加载会并发在飞，而它们的响应**不保证按发起顺序回来**：
  // IMAP 那条要建 TLS + LOGIN + LIST，比 GitHub 的 MCP 慢得多。于是
  //   悬停 QQ 邮箱 → 划到 GitHub → qqmail 的响应后到 → 把邮件文件夹写进了 GitHub 的面板。
  // 表现是标题写着「GitHub」、搜索框写着「搜索代码库」，列表里却是收件箱/已发送，
  // 过一会儿又自己变回仓库（github 的响应终于到了）。
  //
  // 「自己会恢复」正是它难被报出来的原因——用户再看一眼往往已经正常，容易被当成看花眼。
  // 而它不只是观感问题：这个面板是能点的，用户可能以为自己在给 GitHub 勾选邮件文件夹。
  //
  // 不用 `providerId !== flyoutId.value` 判：同一个 provider 快速划开又划回时，
  // 两次请求的 providerId 相同，那个判据区分不了先后。自增序号才唯一。
  const seq = ++resourceSeq;
  resourceLoading.value = true;
  resourceError.value = '';
  resources.value = [];
  try {
    const list = await listConnectorResources(providerId);
    if (seq !== resourceSeq) return;
    resources.value = list;
  } catch (error: any) {
    // 失败分支同样要判：否则旧请求的错误会盖在新面板上，显示成新连接器加载失败
    if (seq !== resourceSeq) return;
    resourceError.value = error?.message || '加载失败';
  } finally {
    // **finally 尤其要判**：只判成功分支的话，旧请求回来会把新请求的 loading 关掉，
    // 表现是「转圈没了但列表是空的」——看起来像"这个连接器没有任何资源"。
    if (seq === resourceSeq) resourceLoading.value = false;
  }
}

async function toggleResource(fullName: string) {
  // 飞出面板和「详情」视图共用这条路径：谁在前台就用谁（同 beginInstall 的写法）。
  // 2026-07-29 修：这里原先只认 `active`，而二级面板改成飞出式之后渲染用的是
  // `flyoutItem`、从不进 view==='detail'，于是 `active` 恒为 null——函数第一行就
  // return，勾选仓库彻底空转（实测：零网络请求、零 toast、class 不变，三者同时为
  // 空正是"守卫拦下"区别于"保存失败回滚"的判据）。
  const item = flyoutItem.value || active.value;
  if (!item) return;
  const next = new Set(selected.value);
  if (next.has(fullName)) next.delete(fullName);
  else next.add(fullName);
  const previous = selected.value;
  selected.value = next; // 先动 UI，失败再回滚——勾选要跟手
  try {
    applyConnector(await setConnectorResources(item.id, [...next]));
  } catch (error: any) {
    selected.value = previous;
    message.error(error?.message || '保存失败');
  }
}

async function toggleEnabledFor(item: ConnectorItem) {
  try {
    applyConnector(await setConnectorEnabled(item.id, !item.enabled));
  } catch (error: any) {
    message.error(error?.message || '操作失败');
  }
}

async function toggleEnabled() {
  // 同 toggleResource：不能只认 `active`。这条目前不可达（飞出面板里的开关直接绑
  // `toggleEnabledFor(flyoutItem)`），但留着旧写法等于埋一颗同款的雷——哪天有人把
  // 飞出面板的开关改成调它，就会静默失效且毫无报错。
  const item = flyoutItem.value || active.value;
  if (item) await toggleEnabledFor(item);
}

async function disconnect() {
  if (!active.value) return;
  const name = active.value.name;
  try {
    applyConnector(await disconnectConnector(active.value.id));
    message.success(`已断开 ${name}`);
    backToList();
  } catch (error: any) {
    message.error(error?.message || '断开失败');
  }
}

// ---------------------------------------------------------------- 生命周期

function handleClickOutside() {
  closePanel();
}

function handlePickerOpen(event: Event) {
  if ((event as CustomEvent<string>).detail !== 'connectors') closePanel();
}

/**
 * 接住授权回跳的结果。
 *
 * 原页跳转必须有这一环：授权是整页跳走再跳回来的，组件是**重新挂载**的，
 * 内存里的等待态早没了。不读这几个查询参数的话，用户授权完回到对话会发现
 * 「什么都没发生」——功能其实成了，只是没人告诉他。
 *
 * 读完立刻用 replaceState 把参数擦掉，否则刷新一次就重复提示一次。
 */
async function consumeReturnStatus() {
  const params = new URLSearchParams(window.location.search);
  const provider = params.get('connector');
  const status = params.get('connector_status');
  if (!provider || !status) return;

  const detail = params.get('connector_detail') || '';
  params.delete('connector');
  params.delete('connector_status');
  params.delete('connector_detail');
  const query = params.toString();
  window.history.replaceState({}, '', window.location.pathname + (query ? `?${query}` : ''));

  await loadConnectors();
  const item = connectors.value.find((c) => c.id === provider);
  if (!item) return;
  if (status === 'installed') {
    message.success(detail ? `已授权 ${detail} 个${item.resourceLabel}` : `${item.name} 已连接`);
  } else {
    // 只完成第一段：直接把他带到「添加仓库」那一步，否则他并不知道还差什么
    message.info(`${item.name} 已连接，还需要选择要授权的${item.resourceLabel}`);
    isOpen.value = true;
    openDetail(item);
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside);
  document.addEventListener(PICKER_OPEN_EVENT, handlePickerOpen);
  // 进页面就拉一次：触发器图标要立刻反映「这轮对话手上有 GitHub」，
  // 等用户点开菜单才加载的话，图标会先是通用的再突然变，像坏了
  loadConnectors();
  consumeReturnStatus();
});

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside);
  document.removeEventListener(PICKER_OPEN_EVENT, handlePickerOpen);
});
</script>

<style scoped>
.cn-wrap {
  /* 设计令牌取自 Manus 线上计算样式实测（2026-07-28）：
     文字 rgb(52,50,45) / 次要 rgb(133,132,129) / 面 rgb(248,248,247)
     描边 rgba(0,0,0,.06)（分隔）与 rgba(0,0,0,.12)（控件）
     圆角 弹层16 / 卡片12 / 控件8；字号 标题16 · 控件14 · 次要12
     —— 之前用的是冷灰体系（var(--cn-text)/var(--cn-muted)/#fff），这是"看着不像"的主因。 */
  --cn-text: #34322d;
  --cn-muted: #858481;
  --cn-surface: #f8f8f7;
  --cn-line: rgba(0, 0, 0, 0.06);
  --cn-line-strong: rgba(0, 0, 0, 0.12);
  --cn-hover: rgba(0, 0, 0, 0.04);
  --cn-r-panel: 12px;
  --cn-r-card: 12px;
  --cn-r-ctl: 8px;
  position: relative;
}

/* 圆形（2026-07-28 用户拍板，对照 Manus）：与紧邻的 + 号同一形状。
   注意 .active 态会横向撑开放多个应用图标，那时必须退回胶囊（见下），
   否则 border-radius:50% 在非等宽高元素上会拉成椭圆。 */
.cn-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  width: 34px;
  height: 34px;
  border: 1px solid var(--cn-line-strong);
  border-radius: 50%;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
  color: var(--cn-text);
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.cn-trigger:hover {
  border-color: var(--cn-line-strong);
  background: var(--cn-hover);
  color: var(--cn-text);
}

.cn-trigger.active {
  width: auto;
  min-width: 34px;
  padding: 0 10px;
  /* 启用后横向变长（并排多个应用图标 + 计数），此时用胶囊而不是 50%——
     后者在宽高不等的元素上会把边框拉成椭圆 */
  border-radius: 999px;
  border-color: var(--cn-line-strong);
  background: var(--cn-line);
  color: var(--cn-text);
}

/* 多个已启用应用的图标并排；轻微负间距让它们读起来是一组而不是几个按钮 */
.cn-trigger-icon {
  display: inline-flex;
  align-items: center;
  font-size: 15px;
}

.cn-trigger-icon + .cn-trigger-icon {
  margin-left: -1px;
}

.cn-trigger:focus {
  outline: none;
}

.cn-trigger:focus-visible,
.cn-row-link:focus-visible,
.cn-res:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.cn-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--cn-text);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
}

.cn-panel {
  position: absolute;
  left: 0;
  display: flex;
  max-height: var(--picker-available-height, 420px);
  flex-direction: column;
  /* 参考实现实测约 375px；一级菜单与右侧预览卡并排时保持该比例。 */
  width: min(375px, calc(100vw - 32px));
  border: 1px solid var(--cn-line);
  border-radius: var(--cn-r-panel);
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.12);
  padding: 8px;
  color: var(--cn-text);
  z-index: 100;
  animation: cn-enter 0.18s ease both;
}

.cn-panel.placement-top {
  bottom: calc(100% + 8px);
  transform-origin: left bottom;
}

.cn-panel.placement-bottom {
  top: calc(100% + 8px);
  transform-origin: left top;
}

@keyframes cn-enter {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

/* 根因不是「某条全局规则漏进浮层」，而是**项目里压根没有 button reset**——
   实测到的 background rgb(239,239,239) + padding 1px 6px 就是 Chrome 对 <button>
   的 UA 默认样式（buttonface 底色）。所以影响面按「哪些 button 没自己设 background」
   划，不按「哪些容器是浮层」划。这里统一清零，各自样式再往上加。

   ⚠️ 必须用 `:where()` 把它的特异性压成 (0,0,1)。写成 `.cn-panel button` 是 (0,1,1)，
   会**压过各按钮自己的类规则** (0,1,0) —— 实测把 `.cn-res` 的 padding 清成 0，
   行高从 32px 塌到 17px。清零是给"没人管的 button"兜底的，不该跟具名规则抢。 */
:where(.cn-panel) button {
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: inherit;
  /* ⚠️ 这里**不能**写 `font: inherit`：`.cn-panel button` 的特异性(0,1,1)高过
     `.cn-foot-link`(0,1,0)，简写会把各按钮自己设的 font-size 一并顶掉——实测把
     14px/12px 全变成了继承来的 16px。只统一字族，字号交给各自的规则。 */
  font-family: inherit;
  line-height: normal;
}

.cn-head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 6px;
  padding: 2px 4px 8px;
}

.cn-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: var(--cn-text);
}

.cn-back {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: var(--cn-r-ctl);
  background: transparent;
  color: var(--cn-muted);
  cursor: pointer;
}

.cn-back:hover {
  background: var(--cn-hover);
  color: var(--cn-text);
}

.cn-list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
  min-height: 0;
}

.cn-state {
  padding: 14px 8px;
  color: var(--cn-muted);
  font-size: 12px;
  text-align: center;
}

/* 失败态与陈旧提示（2026-07-29）：克制的黑白风格——不上红底、不加图标，
   只把文案和一个「重试」并排放好。这是个窄条下拉，任何装饰都会让它变形。 */
.cn-state-error,
.cn-stale {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 12px;
}

/* 陈旧提示挂在列表**顶部**、贴着下面的行，压到一行高：它是给现有数据加的一句限定语，
   不是一个独立空状态，占 14px 上下留白会把它读成"列表就这一条" */
.cn-stale {
  flex: none;
  padding: 6px 8px;
  border-bottom: 1px solid var(--cn-line);
  color: var(--cn-muted);
}

.cn-state-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cn-retry {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--cn-line-strong);
  border-radius: 6px;
  background: transparent;
  color: var(--cn-text);
  cursor: pointer;
  font-size: 12px;
}

.cn-retry:hover:not(:disabled) {
  background: var(--cn-hover);
}

.cn-retry:disabled {
  color: var(--cn-muted);
  cursor: not-allowed;
}

.cn-retry:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 0;
}

.cn-row {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 8px 0 4px;
  border-radius: var(--cn-r-ctl);
}

.cn-row:hover {
  background: var(--cn-hover);
}

.cn-row.disabled {
  opacity: 0.55;
}

.cn-row-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  color: var(--cn-text);
  font-size: 16px;
}

.cn-row-name {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 5px;
  min-width: 0;
  color: var(--cn-text);
  font-size: 14px;
}

.cn-tag {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--cn-line);
  color: var(--cn-muted);
  font-size: 10px;
}

.cn-row-link {
  flex: none;
  border: 0;
  background: transparent;
  color: var(--cn-text);
  cursor: pointer;
  font-size: 12px;
}

.cn-row-link:disabled {
  color: var(--cn-muted);
  cursor: not-allowed;
}

/* 悬停该行才显形：这是个危险动作，常驻会让整列都挂着「取消授权」 */
.cn-row-revoke {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--cn-muted);
  cursor: pointer;
  font-size: 13px;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease;
}

.cn-row:hover .cn-row-revoke,
.cn-row-revoke:focus-visible {
  opacity: 1;
}

.cn-row-revoke:hover {
  background: var(--cn-hover);
  color: var(--cn-text);
}

.cn-row-more {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 5px;
  border: 0;
  background: transparent;
  color: var(--cn-muted);
  cursor: pointer;
  font-size: 12px;
}

.cn-row-badge,
.cn-row-more {
  font-size: 12px;
}

.cn-row-more:hover {
  color: var(--cn-text);
}

.cn-row-badge {
  color: var(--cn-muted);
  font-size: 12px;
}

.cn-foot {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 4px 2px;
  border-top: 1px solid var(--cn-line);
  margin-top: 6px;
}

.cn-foot-hint,
.cn-hint {
  color: var(--cn-muted);
  font-size: 12px;
}

.cn-hint {
  flex: none;
  padding: 6px 4px 0;
  line-height: 1.5;
}

.cn-danger {
  border: 0;
  background: transparent;
  color: #a1121f;
  cursor: pointer;
  font-size: 12px;
}

/* ---- 授权 ---- */
.cn-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding: 0 4px 2px;
}

.cn-help {
  margin: 0;
  color: var(--cn-text);
  font-size: 12px;
  line-height: 1.6;
}

/* 等待授权：一个安静的转圈 + 一句说明 */
.cn-waiting {
  align-items: center;
  padding: 18px 10px 8px;
  text-align: center;
}

.cn-spinner {
  width: 26px;
  height: 26px;
  border: 2px solid var(--cn-line);
  border-top-color: var(--cn-text);
  border-radius: 50%;
  animation: cn-spin 0.8s linear infinite;
}

@keyframes cn-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .cn-spinner {
    animation-duration: 2.4s;
  }
}

.cn-wait-title {
  margin: 4px 0 0;
  color: var(--cn-text);
  font-size: 14px;
  font-weight: 600;
}

.cn-wait-text {
  margin: 0;
  color: var(--cn-muted);
  font-size: 12px;
  line-height: 1.7;
}

/* 关闭态详情里的「配置 X」入口 */
.cn-config-row {
  display: flex;
  flex: none;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 42px;
  padding: 0 8px;
  border-top: 1px solid var(--cn-line);
  border-radius: 0 0 var(--cn-r-panel) var(--cn-r-panel);
  background: transparent;
  color: var(--cn-text);
  cursor: pointer;
  font-size: 14px;
  text-align: left;
}

.cn-config-row:disabled {
  color: var(--cn-muted);
  cursor: progress;
}

.cn-flyout-error {
  padding: 6px 8px 0;
}

.cn-config-row:hover {
  background: var(--cn-hover);
  color: var(--cn-text);
}

.cn-config-name {
  flex: 1;
  text-align: left;
}

/* ---- 授权引导卡 ---- */
/* 「去哪拿令牌」整块收进一张卡，用的就是 ConnectorsModal 里 .cm-card 那一套
   （1px var(--cn-line) 描边 + rgba(0,0,0,.02) 填充 + 12px 圆角/内边距）。
   参考实现里卡片是**有底色填充**的，只描边会让这块发飘。 */
/* ⚠️ 这张卡是本表单里**唯一该让步的块**，别再给它 `flex: none`。

   面板高度由 `--picker-available-height` 给定（composer 贴近视口底部时只有 300 出头），
   而「引导卡 + 两个字段 + 操作行」本来就装不下。2026-07-29 实测过一次两者叠加的事故：
   卡高 207 / 内容总高 406 / 可视 285，结果是**邮箱框被不透明的 sticky 操作条盖住、
   授权码框整个落在面板底之下 17px**——用户打开这个表单，两个要填的框一个看不清、
   一个看不见。

   靠调 padding/gap/行高把卡"压瘦"是治不好的：那是在赌一个固定高度能同时适配所有
   视口，而卡片内容还随 provider 的 tokenHelp 长度变化。正确做法是让它**按剩余空间
   收缩并自己滚动**——字段和按钮是用户来这儿要用的东西，必须常驻；说明文字可以滚。

   ⚠️ `min-height` 必须是 **0**，不能给一个"至少露出一步半"的下限。我第一版写了 72px，
   在 351px 的面板上两条判据都过，看着完全没问题；把 --picker-available-height 逐档
   压下去才发现 **300 就退回同一个 bug**（引导卡卡在地板不肯让 → 输入框又被吸底条盖住），
   而原注释里记的真实最小值就是"300 出头"。一个只在宽松档位成立的修法等于没修。

   逐档实测（输入框+按钮全可见 / 吸底条不压输入框）：
     min-height:72px → 351 ✅✅ | 300 ✅❌ | 260 ✅❌ | 220 ❌❌
     min-height:0    → 351 ✅✅ | 300 ✅✅ | 260 ✅✅ | 220 ❌❌
   0 之后仍有 22px 下限，那是卡片自己的 padding(10×2)+border(1×2)，CSS 压不掉；
   面板矮到 220 以下时会再次重叠，但那个高度连两个字段加一个按钮都放不下，
   不是这条规则该解决的问题。 */
.cn-guide {
  display: flex;
  flex: 1 1 auto;
  min-height: 0;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding: 10px;
  border: 1px solid var(--cn-line);
  border-radius: var(--cn-r-card);
  background: rgba(0, 0, 0, 0.02);
}

.cn-guide-steps {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

/* 序号与正文**分列**（grid 而不是 inline 序号）：正文换行时缩进对齐在文字左边缘，
   不会绕回序号底下——第 1 步在 320px 面板里必然折行，这是它读起来齐不齐的关键。 */
.cn-guide-step {
  display: grid;
  align-items: start;
  gap: 8px;
  grid-template-columns: 16px minmax(0, 1fr);
}

.cn-guide-num {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  /* 与 12px/1.5 正文的首行对中：行高 18，(18-16)/2 = 1 */
  margin-top: 1px;
  border: 1px solid var(--cn-line-strong);
  border-radius: 50%;
  background: #fff;
  color: var(--cn-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.cn-guide-text {
  color: var(--cn-text);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

/* 路径箭头退到次要色：一步之内的「【设置】→【账号与安全】」是导航路径，不是并列动作，
   与正文同色时整行看着像四五件要分别去做的事。 */
.cn-guide-arrow {
  padding: 0 4px;
  color: var(--cn-muted);
}

/* 外链做成**次级按钮**（对齐 ConnectorsModal 的 .cm-btn.outlined）：它是这张卡里
   唯一要点的东西，而 12px 的下划线文字在一堆 12px 说明里根本读不出是可点的。 */
.cn-device-link {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 30px;
  padding: 0 10px;
  border: 1px solid var(--cn-line-strong);
  border-radius: var(--cn-r-ctl);
  background: #fff;
  color: var(--cn-text);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.cn-device-link:hover {
  border-color: rgba(0, 0, 0, 0.22);
  background: var(--cn-hover);
  color: var(--cn-text);
}

/* ---- 授权表单字段 ---- */
/* 一个字段＝标签 + 输入框（+ 可选的就地提示）作为一整块，块内 6px、块间 12px。
   原先标签的 margin 和 .cn-body 的 gap 叠在一起，行距变成 8/12/16 三种，
   看着就是"挤在一起"而不是一张表单。 */
.cn-fields {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 10px;
  margin-top: 2px;
}

.cn-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

/* 账号型授权有两个输入框，必须各自带标签——两个长得一样的框叠在一起，
   光靠 placeholder 区分，用户一旦开始输入就再也看不出哪个是哪个。 */
.cn-field-label {
  color: var(--cn-muted);
  font-size: 12px;
  line-height: 1.25;
}

.cn-input {
  width: 100%;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--cn-line-strong);
  border-radius: var(--cn-r-ctl);
  outline: none;
  background: var(--cn-hover);
  color: var(--cn-text);
  font-size: 14px;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
}

.cn-input::placeholder {
  color: var(--cn-muted);
}

/* 与全局表单控件保持同一套轻量焦点态，避免人员中心出现粗黑框。 */
.cn-input:focus {
  border-color: #818cf8;
  background: #fff;
  box-shadow: none;
}

/* 错误态用与 .cn-error 同一个红（#a1121f 是本文件既有的硬编码值，不另立变量）。
   聚焦时也保持红边：正在改的就是这个错，焦点态把红盖掉等于提示自己消失了。 */
.cn-input.invalid,
.cn-input.invalid:focus {
  border-color: #a1121f;
  box-shadow: none;
}

/* 就地提示刻意**不用**报错红：用户还在打字，这是引导不是失败，真提交失败才归 .cn-error。
   ⚠️ 不复用 .cn-hint —— 那个类详情视图的 resourceHint 也在用，动它会连那处的间距
   一起改掉（本文件既有教训：把共用的东西当成自己的改，失配是静默的）。 */
.cn-field-hint {
  color: var(--cn-muted);
  font-size: 12px;
  line-height: 1.5;
}

/* 降级说明：不是错误，但必须让用户看见「为什么不是跳转登录」。
   底色从 #f4f5f7（冷灰）改成 --cn-surface：本面板整套是暖灰，冷灰那块会明显"不是一家的"。 */
.cn-notice {
  margin: 0;
  flex: none;
  padding: 8px 10px;
  border-radius: var(--cn-r-ctl);
  background: var(--cn-surface);
  color: var(--cn-text);
  font-size: 12px;
  line-height: 1.6;
}

/* 提交失败的报错做成一块，而不是一行飘着的红字（红底值同 .cn-error-row）。
   只作用于授权表单直属的那条：.cn-error 还被详情视图和飞出面板用着。 */
.cn-body > .cn-error {
  flex: none;
  padding: 8px 10px;
  border-radius: var(--cn-r-ctl);
  background: rgba(161, 18, 31, 0.06);
}

/* 默认一行截断，点开展开；不这么做的话一条带完整 URL 的同步失败能占掉半个面板 */
.cn-error-row {
  overflow: hidden;
  padding: 6px 8px;
  border-radius: var(--cn-r-ctl);
  background: rgba(161, 18, 31, 0.06);
  cursor: pointer;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cn-error-row.expanded {
  white-space: normal;
}

.cn-error {
  margin: 0;
  color: #a1121f;
  font-size: 12px;
  line-height: 1.5;
}

/* 账号格式提示复用既有的 .cn-hint（本面板里 resourceHint 也用它），不新增规则：
   我一度在这里补了一条同名规则，它排在后面会连既有那处的间距一起改掉——
   把共用的东西当成自己的改，是「同构两处只改一处」的镜像版，同样静默。
   刻意不用报错红：用户还在打字，这是引导不是失败，等真提交失败才归 .cn-error。 */

.cn-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

/* 授权表单里把操作行钉在底部。`.cn-body` 是滚动容器，而面板高度受
   --picker-available-height 限制（输入框贴近视口底部时只有 300 出头），
   内容一长「连接」按钮就被顶出可视区——**主操作看不见，用户填完两个框
   面前没有按钮**。真机实测：按钮底 425 超出面板底 399，只露 6px。
   QQ 邮箱这种「长引导 + 两个输入框 + 格式提示」的组合必然触发。 */
.cn-body .cn-actions {
  position: sticky;
  bottom: 0;
  /* .cn-body 是 column flex + overflow:auto，默认 flex-shrink:1 会在内容变长时
     把这一行压扁——它是主操作所在，必须不参与收缩 */
  flex: none;
  /* 主操作恒定在右下角；次要动作靠 margin-right:auto 推到最左。
     原来是 space-between，只有一个按钮时它就贴在**左**下角——一个 52px 的小方块
     孤零零杵在那儿，既不像主操作，整块表单也没有收口。 */
  justify-content: flex-end;
  padding: 8px 0 2px;
  border-top: 1px solid var(--cn-line);
  margin-top: 2px;
  background: #fff;
  /* 内容是从这一行**底下穿过去**的（面板高度不够时表单必然要滚）。只有一条实线时，
     被压在下面的输入框会被齐刷刷切一刀，读起来像布局塌了而不是"下面还有"。
     一道朝上的柔和阴影才说明白这层关系。 */
  box-shadow: 0 -10px 12px -10px rgb(0 0 0 / 14%);
}

.cn-body .cn-alt {
  margin-right: auto;
}

/* 只有主按钮时撑满整行（GitHub 那种带 oauth 的仍是「左文字链 + 右按钮」） */
.cn-body .cn-primary:only-child {
  width: 100%;
}

.cn-primary {
  height: 32px;
  padding: 0 14px;
  border: 0;
  border-radius: var(--cn-r-ctl);
  background: var(--cn-text);
  color: #fff;
  cursor: pointer;
  font-size: 12px;
}

.cn-primary:disabled {
  background: var(--cn-line-strong);
  cursor: not-allowed;
}

/* 授权表单里的主按钮按 ConnectorDetailModal 的 .cdm-primary 走（34/13/500）：
   12px 在一屏 12px 说明文字里完全不像个按钮。
   ⚠️ 只在 .cn-body 内覆盖——.cn-primary 详情视图的「添加代码库」也在用，
   那里的 disabled 语义是"正在进行中"，不该套用下面这套"还差东西"的读法。 */
.cn-body .cn-primary {
  height: 34px;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}

.cn-body .cn-primary:hover:not(:disabled) {
  background: #4a4740;
}

/* 禁用＝「还差东西」，不是「坏了」：原先是 12% 黑底 + **白字**，白字压在浅灰上
   几乎读不出字来，看着就是个失效控件。改成浅底描边灰字（同 .cm-btn.outlined:disabled
   的读法），缺什么再挂到 title 上。 */
.cn-body .cn-primary:disabled {
  border: 1px solid var(--cn-line-strong);
  background: var(--cn-surface);
  color: var(--cn-muted);
  cursor: not-allowed;
}

.cn-alt {
  border: 0;
  background: transparent;
  color: var(--cn-muted);
  cursor: pointer;
  font-size: 12px;
  text-align: left;
  text-decoration: underline;
}

/* 两段授权引导：只在「登录了但还没授权仓库」时出现，是脚手架不是常驻 */
.cn-steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px 4px 0;
}

.cn-steps-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
}

.cn-step {
  color: var(--cn-muted);
}

.cn-step.done {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #15803d;
}

.cn-step-sep {
  color: var(--cn-line-strong);
}

.cn-steps-text {
  margin: 0;
  color: var(--cn-muted);
  font-size: 12px;
  line-height: 1.7;
}

.cn-foot-actions {
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 2px;
}

.cn-foot-link {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  height: 30px;
  padding: 0 6px;
  border-radius: var(--cn-r-ctl);
  color: var(--cn-text);
  cursor: pointer;
  font-size: 14px;
  gap: 6px;
}

.cn-foot-link:hover {
  background: var(--cn-hover);
}

.cn-foot-link:hover {
  color: var(--cn-text);
}

/* ---- 二级飞出面板（一级常驻、二级贴右侧并排） ---- */
/* 定位上下文是 .cn-panel（它是 position:absolute）。用 top 而不是 bottom 对齐：
   面板高度随仓库数变化，贴顶对齐时它不会跟着上下跳。 */
.cn-flyout {
  position: absolute;
  top: 0;
  display: flex;
  max-height: var(--picker-available-height, 420px);
  flex-direction: column;
  width: 300px;
  border: 1px solid var(--cn-line);
  border-radius: var(--cn-r-panel);
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.12);
  padding: 8px;
  animation: cn-enter 0.16s ease both;
}

.cn-flyout.right {
  left: calc(100% + 8px);
}

/* 右侧放不下就翻到左边（窄屏/贴视口右缘），否则面板会被切掉半截 */
.cn-flyout.left {
  right: calc(100% + 8px);
}

.cn-flyout-head {
  display: flex;
  flex: none;
  align-items: center;
  gap: 9px;
  padding: 4px 6px 8px;
}

.cn-flyout-name {
  flex: 1;
  overflow: hidden;
  color: var(--cn-text);
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cn-accounts {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* 账户行现在是**可勾选项**（多账户，2026-07-29），所以给了 hover 与指针手型——
   与前一版"纯展示"相反：那时只有一个账户、点了也没有任何事情发生，给手型是骗人。 */
.cn-account-row {
  width: 100%;
  border: 0;
  cursor: pointer;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--cn-r-ctl);
  background: var(--cn-hover);
  color: var(--cn-text);
  font-size: 13px;
}

.cn-account-icon {
  display: inline-flex;
  flex: none;
  font-size: 15px;
}

.cn-account-row:hover { background: var(--cn-line); }
.cn-account-row:disabled { cursor: default; opacity: .6; }
.cn-account-row.checked { color: var(--cn-text); }

.cn-account-name {
  overflow: hidden;
  flex: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cn-flyout-list {
  flex: 1 1 auto;
  overflow-y: auto;
  min-height: 0;
  max-height: 260px;
}

/* 一级里当前展开的那一行给个底色，指明二级属于谁 */
.cn-row.active {
  background: var(--cn-hover);
}

/* ---- 详情 ---- */
.cn-switch {
  position: relative;
  flex: none;
  width: 34px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: var(--cn-line-strong);
  cursor: pointer;
  transition: background 0.18s ease;
}

.cn-switch.on {
  background: var(--cn-text);
}

.cn-switch:disabled {
  cursor: not-allowed;
}

.cn-switch-dot {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  transition: transform 0.18s ease;
}

.cn-switch.on .cn-switch-dot {
  transform: translateX(14px);
}

.cn-account {
  margin: 0 4px 6px;
  color: var(--cn-muted);
  font-size: 12px;
}

.cn-search {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 11px;
  border: 1px solid var(--cn-line-strong);
  border-radius: var(--cn-r-ctl);
  margin-bottom: 6px;
  background: var(--cn-hover);
  color: var(--cn-muted);
}

.cn-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: var(--cn-text);
  font-size: 14px;
}

.cn-res {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  padding: 7px 8px;
  font-size: 14px;
  border: 0;
  border-radius: var(--cn-r-ctl);
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.cn-res:hover {
  background: var(--cn-hover);
}

.cn-res-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  color: var(--cn-muted);
  font-size: 14px;
}

/* 选中标记在**右侧**（对齐参考实现）；左边留给资源自己的图标 */
.cn-res-tick {
  flex: none;
  color: var(--cn-text);
  font-size: 12px;
}

.cn-res-name {
  flex: 1;
  overflow: hidden;
  color: var(--cn-text);
  font-size: 14px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
