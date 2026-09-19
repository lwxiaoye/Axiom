/**
 * 输入草稿本机持久化（审计 D-01/D-02/D-03，2026-07-22）。
 *
 * - D-01：草稿不再只活在组件内存 Map——IndexedDB 落盘，刷新/重启/重新挂载后仍在；
 *   indexedDB 不可用（老内核/单测环境）降级为模块级内存 Map（行为等同旧实现，不报错）。
 * - D-02：每个未发送的新会话有独立 draftId（`new:<uuid>`），不再共用空字符串键；
 *   已有会话的草稿键为 `thread:<threadId>`。首次发送成功后由调用方删除草稿记录。
 * - D-03：上传接口已把原始字节保存为唯一文件实体，草稿持久化 fileId/sha256 与展示
 *   元数据，不保存原图 data URL 或解析全文。刷新后仍可按 fileId 精确发送和修改；
 *   只有旧草稿没有 fileId 时才要求重新上传。
 * - D-04（2026-08-05）：历史抽屉主列表只展示「有实质内容」的匿名草稿，并有数量上限，
 *   避免失败恢复/反复新建对话把历史污染成失败清单。会话内草稿（thread:）仍只服务 composer。
 *
 * 刻意不用 localStorage：草稿含缩略图，单键字符串会既胀又慢；IndexedDB 是正解。
 */
import type { AssistantPreset, KnowledgeSelection, SkillItem, SubagentItem } from '../agentApi';
import type { WorkFolderSelection } from '../myfiles.api';

/** 历史抽屉「本地草稿」最多展示条数；超出时保留最新，旧的从列表与存储清理。 */
export const MAX_LISTED_ANONYMOUS_DRAFTS = 5;

export type DraftAttachmentMeta = {
  filename: string;
  kind: string;
  fileId?: string;
  sha256?: string;
  status?: string;
  note?: string;
  /** 压缩缩略图（≤200k 字符 JPEG data URL）：恢复后图片卡仍有图可看 */
  thumbUrl?: string;
};

export type PersistedChatDraft = {
  draftId: string;               // thread:<threadId> | new:<uuid>
  userId: string;
  threadId: string | null;
  content: string;
  attachments: DraftAttachmentMeta[];
  skills: SkillItem[];
  /** @ 选中的一次性委托目标；刷新后恢复，发送成功后清空。 */
  subagent?: SubagentItem;
  knowledge: KnowledgeSelection[];
  files: Array<{ id: string; filename: string }>;
  /** 「最近的对话」引用（2026-07-28）。可选：老草稿记录没有这个字段，读回来当作没选。 */
  threads?: Array<{ id: string; title: string }>;
  webSearch: boolean;
  planMode: boolean;
  assistantPreset?: AssistantPreset;
  workspaceFolder?: WorkFolderSelection;
  /** 首帧前断线后尚未确认受理的请求；同草稿重试必须复用该幂等键。 */
  pendingRequestId?: string;
  pendingRequestFingerprint?: string;
  updatedAt: number;
  version: 1;
};

/** 是否值得出现在历史抽屉：仅有联网/任务开关等上下文、没有正文或附件的不算。 */
export function isListableAnonymousDraft(record: PersistedChatDraft): boolean {
  if (!record || record.threadId) return false;
  if ((record.content || '').trim()) return true;
  if (Array.isArray(record.attachments) && record.attachments.length > 0) return true;
  return false;
}

const DB_NAME = 'center-chat-drafts';
const DB_VERSION = 1;
const STORE = 'drafts';

// indexedDB 不可用时的进程内回退（单测/老内核）：语义与 IndexedDB 相同，仅不跨刷新
const memoryStore = new Map<string, PersistedChatDraft>();

function idbUsable(): boolean {
  try {
    return typeof indexedDB !== 'undefined' && indexedDB != null;
  } catch {
    return false;
  }
}

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  if (!idbUsable()) return Promise.resolve(null);
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve) => {
    try {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: 'draftId' });
          store.createIndex('byUser', 'userId', { unique: false });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null); // 打不开（隐私模式等）：降级内存
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

function requestToPromise<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function newAnonymousDraftId(scope: 'ordinary' | AssistantPreset = 'ordinary'): string {
  const prefix = `new:${scope}:`;
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return `${prefix}${crypto.randomUUID().replace(/-/g, '')}`;
    }
  } catch {
    // fall through
  }
  return `${prefix}${Date.now().toString(16)}${Math.random().toString(16).slice(2, 12)}`;
}

export async function saveDraftRecord(draft: PersistedChatDraft): Promise<void> {
  const db = await openDb();
  if (!db) {
    memoryStore.set(draft.draftId, draft);
    return;
  }
  try {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(draft);
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve(); // 落盘失败不阻断输入：草稿是增强，不是主链路
      tx.onabort = () => resolve();
    });
  } catch {
    memoryStore.set(draft.draftId, draft);
  }
}

export async function getDraftRecord(draftId: string): Promise<PersistedChatDraft | null> {
  const db = await openDb();
  if (!db) return memoryStore.get(draftId) || null;
  try {
    const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(draftId);
    const row = await requestToPromise(req);
    return (row as PersistedChatDraft) || null;
  } catch {
    return memoryStore.get(draftId) || null;
  }
}

export async function deleteDraftRecord(draftId: string): Promise<void> {
  memoryStore.delete(draftId);
  const db = await openDb();
  if (!db) return;
  try {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).delete(draftId);
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
  } catch {
    // 删除失败无害：下次保存/清理会再试
  }
}

/** 列出某用户全部草稿（updatedAt 降序）。 */
export async function listDraftRecords(userId: string): Promise<PersistedChatDraft[]> {
  const db = await openDb();
  let rows: PersistedChatDraft[];
  if (!db) {
    rows = [...memoryStore.values()];
  } else {
    try {
      const req = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
      rows = ((await requestToPromise(req)) as PersistedChatDraft[]) || [];
    } catch {
      rows = [...memoryStore.values()];
    }
  }
  return rows
    .filter((r) => r && r.userId === userId)
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

/** 清空全部草稿（登出清理/测试隔离用）。 */
export async function clearAllDraftRecords(): Promise<void> {
  memoryStore.clear();
  const db = await openDb();
  if (!db) return;
  try {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).clear();
    await new Promise<void>((resolve) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    });
  } catch {
    // 清空失败无害
  }
}

/** 只清某个用户的草稿（退出登录清理）。为什么不用 clearAllDraftRecords：
 *  草稿本就按 userId 落库，退出只该抹掉自己的，别的账号（同机多人）的草稿不动。 */
export async function clearDraftRecordsForUser(userId: string): Promise<void> {
  const uid = String(userId || '').trim();
  if (!uid) return;
  for (const [id, record] of memoryStore) {
    if (record?.userId === uid) memoryStore.delete(id);
  }
  const db = await openDb();
  if (!db) return;
  try {
    const store = db.transaction(STORE, 'readwrite').objectStore(STORE);
    // 建库时就有 byUser 索引（见 onupgradeneeded），按索引游标删，不必整表 getAll
    const cursorReq = store.index('byUser').openCursor(IDBKeyRange.only(uid));
    await new Promise<void>((resolve) => {
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result;
        if (!cursor) { resolve(); return; }
        cursor.delete();
        cursor.continue();
      };
      cursorReq.onerror = () => resolve(); // 清不掉不阻断退出：草稿是增强，不是主链路
    });
  } catch {
    // 同上
  }
}

/** 当前登录用户 id（草稿按用户隔离，防同浏览器多账号串看）。
 *  动态 import：单测环境无 Pinia/别名时静默退回 'anon'，不拖垮 composable。 */
export async function currentDraftUserId(): Promise<string> {
  try {
    const mod: any = await import('/@/store/modules/user');
    const info: any = mod.useUserStore().getUserInfo || {};
    return String(info.id || info.username || 'anon');
  } catch {
    return 'anon';
  }
}
