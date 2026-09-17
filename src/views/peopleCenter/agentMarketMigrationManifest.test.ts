import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('agent market category migration manifest', () => {
  it('covers the current 23 applications exactly once with the planned distribution', () => {
    const manifest = JSON.parse(
      readFileSync(
        resolve(process.cwd(), 'agent-api/scripts/agent_market_category_manifest.json'),
        'utf8',
      ),
    ) as Record<string, string[]>;
    const names = Object.values(manifest).flat();

    expect(Object.keys(manifest)).toEqual([
      'communication',
      'document_knowledge',
      'data_table',
      'content_creation',
      'planning_structure',
      'developer_automation',
      'image_multimedia',
      'other',
    ]);
    expect(Object.values(manifest).map((items) => items.length)).toEqual([7, 7, 4, 3, 1, 1, 0, 0]);
    expect(names).toHaveLength(23);
    expect(new Set(names).size).toBe(23);
  });
});
