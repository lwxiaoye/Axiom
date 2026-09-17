import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = resolve(__dirname);
const centerSource = readFileSync(resolve(root, 'center.vue'), 'utf8');
const styleSource = readFileSync(resolve(root, 'styles/centerNew.less'), 'utf8');

describe('主对话历史列表展示', () => {
  it('把智能体身份、时间和标题拆成稳定的两层结构', () => {
    expect(centerSource).toContain('class="conversation-identity"');
    expect(centerSource).toContain('class="conversation-utility"');
    expect(centerSource).toContain('class="conversation-title" :title="item.title"');
    expect(styleSource).toContain("'identity utility'");
    expect(styleSource).toContain("'title title'");
  });

  it('内置智能体显示自己的图标和名称，普通会话稳定回退 AXIOM Agent', () => {
    expect(centerSource).toContain("return builtinHistoryBadge(preset) || 'AXIOM Agent'");
    expect(centerSource).toContain("return getBuiltinAssistantByPreset(preset)?.icon || '/agent-icons/work-agent-orb.svg'");
    expect(centerSource).toContain(":data-assistant=\"item.assistant_preset || 'work-agent'\"");
    expect(styleSource).toContain("[data-assistant='campus_services']");
    expect(styleSource).toContain("[data-assistant='presentation']");
  });

  it('时间组名不在行内重复，悬浮操作也不挤压标题', () => {
    expect(centerSource).toContain('formatThreadTime(item.updated_at, group.key)');
    expect(centerSource).toContain("groupKey === 'today' || groupKey === 'yesterday' || groupKey === 'week'");
    expect(styleSource).toContain('grid-area: utility;');
    expect(styleSource).toContain('.history-popover .conversation-item:hover .conversation-actions');
  });
});
