import {
  isKnowledgeDocumentProcessing,
  knowledgeDocumentStatusText,
  shouldShowKnowledgeDocumentError,
} from './knowledgeStatus';

describe('knowledge document status', () => {
  it.each([
    ['QA_GENERATING', '问答生成中'],
  ] as const)('maps %s to %s', (status, text) => {
    expect(knowledgeDocumentStatusText(status)).toBe(text);
    expect(isKnowledgeDocumentProcessing(status)).toBe(true);
  });

  it('does not retain a failure indicator after a document becomes ready', () => {
    expect(shouldShowKnowledgeDocumentError('READY', '上一轮向量化失败')).toBe(false);
    expect(shouldShowKnowledgeDocumentError('FAILED', '向量化失败')).toBe(true);
  });
});
