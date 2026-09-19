/** 内置助手独立页左栏的会话条目（由主对话 ThreadItem 投影而来）。 */
export type RunSession = {
  id: string;
  appId: string;
  aiAppType?: string;
  title: string;
  pinned?: boolean;
  /** 会话来源（内置助手为其 preset）。 */
  origin?: string | null;
  createTime?: string;
  updateTime?: string;
};
