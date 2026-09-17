import {
  PRESENTATION_ASSISTANT_PRESET,
  type BuiltinAssistant,
} from '../types';

export const PRESENTATION_ASSISTANT_ID = 'builtin:presentation';

export const PRESENTATION_ASSISTANT: BuiltinAssistant = {
  appId: PRESENTATION_ASSISTANT_ID,
  preset: PRESENTATION_ASSISTANT_PRESET,
  name: '演示文稿助手',
  shortBadge: '演示文稿',
  description: '制作、优化并交付可编辑演示文稿',
  icon: '/agent-icons/builtin/presentation-assistant.png',
  legacyIcon: '/agent-icons/presentation-assistant.png',
  category: 'content_creation',
  categoryLabel: '内容创作',
  route: '/center/chat/ppt',
  entryMode: 'standalone',
  recommendOrder: 2,
  mascotVariant: 'presentation',
  uiPolicy: 'presentation_authoring',
  notices: {
    useSkill: '演示文稿助手固定使用 ppt-studio；其他 Skill 请在主对话的新会话中使用',
  },
};
