"""服务层总索引。

主对话执行内核位于 ``agent_harness/``，入口、协议、状态和模型驱动都以该包为准。

域包：
    agents/     模型目录(按用户 Key 解析可用对话模型)
    chat/       主对话域分层(回合/收尾/RunHub/工具集——包内 __init__ 有一眼地图)
    files/      「我的文件」/会话附件/文档解析
    gateway/    工具调用网关(幂等/审批)与 MCP 客户端/公网 URL 守卫
    knowledge/  知识库检索/embedding/引用/联网搜索
    memory/     长期记忆/个性化/上下文压缩
    platform/   Key/配置/模型窗口/迁移回填/token 估算等横切支撑
    skills/     Skill 目录/技能包桥接/PPT 技能策略
    tasks/      Run 生命周期事实源/计划/运行中输入/对账/队列

其余运行内核：sandbox/(bash 沙箱 provider)。
"""
