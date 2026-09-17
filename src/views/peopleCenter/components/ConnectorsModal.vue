<template>
  <a-modal
    :open="open"
    :footer="null"
    :closable="false"
    :width="modalWidth"
    :body-style="{ padding: '0' }"
    :mask-style="MASK_STYLE"
    wrap-class-name="cm-wrap"
    centered
    destroy-on-close
    @update:open="(v) => emit('update:open', v)"
  >
    <!-- 全局 .ant-modal-body{padding:0} + wireframe 主题会让原生 a-modal 贴边，
         所有内边距必须由这里自己给（本项目已在 9 处踩过，见长期记忆）。 -->
    <div class="cm-panel">
      <header class="cm-head">
        <h2 class="cm-title">{{ view === 'added' ? '已添加的连接器' : '连接器' }}</h2>
        <button type="button" class="cm-close" title="关闭" @click="close">
          <CloseOutlined />
        </button>
      </header>

      <!-- 两态的工具条布局**不同**（对照参考实现）：
           已添加态 = 窄搜索框在左 + 「浏览连接器/创建」在右，同一行；
           浏览态   = 搜索框**整行宽**，分类 tab 与「创建」在下一行分列两端。 -->
      <div :class="['cm-toolbar', view === 'browse' ? 'browse' : 'added']">
        <label class="cm-search">
          <SearchOutlined />
          <input v-model="keyword" placeholder="搜索连接器" />
        </label>
        <div v-if="view === 'added'" class="cm-toolbar-right">
          <button type="button" class="cm-btn outlined" @click="view = 'browse'">浏览连接器</button>
          <button type="button" class="cm-btn outlined" disabled title="敬请期待">
            创建 <PremiumChevron direction="down" :size="13" interactive />
          </button>
        </div>
      </div>

      <div v-if="view === 'browse'" class="cm-tabrow">
        <nav class="cm-tabs">
          <button
            v-for="tab in TABS"
            :key="tab.key"
            type="button"
            :class="['cm-tab', { on: tab.key === activeTab }]"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </nav>
        <button type="button" class="cm-btn outlined" disabled title="敬请期待">
          创建 <PremiumChevron direction="down" :size="13" interactive />
        </button>
      </div>

      <div class="cm-body">
        <p v-if="loading && !connectors.length" class="cm-state">加载中...</p>
        <p v-else-if="error" class="cm-state cm-state-error">{{ error }}</p>
        <p v-else-if="!visible.length" class="cm-state">
          {{ keyword ? '没有匹配的连接器' : emptyText }}
        </p>

        <!-- 整张卡片可点＝打开详情（2026-07-29 用户拍板，对齐参考实现）：**连没连都能点**。
             未连接的点进去看"这东西是什么、能干什么"再决定连不连，正是这一步此前是断的——
             之前只有右侧那个 + 能点，等于逼着人先连上再了解。右侧的 +／✓ 保持各自语义，
             `.stop` 挡住冒泡，点 + 仍是直接连接，不绕一层详情。 -->
        <ul v-else class="cm-grid">
          <li
            v-for="item in visible"
            :key="item.id"
            :class="['cm-card', { disabled: !item.available }]"
            role="button"
            tabindex="0"
            :title="`查看 ${item.name} 详情`"
            @click="emit('detail', item)"
            @keydown.enter.prevent="emit('detail', item)"
            @keydown.space.prevent="emit('detail', item)"
          >
            <span class="cm-icon">
              <ConnectorIcon :icon="item.icon" />
            </span>
            <span class="cm-card-text">
              <span class="cm-card-name">
                {{ item.name }}
                <span v-if="item.beta" class="cm-tag">Beta</span>
              </span>
              <!-- 副标题允许换到第二行（参考实现里长描述就是两行），不做单行截断 -->
              <span class="cm-card-sub">{{ cardSub(item) }}</span>
            </span>

            <!-- 已连接打勾、未连接给 +。开关/范围/断开都属于详情，这一屏只回答「有没有」 -->
            <CheckOutlined v-if="item.connected" class="cm-tick" />
            <button
              v-else
              type="button"
              class="cm-add"
              :disabled="!item.available"
              :title="item.available ? `连接 ${item.name}` : item.unavailableReason"
              @click.stop="emit('connect', item.id)"
            >
              <PlusOutlined />
            </button>
          </li>
        </ul>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
/**
 * 连接器管理/浏览弹窗（2026-07-28）。
 *
 * 与 composer 下拉的分工：下拉是「这轮对话用哪个」的快捷操作；这里是「我有哪些、
 * 要不要再加一个、把某个断掉」。做成弹窗而不是独立页面是用户拍板——连接器是设置
 * 性质的东西，跳走一整页会把人从对话里带出去。
 *
 * 视觉取值来自 Manus 线上计算样式实测（卡片 12px 圆角 / 12px 内边距 / 1px
 * rgba(0,0,0,.06) 描边 / 标题 16px / 副标题 12px #858481），不是照着截图估的。
 */
import ConnectorIcon from './ConnectorIcon.vue';
import { computed, ref, watch } from 'vue';
import {
  CheckOutlined, CloseOutlined, PlusOutlined, SearchOutlined,
} from '@ant-design/icons-vue';
import PremiumChevron from './PremiumChevron.vue';
import { listConnectors, type ConnectorItem } from '../connectors.api';

/**
 * 遮罩：淡底 + 明显模糊（2026-07-29 用户拍板，对齐参考实现）。
 *
 * 走 `mask-style` 而不是写 CSS——antd 的遮罩 `.ant-modal-mask` 是 `.ant-modal-wrap` 的
 * **兄弟**节点，`wrapClassName` 够不着它，scoped 样式更够不着；要用 CSS 就得写全局
 * 兄弟选择器，一旦 antd 改了 DOM 结构就静默失效。
 *
 * 底色只用 0.18 的淡墨：遮罩越黑越看不出后面模糊了没有，而这里的重点正是模糊本身。
 * `-webkit-` 前缀是 Safari 必需的，缺了在 Safari 上只剩一层淡灰、完全没有模糊。
 */
const MASK_STYLE = {
  background: 'rgba(15, 23, 42, 0.18)',
  backdropFilter: 'blur(10px)',
  WebkitBackdropFilter: 'blur(10px)',
} as const;

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void;
  (e: 'connect', providerId: string): void;
  (e: 'detail', item: ConnectorItem): void;
  (e: 'changed'): void;
}>();

const TABS = [
  { key: 'app', label: '应用' },
  { key: 'api', label: '自定义 API' },
  { key: 'mcp', label: '自定义 MCP' },
] as const;

const loading = ref(false);
const error = ref('');
const connectors = ref<ConnectorItem[]>([]);
const keyword = ref('');
const view = ref<'added' | 'browse'>('added');
const activeTab = ref<(typeof TABS)[number]['key']>('app');

function close() {
  emit('update:open', false);
}

// 窄屏下弹窗要收进视口，否则两列卡片会横向溢出
const modalWidth = computed(() => (window.innerWidth < 720 ? '92vw' : 760));

const emptyText = computed(() =>
  view.value === 'added' ? '还没有连接任何应用，点「浏览连接器」看看' : '暂无可用的连接器',
);

const visible = computed(() => {
  const kw = keyword.value.trim().toLowerCase();
  return connectors.value
    .filter((c) => (view.value === 'added' ? c.connected : true))
    .filter((c) => !kw || c.name.toLowerCase().includes(kw) || c.summary.toLowerCase().includes(kw))
    .sort((a, b) => Number(b.connected) - Number(a.connected));
});

function cardSub(item: ConnectorItem): string {
  if (!item.available) return item.unavailableReason || '暂不可用';
  if (item.connected && !item.reposAuthorized) return `未授权${item.resourceLabel}，还读不到内容`;
  if (item.connected) return `${item.resourceCount} 个${item.resourceLabel} · ${item.toolCount} 个工具`;
  return item.summary;
}

async function load() {
  loading.value = true;
  error.value = '';
  try {
    const apps = await listConnectors();
    connectors.value = apps;
  } catch (err: any) {
    error.value = err?.message || '加载失败';
  } finally {
    loading.value = false;
  }
}

// 每次打开都重新拉：连接状态可能在别处（下拉、授权回跳）变过
watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    keyword.value = '';
    view.value = 'added';
    load();
  },
  { immediate: true },
);
</script>

<style scoped>
.cm-panel {
  /* 令牌与 ConnectorMenu 同源（Manus 线上实测值） */
  --cm-text: #34322d;
  --cm-muted: #858481;
  --cm-line: rgba(0, 0, 0, 0.06);
  --cm-line-strong: rgba(0, 0, 0, 0.12);
  --cm-hover: rgba(0, 0, 0, 0.04);

  display: flex;
  /* **固定高度**，不随内容收缩（用户拍板）：参考实现里即便只有两三个连接器，
     弹窗依然是这么大、下方留白，不会塌成一条。用 max-height 就会变成内容驱动，
     连接器少的时候弹窗只有一两百像素高，看着完全是另一个东西。 */
  height: min(78vh, 720px);
  flex-direction: column;
  padding: 0 24px 24px;
  background: #f8f8f7;
  color: var(--cm-text);
}

/* Manus 实测：标题块 padding 34px 0 16px，底部一条 1px rgba(0,0,0,.06) 分隔线。
   这条线是这一屏的骨架，缺了整个头部会"浮"在内容上。 */
.cm-head {
  display: flex;
  flex: none;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 34px 0 16px;
  border-bottom: 1px solid var(--cm-line);
}

.cm-title {
  margin: 0;
  color: var(--cm-text);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
}

.cm-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: none;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--cm-muted);
  cursor: pointer;
}

.cm-close:hover {
  background: var(--cm-hover);
  color: var(--cm-text);
}

.cm-toolbar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 18px;
}

/* 浏览态搜索框整行宽（参考实现如此）；已添加态才是左窄框 + 右按钮组 */
.cm-toolbar.browse .cm-search {
  width: 100%;
}

.cm-tabrow {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}

/* 搜索框：h32 / radius8 / padding 4px 12px / 1px rgba(0,0,0,.12)（实测值） */
.cm-search {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: 200px;
  height: 32px;
  padding: 4px 12px;
  border: 1px solid var(--cm-line-strong);
  border-radius: 8px;
  color: var(--cm-muted);
}

.cm-search:focus-within {
  border-color: #818cf8;
  box-shadow: none;
}

.cm-search input {
  width: 100%;
  border: 0;
  outline: none;
  appearance: none;
  background: transparent;
  color: var(--cm-text);
  font-size: 14px;
  line-height: 20px;
}

.cm-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 按钮：h32 / radius8 / padding 0 8px / 14px 500（实测值） */
.cm-btn {
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--cm-text);
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
}

.cm-btn:hover {
  background: var(--cm-hover);
}

/* 「浏览连接器」「创建」是带描边的次级按钮，不是纯文字 */
.cm-btn.outlined {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid var(--cm-line-strong);
}

.cm-btn.outlined:disabled {
  color: var(--cm-muted);
  cursor: not-allowed;
  opacity: 0.7;
}

.cm-tabs {
  display: flex;
  flex: none;
  gap: 4px;
  margin-top: 14px;
}

.cm-tab {
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--cm-muted);
  cursor: pointer;
  font-size: 14px;
}

.cm-tab.on {
  background: rgba(0, 0, 0, 0.06);
  color: var(--cm-text);
}

.cm-body {
  flex: 1 1 auto;
  overflow-y: auto;
  min-height: 0;
  margin-top: 16px;
}

.cm-state {
  padding: 48px 0;
  color: var(--cm-muted);
  font-size: 14px;
  text-align: center;
}

.cm-state-error {
  color: #a1121f;
}

.cm-grid {
  display: grid;
  gap: 12px;
  padding: 0;
  margin: 0;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  list-style: none;
}

/* 卡片：12px 内边距 / 12px 圆角 / 12px gap / 1px rgba(0,0,0,.06)（实测值） */
.cm-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 76px;     /* Manus 实测卡片高度 */
  padding: 12px;
  border: 1px solid var(--cm-line);
  border-radius: 12px;
  /* 参考实现里卡片是**有底色填充**的，不是纯描边——只描边会让整屏发飘 */
  background: rgba(0, 0, 0, 0.02);
}

.cm-card:hover {
  background: rgba(0, 0, 0, 0.04);
}

/* 整卡可点（2026-07-29）：hover 底色本来就有，这里补手型与键盘焦点圈。
   不可用的连接器**照样能看详情**——详情正是解释"为什么现在用不了"的地方，
   所以 disabled 只压低透明度，不改 cursor、也不拦点击。 */
.cm-card {
  cursor: pointer;
}

.cm-card:focus-visible {
  outline: 1px solid #8f96a3;
  outline-offset: 1px;
}

.cm-card.disabled {
  opacity: 0.55;
}

/* 图标是**白底圆角方块**里的图标，不是裸图标。
   ⚠️ 我上一轮量成「裸图标 24×24」是取错了元素——`card.querySelector('img')` 拿到的是
   图片本身，不是包着它的方框。参考截图里每个连接器图标都坐在一个白底圆角块上，
   这一处以截图为准（测量取错元素时，它给的是一个**合法但答非所问**的数字）。 */
.cm-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  flex: none;
  border: 1px solid var(--cm-line);
  border-radius: 10px;
  background: #fff;
  color: var(--cm-text);
  font-size: 20px;
}

.cm-card-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
}

/* 标题 16px/20 · 副标题 12px/16 #858481（实测值） */
.cm-card-name {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  color: var(--cm-text);
  font-size: 16px;      /* Manus 实测卡片标题字号 */
  font-weight: 500;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cm-tag {
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.06);
  color: var(--cm-muted);
  font-size: 10px;
}

/* 允许换到第二行再截断：参考实现里长描述就是两行，强行单行会截掉半句话 */
.cm-card-sub {
  display: -webkit-box;
  overflow: hidden;
  color: var(--cm-muted);
  font-size: 12px;
  line-height: 16px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.cm-tick {
  flex: none;
  color: var(--cm-text);
  font-size: 14px;
}

.cm-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex: none;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--cm-text);
  cursor: pointer;
  font-size: 13px;
}

.cm-add:hover {
  background: var(--cm-hover);
}

.cm-add:disabled {
  color: var(--cm-muted);
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .cm-panel {
    padding: 0 16px 20px;
  }

  .cm-head {
    padding: 20px 0 14px;
  }

  .cm-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .cm-search {
    width: 100%;
  }
}
</style>

<style>
/* 两个坑叠在一起，缺一条都改不动这个容器：
   ① a-modal 的 DOM 被 **teleport 到 body**，scoped 的 `:deep()` 仍以组件根为起点，
      **选不到它** —— 所以这里必须是非 scoped 块（靠 wrapClassName 限定作用域，
      不会外溢到别的弹窗）；
   ② 光是非 scoped 还不够：antd v5 的样式是**运行时注入**到 <head> 的，比打包 CSS
      更靠后，同特异性时它赢。实测规则确实在产物里、`cm-wrap` 也挂上了，圆角却依旧是
      antd 默认的 10px。这里用重复类名把特异性抬到 (0,3,0) 压过它，比 !important 干净。 */
.cm-wrap.cm-wrap .ant-modal-content {
  overflow: hidden;
  padding: 0;
  border: 0;
  /* 这两条**必须** !important，不是偷懒（2026-07-28 逐条查层叠查出来的）：
     项目后台主题里有一条全局规则把 .ant-modal-content 也框进了选择器列表——
       html[data-theme="light"] …, .ant-modal-content {
         border-radius: 10px !important; background: var(--admin-panel) !important }
     `!important` 面前特异性无效，(0,3,0) 也赢不了。
     顺带排除掉的备选：antd 的 `:styles` 传行内样式同样打不过 `!important`
     （普通行内声明优先级低于 author !important），那条路根本走不通。 */
  border-radius: 16px !important;   /* Manus 实测弹层圆角 */
  background: #f8f8f7 !important;   /* Manus 实测弹层底色，不是纯白 */
}

.cm-wrap.cm-wrap .ant-modal-body {
  padding: 0;
}
</style>
