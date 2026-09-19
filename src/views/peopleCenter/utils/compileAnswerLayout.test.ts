import fs from 'node:fs';
import path from 'node:path';
import MarkdownIt from 'markdown-it';
import { compileAnswerLayout } from './compileAnswerLayout';

describe('compileAnswerLayout', () => {
  it('把真实调研墙文编译成可扫描结构，且原字符一个不少、顺序不变', () => {
    const raw =
      '调研完成。以下为结果。结论官方姿态是只出模型、不出 CLI，用 awesome-deepseek-agent 仓库引导第三方工具接入 V4 系列。' +
      '模型端时间线（来自 HuggingFace 模型库元数据）- 2025-09-22：DeepSeek-V3.1-Terminus 发布' +
      '- 2025-09-29：DeepSeek-V3.2-Exp 发布，首次引入 DeepSeek Sparse Attention（稀疏注意力），面向长上下文效率，效果对标 V3.1-Terminus' +
      '- 2026-04-22：DeepSeek-V4-Pro 发布，模型卡下载量已超 141 万' +
      '- 2026-06-27：V4-Pro-DSpark / V4-Flash-DSpark 推理加速变体发布' +
      '- 2026-07-31：DeepSeek-V4-Flash-0731 更新，V4 系最新版harness 工具图谱' +
      '- 原生 harness：Codewhale（Rust 编写，Codex 风格架构，自带沙盒工具执行、MCP 客户端与服务端、1M 上下文）' +
      '- 桥接 harness：deepclaude（把 Claude Code 的 agent 循环直接驱动 V4 Pro）' +
      '- 通用/自演化 harness：penguin-harness、PuddingAgent、SimpleDSH、loushang' +
      '值得关注的点- 与 OpenAI 出 Codex CLI、Anthropic 出 Claude Code 亲自下场做 harness 不同，DeepSeek 官方选择模型中立路线。' +
      '- 原生项目 Codewhale 的转型是当前最有信号意义的动态。' +
      '数据来源与局限- 以上均来自 GitHub 仓库元数据与 HuggingFace 模型页。' +
      '- 时间为各仓库或模型卡的创建时间，代表首发日期而非官方公告日。' +
      '需要的话，我可以把这份调研整理成 Markdown 或 Word 文档保存到你的文件。';

    const result = compileAnswerLayout(raw);

    expect(result.applied).toBe(true);
    expect(result.sourceText).toBe(raw);
    expect(result.markdown).toContain('**结论**\n\n官方姿态');
    expect(result.markdown).toContain('**模型端时间线（来自 HuggingFace 模型库元数据）**\n\n- 2025-09-22');
    expect(result.markdown).toContain('**harness 工具图谱**\n\n- 原生 harness');
    expect(result.markdown).toContain('**值得关注的点**\n\n- 与 OpenAI');
    expect(result.markdown).toContain('**数据来源与局限**\n\n- 以上均来自');
    expect(result.markdown).toContain('\n- 2025-09-29：DeepSeek-V3.2-Exp');
    expect(result.markdown).toContain('\n\n需要的话，我可以');

    const html = new MarkdownIt({ breaks: true }).render(result.markdown);
    expect(html).toContain('<p><strong>结论</strong></p>');
    expect(html).toContain('<p><strong>模型端时间线（来自 HuggingFace 模型库元数据）</strong></p>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<li>2025-09-22：DeepSeek-V3.1-Terminus 发布</li>');
  });

  it('规范 Markdown 逐字直通，不在正确内容上二次猜结构', () => {
    const raw =
      '先给结论：保留现有方案。\n\n**主要依据**\n\n- 成本是 82.7 元\n- 版本为 2026-07-09\n\n后续按计划执行。';
    const result = compileAnswerLayout(raw);

    expect(result.applied).toBe(false);
    expect(result.markdown).toBe(raw);
    expect(result.sourceText).toBe(raw);
  });

  it('没有明确标题和列表的超长单行只在句号后分段，仍保持原文逐字相等', () => {
    const sentence = '这是一段需要保留的完整说明，里面含 82.7、2026-07-09 和 V4-Pro，不应被当成列表。';
    const raw = sentence.repeat(10);
    const result = compileAnswerLayout(raw);

    expect(result.applied).toBe(true);
    expect(result.sourceText).toBe(raw);
    expect(result.markdown).toContain('。\n\n这是一段');
    expect(result.markdown).toContain('82.7');
    expect(result.markdown).toContain('2026-07-09');
    expect(result.markdown).toContain('V4-Pro');
  });

  it('只对超长异常回答修复自然小标题与旧要点标签', () => {
    const raw =
      `已完成改造。核心变化${'保留已有能力并收紧展示边界。'.repeat(16)}`
      + `验证结果${'定向检查已通过。'.repeat(12)}`
      + '说明历史消息只做展示修复。';
    const result = compileAnswerLayout(raw);
    expect(result.sourceText).toBe(raw);
    expect(result.markdown).toContain('**核心变化**\n\n');
    expect(result.markdown).toContain('**验证结果**\n\n');
    expect(result.markdown).toContain('**说明**\n\n');
  });

  it('代码围栏完全不参与墙文编译', () => {
    const raw = `说明${'很长。'.repeat(120)}\n\n\`\`\`ts\nconst value = '- keep';\n\`\`\``;
    expect(compileAnswerLayout(raw).markdown).toBe(raw);
  });

  it('三个对话入口都使用无损编译器，生产链路不再调用旧整形器', () => {
    for (const filename of ['MessageList.vue', 'SideChatPanel.vue']) {
      const source = fs.readFileSync(path.resolve(__dirname, `../components/${filename}`), 'utf8');
      expect(source).toContain("import { compileAnswerLayout }");
      expect(source).not.toContain("import { normalizeAnswerStructure }");
    }
  });
});
