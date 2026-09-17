"""服务层总索引。

主对话执行内核位于 ``agent_harness/``，入口、协议、状态和模型驱动都以该包为准。

域包：
    agents/     子智能体运行/同步/注册/路由/发布可见性
    chat/       主对话域分层(回合/收尾/RunHub/工具集——包内 __init__ 有一眼地图)
    files/      「我的文件」/会话附件/文档解析
    gateway/    工具调用网关(幂等/审批)与 HTTP/MCP 执行原语
    knowledge/  知识库检索/embedding/引用/联网搜索
    memory/     长期记忆/个性化/上下文压缩
    platform/   Key/配置/模型窗口/迁移回填/token 估算等横切支撑
    skills/     Skill 运行时/技能包桥接/内置系统工具
    tasks/      Run 生命周期事实源/计划/运行中输入/对账/队列
    workflows/  画布工作流引擎与节点模板

其余运行内核：sandbox/(bash 沙箱 provider)、workflow_runtime/(工作台运行时)。
"""
