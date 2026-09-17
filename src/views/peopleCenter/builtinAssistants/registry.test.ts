import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  CAMPUS_UI_FLAGS,
  decorateBuiltinCatalogApp,
  getBuiltinAssistantById,
  getBuiltinAssistantByPreset,
  getBuiltinUiFlags,
  getBuiltinUiPolicy,
  isBuiltinAssistant,
  listBuiltinAssistants,
  listRecommendedBuiltinAssistants,
  sortBuiltinCatalogApps,
} from './index';
import { BUILTIN_ASSISTANT_MODULES } from './modules';

describe('builtin assistant registry', () => {
  it('resolves every registered module through the shared identity and UI interfaces', () => {
    for (const module of BUILTIN_ASSISTANT_MODULES) {
      expect(getBuiltinAssistantByPreset(module.assistant.preset)).toBe(module.assistant);
      expect(getBuiltinUiFlags(module.assistant.uiPolicy)).toBe(module.ui);
      expect(getBuiltinUiPolicy(module.assistant.preset)).toBe(module.ui);
    }
    expect(getBuiltinAssistantByPreset('unknown')).toBeUndefined();
    expect(getBuiltinUiPolicy(undefined)).toBeUndefined();
    for (const key of ['appId', 'preset', 'route', 'uiPolicy'] as const) {
      expect(new Set(BUILTIN_ASSISTANT_MODULES.map((module) => module.assistant[key])).size)
        .toBe(BUILTIN_ASSISTANT_MODULES.length);
    }
  });

  it('registers campus and presentation by stable id and preset, not by display name', () => {
    const campus = getBuiltinAssistantByPreset('campus_services');
    const presentation = getBuiltinAssistantById('builtin:presentation');
    expect(campus?.appId).toBe('builtin:campus-services');
    expect(campus?.uiPolicy).toBe('campus_readonly');
    expect(campus?.mascotVariant).toBe('campus');
    expect(CAMPUS_UI_FLAGS.hideComposerMascot).toBe(false);
    expect(CAMPUS_UI_FLAGS.showAvatar).toBe(false);
    expect(CAMPUS_UI_FLAGS.hideModelSelector).toBe(true);
    expect(CAMPUS_UI_FLAGS.imageOnlyUpload).toBe(true);
    expect(campus?.legacyIcon).toBe('/agent-icons/campus-services.png');
    expect(campus?.icon).toBe('/agent-icons/builtin/campus-services.png');
    expect(campus?.route).toBe('/center/chat/campus');
    expect(presentation?.preset).toBe('presentation');
    expect(presentation?.uiPolicy).toBe('presentation_authoring');
    expect(presentation?.route).toBe('/center/chat/ppt');
    expect(presentation?.legacyIcon).toBe('/agent-icons/presentation-assistant.png');
    expect(isBuiltinAssistant({ id: 'builtin:campus-services', appName: '其他名字' })).toBe(true);
    expect(isBuiltinAssistant({ id: 'random', appName: '校园百事通' })).toBe(false);
    expect(isBuiltinAssistant('presentation')).toBe(true);
    expect(isBuiltinAssistant({
      id: 'tenant-specific-id',
      formOptions: JSON.stringify({ builtinPreset: 'presentation', runtimeKind: 'harness_builtin' }),
    })).toBe(true);
    expect(isBuiltinAssistant({ id: 'tenant-specific-id', pcUrl: '/center/chat/ppt' })).toBe(true);
  });

  it('keeps campus first in the recommended builtin order', () => {
    expect(listRecommendedBuiltinAssistants().map((item) => item.preset)).toEqual([
      'campus_services',
      'presentation',
      'interview',
    ]);
    expect(listBuiltinAssistants().every((item) => item.entryMode === 'standalone')).toBe(true);
  });

  it('decorates only an administrator-returned route with default photo and description', () => {
    const creatorFields = {
      createByName: '吴少然',
      createByAvatar: '/avatar/creator.png',
    };
    expect(decorateBuiltinCatalogApp({
      id: 'manual-ppt',
      appName: '自定义演示助手',
      appIcon: '',
      appRemark: '',
      pcUrl: '/center/chat/ppt',
      ...creatorFields,
    })).toEqual(expect.objectContaining({
      id: 'manual-ppt',
      appName: '自定义演示助手',
      appIcon: '/agent-icons/builtin/presentation-assistant.png',
      appRemark: '制作、优化并交付可编辑演示文稿',
      builtinPreset: 'presentation',
      ...creatorFields,
    }));
    expect(decorateBuiltinCatalogApp({
      id: 'manual-campus',
      appName: '校园百事通',
      appIcon: '/uploaded/custom.png',
      appRemark: '管理员自定义说明',
      pcUrl: '/center/chat/campus',
    })).toEqual(expect.objectContaining({
      appIcon: '/uploaded/custom.png',
      appRemark: '管理员自定义说明',
    }));
    expect(decorateBuiltinCatalogApp({
      id: 'ordinary',
      appName: '其他应用',
      appIcon: '',
      pcUrl: '/other',
    }).appIcon).toBe('');
  });

  it('pins only returned builtin routes first and keeps all other apps stable', () => {
    const sorted = sortBuiltinCatalogApps([
      { id: 'ordinary-a', appName: '普通 A', pcUrl: '/a' },
      { id: 'ppt', appName: '我的 PPT', pcUrl: '/center/chat/ppt' },
      { id: 'ordinary-b', appName: '普通 B', pcUrl: '/b' },
      { id: 'campus', appName: '我的校园', pcUrl: '/center/chat/campus' },
    ]);
    expect(sorted.map((item) => item.id)).toEqual([
      'campus',
      'ppt',
      'ordinary-a',
      'ordinary-b',
    ]);
  });

  it('keeps the frontend registry free of tool allowlists and execution hooks', () => {
    const source = readFileSync(resolve(__dirname, 'registry.ts'), 'utf8');
    expect(source).not.toContain('search_knowledge');
    expect(source).not.toContain('search_web');
    expect(source).not.toContain('ppt-studio');
    expect(source).not.toContain('systemPrompt');
  });
});
