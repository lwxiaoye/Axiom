import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const pickerSource = readFileSync(resolve(__dirname, 'RunPresentationPicker.vue'), 'utf8');
const apiSource = readFileSync(resolve(__dirname, '../../../workflow/api/presentation.api.ts'), 'utf8');
const studioSource = readFileSync(resolve(__dirname, '../../../workflow/manage/PresentationStudioPanel.vue'), 'utf8');
const skinPageSource = readFileSync(resolve(__dirname, '../../../workflow/skins/index.vue'), 'utf8');
const staticRoutesSource = readFileSync(resolve(__dirname, '../../../../router/routes/staticRouter.ts'), 'utf8');

describe('global sub-agent skin catalog', () => {
  it('loads the global catalog and falls back safely when the catalog cannot be read', () => {
    expect(pickerSource).toContain('queryAvailablePresentationPresets(props.appId || undefined)');
    expect(pickerSource).toContain('全局皮肤目录');
    expect(pickerSource).toContain('未删除的皮肤包');
    expect(pickerSource).toContain('if (!record.portableSkin || visible.some((option) => option.key === record.key)) continue;');
    expect(pickerSource).toContain(':disabled="disabled || !option.available"');
    expect(pickerSource).not.toContain('authorized');
    expect(pickerSource).not.toContain('当前客户');
  });

  it('exposes global CRUD package operations without tenant and grant APIs', () => {
    expect(apiSource).toContain('/agent-api/workflow/presentation/presets');
    expect(apiSource).toContain('/agent-api/workflow/presentation/admin/skins/import');
    expect(apiSource).toContain('/agent-api/workflow/presentation/admin/skins/');
    expect(apiSource).not.toContain('/agent-api/workflow/presentation/admin/tenants');
    expect(apiSource).not.toContain('/agent-api/workflow/presentation/admin/grant');
    expect(apiSource).not.toContain('tenantId');
  });

  it('子智能体皮肤是独立导航页，并保留 CRUD 和三端验样', () => {
    expect(staticRoutesSource).toContain("path: '/workflow/skins'");
    expect(staticRoutesSource).toContain('SubAgentSkinManagePage');
    expect(skinPageSource).toContain('<PresentationStudioPanel />');
    expect(skinPageSource).not.toContain('全局皮肤库');
    expect(skinPageSource).not.toContain('不按租户拆分·不需单独授权');
    expect(studioSource).toContain('导入皮肤包');
    expect(studioSource).toContain('@click="openDetail"');
    expect(studioSource).toContain('搜索名称或标识');
    expect(studioSource).toContain('exportSubAgentSkin');
    expect(studioSource).toContain('deleteSubAgentSkin');
    expect(studioSource).toContain("const selectedKey = ref('')");
    expect(studioSource).not.toContain("sourceType.startsWith('builtin')");
    expect(studioSource).toContain("type Viewport = 'desktop' | 'tablet' | 'mobile'");
    expect(studioSource).not.toContain('选择皮肤所属客户');
    expect(studioSource).toContain('runtime.sidebarDecoration');
    expect(studioSource).toContain('runtime.inspirationDecoration');
    expect(studioSource).toContain("const compactPanel = ref<'none' | 'left' | 'right'>('none')");
    expect(studioSource).toContain("'show-left': compactPanel === 'left'");
    expect(studioSource).toContain("'show-right': compactPanel === 'right'");
    expect(studioSource).toContain('class="studio-compact-backdrop"');
    expect(studioSource).toContain('aria-label="打开会话列表"');
    expect(studioSource).not.toContain('<span>会话</span>');
    expect(studioSource).not.toContain('选择后可直接验样');
    expect(studioSource).not.toContain('始终可用');
    expect(studioSource).toContain('.studio-run-shell.is-tablet .studio-compact-bar');
  });
});
