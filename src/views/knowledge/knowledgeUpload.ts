import type { KnowledgeUploadOptions } from './knowledge.types';

export const KNOWLEDGE_UPLOAD_ACCEPT = '.pdf,.docx,.txt,.md,.markdown';
export const KNOWLEDGE_UPLOAD_MAX_FILES = 20;
export const KNOWLEDGE_UPLOAD_MAX_SIZE = 50 * 1024 * 1024;
export const KNOWLEDGE_UPLOAD_MAX_SIZE_LABEL = '50 MB';
export const KNOWLEDGE_UPLOAD_ACCEPT_LABEL = 'PDF、DOCX、TXT、Markdown';

export const DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS: KnowledgeUploadOptions = {
  processingMode: 'CHUNK',
  splitConditionType: 'LENGTH_GT',
  splitConditionValue: 1000,
  addTitleToIndex: true,
  imageAutoIndex: false,
  imageExtractionEnabled: true,
  splitStrategy: 'PARAGRAPH',
  maxParagraphDepth: 3,
  chunkSize: 1000,
  overlapSize: 150,
  separator: '\n\n',
};

const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md', '.markdown'];

export function knowledgeUploadFileError(file: { name?: string; size?: number }): string {
  const name = String(file?.name || '').trim();
  const lower = name.toLowerCase();
  const size = Number(file?.size || 0);
  if (!name || !ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
    return `仅支持 ${KNOWLEDGE_UPLOAD_ACCEPT_LABEL}`;
  }
  if (!Number.isFinite(size) || size <= 0) return `${name} 文件为空`;
  if (size > KNOWLEDGE_UPLOAD_MAX_SIZE) return `${name} 超过 ${KNOWLEDGE_UPLOAD_MAX_SIZE_LABEL}`;
  return '';
}

export function resolveCreatedKnowledgeId(created: unknown): string {
  if (typeof created === 'string' && created.trim()) return created.trim();
  if (!created || typeof created !== 'object') return '';
  const record = created as Record<string, unknown>;
  const nested = record.result;
  if (nested && nested !== created) {
    const nestedId = resolveCreatedKnowledgeId(nested);
    if (nestedId) return nestedId;
  }
  const id = record.id ?? record.knowledgeId;
  return id == null ? '' : String(id).trim();
}

export function unwrapKnowledgeUploadResponse<T>(response: unknown): T {
  if (response && typeof response === 'object' && (response as { success?: unknown }).success === false) {
    const message = String((response as { message?: unknown }).message || '').trim();
    throw new Error(message || '请求处理失败');
  }
  const record = response as { result?: T } | T;
  if (record && typeof record === 'object' && 'result' in record && record.result != null) {
    return record.result as T;
  }
  return record as T;
}

export function originFileFromUploadItem(item: unknown): File | undefined {
  if (item instanceof File) return item;
  if (!item || typeof item !== 'object') return undefined;
  const origin = (item as { originFileObj?: unknown }).originFileObj;
  return origin instanceof File ? origin : undefined;
}
