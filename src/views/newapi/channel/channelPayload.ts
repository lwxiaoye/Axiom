type ChannelFormValue = Record<string, any>;

function normalizeModels(models: unknown): string {
  if (Array.isArray(models)) {
    return models.map((item) => String(item).trim()).filter(Boolean).join(',');
  }
  return typeof models === 'string' ? models.split(',').map((item) => item.trim()).filter(Boolean).join(',') : '';
}

function normalizeGroups(channel: ChannelFormValue): string[] {
  if (Array.isArray(channel.groups) && channel.groups.length > 0) {
    return channel.groups.map((item) => String(item).trim()).filter(Boolean);
  }
  const group = typeof channel.group === 'string' && channel.group.trim() ? channel.group.trim() : 'default';
  return group.split(',').map((item) => item.trim()).filter(Boolean);
}

export function buildChannelValue(formState: ChannelFormValue) {
  const groups = normalizeGroups(formState);
  return {
    ...formState,
    models: normalizeModels(formState.models),
    auto_ban: formState.auto_ban ? 1 : 0,
    group: groups.join(',') || 'default',
    groups: groups.length > 0 ? groups : ['default'],
  };
}

export function buildChannelCreatePayload(formState: ChannelFormValue) {
  const channel = buildChannelValue(formState);
  return {
    id: channel.id,
    mode: 'single',
    channel,
  };
}

export function buildChannelUpdatePayload(formState: ChannelFormValue) {
  return buildChannelValue(formState);
}
