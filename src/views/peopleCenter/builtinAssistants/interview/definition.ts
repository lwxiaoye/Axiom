import { INTERVIEW_ASSISTANT_PRESET, type BuiltinAssistant } from '../types';

export const INTERVIEW_ASSISTANT_ID = 'builtin:interview';

export const INTERVIEW_ASSISTANT: BuiltinAssistant = {
  appId: INTERVIEW_ASSISTANT_ID,
  preset: INTERVIEW_ASSISTANT_PRESET,
  name: '面试助手',
  shortBadge: '面试助手',
  description: '根据简历与目标岗位进行文字模拟面试，逐答反馈并复盘提升',
  icon: '/agent-icons/builtin/interview-assistant-v3.svg',
  legacyIcon: '/agent-icons/work-agent-orb.svg',
  category: 'communication',
  categoryLabel: '沟通交互',
  route: '/center/chat/interview',
  entryMode: 'standalone',
  recommendOrder: 3,
  mascotVariant: 'interview',
  uiPolicy: 'interview_practice',
  notices: {
    useSkill: '面试练习使用当前简历与岗位材料，其他 Skill 请在主对话中使用',
    useKnowledge: '面试考察围绕本场目标岗位展开，请在开始前填写岗位要求',
  },
};
