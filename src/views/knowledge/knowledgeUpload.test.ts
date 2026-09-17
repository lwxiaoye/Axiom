import {
  DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS,
  knowledgeUploadFileError,
  originFileFromUploadItem,
  resolveCreatedKnowledgeId,
  unwrapKnowledgeUploadResponse,
} from './knowledgeUpload';

describe('knowledgeUpload', () => {
  it('accepts supported document types within 50 MB', () => {
    expect(knowledgeUploadFileError({ name: '手册.pdf', size: 1024 })).toBe('');
    expect(knowledgeUploadFileError({ name: 'note.MD', size: 2048 })).toBe('');
    expect(knowledgeUploadFileError({ name: 'readme.markdown', size: 4096 })).toBe('');
  });

  it('rejects unsupported types, empty files, and oversized files', () => {
    expect(knowledgeUploadFileError({ name: 'photo.png', size: 1024 })).toContain('仅支持');
    expect(knowledgeUploadFileError({ name: 'empty.pdf', size: 0 })).toContain('为空');
    expect(knowledgeUploadFileError({ name: 'huge.docx', size: 50 * 1024 * 1024 + 1 })).toContain('50 MB');
  });

  it('resolves created knowledge ids from common backend shapes', () => {
    expect(resolveCreatedKnowledgeId('kb-1')).toBe('kb-1');
    expect(resolveCreatedKnowledgeId({ id: 'kb-2' })).toBe('kb-2');
    expect(resolveCreatedKnowledgeId({ knowledgeId: 'kb-3' })).toBe('kb-3');
    expect(resolveCreatedKnowledgeId({ result: { id: 'kb-4' } })).toBe('kb-4');
    expect(resolveCreatedKnowledgeId(null)).toBe('');
  });

  it('unwraps upload payloads and surfaces failed responses', () => {
    expect(unwrapKnowledgeUploadResponse({ result: { id: 'doc-1' } })).toEqual({ id: 'doc-1' });
    expect(unwrapKnowledgeUploadResponse({ id: 'doc-2' })).toEqual({ id: 'doc-2' });
    expect(() => unwrapKnowledgeUploadResponse({ success: false, message: '解析失败' })).toThrow('解析失败');
  });

  it('reads the original File from upload list items', () => {
    const file = new File(['hello'], 'a.txt', { type: 'text/plain' });
    expect(originFileFromUploadItem(file)).toBe(file);
    expect(originFileFromUploadItem({ originFileObj: file })).toBe(file);
    expect(originFileFromUploadItem({ name: 'a.txt' })).toBeUndefined();
  });

  it('uses paragraph chunking as the create-time default', () => {
    expect(DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS.splitStrategy).toBe('PARAGRAPH');
    expect(DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS.processingMode).toBe('CHUNK');
    expect(DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS).not.toHaveProperty('autoQuestionIndex');
    expect(DEFAULT_KNOWLEDGE_UPLOAD_OPTIONS).not.toHaveProperty('indexSize');
  });
});
