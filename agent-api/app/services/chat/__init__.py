"""主对话回合组件：由 ``agent_harness`` 唯一内核编排。

一眼地图(按一轮消息的生命周期排序):

    agent_harness/orchestrator.py  回合编排；只由 HarnessKernel 调用。
    run_hub.py          Run 并发治理:后台泵/订阅回放/取消/僵尸仲裁/部分正文落库。
                        进程内事件分发器,Runtime PG 是事实源。时序敏感(P0/P1 批注)。
    turn_context_builder.py  上下文准备纯函数:system prompt/附件拆分/技能回源(防注入)/
                        预算估算/取消意图/推荐意图。无状态,可单测。
    turn_prepare.py     回合准备:自动路由 + 并发预取(智能体/技能/记忆/个性化/目录)+
                        推荐检索 → TurnContext。
    types.py            内部契约:TurnContext(准备结果)/ TurnOutcome(循环聚合,dict 兼容)/
                        TurnEnv(轮级环境,骨架构造一次、回合体共享)。
    subagent_turn.py    子智能体回合:整轮委派(run_dispatch_turn)+ call_subagent 的
                        runner/流式 runner 工厂 + 外部应用推荐兜底。
    main_tool_turn.py   工具循环回合:run_agent_turn(主路径,orchestrator 分派)+
                        循环事件流→SSE 帧映射(map_tool_loop_events)+ 轨迹换算。
    plain_turn.py       直答回退回合:工具循环零输出异常时的普通问答(超窗压缩重试)。
    turn_finalizer.py   收尾:消息持久化/部分正文落库/标题生成 + finalize_terminal
                        (终态 CAS 唯一实现)/ stream_resume_events(续接轮流式段唯一实现,
                        取消/异常兜底落库)/ finalize_resume_turn(续接轮统一收尾)。
    tools/              主对话工具集(按域):base(契约)/plan/web/knowledge/workspace/memory。
    agent_harness/model_driver.py  单一模型工具循环与安全网。
"""
