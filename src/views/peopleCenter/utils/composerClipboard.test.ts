import { filesFromClipboard, longTextAsPastedFile, pastedTextFilename } from './composerClipboard';

function item(kind: string, file: File | null) {
  return { kind, getAsFile: () => file } as DataTransferItem;
}

describe('composerClipboard', () => {
  it('从 items 取出文件，并给无文件名的截图补名', () => {
    const blob = new File([new Uint8Array([1, 2, 3])], '', { type: 'image/png' });
    const data = {
      items: [item('string', null), item('file', blob)],
      files: [],
    } as unknown as DataTransfer;
    const files = filesFromClipboard(data);
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe('粘贴的图片.png');
    expect(files[0].type).toBe('image/png');
  });

  it('items 没有文件时回退 clipboardData.files', () => {
    const file = new File(['x'], 'a.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    const data = {
      items: [],
      files: [file],
    } as unknown as DataTransfer;
    expect(filesFromClipboard(data).map((item) => item.name)).toEqual(['a.docx']);
  });

  it('短文本不转附件，长文本转 txt', () => {
    expect(longTextAsPastedFile('讲一下这个文件')).toBeNull();
    const wall = Array.from({ length: 20 }, (_, i) => `line ${i} ${'x'.repeat(40)}`).join('\n');
    const asFile = longTextAsPastedFile(wall);
    expect(asFile?.type).toBe('text/plain');
    expect(asFile?.name.startsWith('粘贴的文本-')).toBe(true);
    expect(pastedTextFilename('a/b:c')).toBe('粘贴的文本-abc.txt');
  });
});
