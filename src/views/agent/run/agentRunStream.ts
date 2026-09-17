import { useUserStore } from '/@/store/modules/user';
import { runWorkflowDefinitionStream } from '../../workflow/api/workflowStream';
import type { GeneratedFile } from '../../peopleCenter/agentApi';

/**
 * 独立 Agent 运行页流式执行（WS5）：调用我们自己的 `/agent-api/workflow/definition/executeStream`，
 * 解析 SSE 的 node / result 事件。不引入 enterprise-knowledge-base 分支那套 Java 解析器。
 */

export type RunNodeEvent = { nodeId?: string; nodeType?: string; nodeLabel?: string; status?: string; [k: string]: any };

export type RunInteractive = {
  resumeId: string;
  type: 'userSelect' | 'formInput' | 'userInput';
  params: {
    description?: string;
    userSelectOptions?: { value: string; key: string }[];
    inputForm?: {
      label?: string;
      key: string;
      type?: string;
      valueType?: string;
      required?: boolean;
      description?: string;
      defaultValue?: any;
      maxLength?: number;
      min?: number;
      max?: number;
      list?: { label?: string; value: string }[];
      enums?: { label?: string; value: string }[];
    }[];
    userInputForms?: {
      label?: string;
      key: string;
      type?: string;
      valueType?: string;
      required?: boolean;
      description?: string;
      defaultValue?: any;
      maxLength?: number;
      min?: number;
      max?: number;
      list?: { label?: string; value: string }[];
      enums?: { label?: string; value: string }[];
    }[];
  };
};

export type RunResult = {
  runId: string;
  status: string;
  output: string;
  errorMessage?: string;
  durationMs?: number;
  interactive?: RunInteractive;
  files?: GeneratedFile[];
  [k: string]: any;
};

export type RunStreamHandlers = {
  signal?: AbortSignal;
  onNode?: (node: RunNodeEvent) => void;
  onDelta?: (text: string) => void;
  onResult?: (result: RunResult) => void;
};

export async function runAgentStream(body: Record<string, any>, handlers: RunStreamHandlers): Promise<void> {
  const token = useUserStore().getToken || '';
  await runWorkflowDefinitionStream('/agent-api/workflow/definition/executeStream', body, {
    ...handlers,
    token,
  });
}
