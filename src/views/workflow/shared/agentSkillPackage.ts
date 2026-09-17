export function buildAgentSkillUploadHeaders(token?: string) {
  const headers: Record<string, string> = {};
  if (token) {
    headers['X-Access-Token'] = token;
    headers.Authorization = token;
  }
  return headers;
}
