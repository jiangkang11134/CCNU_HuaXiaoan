from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.user_router import get_logged_in_user, get_user_config, update_user_config
from yuxi.config import UserConfigSchema
from yuxi.config.user import DEFAULT_ENABLE_MEMORY
from yuxi.storage.postgres.models_business import Base, Department, User

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name="User Config Dept")
        user_a = User(
            username="User A",
            uid="user_a",
            password_hash="$argon2id$placeholder",
            role="user",
            department=department,
        )
        user_b = User(
            username="User B",
            uid="user_b",
            password_hash="$argon2id$placeholder",
            role="user",
            department=department,
        )
        db.add_all([department, user_a, user_b])
        await db.commit()
        for user in [user_a, user_b]:
            await db.refresh(user)
        yield db, user_a, user_b
    await engine.dispose()


async def test_user_config_routes_scope_to_current_user(session):
    db, user_a, user_b = session

    own_config = await update_user_config(
        UserConfigSchema(enable_memory=True),
        current_user=user_a,
        db=db,
    )
    # 两人都显式写过、且取值相反：断言的是"配置按 uid 隔离"。
    # 若让 user_b 保持"从未配置"，这里就退化成断言系统默认值——
    # DEFAULT_ENABLE_MEMORY 一改就误报（曾经正是如此）。
    other_config = await update_user_config(
        UserConfigSchema(enable_memory=False),
        current_user=user_b,
        db=db,
    )
    reloaded_own = await get_user_config(current_user=user_a, db=db)

    assert own_config["enable_memory"] is True
    assert other_config["enable_memory"] is False
    assert reloaded_own["enable_memory"] is True


async def test_user_config_unconfigured_user_falls_back_to_system_default(session):
    """没有 user_config 行的用户（绝大多数人从未改过设置）必须走系统默认，
    不能把"查不到记录"当成"用户手动关闭"——那会让默认开启的策略在热路径上失效。"""
    db, _user_a, _user_b = session
    never_configured = User(
        username="Never Configured",
        uid="never_configured_user",
        password_hash="$argon2id$placeholder",
        role="user",
    )

    config = await get_user_config(current_user=never_configured, db=db)

    assert config["enable_memory"] is DEFAULT_ENABLE_MEMORY


async def test_user_config_allows_logged_in_user_without_department():
    user = User(
        username="No Dept User",
        uid="no_dept_user",
        password_hash="$argon2id$placeholder",
        role="user",
    )

    assert await get_logged_in_user(user) is user
