const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

function page(request) {
  const source = fs.readFileSync(path.join(__dirname, '../src/views/peopleCenter/pages/ModelConfigPage.vue'), 'utf8').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const js = ts.transpileModule(source + '\nexport { form, load, save, test, feedback, hasKey, loading, loadError, testing, saving };', { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  const exports = {};
  const events = [];
  const mocks = {
    vue: { ref: (value) => ({ value }), reactive: (v) => v, computed: (fn) => ({ get value() { return fn(); } }), watch() {}, onMounted() {}, nextTick: async () => {} },
    '../agentApi': { requestAgentApi: request },
  };
  new Function('require', 'exports', 'window', js)((name) => { assert.ok(name in mocks, name); return mocks[name]; }, exports, { dispatchEvent: (event) => events.push(event.type) });
  return { ...exports, events };
}
const saved = { base_url: 'https://provider.example/v1', model: 'demo-model', enabled: true, has_api_key: true };

test('malformed scheme is rejected immediately without sending credentials', async () => {
  const p = page(async () => { assert.fail('Unexpected request'); });
  Object.assign(p.form, { base_url: 'https:provider.example/v1', model: 'demo', api_key: 'draft-key' });
  await p.test();
  await p.save();
  assert.match(p.feedback.value.message, /两个斜杠/);
  assert.equal(p.testing.value, false);
});

test('loading saved config shows key status without returning the secret', async () => {
  const p = page(async () => saved);
  await p.load();
  assert.equal(p.form.api_key, '');
  assert.equal(p.hasKey.value, true);
  assert.equal(p.form.model, 'demo-model');
  assert.equal(p.loading.value, false);
});
test('save preserves an existing blank key and notifies model selectors', async () => {
  const requests = [];
  const p = page(async (url, options) => { requests.push({ url, options }); return saved; });
  await p.load();
  p.form.model = 'changed';
  await p.save();
  const payload = JSON.parse(requests[1].options.body);
  assert.equal(requests[1].options.method, 'PUT');
  assert.equal(payload.api_key, '');
  assert.equal(payload.model, 'changed');
  assert.equal(p.feedback.value.success, true);
  assert.deepEqual(p.events, ['axiom:model-config-updated']);
});
test('test uses the unsaved draft, displays result, and does not save it', async () => {
  const requests = [];
  const p = page(async (url, options) => { requests.push({ url, options }); return { success: true, message: 'OK', latency_ms: 20 }; });
  Object.assign(p.form, { base_url: saved.base_url, model: saved.model, api_key: 'draft-key' });
  await p.test();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/model-connection/test');
  assert.equal(JSON.parse(requests[0].options.body).api_key, 'draft-key');
  assert.equal(p.feedback.value.latency_ms, 20);
  assert.deepEqual(p.events, []);
});
test('empty fields do not send a request', async () => {
  const p = page(async () => { assert.fail('Unexpected request'); });
  await p.test();
  await p.save();
  assert.equal(p.feedback.value.success, false);
});
test('failed save releases loading and keeps the unsaved draft', async () => {
  const p = page(async () => { throw new Error('服务暂不可用'); });
  Object.assign(p.form, { base_url: saved.base_url, model: saved.model, api_key: 'draft-key' });
  await p.save();
  assert.equal(p.saving.value, false);
  assert.equal(p.form.api_key, 'draft-key');
  assert.equal(p.feedback.value.message, '服务暂不可用');
});
