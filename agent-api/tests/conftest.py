# -*- coding: utf-8 -*-
"""全局测试夹具/桩。

## MySQL 专有列类型在 sqlite 内存库上的编译 shim

不少用例用 `sqlite+aiosqlite://` 内存库承载 MySQL 模型（`Base.metadata.create_all`），
而 `app/models.py` 里有两种 MySQL 方言类型 sqlite 编译器不认识：

- `MEDIUMTEXT`（消息附件/执行轨迹等大文本）→ 按 `TEXT` 编译；
- `LONGBLOB`（`MainChatSkinAsset.content` / `SubAgentSkinAsset.content` 皮肤资源）→ 按 `BLOB` 编译。

此前 MEDIUMTEXT 的 shim 散在各测试文件里各写一份，LONGBLOB 则没人写——皮肤资源表进
`Base.metadata` 之后，所有做整库 `create_all` 的 sqlite 用例统统在 setup 阶段报
`can't render element of type LONGBLOB`。`@compiles` 是进程级注册，放在 conftest 里一次
生效即可；各文件里已有的 MEDIUMTEXT shim 与这里等价，保留无害。
"""
from sqlalchemy.dialects.mysql import LONGBLOB, MEDIUMTEXT
from sqlalchemy.ext.compiler import compiles


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    return "TEXT"


@compiles(LONGBLOB, "sqlite")
def _longblob_on_sqlite(_element, _compiler, **_kw):
    return "BLOB"
