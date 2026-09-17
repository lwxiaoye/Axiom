import { defHttp } from '/@/utils/http/axios';
export { buildChannelPayload, buildModelPayload, joinTags, splitTags } from './modelGatewayPayload';
export type { ChannelFormState, GatewayStatus, ModelFormState } from './modelGatewayPayload';

export const Api = {
  models: '/model-gateway/admin/models',
  channels: '/model-gateway/admin/channels',
  validate: '/model-gateway/admin/config/validate',
  publish: '/model-gateway/admin/config/publish',
} as const;

export interface ModelView {
  id: string;
  publicName: string;
  displayName: string;
  iconUrl?: string;
  description?: string;
  tags?: string[];
  primaryType: string;
  capabilities?: string[];
  contextWindow: number;
  maxOutputTokens: number;
  tokenizer?: string;
  status: 'ENABLED' | 'DISABLED';
}

export interface ChannelCredential {
  masked?: string;
  version?: number;
  status?: string;
}

export interface ChannelView {
  id: string;
  providerType: string;
  name: string;
  baseUrl: string;
  region?: string;
  proxyEnabled: boolean;
  proxyUrl?: string;
  connectTimeoutMs: number;
  readTimeoutMs: number;
  configJson?: string;
  status: 'ENABLED' | 'DISABLED';
  credential?: ChannelCredential;
}

export interface PublishedConfig {
  version: number;
  checksum: string;
  operatorId: string;
  publishedAt: string;
  snapshot?: {
    models?: unknown[];
    channels?: unknown[];
    bindings?: unknown[];
    routePolicies?: unknown[];
    quotaScopes?: string[];
  };
}

const QUIET_ERROR = { errorMessageMode: 'none' } as const;

export const listGatewayModels = () => defHttp.get<ModelView[]>({ url: Api.models }, QUIET_ERROR);
export const createGatewayModel = (params: Record<string, unknown>) => defHttp.post<ModelView>({ url: Api.models, params });
export const updateGatewayModel = (id: string, params: Record<string, unknown>) =>
  defHttp.put<ModelView>({ url: `${Api.models}/${id}`, params });
export const deleteGatewayModel = (id: string) => defHttp.delete({ url: `${Api.models}/${id}` });

export const listGatewayChannels = () => defHttp.get<ChannelView[]>({ url: Api.channels }, QUIET_ERROR);
export const createGatewayChannel = (params: Record<string, unknown>) => defHttp.post<ChannelView>({ url: Api.channels, params });
export const updateGatewayChannel = (id: string, params: Record<string, unknown>) =>
  defHttp.put<ChannelView>({ url: `${Api.channels}/${id}`, params });
export const deleteGatewayChannel = (id: string) => defHttp.delete({ url: `${Api.channels}/${id}` });

export const validateGatewayConfig = () => defHttp.post<{ valid: boolean }>({ url: Api.validate });
export const publishGatewayConfig = () => defHttp.post<PublishedConfig>({ url: Api.publish });
