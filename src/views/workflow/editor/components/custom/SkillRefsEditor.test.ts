import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('SkillRefsEditor', () => {
  it('loads personal skills and system skills from the separate sources', () => {
    const source = readFileSync(resolve(__dirname, 'SkillRefsEditor.vue'), 'utf8');

    expect(source).toContain("getAgentSkillList({ source: 'personal' })");
    expect(source).toContain('getSkillMarketList()');
    expect(source).toContain("source: 'mine' as AgentSkillSource");
    expect(source).toContain("source: 'system' as AgentSkillSource");
    expect(source).toContain('<a-modal');
    expect(source).toContain('tempSkills');
    expect(source).toContain('confirmPicker');
    expect(source).toContain('padding: 4px 4px 6px');
    expect(source).toContain('padding: 14px 4px 2px');
    expect(source).toContain('min-width: 76px');
  });
});
