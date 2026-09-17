import type { BuiltinAssistantModule } from '../types';
import { PRESENTATION_ASSISTANT } from './definition';
import { PRESENTATION_UI_FLAGS } from './ui';

export { PRESENTATION_ASSISTANT, PRESENTATION_ASSISTANT_ID } from './definition';
export { PRESENTATION_UI_FLAGS } from './ui';

export const PRESENTATION_ASSISTANT_MODULE: BuiltinAssistantModule = {
  assistant: PRESENTATION_ASSISTANT,
  ui: PRESENTATION_UI_FLAGS,
};
