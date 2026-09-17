import type { BuiltinAssistantModule } from '../types';
import { CAMPUS_ASSISTANT } from './definition';
import { CAMPUS_UI_FLAGS } from './ui';

export { CAMPUS_ASSISTANT, CAMPUS_ASSISTANT_ID } from './definition';
export { CAMPUS_UI_FLAGS } from './ui';

export const CAMPUS_ASSISTANT_MODULE: BuiltinAssistantModule = {
  assistant: CAMPUS_ASSISTANT,
  ui: CAMPUS_UI_FLAGS,
};
