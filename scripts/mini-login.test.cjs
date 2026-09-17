const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

const root = path.resolve(__dirname, '..');
function evaluate(source, mocks) {
  const js = ts.transpileModule(source.replaceAll('import.meta.env', '({ VITE_LOCAL_DEMO_USERNAME: "demo-user", VITE_LOCAL_DEMO_PASSWORD: "demo-pass" })'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  new Function('require', 'exports', js)((name) => {
    assert.ok(name in mocks, `Unexpected dependency: ${name}`);
    return mocks[name];
  }, exports);
  return exports;
}

function loginPage({ failure, captchaFailure, redirect } = {}) {
  const calls = { login: [], navigation: [], errors: [], captcha: 0 };
  const pageEnum = { BASE_HOME: '/center/chat', BASE_LOGIN: '/login' };
  const redirectModule = evaluate(fs.readFileSync(path.join(root, 'src/router/postLoginRedirect.ts'), 'utf8'), {
    '/@/enums/pageEnum': { PageEnum: pageEnum },
  });
  const source = fs.readFileSync(path.join(root, 'src/views/system/loginmini/MiniLogin.vue'), 'utf8')
    .match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const page = evaluate(source + '\nexport { accountLogin, formData, randCodeData, loginLoading, handleChangeCheckCode };', {
    vue: { ref: (value) => ({ value }), reactive: (v) => v, toRaw: (v) => v, onMounted: () => {} },
    'vue-router': { useRoute: () => ({ query: { redirect } }) },
    '/@/api/sys/user': { getCodeInfo: async () => {
      calls.captcha++;
      if (captchaFailure) throw new Error('captcha unavailable');
      return 'data:image/png;base64,test';
    } },
    '/@/router': { router: { replace: async (target) => { calls.navigation.push(target); } } },
    '/@/enums/pageEnum': { PageEnum: pageEnum },
    '/@/router/postLoginRedirect': redirectModule,
    '/@/assets/images/checkcode.png': {},
    '/@/hooks/web/useMessage': { useMessage: () => ({
      notification: { error: (e) => calls.errors.push(e) }, createMessage: { warn: () => {} },
    }) },
    '/@/hooks/web/useI18n': { useI18n: () => ({ t: (s) => s }) },
    '/@/utils/cipher': { encryptAESCBC: () => 'encrypted-test-password' },
    '/@/store/modules/user': { useUserStore: () => ({ login: async (params) => {
      calls.login.push(params);
      if (failure) throw new Error(failure);
      return { userInfo: { homePath: '/center/chat/campus' } };
    } }) },
    '/@/utils/cache': { createLocalStorage: () => ({ set: () => {}, remove: () => {} }) },
  });
  assert.equal(page.formData.username, 'demo-user');
  assert.equal(page.formData.password, 'demo-pass');
  Object.assign(page.formData, { username: 'admin', password: 'test-password', inputCode: 'ABCD' });
  page.loginLoading.value = true;
  return { page, calls };
}

test('valid login navigates once through the router and releases the button', async () => {
  const { page, calls } = loginPage();
  await page.accountLogin();
  assert.equal(calls.login[0].goHome, false);
  assert.deepEqual(calls.navigation, ['/center/chat/campus']);
  assert.deepEqual(calls.errors, []);
  assert.equal(page.loginLoading.value, false);
});

test('login follows the requested internal destination', async () => {
  const { page, calls } = loginPage({ redirect: '/login?redirect=/center/chat' });
  await page.accountLogin();
  assert.deepEqual(calls.navigation, ['/center/chat']);
});

for (const captchaFailure of [false, true]) {
  test(`invalid password stays on login even if captcha refresh fails: ${captchaFailure}`, async () => {
    const { page, calls } = loginPage({ failure: '用户名或密码错误', captchaFailure });
    await page.accountLogin();
    assert.deepEqual(calls.navigation, []);
    assert.equal(calls.errors.length, 1);
    assert.equal(calls.errors[0].description, '用户名或密码错误');
    assert.equal(calls.captcha, 1);
    assert.equal(page.formData.inputCode, '');
    assert.equal(page.randCodeData.requestCodeSuccess, !captchaFailure);
    assert.equal(page.loginLoading.value, false);
  });
}

test('captcha refresh suppresses generic success notifications', async () => {
  let request;
  const api = evaluate(fs.readFileSync(path.join(root, 'src/api/sys/user.ts'), 'utf8'), {
    '/@/utils/http/axios': { defHttp: { get: async (...args) => { request = args; return 'image'; } } },
    '/@/hooks/web/useMessage': { useMessage: () => ({}) },
    '/@/store/modules/user': {}, '/@/utils/auth': {}, '/@/enums/cacheEnum': {},
    '/@/router': {}, '/@/enums/pageEnum': {}, '/@/router/postLoginRedirect': {}, '@/enums/exceptionEnum': {},
  });
  assert.equal(await api.getCodeInfo('test-key'), 'image');
  assert.equal(request[1].successMessageMode, 'none');
});
