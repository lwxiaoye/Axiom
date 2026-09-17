import { buildChannelCreatePayload, buildChannelUpdatePayload } from './channelPayload';

describe('newapi channel payloads', () => {
  const form = {
    id: 1,
    type: 4,
    name: 'Ollama',
    key: '',
    base_url: 'http://127.0.0.1:11434',
    models: ['qwen2.5:7b', 'qwen3-embedding:0.6b-q8_0', 'gpt-3.5-turbo'],
    auto_ban: true,
    group: 'default',
    groups: ['default'],
    priority: 3,
    weight: 2,
  };

  it('keeps create payload wrapped for newapi batch mode contract', () => {
    expect(buildChannelCreatePayload(form)).toMatchObject({
      id: 1,
      mode: 'single',
      channel: {
        id: 1,
        models: 'qwen2.5:7b,qwen3-embedding:0.6b-q8_0,gpt-3.5-turbo',
        auto_ban: 1,
      },
    });
  });

  it('builds update payload as the root channel object for /newapi/api/channel/', () => {
    expect(buildChannelUpdatePayload(form)).toMatchObject({
      id: 1,
      type: 4,
      name: 'Ollama',
      base_url: 'http://127.0.0.1:11434',
      models: 'qwen2.5:7b,qwen3-embedding:0.6b-q8_0,gpt-3.5-turbo',
      auto_ban: 1,
      group: 'default',
      groups: ['default'],
      priority: 3,
      weight: 2,
    });
    expect(buildChannelUpdatePayload(form)).not.toHaveProperty('channel');
    expect(buildChannelUpdatePayload(form)).not.toHaveProperty('mode');
  });
});
