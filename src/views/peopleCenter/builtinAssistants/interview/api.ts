import { requestAgentApi } from '../../agentApi';
import type { InterviewSnapshot } from './types';

export function getInterviewSession(threadId: string, signal?: AbortSignal): Promise<InterviewSnapshot> {
  return requestAgentApi<InterviewSnapshot>(
    `/chat/threads/${encodeURIComponent(threadId)}/interview`,
    { method: 'GET', signal },
  );
}
