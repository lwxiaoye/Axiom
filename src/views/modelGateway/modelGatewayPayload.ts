export type GatewayStatus = 'ENABLED' | 'DISABLED';

export interface ModelFormState {
  publicName?: string;
  displayName?: string;
  iconUrl?: string;
  description?: string;
  tagsText?: string;
  primaryType?: string;
  capabilitiesText?: string;
  contextWindow?: number;
  maxOutputTokens?: number;
  tokenizer?: string;
  status?: GatewayStatus;
}

export interface ChannelFormState {
  providerType?: string;
  name?: string;
  baseUrl?: string;
  region?: string;
  proxyEnabled?: boolean;
  proxyUrl?: string;
  connectTimeoutMs?: number;
  readTimeoutMs?: number;
  configJson?: string;
  status?: GatewayStatus;
  secret?: string;
}

function cleanText(value: unknown) {
  const text = String(value ?? '').trim();
  return text || undefined;
}

export function splitTags(value?: string | string[] | Set<string> | null): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (value instanceof Set) {
    return Array.from(value).map((item) => String(item).trim()).filter(Boolean);
  }
  return String(value ?? '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function joinTags(value?: string[] | Set<string> | null) {
  return splitTags(value).join(', ');
}

export function buildModelPayload(form: ModelFormState) {
  return {
    publicName: cleanText(form.publicName),
    displayName: cleanText(form.displayName),
    iconUrl: cleanText(form.iconUrl),
    description: cleanText(form.description),
    tags: splitTags(form.tagsText),
    primaryType: form.primaryType,
    capabilities: splitTags(form.capabilitiesText),
    contextWindow: form.contextWindow,
    maxOutputTokens: form.maxOutputTokens,
    tokenizer: cleanText(form.tokenizer),
    status: form.status || 'ENABLED',
  };
}

export function buildChannelPayload(form: ChannelFormState) {
  const payload: Record<string, unknown> = {
    providerType: form.providerType,
    name: cleanText(form.name),
    baseUrl: cleanText(form.baseUrl),
    region: cleanText(form.region),
    proxyEnabled: Boolean(form.proxyEnabled),
    proxyUrl: cleanText(form.proxyUrl),
    connectTimeoutMs: form.connectTimeoutMs,
    readTimeoutMs: form.readTimeoutMs,
    configJson: cleanText(form.configJson) || '{}',
    status: form.status || 'ENABLED',
  };
  const secret = cleanText(form.secret);
  if (secret) {
    payload.secret = secret;
  }
  return payload;
}
