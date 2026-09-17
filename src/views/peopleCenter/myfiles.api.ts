/**
 * 「我的文件」API（ADR-047 §6.6）。
 *
 * 打 Python agent-api（/agent-api/files/*），鉴权头见 utils/agentAuthHeaders.ts。
 * 独立成文件、不并入 agentApi.ts，保持文件工作区模块自包含。
 */
import { agentAuthHeaders } from './utils/agentAuthHeaders';

export type UserFileItem = {
  id: string;
  filename: string;
  mime: string;
  size: number;
  /** material=download_url 取回的材料（2026-07-27）：默认清单不列，show_all 才可见 */
  source: 'uploaded' | 'generated' | 'material' | 'research';
  threadId: string | null;
  /** 所属文件夹 id（null=未分类） */
  folderId: string | null;
  /** 来源对话标题（generated 产物回溯「这个文件是哪次对话生成的」） */
  threadTitle?: string | null;
  expiresAt: string | null;
  createdAt: string | null;
  /**
   * 交付物判据（后端算好下发）。默认清单已按 `deliverables_only=True` 筛过，只会拿到 true；
   * 只有打开「显示全部文件」（`show_all`）后才会出现 false 的过程文件。
   */
  deliverable?: boolean;
};

/** composer「我的文件」选择器的轻量选中项（随 ChatRequest.file_ids 发送） */
export type UserFileSelection = {
  id: string;
  filename: string;
};

export type UserFilesQuota = {
  usedBytes: number;
  quotaBytes: number;
  count: number;
  maxCount: number;
};

export type UserFolderItem = {
  id: string;
  name: string;
  fileCount: number;
  createdAt: string | null;
};

export type WorkFolderSelection = Pick<UserFolderItem, 'id' | 'name'> & {
  unavailable?: boolean;
};

export type UserFilesResponse = {
  files: UserFileItem[];
  folders?: UserFolderItem[];
  quota: UserFilesQuota;
};

/** 文件版本快照（Phase B）：status=draft 为审查未通过的草稿版 */
export type UserFileVersion = {
  id: string;
  fileId: string;
  versionNo: number;
  filename: string;
  mime: string;
  size: number;
  source: 'uploaded' | 'generated' | 'restored' | 'material' | 'research';
  status: 'active' | 'draft';
  threadId: string | null;
  changeSummary: string | null;
  createdBy: 'user' | 'agent';
  createdAt: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/agent-api/files${path}`, {
    ...init,
    headers: agentAuthHeaders({ ...(init?.headers as Record<string, string> | undefined) }),
  });
  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const data = await response.json();
      message = data?.detail || data?.message || message;
    } catch {
      // 非 JSON 响应时保留状态码信息
    }
    throw new Error(message);
  }
  return response.json();
}

/**
 * 文件清单。默认只回交付物（后端 `deliverables_only=not show_all`）。
 *
 * `showAll` 是**逃生口**：藏起来不等于删掉——模型 `download_url` 取回的材料、为做产物写的
 * build.py 都照常落库并占配额。主对话选择器仍可打开全量；时间线点过程文件会带 `?all=1`。
 */
export function listUserFiles(folderId?: string | null, showAll = false): Promise<UserFilesResponse> {
  const params = new URLSearchParams();
  if (folderId) params.set('folder_id', folderId);
  if (showAll) params.set('show_all', 'true');
  const qs = params.toString();
  return request<UserFilesResponse>(qs ? `?${qs}` : '');
}

export function createFolder(name: string): Promise<UserFolderItem> {
  return request<UserFolderItem>('/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

export function renameFolder(id: string, name: string): Promise<{ id: string; name: string }> {
  return request(`/folders/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

export function deleteFolder(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/folders/${id}`, { method: 'DELETE' });
}

/** 移动文件到文件夹（folderId=null 移出到未分类） */
export function moveUserFile(fileId: string, folderId: string | null): Promise<UserFileItem> {
  return request<UserFileItem>(`/${fileId}/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder_id: folderId }),
  });
}

export function uploadUserFile(file: File, folderId?: string | null): Promise<UserFileItem> {
  const form = new FormData();
  form.append('file', file);
  // 在文件夹视图内上传：带上当前夹 id，文件直接归位而不是落到未分类后"消失"
  if (folderId) form.append('folder_id', folderId);
  // 不手动设 Content-Type：让浏览器自动带 multipart boundary
  return request<UserFileItem>('/upload', { method: 'POST', body: form });
}

export function deleteUserFile(id: string): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/${id}`, { method: 'DELETE' });
}

/** 对话流式产出的 HTML 产物自动存入「我的文件」（产物与文件打通）。
    服务端幂等：同会话同名内容未变返回既有文件（unchanged=true），变化则原地新版本。 */
export function saveArtifactFile(
  filename: string,
  content: string,
  threadId?: string,
): Promise<UserFileItem & { versionNo?: number; unchanged?: boolean }> {
  return request('/save-artifact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, content, thread_id: threadId || null }),
  });
}

/**
 * 幻灯片手改后直接重新生成 PPTX 并覆盖「我的文件」（2026-07-30 用户拍板）。
 *
 * 取代此前的「把重编译指令塞进主对话再发出去」：用户点的是「保存」，就该只是保存，
 * 而不是在对话里冒出一段技术指令 + 一轮 AI 生成。服务端在挂了 PPT 技能包的沙箱里
 * 跑编译（PPTX 是二进制，HTML 编辑源必须过一遍技能的编译管线）。
 *
 * ⚠️ **耗时接口**：编译常在几十秒到两分钟，调用方必须保持 loading 且不要自己加超时。
 * nginx 侧是 `proxy_read_timeout 3600s`，不会中途断。
 */
export function compileSlidesDeck(
  slidesFilename: string,
  deckFilename: string,
  threadId?: string,
): Promise<{ file: UserFileItem; log?: string }> {
  return request('/slides/compile', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      slides_filename: slidesFilename,
      deck_filename: deckFilename,
      thread_id: threadId || null,
    }),
  });
}

/** 带鉴权拿文本类文件原文（产物面板预览用；走 download 端点，不受 content 端点 50k 截断） */
export async function fetchUserFileText(id: string): Promise<string> {
  const response = await fetch(`/agent-api/files/${id}/download`, { headers: agentAuthHeaders() });
  if (!response.ok) throw new Error(`加载失败：${response.status}`);
  return response.text();
}

// ===== 版本历史（Phase B §3.3）=====

export function listFileVersions(
  fileId: string,
): Promise<{ file: UserFileItem; versions: UserFileVersion[] }> {
  return request(`/${fileId}/versions`, { method: 'GET' });
}

export async function downloadFileVersion(fileId: string, ver: UserFileVersion): Promise<void> {
  const response = await fetch(`/agent-api/files/${fileId}/versions/${ver.id}/download`, {
    headers: agentAuthHeaders(),
  });
  if (!response.ok) throw new Error(`下载失败：${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `v${ver.versionNo}_${ver.filename}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 恢复历史版本为当前版：以历史字节创建新的 restored 版本，不删除后续历史 */
export function restoreFileVersion(fileId: string, versionId: string): Promise<UserFileItem> {
  return request(`/${fileId}/versions/${versionId}/restore`, { method: 'POST' });
}

/** 「保留」：generated 产物清 TTL 转永久 */
export function keepUserFile(id: string): Promise<UserFileItem> {
  return request<UserFileItem>(`/${id}/keep`, { method: 'POST' });
}

export type UserFileContent = {
  id: string;
  filename: string;
  mime: string;
  size: number;
  kind: string; // text / pdf / docx / image
  text: string;
  truncated: boolean;
};

/** 预览内容：文本类回原文，pdf/docx 回解析文本，图片回 kind=image（另走 blob 展示） */
export function getUserFileContent(id: string): Promise<UserFileContent> {
  return request<UserFileContent>(`/${id}/content`);
}

/** 带鉴权拿文件 blob URL（图片/对话内联预览用；调用方负责 URL.revokeObjectURL）。
    可选 mime 显式覆盖：download 端点可能回 octet-stream，svg/pdf 没有正确 type 渲染不出来 */
export async function fetchUserFileBlobUrl(id: string, mime?: string): Promise<string> {
  const response = await fetch(`/agent-api/files/${id}/download`, { headers: agentAuthHeaders() });
  if (!response.ok) throw new Error(`加载失败：${response.status}`);
  if (!mime) return URL.createObjectURL(await response.blob());
  return URL.createObjectURL(new Blob([await response.arrayBuffer()], { type: mime }));
}

/** 版式文档（doc/docx/ppt/pptx）高保真预览：后端沙箱 LibreOffice 转 PDF。
    首次转换约几秒~30s（按 file_id 缓存后秒回）；调用方负责 revokeObjectURL。 */
export async function fetchUserFilePreviewPdfUrl(id: string): Promise<string> {
  const response = await fetch(`/agent-api/files/${id}/preview`, { headers: agentAuthHeaders() });
  if (!response.ok) {
    let message = `预览生成失败：${response.status}`;
    try {
      const data = await response.json();
      message = data?.detail || data?.message || message;
    } catch {
      // 非 JSON 响应时保留状态码信息
    }
    throw new Error(message);
  }
  return URL.createObjectURL(await response.blob());
}

/** 经带鉴权头的 fetch 拿 blob 再触发浏览器下载（下载端点需要鉴权，不能裸 <a href>） */
export async function downloadUserFile(item: UserFileItem): Promise<void> {
  const response = await fetch(`/agent-api/files/${item.id}/download`, {
    headers: agentAuthHeaders(),
  });
  if (!response.ok) throw new Error(`下载失败：${response.status}`);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = item.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
