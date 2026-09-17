<template>
  <div class="body" data-theme="A">
    <div class="stage">
      <div class="left-panel">
        <div class="meta-text"><span>AXIOM</span>CAMPUS SERVICES</div>
        <div class="corner-tl"></div>
        <div class="corner-tr"></div>
        <div class="corner-bl"></div>
        <div class="corner-br"></div>

        <div class="grid-bg"></div>
        <div class="orbit-rings">
          <div class="orbit-ring"></div>
          <div class="orbit-ring"></div>
          <div class="orbit-ring"></div>
          <div class="orbit-ring"></div>
        </div>
        <div class="core-glow"></div>
        <canvas class="particles-canvas" ref="canvasRef"></canvas>
        <div class="scan-overlay"></div>

        <div class="data-stream left">
          <span>CAMPUS</span>
          <span class="data-stream-bar" style="animation-delay:0s"></span>
          <span class="data-stream-bar" style="animation-delay:0.1s"></span>
          <span class="data-stream-bar" style="animation-delay:0.2s"></span>
          <span class="data-stream-bar" style="animation-delay:0.3s"></span>
          <span class="data-stream-bar" style="animation-delay:0.4s"></span>
          <span>KNOWLEDGE</span>
        </div>
        <div class="data-stream right">
          <span>AXIOM</span>
          <span class="data-stream-bar" style="animation-delay:0.5s"></span>
          <span class="data-stream-bar" style="animation-delay:0.6s"></span>
          <span class="data-stream-bar" style="animation-delay:0.7s"></span>
          <span class="data-stream-bar" style="animation-delay:0.8s"></span>
          <span>SERVICES</span>
        </div>

        <div class="floating-badge badge-1">CAMPUS KNOWLEDGE</div>
        <div class="floating-badge badge-2">OFFICIAL SOURCES</div>
        <div class="floating-badge badge-3">STUDENT SERVICES</div>

        <div class="hero-text">
          <div class="hero-tag">AXIOM · CAMPUS AGENT</div>
          <h2 class="hero-title">连接<em>校园</em><br />让办事更清晰</h2>
          <p class="hero-sub">查询校园知识与办事流程<br />每一步都有可靠依据</p>
        </div>
      </div>

      <div class="aurora"></div>

      <div class="right-panel">
        <div class="login-card">

          <div class="brand">
            <div class="brand-mark">
              <span class="mark-a">A</span>
              <svg class="mark-c" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" />
                <path d="M2 17l10 5 10-5" />
                <path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <div class="brand-copy">
              <span class="brand-name">AXIOM <span>校园智能体</span></span>
              <span class="brand-context">校园知识 · 办事指南</span>
            </div>
          </div>

          <div class="title-block">
            <h1 class="title">欢迎回来</h1>
            <p class="subtitle">登录账户以继续使用</p>
          </div>

          <form @submit="handleLogin">

            <div class="field">
              <label class="field-label flex-center" for="login-account">
                <i class="icon icon-line-tel mr-2"></i>
                <div>账号</div>
              </label>
              <div class="input-wrap">

                <input
                  id="login-account"
                  v-model="formData.username"
                  type="text"
                  class="field-input"
                  placeholder="请输入学号/工号"
                  required
                  autocomplete="username"
                  autocapitalize="none"
                  spellcheck="false"
                />
                <svg class="field-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
              </div>
            </div>

            <div class="field">
              <!-- <label class="field-label"> <i class="icon icon-password"></i> 密码</label> -->
              <label class="field-label flex-center" for="login-password">
                <i class="icon icon-line-pad mr-2"></i>
                <div>密码</div>
              </label>
              <div class="input-wrap">
                <input id="login-password" type="password" class="field-input" placeholder="请输入密码" required autocomplete="current-password" v-model="formData.password" />
                <svg class="field-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
              </div>
            </div>

            <div class="field">
              <label class="field-label flex-center" for="login-captcha">
                <i class="icon icon-line-msg mr-2"></i>
                <div>验证码</div>
              </label>
              <div class="captcha-row">
                <div class="input-wrap">
                  <input
                    id="login-captcha"
                    v-model="formData.inputCode"
                    type="text"
                    class="field-input"
                    placeholder="请输入 4 位验证码"
                    required
                    maxlength="4"
                    autocomplete="off"
                    autocapitalize="characters"
                    spellcheck="false"
                  />
                  <svg class="field-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M9 12l2 2 4-4" />
                    <path d="M21 12c0 5-3.5 7.5-8.5 9.5C7.5 19.5 3 17 3 12V5l9-3 9 3z" />
                  </svg>
                </div>
                <button
                  type="button"
                  class="captcha-box"
                  @click="handleChangeCheckCode()"
                  title="点击刷新"
                  aria-label="刷新图形验证码"
                >
                  <img v-if="randCodeData.requestCodeSuccess" :src="randCodeData.randCodeImage" class="imgs" alt="图形验证码" />
                  <img v-else style="margin-top: 2px; max-width: initial" :src="codeImg" class="imgs" alt="图形验证码" />
                  <!-- <svg class="captcha-svg" ref="captchaSvgRef" viewBox="0 0 120 46" preserveAspectRatio="none"></svg> -->
                </button>
              </div>
            </div>

            <div class="options-row">
              <label class="checkbox">
                <input type="checkbox" v-model="rememberMe" />
                <span class="check-mark">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </span>
                <span>记住我</span>
              </label>
              <!-- <a href="#" class="forgot" onclick="event.preventDefault();alert('请联系管理员重置密码');">忘记密码？</a> -->
            </div>

            <button type="submit" class="login-btn" :disabled="loginLoading" :aria-busy="loginLoading">
              <span class="login-btn-content">
                <span class="btn-label">{{ loginLoading ? '验证中…' : '登 录' }}</span>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="5" y1="12" x2="19" y2="12" />
                  <polyline points="12 5 19 12 12 19" />
                </svg>
              </span>
            </button>

          </form>

          <!-- <p class="footer">还没有账户？<a href="#">立即注册</a></p> -->

        </div>
      </div>
    </div>

    <!-- <div class="tweaks">
      <button class="tweaks-toggle" onclick="toggleTweaks()" title="Tweaks">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path
            d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
      <div class="tweaks-panel" ref="tweaksPanelRef">
        <div class="tweaks-title">Tweaks</div>
        <div class="tweaks-row">
          <label class="tweaks-label">主题变体</label>
          <div class="tweaks-segments">
            <button class="tweaks-seg active" @click="(e) => setTheme('A', e)">A · 终端</button>
            <button class="tweaks-seg" @click="(e) => setTheme('C', e)">C · 极光</button>
          </div>
        </div>
        <div class="tweaks-row">
          <label class="tweaks-label">主色</label>
          <input type="color" class="tweaks-color-input" ref="colorInputRef" value="#60C796" @input="setPrimary($event.target.value)" />
          <div class="tweaks-hex" ref="hexLabelRef">#60C796</div>
        </div>
      </div>
    </div>
    <div class="tweaks" ref="tweaksRef">
      <button class="tweaks-toggle" @click="toggleTweaks()" title="Tweaks">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="3" />
          <path
            d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </button>
    </div> -->
  </div>
</template>
<script lang="ts" setup name="login-mini">
import { ref, onMounted, reactive, toRaw } from 'vue';

import { getCodeInfo } from '/@/api/sys/user';

import codeImg from '/@/assets/images/checkcode.png';
//账号登录表单字段
const formData = reactive<any>({
  inputCode: '',
  username: '',
  password: '',
  loginOrgCode: '',
});

const randCodeData = reactive<any>({
  randCodeImage: '',
  requestCodeSuccess: false,
  checkKey: null,
});

/**
 * 获取验证码
 */
function handleChangeCheckCode() {
  formData.inputCode = '';
  // 代码逻辑说明: [QQYUN-10775]验证码可以复用 #7674------------
  randCodeData.checkKey = new Date().getTime() + Math.random().toString(36).slice(-4); // 1629428467008;
  getCodeInfo(randCodeData.checkKey).then((res) => {
    randCodeData.randCodeImage = res;
    randCodeData.requestCodeSuccess = true;
  });
}


const canvasRef = ref<HTMLCanvasElement | null>(null);

let ctx: CanvasRenderingContext2D | null = null;
let W = 0, H = 0;
const mouse = { x: -9999, y: -9999, active: false };

onMounted(() => {
  if (canvasRef.value) {
    ctx = canvasRef.value.getContext('2d');
    if (ctx) {
      setupParticles();
    }
  }

  handleChangeCheckCode();
});

const NODE_COUNT = 60;
const LINK_DIST = 110;
const MOUSE_RADIUS = 140;
const nodes = [];
const pulses = [];

const CYCLE_MS = 75000;
const PHASE = {
  GATHER: 0.40,
  HOLD: 0.50,
  SCATTER: 0.85,
};

function resize() {
  if (!canvasRef.value || !ctx) return;
  const rect = canvasRef.value.parentElement.getBoundingClientRect();
  W = rect.width;
  H = rect.height;
  const dpr = window.devicePixelRatio || 1;
  canvasRef.value.width = W * dpr;
  canvasRef.value.height = H * dpr;
  canvasRef.value.style.width = W + 'px';
  canvasRef.value.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function initNodes() {
  nodes.length = 0;
  const cx = W / 2, cy = H / 2;
  nodes.push({
    x: cx, y: cy,
    homeX: cx, homeY: cy,
    r: 4,
    pulse: 0,
    pulseSpeed: 0.04,
    isCore: true,
  });
  for (let i = 1; i < NODE_COUNT; i++) {
    const angle = (i / (NODE_COUNT - 1)) * Math.PI * 2 + Math.random() * 0.8;
    const dist = 80 + Math.random() * Math.min(W, H) * 0.55;
    const homeX = cx + Math.cos(angle) * dist;
    const homeY = cy + Math.sin(angle) * dist;

    const sizeRoll = Math.random();
    let radius;
    if (sizeRoll > 0.92) {
      radius = 3.2 + Math.random() * 1.4;
    } else if (sizeRoll > 0.75) {
      radius = 2 + Math.random() * 1.0;
    } else {
      radius = 0.7 + Math.random() * 1.1;
    }

    nodes.push({
      x: homeX, y: homeY,
      homeX, homeY,
      gatherAngle: angle + (Math.random() - 0.5) * 1.2,
      gatherDist: 14 + Math.random() * 38,
      wobblePhase: Math.random() * Math.PI * 2,
      wobbleSpeed: 0.005 + Math.random() * 0.009,
      gatherDelay: Math.random() * 0.55,
      scatterDelay: Math.random() * 0.55,
      easeFactor: 0.006 + Math.random() * 0.018,
      r: radius,
      pulse: Math.random() * Math.PI * 2,
      pulseSpeed: 0.005 + Math.random() * 0.011,
    });
  }
}

function getPrimaryRgb() {
  const hex = getComputedStyle(document.documentElement).getPropertyValue('--primary').trim() || '#60C796';
  const m = hex.match(/\w\w/g);
  if (!m) return [96, 199, 150];
  return m.map(x => parseInt(x, 16));
}

function easeInOut(t) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

function spawnPulse() {
  if (nodes.length < 3) return;
  const fromIdx = 1 + Math.floor(Math.random() * (nodes.length - 1));
  let toIdx;
  let attempts = 0;
  let found = false;
  while (attempts < 5 && !found) {
    toIdx = Math.floor(Math.random() * nodes.length);
    if (toIdx !== fromIdx) {
      const a = nodes[fromIdx], b = nodes[toIdx];
      const d = Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
      if (d < LINK_DIST * 1.8) { found = true; }
    }
    attempts++;
  }
  if (!found) return;
  pulses.push({
    from: fromIdx,
    to: toIdx,
    progress: 0,
    speed: 0.014 + Math.random() * 0.012,
  });
}

let lastPulseSpawn = 0;
let startTime = 0;

function draw(timestamp) {
  if (W === 0 || !ctx) { requestAnimationFrame(draw); return; }
  if (!startTime) startTime = timestamp;
  const elapsed = (timestamp - startTime) % CYCLE_MS;
  const t = elapsed / CYCLE_MS;

  let cohesion;
  if (t < PHASE.GATHER) {
    cohesion = easeInOut(t / PHASE.GATHER);
  } else if (t < PHASE.HOLD) {
    cohesion = 1;
  } else if (t < PHASE.SCATTER) {
    cohesion = 1 - easeInOut((t - PHASE.HOLD) / (PHASE.SCATTER - PHASE.HOLD));
  } else {
    cohesion = 0;
  }

  const [pr, pg, pb] = getPrimaryRgb();
  ctx.clearRect(0, 0, W, H);

  if (timestamp - lastPulseSpawn > 1200 && cohesion > 0.3) {
    spawnPulse();
    lastPulseSpawn = timestamp;
  }

  const cx = W / 2, cy = H / 2;
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    n.pulse += n.pulseSpeed;

    if (n.isCore) continue;

    n.wobblePhase += n.wobbleSpeed;

    let nodeCohesion;
    if (t < PHASE.GATHER) {
      const localT = (t - n.gatherDelay * PHASE.GATHER) / (PHASE.GATHER * (1 - n.gatherDelay));
      nodeCohesion = localT <= 0 ? 0 : easeInOut(Math.min(1, localT));
    } else if (t < PHASE.HOLD) {
      nodeCohesion = 1;
    } else if (t < PHASE.SCATTER) {
      const scatterRange = PHASE.SCATTER - PHASE.HOLD;
      const localT = (t - PHASE.HOLD - n.scatterDelay * scatterRange) / (scatterRange * (1 - n.scatterDelay));
      nodeCohesion = localT <= 0 ? 1 : 1 - easeInOut(Math.min(1, localT));
    } else {
      nodeCohesion = 0;
    }

    const gatherX = cx + Math.cos(n.gatherAngle) * n.gatherDist;
    const gatherY = cy + Math.sin(n.gatherAngle) * n.gatherDist;

    const wobX = Math.sin(n.wobblePhase) * 4;
    const wobY = Math.cos(n.wobblePhase * 0.8) * 4;
    const homeX = n.homeX + wobX;
    const homeY = n.homeY + wobY;

    const targetX = homeX + (gatherX - homeX) * nodeCohesion;
    const targetY = homeY + (gatherY - homeY) * nodeCohesion;

    n.x += (targetX - n.x) * n.easeFactor;
    n.y += (targetY - n.y) * n.easeFactor;

    if (mouse.active) {
      const dx = n.x - mouse.x;
      const dy = n.y - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < MOUSE_RADIUS && dist > 0) {
        const force = (MOUSE_RADIUS - dist) / MOUSE_RADIUS * 0.4;
        n.x += (dx / dist) * force;
        n.y += (dy / dist) * force;
      }
    }
  }

  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < LINK_DIST) {
        const opacity = (1 - dist / LINK_DIST) * 0.4;
        ctx.strokeStyle = `rgba(${pr},${pg},${pb},${opacity})`;
        ctx.lineWidth = 0.4;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    if (a.isCore && cohesion > 0.4) {
      const broadcastOpacity = (cohesion - 0.4) / 0.6;
      for (let k = 1; k < nodes.length; k++) {
        const b = nodes[k];
        const d = Math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2);
        if (d < 200) {
          ctx.strokeStyle = `rgba(${pr},${pg},${pb},${(1 - d / 200) * 0.3 * broadcastOpacity})`;
          ctx.lineWidth = 0.3;
          ctx.beginPath();
          ctx.moveTo(a.x, a.y);
          ctx.lineTo(b.x, b.y);
          ctx.stroke();
        }
      }
    }

    if (mouse.active) {
      const dx = a.x - mouse.x;
      const dy = a.y - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < MOUSE_RADIUS) {
        const opacity = (1 - dist / MOUSE_RADIUS);
        ctx.strokeStyle = `rgba(${pr},${pg},${pb},${opacity})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(mouse.x, mouse.y);
        ctx.stroke();
      }
    }
  }

  for (let p = pulses.length - 1; p >= 0; p--) {
    const pulse = pulses[p];
    pulse.progress += pulse.speed;
    if (pulse.progress >= 1) {
      pulses.splice(p, 1);
      continue;
    }
    const a = nodes[pulse.from];
    const b = nodes[pulse.to];
    if (!a || !b) continue;
    const tt = pulse.progress;
    const x = a.x + (b.x - a.x) * tt;
    const y = a.y + (b.y - a.y) * tt;
    const trailX = a.x + (b.x - a.x) * Math.max(0, tt - 0.25);
    const trailY = a.y + (b.y - a.y) * Math.max(0, tt - 0.25);

    const grad = ctx.createLinearGradient(trailX, trailY, x, y);
    grad.addColorStop(0, `rgba(${pr},${pg},${pb},0)`);
    grad.addColorStop(1, `rgba(${pr},${pg},${pb},1)`);
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.3;
    ctx.beginPath();
    ctx.moveTo(trailX, trailY);
    ctx.lineTo(x, y);
    ctx.stroke();

    ctx.fillStyle = `rgba(${pr},${pg},${pb},0.45)`;
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgba(255,255,255,1)`;
    ctx.beginPath();
    ctx.arc(x, y, 1.4, 0, Math.PI * 2);
    ctx.fill();
  }

  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i];
    const pulse = (Math.sin(n.pulse) + 1) / 2;

    if (n.isCore) {
      const coreScale = 0.7 + cohesion * 0.6;
      ctx.fillStyle = `rgba(${pr},${pg},${pb},${(0.08 + pulse * 0.1) * (0.5 + cohesion * 0.5)})`;
      ctx.beginPath();
      ctx.arc(n.x, n.y, (32 + pulse * 8) * coreScale, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = `rgba(${pr},${pg},${pb},${(0.25 + pulse * 0.25) * (0.5 + cohesion * 0.5)})`;
      ctx.beginPath();
      ctx.arc(n.x, n.y, (12 + pulse * 3) * coreScale, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = `rgba(255,255,255,${0.7 + cohesion * 0.3})`;
      ctx.beginPath();
      ctx.arc(n.x, n.y, 3.5 + cohesion * 1.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = `rgba(${pr},${pg},${pb},${0.5 + cohesion * 0.4})`;
      ctx.lineWidth = 0.8;
      ctx.beginPath();
      ctx.arc(n.x, n.y, (7 + pulse * 2) * coreScale, 0, Math.PI * 2);
      ctx.stroke();
      continue;
    }

    const radius = n.r + pulse * 0.6;
    ctx.fillStyle = `rgba(${pr},${pg},${pb},${0.1 + pulse * 0.12})`;
    ctx.beginPath();
    ctx.arc(n.x, n.y, radius * 2.8, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgba(${pr},${pg},${pb},${0.65 + pulse * 0.25})`;
    ctx.beginPath();
    ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  requestAnimationFrame(draw);
}

function setupParticles() {
  // 手机端左侧粒子面板不展示，也不应继续占用 GPU/CPU。
  if (window.matchMedia('(max-width: 1024px), (prefers-reduced-motion: reduce)').matches) return;
  resize();
  initNodes();
  requestAnimationFrame(draw);
}

onMounted(() => {
  window.addEventListener('resize', () => {
    resize();
    initNodes();
  });

  if (canvasRef.value) {
    canvasRef.value.addEventListener('mousemove', (e) => {
      const rect = canvasRef.value!.getBoundingClientRect();
      mouse.x = e.clientX - rect.left;
      mouse.y = e.clientY - rect.top;
      mouse.active = true;
    });

    canvasRef.value.addEventListener('mouseleave', () => { mouse.active = false; });
  }
});

function handleLogin(e: Event) {
  e.preventDefault();
  if (!loginLoading.value) accountLogin();
}
import { useMessage } from '/@/hooks/web/useMessage';
import { useI18n } from '/@/hooks/web/useI18n';
import { encryptAESCBC } from '/@/utils/cipher';
import { useUserStore } from '/@/store/modules/user';
import { createLocalStorage } from '/@/utils/cache';


const { notification, createMessage } = useMessage();
const { t } = useI18n();

const loginLoading = ref<boolean>(false);
// 记住用户名
const rememberMe = ref<boolean>(false);

const REMEMBER_USERNAME_KEY = 'LOGIN_REMEMBER_USERNAME';


const userStore = useUserStore();

const $ls = createLocalStorage();

async function accountLogin() {
  if (!formData.username) {
    createMessage.warn(t('sys.login.accountPlaceholder'));
    return;
  }
  if (!formData.password) {
    createMessage.warn(t('sys.login.passwordPlaceholder'));
    return;
  }
  try {
    loginLoading.value = true;

    // 密码使用AES加密传输
    const encryptedPassword = encryptAESCBC(formData.password);
    const { userInfo } = await userStore.login(
      toRaw({
        password: encryptedPassword,
        username: formData.username,
        loginOrgCode: formData.loginOrgCode,
        captcha: formData.inputCode,
        checkKey: randCodeData.checkKey,
        mode: 'none', //不要默认的错误提示
      })
    );
    if (userInfo) {
      notification.success({
        message: t('sys.login.loginSuccessTitle'),
        description: `${t('sys.login.loginSuccessDesc')}: ${userInfo.realname}`,
        duration: 3,
      });
      // 登录成功后处理记住用户名
      if (rememberMe.value && formData.username) {
        $ls.set(REMEMBER_USERNAME_KEY, formData.username)
      } else {
        $ls.remove(REMEMBER_USERNAME_KEY)
      }
    }
  } catch (error:any) {
    notification.error({
      message: t('sys.api.errorTip'),
      description: error.message || t('sys.login.networkExceptionMsg'),
      duration: 3,
    });
    handleChangeCheckCode();
  } finally {
    loginLoading.value = false;
  }
}

onMounted(() => {
  // 恢复已记住的用户名
  const saved = $ls.get(REMEMBER_USERNAME_KEY);
  if (saved) {
    formData.username = saved;
    rememberMe.value = true;
  }
});
</script>

<style lang="less" scoped>
@import '/@/assets/loginmini/style/home.less';
@import '/@/assets/loginmini/style/base.less';


/* Define variables at component level */
.body {
  --primary: #60C796;
  --primary-dim: #4ea87d;
  --primary-glow: rgba(96, 199, 150, 0.35);
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html,
.body {
  height: 100%;
}

.body {
  overflow: hidden;
  transition: background 0.5s ease, color 0.5s ease;
}

.body[data-theme="A"] {
  background: #F5F9FF;
  color: #17203B;
  font-family: 'Inter', 'Segoe UI', sans-serif;
  --bg: #F5F9FF;
  --panel: rgba(255, 255, 255, 0.96);
  --text: #17203B;
  --text-dim: #5B6B8A;
  --text-mute: #7A8AA8;
  --border: rgba(49, 115, 255, 0.12);
  --border-hover: rgba(49, 115, 255, 0.24);
  --accent: #3173FF;
  --accent-soft: rgba(49, 115, 255, 0.14);
  --primary-glow: rgba(49, 115, 255, 0.18);
}

.body[data-theme="C"] {
  background: #0B1220;
  color: #E8EAFF;
  font-family: 'Geist', -apple-system, sans-serif;
  --bg: #0B1220;
  --panel: rgba(255, 255, 255, 0.04);
  --text: #E8EAFF;
  --text-dim: rgba(232, 234, 255, 0.55);
  --text-mute: rgba(232, 234, 255, 0.32);
  --border: rgba(255, 255, 255, 0.08);
  --border-hover: rgba(96, 199, 150, 0.4);
  --accent: #A78BFA;
}

.stage {
  position: relative;
  width: 100%;
  height: 100vh;
  display: grid;
  grid-template-columns: 1fr 1fr;
  overflow: hidden;
}

.body[data-theme="C"] .stage {
  display: flex;
  align-items: center;
  justify-content: center;
}

.left-panel {
  position: relative;
  overflow: hidden;
  border-right: 1px solid rgba(49, 115, 255, 0.08);
  transition: opacity 0.4s, background 0.4s;
  background: linear-gradient(145deg, rgba(245, 249, 255, 0.98), rgba(232, 240, 255, 0.98));
  box-shadow: inset -1px 0 0 rgba(49, 115, 255, 0.04);
}

.body[data-theme="C"] .left-panel {
  display: none;
}

.particles-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  z-index: 1;
  opacity: 0.8;
}

.orbit-rings {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 680px;
  height: 680px;
  z-index: 0;
  pointer-events: none;
  opacity: 0.45;
}

.orbit-ring {
  position: absolute;
  inset: 0;
  border: 1px solid rgba(49, 115, 255, 0.18);
  border-radius: 50%;
  opacity: 0.2;
}

.orbit-ring:nth-child(1) {
  animation: ringPulse 4s ease-in-out infinite;
}

.orbit-ring:nth-child(2) {
  inset: 80px;
  animation: ringPulse 4s ease-in-out 0.6s infinite;
  opacity: 0.18;
}

.orbit-ring:nth-child(3) {
  inset: 160px;
  animation: ringPulse 4s ease-in-out 1.2s infinite;
  opacity: 0.25;
}

.orbit-ring:nth-child(4) {
  inset: 240px;
  animation: ringPulse 4s ease-in-out 1.8s infinite;
  opacity: 0.32;
}

@keyframes ringPulse {

  0%,
  100% {
    transform: scale(1);
    opacity: 0.1;
  }

  50% {
    transform: scale(1.04);
    opacity: 0.4;
  }
}

.core-glow {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 340px;
  height: 340px;
  border-radius: 50%;
  background: radial-gradient(circle, var(--primary-glow) 0%, transparent 65%);
  z-index: 0;
  pointer-events: none;
  animation: coreBreath 4s ease-in-out infinite;
  opacity: 0.65;
}

@keyframes coreBreath {

  0%,
  100% {
    opacity: 0.5;
    transform: translate(-50%, -50%) scale(1);
  }

  50% {
    opacity: 0.75;
    transform: translate(-50%, -50%) scale(1.1);
  }
}

.grid-bg {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(96, 199, 150, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(96, 199, 150, 0.04) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse at center, black 20%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse at center, black 20%, transparent 75%);
  z-index: 0;
  pointer-events: none;
}

.scan-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 25%, rgba(0, 0, 0, 0.5) 100%);
  z-index: 2;
}

.data-stream {
  position: absolute;
  z-index: 3;
  pointer-events: none;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: var(--primary);
  opacity: 0.5;
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.data-stream.left {
  top: 18%;
  left: 32px;
  transform: rotate(-90deg);
  transform-origin: left top;
}

.data-stream.right {
  bottom: 18%;
  right: 32px;
  transform: rotate(90deg);
  transform-origin: right bottom;
}

.data-stream-bar {
  display: inline-block;
  width: 6px;
  margin: 0 2px;
  background: var(--primary);
  vertical-align: middle;
  animation: barPulse 1.5s ease-in-out infinite;
}

@keyframes barPulse {

  0%,
  100% {
    height: 4px;
    opacity: 0.4;
  }

  50% {
    height: 14px;
    opacity: 1;
  }
}

.floating-badge {
  position: absolute;
  z-index: 4;
  padding: 6px 10px;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(8px);
  border: 1px solid var(--primary);
  color: var(--primary);
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.1em;
  pointer-events: none;
  animation: badgeFloat 4s ease-in-out infinite;
}

.floating-badge::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--primary);
  margin-right: 6px;
  vertical-align: middle;
  box-shadow: 0 0 8px var(--primary);
  animation: dotBlink 1.2s ease-in-out infinite;
}

@keyframes dotBlink {

  0%,
  100% {
    opacity: 0.4;
  }

  50% {
    opacity: 1;
  }
}

@keyframes badgeFloat {

  0%,
  100% {
    transform: translateY(0);
  }

  50% {
    transform: translateY(-6px);
  }
}

.badge-1 {
  top: 14%;
  right: 48px;
}

.badge-2 {
  top: 42%;
  left: 48px;
  animation-delay: 1s;
}

.badge-3 {
  bottom: 36%;
  right: 64px;
  animation-delay: 2s;
}

.hero-text {
  position: absolute;
  bottom: 64px;
  left: 64px;
  z-index: 5;
  pointer-events: none;
}

.hero-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 1px solid rgba(49, 115, 255, 0.18);
  color: var(--accent);
  font-size: 11px;
  letter-spacing: 0.18em;
  margin-bottom: 18px;
  text-transform: uppercase;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(8px);
  border-radius: 999px;
}

.hero-tag::before {
  content: '';
  display: inline-block;
  width: 12px;
  height: 12px;
  background: var(--accent);
  clip-path: polygon(50% 0, 100% 100%, 0 100%);
  animation: tagSpin 3s linear infinite;
}

@keyframes tagSpin {
  to {
    transform: rotate(360deg);
  }
}

.hero-title {
  font-size: 40px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.1;
  letter-spacing: -0.02em;
  margin-bottom: 12px;
  text-shadow: 0 12px 40px rgba(49, 115, 255, 0.08);
}

.hero-title em {
  font-style: normal;
  color: var(--accent);
  text-shadow: 0 0 16px rgba(49, 115, 255, 0.16);
  position: relative;
  margin-left: 0.18em;
  margin-right: 0.05em;
}

.hero-sub {
  font-size: 14px;
  color: var(--text-dim);
  letter-spacing: 0.02em;
  max-width: 360px;
  line-height: 1.7;
}

.body[data-theme="A"] .hero-title {
  font-family: 'Inter', sans-serif;
  text-transform: none;
  font-size: 40px;
  letter-spacing: -0.02em;
}

.corner-tl,
.corner-tr,
.corner-bl,
.corner-br {
  position: absolute;
  width: 12px;
  height: 12px;
  border: 1px solid var(--primary);
  z-index: 5;
}

.corner-tl {
  top: 24px;
  left: 24px;
  border-right: none;
  border-bottom: none;
}

.corner-tr {
  top: 24px;
  right: 24px;
  border-left: none;
  border-bottom: none;
}

.corner-bl {
  bottom: 24px;
  left: 24px;
  border-right: none;
  border-top: none;
}

.corner-br {
  bottom: 24px;
  right: 24px;
  border-left: none;
  border-top: none;
}

.meta-text {
  position: absolute;
  top: 24px;
  left: 64px;
  font-size: 10px;
  color: var(--text-mute);
  letter-spacing: 0.15em;
  z-index: 5;
}

.meta-text span {
  color: var(--primary);
  margin-right: 12px;
}

.right-panel {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  padding: 48px 48px 48px 88px;
  z-index: 1;
}

.body[data-theme="C"] .right-panel {
  width: 100%;
  justify-content: center;
  padding: 48px;
}

.aurora {
  display: none;
  position: absolute;
  inset: 0;
  overflow: hidden;
  z-index: 0;
}

.body[data-theme="C"] .aurora {
  display: block;
}

.body[data-theme="C"] .stage::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 800px 600px at 15% 25%, rgba(96, 199, 150, 0.18), transparent 60%),
    radial-gradient(ellipse 700px 500px at 85% 75%, rgba(167, 139, 250, 0.16), transparent 60%),
    radial-gradient(ellipse 500px 400px at 50% 100%, rgba(96, 199, 150, 0.1), transparent 60%);
  pointer-events: none;
  animation: auroraShift 20s ease-in-out infinite;
}

@keyframes auroraShift {

  0%,
  100% {
    transform: translate(0, 0) scale(1);
  }

  50% {
    transform: translate(-30px, 20px) scale(1.05);
  }
}

.body[data-theme="C"] .stage::after {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
  -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 75%);
  pointer-events: none;
}

.login-card {
  width: 100%;
  max-width: 420px;
  position: relative;
  z-index: 2;
  animation: cardIn 0.7s cubic-bezier(0.2, 0.9, 0.3, 1.05) both;
}

@keyframes cardIn {
  from {
    opacity: 0;
    transform: translateY(16px);
  }

  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.body[data-theme="A"] .login-card {
  padding: 44px 40px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 24px;
  box-shadow: 0 24px 60px rgba(49, 93, 172, 0.1);
}

.body[data-theme="C"] .login-card {
  padding: 44px 40px;
  background: var(--panel);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border: 1px solid var(--border);
  border-radius: 18px;
  box-shadow:
    0 30px 60px -20px rgba(0, 0, 0, 0.6),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.body[data-theme="C"] .login-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--primary), var(--accent), transparent);
  opacity: 0.6;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 32px;
}

.brand-copy {
  display: flex;
  align-items: baseline;
}

.brand-context {
  display: none;
}

.body[data-theme="A"] .mark-c,
.body[data-theme="C"] .mark-a {
  display: none;
}

.body[data-theme="A"] .brand-mark {
  width: 40px;
  height: 40px;
  border-radius: 14px;
  background: linear-gradient(135deg, var(--accent), #6b8aff);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  box-shadow: 0 14px 28px rgba(49, 115, 255, 0.18);
}

.body[data-theme="C"] .brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--primary), var(--primary-dim));
  display: flex;
  align-items: center;
  justify-content: center;
  color: #0B1220;
  box-shadow: 0 6px 18px -4px var(--primary-glow);
}

.body[data-theme="C"] .brand-mark svg {
  width: 18px;
  height: 18px;
}

.brand-name {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.body[data-theme="A"] .brand-name {
  color: var(--text);
  text-transform: none;
}

.body[data-theme="A"] .brand-name span {
  color: var(--accent);
}

.body[data-theme="C"] .brand-name {
  color: var(--text);
  letter-spacing: 0;
  font-size: 14px;
}

.title-block {
  margin-bottom: 32px;
}

.title {
  font-size: 28px;
  font-weight: 500;
  letter-spacing: -0.01em;
  margin-bottom: 8px;
}

.body[data-theme="A"] .title {
  font-weight: 700;
  text-transform: none;
  letter-spacing: -0.02em;
}

.body[data-theme="A"] .title::before {
  content: none;
}

.subtitle {
  font-size: 13px;
  color: var(--text-dim);
}

.body[data-theme="A"] .subtitle::before {
  content: none;
}

.field {
  position: relative;
  margin-bottom: 14px;
}

.field-label {
  display: block;
  font-size: 11px;
  color: var(--text-dim);
  margin-bottom: 6px;
  letter-spacing: 0.08em;
}

.body[data-theme="A"] .field-label {
  text-transform: uppercase;
}

.body[data-theme="A"] .field-label::before {
  content: ' ';
  color: var(--primary);
}

.body[data-theme="C"] .field-label {
  font-size: 12px;
  letter-spacing: 0;
  text-transform: none;
}

.input-wrap {
  position: relative;
}

.field-input {
  width: 100%;
  height: 46px;
  padding: 0 16px;
  color: var(--text);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: all 0.2s ease;
}

.body[data-theme="A"] .field-input {
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding-left: 42px;
  color: var(--text);
  box-shadow: inset 0 1px 2px rgba(18, 41, 72, 0.05);
}

.body[data-theme="A"] .field-input:hover {
  border-color: rgba(49, 115, 255, 0.24);
}

.body[data-theme="A"] .field-input:focus {
  border-color: var(--accent);
  box-shadow: none;
  background: #fff;
}

.body[data-theme="A"] .input-wrap::before {
  content: none;
}

.body[data-theme="C"] .field-input {
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding-left: 42px;
}

.body[data-theme="C"] .field-input:hover {
  border-color: rgba(255, 255, 255, 0.18);
}

.body[data-theme="C"] .field-input:focus {
  border-color: var(--border-hover);
  background: rgba(255, 255, 255, 0.06);
  box-shadow: none;
}

.body[data-theme="A"] .field-icon {
  display: block;
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  color: var(--text-mute);
  transition: color 0.2s;
  pointer-events: none;
}

.body[data-theme="A"] .field-input:focus + .field-icon {
  color: var(--accent);
}

.body[data-theme="C"] .field-icon {
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  width: 18px;
  height: 18px;
  color: var(--text-mute);
  transition: color 0.2s;
  pointer-events: none;
}

.body[data-theme="C"] .field-input:focus+.field-icon {
  color: var(--primary);
}

.body[data-theme="A"] .field-icon {
  display: none;
}

.captcha-row {
  display: grid;
  grid-template-columns: 1fr 120px;
  gap: 10px;
  align-items: end;
}

.captcha-box {
  height: 46px;
  padding: 0;
  appearance: none;
  cursor: pointer;
  overflow: hidden;
  transition: all 0.2s;
  position: relative;

  .imgs {
    width: 100%;
    height: 100%;
    display: block;
  }
}

.body[data-theme="A"] .captcha-box {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: #fff;
  box-shadow: inset 0 1px 2px rgba(49, 115, 255, 0.08);
}

.body[data-theme="A"] .captcha-box:hover {
  border-color: rgba(49, 115, 255, 0.4);
  transform: translateY(-1px);
}

.body[data-theme="C"] .captcha-box {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(96, 199, 150, 0.08), rgba(167, 139, 250, 0.08));
}

.body[data-theme="C"] .captcha-box:hover {
  border-color: var(--border-hover);
  transform: translateY(-1px);
}

.captcha-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.options-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 22px 0 28px;
}

.checkbox {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
}

.checkbox input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.check-mark {
  width: 16px;
  height: 16px;
  border: 1px solid var(--border-hover);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  position: relative;
}

.body[data-theme="A"] .check-mark {
  border-radius: 0;
}

.body[data-theme="C"] .check-mark {
  border-radius: 4px;
}

.check-mark svg {
  width: 10px;
  height: 10px;
  opacity: 0;
  transform: scale(0.5);
  transition: all 0.18s;
}

.body[data-theme="A"] .check-mark svg {
  color: #000;
}

.body[data-theme="C"] .check-mark svg {
  color: #0B1220;
}

.checkbox input:checked+.check-mark {
  background: var(--primary);
  border-color: var(--primary);
}

.checkbox input:checked+.check-mark svg {
  opacity: 1;
  transform: scale(1);
}

.checkbox input:focus-visible+.check-mark,
.captcha-box:focus-visible,
.login-btn:focus-visible {
  outline: 3px solid rgba(49, 115, 255, 0.24);
  outline-offset: 3px;
}

.checkbox:hover {
  color: var(--text);
}

.forgot {
  font-size: 12px;
  color: var(--text-dim);
  text-decoration: none;
  position: relative;
  transition: color 0.2s;
}

.body[data-theme="A"] .forgot::before {
  content: '? ';
  color: var(--text-mute);
}

.forgot:hover {
  color: var(--primary);
}

.forgot::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: -2px;
  width: 0;
  height: 1px;
  background: var(--primary);
  transition: width 0.25s;
}

.forgot:hover::after {
  width: 100%;
}

.login-btn {
  width: 100%;
  height: 48px;
  border: none;
  color: #0A0A0A;
  font-size: 14px;
  font-weight: 500;
  font-family: inherit;
  letter-spacing: 0.02em;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  transition: all 0.2s ease;
}

.body[data-theme="A"] .login-btn {
  background: linear-gradient(135deg, var(--accent), #6b8aff);
  border-radius: 14px;
  color: #fff;
  font-weight: 700;
  letter-spacing: 0.08em;
  box-shadow: 0 14px 28px rgba(49, 115, 255, 0.18);
}

.body[data-theme="A"] .login-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 18px 32px rgba(49, 115, 255, 0.22);
}

.body[data-theme="A"] .login-btn:active {
  transform: translateY(0);
}

.body[data-theme="C"] .login-btn {
  background: linear-gradient(135deg, var(--primary), var(--primary-dim));
  border-radius: 10px;
  box-shadow: 0 8px 24px -8px var(--primary-glow), inset 0 1px 0 rgba(255, 255, 255, 0.25);
}

.body[data-theme="C"] .login-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 32px -8px var(--primary-glow), inset 0 1px 0 rgba(255, 255, 255, 0.3);
}

.body[data-theme="C"] .login-btn:active {
  transform: translateY(0);
}

.login-btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
  transition: left 0.6s;
}

.login-btn:hover::before {
  left: 100%;
}

.login-btn-content {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.login-btn-content svg {
  width: 14px;
  height: 14px;
  transition: transform 0.2s;
}

.login-btn:hover .login-btn-content svg {
  transform: translateX(3px);
}

.footer {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: var(--text-mute);
}

.footer a {
  color: var(--primary);
  text-decoration: none;
}

.body[data-theme="A"] .footer {
  letter-spacing: 0.05em;
}

.tweaks {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 1000;
}

.tweaks-toggle {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: var(--primary);
  color: #0A0A0A;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 24px -6px var(--primary-glow);
  transition: transform 0.2s;
}

.tweaks-toggle:hover {
  transform: rotate(45deg);
}

.tweaks-panel {
  position: absolute;
  bottom: 52px;
  right: 0;
  width: 240px;
  padding: 16px;
  background: rgba(20, 20, 20, 0.95);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  color: #fff;
  font-family: 'Geist', sans-serif;
  font-size: 13px;
  display: none;
  box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.5);
}

.tweaks-panel.open {
  display: block;
}

.tweaks-title {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 12px;
}

.tweaks-row {
  margin-bottom: 10px;
}

.tweaks-label {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.7);
  margin-bottom: 6px;
  display: block;
}

.tweaks-segments {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px;
  background: rgba(0, 0, 0, 0.4);
  padding: 3px;
  border-radius: 8px;
}

.tweaks-seg {
  padding: 7px 10px;
  border: none;
  background: transparent;
  color: rgba(255, 255, 255, 0.6);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  border-radius: 6px;
  transition: all 0.15s;
}

.tweaks-seg.active {
  background: var(--primary);
  color: #0A0A0A;
  font-weight: 500;
}

.tweaks-color-input {
  width: 100%;
  height: 32px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: transparent;
  border-radius: 6px;
  cursor: pointer;
}

.tweaks-hex {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.5);
  margin-top: 4px;
}

@media (max-width: 1024px) {
  .body {
    min-height: 100%;
    overflow-x: hidden;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .particles-canvas {
    display: none;
  }

  .login-card,
  .captcha-row,
  .captcha-row .input-wrap {
    min-width: 0;
  }

  .field {
    scroll-margin-top: 16px;
  }

  .field-input,
  .captcha-box,
  .login-btn {
    height: 50px;
    min-height: 50px;
  }

  /* A 主题不显示输入框内图标，触控端释放预留空间给文字。 */
  .body[data-theme="A"] .field-input {
    padding-right: 16px;
    padding-left: 16px;
  }

  .captcha-box,
  .login-btn,
  .checkbox {
    touch-action: manipulation;
  }

  .checkbox {
    min-height: 44px;
  }

  .check-mark {
    width: 18px;
    height: 18px;
    flex: 0 0 auto;
  }
}

/* iPad：用一张双栏面板承接桌面端的 AI 视觉，但停掉粒子动画。 */
@media (min-width: 720px) and (max-width: 1024px) {
  .body {
    display: flex;
    padding:
      max(24px, env(safe-area-inset-top))
      24px
      max(24px, env(safe-area-inset-bottom));
    background:
      radial-gradient(circle at 82% 16%, rgba(49, 115, 255, 0.1), transparent 34%),
      #f3f7fd;
  }

  .stage,
  .body[data-theme="C"] .stage {
    display: grid;
    grid-template-columns: minmax(230px, 0.72fr) minmax(420px, 1.28fr);
    width: 100%;
    max-width: 820px;
    height: min(720px, calc(100dvh - 48px));
    min-height: 640px;
    margin: auto;
    overflow: hidden;
    border: 1px solid rgba(49, 115, 255, 0.12);
    border-radius: 30px;
    background: rgba(255, 255, 255, 0.98);
    box-shadow: 0 34px 80px rgba(51, 82, 139, 0.16);
  }

  .body[data-theme="A"] .left-panel,
  .body[data-theme="C"] .left-panel {
    display: block;
    min-width: 0;
    border-right: 1px solid rgba(49, 115, 255, 0.1);
    background:
      radial-gradient(circle at 52% 38%, rgba(255, 255, 255, 0.96) 0 4px, rgba(49, 115, 255, 0.18) 5px 7px, transparent 8px),
      linear-gradient(155deg, #edf4ff 0%, #dce8fb 100%);
    box-shadow: none;
  }

  .right-panel,
  .body[data-theme="C"] .right-panel {
    width: 100%;
    min-width: 0;
    min-height: 0;
    align-items: center;
    justify-content: center;
    padding: 28px 30px;
    background: rgba(255, 255, 255, 0.98);
  }

  .login-card {
    width: 100%;
    max-width: 400px;
  }

  .body[data-theme="A"] .login-card,
  .body[data-theme="C"] .login-card {
    padding: 24px 0;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .meta-text,
  .data-stream,
  .floating-badge {
    display: none;
  }

  .grid-bg {
    background-image:
      linear-gradient(rgba(49, 115, 255, 0.075) 1px, transparent 1px),
      linear-gradient(90deg, rgba(49, 115, 255, 0.075) 1px, transparent 1px);
    background-size: 32px 32px;
    mask-image: linear-gradient(to bottom, transparent, #000 18%, #000 82%, transparent);
    -webkit-mask-image: linear-gradient(to bottom, transparent, #000 18%, #000 82%, transparent);
  }

  .orbit-rings {
    top: 36%;
    width: 430px;
    height: 430px;
    opacity: 0.55;
  }

  .orbit-ring {
    border-color: rgba(49, 115, 255, 0.24);
  }

  .core-glow {
    top: 36%;
    width: 240px;
    height: 240px;
    opacity: 0.72;
  }

  .scan-overlay {
    background: linear-gradient(180deg, rgba(235, 243, 255, 0.08), rgba(47, 87, 153, 0.08));
  }

  .hero-text {
    right: 28px;
    bottom: 40px;
    left: 30px;
  }

  .hero-tag {
    padding: 5px 9px;
    font-size: 9px;
    letter-spacing: 0.13em;
  }

  .hero-title,
  .body[data-theme="A"] .hero-title {
    font-size: 31px;
    line-height: 1.08;
  }

  .hero-sub {
    font-size: 12px;
    line-height: 1.65;
  }

  .brand {
    margin-bottom: 22px;
  }

  .title-block {
    margin-bottom: 24px;
  }

  .field {
    margin-bottom: 14px;
  }

  .field-input {
    font-size: 15px;
  }

  .captcha-row {
    grid-template-columns: minmax(0, 1fr) 112px;
    gap: 10px;
  }

  .options-row {
    margin: 16px 0 20px;
  }
}

/* 手机：改成通栏登录页，避免小屏上再套一层巨大白色卡片。 */
@media (max-width: 719px) {
  .body {
    position: relative;
    isolation: isolate;
    background: #f8fbff;
  }

  .body::before {
    content: '';
    position: fixed;
    z-index: 0;
    top: -280px;
    left: 50%;
    width: 620px;
    height: 620px;
    border-radius: 50%;
    background:
      radial-gradient(circle at center, rgba(255, 255, 255, 0.98) 0 3px, rgba(49, 115, 255, 0.18) 4px 6px, transparent 7px),
      repeating-radial-gradient(circle at center, rgba(49, 115, 255, 0.13) 0 1px, transparent 1px 62px),
      radial-gradient(circle at center, rgba(49, 115, 255, 0.18), rgba(49, 115, 255, 0.045) 44%, transparent 70%);
    transform: translateX(-50%);
    pointer-events: none;
  }

  .body::after {
    content: '';
    position: fixed;
    z-index: 0;
    inset: 0;
    background-image: radial-gradient(rgba(49, 115, 255, 0.12) 0.7px, transparent 0.7px);
    background-size: 22px 22px;
    mask-image: linear-gradient(to bottom, #000, transparent 48%);
    -webkit-mask-image: linear-gradient(to bottom, #000, transparent 48%);
    opacity: 0.38;
    pointer-events: none;
  }

  .stage,
  .body[data-theme="C"] .stage {
    position: relative;
    z-index: 1;
    display: block;
    width: 100%;
    height: auto;
    min-height: 100vh;
    min-height: 100dvh;
    overflow: visible;
  }

  .body[data-theme="A"] .left-panel,
  .body[data-theme="C"] .left-panel {
    display: none;
  }

  .right-panel,
  .body[data-theme="C"] .right-panel {
    width: 100%;
    min-width: 0;
    min-height: 100vh;
    min-height: 100dvh;
    align-items: center;
    justify-content: center;
    padding:
      max(24px, env(safe-area-inset-top))
      max(20px, env(safe-area-inset-right))
      max(24px, env(safe-area-inset-bottom))
      max(20px, env(safe-area-inset-left));
  }

  .login-card {
    width: 100%;
    max-width: 420px;
  }

  .body[data-theme="A"] .login-card,
  .body[data-theme="C"] .login-card {
    padding: 28px 2px 30px;
    border: 0;
    border-radius: 0;
    background: transparent;
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }

  .brand {
    gap: 12px;
    margin-bottom: 28px;
  }

  .body[data-theme="A"] .brand-mark {
    position: relative;
    width: 46px;
    height: 46px;
    border-radius: 15px;
    font-size: 13px;
    box-shadow: 0 16px 34px rgba(49, 115, 255, 0.24);
  }

  .body[data-theme="A"] .brand-mark::before {
    content: '';
    position: absolute;
    inset: -8px -12px;
    border: 1px solid rgba(49, 115, 255, 0.22);
    border-radius: 50%;
    transform: rotate(-18deg);
    pointer-events: none;
  }

  .brand-copy {
    display: grid;
    gap: 3px;
  }

  .brand-name,
  .body[data-theme="A"] .brand-name {
    font-size: 15px;
  }

  .brand-context {
    display: block;
    color: var(--text-mute);
    font-size: 11px;
    letter-spacing: 0.08em;
  }

  .title-block {
    margin-bottom: 26px;
  }

  .title {
    margin-bottom: 7px;
    font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;
    font-size: 30px;
    line-height: 1.2;
  }

  .subtitle {
    font-size: 14px;
    line-height: 1.6;
  }

  .field {
    margin-bottom: 15px;
  }

  .field-label,
  .body[data-theme="A"] .field-label {
    margin-bottom: 7px;
    font-size: 13px;
    letter-spacing: 0.02em;
    text-transform: none;
  }

  .field-label .icon {
    opacity: 0.78;
  }

  .field-input,
  .captcha-box,
  .login-btn {
    height: 52px;
    min-height: 52px;
  }

  .field-input {
    font-size: 16px;
  }

  .body[data-theme="A"] .field-input,
  .body[data-theme="A"] .captcha-box {
    border-color: rgba(49, 115, 255, 0.16);
    border-radius: 15px;
    background: rgba(255, 255, 255, 0.94);
    box-shadow: 0 8px 26px rgba(55, 84, 135, 0.055), inset 0 1px 2px rgba(18, 41, 72, 0.035);
  }

  .body[data-theme="A"] .field-input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(49, 115, 255, 0.1);
  }

  .captcha-row {
    grid-template-columns: minmax(0, 1fr) 112px;
    gap: 9px;
  }

  .options-row {
    margin: 14px 0 18px;
  }

  .checkbox {
    gap: 10px;
    font-size: 14px;
  }

  .check-mark,
  .body[data-theme="A"] .check-mark {
    width: 20px;
    height: 20px;
    border-radius: 6px;
    background: rgba(255, 255, 255, 0.82);
  }

  .login-btn {
    font-size: 15px;
  }
}

@media (max-width: 360px) {
  .right-panel,
  .body[data-theme="C"] .right-panel {
    padding-right: max(16px, env(safe-area-inset-right));
    padding-left: max(16px, env(safe-area-inset-left));
  }

  .title {
    font-size: 28px;
  }

  .brand-context {
    letter-spacing: 0.04em;
  }

  .captcha-row {
    grid-template-columns: minmax(0, 1fr) 98px;
    gap: 8px;
  }
}

@media (max-width: 719px) and (max-height: 720px) {
  .right-panel,
  .body[data-theme="C"] .right-panel {
    align-items: flex-start;
    padding-top: max(12px, env(safe-area-inset-top));
    padding-bottom: max(12px, env(safe-area-inset-bottom));
  }

  .body[data-theme="A"] .login-card,
  .body[data-theme="C"] .login-card {
    padding-top: 10px;
    padding-bottom: 10px;
  }

  .brand {
    margin-bottom: 14px;
  }

  .body[data-theme="A"] .brand-mark {
    width: 40px;
    height: 40px;
    border-radius: 13px;
  }

  .brand-context {
    display: none;
  }

  .title-block {
    margin-bottom: 14px;
  }

  .title {
    margin-bottom: 4px;
    font-size: 26px;
  }

  .field {
    margin-bottom: 10px;
  }

  .field-input,
  .captcha-box,
  .login-btn {
    height: 48px;
    min-height: 48px;
  }

  .options-row {
    margin: 6px 0 10px;
  }
}

.login-btn:disabled {
  cursor: wait;
  opacity: 0.78;
}

.login-btn:disabled::before {
  display: none;
}

@media (prefers-reduced-motion: reduce) {
  .body,
  .login-card,
  .field-input,
  .captcha-box,
  .login-btn,
  .login-btn-content svg,
  .check-mark,
  .check-mark svg {
    animation: none;
    transition: none;
  }
}

.mr-2 {
  margin-right: 8px;
}

.flex-center {
  display: flex;
  align-items: center;
}
</style>
