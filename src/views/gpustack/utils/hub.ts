/**
 * HuggingFace / ModelScope 模型库公共数据直拉。
 *
 * GPUStack 目录与当前 Java 代理均不提供 likes/downloads，这里按 spec 内的
 * repo id 客户端拉取。所有方法失败均返回 null，由调用方降级，不抛错刷屏。
 */

const HF_BASE = 'https://huggingface.co';
const MODELSCOPE_BASE = 'https://modelscope.cn';

export interface HubMeta {
  likes?: number;
  downloads?: number;
  trending?: boolean;
  updatedAt?: string;
}

/** 取 spec 的仓库标识：优先 HuggingFace，其次 ModelScope */
export function pickRepoId(spec: any): { source: 'huggingface' | 'model_scope' | null; repo: string } {
  if (spec?.huggingface_repo_id) return { source: 'huggingface', repo: spec.huggingface_repo_id };
  if (spec?.model_scope_model_id) return { source: 'model_scope', repo: spec.model_scope_model_id };
  return { source: null, repo: '' };
}

/**
 * 取模型元数据（点赞 / 下载 / 更新时间）。
 * HuggingFace: GET /api/models/{repo} → { downloads, downloadsAllTime, likes, lastModified, trending }
 * ModelScope: GET /api/v1/models/{ns}/{name} → data.{Downloads,Likes,Published?}
 * 任一失败或无 repo 均返回 null。
 */
export async function fetchHubMeta(spec: any): Promise<HubMeta | null> {
  const { source, repo } = pickRepoId(spec);
  if (!source || !repo) return null;
  try {
    if (source === 'huggingface') {
      const res = await fetch(`${HF_BASE}/api/models/${repo}`, { headers: { Accept: 'application/json' } });
      if (!res.ok) return null;
      const j = await res.json();
      return {
        likes: typeof j.likes === 'number' ? j.likes : undefined,
        downloads: typeof j.downloads === 'number' ? j.downloads : j.downloadsAllTime,
        trending: !!j.trending,
        updatedAt: j.lastModified || j.lastModifieds?.[0] || undefined,
      };
    }
    // model_scope: repo 形如 Qwen/Qwen2.5-7B-Instruct
    const res = await fetch(`${MODELSCOPE_BASE}/api/v1/models/${repo}`, { headers: { Accept: 'application/json' } });
    if (!res.ok) return null;
    const j = await res.json();
    const d = j?.Data || j?.data || {};
    return {
      likes: typeof d.Likes === 'number' ? d.Likes : undefined,
      downloads: typeof d.Downloads === 'number' ? d.Downloads : undefined,
      updatedAt: d.Published || undefined,
    };
  } catch {
    return null;
  }
}

/** 格式化下载/点赞量为展示文案 */
export function formatCount(n?: number): string {
  if (n == null || Number.isNaN(n)) return '-';
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
