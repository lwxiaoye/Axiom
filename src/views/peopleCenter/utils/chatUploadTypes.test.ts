import { CHAT_UPLOAD_ACCEPT, chatUploadFileError } from './chatUploadTypes';

describe('chatUploadFileError', () => {
  it('allows bidding formats plus csv/json', () => {
    expect(chatUploadFileError({ name: 'a.pdf' })).toBe('');
    expect(chatUploadFileError({ name: 'a.docx' })).toBe('');
    expect(chatUploadFileError({ name: 'a.pptx' })).toBe('');
    expect(chatUploadFileError({ name: 'a.xlsx' })).toBe('');
    expect(chatUploadFileError({ name: 'a.txt' })).toBe('');
    expect(chatUploadFileError({ name: 'a.md' })).toBe('');
    expect(chatUploadFileError({ name: 'a.html' })).toBe('');
    expect(chatUploadFileError({ name: 'a.csv' })).toBe('');
    expect(chatUploadFileError({ name: 'a.json' })).toBe('');
    expect(chatUploadFileError({ name: 'a.png' })).toBe('');
  });

  it('rejects zip/exe/code with a visible format hint', () => {
    expect(chatUploadFileError({ name: 'payload.zip' })).toContain('不支持「payload.zip」');
    expect(chatUploadFileError({ name: 'setup.exe' })).toContain('请上传');
    expect(chatUploadFileError({ name: 'main.py' })).toContain('不支持');
  });

  it('tells users to resave legacy Office binaries', () => {
    expect(chatUploadFileError({ name: 'old.doc' })).toContain('另存为 .docx');
    expect(chatUploadFileError({ name: 'old.ppt' })).toContain('另存为 .pptx');
    expect(chatUploadFileError({ name: 'old.xls' })).toContain('另存为 .xlsx');
  });

  it('allows clipboard images that only have a MIME type', () => {
    expect(chatUploadFileError({ name: 'image', type: 'image/png' })).toBe('');
    expect(chatUploadFileError({ name: 'paste', type: 'image/jpeg' })).toBe('');
  });

  it('keeps html in the file picker accept list', () => {
    expect(CHAT_UPLOAD_ACCEPT).toContain('.html');
    expect(CHAT_UPLOAD_ACCEPT).not.toContain('image/*');
    expect(CHAT_UPLOAD_ACCEPT).not.toContain('text/*');
  });
});
