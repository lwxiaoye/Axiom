import type { KnowledgeDocument } from './knowledge.types';

const processingStatuses: KnowledgeDocument['status'][] = [
  'UPLOADED', 'PARSING', 'SPLITTING', 'EMBEDDING', 'QA_GENERATING',
];

const statusTexts: Record<KnowledgeDocument['status'], string> = {
  UPLOADED: '待处理',
  PARSING: '解析中',
  SPLITTING: '切分中',
  EMBEDDING: '原文索引中',
  QA_GENERATING: '问答生成中',
  READY: '可用',
  FAILED: '失败',
};

export function isKnowledgeDocumentProcessing(status: KnowledgeDocument['status']): boolean {
  return processingStatuses.includes(status);
}

export function knowledgeDocumentStatusText(status: KnowledgeDocument['status']): string {
  return statusTexts[status];
}

export function shouldShowKnowledgeDocumentError(status: KnowledgeDocument['status'], errorMessage?: string): boolean {
  return status === 'FAILED' && Boolean(errorMessage);
}
