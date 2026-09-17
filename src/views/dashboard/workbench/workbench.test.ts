import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('workbench performance overview', () => {
  const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

  it('shows CPU and memory monitoring instead of model and retrieval analytics', () => {
    const source = read('src/views/dashboard/workbench/index.vue');

    expect(source).toContain('CPU 使用情况');
    expect(source).toContain('内存使用情况');
    expect(source).toContain("getServerInfo('1')");
    expect(source).toContain("getServerInfo('5')");
    expect(source).not.toContain('模型调用趋势');
    expect(source).not.toContain('知识检索质量');
  });

  it('uses token management in the capability topology and hides theme settings', () => {
    const workbench = read('src/views/dashboard/workbench/index.vue');
    const settings = read('src/settings/projectSetting.ts');

    expect(workbench).toContain("['令牌管理', '统一管理访问令牌', '23', 'ant-design:key-outlined', '#2778ff', '#edf5ff', '/newapi/token']");
    expect(settings).toContain('"showSettingButton": false');
    expect(settings).toContain('"showDarkModeToggle": false');
  });
});
