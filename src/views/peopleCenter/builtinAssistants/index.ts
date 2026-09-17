import type { BuiltinAssistant } from './types';

export {
  CAMPUS_ASSISTANT_PRESET,
  PRESENTATION_ASSISTANT_PRESET,
  INTERVIEW_ASSISTANT_PRESET,
  type AssistantPreset,
  type BuiltinAssistant,
  type BuiltinAssistantModule,
  type BuiltinAssistantPreset,
  type BuiltinAssistantNotices,
  type BuiltinEntryMode,
  type BuiltinMascotVariant,
  type BuiltinUiFlags,
  type BuiltinUiPolicyKey,
} from './types';

export type BuiltinAssistantIdentity = BuiltinAssistant;

export {
  CAMPUS_ASSISTANT,
  CAMPUS_ASSISTANT_ID,
  CAMPUS_UI_FLAGS,
} from './campusServices';

export {
  PRESENTATION_ASSISTANT,
  PRESENTATION_ASSISTANT_ID,
  PRESENTATION_UI_FLAGS,
} from './presentation';

export { INTERVIEW_ASSISTANT, INTERVIEW_ASSISTANT_ID, INTERVIEW_UI_FLAGS } from './interview';

export {
  decorateBuiltinCatalogApp,
  getBuiltinAssistantById,
  getBuiltinAssistantFromMarketplace,
  getBuiltinAssistantByPreset,
  getBuiltinUiFlags,
  getBuiltinUiPolicy,
  isAssistantPreset,
  isBuiltinAssistant,
  isBuiltinAssistantId,
  isRestrictedAssistantPreset,
  listBuiltinAssistants,
  listRecommendedBuiltinAssistants,
  sortBuiltinCatalogApps,
} from './registry';
