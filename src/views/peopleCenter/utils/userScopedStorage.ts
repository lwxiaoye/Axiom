/**
 * 按登录用户隔离的本机存储键（2026-09-19 客户端会话隔离审计）。
 *
 * 为什么：校园场景（机房/图书馆公用机）同一浏览器先后登录不同账号。服务端隔离已验证
 * 没问题（读别人会话 → 404），但业务代码自己写在 localStorage / sessionStorage 里的
 * 「新对话默认模型」「面试提交冲突提示」「子智能体运行变量」「工作流本地备份」等 key
 * 原先不带用户 id、退出登录也不清——B 登录后会原样读到 A 的。
 *
 * 规则：
 *   - 含会话/业务数据的 key 一律经这里读写：键名变成 `axiom:u:<userId>:<base>`；
 *   - 未登录（拿不到用户 id）时不读不写——宁可丢一次偏好，也不能写进无主的公共命名空间；
 *   - 退出登录（含 token 失效被踢回登录页）调用 clearUserScopedStorage() 把该用户的
 *     全部作用域 key 与 IndexedDB 草稿一起清掉；
 *   - 旧的无前缀 key 不迁移：首次读取时直接删掉（purgeLegacyUnscopedKeys）。
 *   - 纯 UI 偏好（侧栏宽度/折叠、视图模式、主题、语言）不经这里，保留跨用户。
 *
 * 用户 id 的来源用「注册读取器」而不是直接 import user store：本文件因此没有任何
 * `/@/` 别名依赖，node 环境的 jest（chat 配置无别名、无 Pinia）可以直接跑；
 * user store 模块加载时注册真实读取器（见 src/store/modules/user.ts 末尾）。
 */
import { clearDraftRecordsForUser } from '../composables/chatDrafts';

/** 作用域键前缀：`axiom:u:<userId>:<base>` */
export const USER_SCOPE_PREFIX = 'axiom:u:';

type UserIdGetter = () => string;

let userIdGetter: UserIdGetter = () => '';

/** 由 user store 模块在加载时注册真实读取器；未注册 = 视为未登录。 */
export function registerStorageUserIdGetter(getter: UserIdGetter): void {
  userIdGetter = getter;
}

/** 当前登录用户 id；未登录 / 读取器未注册 / 读取抛错 → 空串。 */
export function currentStorageUserId(): string {
  try {
    return String(userIdGetter() || '').trim();
  } catch {
    return '';
  }
}

/** 按用户作用域后的键名；未登录返回 null（调用方据此不读不写）。 */
export function userScopedStorageKey(base: string, userId: string = currentStorageUserId()): string | null {
  const uid = String(userId || '').trim();
  if (!uid || !base) return null;
  return `${USER_SCOPE_PREFIX}${uid}:${base}`;
}

/**
 * 历史上业务代码直接写在根命名空间的 key（不含用户 id）。
 * 不做迁移：这些值本来就分不清属于谁，读到就删，一次性清理。
 */
export const LEGACY_UNSCOPED_KEYS: readonly string[] = ['agent-active-model'];
export const LEGACY_UNSCOPED_KEY_PREFIXES: readonly string[] = [
  'interview:submission-conflict:',
  'agent-run:variables:',
  'wf-draft-backup:',
];

function isLegacyUnscopedKey(key: string): boolean {
  return LEGACY_UNSCOPED_KEYS.includes(key) || LEGACY_UNSCOPED_KEY_PREFIXES.some((p) => key.startsWith(p));
}

function safeStorage(kind: 'local' | 'session'): Storage | null {
  try {
    const s = kind === 'local' ? globalThis.localStorage : globalThis.sessionStorage;
    return s || null;
  } catch {
    return null; // 隐私模式 / 被禁用：当作没有存储
  }
}

function keysOf(storage: Storage): string[] {
  const keys: string[] = [];
  try {
    for (let i = 0; i < storage.length; i += 1) {
      const k = storage.key(i);
      if (k != null) keys.push(k);
    }
  } catch {
    // 读不到 key 列表：返回已收集到的
  }
  return keys;
}

let legacyPurged = false;

/** 清一次旧的无前缀 key（localStorage + sessionStorage）。每次页面加载最多执行一次。 */
export function purgeLegacyUnscopedKeys(force = false): number {
  if (legacyPurged && !force) return 0;
  legacyPurged = true;
  let removed = 0;
  for (const kind of ['local', 'session'] as const) {
    const storage = safeStorage(kind);
    if (!storage) continue;
    for (const key of keysOf(storage)) {
      if (!isLegacyUnscopedKey(key)) continue;
      try {
        storage.removeItem(key);
        removed += 1;
      } catch {
        // 删不掉就算了：下次读取再试
      }
    }
  }
  return removed;
}

/** 读当前用户作用域下的值；未登录或没有存储 → null。 */
export function readUserScoped(base: string, storage: Storage | null = safeStorage('local')): string | null {
  purgeLegacyUnscopedKeys();
  const key = userScopedStorageKey(base);
  if (!key || !storage) return null;
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

/** 写当前用户作用域下的值；未登录时静默不写（不能把业务数据写进公共命名空间）。 */
export function writeUserScoped(base: string, value: string, storage: Storage | null = safeStorage('local')): boolean {
  purgeLegacyUnscopedKeys();
  const key = userScopedStorageKey(base);
  if (!key || !storage) return false;
  try {
    storage.setItem(key, value);
    return true;
  } catch {
    return false; // 配额满 / 隐私模式：内存态照常生效
  }
}

/** 删当前用户作用域下的一个 key。 */
export function removeUserScoped(base: string, storage: Storage | null = safeStorage('local')): void {
  const key = userScopedStorageKey(base);
  if (!key || !storage) return;
  try {
    storage.removeItem(key);
  } catch {
    // 删不掉无害
  }
}

/**
 * 退出登录清理：删掉 `axiom:u:<userId>:*` 全部 key（localStorage + sessionStorage），
 * 并清掉该用户在 IndexedDB 里的输入草稿。
 *
 * userId 可显式传入：logout 流程里 store 的 userInfo 会先被置空，调用方要在置空前把 id 取出来。
 * 传不进 id 且当前也读不到 → 只清旧的无前缀 key，不动别人的作用域。
 * 返回删掉的 Web Storage key 数（IndexedDB 部分异步、不计入）。
 */
export function clearUserScopedStorage(userId: string = currentStorageUserId()): number {
  let removed = purgeLegacyUnscopedKeys(true);
  const uid = String(userId || '').trim();
  if (!uid) return removed;
  const prefix = `${USER_SCOPE_PREFIX}${uid}:`;
  for (const kind of ['local', 'session'] as const) {
    const storage = safeStorage(kind);
    if (!storage) continue;
    for (const key of keysOf(storage)) {
      if (!key.startsWith(prefix)) continue;
      try {
        storage.removeItem(key);
        removed += 1;
      } catch {
        // 删不掉无害
      }
    }
  }
  // 草稿在 IndexedDB（含缩略图），按 userId 字段清；失败不阻断退出。
  void clearDraftRecordsForUser(uid).catch(() => {});
  return removed;
}
