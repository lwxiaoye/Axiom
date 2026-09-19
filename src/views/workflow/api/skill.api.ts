import { defHttp } from '/@/utils/http/axios';
import { getToken } from '/@/utils/auth';
import { buildAgentSkillUploadHeaders } from '../shared/agentSkillPackage';

/**
 * 智能体技能（Agent Skill）API，契约对齐 v1.9 §10.5.7（蓝本 FastGPT agent_skills）。
 * 技能 = 版本化 zip 技能包，运行于受控沙箱；与 Java /ai/skill/*（提示词型 Skill 广场）是两套并存概念。
 * 后端未就绪时前端保持稳定空态，不阻塞工作台其他板块。
 */

export type AgentSkillCategory = 'search' | 'tool' | 'coding' | 'data' | 'analysis' | 'communication' | 'other';

export const AgentSkillCategoryLabelMap: Record<AgentSkillCategory, string> = {
  search: '搜索',
  tool: '工具',
  coding: '编码',
  data: '数据',
  analysis: '分析',
  communication: '沟通',
  other: '其他',
};

export type AgentSkill = {
  id: string;
  parentId?: string | null;
  type: 'folder' | 'skill';
  source: 'system' | 'personal';
  name: string;
  description?: string;
  category?: AgentSkillCategory[];
  avatar?: string;
  currentVersionId?: string;
  creationStatus?: 'creating' | 'ready' | 'failed';
  creationError?: string;
  appCount?: number;
  createTime?: string;
  updateTime?: string;
};

export type AgentSkillVersion = {
  id: string;
  skillId: string;
  versionName?: string;
  storageKey?: string;
  importSource?: { originalFilename: string; importedAt: string };
  createdAt?: string;
};

export type AgentSkillContent = {
  skillId: string;
  versionId?: string | null;
  versionName?: string | null;
  content: string;
};

export type SkillMarketSkill = {
  id: string;
  recordId?: string;
  skillId: string;
  name: string;
  description?: string;
};

/**
 * 技能运行时归 agent-api（Python，与工作流同 ADR-031 路径）。
 * /agent-api 前缀直达 FastAPI 原始 JSON，apiUrl 置空避免拼 /api 前缀。
 */
enum Api {
  list = '/agent-api/skill/list',
  add = '/agent-api/skill/add',
  edit = '/agent-api/skill/edit',
  delete = '/agent-api/skill/delete',
  importZip = '/agent-api/skill/import',
  versions = '/agent-api/skill/versions',
  content = '/agent-api/skill/content',
}

const RAW = { isTransformResponse: false, apiUrl: '' } as const;

export const getAgentSkillList = (params?: { keyword?: string; parentId?: string; source?: AgentSkill['source'] }) =>
  defHttp.get<AgentSkill[]>({ url: Api.list, params }, { ...RAW, errorMessageMode: 'none' });

/**
 * 平台技能（工作流 / 智能体编辑器里「系统技能」选择器的数据源）。
 * 原来打 Java 的 /ai/skill/list，Java 下线后该地址 404，选择器永远为空；
 * 改走 agent-api 的目录接口并只取 scope=system（内置 + 管理员分发的），返回形状不变。
 */
export async function getSkillMarketList(): Promise<SkillMarketSkill[]> {
  const data: any = await defHttp.get(
    { url: Api.list, params: { scope: 'system' } },
    { ...RAW, errorMessageMode: 'none' }
  );
  const list = Array.isArray(data)
    ? data
    : Array.isArray(data?.records)
      ? data.records
      : Array.isArray(data?.data)
        ? data.data
        : [];

  return list
    .filter((item: any) => item?.enabled === 1 || item?.enabled === true || item?.enabled === '1')
    .map((item: any) => {
      const skillId = String(item?.skillId || item?.id || '').trim();
      return {
        id: skillId,
        recordId: String(item?.recordId || item?.id || '').trim(),
        skillId,
        name: String(item?.name || skillId || '未命名 Skill'),
        description: String(item?.description || ''),
      };
    })
    .filter((item: SkillMarketSkill) => item.id);
}

/** 描述生成创建：提交名称+描述，后端异步生成技能包（creationStatus: creating -> ready/failed） */
export const createAgentSkill = (data: { name: string; description?: string; category?: AgentSkillCategory[] }) =>
  defHttp.post<AgentSkill>({ url: Api.add, data }, RAW);

export const updateAgentSkill = (data: Partial<AgentSkill> & { id: string }) =>
  defHttp.put<AgentSkill>({ url: Api.edit, data }, RAW);

export const deleteAgentSkill = (id: string) =>
  defHttp.delete({ url: Api.delete, params: { id } }, { ...RAW, joinParamsToUrl: true });

/** zip 导入创建：包结构需含 skill.json（name/description/category）或 SKILL.md */
export const importAgentSkillZip = (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  return fetch(Api.importZip, {
    method: 'POST',
    headers: buildAgentSkillUploadHeaders(getToken()),
    body: formData,
  }).then(async (response) => {
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : `导入失败：${response.status}`;
      throw new Error(detail);
    }
    return data as AgentSkill;
  });
};

export const getAgentSkillVersions = (skillId: string) =>
  defHttp.get<AgentSkillVersion[]>({ url: Api.versions, params: { skillId } }, { ...RAW, errorMessageMode: 'none' });

/** 技能说明书内容（默认当前版本） */
export const getAgentSkillContent = (skillId: string, versionId?: string) =>
  defHttp.get<AgentSkillContent>(
    { url: Api.content, params: { skillId, versionId } },
    { ...RAW, errorMessageMode: 'none' }
  );
