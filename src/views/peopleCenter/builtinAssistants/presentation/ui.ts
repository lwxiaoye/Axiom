import type { BuiltinUiFlags } from '../types';

export const PRESENTATION_UI_FLAGS: BuiltinUiFlags = {
  welcomeTitle: '你好，今天想制作什么演示文稿？',
  welcomeSubtitle: '告诉我主题、受众和使用场景，我会帮你梳理内容、设计版式并生成可编辑的演示文稿。',
  composerPlaceholder: '描述你想制作或优化的演示文稿…',
  showAvatar: false,
  hideComposerMascot: false,
  hideMention: true,
  hideSkillSelector: true,
  hideSubagent: true,
  hideRecommendGrid: true,
  hidePlusMenu: false,
  hideModelSelector: false,
  hideKnowledge: false,
  hideFiles: false,
  hideThreads: false,
  hidePlanMode: true,
  hideResearch: true,
  hideWebSearchToggle: false,
  imageOnlyUpload: false,
  allowPasteUpload: true,
  emptyStateClass: 'presentation-state',
};
