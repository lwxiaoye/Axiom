"""已发布应用的可运行权限（发布可见角色/部门，WS2）。

权限语义（2026-08-11 委派执行口径确认）：
- 所有应用必须处于 published 状态；
- owner 在 published 前提下可用；
- 非 owner 还必须命中当前线上 approved 版本的 visible_role_ids / visible_dept_ids；
- 可见角色与部门都为空 → 非 owner 拒绝；
- 找不到 approved 发布版本 → 拒绝（不再沿用「无版本默认所有人可用」）。

除单应用判定（user_can_run_published_app）外，提供召回层用的**全集批量**判定
（list_callable_subagent_ids 在 subagent_service 中组装，本模块提供批量原语）：
一次查询取回候选与版本可见性，不做逐应用 N+1。
"""
import logging
from typing import Optional

from sqlalchemy import bindparam, desc, or_, select, text, true

from app.core.auth import UserContext
from app.core.config import settings
from app.models import WorkflowApp, WorkflowDefinition, WorkflowVersion
from app.services.agents.app_info_publish_service import normalize_visible_ids

logger = logging.getLogger(__name__)


def publish_visibility_allows_user(
    visible_role_ids,
    visible_dept_ids,
    user: UserContext,
    *,
    relation_role_ids=None,
    relation_dept_ids=None,
) -> bool:
    role_ids = set(normalize_visible_ids(visible_role_ids))
    dept_ids = set(normalize_visible_ids(visible_dept_ids))
    if not role_ids and not dept_ids:
        return False
    user_role_ids = set(user.role_ids or []) | set(normalize_visible_ids(relation_role_ids))
    user_dept_ids = set(user.dept_ids or []) | set(normalize_visible_ids(relation_dept_ids))
    return bool(role_ids & user_role_ids or dept_ids & user_dept_ids)


async def load_user_relation_ids(session, user: UserContext) -> tuple[list[str], list[str]]:
    try:
        role_ids = (
            await session.execute(
                text("SELECT role_id FROM sys_user_role WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
        ).scalars().all()
        dept_ids = (
            await session.execute(
                text("SELECT dep_id FROM sys_user_depart WHERE user_id = :user_id"),
                {"user_id": user.user_id},
            )
        ).scalars().all()
        return normalize_visible_ids(role_ids), normalize_visible_ids(dept_ids)
    except Exception:
        logger.warning("Failed to load Java user role/dept relations for user %s", user.user_id, exc_info=True)
        return [], []


def tenant_matches(app_tenant, user_tenant) -> bool:
    """租户匹配（语义发现升级复审 #2）：NULL/'' 归一为 '0' 后严格相等。

    应用侧 '0'（历史/未分配占位）视为全租户可见：Java 回源的用户租户解析不稳
    （同一 token 相邻请求可分别解析为 '0' 与 '1000'，2026-07-22 真机日志实证），
    严格相等会让占位租户应用被随机拒绝——含 owner 本人。真实租户（非 0）仍严格隔离。
    """
    normalize = lambda v: str(v).strip() if v is not None and str(v).strip() else "0"  # noqa: E731
    if normalize(app_tenant) == "0":
        return True
    return normalize(app_tenant) == normalize(user_tenant)


def tenant_visible_clause(tenant_col, user_tenant):
    """tenant_matches 的 SQL 等价形式（召回层批量过滤共用，防止各处各写一份走样）。

    应用侧 '0'/NULL/'' = 全局占位，对任何用户租户都可见；非 0 租户用户额外命中本租户应用。
    只用精确相等（`tenant_col == 本租户`）会把全局占位应用从候选里整体排除——`@` 模式走
    tenant_matches 能看到、call_subagent 候选发现却看不到，叠加 Java 回源租户解析不稳
    （同一 token 相邻请求可分别解析为 '0' / '1000'）就表现为同一用户随机丢候选。
    """
    tenant = str(user_tenant).strip() if user_tenant is not None and str(user_tenant).strip() else "0"
    global_placeholder = or_(tenant_col == "0", tenant_col.is_(None), tenant_col == "")
    if tenant == "0":
        return global_placeholder
    return or_(tenant_col == tenant, global_placeholder)


def workflow_tenant_matches(app_tenant, user_tenant) -> bool:
    """智能体/工作流的临时租户开关。

    发布角色/部门仍是运行的唯一业务授权来源；当前阶段按产品要求允许跨租户
    命中这些发布 ACL。恢复隔离时打开配置即可回到 tenant_matches 的既有语义。
    """
    if not settings.AGENT_WORKFLOW_TENANT_ISOLATION_ENABLED:
        return True
    return tenant_matches(app_tenant, user_tenant)


def workflow_tenant_visible_clause(tenant_col, user_tenant):
    """workflow_tenant_matches 的 SQL 等价形式，仅供智能体/工作流候选集使用。"""
    if not settings.AGENT_WORKFLOW_TENANT_ISOLATION_ENABLED:
        return true()
    return tenant_visible_clause(tenant_col, user_tenant)


async def load_published_visibility_version(session, app_id: str) -> Optional[WorkflowVersion]:
    """取当前线上版本（权限/路由元数据的权威版本）。

    严格指针语义（复审 #3）：definition.published_version 指针存在（>0）时**只认**
    与指针精确匹配的 approved 版本——指针指向的版本不存在或非 approved 即返回 None
    （拒绝），不得回退「任意最新 approved」：那会让权限用旧版本的 ACL 配置，与实际
    执行的 published_json 版本错位。仅指针缺失（0/无 definition 行）保留最新
    approved 兜底（老数据边缘态）。
    """
    definition = (
        await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.app_id == app_id))
    ).scalar_one_or_none()
    published_version = int(getattr(definition, "published_version", 0) or 0)
    if published_version:
        return (
            await session.execute(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.app_id == app_id,
                    WorkflowVersion.version_no == published_version,
                    WorkflowVersion.status == "approved",
                )
                .limit(1)
            )
        ).scalar_one_or_none()

    return (
        await session.execute(
            select(WorkflowVersion)
            .where(WorkflowVersion.app_id == app_id, WorkflowVersion.status == "approved")
            .order_by(desc(WorkflowVersion.published_at), desc(WorkflowVersion.version_no))
            .limit(1)
        )
    ).scalar_one_or_none()


async def user_can_run_published_app(session, app: WorkflowApp, user: UserContext) -> bool:
    """委派执行侧的权威权限闸门。

    owner 也必须已发布；非 owner 还必须命中当前 approved 版本的角色/部门可见范围。
    召回候选、显式 subagent_id、流式执行和 HITL 恢复都必须最终收敛到这里。
    """
    # 当前阶段智能体/工作流允许跨租户命中发布 ACL；开关恢复后，显式 app_id、旧挂起
    # 快照等执行入口仍会在这里统一收敛为严格租户校验。
    if not workflow_tenant_matches(getattr(app, "tenant_id", None), user.tenant_id):
        return False
    # 所有执行入口（首次、流式、HITL 恢复）共用这一道发布态闸门。必须先于 owner
    # 短路，否则 owner 在恢复路径可绕过“只能委派已发布应用”的统一口径。
    if app.status != "published":
        return False
    if app.owner_user_id == user.user_id:
        return True
    version = await load_published_visibility_version(session, app.id)
    if not version:
        # deny-by-default：无 approved 版本（或指针错位，见 load_published_visibility_version）即拒绝
        return False
    relation_role_ids, relation_dept_ids = await load_user_relation_ids(session, user)
    return publish_visibility_allows_user(
        version.visible_role_ids,
        version.visible_dept_ids,
        user,
        relation_role_ids=relation_role_ids,
        relation_dept_ids=relation_dept_ids,
    )


# ---------- 召回层批量原语（一次查询，无逐应用 N+1） ----------

# ---------- 已删除：_acl_relation_table_columns / acl_table_app_hits（2026-07-28）----------
# 它们让召回层多出一条 app_role/app_dept 支路：表命中即放行，用来覆盖 WorkflowVersion 上
# visible_* 缺失的存量应用。但 user_can_run_published_app（本文件唯一的执行侧判据）**从不读
# 那两张表**，于是这条支路放进来的恰好是执行侧必拒的那批——模型看得见、选得中，一执行就报
# 「子智能体不存在或无访问权限」。
# 用户拍板：这类「可见但不可执行」的应用不进委派候选，改以**推荐卡**形式给用户自己点
# （推荐卡走 turn_context_builder._retrieve_agents → Qdrant，不经过本模块）。
# 需要回退（让执行侧也读那两张表 = 放宽权限）时，`git show HEAD -- ` 本文件即可取回实现。

async def load_approved_visibility_map(
    session, app_ids: list[str], published_versions: dict[str, int]
) -> dict[str, tuple]:
    """批量取每个应用的权威 approved 版本可见性（不载 definition_json 大字段）。

    择版与 load_published_visibility_version 单应用语义一致（复审 #3 严格指针）：
    指针（published_version>0）只认精确匹配的 approved 版本，匹配不到即视为无版本
    （拒绝，不回退旧版本）；仅指针缺失时用最新 approved 兜底。
    返回 {app_id: (visible_role_ids, visible_dept_ids)}；无权威版本的应用不在结果里。
    """
    if not app_ids:
        return {}
    from datetime import datetime

    rows = (
        await session.execute(
            select(
                WorkflowVersion.app_id,
                WorkflowVersion.version_no,
                WorkflowVersion.visible_role_ids,
                WorkflowVersion.visible_dept_ids,
                WorkflowVersion.published_at,
            ).where(
                WorkflowVersion.app_id.in_(list(app_ids)),
                WorkflowVersion.status == "approved",
            )
        )
    ).all()
    by_app: dict[str, list[tuple]] = {}
    for app_id, version_no, role_ids, dept_ids, published_at in rows:
        by_app.setdefault(str(app_id), []).append((version_no, role_ids, dept_ids, published_at))
    result: dict[str, tuple] = {}
    for app_id, versions in by_app.items():
        pinned = int(published_versions.get(app_id) or 0)
        if pinned:
            chosen = next((v for v in versions if int(v[0] or 0) == pinned), None)
            if chosen is None:
                continue  # 指针错位：拒绝，不回退旧版本 ACL
        else:
            chosen = max(versions, key=lambda v: (v[3] or datetime.min, int(v[0] or 0)))
        result[app_id] = (chosen[1], chosen[2])
    return result
