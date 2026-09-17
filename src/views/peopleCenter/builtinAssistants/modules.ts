import { CAMPUS_ASSISTANT_MODULE } from './campusServices';
import { PRESENTATION_ASSISTANT_MODULE } from './presentation';
import { INTERVIEW_ASSISTANT_MODULE } from './interview';
import type { BuiltinAssistantModule } from './types';

/** 新助手在此显式注册，不复制 useCenterChat 或按名称猜测身份。 */
export const BUILTIN_ASSISTANT_MODULES: readonly BuiltinAssistantModule[] = [
  CAMPUS_ASSISTANT_MODULE,
  PRESENTATION_ASSISTANT_MODULE,
  INTERVIEW_ASSISTANT_MODULE,
];
