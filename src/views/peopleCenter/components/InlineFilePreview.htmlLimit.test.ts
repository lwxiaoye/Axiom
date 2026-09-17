import fs from 'node:fs';
import path from 'node:path';

describe('InlineFilePreview HTML size contract', () => {
  it('keeps self-contained HTML previewable up to the persisted file ceiling', () => {
    const source = fs.readFileSync(path.resolve(__dirname, 'InlineFilePreview.vue'), 'utf8');

    expect(source).toContain('const MAX_HTML_BYTES = 15 * 1024 * 1024;');
    expect(source).toContain("return size <= MAX_HTML_BYTES ? 'html' : null;");
    expect(source).toContain("return size <= MAX_TEXT_BYTES ? 'markdown' : null;");
  });
});
