/**
 * Skill 广场「我的技能 / 平台技能 + 上传 + 按权限位操作」契约（2026-09-19）。
 *
 * 后端由另一名工程师并行实现，这里把前端对契约的依赖钉死：请求路径、body 形状、
 * 权限位 → 按钮 的映射、两个分区与空态文案。后端合并后真机点的时候，这些不该再变。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const defHttpGet = jest.fn();
jest.mock('/@/utils/http/axios', () => ({ defHttp: { get: (...args: unknown[]) => defHttpGet(...args) } }), { virtual: true });
jest.mock('/@/utils/auth', () => ({ getToken: () => 'test-token' }), { virtual: true });
jest.mock('../utils/agentAuthHeaders', () => ({
  resolveAgentAccessToken: () => 'test-token',
  agentAuthHeaders: (headers: Record<string, string>) => ({ ...headers, 'X-Access-Token': 'test-token' }),
}));

import {
  getSkills,
  normalizeSkillItem,
  isPackageSkill,
  importSkillZip,
  createSkill,
  updateSkill,
  deleteSkill,
  distributeSkill,
  revokeSkillDistribution,
} from '../agentApi';

const square = readFileSync(resolve(__dirname, 'SkillSquare.vue'), 'utf8');
const selector = readFileSync(resolve(__dirname, 'SkillSelector.vue'), 'utf8');
const useCenterChat = readFileSync(resolve(__dirname, '../composables/useCenterChat.ts'), 'utf8');

/** 后端契约里的一条完整记录 */
const RECORD = {
  skillId: 'sk-1',
  recordId: 'sk-1',
  name: '周报整理',
  description: '把零散记录整理成周报',
  source: 'personal',
  enabled: true,
  version: 'v1',
  versionName: 'v1',
  ownerUserId: 'u-1',
  distributedByUserId: null,
  builtin: false,
  canEdit: true,
  canDelete: true,
  canDistribute: false,
  canRevoke: false,
  fileCount: 1,
  updatedAt: '2026-09-19 10:00:00',
};

afterEach(() => {
  jest.restoreAllMocks();
  defHttpGet.mockReset();
});

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('列表：GET /agent-api/skill/list?scope=', () => {
  it('默认 scope=all、只留 enabled；@Skill 选择器与广场共用这一份数据', async () => {
    defHttpGet.mockResolvedValueOnce([RECORD, { ...RECORD, skillId: 'sk-off', enabled: 0 }]);
    const list = await getSkills();
    expect(defHttpGet).toHaveBeenCalledWith(
      { url: '/agent-api/skill/list', params: { scope: 'all' } },
      { apiUrl: '', isTransformResponse: false, errorMessageMode: 'none' },
    );
    expect(list.map((s) => s.id)).toEqual(['sk-1']);
  });

  it('广场传 includeDisabled 时保留不可用的个人技能（好删掉）', async () => {
    defHttpGet.mockResolvedValueOnce([RECORD, { ...RECORD, skillId: 'sk-off', enabled: false }]);
    const list = await getSkills({ scope: 'all', includeDisabled: true });
    expect(list.map((s) => [s.id, s.enabled])).toEqual([['sk-1', true], ['sk-off', false]]);
  });

  it('scope 原样透传给后端', async () => {
    defHttpGet.mockResolvedValueOnce([]);
    await getSkills({ scope: 'personal' });
    expect(defHttpGet.mock.calls[0][0]).toEqual({ url: '/agent-api/skill/list', params: { scope: 'personal' } });
  });

  it('契约字段逐个归一化；旧后端没返回的权限位一律 false、fileCount 0', () => {
    const full = normalizeSkillItem({ ...RECORD, canDistribute: 1, canRevoke: 'true', builtin: true, fileCount: '3' });
    expect(full).toMatchObject({
      id: 'sk-1',
      recordId: 'sk-1',
      skillId: 'sk-1',
      source: 'personal',
      enabled: true,
      version: 'v1',
      ownerUserId: 'u-1',
      distributedByUserId: null,
      builtin: true,
      canEdit: true,
      canDelete: true,
      canDistribute: true,
      canRevoke: true,
      fileCount: 3,
      updatedAt: '2026-09-19 10:00:00',
    });

    const legacy = normalizeSkillItem({ id: 'ppt-studio', name: 'PPT', enabled: 1, source: 'system', versionName: 'v1' });
    expect(legacy).toMatchObject({
      id: 'ppt-studio',
      canEdit: false,
      canDelete: false,
      canDistribute: false,
      canRevoke: false,
      builtin: false,
      fileCount: 0,
      distributedByUserId: null,
    });
    expect(normalizeSkillItem({ name: '没有 id' })).toBeNull();
  });

  it('fileCount > 1 视为 zip 包（编辑时不能改正文）', () => {
    expect(isPackageSkill({ fileCount: 0 })).toBe(false);
    expect(isPackageSkill({ fileCount: 1 })).toBe(false);
    expect(isPackageSkill({ fileCount: 2 })).toBe(true);
    expect(isPackageSkill({})).toBe(false);
  });

});

describe('上传：POST /agent-api/skill/import（multipart file）与 POST /agent-api/skill/add（JSON）', () => {
  it('zip 走 multipart，字段名 file，不手动设 Content-Type，返回归一化后的记录', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse({ ...RECORD, fileCount: 4 }));
    const file = new File(['zip-bytes'], 'weekly.zip', { type: 'application/zip' });
    const created = await importSkillZip(file);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/agent-api/skill/import');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get('file')).toBe(file);
    expect(Object.keys(init.headers as Record<string, string>)).not.toContain('Content-Type');
    expect((init.headers as Record<string, string>)['X-Access-Token']).toBe('test-token');
    expect(created).toMatchObject({ id: 'sk-1', fileCount: 4, canEdit: true });
  });

  it('压缩后超过 5MB 不发请求，直接给出原因', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch');
    const big = new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'big.zip');
    await expect(importSkillZip(big)).rejects.toThrow('5MB');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('后端 400 的 {detail} 原样透出给弹窗', async () => {
    jest.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse({ detail: '已有同名技能「周报整理」' }, 400));
    await expect(importSkillZip(new File(['x'], 'dup.zip'))).rejects.toMatchObject({
      message: '已有同名技能「周报整理」',
      status: 400,
    });
  });

  it('直接编写走 /skill/add，body 是 {name, description, content}', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse(RECORD));
    await createSkill({ name: ' 周报整理 ', description: ' 一句话 ', content: '# 周报\n步骤…' });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/agent-api/skill/add');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ name: '周报整理', description: '一句话', content: '# 周报\n步骤…' });
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });
});

describe('编辑 / 删除 / 分发 / 撤回', () => {
  it('PUT /skill/edit：内容型带 content，zip 型不带该键', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(RECORD))
      .mockResolvedValueOnce(jsonResponse({ ...RECORD, fileCount: 3 }));

    await updateSkill({ skillId: 'sk-1', name: '周报整理', description: '改了', content: '# 新正文' });
    let [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/agent-api/skill/edit');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ skillId: 'sk-1', name: '周报整理', description: '改了', content: '# 新正文' });

    await updateSkill({ skillId: 'sk-1', name: '周报整理', description: '只改描述' });
    [url, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ skillId: 'sk-1', name: '周报整理', description: '只改描述' });
  });

  it('DELETE /skill/delete?skillId=', async () => {
    const fetchMock = jest.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse({ success: true }));
    await deleteSkill('sk 1');
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/agent-api/skill/delete?skillId=sk%201');
    expect(init.method).toBe('DELETE');
  });

  it('POST /skill/{id}/distribute 与 /skill/{id}/revoke-distribution', async () => {
    const fetchMock = jest
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ ...RECORD, source: 'system', canRevoke: true, canDistribute: false }))
      .mockResolvedValueOnce(jsonResponse({ ...RECORD, source: 'personal', canRevoke: false, canDistribute: true }));

    const distributed = await distributeSkill('sk-1');
    expect(fetchMock.mock.calls[0][0]).toBe('/agent-api/skill/sk-1/distribute');
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe('POST');
    expect(distributed).toMatchObject({ source: 'system', canRevoke: true });

    const revoked = await revokeSkillDistribution('sk-1');
    expect(fetchMock.mock.calls[1][0]).toBe('/agent-api/skill/sk-1/revoke-distribution');
    expect((fetchMock.mock.calls[1][1] as RequestInit).method).toBe('POST');
    expect(revoked).toMatchObject({ source: 'personal', canDistribute: true });
  });

  it('非管理员打分发接口拿到 403 时，detail 与状态码都带回来', async () => {
    jest.spyOn(globalThis, 'fetch').mockResolvedValueOnce(jsonResponse({ detail: '只有管理员可以分发技能' }, 403));
    await expect(distributeSkill('sk-1')).rejects.toMatchObject({ message: '只有管理员可以分发技能', status: 403 });
  });
});

describe('SkillSquare.vue：两个分区、上传弹窗、按权限位显示按钮', () => {
  it('固定两个分区：先「我的技能」再「平台技能」，各自有计数与空态', () => {
    expect(square).toContain(':data-section="group.key"');
    const personal = square.indexOf("key: 'personal' as const");
    const system = square.indexOf("key: 'system' as const");
    expect(personal).toBeGreaterThan(-1);
    expect(system).toBeGreaterThan(personal);
    expect(square).toContain("title: '我的技能'");
    expect(square).toContain("title: '平台技能'");
    expect(square).toContain('{{ group.all.length }}');
    // 空态：告诉用户下一步在哪
    expect(square).toContain('还没有上传技能');
    expect(square).toContain('点右上角「上传技能」');
    expect(square).toContain('暂无平台技能');
    // 一次 scope=all 拉回来按 source 切，个人区 = 非 system
    expect(square).toContain("getSkills({ scope: 'all', includeDisabled: true })");
    expect(square).toContain("skills.value.filter((s) => s.source !== 'system')");
    // 平台技能不可用的对用户没有任何可做的操作，直接不显示；自己的不可用也列出来好删掉
    expect(square).toContain("skills.value.filter((s) => s.source === 'system' && s.enabled !== false)");
  });

  it('上传弹窗：一个弹窗两种方式（zip / 直接写），zip 只暂存等用户点「上传」，失败展示后端 detail', () => {
    expect(square).toContain('<button class="skill-upload" type="button" @click="openUpload">上传技能</button>');
    expect(square).toContain('title="上传技能"');
    expect(square).toContain('上传 zip 包');
    expect(square).toContain('直接编写');
    expect(square).toContain(':before-upload="stageZip"');
    expect(square).toMatch(/function stageZip\(file: File\) \{[\s\S]*?zipFile\.value = file;\s*return false;/);
    expect(square).toContain('await importSkillZip(zipFile.value)');
    expect(square).toContain('await createSkill({ name, description: uploadForm.description, content: uploadForm.content })');
    expect(square).toContain('v-model:value="uploadForm.name"');
    expect(square).toContain('v-model:value="uploadForm.description"');
    expect(square).toContain('v-model:value="uploadForm.content"');
    expect(square).toContain("formError.value = errorText(error, '上传失败，请稍后再试')");
    expect(square).toContain('<p v-if="formError" class="skill-form-error" role="alert">{{ formError }}</p>');
    // 成功后刷新列表
    expect(square).toMatch(/uploadOpen\.value = false;[\s\S]*?await reloadQuietly\(\);/);
  });

  it('卡片操作只看后端权限位，前端不自己判管理员', () => {
    expect(square).toContain('<button v-if="skill.canEdit" class="skill-op" type="button" @click="openEdit(skill)">编辑</button>');
    expect(square).toContain("if (skill.canDistribute) items.push({ key: 'distribute', label: '分发到全平台'");
    expect(square).toContain("if (skill.canRevoke) items.push({ key: 'revoke', label: '撤回分发'");
    expect(square).toContain("if (skill.canDelete) items.push({ key: 'delete', label: '删除', danger: true");
    expect(square).not.toMatch(/hasPermission|isAdmin|roleList|'admin'/);
    // 删除 / 分发 / 撤回都要先确认
    expect(square).toMatch(/function confirmDelete[\s\S]*?Modal\.confirm\(\{[\s\S]*?await deleteSkill\(/);
    expect(square).toMatch(/function confirmDistribute[\s\S]*?Modal\.confirm\(\{[\s\S]*?await distributeSkill\(/);
    expect(square).toMatch(/function confirmRevoke[\s\S]*?Modal\.confirm\(\{[\s\S]*?await revokeSkillDistribution\(/);
  });

  it('编辑：内容型可改 SKILL.md，zip 型只改名称/描述（不传 content）', () => {
    expect(square).toContain('content: packaged ? undefined : editForm.content');
    expect(square).toContain('if (isPackageSkill(skill)) return;');
    expect(square).toContain('这里只能改名称和描述');
    expect(square).toContain('skillId: skill.skillId || skill.id');
  });

  it('平台技能卡片标「内置 / 管理员分发」，不可用的不给「使用」按钮', () => {
    expect(square).toContain("if (skill.source === 'system') return skill.builtin ? '内置' : '管理员分发';");
    expect(square).toContain('<span v-if="skill.source === \'system\'" class="skill-origin">{{ originLabel(skill) }}</span>');
    expect(square).toContain('<button v-if="skill.enabled !== false" class="skill-use" type="button" @click.stop="useSkill(skill)">');
  });

  it('沿用广场既有配色令牌，不新加一套颜色', () => {
    // 新增的桌面样式块：从 .skill-header-actions 到原有的 .skill-grid 之前
    const added = square.slice(square.indexOf('.skill-header-actions {'), square.indexOf('.skill-grid {'));
    // 新增样式只用 var(--x) 令牌或既有卡片里已经出现过的三个灰度值
    const colors = added.match(/#[0-9a-fA-F]{3,6}\b/g) || [];
    expect(new Set(colors)).toEqual(new Set(['#e3e5ea', '#fff', '#303035', '#eef0f4']));
    expect(added).toContain('var(--ink)');
    expect(added).toContain('var(--danger)');
  });

  it('@Skill 选择器不动：仍由 useCenterChat 用同一份 getSkills() 喂数据', () => {
    expect(useCenterChat).toContain('mentionSkills.value = await getSkills();');
    expect(selector).toContain('skills: SkillItem[];');
    expect(selector).not.toContain('getSkills(');
  });
});
