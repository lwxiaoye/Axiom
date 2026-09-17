<template>
  <span class="research-orb" :class="{ active }" :data-role="role" aria-hidden="true">
    <span class="orb-motion">
      <span class="orb-art">
        <img
          class="orb-body"
          src="/agent-icons/research/leader-eyeless-v2.svg"
          alt=""
        />
        <span class="orb-gaze">
          <img class="orb-eye orb-eye-left" src="/agent-icons/research/white-eye-v1.svg" alt="" />
          <img class="orb-eye orb-eye-right" src="/agent-icons/research/white-eye-v1.svg" alt="" />
        </span>
        <span v-if="role === 'researcher'" class="orb-tool tool-magnifier">
          <SearchOutlined />
        </span>
        <span v-else-if="role === 'analyst'" class="analyst-tools">
          <span class="orb-tool tool-paper">
            <FileTextFilled class="paper-glyph" />
          </span>
          <span class="orb-tool tool-writer">
            <img src="/agent-icons/research/pencil-simple-fill.svg" alt="" />
          </span>
        </span>
        <span v-else-if="role === 'verifier'" class="orb-tool tool-verify">
          <CheckCircleFilled />
        </span>
      </span>
    </span>
  </span>
</template>
<script setup lang="ts">
import { CheckCircleFilled, FileTextFilled, SearchOutlined } from '@ant-design/icons-vue';
import type { ResearchRole } from '../utils/researchTeam';
defineProps<{ role: ResearchRole | 'leader'; active?: boolean }>();
</script>
<style scoped lang="less">
.research-orb { position: relative; display: inline-flex; flex: none; width: 26px; height: 26px; overflow: visible; background: transparent; vertical-align: middle; isolation: isolate; }
/* Normalize the blue body to ~80% of the slot, rather than the asset canvas. */
.research-orb[data-role='leader'] {
  --orb-scale: 1.16; --orb-x: 3.4%; --orb-y: 2.1%;
  --eye-left-x: 41.5%; --eye-right-x: 57.9%; --eye-y: 37.4%; --eye-width: 9%; --eye-height: 16.2%;
}
.research-orb[data-role='researcher'] {
  --orb-scale: 1.16; --orb-x: 3.4%; --orb-y: 2.1%;
  --eye-left-x: 41.5%; --eye-right-x: 57.9%; --eye-y: 37.4%; --eye-width: 9%; --eye-height: 16.2%;
}
.research-orb[data-role='analyst'] {
  --orb-scale: 1.16; --orb-x: 3.4%; --orb-y: 2.1%;
  --eye-left-x: 41.5%; --eye-right-x: 57.9%; --eye-y: 37.4%; --eye-width: 9%; --eye-height: 16.2%;
}
.research-orb[data-role='verifier'] {
  --orb-scale: 1.16; --orb-x: 3.4%; --orb-y: 2.1%;
  --eye-left-x: 41.5%; --eye-right-x: 57.9%; --eye-y: 37.4%; --eye-width: 9%; --eye-height: 16.2%;
}
.orb-motion { position: absolute; inset: 0; display: block; transform: translateZ(0); backface-visibility: hidden; }
.orb-art { position: absolute; inset: 0; display: block; transform: translate(var(--orb-x), var(--orb-y)) scale(var(--orb-scale)); transform-origin: center; }
.orb-body { display: block; width: 100%; height: 100%; object-fit: contain; }
.orb-gaze { position: absolute; inset: 0; display: block; transform: translateZ(0); }
.orb-eye {
  position: absolute;
  top: var(--eye-y);
  width: var(--eye-width);
  height: var(--eye-height);
  object-fit: fill;
  transform-origin: 50% 50%;
  will-change: transform;
}
.orb-eye-left { left: var(--eye-left-x); }
.orb-eye-right { left: var(--eye-right-x); }
.analyst-tools { position: absolute; inset: 0; display: block; }
.orb-tool { position: absolute; inset: 0; display: block; width: 100%; height: 100%; pointer-events: none; will-change: transform; }
.orb-tool :deep(.anticon),
.orb-tool :deep(svg),
.orb-tool > img { display: block; width: 100%; height: 100%; }
.tool-magnifier { color: #0b3d9c; transform: translate3d(-24%, 24%, 0) scale(.48) rotate(-14deg); }
.tool-paper { transform: translate3d(-30%, 27%, 0) scale(.55) rotate(-5deg); }
.paper-glyph { position: absolute; inset: 0; display: block; color: #eaf2ff; filter: drop-shadow(0 0 .45px #0a3a96); }
.tool-writer { color: #0a3a96; transform: translate3d(12%, 26%, 0) scale(.42) rotate(-10deg); }
.tool-verify { color: #0a3a96; transform: translate3d(28%, 27%, 0) scale(.36) rotate(-7deg); }

/* Each role stays still for part of its loop, then performs its own short gesture.
 * Negative delays start the team at different phases instead of a synchronized dance. */
.research-orb.active[data-role='leader'] .orb-motion {
  transform-origin: 52% 78%;
  animation: leader-listen 7.4s cubic-bezier(.38, 0, .22, 1) -2.1s infinite;
}
.research-orb.active[data-role='researcher'] .orb-motion {
  transform-origin: 56% 76%;
  animation: researcher-scan 6.1s cubic-bezier(.4, 0, .2, 1) -4.7s infinite;
}
.research-orb.active[data-role='analyst'] .orb-motion {
  transform-origin: 56% 78%;
  animation: analyst-note 5.3s cubic-bezier(.35, 0, .25, 1) -1.3s infinite;
}
.research-orb.active[data-role='verifier'] .orb-motion {
  transform-origin: 50% 76%;
  animation: verifier-check 8.2s cubic-bezier(.4, 0, .2, 1) -6.4s infinite;
}

/* White eyes stay solid. Each role uses its own route, cadence and phase,
 * matching the reference's long holds, quick glances and occasional blinks. */
.research-orb.active[data-role='leader'] .orb-gaze { animation: leader-gaze 8.7s ease-in-out -1.9s infinite; }
.research-orb.active[data-role='leader'] .orb-eye { animation: leader-blink 7.9s ease-in-out -3.2s infinite; }
.research-orb.active[data-role='researcher'] .orb-gaze { animation: researcher-gaze 6.4s cubic-bezier(.4, 0, .2, 1) -4.1s infinite; }
.research-orb.active[data-role='researcher'] .orb-eye { animation: researcher-blink 9.3s ease-in-out -6.8s infinite; }
.research-orb.active[data-role='analyst'] .orb-gaze { animation: analyst-gaze 7.1s ease-in-out -0.7s infinite; }
.research-orb.active[data-role='analyst'] .orb-eye { animation: analyst-double-blink 5.9s ease-in-out -2.6s infinite; }
.research-orb.active[data-role='verifier'] .orb-gaze { animation: verifier-gaze 5.2s cubic-bezier(.45, 0, .2, 1) -3.7s infinite; }
.research-orb.active[data-role='verifier'] .orb-eye { animation: verifier-blink 8.6s ease-in-out -5.1s infinite; }
.research-orb.active[data-role='researcher'] .tool-magnifier { animation: researcher-magnify 6.4s cubic-bezier(.4, 0, .2, 1) -4.1s infinite; }
.research-orb.active[data-role='analyst'] .tool-paper { animation: analyst-paper 6.8s ease-in-out -4.4s infinite; }
.research-orb.active[data-role='analyst'] .tool-writer { animation: analyst-write 6.8s cubic-bezier(.4, 0, .2, 1) -4.4s infinite; }
.research-orb.active[data-role='verifier'] .tool-verify { animation: verifier-stamp 5.2s ease-in-out -3.7s infinite; }

@keyframes leader-listen {
  0%, 10%, 31%, 56%, 78%, 100% { transform: translate3d(0, 0, 0) rotate(0); }
  15% { transform: translate3d(0, -.18px, 0) rotate(-.16deg) scale(1.003); }
  20% { transform: translate3d(.05px, .02px, 0) rotate(.12deg); }
  36% { transform: translate3d(-.08px, -.07px, 0) rotate(-.2deg); }
  43% { transform: translate3d(.06px, -.03px, 0) rotate(.16deg); }
  61% { transform: translate3d(0, -.2px, 0) scale(1.003); }
  66% { transform: translate3d(0, .03px, 0); }
  83% { transform: translate3d(.08px, -.05px, 0) rotate(.18deg); }
  89% { transform: translate3d(-.04px, 0, 0) rotate(-.1deg); }
}
@keyframes researcher-scan {
  0%, 9%, 42%, 61%, 82%, 100% { transform: translate3d(0, 0, 0) rotate(0); }
  15% { transform: translate3d(-.12px, .02px, 0) rotate(-.28deg); }
  23% { transform: translate3d(-.06px, -.1px, 0) rotate(-.15deg); }
  32% { transform: translate3d(.13px, -.03px, 0) rotate(.27deg); }
  37% { transform: translate3d(.05px, .01px, 0) rotate(.1deg); }
  66% { transform: translate3d(0, -.2px, 0) rotate(-.17deg) scale(1.003); }
  71% { transform: translate3d(.03px, .02px, 0) rotate(.08deg); }
  87% { transform: translate3d(.1px, -.04px, 0) rotate(.22deg); }
  93% { transform: translate3d(-.03px, 0, 0) rotate(-.08deg); }
}
@keyframes analyst-note {
  0%, 13%, 36%, 57%, 79%, 100% { transform: translate3d(0, 0, 0) rotate(0); }
  18% { transform: translate3d(-.06px, .08px, 0) rotate(-.18deg); }
  23% { transform: translate3d(.03px, -.1px, 0) rotate(.13deg); }
  28% { transform: translate3d(-.05px, .04px, 0) rotate(-.15deg); }
  32% { transform: translate3d(.02px, -.02px, 0) rotate(.06deg); }
  62% { transform: translate3d(.08px, -.08px, 0) rotate(.2deg); }
  69% { transform: translate3d(-.06px, .02px, 0) rotate(-.12deg); }
  84% { transform: translate3d(0, -.16px, 0) scale(1.003); }
  90% { transform: translate3d(0, .02px, 0); }
}
@keyframes verifier-check {
  0%, 15%, 48%, 69%, 87%, 100% { transform: translate3d(0, 0, 0) rotate(0); }
  21%, 28% { transform: translate3d(-.1px, -.02px, 0) rotate(-.18deg); }
  36%, 42% { transform: translate3d(.1px, -.02px, 0) rotate(.18deg); }
  54% { transform: translate3d(-.03px, -.18px, 0) rotate(-.09deg) scale(1.003); }
  59% { transform: translate3d(.02px, .02px, 0) rotate(.07deg); }
  74%, 78% { transform: translate3d(.08px, -.06px, 0) rotate(.16deg); }
  92% { transform: translate3d(-.04px, -.02px, 0) rotate(-.1deg); }
}

@keyframes leader-gaze {
  0%, 14%, 30%, 51%, 71%, 88%, 100% { transform: translate3d(0, 0, 0); }
  19%, 25% { transform: translate3d(-.8px, -.15px, 0); }
  57%, 65% { transform: translate3d(.9px, .05px, 0); }
  76%, 82% { transform: translate3d(.2px, -.75px, 0); }
}
@keyframes researcher-gaze {
  0%, 10%, 41%, 58%, 84%, 100% { transform: translate3d(0, 0, 0); }
  16%, 26% { transform: translate3d(-1.15px, .08px, 0); }
  33%, 38% { transform: translate3d(1.05px, -.12px, 0); }
  64%, 73% { transform: translate3d(.65px, -.7px, 0); }
  78% { transform: translate3d(-.45px, -.35px, 0); }
}
@keyframes analyst-gaze {
  0%, 18%, 37%, 54%, 76%, 100% { transform: translate3d(0, 0, 0); }
  23%, 31% { transform: translate3d(-.3px, -.85px, 0); }
  60%, 68% { transform: translate3d(.7px, .42px, 0); }
  82%, 91% { transform: translate3d(-.75px, .15px, 0); }
}
@keyframes verifier-gaze {
  0%, 9%, 30%, 47%, 68%, 86%, 100% { transform: translate3d(0, 0, 0); }
  14%, 23% { transform: translate3d(-.85px, 0, 0); }
  35%, 42% { transform: translate3d(.9px, -.1px, 0); }
  53%, 62% { transform: translate3d(-.55px, -.55px, 0); }
  74%, 80% { transform: translate3d(.45px, .4px, 0); }
}

@keyframes leader-blink {
  0%, 42%, 47%, 76%, 81%, 100% { transform: scaleY(1); }
  44.5%, 78.5% { transform: scaleY(.08); }
}
@keyframes researcher-blink {
  0%, 58%, 63%, 100% { transform: scaleY(1); }
  60.5% { transform: scaleY(.1); }
}
@keyframes analyst-double-blink {
  0%, 31%, 35%, 38%, 42%, 100% { transform: scaleY(1); }
  33%, 40% { transform: scaleY(.08); }
}
@keyframes verifier-blink {
  0%, 69%, 74%, 100% { transform: scaleY(1); }
  71.5% { transform: scaleY(.12); }
}

@keyframes researcher-magnify {
  0%, 46%, 86%, 100% { transform: translate3d(-24%, 24%, 0) scale(.48) rotate(-14deg); }
  56%, 74% { transform: translate3d(1%, -4%, 0) scale(.48) rotate(-21deg); }
  80% { transform: translate3d(-9%, 7%, 0) scale(.48) rotate(-18deg); }
}
@keyframes analyst-paper {
  0%, 35%, 67%, 100% { transform: translate3d(-30%, 27%, 0) scale(.55) rotate(-5deg); }
  43%, 59% { transform: translate3d(-30%, 26%, 0) scale(.55) rotate(-4.4deg); }
}
@keyframes analyst-write {
  0%, 31%, 69%, 100% { transform: translate3d(12%, 26%, 0) scale(.42) rotate(-10deg); }
  39% { transform: translate3d(6%, 22%, 0) scale(.42) rotate(-13deg); }
  47% { transform: translate3d(0, 25%, 0) scale(.42) rotate(-9deg); }
  55% { transform: translate3d(6%, 28%, 0) scale(.42) rotate(-12deg); }
  62% { transform: translate3d(0, 30%, 0) scale(.42) rotate(-9deg); }
}
@keyframes verifier-stamp {
  0%, 35%, 73%, 100% { transform: translate3d(28%, 27%, 0) scale(.36) rotate(-7deg); }
  43%, 50% { transform: translate3d(23%, 15%, 0) scale(.36) rotate(-12deg); }
  56% { transform: translate3d(22%, 29%, 0) scale(.36) rotate(-4deg); }
  62% { transform: translate3d(26%, 23%, 0) scale(.36) rotate(-7deg); }
}

@media (prefers-reduced-motion: reduce) {
  .research-orb.active .orb-motion { animation: none; transform: none; }
  .research-orb.active .orb-gaze,
  .research-orb.active .orb-eye,
  .research-orb.active .orb-tool { animation: none; }
}
</style>
