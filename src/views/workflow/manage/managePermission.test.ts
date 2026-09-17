import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('workflow manage navigation separation', () => {
  it('keeps skin management out of the intelligent-agent application page', () => {
    const source = readFileSync(resolve(__dirname, 'index.vue'), 'utf8');

    expect(source).toContain('<BasicTable @register="registerTable">');
    expect(source).not.toContain('PresentationStudioPanel');
    expect(source).not.toContain('PresentationGrantPanel');
    expect(source).not.toContain('外观授权');
  });
});
