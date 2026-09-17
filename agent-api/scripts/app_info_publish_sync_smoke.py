import asyncio
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session, engine
from app.services.agents.app_info_publish_service import upsert_app_info_for_approved_version


async def main():
    app_id = f"smoke_{uuid.uuid4().hex[:12]}"
    app = SimpleNamespace(
        id=app_id,
        name="发布同步冒烟应用",
        description="验证 app_info/app_role/app_dept 同步",
        app_icon="",
        app_category="office",
        ai_app_type="workflow",
        owner_username="admin",
        owner_user_id="admin",
        tenant_id="0",
    )
    version = SimpleNamespace(
        version_no=1,
        visible_role_ids=json.dumps(["role-smoke-a", "role-smoke-b"]),
        visible_dept_ids=json.dumps(["dept-smoke-a"]),
        reviewed_by_name="admin",
        reviewed_by="admin",
        submitted_by_name="admin",
    )

    async with async_session() as session:
        await upsert_app_info_for_approved_version(session, app, version)
        await session.commit()

        info = (
            await session.execute(
                text(
                    """
                    SELECT id, app_name, app_type, ai_app_type, status, form_options
                    FROM app_info
                    WHERE id = :id
                    """
                ),
                {"id": app_id},
            )
        ).mappings().first()
        roles = (
            await session.execute(
                text("SELECT role_id FROM app_role WHERE app_id = :id ORDER BY role_id"),
                {"id": app_id},
            )
        ).scalars().all()
        depts = (
            await session.execute(
                text("SELECT dept_id FROM app_dept WHERE app_id = :id ORDER BY dept_id"),
                {"id": app_id},
            )
        ).scalars().all()

        assert info is not None, "missing app_info row"
        assert info["app_name"] == "发布同步冒烟应用", info
        assert info["app_type"] == "ai", info
        assert info["ai_app_type"] == "workflow", info
        assert info["status"] == "1", info
        assert roles == ["role-smoke-a", "role-smoke-b"], roles
        assert depts == ["dept-smoke-a"], depts

        await session.execute(text("DELETE FROM app_role WHERE app_id = :id"), {"id": app_id})
        await session.execute(text("DELETE FROM app_dept WHERE app_id = :id"), {"id": app_id})
        await session.execute(text("DELETE FROM app_info WHERE id = :id"), {"id": app_id})
        await session.commit()

    print("APP_INFO_PUBLISH_SYNC_SMOKE_OK")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
