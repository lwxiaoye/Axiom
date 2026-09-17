# 运行页外观（“衣服”）开发约定

这套机制把四件事分开：

1. 前端只提供经过审计的渲染器；衣服可以是随版本发布的代码内置预设，也可以是可导入的声明式 `.axiomskin` 包。
2. 数据库负责登记全局皮肤目录、素材和智能体分配结果。
3. 工作流编辑器的“外观试衣间”展示系统已安装的所有皮肤，不按租户拆分，不需单独授权。
4. 后端在保存、发布、审核、回滚和运行时重复校验皮肤是否安装、素材是否完整、分配是否一致。

一件完整的衣服可以同时控制三个区域：

- 左侧会话栏：通过 `sidebarDecoration` 和 `--run-left-*` 设计 token 定制。
- 中间对话区：通过 `backdrop`、`composerDecoration` 和对话区 token 定制。
- 右侧推荐栏：通过 `inspirationDecoration` 和 `--run-right-*` 设计 token 定制。

代码内置预设使用受控 Vue 组件；可移植皮肤使用唯一的 `decorated-agent-run-v1` 通用组件。数据库只保存已校验的 manifest、图片字节和分配 key，不保存或执行导入包里的 CSS、HTML、JavaScript、Vue 组件或远程 URL。

## 新增代码内置衣服

假设新 key 是 `interview-coach-v1`：

1. 在 `presets/interview-coach-v1/` 创建外观组件和 `assets/`，不要从数据库接收 CSS、HTML、组件名或任意 URL。
2. 在 `catalog.ts` 登记可安装的 key、名称和说明。
3. 在 `registry.ts` 把 key 绑定到 code-owned Vue 组件和设计 token。
4. 在 `preview.ts` 把 key 绑定到本地预览图。
5. 在后端 `app/services/workflows/presentation_service.py` 的
   `BUILTIN_PRESENTATION_PRESETS` 登记同一个 key、版本、素材内部引用和 SHA-256。
6. 新增 MySQL Alembic 迁移，向以下两张目录表写入衣服和素材：
   `agent_presentation_preset`、`agent_presentation_preset_asset`。
7. 平台开发人员进入独立导航“子智能体皮肤管理”，在同一张预览中检查左、中、右三栏和桌面、平板、手机三种尺寸。
8. 对 PNG 素材必须检查真实 alpha 通道；不能把浅色背景或透明棋盘格烤进图片。原始图可保留为源素材，运行组件引用 `*-cutout.png` 成品。
9. 拥有该智能体 `OWNER` / `EDITOR` 权限的人，在工作流系统配置的“外观试衣间”选择并保存草稿。
10. 按正常发布审核流程上线；草稿外观和线上外观分别记录，改草稿不会直接影响学生页面。

## 制作并交付可移植衣服

1. 从 `agent-api/skin_packages/sub_agent/` 下的示例目录复制一份源工程，修改 manifest 和 PNG/JPEG/WebP 素材。
2. manifest 必须使用 `kind=axiom-sub-agent-skin`、`scope=sub_agent`、`renderer=decorated-agent-run-v1`，并同时提供 desktop/tablet/mobile 三端布局。
3. 执行：

   ```bash
   PYTHONPATH=agent-api python3 agent-api/scripts/build_sub_agent_skin_package.py \
     agent-api/skin_packages/sub_agent/<source-dir> \
     output/<skin-key>-<version>.axiomskin
   ```

4. 在开发系统的独立“子智能体皮肤管理”页导入包并完成三端试穿；再点击“导出”验证从数据库回读后的包仍可生成。
5. 把导出的 `.axiomskin` 交给客户。客户系统的平台管理员在同一入口直接导入，不需要重新发布前端。
6. 导入后皮肤进入全局目录，不按租户归属，不走皮肤授权开关；智能体编辑者随后在“外观试衣间”选择并走正常草稿/审核/发布流程。

包格式和安全边界以 `docs/可移植皮肤包与前端响应式规范.md` 为准。主对话包与子智能体包不可混用。

## 不能省略的验收

- 未安装或已删除的 key 保存时，后端返回 400；运行时发现皮肤丢失则安全退回标准外观。
- 素材缺失、内部引用或 SHA-256 不匹配时，皮肤不可发布。
- 已发布版本、草稿预览和审核预览不能互相串外观。
- 前端注册表没有该 key 时，即使数据库误登记也只能显示标准外观。
- 可移植包 scope/renderer 不匹配、缺任意端布局、带未声明文件或素材哈希不一致时必须整包拒绝。
- 空白欢迎页与已有对话状态都要验收；输出后需保留的主题元素不能消失，也不能挡住最后一条消息。

## 数据表职责

| 表 | 只负责什么 |
| --- | --- |
| `agent_presentation_preset` | 代码内置衣服的全局目录、版本和渲染 key |
| `agent_presentation_preset_asset` | 代码内置衣服所需素材、内部引用和校验值 |
| `agent_presentation_assignment` | 哪个智能体的草稿/线上版本当前穿哪件衣服 |
| `agent_sub_agent_skin` | 系统全局已安装的不可变可移植皮肤版本和安全 manifest |
| `agent_sub_agent_skin_asset` | 可移植皮肤随包安装的已校验图片字节 |

皮肤本身没有单独授权流程；用户仍需要该智能体的原有运行权限，两者不要混在一起。
