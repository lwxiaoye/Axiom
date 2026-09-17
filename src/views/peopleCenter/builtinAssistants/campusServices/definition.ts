import {
  CAMPUS_ASSISTANT_PRESET,
  type BuiltinAssistant,
} from '../types';

export const CAMPUS_ASSISTANT_ID = 'builtin:campus-services';

export const CAMPUS_ASSISTANT: BuiltinAssistant = {
  appId: CAMPUS_ASSISTANT_ID,
  preset: CAMPUS_ASSISTANT_PRESET,
  name: '校园百事通',
  shortBadge: '校园百事通',
  description: '校园政策、办事流程与常见问题官方问答',
  icon: '/agent-icons/builtin/campus-services.png',
  legacyIcon: '/agent-icons/campus-services.png',
  category: 'document_knowledge',
  categoryLabel: '文档与知识',
  route: '/center/chat/campus',
  entryMode: 'standalone',
  recommendOrder: 1,
  mascotVariant: 'campus',
  uiPolicy: 'campus_readonly',
  notices: {
    useSkill: '校园百事通只回答学校官方知识，不能使用 Skill',
    useKnowledge: '校园百事通使用管理员发布的固定知识库，不能由用户改选',
  },
};
