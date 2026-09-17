import { defHttp } from '/@/utils/http/axios';
import type { RunPresentationConfig, VariableItemType } from '../../workflow/core/type';
import { requestAgentApi, type GeneratedFile } from '../../peopleCenter/agentApi';
import type { RunInspirationScene } from './agentRunPresentation';

/** apiUrl 置空：/agent-api 直连 FastAPI，不拼 defHttp 默认的 /api 前缀 */
const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export type RunAppMeta = {
  id: string;
  name?: string;
  aiAppType: string;
  appIcon?: string;
  appCategory?: string;
  description?: string;
  status?: string;
  sharePermission?: string;
  previewMode?: boolean;
  welcomeText?: string;
  presentation?: RunPresentationConfig;
  quickQuestions?: string[];
  inspirationScenes?: RunInspirationScene[];
  variables?: VariableItemType[];
  ttsConfig?: {
    type?: 'none' | 'web';
  };
  fileSelectConfig?: {
    canSelectFile?: boolean;
    canSelectImg?: boolean;
    canSelectVideo?: boolean;
    canSelectAudio?: boolean;
    canSelectCustomFileExtension?: boolean;
    customFileExtensionList?: string[];
    maxFiles?: number;
  };
  /** 画布对话/Agent 节点配置的模型，供上传时决定是否跳过 OCR */
  chatModel?: string;
};

export type RunSession = {
  id: string;
  appId: string;
  aiAppType?: string;
  title: string;
  pinned?: boolean;
  /** 主对话委派会话的来源主对话 id（悬浮窗按此优先选中当前主对话的会话） */
  parentThreadId?: string | null;
  /** 会话来源：'delegation'=主对话委派产生。稳定标记（删来源主对话清空 parentThreadId 后仍在），
   *  悬浮窗据此判定是否委派，避免孤儿委派会话被误当普通独立对话回退选中 */
  origin?: string | null;
  createTime?: string;
  updateTime?: string;
};

export type RunMessage = {
  id: number | string;
  role: 'user' | 'assistant';
  content: string;
  turnId?: string | null;
  status?: RunMessageStatus;
  feedback?: 'up' | 'down' | null;
  /** user 角色的真实发送方；AXIOM Agent 委派与人类追问必须逐消息区分。 */
  senderType?: 'human' | 'work_agent' | null;
  attachments?: RunMessageAttachment[] | null;
  generatedFiles?: GeneratedFile[] | null;
  createTime?: string;
};

export type RunMessageStatus = 'completed' | 'partial' | 'failed' | 'cancelled' | 'interrupted' | 'unknown';

export type RunMessageAttachment = {
  filename: string;
  kind?: string;
  status?: string;
  note?: string;
  file_id?: string;
  preview_url?: string;
};

export const getRunApp = (appId: string, previewVersionId?: string, previewDraft?: boolean) =>
  defHttp.get<RunAppMeta>(
    { url: '/agent-api/workflow/run/app', params: { appId, previewVersionId, previewDraft: previewDraft ? '1' : undefined } },
    { ...RAW, errorMessageMode: 'none' }
  );

export const getRunSessions = (appId: string, keyword?: string) =>
  defHttp.get<RunSession[]>({ url: '/agent-api/workflow/run/sessions', params: { appId, keyword } }, { ...RAW, errorMessageMode: 'none' });

export const createRunSession = (appId: string, title?: string) =>
  defHttp.post<RunSession>({ url: '/agent-api/workflow/run/sessions', data: { appId, title } }, RAW);

export const deleteRunSession = (id: string) =>
  defHttp.delete({ url: '/agent-api/workflow/run/sessions', params: { id } }, { ...RAW, joinParamsToUrl: true });

export const pinRunSession = (id: string, pinned: boolean) =>
  defHttp.post(
    { url: `/agent-api/chat/threads/${encodeURIComponent(id)}/pin`, data: { pinned } },
    RAW,
  );

export const renameRunSession = (id: string, title: string) =>
  defHttp.post<{ success: boolean; title: string }>(
    { url: `/agent-api/chat/threads/${encodeURIComponent(id)}/rename`, data: { title } },
    RAW,
  );

export const getRunMessages = (sessionId: string) =>
  defHttp.get<RunMessage[]>({ url: '/agent-api/workflow/run/messages', params: { sessionId } }, { ...RAW, errorMessageMode: 'none' });

export const appendRunMessage = (
  sessionId: string,
  role: 'user' | 'assistant',
  content: string,
  attachments?: RunMessageAttachment[],
  options?: {
    runId?: string;
    turnId?: string;
    status?: Exclude<RunMessageStatus, 'unknown'>;
    generatedFiles?: GeneratedFile[];
    truncateFromId?: number;
  },
) =>
  defHttp.post<{ id: number; title: string }>(
    {
      url: '/agent-api/workflow/run/messages',
      data: {
        sessionId,
        role,
        content,
        attachments,
        runId: options?.runId,
        turnId: options?.turnId,
        status: options?.status,
        generatedFiles: options?.generatedFiles,
        truncateFromId: options?.truncateFromId,
      },
    },
    RAW,
  );

export async function submitRunMessageFeedback(
  messageId: number,
  feedback: 'up' | 'down' | null,
): Promise<void> {
  await requestAgentApi(`/chat/messages/${messageId}/feedback`, {
    method: 'POST',
    body: JSON.stringify({ feedback }),
  });
}

/** 恢复交互挂起（HITL）：复用工作流 resume 端点 */
export const resumeRunDefinition = (data: {
  appId: string;
  sessionId?: string;
  previewVersionId?: string;
  previewDraft?: boolean;
  resumeId: string;
  value: string | Recordable;
}) =>
  defHttp.post({ url: '/agent-api/workflow/definition/resume', data }, { ...RAW, errorMessageMode: 'none' });
