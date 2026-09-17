<template>
  <div :class="['skin-preview-stage', `is-${device}`]" :style="styleVars">
    <MainChatSkinBackdrop v-if="layout" :skin="skin" :layout="layout" :empty-state="true" />
    <MainChatSkinDecorations v-if="layout" :skin="skin" :layout="layout" region="page" />
    <div class="preview-content">
      <div class="preview-intro">
        <MainChatSkinDecorations v-if="layout" :skin="skin" :layout="layout" region="intro" />
        <small>校园百事通</small>
        <h3>你好，有什么校园事务想了解？</h3>
        <p>校园政策、办事流程和常见问题，都可以在这里查询。</p>
      </div>
      <div class="preview-composer">
        <MainChatSkinDecorations v-if="layout" :skin="skin" :layout="layout" region="composer" />
        <img
          class="preview-campus-mascot"
          src="/agent-icons/builtin/campus-services-static-v2.png"
          alt=""
          aria-hidden="true"
        />
        <span>请输入校园政策、办事流程或常见问题…</span>
        <div><i>＋</i><b>↑</b></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import MainChatSkinBackdrop from './MainChatSkinBackdrop.vue';
import MainChatSkinDecorations from './MainChatSkinDecorations.vue';
import { mainChatSkinStyle, resolveMainChatSkinLayout } from './runtime';
import type { HydratedMainChatSkin, MainChatSkinDevice } from './types';

const props = defineProps<{
  skin: HydratedMainChatSkin;
  device: MainChatSkinDevice;
}>();

const layout = computed(() => resolveMainChatSkinLayout(props.skin.manifest, props.device));
const styleVars = computed(() => mainChatSkinStyle(props.skin, layout.value));
</script>

<style scoped lang="less">
.skin-preview-stage {
  position: relative;
  width: min(100%, 920px);
  aspect-ratio: 16 / 8.7;
  overflow: hidden;
  border: 1px solid rgba(60, 104, 145, 0.2);
  border-radius: 14px;
  background: var(--main-chat-skin-page, #fff);
  color: var(--main-chat-skin-title, #111827);
  box-shadow: 0 18px 40px rgba(31, 66, 99, 0.1);
}

.skin-preview-stage.is-tablet { width: min(100%, 660px); aspect-ratio: 4 / 3; }
.skin-preview-stage.is-mobile { width: min(100%, 330px); aspect-ratio: 9 / 16; }
.preview-content { position: relative; z-index: 3; display: flex; height: 100%; box-sizing: border-box; align-items: center; flex-direction: column; padding: 14% 7% 8%; }
.preview-intro { position: relative; width: 100%; text-align: center; }
.preview-intro small { color: var(--main-chat-skin-body, #667085); font-size: 11px; font-weight: 700; }
.preview-intro h3 { margin: 9px 0 0; color: var(--main-chat-skin-title, #111827); font-size: clamp(17px, 2.2vw, 28px); letter-spacing: -0.035em; }
.preview-intro p { margin: 11px auto 0; color: var(--main-chat-skin-body, #667085); font-size: 11px; line-height: 1.7; }
.preview-composer { position: relative; z-index: 3; display: flex; width: min(100%, var(--chat-content-max-width, 820px)); min-height: 84px; box-sizing: border-box; justify-content: space-between; flex-direction: column; margin-top: 12%; padding: 14px 12px 10px; border: 1px solid var(--main-chat-skin-composer-border, #e5e7eb); border-radius: var(--main-chat-skin-composer-radius, 24px); background: var(--main-chat-skin-composer, #fff); box-shadow: var(--main-chat-skin-composer-shadow); }
.preview-campus-mascot { position: absolute; z-index: 3; bottom: calc(100% - 4px); left: 92px; display: block; width: 74px; height: auto; pointer-events: none; user-select: none; }
.preview-composer > span { position: relative; z-index: 4; color: #98a2b3; font-size: 11px; }
.preview-composer > div { position: relative; z-index: 4; display: flex; align-items: center; justify-content: space-between; }
.preview-composer i, .preview-composer b { display: grid; width: 27px; height: 27px; place-items: center; border-radius: 50%; font-size: 13px; font-style: normal; }
.preview-composer i { border: 1px solid #dce4ea; background: #fff; color: #586b7c; }
.preview-composer b { background: var(--main-chat-skin-accent, #202124); color: #fff; }
.skin-preview-stage.is-mobile .preview-content { justify-content: center; padding: 22% 7% 12%; }
.skin-preview-stage.is-mobile .preview-intro h3 { font-size: 20px; line-height: 1.3; }
.skin-preview-stage.is-mobile .preview-intro p { max-width: 250px; }
.skin-preview-stage.is-mobile .preview-composer { min-height: 96px; margin-top: 24%; }
.skin-preview-stage.is-tablet .preview-campus-mascot { left: 76px; width: 66px; }
.skin-preview-stage.is-mobile .preview-campus-mascot { left: 52px; width: 56px; }
</style>
