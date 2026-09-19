/**
 * 按用户隔离的本机存储：键作用域、未登录不读不写、退出登录清理、旧无前缀 key 清理。
 * node 环境没有 Web Storage，这里用一个最小的内存 Storage 桩喂给 globalThis。
 */
import {
  clearUserScopedStorage,
  currentStorageUserId,
  purgeLegacyUnscopedKeys,
  readUserScoped,
  registerStorageUserIdGetter,
  removeUserScoped,
  USER_SCOPE_PREFIX,
  userScopedStorageKey,
  writeUserScoped,
} from './userScopedStorage';
import { clearDraftRecordsForUser, listDraftRecords, saveDraftRecord } from '../composables/chatDrafts';

class MemoryStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  key(i: number) { return [...this.map.keys()][i] ?? null; }
  getItem(k: string) { return this.map.has(k) ? this.map.get(k)! : null; }
  setItem(k: string, v: string) { this.map.set(k, String(v)); }
  removeItem(k: string) { this.map.delete(k); }
  clear() { this.map.clear(); }
  keys() { return [...this.map.keys()]; }
}

let local: MemoryStorage;
let session: MemoryStorage;
let userId = '';

beforeEach(() => {
  local = new MemoryStorage();
  session = new MemoryStorage();
  (globalThis as any).localStorage = local;
  (globalThis as any).sessionStorage = session;
  userId = '';
  registerStorageUserIdGetter(() => userId);
});

afterEach(() => {
  delete (globalThis as any).localStorage;
  delete (globalThis as any).sessionStorage;
  registerStorageUserIdGetter(() => '');
});

describe('userScopedStorageKey', () => {
  it('prefixes the base key with the current user id', () => {
    userId = 'u-alice';
    expect(userScopedStorageKey('agent-active-model')).toBe(`${USER_SCOPE_PREFIX}u-alice:agent-active-model`);
    expect(currentStorageUserId()).toBe('u-alice');
  });

  it('returns null when nobody is logged in or the getter throws', () => {
    expect(userScopedStorageKey('agent-active-model')).toBeNull();
    registerStorageUserIdGetter(() => { throw new Error('pinia not ready'); });
    expect(currentStorageUserId()).toBe('');
    expect(userScopedStorageKey('agent-active-model')).toBeNull();
  });

  it('gives different users different keys for the same base', () => {
    expect(userScopedStorageKey('x', 'a')).not.toBe(userScopedStorageKey('x', 'b'));
  });
});

describe('read / write / remove', () => {
  it('does not read or write anything while logged out', () => {
    expect(writeUserScoped('agent-active-model', 'gpt')).toBe(false);
    expect(local.keys()).toEqual([]);
    local.setItem(`${USER_SCOPE_PREFIX}u-alice:agent-active-model`, 'gpt');
    expect(readUserScoped('agent-active-model')).toBeNull();
  });

  it('writes under the user scope and only reads back for that same user', () => {
    userId = 'u-alice';
    expect(writeUserScoped('agent-active-model', 'deepseek')).toBe(true);
    expect(local.keys()).toEqual([`${USER_SCOPE_PREFIX}u-alice:agent-active-model`]);
    expect(readUserScoped('agent-active-model')).toBe('deepseek');

    userId = 'u-bob';
    expect(readUserScoped('agent-active-model')).toBeNull();
    writeUserScoped('agent-active-model', 'qwen');
    expect(readUserScoped('agent-active-model')).toBe('qwen');

    userId = 'u-alice';
    expect(readUserScoped('agent-active-model')).toBe('deepseek');
  });

  it('supports sessionStorage as the backing store', () => {
    userId = 'u-alice';
    writeUserScoped('agent-run:variables:app1:new', '{"city":"重庆"}', session);
    expect(local.keys()).toEqual([]);
    expect(readUserScoped('agent-run:variables:app1:new', session)).toBe('{"city":"重庆"}');
    removeUserScoped('agent-run:variables:app1:new', session);
    expect(session.keys()).toEqual([]);
  });

  it('survives a storage that throws (private mode / quota)', () => {
    userId = 'u-alice';
    const broken = {
      length: 0,
      key: () => null,
      getItem: () => { throw new Error('denied'); },
      setItem: () => { throw new Error('quota'); },
      removeItem: () => { throw new Error('denied'); },
      clear: () => {},
    } as unknown as Storage;
    expect(writeUserScoped('k', 'v', broken)).toBe(false);
    expect(readUserScoped('k', broken)).toBeNull();
    expect(() => removeUserScoped('k', broken)).not.toThrow();
  });
});

describe('legacy unscoped keys', () => {
  it('drops old root-namespace business keys on first access and ignores their values', () => {
    local.setItem('agent-active-model', 'leaked-model');
    local.setItem('interview:submission-conflict:t1', 'leaked');
    local.setItem('wf-draft-backup:app1', '{"json":"..."}');
    session.setItem('agent-run:variables:app1:new', '{"x":1}');
    local.setItem('center-nav-width', '260'); // UI 偏好：不在清理名单
    // 「每次页面加载只清一次」是模块级状态；用 isolateModules 模拟一次全新的页面加载
    jest.isolateModules(() => {
      const fresh = require('./userScopedStorage') as typeof import('./userScopedStorage');
      fresh.registerStorageUserIdGetter(() => 'u-alice');
      expect(fresh.readUserScoped('agent-active-model')).toBeNull();
    });
    expect(local.keys()).toEqual(['center-nav-width']);
    expect(session.keys()).toEqual([]);
  });

  it('purgeLegacyUnscopedKeys reports how many were removed', () => {
    local.setItem('agent-active-model', 'm');
    expect(purgeLegacyUnscopedKeys(true)).toBe(1);
    expect(purgeLegacyUnscopedKeys(true)).toBe(0);
  });
});

describe('clearUserScopedStorage (logout)', () => {
  it('removes every key of that user in both storages and leaves other users and UI prefs alone', () => {
    userId = 'u-alice';
    writeUserScoped('agent-active-model', 'deepseek');
    writeUserScoped('interview:submission-conflict:t1', '面试进度已更新');
    writeUserScoped('agent-run:variables:app1:new', '{"x":1}', session);
    userId = 'u-bob';
    writeUserScoped('agent-active-model', 'qwen');
    local.setItem('center-nav-width', '260');
    local.setItem('wb-nav-collapsed', '1');

    const removed = clearUserScopedStorage('u-alice');
    expect(removed).toBe(3);
    expect(local.keys().sort()).toEqual([
      `${USER_SCOPE_PREFIX}u-bob:agent-active-model`,
      'center-nav-width',
      'wb-nav-collapsed',
    ].sort());
    expect(session.keys()).toEqual([]);
  });

  it('uses the current user when no id is passed and is a no-op for the scope when logged out', () => {
    userId = 'u-alice';
    writeUserScoped('agent-active-model', 'deepseek');
    expect(clearUserScopedStorage()).toBe(1);
    expect(local.keys()).toEqual([]);

    userId = '';
    local.setItem(`${USER_SCOPE_PREFIX}u-bob:agent-active-model`, 'qwen');
    expect(clearUserScopedStorage()).toBe(0);
    expect(local.keys()).toEqual([`${USER_SCOPE_PREFIX}u-bob:agent-active-model`]);
  });

  it('also drops that user\'s IndexedDB drafts (memory fallback here) and keeps the other user\'s', async () => {
    const base = { threadId: null, content: '', attachments: [], skills: [], knowledge: [], files: [], webSearch: false, planMode: false, updatedAt: 1, version: 1 as const };
    await saveDraftRecord({ ...base, draftId: 'new:ordinary:a1', userId: 'u-alice', content: 'A 的草稿' });
    await saveDraftRecord({ ...base, draftId: 'thread:t-a', userId: 'u-alice', threadId: 't-a', content: 'A 会话草稿' });
    await saveDraftRecord({ ...base, draftId: 'new:ordinary:b1', userId: 'u-bob', content: 'B 的草稿' });

    clearUserScopedStorage('u-alice');
    await new Promise((r) => setTimeout(r, 0));

    expect(await listDraftRecords('u-alice')).toEqual([]);
    expect((await listDraftRecords('u-bob')).map((d) => d.draftId)).toEqual(['new:ordinary:b1']);
    await clearDraftRecordsForUser('u-bob');
    expect(await listDraftRecords('u-bob')).toEqual([]);
  });
});

/** 接线契约：user store / 路由守卫 / 各业务读写点确实经过这个工具（源码文本断言，与仓库既有契约测试同法）。 */
describe('wiring contract', () => {
  const fs = require('node:fs') as typeof import('node:fs');
  const path = require('node:path') as typeof import('node:path');
  const src = path.resolve(__dirname, '../../..');
  const read = (rel: string) => fs.readFileSync(path.join(src, rel), 'utf8');

  it('user store registers the id getter and clears the scope on logout before userInfo is dropped', () => {
    const store = read('store/modules/user.ts');
    expect(store).toContain('registerStorageUserIdGetter(');
    const clearAt = store.indexOf('clearUserScopedStorage(resolveStorageUserId(this.getUserInfo))');
    const dropAt = store.indexOf('this.setUserInfo(null);');
    expect(clearAt).toBeGreaterThan(-1);
    expect(dropAt).toBeGreaterThan(clearAt);
    // 主动退出不带 redirect；被踢只带 pathname
    expect(store).toContain('await this.logout(true, true);');
    expect(store).toContain('query: userInitiated ? {} : loginRedirectQuery(router.currentRoute.value.path)');
  });

  it('stateGuard clears the scope before resetting the user state', () => {
    const guard = read('router/guard/stateGuard.ts');
    expect(guard.indexOf('clearUserScopedStorage();')).toBeGreaterThan(-1);
    expect(guard.indexOf('clearUserScopedStorage();')).toBeLessThan(guard.indexOf('userStore.resetState();'));
  });

  it('business persistence goes through the scoped helpers, not raw Web Storage', () => {
    for (const rel of [
      'views/peopleCenter/composables/useCenterChat.ts',
      'views/peopleCenter/components/ModelSelector.vue',
      'views/peopleCenter/builtinAssistants/interview/useInterviewSession.ts',
      'views/agent/run/useAgentRun.ts',
      'views/agent/run/index.vue',
      'views/workflow/editor/index.vue',
    ]) {
      const code = read(rel);
      expect(code).toMatch(/from '[./@a-zA-Z]*utils\/userScopedStorage'/);
      expect(code).not.toMatch(/sessionStorage\.(get|set|remove)Item\(/);
    }
    // useCenterChat 里 center-chat-follow-up-mode 是纯交互偏好，允许直接用 localStorage；
    // 其余文件不得再直接读写 localStorage
    for (const rel of [
      'views/peopleCenter/components/ModelSelector.vue',
      'views/peopleCenter/builtinAssistants/interview/useInterviewSession.ts',
      'views/agent/run/useAgentRun.ts',
      'views/agent/run/index.vue',
      'views/workflow/editor/index.vue',
    ]) {
      expect(read(rel)).not.toMatch(/localStorage\.(get|set|remove)Item\(/);
    }
  });
});
