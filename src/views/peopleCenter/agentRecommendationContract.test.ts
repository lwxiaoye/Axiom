import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { restoreExecutionTrace } from './composables/executionTimeline';

const root = resolve(__dirname);
const apiSource = readFileSync(resolve(root, 'agentApi.ts'), 'utf8');
const chatSource = readFileSync(resolve(root, 'composables/useCenterChat.ts'), 'utf8');
const messageSource = readFileSync(resolve(root, 'components/MessageList.vue'), 'utf8');

describe('主对话智能体推荐卡契约', () => {
  it('消费结构化 SSE，不用模型正文编造理由', () => {
    expect(apiSource).toContain('export type RecommendAgentsPayload');
    expect(apiSource).toContain("case 'recommend_agents':");
    expect(apiSource).toContain('reasons: d.reasons');
    expect(chatSource).toContain('findAppsByIds(payload.ids, payload.reasons)');
    expect(chatSource).toContain('recommendReason: reasons?.[String(app.id)]');
  });

  it('推荐卡从智能体广场目录回源，不被 @ 可委派列表限制', () => {
    const resolverStart = chatSource.indexOf('function findAppsByIds');
    const resolverEnd = chatSource.indexOf('/** 历史轨迹回放', resolverStart);
    const resolver = chatSource.slice(resolverStart, resolverEnd);
    expect(resolver).toContain('options.appList.value');
    expect(resolver).not.toContain('subagents.value');
    expect(chatSource).toContain('外部智能体可以被推荐并新窗口打开');
  });

  it('卡片放在本轮输出底部，隐式与明确推荐使用克制文案', () => {
    expect(messageSource).toContain('如果你希望用更专门的流程继续，可以试试');
    expect(messageSource).toContain('这些智能体与刚才的需求比较匹配');
    expect(messageSource).toContain("app.recommendReason || app.appRemark");
    expect(messageSource).toContain('打开智能体');
    expect(messageSource.indexOf('class="message-agent-recs"')).toBeGreaterThan(
      messageSource.indexOf('class="msg-actions"'),
    );
  });

  it('历史回放保留 intent 和服务端理由，候选仍由当前应用列表重新匹配', () => {
    const restored = restoreExecutionTrace({
      recommended_agent_ids: ['agent-1'],
      recommendation_meta: {
        intent: 'explicit_request',
        confidence: 'strong',
        reasons: { 'agent-1': '适合结构化模拟面试' },
      },
    } as any) as any;
    expect(restored.recommendedAgentIds).toEqual(['agent-1']);
    expect(restored.recommendationIntent).toBe('explicit_request');
    expect(restored.recommendationReasons).toEqual({
      'agent-1': '适合结构化模拟面试',
    });
    expect(chatSource).toContain('findAppsByIds(ids, restored.recommendationReasons)');
  });

  it('无结果不留空卡，且动画遵循 reduced-motion', () => {
    expect(chatSource).toContain('if (!apps.length) return;');
    expect(messageSource).toContain('@media (prefers-reduced-motion: reduce)');
    expect(messageSource).toMatch(/\.agent-rec-card,[\s\S]{0,160}transition: none/);
  });
});
