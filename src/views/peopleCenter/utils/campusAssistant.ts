import {
  CAMPUS_ASSISTANT,
  CAMPUS_ASSISTANT_ID,
  CAMPUS_ASSISTANT_PRESET,
} from '../builtinAssistants';

export { CAMPUS_ASSISTANT_ID, CAMPUS_ASSISTANT_PRESET };

export const CAMPUS_ASSISTANT_NAME = CAMPUS_ASSISTANT.name;
export const CAMPUS_ASSISTANT_DESCRIPTION = CAMPUS_ASSISTANT.description;
/** 旧路径兼容；新代码请读 registry 的 icon。 */
export const CAMPUS_ASSISTANT_ICON = '/agent-icons/campus-services.png';

export function isCampusAssistant(item: { id?: unknown; appName?: unknown; name?: unknown }): boolean {
  return String(item?.id || '') === CAMPUS_ASSISTANT_ID;
}
