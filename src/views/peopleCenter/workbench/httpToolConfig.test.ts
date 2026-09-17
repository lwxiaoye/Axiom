import {
  importOpenApiDocument,
  buildHttpToolTestFormData,
  parseHttpToolSetConfig,
  serializeHttpToolSetConfig,
  validateHttpToolSetConfig,
  type HttpToolSetConfig,
} from './httpToolConfig';

const property = (type = 'string') => ({ type: 'object', properties: { userId: { type, description: '用户 ID' } }, required: ['userId'] });

describe('HTTP tool configuration model', () => {
  it('migrates legacy GET inputs to query parameters', () => {
    const parsed = parseHttpToolSetConfig({
      baseUrl: 'https://api.example.com',
      toolList: [{ name: 'getUser', method: 'GET', path: '/users', inputSchema: property() }],
    });

    expect(parsed.toolList[0].queryParams).toEqual([
      { key: 'userId', type: 'string', required: true, description: '用户 ID' },
    ]);
    expect(parsed.toolList[0].bodyParams).toEqual([]);
  });

  it('migrates legacy placeholders to path parameters', () => {
    const parsed = parseHttpToolSetConfig({
      toolList: [{ name: 'getUser', method: 'GET', path: '/users/{{userId}}', inputSchema: property() }],
    });

    expect(parsed.toolList[0].pathParams[0].key).toBe('userId');
    expect(parsed.toolList[0].queryParams).toEqual([]);
  });

  it('serializes all locations into one model input schema', () => {
    const config = makeConfig();
    config.toolList[0].queryParams.push({ key: 'page', type: 'integer', required: false, description: '页码' });
    config.toolList[0].bodyParams.push({ key: 'avatar', type: 'file', required: true, description: '头像' });

    const saved = serializeHttpToolSetConfig(config);
    expect(saved.toolList[0].inputSchema?.properties).toEqual({
      page: { type: 'integer', description: '页码' },
      avatar: { type: 'string', description: '头像（平台文件引用或受保护文件 URL）' },
    });
    expect(saved.toolList[0].inputSchema?.required).toEqual(['avatar']);
  });

  it('imports OpenAPI parameters and multipart binary fields by location', () => {
    const result = importOpenApiDocument({
      openapi: '3.0.0',
      servers: [{ url: 'https://api.example.com' }],
      paths: {
        '/users/{userId}': {
          get: {
            operationId: 'getUser',
            parameters: [
              { name: 'userId', in: 'path', required: true, schema: { type: 'string' } },
              { name: 'page', in: 'query', schema: { type: 'integer' } },
              { name: 'X-Locale', in: 'header', schema: { type: 'string' } },
            ],
          },
        },
        '/avatars': {
          post: {
            operationId: 'uploadAvatar',
            requestBody: {
              required: true,
              content: {
                'multipart/form-data': {
                  schema: {
                    type: 'object',
                    required: ['avatar'],
                    properties: { avatar: { type: 'string', format: 'binary' }, caption: { type: 'string' } },
                  },
                },
              },
            },
          },
        },
      },
    });

    expect(result.baseUrl).toBe('https://api.example.com');
    expect(result.tools[0].pathParams[0].key).toBe('userId');
    expect(result.tools[0].queryParams[0].key).toBe('page');
    expect(result.tools[0].headerParams[0].key).toBe('X-Locale');
    expect(result.tools[1].bodyParams).toEqual([
      { key: 'avatar', type: 'file', required: true, description: '' },
      { key: 'caption', type: 'string', required: false, description: '' },
    ]);
  });

  it('reports duplicate parameter names and undeclared path placeholders', () => {
    const config = makeConfig();
    config.toolList[0].path = '/users/{userId}';
    config.toolList[0].queryParams.push({ key: 'page', type: 'integer', required: false, description: '' });
    config.toolList[0].bodyParams.push({ key: 'page', type: 'string', required: false, description: '' });

    const codes = validateHttpToolSetConfig(config).issues.map((issue) => issue.code);
    expect(codes).toContain('duplicate-param');
    expect(codes).toContain('missing-path-param');
  });

  it('serializes test files with a stable parameter mapping', () => {
    const file = new Blob(['png'], { type: 'image/png' });
    const form = buildHttpToolTestFormData(makeConfig(), 'getUser', { caption: 'profile' }, { avatar: file });

    expect(JSON.parse(String(form.get('values')))).toEqual({ caption: 'profile' });
    expect(JSON.parse(String(form.get('fileParams')))).toEqual(['avatar']);
    expect(form.getAll('files')).toHaveLength(1);
  });
});

function makeConfig(): HttpToolSetConfig {
  return {
    baseUrl: 'https://api.example.com',
    headers: [],
    toolList: [
      {
        name: 'getUser',
        method: 'GET',
        path: '/users',
        description: '',
        pathParams: [],
        queryParams: [],
        headerParams: [],
        bodyParams: [],
      },
    ],
  };
}
