export type ParamLocation = 'path' | 'query' | 'header' | 'body';
export type ParamType = 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'object' | 'file';

export type ParamRow = {
  key: string;
  type: ParamType;
  required: boolean;
  description: string;
};

export type HttpToolItem = {
  name: string;
  method: string;
  path: string;
  description: string;
  pathParams: ParamRow[];
  queryParams: ParamRow[];
  headerParams: ParamRow[];
  bodyParams: ParamRow[];
  inputSchema?: Record<string, any>;
};

export type HttpToolSetConfig = {
  baseUrl: string;
  headers: Array<{ key: string; value: string }>;
  toolList: HttpToolItem[];
};

export type ValidationIssue = {
  code: 'base-url' | 'empty-tools' | 'tool-name' | 'duplicate-tool' | 'tool-path' | 'param-name' | 'duplicate-param' | 'missing-path-param' | 'unused-path-param' | 'file-location';
  message: string;
  toolIndex?: number;
  location?: ParamLocation;
  paramIndex?: number;
};

const LOCATIONS: Array<{ key: keyof Pick<HttpToolItem, 'pathParams' | 'queryParams' | 'headerParams' | 'bodyParams'>; location: ParamLocation }> = [
  { key: 'pathParams', location: 'path' },
  { key: 'queryParams', location: 'query' },
  { key: 'headerParams', location: 'header' },
  { key: 'bodyParams', location: 'body' },
];

export function createEmptyTool(): HttpToolItem {
  return {
    name: '',
    method: 'GET',
    path: '',
    description: '',
    pathParams: [],
    queryParams: [],
    headerParams: [],
    bodyParams: [],
  };
}

export function createEmptyConfig(): HttpToolSetConfig {
  return { baseUrl: '', headers: [], toolList: [] };
}

export function parseHttpToolSetConfig(raw: any): HttpToolSetConfig {
  const source = raw && typeof raw === 'object' ? raw : {};
  return {
    baseUrl: String(source.baseUrl || ''),
    headers: Array.isArray(source.headers)
      ? source.headers.map((row: any) => ({ key: String(row?.key || ''), value: String(row?.value || '') }))
      : [],
    toolList: Array.isArray(source.toolList) ? source.toolList.map(parseTool) : [],
  };
}

function parseTool(raw: any): HttpToolItem {
  const tool = createEmptyTool();
  tool.name = String(raw?.name || '');
  tool.method = String(raw?.method || 'GET').toUpperCase();
  tool.path = String(raw?.path || '');
  tool.description = String(raw?.description || '');

  const hasLocations = LOCATIONS.some(({ key }) => Array.isArray(raw?.[key]));
  if (hasLocations) {
    for (const { key } of LOCATIONS) tool[key] = parseRows(raw?.[key]);
  } else {
    const rows = schemaToRows(raw?.inputSchema);
    const placeholders = new Set(extractPathPlaceholders(tool.path));
    tool.pathParams = rows.filter((row) => placeholders.has(row.key));
    const remaining = rows.filter((row) => !placeholders.has(row.key));
    if (tool.method === 'GET') tool.queryParams = remaining;
    else tool.bodyParams = remaining;
  }
  return tool;
}

function parseRows(rows: any): ParamRow[] {
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => ({
    key: String(row?.key || ''),
    type: normalizeType(row?.type),
    required: !!row?.required,
    description: String(row?.description || ''),
  }));
}

function schemaToRows(schema: any): ParamRow[] {
  const properties = schema && typeof schema === 'object' ? schema.properties || {} : {};
  const required = new Set(Array.isArray(schema?.required) ? schema.required.map(String) : []);
  return Object.entries(properties).map(([key, value]: [string, any]) => ({
    key,
    type: normalizeType(value?.type),
    required: required.has(key),
    description: String(value?.description || ''),
  }));
}

export function serializeHttpToolSetConfig(config: HttpToolSetConfig): HttpToolSetConfig {
  return {
    baseUrl: config.baseUrl.trim().replace(/\/+$/, ''),
    headers: config.headers.filter((row) => row.key.trim()).map((row) => ({ key: row.key.trim(), value: row.value })),
    toolList: config.toolList.map((tool) => {
      const normalized: HttpToolItem = {
        ...tool,
        name: tool.name.trim(),
        method: tool.method.toUpperCase(),
        path: normalizePath(tool.path),
        description: tool.description.trim(),
        pathParams: cleanRows(tool.pathParams),
        queryParams: cleanRows(tool.queryParams),
        headerParams: cleanRows(tool.headerParams),
        bodyParams: cleanRows(tool.bodyParams),
      };
      const allRows = LOCATIONS.flatMap(({ key }) => normalized[key]);
      if (allRows.length) normalized.inputSchema = rowsToSchema(allRows);
      else delete normalized.inputSchema;
      return normalized;
    }),
  };
}

function cleanRows(rows: ParamRow[]): ParamRow[] {
  return rows.filter((row) => row.key.trim()).map((row) => ({ ...row, key: row.key.trim(), description: row.description.trim() }));
}

function rowsToSchema(rows: ParamRow[]): Record<string, any> {
  const properties: Record<string, any> = {};
  for (const row of rows) {
    const isFile = row.type === 'file';
    const suffix = '（平台文件引用或受保护文件 URL）';
    const description = isFile ? `${row.description || row.key}${suffix}` : row.description;
    properties[row.key] = {
      type: isFile ? 'string' : row.type,
      ...(description ? { description } : {}),
    };
  }
  return { type: 'object', properties, required: rows.filter((row) => row.required).map((row) => row.key) };
}

export function importOpenApiDocument(schema: any): { baseUrl: string; tools: HttpToolItem[] } {
  const tools: HttpToolItem[] = [];
  const usedNames = new Set<string>();
  Object.entries(schema?.paths || {}).forEach(([path, pathItem]: [string, any]) => {
    Object.entries(pathItem || {}).forEach(([method, operation]: [string, any]) => {
      if (!['get', 'post', 'put', 'delete', 'patch'].includes(method)) return;
      const tool = createEmptyTool();
      tool.method = method.toUpperCase();
      tool.path = path;
      tool.name = uniqueName(String(operation?.operationId || `${method}_${path}`.replace(/\W+/g, '_')).replace(/^_+|_+$/g, ''), usedNames);
      tool.description = String(operation?.description || operation?.summary || '');

      const parameters = mergeParameters(pathItem?.parameters, operation?.parameters);
      for (const param of parameters) {
        const locationKey = param?.in === 'path' ? 'pathParams' : param?.in === 'query' ? 'queryParams' : param?.in === 'header' ? 'headerParams' : null;
        if (!locationKey || !param?.name) continue;
        tool[locationKey].push({
          key: String(param.name),
          type: normalizeType(param?.schema?.type || param?.type),
          required: param.in === 'path' || !!param.required,
          description: String(param?.description || param?.schema?.description || ''),
        });
      }

      const content = operation?.requestBody?.content || {};
      const multipart = content['multipart/form-data']?.schema;
      const bodySchema = multipart || content['application/json']?.schema || content['application/x-www-form-urlencoded']?.schema;
      if (bodySchema?.properties) {
        const required = new Set(Array.isArray(bodySchema.required) ? bodySchema.required.map(String) : []);
        Object.entries(bodySchema.properties).forEach(([key, value]: [string, any]) => {
          tool.bodyParams.push({
            key,
            type: multipart && value?.type === 'string' && value?.format === 'binary' ? 'file' : normalizeType(value?.type),
            required: required.has(key),
            description: String(value?.description || ''),
          });
        });
      }
      tools.push(tool);
    });
  });
  return { baseUrl: String(schema?.servers?.[0]?.url || ''), tools };
}

function mergeParameters(pathRows: any, operationRows: any): any[] {
  const merged = new Map<string, any>();
  for (const row of [...(Array.isArray(pathRows) ? pathRows : []), ...(Array.isArray(operationRows) ? operationRows : [])]) {
    if (row?.name && row?.in) merged.set(`${row.in}:${row.name}`, row);
  }
  return [...merged.values()];
}

function uniqueName(seed: string, used: Set<string>): string {
  const base = seed || 'http_tool';
  let name = base;
  let index = 2;
  while (used.has(name)) name = `${base}_${index++}`;
  used.add(name);
  return name;
}

export function validateHttpToolSetConfig(config: HttpToolSetConfig): { valid: boolean; issues: ValidationIssue[] } {
  const issues: ValidationIssue[] = [];
  try {
    const url = new URL(config.baseUrl.trim());
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('protocol');
  } catch {
    issues.push({ code: 'base-url', message: '服务地址必须是有效的 HTTP 或 HTTPS URL' });
  }
  if (!config.toolList.length) issues.push({ code: 'empty-tools', message: '请至少添加一个接口' });
  const toolNames = new Set<string>();
  config.toolList.forEach((tool, toolIndex) => {
    const name = tool.name.trim();
    if (!name) issues.push({ code: 'tool-name', message: '请填写工具名', toolIndex });
    else if (toolNames.has(name)) issues.push({ code: 'duplicate-tool', message: `工具名 ${name} 重复`, toolIndex });
    toolNames.add(name);
    if (!tool.path.trim()) issues.push({ code: 'tool-path', message: '请填写接口路径', toolIndex });

    const allNames = new Map<string, ParamLocation>();
    for (const { key, location } of LOCATIONS) {
      tool[key].forEach((row, paramIndex) => {
        const paramName = row.key.trim();
        if (!paramName) issues.push({ code: 'param-name', message: '请填写参数名', toolIndex, location, paramIndex });
        else if (allNames.has(paramName)) issues.push({ code: 'duplicate-param', message: `参数 ${paramName} 不能出现在多个位置`, toolIndex, location, paramIndex });
        else allNames.set(paramName, location);
        if (row.type === 'file' && location !== 'body') issues.push({ code: 'file-location', message: '文件参数只能用于请求 Body', toolIndex, location, paramIndex });
      });
    }
    const placeholders = new Set(extractPathPlaceholders(tool.path));
    const declaredPath = new Set(tool.pathParams.map((row) => row.key.trim()).filter(Boolean));
    placeholders.forEach((name) => {
      if (!declaredPath.has(name)) issues.push({ code: 'missing-path-param', message: `路径参数 ${name} 尚未声明`, toolIndex, location: 'path' });
    });
    declaredPath.forEach((name) => {
      if (!placeholders.has(name)) issues.push({ code: 'unused-path-param', message: `路径中未使用参数 ${name}`, toolIndex, location: 'path' });
    });
  });
  return { valid: !issues.length, issues };
}

export function extractPathPlaceholders(path: string): string[] {
  const names = new Set<string>();
  const pattern = /\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}|\$\{\s*([A-Za-z_][\w.-]*)\s*\}|(?<!\{)\{\s*([A-Za-z_][\w.-]*)\s*\}(?!\})/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(path))) names.add(match[1] || match[2] || match[3]);
  return [...names];
}

export function buildHttpToolTestFormData(
  config: HttpToolSetConfig,
  toolName: string,
  values: Record<string, unknown>,
  files: Record<string, Blob>
): FormData {
  const form = new FormData();
  form.append('config', JSON.stringify(serializeHttpToolSetConfig(config)));
  form.append('toolName', toolName);
  form.append('values', JSON.stringify(values));
  const entries = Object.entries(files).filter(([, file]) => file instanceof Blob);
  form.append('fileParams', JSON.stringify(entries.map(([key]) => key)));
  for (const [key, file] of entries) {
    const filename = 'name' in file && typeof file.name === 'string' ? file.name : `${key}.bin`;
    form.append('files', file, filename);
  }
  return form;
}

function normalizeType(value: any): ParamType {
  return ['number', 'integer', 'boolean', 'array', 'object', 'file'].includes(value) ? value : 'string';
}

function normalizePath(path: string): string {
  const value = path.trim();
  if (!value || value.startsWith('/')) return value;
  return `/${value}`;
}
