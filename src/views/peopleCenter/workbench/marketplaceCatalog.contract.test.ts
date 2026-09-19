import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const read = (relative: string) => readFileSync(resolve(__dirname, relative), 'utf8');

const market = read('../composables/useAgentMarket.ts');
const api = read('../../workflow/api/workflow.api.ts');

describe('marketplace catalog includes published self-built agents', () => {
  it('feeds published self-built agents into the marketplace next to builtin assistants', () => {
    expect(market).toContain('queryMarketplaceWorkflowApps()');
    expect(market).toContain('mergeMarketplaceApps(builtinApps, normalizeAppListResponse(appResult.value), workflowApps)');
    expect(market).toContain("myAppList({ column: 'createTime', order: 'desc' })");
    expect(market).toContain('reloadOptions?.force');
  });

  it('reads the catalog from agent-api instead of the auth-api stub', () => {
    expect(api).toContain("marketplaceApps = '/agent-api/workflow/app/marketplace'");
    expect(market).toContain("import { queryMarketplaceWorkflowApps } from '../../workflow/api/workflow.api';");
  });
});
