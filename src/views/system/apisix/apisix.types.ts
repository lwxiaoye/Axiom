export type ApisixRouteStatus = 0 | 1;

export interface ApisixUpstreamNode {
  host: string;
  port: number;
  weight: number;
  priority: number;
}

export interface ApisixPluginConfig {
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
}

export interface ApisixRoute {
  id?: string;
  routeId: string;
  name: string;
  description?: string;
  uris: string[];
  methods: string[];
  hosts: string[];
  priority: number;
  status: ApisixRouteStatus;
  upstreamId?: string;
  upstreamName?: string;
  upstreamType?: string;
  upstreamScheme?: string;
  upstreamHashOn?: string;
  upstreamPassHost?: string;
  nodes?: ApisixUpstreamNode[];
  plugins: ApisixPluginConfig[];
  labels: Record<string, string>;
  timeout: {
    connect: number;
    send: number;
    read: number;
  };
  websocket?: boolean;
  updatedAt?: string;
}

export interface ApisixRouteListResult {
  records: ApisixRoute[];
  total: number;
}

export interface ApisixOverview {
  instanceOnline: number;
  instanceTotal: number;
  totalRoutes: number;
  enabledRoutes: number;
  disabledRoutes: number;
}
