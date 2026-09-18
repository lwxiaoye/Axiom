import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { decorateBuiltinCatalogApp, listBuiltinAssistants } from './builtinAssistants/registry';

const peopleCenterRoot = resolve(__dirname);
const source = (relative: string) => readFileSync(resolve(peopleCenterRoot, relative), 'utf8');

describe('系统内置 Harness 应用契约', () => {
  it('两个应用均使用独立受保护路由和新页窗口', () => {
    const routes = source('../../router/routes/mainOut.ts');
    const market = source('composables/useAgentMarket.ts');
    const runPage = source('pages/BuiltinHarnessRunPage.vue');

    expect(routes).toContain("path: '/center/chat/ppt'");
    expect(routes).toContain("props: { preset: 'presentation' }");
    expect(routes).toContain("path: '/center/chat/campus'");
    expect(routes).toContain("props: { preset: 'campus_services' }");
    expect(routes).not.toMatch(/center\/chat\/(?:ppt|campus)[\s\S]{0,220}ignoreAuth/);
    expect(market).toContain('openAgentRunWindow(targetUrl)');
    expect(source('composables/useCenterChat.ts')).toContain('openAgentRunWindow(assistant.route)');
    expect(runPage).toContain('<RunSessionList');
    expect(runPage).toContain('<ChatPage v-else-if="appMeta" />');
    expect(runPage).toContain('<RunCompactHeader');
    expect(runPage).toContain('<Transition name="run-history-backdrop">');
    expect(runPage).not.toContain('show-inspiration');
    expect(runPage).not.toContain('系统内置应用');
  });

  it('主对话、PPT、校园百事通使用三个服务端历史域与草稿域', () => {
    const api = source('agentApi.ts');
    const chat = source('composables/useCenterChat.ts');
    const drafts = source('composables/chatDrafts.ts');
    const runPage = source('pages/BuiltinHarnessRunPage.vue');

    expect(api).toContain("export type ThreadScope = 'ordinary' | AssistantPreset");
    expect(api).toContain("scope=${encodeURIComponent(scope)}");
    expect(chat).toContain('fixedAssistantPreset?: AssistantPreset');
    expect(chat).toContain('getThreads(search ?? threadSearch.value, THREADS_PAGE_SIZE, 0, threadScope)');
    expect(chat).toContain('draftMatchesThreadScope');
    expect(drafts).toContain('const prefix = `new:${scope}:`');
    expect(runPage).toContain('fixedAssistantPreset: props.preset');
    expect(runPage).toContain('threadScope: props.preset');
    expect(source('pages/MyFilesPage.vue')).toContain('openAgentRunWindow(');
    expect(source('pages/MyFilesPage.vue')).toContain('thread=${encodeURIComponent(threadId)}');
  });

  it('应用记录由管理员作为普通外部应用新增、编辑和删除', () => {
    const list = source('../flow/app/AppInfoList.vue');
    const editor = source('../flow/app/components/chooseModel.vue');
    const market = source('composables/useAgentMarket.ts');

    expect(list).toContain("label: '删除'");
    expect(list).not.toContain('isAiAgentApp');
    expect(list).not.toContain('isSystemManagedApp');
    // 编辑器的新建默认值已从 external 改为 custom（组件副标题写明「新增应用默认为自建业务应用，
    // 可按实际交付方式切换为外部接入应用」）；仓库历史是压缩快照，找不到改它的提交。
    // 本契约关心的是「内置应用能作为普通外部应用被录入」，所以只钉 external 仍是可选来源，不钉默认值。
    expect(editor).toContain('<a-radio-button value="external">');
    expect(editor).toContain("if (source === 'external') return 'external';");
    expect(editor).not.toContain('systemManagedCoreSnapshot');
    expect(editor).toContain('selectedRoleValues');
    expect(editor).toContain('selectedDepartValues');
    expect(market).toContain("myAppList({ column: 'createTime', order: 'desc' })");
    expect(market).toContain('decorateBuiltinCatalogApp(item)');
    expect(market).toContain('sortBuiltinCatalogApps(');
    expect(market).not.toContain('getBuiltinApps');
    expect(market).not.toContain('remoteApps.filter');
  });

  it('应用卡片显示管理端记录的创建人头像、姓名和日期', () => {
    const marketTab = source('tabs/AgentMarketTab.vue');

    expect(marketTab).toContain('item.createByAvatar');
    expect(marketTab).toContain('{{ getMarketplaceCreatorName(item) }}');
    expect(marketTab).toContain('{{ formatDate(item.createTime) }}');
    expect(marketTab).not.toContain("isBuiltinAssistant(item) ? '系统内置'");
    expect(marketTab).not.toContain('getBuiltinAssistantById(item.id)');
    expect(marketTab).not.toContain('app-type-badge');
    expect(marketTab).not.toContain('>外部</em>');
  });

  it('已配置应用回退默认照片和描述并置顶，但不静态补卡', () => {
    const registry = source('builtinAssistants/registry.ts');
    const presentation = source('builtinAssistants/presentation/definition.ts');
    const campus = source('builtinAssistants/campusServices/definition.ts');

    for (const assistant of listBuiltinAssistants()) {
      const app = { id: 'configured-app', appName: assistant.name, pcUrl: assistant.route, appRemark: '' };
      for (const appIcon of ['', assistant.legacyIcon || '']) {
        expect(decorateBuiltinCatalogApp({ ...app, appIcon })).toMatchObject({ appIcon: assistant.icon, appRemark: assistant.description });
      }
      expect(decorateBuiltinCatalogApp({ ...app, appIcon: '/uploaded/custom-avatar.png' }).appIcon).toBe('/uploaded/custom-avatar.png');
    }
    expect(registry).toContain('configuredRemark ? item.appRemark : assistant.description');
    expect(registry).toContain('sortBuiltinCatalogApps');
    expect(registry).not.toContain('listBuiltinMarketApps');
    expect(presentation).toContain("icon: '/agent-icons/builtin/presentation-assistant.png'");
    expect(campus).toContain("icon: '/agent-icons/builtin/campus-services.png'");
  });
});
