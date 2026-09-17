import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('Skill network install removal', () => {
  const root = process.cwd();
  const listSource = readFileSync(resolve(root, 'src/views/skills/SkillList.vue'), 'utf8');
  const apiSource = readFileSync(resolve(root, 'src/views/skills/skill.api.ts'), 'utf8');

  it('removes the network install entry from the Skill list page', () => {
    expect(listSource).not.toContain('SkillInstallUrlModal');
    expect(listSource).not.toContain('installOpen');
    expect(listSource).not.toContain('网络安装');
  });

  it('removes the network install API wrapper and modal component', () => {
    expect(apiSource).not.toContain('installFromUrl');
    expect(apiSource).not.toContain('/ai/skill/installFromUrl');
    expect(existsSync(resolve(root, 'src/views/skills/components/SkillInstallUrlModal.vue'))).toBe(false);
  });
});
