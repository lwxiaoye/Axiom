type RuntimeMessage = {
  role: string;
  content?: string;
  failed?: boolean;
};

type RuntimeUserInfo = {
  id?: string;
  userId?: string;
  username?: string;
  realname?: string;
  realName?: string;
};

export function buildMessageHistories(messages: RuntimeMessage[]) {
  return messages
    .filter((item) => !item.failed && (item.role === 'user' || item.role === 'assistant') && item.content)
    .map((item) => ({ role: item.role as 'user' | 'assistant', content: String(item.content) }));
}

export function buildWorkflowRuntimeVariables(options: {
  userInfo?: RuntimeUserInfo | null;
  histories?: { role: string; content: string }[];
  extra?: Record<string, any>;
}) {
  const info = options.userInfo || {};
  const userId = info.id || info.userId || '';
  const username = info.username || '';
  const realname = info.realname || info.realName || username;

  return {
    ...(options.extra || {}),
    histories: Array.isArray(options.histories) ? options.histories : [],
    ...(userId ? { userId } : {}),
    ...(username ? { username } : {}),
    ...(realname ? { realname } : {}),
  };
}

export function interpolateWorkflowText(text: string, variables: Record<string, any>) {
  return String(text || '').replace(/\{\{([^{}]+)\}\}/g, (placeholder, key) => {
    const name = String(key || '').trim();
    if (!Object.prototype.hasOwnProperty.call(variables, name)) return placeholder;
    const value = variables[name];
    if (value == null) return '';
    return typeof value === 'string' ? value : JSON.stringify(value);
  });
}
