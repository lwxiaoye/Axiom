import type { BuiltinAssistantModule } from '../types';
import { INTERVIEW_ASSISTANT } from './definition';
import { INTERVIEW_UI_FLAGS } from './ui';

export { INTERVIEW_ASSISTANT, INTERVIEW_ASSISTANT_ID } from './definition';
export { INTERVIEW_UI_FLAGS } from './ui';

export const INTERVIEW_ASSISTANT_MODULE: BuiltinAssistantModule = {
  assistant: INTERVIEW_ASSISTANT,
  ui: INTERVIEW_UI_FLAGS,
};
