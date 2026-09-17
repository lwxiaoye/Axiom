import { decorateAppsWithModelAvailability, getMissingModels, parseUseModels } from './agentModelRequirements';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

describe('parseUseModels', () => {
  it('splits comma-separated model ids, trims whitespace, and removes duplicates', () => {
    expect(parseUseModels(' gpt-4o, gpt-4o-mini ,,gpt-4o ')).toEqual(['gpt-4o', 'gpt-4o-mini']);
  });

  it('returns no models for non-string values', () => {
    expect(parseUseModels(undefined)).toEqual([]);
    expect(parseUseModels(['gpt-4o'])).toEqual([]);
  });
});

describe('getMissingModels', () => {
  it('returns required models unavailable to the authenticated user', () => {
    expect(getMissingModels('gpt-4o, gpt-4o-mini, claude-3', ['gpt-4o', 'claude-3'])).toEqual(['gpt-4o-mini']);
  });

  it('returns no missing models when the app does not declare requirements', () => {
    expect(getMissingModels('', [])).toEqual([]);
  });
});

describe('decorateAppsWithModelAvailability', () => {
  it('suppresses missing-model markers when the model lookup failed', () => {
    expect(decorateAppsWithModelAvailability([
      { id: 'app-1', useModels: 'gpt-4o', missingModels: ['stale-model'] },
    ], null)).toEqual([{ id: 'app-1', useModels: 'gpt-4o' }]);
  });

  it('adds missingModels only to apps with unavailable required models', () => {
    expect(decorateAppsWithModelAvailability([
      { id: 'available', useModels: 'gpt-4o' },
      { id: 'blocked', use_models: 'gpt-4o-mini' },
    ], ['gpt-4o'])).toEqual([
      { id: 'available', useModels: 'gpt-4o' },
      { id: 'blocked', use_models: 'gpt-4o-mini', missingModels: ['gpt-4o-mini'] },
    ]);
  });
});

describe('agent market missing-model badge', () => {
  it('keeps the warning in the title area and leaves the footer arrow unobstructed', () => {
    const template = readFileSync(resolve(process.cwd(), 'src/views/peopleCenter/tabs/AgentMarketTab.vue'), 'utf8');

    expect(template).toContain('app-model-warning');
    expect(template).toContain('app-card-heading-row');
    expect(template).toContain('ExclamationCircleOutlined');
    expect(template).toContain('ArrowRightOutlined');
    expect(template).not.toContain('model-warning-mark');
  });
});
