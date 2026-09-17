import fs from 'fs';
import path from 'path';

describe('agent run knowledge image layout', () => {
  const source = fs.readFileSync(path.join(__dirname, 'RunAssistantMarkdown.vue'), 'utf8');

  it('preserves intrinsic ratio and gives maps and QR codes different display widths', () => {
    expect(source).toContain('const ratio = width / height;');
    expect(source).toContain("ratio >= 1.2 ? 'run-image-landscape'");
    expect(source).toContain("ratio >= 0.82 ? 'run-image-square'");
    expect(source).toMatch(/img\[data-run-image\][\s\S]*?height:\s*auto;/);
    expect(source).toMatch(/img\.run-image-landscape\) \{ width: min\(100%, 640px\); \}/);
    expect(source).toMatch(/img\.run-image-square\) \{ width: min\(100%, 320px\); \}/);
  });

  it('opens knowledge images in an accessible full-screen preview', () => {
    expect(source).toContain('<Teleport to="body">');
    expect(source).toContain('role="dialog"');
    expect(source).toContain('aria-modal="true"');
    expect(source).toContain("closest<HTMLImageElement>('img[data-run-image]')");
    expect(source).toContain("event.key === 'Escape'");
    expect(source).toContain('cursor: zoom-in;');
    expect(source).toContain('@click="closeImagePreview"');
    expect(source).toContain('@click.stop="closeImagePreview"');
  });
});
