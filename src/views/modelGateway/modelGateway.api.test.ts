import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { buildChannelPayload, buildModelPayload, splitTags } from './modelGatewayPayload';

describe('model gateway admin frontend contract', () => {
  const apiSource = readFileSync(resolve(process.cwd(), 'src/views/modelGateway/modelGateway.api.ts'), 'utf8');

  it('uses the Java model gateway admin endpoints', () => {
    expect(apiSource).toContain("models: '/model-gateway/admin/models'");
    expect(apiSource).toContain("channels: '/model-gateway/admin/channels'");
    expect(apiSource).toContain("validate: '/model-gateway/admin/config/validate'");
    expect(apiSource).toContain("publish: '/model-gateway/admin/config/publish'");
  });

  it('normalizes comma separated lists for model payloads', () => {
    expect(
      buildModelPayload({
        publicName: 'gpt-4o-mini',
        displayName: 'GPT-4o mini',
        primaryType: 'CHAT',
        capabilitiesText: 'CHAT, VISION,,JSON',
        tagsText: 'fast, default',
        contextWindow: 128000,
        maxOutputTokens: 16384,
        status: 'ENABLED',
      })
    ).toMatchObject({
      publicName: 'gpt-4o-mini',
      capabilities: ['CHAT', 'VISION', 'JSON'],
      tags: ['fast', 'default'],
    });
  });

  it('omits blank write-only channel secrets on update', () => {
    expect(
      buildChannelPayload({
        providerType: 'OPENAI_COMPATIBLE',
        name: 'local',
        baseUrl: 'http://127.0.0.1:11434/v1',
        proxyEnabled: false,
        connectTimeoutMs: 10000,
        readTimeoutMs: 60000,
        configJson: '{}',
        status: 'ENABLED',
        secret: '',
      })
    ).not.toHaveProperty('secret');
  });

  it('trims and drops empty list items', () => {
    expect(splitTags(' a, ,b ,, c ')).toEqual(['a', 'b', 'c']);
  });
});
