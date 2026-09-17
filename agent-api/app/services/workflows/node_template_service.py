"""四 Tab 节点模板目录与 preview node 服务（gap-audit §7.3）。

蓝本契约（FastGPT getTeamAppTemplates / getSystemToolTemplates / getPreviewNode）：
- 目录接口只返回模板摘要（NodeTemplateListItemType 子集）；
- 点击动态项时经 previewNode 由服务端 schema 生成完整 FlowNodeTemplateType
  （inputs/outputs/toolConfig/version/pluginId/source），前端不得自拼残缺节点；
- 类型映射：workflowTool→pluginModule，http/mcpToolSet→toolSet(目录，可下钻)，
  工具集子项→tool，simple/chatAgent/workflow 应用→appModule；
- 密钥（headers）永不回传：preview 只带 hasSystemSecret 布尔。

保留差异（登记于 gap-audit 差异登记表）：
- pluginModule 本期单参数 question（多参数依赖 pluginInput/pluginOutput，P2）；
- toolSet 仅作目录不直落画布（工具锚点属 toolCall/agent，P2）；
- 系统工具以 builtin category 作标签、平铺无层级。
"""
import json
from typing import Optional

from sqlalchemy import or_, select

from app.models import WorkflowApp, WorkflowDefinition
from app.services.skills.builtin_tools import BUILTIN_TOOL_MAP, BUILTIN_TOOLS

# 摘要/preview 的 source 标识（蓝本 AppToolSourceEnum 语义）
SOURCE_SYSTEM_TOOL = "systemTool"
SOURCE_HTTP = "http"
SOURCE_MCP = "mcp"
SOURCE_WORKFLOW_TOOL = "workflowTool"
SOURCE_APP = "app"

MY_TOOL_APP_TYPES = ("workflowTool", "httpToolSet", "mcpToolSet")
AGENT_APP_TYPES = ("simple", "chatAgent", "workflow")

# JSON Schema 类型 -> 画布 valueType / 渲染器（蓝本 jsonSchema2NodeInput：具体控件在前，reference 在后）
_SCHEMA_VALUE_TYPE = {
    "string": ("string", ["input", "reference"]),
    "number": ("number", ["numberInput", "reference"]),
    "integer": ("number", ["numberInput", "reference"]),
    "boolean": ("boolean", ["switch", "reference"]),
    "object": ("object", ["JSONEditor", "reference"]),
    "array": ("arrayAny", ["JSONEditor", "reference"]),
}

# 数组 items 类型 -> 数组 valueType（蓝本 jsonSchema2NodeInput）
_ARRAY_ITEM_VALUE_TYPE = {
    "string": "arrayString",
    "number": "arrayNumber",
    "integer": "arrayNumber",
    "boolean": "arrayBoolean",
    "object": "arrayObject",
}


def _parse_config(config_json: Optional[str]) -> dict:
    try:
        parsed = json.loads(config_json or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _error_output() -> dict:
    return {
        "id": "system_error_text",
        "key": "system_error_text",
        "label": "错误信息",
        "type": "error",
        "valueType": "string",
    }


def _schema_to_inputs(parameters: dict) -> list[dict]:
    """JSON Schema(object) -> 节点 inputs（蓝本 jsonSchema2NodeInput 等价实现）。

    覆盖：enum -> select+list（默认值取第一个枚举）；number min/max；array items -> array* 类型；
    items.enum -> multipleSelect；缺省 type -> any + JSONEditor；label 取 title||key。
    """
    properties = (parameters or {}).get("properties") or {}
    required = set((parameters or {}).get("required") or [])
    inputs: list[dict] = []
    for key, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        prop_type = prop.get("type")
        if prop_type is None:
            value_type, render_list = "any", ["JSONEditor", "reference"]
        else:
            value_type, render_list = _SCHEMA_VALUE_TYPE.get(str(prop_type), _SCHEMA_VALUE_TYPE["string"])
        description = str(prop.get("description") or "")
        item: dict = {
            "key": key,
            "label": str(prop.get("title") or key),
            "renderTypeList": list(render_list),
            "valueType": value_type,
            "required": key in required,
            "description": description or None,
            "toolDescription": description or key,
        }
        if prop.get("multiline") is True and str(prop_type) == "string":
            item["renderTypeList"] = ["textarea", "reference"]
        if prop.get("secret") is True and str(prop_type) == "string":
            item["renderTypeList"] = ["password", "reference"]
        if prop.get("modelSelector") is True and str(prop_type) == "string":
            item["renderTypeList"] = ["selectLLMModel"]
        enum_values = prop.get("enum") if isinstance(prop.get("enum"), list) else None
        items_schema = prop.get("items") if isinstance(prop.get("items"), dict) else {}
        if enum_values:
            item["renderTypeList"] = ["select", "reference"]
            enum_labels = prop.get("enumLabels") if isinstance(prop.get("enumLabels"), dict) else {}
            item["list"] = [{"label": str(enum_labels.get(str(v), v)), "value": v} for v in enum_values]
            item["value"] = enum_values[0]
            if prop.get("searchable") is True:
                item["searchable"] = True
        elif str(prop_type) == "array":
            if isinstance(items_schema.get("enum"), list):
                item["renderTypeList"] = ["multipleSelect", "reference"]
                item["list"] = [{"label": str(v), "value": v} for v in items_schema["enum"]]
            item["valueType"] = _ARRAY_ITEM_VALUE_TYPE.get(str(items_schema.get("type") or ""), "arrayAny")
        if str(prop_type) in ("number", "integer"):
            if isinstance(prop.get("minimum"), (int, float)):
                item["min"] = prop["minimum"]
            if isinstance(prop.get("maximum"), (int, float)):
                item["max"] = prop["maximum"]
        if prop.get("default") is not None and "value" not in item:
            item["value"] = prop.get("default")
        inputs.append(item)
    return inputs


def _raw_response_output() -> dict:
    """蓝本子工具唯一/首要输出：system_rawResponse（原始响应）。"""
    return {
        "id": "system_rawResponse",
        "key": "system_rawResponse",
        "label": "原始响应",
        "description": "工具的原始响应",
        "type": "static",
        "valueType": "any",
    }


def _summary(
    *,
    id_: str,
    flow_node_type: str,
    name: str,
    intro: str = "",
    avatar: str = "",
    is_folder: bool = False,
    source: Optional[str] = None,
    plugin_id: Optional[str] = None,
    version: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    return {
        "id": id_,
        "pluginId": plugin_id or id_,
        "flowNodeType": flow_node_type,
        "templateType": "teamApp",
        "avatar": avatar,
        "name": name,
        "intro": intro,
        "isFolder": is_folder,
        "isTool": True,
        **({"source": source} if source else {}),
        **({"version": version} if version else {}),
        **({"status": status} if status else {}),
    }


def _match(keyword: Optional[str], *texts: str) -> bool:
    if not keyword or not keyword.strip():
        return True
    needle = keyword.strip().lower()
    return any(needle in (text or "").lower() for text in texts)


def list_system_tool_tags() -> list[dict]:
    seen: list[str] = []
    for tool in BUILTIN_TOOLS:
        category = str(tool.get("category") or "其他")
        if category not in seen:
            seen.append(category)
    return [{"id": category, "label": category} for category in seen]


def list_system_tool_templates(search_key: Optional[str], tags: Optional[list[str]]) -> list[dict]:
    items: list[dict] = []
    for tool in BUILTIN_TOOLS:
        if tags and str(tool.get("category") or "其他") not in tags:
            continue
        if not _match(search_key, str(tool.get("name")), str(tool.get("description"))):
            continue
        items.append(
            _summary(
                id_=str(tool["id"]),
                flow_node_type="tool",
                name=str(tool["name"]),
                intro=str(tool.get("description") or ""),
                source=SOURCE_SYSTEM_TOOL,
                version="builtin",
            )
        )
    return items


async def _visible_apps(session, user, app_types: tuple, search_key: Optional[str]) -> list[WorkflowApp]:
    """owner/ACL 可见的应用（与 page_apps 同一可见性规则）。"""
    from app.models import WorkflowAcl

    acl_subject = or_(
        (WorkflowAcl.subject_type == "USER") & (WorkflowAcl.subject_id == user.user_id),
        (WorkflowAcl.subject_type == "ROLE") & (WorkflowAcl.subject_id.in_(user.role_ids or [""])),
        (WorkflowAcl.subject_type == "DEPARTMENT") & (WorkflowAcl.subject_id.in_(user.dept_ids or [""])),
    )
    shared_ids = select(WorkflowAcl.app_id).where(acl_subject)
    query = select(WorkflowApp).where(
        or_(WorkflowApp.owner_user_id == user.user_id, WorkflowApp.id.in_(shared_ids)),
        WorkflowApp.ai_app_type.in_(app_types),
    )
    if search_key and search_key.strip():
        like = f"%{search_key.strip()}%"
        query = query.where(or_(WorkflowApp.name.like(like), WorkflowApp.description.like(like)))
    rows = (await session.execute(query.order_by(WorkflowApp.update_time.desc()).limit(200))).scalars().all()
    return list(rows)


def _toolset_children(app: WorkflowApp, search_key: Optional[str]) -> list[dict]:
    """工具集下钻：config_json.toolList -> tool 子项（id 蓝本约定 `{source}-{appId}/{toolName}`）。"""
    config = _parse_config(app.config_json)
    source = SOURCE_MCP if str(app.ai_app_type) == "mcpToolSet" else SOURCE_HTTP
    children: list[dict] = []
    for item in config.get("toolList") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        if not _match(search_key, name, str(item.get("description") or "")):
            continue
        children.append(
            _summary(
                id_=f"{source}-{app.id}/{name}",
                plugin_id=str(app.id),
                flow_node_type="tool",
                name=name,
                intro=str(item.get("description") or ""),
                avatar=str(app.app_icon or ""),
                source=source,
            )
        )
    return children


async def list_templates(
    session,
    user,
    *,
    tab: str,
    search_key: Optional[str] = None,
    parent_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    exclude_app_id: Optional[str] = None,
) -> list[dict]:
    if tab == "systemTools":
        return list_system_tool_templates(search_key, tags)

    if tab == "myTools":
        if parent_id:
            app = await session.get(WorkflowApp, parent_id)
            if app is None or str(app.ai_app_type) not in ("httpToolSet", "mcpToolSet"):
                return []
            return _toolset_children(app, search_key)
        apps = await _visible_apps(session, user, MY_TOOL_APP_TYPES, search_key)
        items = []
        for app in apps:
            if exclude_app_id and str(app.id) == exclude_app_id:
                continue
            kind = str(app.ai_app_type)
            items.append(
                _summary(
                    id_=str(app.id),
                    flow_node_type="pluginModule" if kind == "workflowTool" else "toolSet",
                    name=str(app.name),
                    intro=str(app.description or ""),
                    avatar=str(app.app_icon or ""),
                    is_folder=kind in ("httpToolSet", "mcpToolSet"),
                    source=SOURCE_WORKFLOW_TOOL if kind == "workflowTool" else (SOURCE_MCP if kind == "mcpToolSet" else SOURCE_HTTP),
                    status="",
                )
            )
        return items

    if tab == "agent":
        apps = await _visible_apps(session, user, AGENT_APP_TYPES, search_key)
        return [
            _summary(
                id_=str(app.id),
                flow_node_type="appModule",
                name=str(app.name),
                intro=str(app.description or ""),
                avatar=str(app.app_icon or ""),
                source=SOURCE_APP,
                status=str(app.status or ""),
            )
            for app in apps
            if str(app.status) == "published" and not (exclude_app_id and str(app.id) == exclude_app_id)
        ]

    raise ValueError(f"未知模板 Tab: {tab}")


# ---------- preview node ----------


def _preview_base(
    *,
    id_: str,
    flow_node_type: str,
    name: str,
    intro: str,
    avatar: str = "",
    source: str,
    plugin_id: str,
    version: str,
    is_latest: bool = True,
    has_secret: bool = False,
    tool_description: str = "",
) -> dict:
    node = {
        "id": id_,
        "flowNodeType": flow_node_type,
        "templateType": "teamApp",
        "name": name,
        "intro": intro,
        "icon": "ApiOutlined",
        "color": "#6f5dd7",
        "avatar": avatar,
        "showSourceHandle": True,
        "showTargetHandle": True,
        "isTool": True,
        "showStatus": True,
        "toolDescription": tool_description or intro or name,
        "pluginId": plugin_id,
        "source": source,
        "isLatestVersion": is_latest,
        "hasSystemSecret": has_secret,
    }
    if version:
        node["version"] = version
        node["versionLabel"] = version
    return node


def _parse_toolset_child_id(template_id: str) -> Optional[tuple[str, str, str]]:
    """`http-{appId}/{toolName}` / `mcp-{appId}/{toolName}` -> (source, appId, toolName)。"""
    for prefix, source in (("http-", SOURCE_HTTP), ("mcp-", SOURCE_MCP)):
        if template_id.startswith(prefix) and "/" in template_id:
            rest = template_id[len(prefix):]
            app_id, _, tool_name = rest.partition("/")
            if app_id and tool_name:
                return source, app_id, tool_name
    return None


async def build_preview_node(session, user, *, template_id: str, exclude_app_id: Optional[str] = None) -> dict:
    """按摘要 id 生成完整可落图节点。失败抛 ValueError（调用方转 400，前端不得落残缺节点）。"""
    # 1) 系统工具
    if template_id.startswith("builtin."):
        tool = BUILTIN_TOOL_MAP.get(template_id)
        if not tool:
            raise ValueError(f"系统工具 {template_id} 不存在")
        node = _preview_base(
            id_=template_id,
            flow_node_type="tool",
            name=str(tool["name"]),
            intro=str(tool.get("description") or ""),
            source=SOURCE_SYSTEM_TOOL,
            plugin_id=template_id,
            version="",
        )
        node["toolConfig"] = {"systemTool": {"toolId": template_id}}
        node["inputs"] = _schema_to_inputs(tool.get("parameters") or {})
        declared_outputs = tool.get("outputs") if isinstance(tool.get("outputs"), dict) else {}
        if template_id == "builtin.json_parse":
            node["outputs"] = [
                {"id": "result", "key": "result", "label": "转换结果", "type": "static", "valueType": "any"},
                _raw_response_output(),
                _error_output(),
            ]
        elif declared_outputs:
            node["outputs"] = [
                {
                    "id": key,
                    "key": key,
                    "label": str(spec.get("label") or key),
                    "type": "static",
                    "valueType": str(spec.get("valueType") or "any"),
                }
                for key, spec in declared_outputs.items()
            ] + [_raw_response_output(), _error_output()]
        else:
            node["outputs"] = [_raw_response_output(), _error_output()]
        return node

    # 2) 工具集子工具（http-/mcp- 前缀）
    child = _parse_toolset_child_id(template_id)
    if child:
        source, app_id, tool_name = child
        app = await session.get(WorkflowApp, app_id)
        if app is None:
            raise ValueError("工具集不存在或已删除")
        config = _parse_config(app.config_json)
        items = {str(i.get("name")): i for i in config.get("toolList") or [] if isinstance(i, dict)}
        item = items.get(tool_name)
        if item is None:
            raise ValueError(f"工具「{tool_name}」不在工具集清单中")
        has_secret = bool(config.get("headers"))
        node = _preview_base(
            id_=template_id,
            # 蓝本命名：`${工具集名}/${工具名}`，避免不同工具集重名子工具混淆；version 不展示发布状态
            flow_node_type="tool",
            name=f"{app.name}/{tool_name}",
            intro=str(item.get("description") or ""),
            avatar=str(app.app_icon or ""),
            source=source,
            plugin_id=str(app.id),
            version="",
            has_secret=has_secret,
        )
        if source == SOURCE_MCP:
            node["toolConfig"] = {"mcpTool": {"toolId": template_id}}
            node["inputs"] = _schema_to_inputs(item.get("inputSchema") or {})
            node["outputs"] = [_raw_response_output(), _error_output()]
        else:
            node["toolConfig"] = {"httpTool": {"toolId": template_id}}
            input_schema = item.get("inputSchema") if isinstance(item.get("inputSchema"), dict) else {}
            if (input_schema or {}).get("properties"):
                # 蓝本 getHTTPToolRuntimeNode：inputSchema 逐参数展开
                node["inputs"] = _schema_to_inputs(input_schema)
            else:
                # 无参数 schema 的存量工具集：退化为单 payload 对象（登记差异 D-18）
                node["inputs"] = [
                    {
                        "key": "payload",
                        "label": "请求参数",
                        "renderTypeList": ["JSONEditor", "reference"],
                        "valueType": "object",
                        "description": "键值对参数：GET 作为查询参数，其余作为 JSON 请求体",
                        "toolDescription": str(item.get("description") or tool_name),
                    }
                ]
            # 蓝本 http 子工具：outputSchema 逐属性 static 输出 + system_rawResponse
            output_schema = item.get("outputSchema") if isinstance(item.get("outputSchema"), dict) else {}
            property_outputs = [
                {
                    "id": str(key),
                    "key": str(key),
                    "label": str((prop or {}).get("title") or key),
                    "description": str((prop or {}).get("description") or "") or None,
                    "type": "static",
                    "valueType": _SCHEMA_VALUE_TYPE.get(str((prop or {}).get("type") or "string"), _SCHEMA_VALUE_TYPE["string"])[0],
                }
                for key, prop in ((output_schema.get("properties") or {}).items())
                if isinstance(prop, dict) or prop is None
            ]
            node["outputs"] = [*property_outputs, _raw_response_output(), _error_output()]
        return node

    # 3) 应用类（workflowTool -> pluginModule；simple/chatAgent/workflow -> appModule）
    app = await session.get(WorkflowApp, template_id)
    if app is None:
        raise ValueError("应用不存在或已删除")
    if exclude_app_id and str(app.id) == exclude_app_id:
        raise ValueError("不能引用当前应用自身")
    kind = str(app.ai_app_type)
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app.id))
    ).scalar_one_or_none()
    version = str(definition.published_version or 0) if definition else "0"

    if kind == "workflowTool":
        node = _preview_base(
            id_=str(app.id),
            flow_node_type="pluginModule",
            name=str(app.name),
            intro=str(app.description or ""),
            avatar=str(app.app_icon or ""),
            source=SOURCE_WORKFLOW_TOOL,
            plugin_id=str(app.id),
            version=version,
        )
        # 保留差异：单参数 question（多参数 schema 依赖 pluginInput/pluginOutput，P2）
        node["inputs"] = [
            {
                "key": "question",
                "label": "输入问题",
                "renderTypeList": ["reference", "textarea"],
                "valueType": "string",
                "required": True,
                "toolDescription": str(app.description or app.name),
            }
        ]
        node["outputs"] = [
            {"id": "result", "key": "result", "label": "工具输出", "type": "static", "valueType": "string"},
            _error_output(),
        ]
        return node

    if kind in AGENT_APP_TYPES:
        node = _preview_base(
            id_=str(app.id),
            flow_node_type="appModule",
            name=str(app.name),
            intro=str(app.description or ""),
            avatar=str(app.app_icon or ""),
            source=SOURCE_APP,
            plugin_id=str(app.id),
            version=version,
        )
        # 蓝本 appData2FlowNodeIO：history + userChatInput + forbid_stream + 被引应用全局变量展开
        variable_inputs: list[dict] = []
        if definition and definition.published_json:
            try:
                parsed_def = json.loads(definition.published_json)
                chat_config = (parsed_def.get("fastgpt") or parsed_def).get("chatConfig") or {}
                for variable in chat_config.get("variables") or []:
                    if not isinstance(variable, dict) or not variable.get("key"):
                        continue
                    variable_inputs.append(
                        {
                            "key": str(variable["key"]),
                            "label": str(variable.get("label") or variable["key"]),
                            "renderTypeList": ["input", "reference"],
                            "valueType": str(variable.get("valueType") or "string"),
                            "required": bool(variable.get("required")),
                            "description": str(variable.get("description") or "") or None,
                            **({"value": variable.get("defaultValue")} if variable.get("defaultValue") is not None else {}),
                        }
                    )
            except (json.JSONDecodeError, AttributeError):
                pass
        node["inputs"] = [
            {
                "key": "history",
                "label": "聊天记录",
                "renderTypeList": ["numberInput", "reference"],
                "valueType": "chatHistory",
                "required": True,
                "min": 0,
                "max": 50,
                "value": 6,
                "description": "最多携带多少轮对话记录",
            },
            {
                "key": "userChatInput",
                "label": "用户问题",
                "renderTypeList": ["reference", "textarea"],
                "valueType": "string",
                "required": True,
                "toolDescription": "user question",
            },
            {
                "key": "system_forbid_stream",
                "label": "禁用流输出",
                "renderTypeList": ["switch"],
                "valueType": "boolean",
                "value": False,
            },
            *variable_inputs,
        ]
        node["outputs"] = [
            {
                "id": "history",
                "key": "history",
                "label": "新的上下文",
                "description": "将本次回复内容拼接上历史记录，作为新的上下文返回",
                "type": "static",
                "valueType": "chatHistory",
                "required": True,
            },
            {"id": "answerText", "key": "answerText", "label": "回复内容", "type": "static", "valueType": "string"},
            _error_output(),
        ]
        return node

    if kind in ("httpToolSet", "mcpToolSet"):
        raise ValueError("工具集不能直接添加为节点，请进入目录选择具体工具")
    raise ValueError(f"应用类型 {kind} 不支持作为画布节点")
