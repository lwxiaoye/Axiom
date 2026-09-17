/**
 * 外部应用连接器 API（打 Python agent-api /agent-api/connectors/*）。
 *
 * 鉴权头见 utils/agentAuthHeaders.ts。独立成文件，保持连接器模块自包含。
 * 注意：接口回来的对象**永远不含凭据**——服务端只下发账号名、状态、已选资源。
 */
import { agentAuthHeaders } from './utils/agentAuthHeaders';

export type ConnectorAccount = {
  login: string;
  name: string;
  avatar: string;
};

/** 账户列表里的一项。`selected` 与连接器总开关是两个维度：
 *  总开关＝这一轮用不用这个应用；selected＝这个账户算不算在读取范围内。 */
export type ConnectorAccountItem = {
  login: string;
  name: string;
  avatar: string;
  selected: boolean;
  status: string;
  lastError: string;
};

export type ConnectorItem = {
  id: string;
  name: string;
  icon: string;
  summary: string;
  beta: boolean;
  /** provider 本身是否可用（缺配置时为 false，unavailableReason 说明原因） */
  available: boolean;
  unavailableReason: string;
  /** 实际可用的授权方式：oauth（跳转登录授权）/ token（粘令牌） */
  authKinds: ('oauth' | 'token')[];
  /** 声明支持但当前缺配置而用不了的授权方式的说明（空串=没有可说的） */
  authNotice: string;
  resourceKind: string;
  resourceLabel: string;
  resourceHint: string;
  tokenLabel: string;
  tokenHelp: string;
  tokenLink: string;
  /** 详情页「配置 X」的去处（对方账号里管理本连接的页面，不是生成令牌的页面） */
  configLink: string;
  /** 详情弹窗的整段介绍（后端 detail_description：没单独写就退回 summary） */
  description: string;
  /** 详情弹窗的示例提示词卡：整句、可直接抄去用 */
  examplePrompts: string[];
  /** 连接器的提供方（谁做的这个集成），不是应用厂商 */
  author: string;
  homepage: string;
  privacyLink: string;
  /**
   * 详情里「连接器类型」那一格的显示文案（后端按 ProviderSpec.kind 下发：应用 / MCP）。
   * 可选：后端若还没下发（前后端不同步部署时会出现），模板退回「应用」。
   */
  connectorType?: string;
  /** 「更多信息」里的文档链接，只有部分连接器有，模板 v-if 挡 */
  docLink?: string;
  /**
   * 账号型授权（如 QQ 邮箱 IMAP：邮箱地址 + 授权码）第一个输入框的标签与占位。
   * 后端已下发，但**授权表单尚未渲染这两个字段**——QQ 邮箱当前 available=false（适配器未落地），
   * 表单够不着，所以不阻塞。实现双输入框时从这里取，别再自己造字段名。
   */
  accountLabel?: string;
  accountPlaceholder?: string;
  /**
   * 账号字段的期望格式，由后端显式声明（目前只有 'email'，空串=不校验格式只校验非空）。
   * 有这个字段是因为前端两次靠推断都翻了车：先猜 `resourceKind === 'mailbox'`（实际是
   * 'folder'）成了死代码，再从占位文案里找 `@` 又是改文案就会静默失效的隐性耦合。
   */
  accountFormat?: string;
  /** 第一段授权（登录）是否完成 —— 只证明「你是谁」 */
  accountAuthorized: boolean;
  /**
   * 第二段授权（在 GitHub 上安装并勾选仓库）是否完成 —— 决定「能读什么」。
   * 只有第一段时连接器是「连上了却读不到任何东西」，界面必须把这个差别摆出来。
   */
  reposAuthorized: boolean;
  connected: boolean;
  /** 两段都完成且开关打开才算 enabled（后端同口径） */
  enabled: boolean;
  status: string;
  lastError: string;
  account: ConnectorAccount | null;
  /**
   * 已连接的**全部账户**（多账户，2026-07-29）。单账户时也是长度 1 的数组，
   * 前端不必分两套渲染。`account` 字段仍是第一个账户，保留是为了不动既有语义。
   */
  accounts?: ConnectorAccountItem[];
  /** 用户勾选的资源（GitHub 即 owner/repo 全名） */
  resources: string[];
  resourceCount: number;
  toolCount: number;
};

export type ConnectorResource = {
  fullName: string;
  name: string;
  private: boolean;
  description: string;
  language: string;
  updatedAt: string;
};

export type OAuthStart = {
  /** GitHub 登录授权页地址；前端在新窗口打开它 */
  authorizeUrl: string;
};

export type InstallStart = {
  /** GitHub App 安装页地址：用户在 GitHub 上勾选授权哪些仓库 */
  installUrl: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/agent-api/connectors${path}`, {
    ...init,
    headers: agentAuthHeaders({ ...(init?.headers as Record<string, string> | undefined) }),
  });
  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const data = await response.json();
      message = data?.detail || data?.message || message;
    } catch {
      // 非 JSON 响应时保留状态码信息
    }
    throw new Error(message);
  }
  return response.json();
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export async function listConnectors(): Promise<ConnectorItem[]> {
  return request<ConnectorItem[]>('');
}

/**
 * 发起登录授权：拿到授权页地址（state 留在服务端，回调也由服务端落地）。
 * returnTo = 授权完成后要回到的站内地址；服务端存进 state，回调时 303 跳回来。
 */
export async function startOAuth(providerId: string, returnTo: string): Promise<OAuthStart> {
  return request<OAuthStart>(`/${providerId}/oauth/start`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ return_to: returnTo }),
  });
}

/** 发起第二段授权：拿到 App 安装页地址（用户在 GitHub 上选仓库） */
export async function startInstall(providerId: string, returnTo: string): Promise<InstallStart> {
  return request<InstallStart>(`/${providerId}/install/start`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ return_to: returnTo }),
  });
}

/** 授权进行中轮询用：只取单个连接器的最新状态 */
export async function getConnector(providerId: string): Promise<ConnectorItem> {
  return request<ConnectorItem>(`/${providerId}`);
}

/**
 * 令牌式连接。`account` 只有账号型 provider 才传（QQ 邮箱 IMAP 要邮箱地址 + 授权码两样）；
 * 不传时请求体与旧版逐字节一致，GitHub 这类单字段 provider 完全不受影响。
 */
export async function connectWithToken(
  providerId: string,
  token: string,
  account?: string,
): Promise<ConnectorItem> {
  return request<ConnectorItem>(`/${providerId}/token`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(account ? { token, account } : { token }),
  });
}

/** 断开连接。`account` 非空＝只断那一个账户；空＝断掉该连接器的全部账户。 */
export async function disconnectConnector(
  providerId: string,
  account?: string,
): Promise<ConnectorItem> {
  const suffix = account ? `?account=${encodeURIComponent(account)}` : '';
  return request<ConnectorItem>(`/${providerId}${suffix}`, { method: 'DELETE' });
}

/** 勾选/取消某一个账户（多账户）。 */
export async function setConnectorAccountSelected(
  providerId: string,
  account: string,
  selected: boolean,
): Promise<ConnectorItem> {
  return request<ConnectorItem>(`/${providerId}/accounts/selected`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ account, selected }),
  });
}

export async function setConnectorEnabled(providerId: string, enabled: boolean): Promise<ConnectorItem> {
  return request<ConnectorItem>(`/${providerId}/enabled`, {
    method: 'PATCH',
    headers: JSON_HEADERS,
    body: JSON.stringify({ enabled }),
  });
}

export async function listConnectorResources(providerId: string): Promise<ConnectorResource[]> {
  return request<ConnectorResource[]>(`/${providerId}/resources`);
}

export async function setConnectorResources(
  providerId: string,
  resources: string[],
): Promise<ConnectorItem> {
  return request<ConnectorItem>(`/${providerId}/resources`, {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify({ resources }),
  });
}
