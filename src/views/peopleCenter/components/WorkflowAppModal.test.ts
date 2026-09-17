import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('WorkflowAppModal agent icon upload', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/components/WorkflowAppModal.vue'), 'utf8');

  it('uses file upload instead of a manual icon URL input for agent icons', () => {
    expect(source).toContain('label="智能体图标"');
    expect(source).toContain('<a-upload');
    expect(source).toContain('accept="image/*"');
    expect(source).toContain(':before-upload="beforeIconUpload"');
    expect(source).toContain('@change="handleIconUploadChange"');
    expect(source).toContain('uploadImg({ file }');
    expect(source).toContain('form.appIcon = url');
    expect(source).not.toContain('label="图标地址"');
    expect(source).not.toContain('可填写 http(s) 图片地址');
  });
});
