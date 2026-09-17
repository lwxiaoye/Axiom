import {
  PRESENTATION_ASSISTANT,
  PRESENTATION_ASSISTANT_ID,
  PRESENTATION_ASSISTANT_PRESET,
  getBuiltinAssistantById,
} from '../builtinAssistants';

export { PRESENTATION_ASSISTANT_ID, PRESENTATION_ASSISTANT_PRESET };
export type AssistantPreset = typeof PRESENTATION_ASSISTANT_PRESET;

export const PRESENTATION_ASSISTANT_NAME = PRESENTATION_ASSISTANT.name;
export const PRESENTATION_ASSISTANT_DESCRIPTION = PRESENTATION_ASSISTANT.description;
/** 旧路径兼容；新代码请读 registry 的 icon。 */
export const PRESENTATION_ASSISTANT_ICON = '/agent-icons/presentation-assistant.png';

export function isPresentationAssistant(item: { id?: unknown; appName?: unknown; name?: unknown }): boolean {
  return getBuiltinAssistantById(item?.id)?.preset === PRESENTATION_ASSISTANT_PRESET;
}
