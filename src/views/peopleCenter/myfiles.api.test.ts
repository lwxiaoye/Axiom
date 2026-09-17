/**
 * listUserFiles 的查询串（2026-07-27）。
 *
 * 后端 /files 默认 `deliverables_only=not show_all`，`show_all=true` 是拿回非交付物的
 * **唯一**逃生口。前端此前从不带这个参数，等于逃生口在生产上根本不存在：模型 download_url
 * 取回的 PDF、生成的 .py/.zip 落库占配额，却没有任何入口能看到或下载。
 */
jest.mock(
  '/@/store/modules/user',
  () => ({
    useUserStore: () => ({ getToken: 'tk', getUserInfo: { id: 7, username: 'u7' } }),
  }),
  { virtual: true },
);
jest.mock(
  '/@/utils/auth',
  () => ({
    getToken: () => 'tk',
  }),
  { virtual: true },
);

import { listUserFiles } from './myfiles.api';
import { PICKER_FOLDER_ID } from './composables/filePicker';

const okJson = () => ({ ok: true, json: async () => ({ files: [], quota: null }) }) as any;

function lastUrl(fetchMock: jest.Mock): string {
  return String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0]);
}

describe('listUserFiles 查询串', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn(async () => okJson());
    (globalThis as any).fetch = fetchMock;
  });

  it('默认不带任何参数（保持克制视图：只列交付物）', async () => {
    await listUserFiles();
    expect(lastUrl(fetchMock)).toBe('/agent-api/files');
  });

  it('showAll=true 带上 show_all（逃生口）', async () => {
    await listUserFiles(null, true);
    expect(lastUrl(fetchMock)).toBe('/agent-api/files?show_all=true');
  });

  it('文件夹视图下两个参数并存，且 folder_id 仍被编码', async () => {
    await listUserFiles('工作 区/A', true);
    const url = lastUrl(fetchMock);
    expect(url).toContain('folder_id=%E5%B7%A5%E4%BD%9C+%E5%8C%BA%2FA');
    expect(url).toContain('show_all=true');
  });

  it('showAll=false 不发参数（省得后端把空串当真值）', async () => {
    await listUserFiles('__all__', false);
    expect(lastUrl(fetchMock)).toBe('/agent-api/files?folder_id=__all__');
  });

  it('composer 选择器的取数形状：扁平全量 + 逃生口（2026-07-28 之前少了第二个参数）', async () => {
    // 选择器一直只发 folder_id=__all__，于是「我的文件」里看得到的 .py / 取回的材料
    // 在这里选不到——「带进下一轮」那条路是断的
    await listUserFiles(PICKER_FOLDER_ID, true);
    expect(lastUrl(fetchMock)).toBe('/agent-api/files?folder_id=__all__&show_all=true');
  });
});
