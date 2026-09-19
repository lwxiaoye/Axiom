# -*- coding: utf-8 -*-
"""全局测试夹具/桩。

## MySQL 专有列类型在 sqlite 内存库上的编译 shim

不少用例用 `sqlite+aiosqlite://` 内存库承载 MySQL 模型（`Base.metadata.create_all`），
而 `app/models.py` 里有 MySQL 方言类型 sqlite 编译器不认识：

- `MEDIUMTEXT`（消息附件/执行轨迹等大文本）→ 按 `TEXT` 编译。

此前 MEDIUMTEXT 的 shim 散在各测试文件里各写一份；`@compiles` 是进程级注册，放在 conftest 里
一次生效即可；各文件里已有的 MEDIUMTEXT shim 与这里等价，保留无害。
（皮肤资源表的 `LONGBLOB` 已随皮肤系统一起删除，对应 shim 不再需要。）
"""
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.ext.compiler import compiles


@compiles(MEDIUMTEXT, "sqlite")
def _mediumtext_on_sqlite(_element, _compiler, **_kw):
    return "TEXT"
