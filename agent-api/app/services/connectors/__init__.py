"""外部应用连接器（GitHub 等）。

分层：providers（注册表/扩展点）→ 各 provider 适配（github.py）→ connector_service
（绑定与授权编排）→ 上层消费（routers/connectors.py 与 chat/tools/connectors.py）。
凭据加解密集中在 crypto.py，明文令牌不越出 service 层。
"""
