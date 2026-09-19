<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-head">
        <div class="wordmark">AXIOM</div>
        <p class="tagline">校园智能体 · 查得到出处的办事助手</p>
      </div>

      <div class="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          class="tab"
          :class="{ active: mode === 'login' }"
          :aria-selected="mode === 'login'"
          @click="switchMode('login')"
        >
          登录
        </button>
        <button
          type="button"
          role="tab"
          class="tab"
          :class="{ active: mode === 'register' }"
          :aria-selected="mode === 'register'"
          @click="switchMode('register')"
        >
          注册
        </button>
      </div>

      <!-- 回车提交只交给 <form> 的隐式提交：中文输入法组字时按回车是「确认候选词」，
           浏览器不会触发隐式提交，但 keyup.enter 仍会响，之前每个 input 上的
           @keyup.enter="submit" 让验证码还没打完就登录了（并顺手消耗掉验证码）。 -->
      <form class="form" autocomplete="on" @submit.prevent="submit">
        <div class="field">
          <label for="af-username">用户名</label>
          <input
            id="af-username"
            v-model.trim="form.username"
            type="text"
            autocomplete="username"
            :placeholder="mode === 'login' ? '请输入用户名' : '字母开头，3-32 位'"
          />
        </div>

        <div v-if="mode === 'register'" class="field">
          <label for="af-realname">姓名<span class="optional">选填</span></label>
          <input
            id="af-realname"
            v-model.trim="form.realname"
            type="text"
            autocomplete="name"
            placeholder="用于在界面上显示"
          />
        </div>

        <div class="field">
          <label for="af-password">密码</label>
          <input
            id="af-password"
            v-model="form.password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :placeholder="mode === 'login' ? '请输入密码' : '至少 8 位'"
          />
        </div>

        <div v-if="mode === 'register'" class="field">
          <label for="af-confirm">确认密码</label>
          <input
            id="af-confirm"
            v-model="form.confirm"
            type="password"
            autocomplete="new-password"
            placeholder="再次输入密码"
          />
        </div>

        <div class="field">
          <label for="af-captcha">验证码</label>
          <div class="captcha-row">
            <input
              id="af-captcha"
              v-model.trim="form.captcha"
              type="text"
              maxlength="6"
              autocomplete="off"
              placeholder="请输入右侧字符"
              />
            <button
              type="button"
              class="captcha-img"
              title="刷新图形验证码"
              aria-label="刷新图形验证码"
              @click="refreshCaptcha"
            >
              <img v-if="captcha.image" :src="captcha.image" alt="验证码" />
              <span v-else class="captcha-fallback">点击获取</span>
            </button>
          </div>
        </div>

        <label v-if="mode === 'login'" class="remember">
          <input v-model="remember" type="checkbox" />
          <span>记住用户名</span>
        </label>

        <p v-if="error" class="error" role="alert">{{ error }}</p>

        <button type="submit" class="submit" :disabled="loading">
          {{ loading ? '处理中…' : mode === 'login' ? '登录' : '创建账号' }}
        </button>
      </form>

      <p class="foot">
        <template v-if="mode === 'login'">还没有账号？<button type="button" class="link" @click="switchMode('register')">创建一个</button></template>
        <template v-else>已有账号？<button type="button" class="link" @click="switchMode('login')">直接登录</button></template>
      </p>
    </div>
  </div>
</template>

<script lang="ts" setup name="login-mini">
  import { reactive, ref, onMounted, toRaw } from 'vue';
  import { useRoute } from 'vue-router';
  import { getCodeInfo, register } from '/@/api/sys/user';
  import { router } from '/@/router';
  import { PageEnum } from '/@/enums/pageEnum';
  import { resolvePostLoginPath } from '/@/router/postLoginRedirect';
  import { encryptAESCBC } from '/@/utils/cipher';
  import { useUserStore } from '/@/store/modules/user';
  import { createLocalStorage } from '/@/utils/cache';

  const REMEMBER_USERNAME_KEY = 'LOGIN_REMEMBER_USERNAME';
  const USERNAME_RE = /^[A-Za-z][A-Za-z0-9_]{2,31}$/;

  const userStore = useUserStore();
  const route = useRoute();
  const $ls = createLocalStorage();

  const mode = ref<'login' | 'register'>('login');
  const loading = ref(false);
  const error = ref('');
  const remember = ref(false);

  const form = reactive({
    username: import.meta.env.VITE_LOCAL_DEMO_USERNAME || '',
    password: import.meta.env.VITE_LOCAL_DEMO_PASSWORD || '',
    realname: '',
    confirm: '',
    captcha: '',
  });

  const captcha = reactive<{ image: string; checkKey: string | null }>({ image: '', checkKey: null });

  async function refreshCaptcha() {
    form.captcha = '';
    captcha.checkKey = `${Date.now()}${Math.random().toString(36).slice(-4)}`;
    try {
      captcha.image = await getCodeInfo(captcha.checkKey);
    } catch {
      captcha.image = '';
    }
  }

  function switchMode(next: 'login' | 'register') {
    if (mode.value === next) return;
    mode.value = next;
    error.value = '';
    form.password = '';
    form.confirm = '';
    refreshCaptcha();
  }

  /** Returns a message when the form is not ready to submit. */
  function validate(): string {
    if (!form.username) return '请输入用户名';
    if (!form.password) return '请输入密码';
    if (!form.captcha) return '请输入验证码';
    if (mode.value === 'register') {
      if (!USERNAME_RE.test(form.username)) return '用户名需为 3-32 位字母、数字或下划线，且以字母开头';
      if (form.password.length < 8) return '密码至少 8 位';
      if (form.password !== form.confirm) return '两次输入的密码不一致';
    }
    return '';
  }

  async function submit() {
    if (loading.value) return;
    const invalid = validate();
    if (invalid) {
      error.value = invalid;
      return;
    }
    error.value = '';
    loading.value = true;
    try {
      if (mode.value === 'login') {
        await doLogin();
      } else {
        await doRegister();
      }
    } catch (e: any) {
      error.value = e?.response?.data?.message || e?.message || '网络异常，请稍后重试';
      await refreshCaptcha();
    } finally {
      loading.value = false;
    }
  }

  async function doLogin() {
    const result = await userStore.login(
      toRaw({
        username: form.username,
        password: encryptAESCBC(form.password),
        captcha: form.captcha,
        checkKey: captcha.checkKey,
        loginOrgCode: '',
        mode: 'none',
        goHome: false,
      })
    );
    const userInfo = result?.userInfo;
    if (!userInfo) throw new Error('登录失败，请稍后重试');
    if (remember.value) {
      $ls.set(REMEMBER_USERNAME_KEY, form.username);
    } else {
      $ls.remove(REMEMBER_USERNAME_KEY);
    }
    await router.replace(resolvePostLoginPath(route.query.redirect, userInfo.homePath));
  }

  async function doRegister() {
    const res: any = await register({
      username: form.username,
      password: encryptAESCBC(form.password),
      realname: form.realname,
      captcha: form.captcha,
      checkKey: captcha.checkKey,
    });
    // register() is configured with isReturnNativeResponse, so unwrap the body.
    const body = res?.data ?? res;
    if (!body?.success) throw new Error(body?.message || '注册失败');
    const data = body.result;
    if (!data?.token) throw new Error('注册成功但未返回登录凭证，请改用登录');
    // 注册和登录一个口径：勾了「记住我」才记，公用电脑上别把上一个注册者的账号留给下一个人
    if (remember.value) {
      $ls.set(REMEMBER_USERNAME_KEY, form.username);
    } else {
      $ls.remove(REMEMBER_USERNAME_KEY);
    }
    await userStore.afterLoginAction(true, data);
  }

  onMounted(() => {
    // Collapse /login?redirect=/login?redirect=... in the address bar before the
    // user ever submits, so the post-login jump has a single clean target.
    const cleaned = resolvePostLoginPath(route.query.redirect, PageEnum.BASE_HOME);
    const raw = Array.isArray(route.query.redirect) ? route.query.redirect[0] : route.query.redirect;
    if (typeof raw === 'string' && raw.includes('/login') && cleaned !== raw) {
      void router.replace({ path: PageEnum.BASE_LOGIN, query: { redirect: cleaned } });
    }
    const saved = $ls.get(REMEMBER_USERNAME_KEY);
    if (saved && !form.username) {
      form.username = saved;
      remember.value = true;
    }
    refreshCaptcha();
  });
</script>

<style lang="less" scoped>
  .auth-page {
    --ink: #111827;
    --muted: #6b7280;
    --line: #e5e7eb;
    --bg: #fafafa;
    --danger: #b42318;

    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    min-height: 100dvh;
    overflow-y: auto;
    padding: 24px;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
      'Microsoft YaHei', sans-serif;
  }

  .auth-card {
    width: 100%;
    max-width: 380px;
    padding: 36px 32px 28px;
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 10px;
  }

  .auth-head {
    margin-bottom: 26px;
  }

  .wordmark {
    font-size: 22px;
    font-weight: 600;
    letter-spacing: 0.14em;
  }

  .tagline {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--muted);
  }

  .tabs {
    display: flex;
    gap: 22px;
    margin-bottom: 22px;
    border-bottom: 1px solid var(--line);
  }

  .tab {
    padding: 0 0 10px;
    font-size: 14px;
    color: var(--muted);
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    cursor: pointer;

    &.active {
      color: var(--ink);
      font-weight: 500;
      border-bottom-color: var(--ink);
    }
  }

  .field {
    margin-bottom: 16px;

    label {
      display: block;
      margin-bottom: 6px;
      font-size: 13px;
      color: var(--muted);
    }

    input {
      width: 100%;
      height: 38px;
      padding: 0 11px;
      font-size: 14px;
      color: var(--ink);
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      outline: none;
      transition: border-color 0.15s;

      &::placeholder {
        color: #b0b6c0;
      }

      &:focus {
        border-color: var(--ink);
      }
    }
  }

  .optional {
    margin-left: 6px;
    font-size: 12px;
    color: #b0b6c0;
  }

  .captcha-row {
    display: flex;
    gap: 10px;

    input {
      flex: 1;
      min-width: 0;
    }
  }

  .captcha-img {
    flex: 0 0 104px;
    height: 38px;
    padding: 0;
    overflow: hidden;
    background: #f3f4f6;
    border: 1px solid var(--line);
    border-radius: 6px;
    cursor: pointer;

    img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
  }

  .captcha-fallback {
    font-size: 12px;
    color: var(--muted);
  }

  .remember {
    display: flex;
    gap: 7px;
    align-items: center;
    margin-bottom: 18px;
    font-size: 13px;
    color: var(--muted);
    cursor: pointer;

    input {
      width: 14px;
      height: 14px;
      margin: 0;
      accent-color: var(--ink);
    }
  }

  .error {
    margin: 0 0 14px;
    font-size: 13px;
    color: var(--danger);
  }

  .submit {
    width: 100%;
    height: 40px;
    font-size: 14px;
    color: #fff;
    background: var(--ink);
    border: 0;
    border-radius: 6px;
    cursor: pointer;
    transition: opacity 0.15s;

    &:hover:not(:disabled) {
      opacity: 0.88;
    }

    &:disabled {
      cursor: default;
      opacity: 0.5;
    }
  }

  .foot {
    margin: 20px 0 0;
    font-size: 13px;
    color: var(--muted);
    text-align: center;
  }

  .link {
    padding: 0;
    font-size: 13px;
    color: var(--ink);
    text-decoration: underline;
    background: none;
    border: 0;
    cursor: pointer;
  }

  @media (max-width: 480px) {
    .auth-card {
      padding: 28px 20px 24px;
      border: 0;
      border-radius: 0;
      background: transparent;
    }

    .auth-page {
      align-items: flex-start;
      padding: 16px;
      background: #fff;
    }

    // iOS zooms the page when a focused input is under 16px.
    .field input {
      font-size: 16px;
    }
  }
</style>
