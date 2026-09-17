import { readFileSync } from 'fs';
import { resolve } from 'path';

describe('agent skill workbench panel', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/workbench/SkillsPanel.vue'), 'utf8');
  const myAgentsSource = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/MyAgentsTab.vue'), 'utf8');

  it('keeps skill cards source-agnostic and hides status labels', () => {
    expect(source).not.toContain("skill.source === 'system' ? '系统技能' : '自定义技能'");
    expect(source).not.toContain('statusText(skill)');
    expect(source).not.toContain('statusClass(skill)');
  });

  it('supports editing personal skills through the existing skill edit API', () => {
    expect(source).toContain('updateAgentSkill');
    expect(source).toContain('openEdit(skill)');
    expect(source).toContain('title="编辑技能"');
  });

  it('loads only personal skills in the user skill workbench', () => {
    expect(source).toContain("getAgentSkillList({ source: 'personal' })");
  });

  it('prefetches only personal skills for the my-agent navigation count', () => {
    expect(myAgentsSource).toContain("getAgentSkillList({ source: 'personal' })");
  });
});
