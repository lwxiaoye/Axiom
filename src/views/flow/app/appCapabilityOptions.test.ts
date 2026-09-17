import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { AGENT_CAPABILITY_DEFINITIONS } from '@/views/peopleCenter/agentMarketCapabilities';
import { APP_CAPABILITY_OPTIONS, isAppCapabilityValue } from './appCapabilityOptions';

describe('系统设置应用能力分类', () => {
  it('与智能体广场的标签值、文案和顺序完全一致', () => {
    expect(APP_CAPABILITY_OPTIONS).toEqual(
      AGENT_CAPABILITY_DEFINITIONS.map(({ key, label }) => ({ value: key, label })),
    );
    expect(APP_CAPABILITY_OPTIONS.map((item) => item.label)).toEqual([
      '沟通交互',
      '文档与知识',
      '数据与表格',
      '内容创作',
      '规划与结构',
      '开发与自动化',
      '图像与多媒体',
      '综合/其他',
    ]);
  });

  it('不再接受旧的办公 AI、推荐、视频或音频分类值', () => {
    expect(isAppCapabilityValue('communication')).toBe(true);
    expect(isAppCapabilityValue('image_multimedia')).toBe(true);
    expect(isAppCapabilityValue('work')).toBe(false);
    expect(isAppCapabilityValue('top')).toBe(false);
    expect(isAppCapabilityValue('video')).toBe(false);
    expect(isAppCapabilityValue('audio')).toBe(false);
  });

  it('系统设置编辑弹窗不再从 app_category 通用字典取选项', () => {
    const editorSource = readFileSync(resolve(__dirname, 'components/chooseModel.vue'), 'utf8');
    const schemaSource = readFileSync(resolve(__dirname, 'AppInfo.data.ts'), 'utf8');

    expect(editorSource).toContain(':options="APP_CAPABILITY_OPTIONS"');
    expect(editorSource).toContain('optionFilterProp="label"');
    expect(editorSource).not.toContain('dictCode="app_category"');
    expect(schemaSource).toContain('options: APP_CAPABILITY_OPTIONS');
    expect(schemaSource).not.toContain("dictCode: 'app_category'");
  });
});
