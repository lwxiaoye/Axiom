const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const ts = require('typescript');
const dotenv = require('dotenv');

const root = path.resolve(__dirname, '..');
function frontendCipher(env) {
  const source = fs.readFileSync(path.join(root, 'src/utils/cipher.ts'), 'utf8')
    .replaceAll('import.meta.env', '__env');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'exports', 'module', '__env', js)(require, module.exports, module, env);
  return module.exports;
}

for (const file of ['.env.development', '.env.local.example']) {
  test(`${file}: actual Vue cipher interoperates with standard AES-CBC`, () => {
    const env = dotenv.parse(fs.readFileSync(path.join(root, file)));
    const protocol = dotenv.parse(fs.readFileSync(path.join(root, 'deploy/local/.env.example')));
    assert.equal(env.VITE_LOGIN_AES_KEY, protocol.AXIOM_LOGIN_AES_KEY);
    assert.equal(env.VITE_LOGIN_AES_IV, protocol.AXIOM_LOGIN_AES_IV);
    const plaintext = 'test-password-中文';
    const encrypted = frontendCipher(env).encryptAESCBC(plaintext);
    const decoder = crypto.createDecipheriv('aes-128-cbc', Buffer.from(env.VITE_LOGIN_AES_KEY), Buffer.from(env.VITE_LOGIN_AES_IV));
    assert.equal(Buffer.concat([decoder.update(Buffer.from(encrypted, 'base64')), decoder.final()]).toString(), plaintext);
  });
}

test('invalid protocol parameters fail before a login request', () => {
  assert.throws(() => frontendCipher({ VITE_LOGIN_AES_KEY: 'short', VITE_LOGIN_AES_IV: 'short' }).encryptAESCBC('test'));
});
