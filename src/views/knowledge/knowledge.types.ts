export interface PageResult<T> {
  records: T[];
  total: number;
  size: number;
  current: number;
  pages?: number;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description?: string;
  ownerUserId: string;
  createBy?: string;
  createBy_dictText?: string;
  permission?: KnowledgePermission;
  currentPermission?: KnowledgePermission;
  accessPermission?: KnowledgePermission;
  aclPermission?: KnowledgePermission;
  sharePermission?: KnowledgePermission;
  status: 'ACTIVE' | 'DISABLED';
  retrievalMode: string;
  // 混合检索两路权重（0–1，和为 1）；旧接口没有这两个字段，读时按 0.5 兜底
  semanticWeight?: number;
  keywordWeight?: number;
  topK: number;
  scoreThreshold: number;
  chunkSize: number;
  chunkOverlap: number;
  documentCount: number;
  chunkCount: number;
  createTime: string;
  updateTime: string;
}

export interface KnowledgeDocument {
  id: string;
  knowledgeId: string;
  originalName: string;
  fileType: string;
  contentType?: string;
  fileSize: number;
  status: 'UPLOADED' | 'PARSING' | 'SPLITTING' | 'EMBEDDING' | 'QA_GENERATING' | 'READY' | 'FAILED';
  progress: number;
  errorMessage?: string;
  processingMode?: 'CHUNK' | 'QA';
  splitStrategy?: 'PARAGRAPH' | 'LENGTH' | 'SEPARATOR';
  splitConfigJson?: string;
  chunkCount: number;
  enabled: number;
  createTime: string;
  updateTime: string;
}

export interface KnowledgeChunk {
  id: string;
  knowledgeId: string;
  documentId: string;
  chunkIndex: number;
  title?: string;
  content: string;
  contentWithImages?: string;
  charCount: number;
  pageNumber?: number;
  images?: KnowledgePreviewImage[];
  enabled: number;
  updateTime: string;
}

export interface RetrievalItem {
  knowledgeId: string;
  documentId: string;
  chunkId: string;
  documentName: string;
  title?: string;
  pageNumber?: number;
  content: string;
  contentWithImages?: string;
  imageUrls?: string[];
  sourceType?: 'DOCUMENT_CHUNK';
  score: number;
}

export interface KnowledgePreviewImage {
  pageNumber?: number;
  imageId?: string;
  url: string;
  ocrText?: string;
  caption?: string;
}

export interface KnowledgePreviewChunk {
  content: string;
  pageNumber?: number;
  images?: KnowledgePreviewImage[];
}

export interface RetrievalResponse {
  query: string;
  latencyMs: number;
  items: RetrievalItem[];
}

export interface KnowledgeDocumentPreview {
  fileName: string;
  fileType: string;
  totalChunks: number;
  chunks: KnowledgePreviewChunk[];
}

export interface KnowledgeUploadOptions {
  processingMode: 'CHUNK' | 'QA';
  splitConditionType: 'LENGTH_GT' | 'ALWAYS';
  splitConditionValue: number;
  addTitleToIndex: boolean;
  imageAutoIndex: boolean;
  imageExtractionEnabled: boolean;
  splitStrategy: 'PARAGRAPH' | 'LENGTH' | 'SEPARATOR';
  maxParagraphDepth: number;
  chunkSize: number;
  overlapSize: number;
  separator: string;
}

export interface KnowledgeAcl {
  id?: string;
  knowledgeId?: string;
  subjectType: 'USER' | 'ROLE' | 'DEPARTMENT';
  subjectId: string | string[];
  permission: KnowledgePermission;
}

export type KnowledgePermission = 'VIEWER' | 'EDITOR' | 'OWNER';

export type KnowledgeAnalyticsPreset = 'today' | 'last7' | 'last30' | 'custom';

export interface KnowledgeAnalyticsRange {
  from?: string;
  to?: string;
}

export interface KnowledgeAnalyticsStock {
  knowledgeBaseCount: number;
  documentCount: number;
  chunkCount: number;
}

export interface KnowledgeAnalyticsMetrics {
  qaCount: number;
  retrievalCount: number;
  fileRetrievalCount: number;
  chunkHitCount: number;
  noHitCount: number;
  noHitRate: number;
  averageLatencyMs: number;
}

export interface KnowledgeAnalyticsTrend {
  date: string;
  qaCount: number;
  retrievalCount: number;
  fileRetrievalCount: number;
  chunkHitCount: number;
  noHitCount: number;
}

export interface KnowledgeAnalyticsRanking {
  id: string;
  knowledgeId?: string;
  name: string;
  qaCount: number;
  retrievalCount: number;
  fileRetrievalCount: number;
  chunkHitCount: number;
  noHitCount: number;
  noHitRate: number;
}

export interface KnowledgeAnalyticsOverview {
  from: string;
  to: string;
  stock: KnowledgeAnalyticsStock;
  metrics: KnowledgeAnalyticsMetrics;
  trend: KnowledgeAnalyticsTrend[];
  knowledgeBases: KnowledgeAnalyticsRanking[];
  documents: KnowledgeAnalyticsRanking[];
  chunks: KnowledgeAnalyticsRanking[];
}
