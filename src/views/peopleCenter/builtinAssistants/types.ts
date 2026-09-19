export const PRESENTATION_ASSISTANT_PRESET = 'presentation' as const;
export const CAMPUS_ASSISTANT_PRESET = 'campus_services' as const;
export const INTERVIEW_ASSISTANT_PRESET = 'interview' as const;

export type AssistantPreset =
  | typeof PRESENTATION_ASSISTANT_PRESET
  | typeof CAMPUS_ASSISTANT_PRESET
  | typeof INTERVIEW_ASSISTANT_PRESET;

export type BuiltinAssistantPreset = AssistantPreset;

export type BuiltinEntryMode = 'standalone';

export type BuiltinMascotVariant = 'main' | 'campus' | 'presentation' | 'interview';

export type BuiltinUiPolicyKey = 'campus_readonly' | 'presentation_authoring' | 'interview_practice';

/** 展示层开关。服务端策略仍是最终权限事实源。 */
export type BuiltinUiFlags = {
  welcomeTitle: string;
  welcomeSubtitle: string;
  welcomeHint?: string;
  composerPlaceholder: string;
  showAvatar: boolean;
  hideComposerMascot: boolean;
  hideMention: boolean;
  hideSkillSelector: boolean;
  hideRecommendGrid: boolean;
  hidePlusMenu: boolean;
  hideModelSelector: boolean;
  hideKnowledge: boolean;
  hideFiles: boolean;
  hideThreads: boolean;
  hidePlanMode: boolean;
  hideResearch: boolean;
  hideWebSearchToggle: boolean;
  /** 复用主对话上传链路，但只开放图片，不开放「我的文件」或文档附件。 */
  imageOnlyUpload: boolean;
  allowPasteUpload: boolean;
  emptyStateClass: string;
};

export type BuiltinAssistantNotices = {
  useSkill?: string;
  useKnowledge?: string;
};

export type BuiltinAssistant = {
  appId: string;
  preset: AssistantPreset;
  name: string;
  shortBadge: string;
  description: string;
  icon: string;
  legacyIcon: string;
  category: string;
  categoryLabel: string;
  /** Protected standalone route opened in a new browser page. */
  route: string;
  entryMode: BuiltinEntryMode;
  recommendOrder: number;
  mascotVariant: BuiltinMascotVariant;
  uiPolicy: BuiltinUiPolicyKey;
  notices: BuiltinAssistantNotices;
};

/** 每个助手只提供展示定义；聊天和执行能力由共享入口持有。 */
export type BuiltinAssistantModule = {
  assistant: BuiltinAssistant;
  ui: BuiltinUiFlags;
};
