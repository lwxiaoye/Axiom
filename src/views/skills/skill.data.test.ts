import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('Skill list columns', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/views/skills/skill.data.ts'), 'utf8');

  it('hides author and install status from the table columns', () => {
    expect(source).not.toContain("title: '作者'");
    expect(source).not.toContain("dataIndex: 'author'");
    expect(source).not.toContain("title: '安装状态'");
    expect(source).not.toContain("dataIndex: 'installStatus'");
  });
});
