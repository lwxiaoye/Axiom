<template>
  <!-- 连接器详情弹窗（2026-07-29 用户拍板，一比一对标参考实现）。
       入口＝连接器行右侧那个滑块按钮。此前它直接弹「取消授权？」确认框——
       一个纯危险动作，用户点进来其实是想**看看这个连接器是什么、怎么用**。
       现在这里回答那个问题：介绍 + 示例提示词 + 详情；断开连接收进「管理」下拉里。 -->
  <!-- z-index 必须高过「浏览连接器」弹窗（antd 默认 1000）：详情是从那张卡片点进来的，
       两层同时在场，详情要压在上面，它的遮罩才能把下层列表一起模糊掉——参考实现里
       正是这个观感（底下的列表still可见但糊掉）。 -->
  <a-modal
    :open="open"
    :footer="null"
    :width="640"
    :mask-closable="true"
    :mask-style="MASK_STYLE"
    :z-index="1100"
    centered
    wrap-class-name="cdm-wrap"
    @cancel="emit('close')"
  >
    <div v-if="item" class="cdm">
      <!-- 头部：大图标 + 名称 + 整段介绍，居中 -->
      <header class="cdm-head">
        <span class="cdm-logo">
          <ConnectorIcon :icon="item.icon" />
        </span>
        <h2 class="cdm-title">{{ item.name }}</h2>
        <p class="cdm-desc">{{ item.description || item.summary }}</p>

        <!-- 用不了的原因摆在动作之前（对齐参考实现里 Instagram 那条灰底提示）：
             按钮为什么是灰的，答案必须在按下之前就看得到，而不是点完弹个报错。 -->
        <p v-if="!item.available && item.unavailableReason" class="cdm-notice">
          <InfoCircleOutlined />
          <span>{{ item.unavailableReason }}</span>
        </p>

        <!-- 未连接态（2026-07-29 补）：此前这里无条件渲染「试用一下 + 管理(断开连接)」，
             而从「浏览连接器」点进来的多半**根本没连**——给一个连都没连的东西提供
             「断开连接」，以及一个调不动工具的「试用一下」，都是错的。 -->
        <div class="cdm-actions">
          <template v-if="item.connected">
            <button type="button" class="cdm-primary" @click="onTry">
              <SendOutlined />
              试用一下
            </button>

            <!-- 「管理」下拉：配置（去对方账号页）/ 断开连接（危险，红字） -->
            <div ref="manageRef" class="cdm-manage">
              <button
                type="button"
                class="cdm-ghost"
                :aria-expanded="manageOpen"
                @click="manageOpen = !manageOpen"
              >
                管理
                <PremiumChevron class="cdm-caret" :direction="manageOpen ? 'up' : 'down'" :size="14" interactive />
              </button>
              <div v-if="manageOpen" class="cdm-menu">
                <button
                  v-if="item.configLink"
                  type="button"
                  class="cdm-menu-item"
                  @click="onConfig"
                >
                  <ControlOutlined />
                  配置
                </button>
                <button type="button" class="cdm-menu-item danger" @click="askDisconnect">
                  <DisconnectOutlined />
                  断开连接
                </button>
              </div>
            </div>
          </template>

          <button
            v-else
            type="button"
            class="cdm-primary"
            :disabled="!item.available"
            :title="item.available ? `连接 ${item.name}` : item.unavailableReason"
            @click="emit('connect', item)"
          >
            连接
          </button>
        </div>
      </header>

      <!-- 示例提示词：2×2 卡片。点一张即把它填进输入框（比只给人看更有用） -->
      <section v-if="item.examplePrompts?.length" class="cdm-section">
        <h3 class="cdm-section-title">示例提示词</h3>
        <div class="cdm-prompts">
          <button
            v-for="(p, i) in item.examplePrompts"
            :key="i"
            type="button"
            class="cdm-prompt"
            :title="p"
            @click="usePrompt(p)"
          >
            <MessageOutlined class="cdm-prompt-icon" />
            <span class="cdm-prompt-text">{{ p }}</span>
          </button>
        </div>
      </section>

      <!-- 详情：2 列键值表 -->
      <section class="cdm-section">
        <h3 class="cdm-section-title">详情</h3>
        <div class="cdm-detail">
          <div class="cdm-field">
            <div class="cdm-label">连接器类型</div>
            <div class="cdm-value">{{ item.connectorType || '应用' }}</div>
          </div>
          <div class="cdm-field">
            <div class="cdm-label">作者</div>
            <div class="cdm-value">{{ item.author || 'AXIOM 校园智能体' }}</div>
          </div>
          <div class="cdm-field">
            <div class="cdm-label">更多信息</div>
            <div class="cdm-value">
              <a v-if="item.homepage" :href="item.homepage" target="_blank" rel="noreferrer" class="cdm-link">
                网站 <ExportOutlined />
              </a>
              <a v-if="item.docLink" :href="item.docLink" target="_blank" rel="noreferrer" class="cdm-link">
                文档 <ExportOutlined />
              </a>
              <a
                v-if="item.privacyLink"
                :href="item.privacyLink"
                target="_blank"
                rel="noreferrer"
                class="cdm-link"
              >
                隐私政策 <ExportOutlined />
              </a>
              <span v-if="!item.homepage && !item.docLink && !item.privacyLink" class="cdm-muted">—</span>
            </div>
          </div>
          <div class="cdm-field">
            <div class="cdm-label">UUID</div>
            <div class="cdm-value cdm-uuid" :title="item.id">{{ item.id }}</div>
          </div>
        </div>
      </section>

      <footer class="cdm-foot">
        <button type="button" class="cdm-feedback" @click="emit('feedback', item)">提供反馈</button>
      </footer>

      <!-- 断开连接的二次确认：就地铺一层，不再另开一个 Modal.confirm。
           删凭据不可逆，确认这一步不能省；但它是这个弹窗内部的一步，不是入口。 -->
      <div v-if="confirming" class="cdm-confirm">
        <div class="cdm-confirm-box">
          <h4 class="cdm-confirm-title">断开 {{ item.name }} 的连接？</h4>
          <p class="cdm-confirm-text">
            会删除保存的授权凭据，主对话将无法再读取{{ item.resourceLabel || '该应用的数据' }}。
            只是本轮不想用的话，用面板里的开关关掉即可。
          </p>
          <div class="cdm-confirm-actions">
            <button type="button" class="cdm-ghost" :disabled="busy" @click="confirming = false">返回</button>
            <button type="button" class="cdm-danger" :disabled="busy" @click="doDisconnect">
              {{ busy ? '断开中…' : '断开连接' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import {
  ControlOutlined,
  DisconnectOutlined,
  ExportOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  SendOutlined,
} from '@ant-design/icons-vue';
import type { ConnectorItem } from '../connectors.api';
import ConnectorIcon from './ConnectorIcon.vue';
import PremiumChevron from './PremiumChevron.vue';

/**
 * 遮罩：与 ConnectorsModal 同一组取值（淡底 + 明显模糊，2026-07-29 用户拍板）。
 * 走 `mask-style` 是因为 `.ant-modal-mask` 是 `.ant-modal-wrap` 的兄弟节点，
 * `wrapClassName` 和 scoped 样式都够不着它。`-webkit-` 前缀是 Safari 必需。
 */
const MASK_STYLE = {
  background: 'rgba(15, 23, 42, 0.18)',
  backdropFilter: 'blur(10px)',
  WebkitBackdropFilter: 'blur(10px)',
} as const;

const props = defineProps<{
  open: boolean;
  item: ConnectorItem | null;
  /** 断开连接进行中（由父组件驱动，避免本组件自己发请求） */
  busy?: boolean;
}>();

const emit = defineEmits<{
  (e: 'close'): void;
  (e: 'connect', item: ConnectorItem): void;
  (e: 'disconnect', item: ConnectorItem): void;
  (e: 'usePrompt', text: string): void;
  (e: 'feedback', item: ConnectorItem): void;
}>();

const manageOpen = ref(false);
const confirming = ref(false);
const manageRef = ref<HTMLElement | null>(null);

// 每次重新打开都回到干净状态：下拉收起、确认层收掉。
// 不重置的话，上次点开「管理」后关掉弹窗，下次进来菜单还挂着。
// 另外挂/摘 Esc 监听：a-modal 自带的 Esc 关闭依赖焦点落在弹窗内，而点完示例提示词
// 焦点会被移到弹窗外的输入框，那之后 Esc 就失灵了。这里在 window 上兜一道，
// 无论焦点在哪都能关（浮层必须能用键盘关掉，见 CLAUDE.md 浮层约定）。
function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'Escape') return;
  // 确认层开着时，Esc 先退回详情，而不是把整个弹窗关掉——
  // 否则用户想取消一个危险动作，却把上下文一起丢了。
  if (confirming.value) {
    confirming.value = false;
    return;
  }
  if (manageOpen.value) {
    manageOpen.value = false;
    return;
  }
  emit('close');
}

watch(
  () => props.open,
  (v) => {
    if (v) {
      window.addEventListener('keydown', onKeydown);
    } else {
      window.removeEventListener('keydown', onKeydown);
      manageOpen.value = false;
      confirming.value = false;
    }
  },
  // immediate：万一将来父组件以 open=true 直接挂载本组件，watch 不会补触发，
  // Esc 监听就永远挂不上。当前 open 恒从 false 起，这里只是不让它成为隐患。
  { immediate: true },
);

onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown));

function onTry() {
  usePrompt(props.item?.examplePrompts?.[0] || '');
}

function onConfig() {
  manageOpen.value = false;
  if (props.item?.configLink) window.open(props.item.configLink, '_blank', 'noreferrer');
}

function askDisconnect() {
  manageOpen.value = false;
  confirming.value = true;
}

function doDisconnect() {
  if (props.item) emit('disconnect', props.item);
}

function usePrompt(text: string) {
  emit('usePrompt', text);
  // 必须同时关掉弹窗：提示词是填进**弹窗背后**的输入框的，不关的话用户看不到
  // 自己刚选的内容落在哪儿；而且父层会把焦点移到那个输入框上，焦点一旦跑到弹窗外，
  // a-modal 自带的 Esc 关闭也就失灵了（真机实测确认过）。
  emit('close');
}
</script>

<style scoped>
/* 版式对标参考实现：白底、圆角、内容居中的头部 + 两个分区 + 底部反馈。
   全程克制的黑白灰，唯一的彩色是危险动作的红（CLAUDE.md 产品决策）。 */
.cdm {
  /* 留白全部由非 scoped 块里的 .ant-modal-body 负责（全局有 padding:0 要压），
     这里不再叠一层，否则左右会比参考实现宽出 16px */
  position: relative;
}

/* ---- 头部 ---- */
.cdm-head {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 8px 24px 4px;
}

.cdm-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 16px;
  border: 1px solid #ececf0;
  background: #fff;
  font-size: 34px;
  color: #17181c;
}

.cdm-title {
  margin: 14px 0 0;
  font-size: 20px;
  font-weight: 600;
  color: #17181c;
}

.cdm-desc {
  margin: 8px 0 0;
  max-width: 460px;
  font-size: 13px;
  line-height: 1.65;
  color: #6b7280;
}

/* 不可用原因条（对齐参考实现 Instagram 那条）：灰底、左图标、整段文字，
   摆在动作按钮**之前**——按钮为什么是灰的，要在按下之前就答清楚。 */
.cdm-notice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  margin: 16px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: #f4f5f7;
  color: #6b7280;
  font-size: 13px;
  line-height: 1.5;
  text-align: left;
}

.cdm-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
}

/* 未连接且 provider 不可用时的「连接」：禁用态要一眼可辨，不能只是变浅一点点 */
.cdm-primary:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.cdm-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 16px;
  border: 0;
  border-radius: 8px;
  background: #17181c;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s ease;
}
.cdm-primary:hover { background: #30343b; }

.cdm-ghost {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 34px;
  padding: 0 14px;
  border: 1px solid #e2e4e9;
  border-radius: 8px;
  background: #fff;
  color: #30343b;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.cdm-ghost:hover:not(:disabled) { background: #f7f8fa; border-color: #cfd3da; }
.cdm-ghost:disabled { color: #a8adb6; cursor: default; }

.cdm-caret { font-size: 10px; color: #8a9099; }

/* ---- 管理下拉 ---- */
.cdm-manage { position: relative; }

.cdm-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 168px;
  padding: 6px;
  border: 1px solid #ececf0;
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 10px 28px rgb(17 24 39 / 12%);
  z-index: 20;
}

.cdm-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 34px;
  padding: 0 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: #30343b;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.cdm-menu-item:hover { background: #f5f6f8; }
.cdm-menu-item.danger { color: #d93026; }
.cdm-menu-item.danger:hover { background: #fdf2f1; }

/* ---- 分区 ---- */
.cdm-section { margin-top: 26px; }

.cdm-section-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: #17181c;
}

/* ---- 示例提示词 2×2 ---- */
.cdm-prompts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.cdm-prompt {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 92px;
  padding: 12px 14px;
  border: 1px solid #ececf0;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.cdm-prompt:hover { background: #fafbfc; border-color: #dfe2e8; }

.cdm-prompt-icon { font-size: 15px; color: #8a9099; }

.cdm-prompt-text {
  font-size: 13px;
  line-height: 1.55;
  color: #30343b;
  /* 三行截断：卡片等高才排得齐，长句不撑破网格 */
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ---- 详情 2 列 ---- */
.cdm-detail {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px 16px;
  padding: 16px;
  border: 1px solid #ececf0;
  border-radius: 10px;
  background: #fafbfc;
}

.cdm-label { font-size: 12px; color: #8a9099; }

.cdm-value {
  margin-top: 5px;
  font-size: 13px;
  color: #30343b;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cdm-uuid {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}

.cdm-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  /* .cdm-value 是列向 flex，默认 stretch 会把链接拉成整格宽，
     hover 的下划线跟着横穿整行；收成内容宽度 */
  align-self: flex-start;
  color: #2563eb;
  font-size: 13px;
}
.cdm-link:hover { text-decoration: underline; }

.cdm-muted { color: #a8adb6; }

/* ---- 底部 ---- */
.cdm-foot {
  display: flex;
  justify-content: center;
  padding: 22px 0 10px;
}

/* 窄屏：antd 自带 max-width:calc(100vw-32px) 会收窄弹窗，但两个 2 列网格不会自动让位
   —— 375px 下每列只剩约 135px，示例提示词被压成一长条。并成一列。 */
@media (max-width: 560px) {
  .cdm-prompts,
  .cdm-detail {
    grid-template-columns: minmax(0, 1fr);
  }
  .cdm-desc { max-width: none; }
  .cdm-head { padding-left: 0; padding-right: 0; }
}

.cdm-feedback {
  border: 0;
  background: transparent;
  color: #8a9099;
  font-size: 12px;
  text-decoration: underline;
  cursor: pointer;
}
.cdm-feedback:hover { color: #30343b; }

/* ---- 就地二次确认 ---- */
.cdm-confirm {
  position: absolute;
  inset: -8px -8px 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgb(255 255 255 / 82%);
  backdrop-filter: blur(2px);
  border-radius: 10px;
  z-index: 30;
}

.cdm-confirm-box {
  width: 100%;
  max-width: 380px;
  padding: 18px 20px;
  border: 1px solid #ececf0;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 16px 40px rgb(17 24 39 / 14%);
}

.cdm-confirm-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: #17181c;
}

.cdm-confirm-text {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.6;
  color: #6b7280;
}

.cdm-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.cdm-danger {
  height: 34px;
  padding: 0 14px;
  border: 1px solid #f0b7b2;
  border-radius: 8px;
  background: #fff;
  color: #d93026;
  font-size: 13px;
  cursor: pointer;
}
.cdm-danger:hover:not(:disabled) { background: #fdf2f1; }
.cdm-danger:disabled { opacity: 0.6; cursor: default; }
</style>

<!-- 非 scoped：a-modal 是 teleport 出去的，scoped 的 :deep() 够不到它的外壳 DOM。
     这里专门压住三条会毁掉版式的全局规则（都在 index-*.css 里，实测存在）：
       1) `.ant-modal-body{padding:0}`      —— 内容整个贴边
       2) `.ant-modal-content{height:100%}` —— 内容被拉满视口高
       3) `html[data-theme=light] .ant-modal-content{border-radius:10px!important;
          background:var(--admin-panel)!important}` —— **带 !important，普通选择器压不住**
     所以下面必须同样用 !important，且靠 wrap-class-name 限定只作用于本弹窗。 -->
<style>
.cdm-wrap .ant-modal-content {
  height: auto !important;
  padding: 0 !important;
  border: 1px solid #ececf0 !important;
  border-radius: 16px !important;
  background: #fff !important;
  box-shadow: 0 24px 60px rgb(17 24 39 / 16%) !important;
  overflow: hidden;
}

.cdm-wrap .ant-modal-body {
  /* 参考实现的留白：上下 28、左右 28；本组件内部再各留 8 做视觉呼吸 */
  padding: 28px 28px 20px !important;
}

/* 关闭按钮：全局主题可能给它上了色/描边，这里回到安静的灰 */
.cdm-wrap .ant-modal-close {
  top: 10px;
  inset-inline-end: 10px;
  color: #8a9099;
}
.cdm-wrap .ant-modal-close:hover { color: #30343b; background: transparent; }

/* 窄屏只收留白；网格并列写在 scoped 块里——那两个类是 scoped 的，
   写在这里特异度与 scoped 规则相同，谁赢只取决于产物里两段 CSS 的先后顺序，不可靠。 */
@media (max-width: 560px) {
  .cdm-wrap .ant-modal-body { padding: 22px 18px 16px !important; }
}
</style>
