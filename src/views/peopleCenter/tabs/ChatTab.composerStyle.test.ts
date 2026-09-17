import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const source = readFileSync(resolve(__dirname, 'ChatTab.vue'), 'utf8');

describe('ChatTab composer profile styling', () => {
  it('keeps the shared composer border when Plan Profile is selected', () => {
    expect(source).toContain("'task-mode-active': planMode");
    expect(source).not.toMatch(
      /\.composer\.task-mode-active\s*\{[\s\S]{0,180}border-color/,
    );
  });
});

