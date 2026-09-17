import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const panel = readFileSync(resolve(__dirname, 'AgentsPanel.vue'), 'utf8');
const tab = readFileSync(resolve(__dirname, '../tabs/MyAgentsTab.vue'), 'utf8');
const page = readFileSync(resolve(__dirname, '../pages/MyAgentsPage.vue'), 'utf8');
const routes = readFileSync(resolve(process.cwd(), 'src/router/routes/mainOut.ts'), 'utf8');

describe('AgentsPanel monitoring navigation', () => {
  it('uses the card as the only editor entry and keeps preview out of the overflow menu', () => {
    expect(panel).toContain("@click=\"emit('openDesigner', item)\"");
    expect(panel).not.toContain('key="designer"');
    expect(panel).not.toContain('key="preview"');
  });

  it('limits monitoring navigation to application owners', () => {
    expect(panel).toContain('v-if="isOwner(item)" key="metrics"');
    expect(panel).toContain("emit('open-metrics', item)");
    expect(panel).toContain("key === 'metrics'");
    expect(tab).toContain("@open-metrics=\"emit('openMetrics', $event)\"");
    expect(tab).toContain("(e: 'openMetrics', item: any): void;");
    expect(page).toContain('@open-metrics="openMetrics"');
    expect(routes).toContain("path: 'my-agent/:appId/metrics'");
  });
});
