<template>
  <component :is="comp" v-if="comp" />
  <img v-else-if="brandImg" :src="brandImg" class="cn-brand-img" alt="" aria-hidden="true" />
  <span v-else-if="brand" class="cn-brand-icon" aria-hidden="true" v-html="brand" />
  <ApiOutlined v-else />
</template>

<script lang="ts">
/**
 * 实例序号发生器。**必须放在普通 `<script>` 块里，不能写在 `<script setup>` 中**：
 * `<script setup>` 的内容会被编译进 `setup()`，里面的 `let seq = 0` 是**每个实例各自
 * 归零**的，算出来的后缀永远是同一个值——改了等于没改，而且完全看不出来。
 */
let iconSeq = 0;
export function nextIconSeq(): number {
  iconSeq += 1;
  return iconSeq;
}
</script>

<script setup lang="ts">
/**
 * 连接器图标：按 provider 的 `icon` 标识取图。
 *
 * 抽出来的原因很具体：这段映射此前在 ConnectorMenu(4 处) / ConnectorsModal / ConnectorDetailModal
 * 一共复制了 **6 份**，全是 `GithubOutlined v-if=... / ApiOutlined v-else`。只接了 GitHub 一家时
 * 看不出问题，但每加一个连接器就要同步改 6 个地方——漏掉任何一处，那个入口就悄悄退回通用图标，
 * 而它长得像「正常的默认样式」，不像 bug。这正是「同构的两处只改一处」那类最难发现的缺陷。
 *
 * 现在加连接器只改下面两张表。表里都没有的退回 ApiOutlined（通用「应用」图标），
 * 不报错也不留空——图标缺失不该阻断连接器可用。
 *
 * ## 为什么用内联品牌 SVG 而不是 antd 图标（2026-07-29 用户拍板）
 * antd 的 `GoogleOutlined` 是一个字母 G，`WindowsOutlined` 是四格窗口——它们和 Gmail 那个
 * 彩色信封 M、Outlook 那个蓝色信封**根本不是一个东西**。用户在连接器列表里靠图标一眼认出
 * 是哪个应用，字形对不上就等于没有图标。GitHub 是例外：antd 的 GithubOutlined 就是官方
 * 章鱼猫单色版，形状是对的，保持不动。
 *
 * 不外链第三方 CDN 取品牌 logo：内网部署取不到，且会把可用性绑在外部站点上。
 */
import { computed } from 'vue';
import { ApiOutlined, GithubOutlined, GlobalOutlined, MailOutlined } from '@ant-design/icons-vue';

const props = defineProps<{ icon?: string }>();

/** 形状本来就对的，继续用 antd 组件 */
const MAP: Record<string, any> = {
  github: GithubOutlined,
  mail: MailOutlined,
  browser: GlobalOutlined,
};

/**
 * 官方品牌字形。多色，跟随各家品牌规范的配色，不随主题变色——品牌标识就该长成它自己
 * 的样子，跟着深浅色主题变会认不出来。尺寸用 1em 跟随字号。
 */
const BRAND: Record<string, string> = {
  // Gmail：白信封 + 彩色 M。四色分别是 Google 品牌色
  gmail: `<svg viewBox="0 0 24 24" width="1em" height="1em" xmlns="http://www.w3.org/2000/svg">
    <path fill="#4285F4" d="M22.364 21.818h-3.819V12.5L24 8.318v11.864c0 .904-.732 1.636-1.636 1.636z"/>
    <path fill="#34A853" d="M1.636 21.818h3.819V12.5L0 8.318v11.864c0 .904.732 1.636 1.636 1.636z"/>
    <path fill="#FBBC04" d="M18.545 3.545 24 8.318V4.364c0-2.023-2.31-3.178-3.927-1.964l-1.528 1.145z"/>
    <path fill="#EA4335" d="M5.455 12.5V3.545L12 8.455l6.545-4.91V12.5L12 17.41z"/>
    <path fill="#C5221F" d="M0 4.364v3.954L5.455 12.5V3.545L3.927 2.4C2.309 1.186 0 2.34 0 4.364z"/>
  </svg>`,
  // Outlook：蓝色信封 + 左侧 O 方块
  outlook: `<svg viewBox="0 0 24 24" width="1em" height="1em" xmlns="http://www.w3.org/2000/svg">
    <path fill="#0A2767" d="M24 12.06c0-.34-.18-.65-.47-.82l-.02-.01-8.2-4.85a.89.89 0 0 0-.98 0l-8.2 4.85-.02.01a.95.95 0 0 0 .01 1.64l8.2 4.85c.3.18.68.18.98 0l8.2-4.85c.3-.17.5-.48.5-.82z"/>
    <path fill="#0364B8" d="M6.82 8.72h5.3v4.86h-5.3zM22.86 4.36V2.62c0-.5-.4-.9-.9-.9H9.68c-.5 0-.9.4-.9.9v1.74l7.2 1.92z"/>
    <path fill="#0078D4" d="M8.78 4.36h4.86v4.37H8.78z"/>
    <path fill="#28A8EA" d="M17.28 4.36h5.58v4.37h-5.58zM13.64 4.36h3.64v4.37h-3.64z"/>
    <path fill="#0078D4" d="M13.64 8.72h3.64v4.86h-3.64z"/>
    <path fill="#0364B8" d="M17.28 8.72h5.58v4.86h-5.58z"/>
    <path fill="#14447D" d="M23.53 11.24 23.51 11.23l-8.2-4.62a.9.9 0 0 0-.15-.07.89.89 0 0 0-.83.07l-8.2 4.62-.03.01a.95.95 0 0 0-.48.83v9.02c0 .5.4.9.9.9h16.6c.03 0 .05 0 .08-.01a.9.9 0 0 0 .8-.89v-9.02c0-.34-.18-.65-.47-.83z"/>
    <path fill="#0078D4" d="M11.28 19.11h-.06l-4.9-3.35v-4.52l.05-.03 4.9 2.9.01.01z" opacity=".5"/>
    <path fill="#1490DF" d="M23.53 11.24 23.51 11.25l-8.2 4.62a.89.89 0 0 1-.9.03l2.86 3.83 6.24 1.35h.01a.9.9 0 0 0 .48-.79v-9.02c0-.34-.18-.65-.47-.83z" opacity=".9"/>
    <path fill="#000" d="M6 20.26v-.16l-.02-.01-.06-.03-.02-.01a.9.9 0 0 1-.28-.72v.14l4.66-2.69.01-.01.06-.03z" opacity=".05"/>
    <path fill="#0A2767" d="M6.05 20.7 6 20.69l-.02-.02a.9.9 0 0 1-.36-.72v-9.02c0-.34.18-.65.47-.82l.02-.02 8.2-4.62a.89.89 0 0 1 .9.02l-8.2 4.6a.95.95 0 0 0-.49.84z" opacity=".1"/>
    <path fill="#1B1B1B" d="M11.53 6.09H1.15c-.5 0-.91.4-.91.9v10.38c0 .5.4.9.9.9h10.39c.5 0 .9-.4.9-.9V6.99c0-.5-.4-.9-.9-.9z" opacity=".02"/>
    <path fill="#0078D4" d="M10.62 5.18H.9c-.5 0-.9.4-.9.9v9.72c0 .5.4.9.9.9h9.72c.5 0 .9-.4.9-.9V6.08c0-.5-.4-.9-.9-.9z"/>
    <path fill="#FFF" d="M5.76 7.36c-2 0-3.42 1.55-3.42 3.6 0 2.03 1.4 3.56 3.36 3.56 1.98 0 3.4-1.5 3.4-3.62 0-2.06-1.36-3.54-3.34-3.54zm-.05 5.9c-1.06 0-1.78-.94-1.78-2.32 0-1.4.73-2.34 1.8-2.34 1.07 0 1.76.93 1.76 2.32 0 1.42-.68 2.34-1.78 2.34z"/>
  </svg>`,
  // Canva：品牌青紫渐变圆 + C。目前未上架（卡在 Canva 的 Waitlist 准入），字形先留着
  canva: `<svg viewBox="0 0 24 24" width="1em" height="1em" xmlns="http://www.w3.org/2000/svg">
    <defs><linearGradient id="cn-canva-g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#00C4CC"/><stop offset="100%" stop-color="#7D2AE8"/>
    </linearGradient></defs>
    <circle cx="12" cy="12" r="11" fill="url(#cn-canva-g)"/>
    <path d="M15.4 14.6c-.9 1.2-2.2 1.9-3.5 1.9-2 0-3.3-1.5-3.3-3.7 0-2.9 1.9-5.3 4-5.3 1.1 0 1.8.6 1.8 1.5 0 .5-.2.9-.5 1.2-.2.2-.5.1-.5-.2 0-.7-.3-1.1-.9-1.1-1.2 0-2.3 1.7-2.3 3.7 0 1.4.7 2.3 1.8 2.3.9 0 1.8-.5 2.5-1.4.3-.3.7 0 .4.4z" fill="#fff"/>
  </svg>`,
};

/**
 * 同一个连接器会在页面上同时渲染多处（列表行 + 详情弹窗），而品牌 SVG 里的
 * `<defs><linearGradient id="...">` 是**全局 DOM id**——重复时浏览器只认文档顺序里的
 * 第一个。目前两份定义完全相同所以无害，但这是个会静默生效的隐患：将来同一个 id
 * 下出现不同渐变（换配色、加深色版），第二处就会莫名其妙用上第一处的颜色，
 * 而且**不报错、不留痕**，只会看着"颜色不对"。
 *
 * 每个组件实例给 id 一个后缀，从根上消掉这类问题。正则只改 `id="cn-*"` 和对应的
 * `url(#cn-*)`，不碰别的属性。
 */
const uid = `i${nextIconSeq()}`;

/**
 * 光栅品牌标：对方只发布位图、拿不到官方矢量时用这张表。内联成 data URI 而不是外链
 * —— 内网部署取不到对方 CDN，外链等于把可用性绑在别人站点上（见文件顶部说明）。
 *
 * qqmail：取自 QQ 邮箱现役站点资源（`res.wx.qq.com/.../qqmail_favicon_96h`，96×96 带
 * 透明通道）。**这是官方标识本身，不是仿画的**——此前这里是一版按品牌形态手绘的蓝色
 * 圆角方块 + 白信封，和真标（彩环 + 企鹅）根本不是一个东西，用户在列表里认不出来。
 * 换新标时把下面这串换掉即可，其余代码不用动。
 */
const BRAND_IMG: Record<string, string> = {
  qqmail: 'data:image/png;base64,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',
};

const comp = computed(() => MAP[String(props.icon || '').toLowerCase()] || null);
const brand = computed(() => {
  const raw = BRAND[String(props.icon || '').toLowerCase()] || '';
  return raw
    ? raw.replace(/id="(cn-[\w-]+)"/g, `id="$1-${uid}"`).replace(/url\(#(cn-[\w-]+)\)/g, `url(#$1-${uid})`)
    : '';
});
const brandImg = computed(() => BRAND_IMG[String(props.icon || '').toLowerCase()] || '');
</script>

<style scoped>
/* 品牌 SVG 用行内块跟随字号，和 antd 图标的盒模型对齐，免得同一行里高低不齐 */
.cn-brand-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.cn-brand-icon :deep(svg) {
  display: block;
}

/* 光栅品牌标：跟随字号，方形不变形。1em 让它和同一行的 antd 图标对齐。 */
.cn-brand-img {
  width: 1em;
  height: 1em;
  object-fit: contain;
  display: block;
}
</style>
