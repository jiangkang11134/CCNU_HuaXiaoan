from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.auth_router import delete_user
from server.routers.user_router import APIKeyCreate, create_api_key
from server.utils.auth_middleware import _verify_api_key
from yuxi.repositories import user_repository as user_repository_module
from yuxi.repositories.user_repository import UserRepository
from yuxi.storage.postgres.models_business import APIKey, Base, Department, User
from yuxi.utils.auth_utils import AuthUtils

# 口径（2026-09-21 起）：HTTP 层 API Key 端点一律要求 system_admin（见 user_router 的 get_admin_user 依赖），
# 学生/教师账号前后端均无入口。本文件直接调用函数体，验证的是**函数体自身**的归属/部门规则，
# 因此调用者必须是管理员角色（system_admin），否则与真实可达路径不符。
pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeApiKeySession:
    def __init__(self, api_key: APIKey):
        self.api_key = api_key
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _ScalarResult(self.api_key)


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        dept_a = Department(name="Dept A")
        dept_b = Department(name="Dept B")
        superadmin = User(
            username="Super Admin",
            uid="superadmin",
            password_hash="$argon2id$placeholder",
            role="superadmin",
            department=dept_a,
        )
        dept_b_admin = User(
            username="Dept B Admin",
            uid="dept_b_admin",
            password_hash="$argon2id$placeholder",
            role="admin",
            department=dept_b,
        )
        regular_user = User(
            username="Regular",
            uid="regular",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
        )
        system_admin = User(
            username="System Admin",
            uid="system_admin",
            password_hash="$argon2id$placeholder",
            role="system_admin",
            department=dept_a,
        )
        deleted_user = User(
            username="Deleted",
            uid="deleted",
            password_hash="$argon2id$placeholder",
            role="user",
            department=dept_a,
            is_deleted=1,
        )
        db.add_all([dept_a, dept_b, superadmin, dept_b_admin, regular_user, system_admin, deleted_user])
        await db.commit()
        for item in [dept_a, dept_b, superadmin, dept_b_admin, regular_user, system_admin, deleted_user]:
            await db.refresh(item)
        yield {
            "db": db,
            "dept_a": dept_a,
            "dept_b": dept_b,
            "superadmin": superadmin,
            "dept_b_admin": dept_b_admin,
            "regular_user": regular_user,
            "system_admin": system_admin,
            "deleted_user": deleted_user,
        }
    await engine.dispose()


async def test_api_key_rejects_deleted_bound_user_without_department_or_superadmin_fallback(session):
    db = session["db"]
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="deleted user key",
        user_id=session["deleted_user"].id,
        department_id=session["dept_b"].id,
        created_by=str(session["deleted_user"].id),
    )
    db.add(api_key)
    await db.commit()

    user, verified_key = await _verify_api_key(secret, db)

    assert user is None
    assert verified_key is None


async def test_api_key_without_user_binding_is_rejected_before_department_mapping(session):
    secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="department key",
        user_id=None,
        department_id=session["dept_b"].id,
        created_by=str(session["superadmin"].id),
    )
    fake_db = _FakeApiKeySession(api_key)

    user, verified_key = await _verify_api_key(secret, fake_db)

    assert user is None
    assert verified_key is None
    assert fake_db.execute_calls == 1


async def test_create_api_key_rejects_mismatched_department(session):
    """管理员给自己签发时，department_id 必须与自身部门一致。"""
    db = session["db"]

    with pytest.raises(HTTPException) as exc:
        await create_api_key(
            APIKeyCreate(name="wrong department", department_id=session["dept_b"].id),
            current_user=session["system_admin"],
            db=db,
        )

    assert exc.value.status_code == 403


async def test_create_api_key_allows_admin_own_department(session):
    """管理员不指定 user_id 时，密钥自动绑定到管理员本人及其部门。"""
    db = session["db"]

    response = await create_api_key(
        APIKeyCreate(name="own department", department_id=session["dept_a"].id),
        current_user=session["system_admin"],
        db=db,
    )

    assert response.api_key.user_id == session["system_admin"].id
    assert response.api_key.department_id == session["dept_a"].id
    assert response.secret.startswith(response.api_key.key_prefix)


async def test_delete_user_disables_owned_api_keys(session):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    result = await delete_user(session["regular_user"].id, None, session["superadmin"], db)
    await db.refresh(api_key)

    assert result["success"] is True
    assert api_key.is_enabled is False


async def test_user_repository_soft_delete_disables_owned_api_keys(session, monkeypatch):
    db = session["db"]
    _secret, key_hash, key_prefix = AuthUtils.generate_api_key()
    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name="repository owned key",
        user_id=session["regular_user"].id,
        created_by=str(session["regular_user"].id),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    @asynccontextmanager
    async def fake_session_context():
        yield db
        await db.commit()

    monkeypatch.setattr(user_repository_module.pg_manager, "get_async_session_context", fake_session_context)

    assert await UserRepository().soft_delete(session["regular_user"].id) is True
    await db.refresh(api_key)

    assert api_key.is_enabled is False


async def test_apikey_endpoints_require_system_admin_dependency():
    """钉死路由层门槛：/user/apikey 全部端点的直接依赖必须是 get_admin_user。

    学生/教师账号（role=user/faculty）在后端不得有任何入口 —— 漏掉这里就等于
    "任何登录用户都能给自己签发 API Key"，而该 Key 可以反过来认证成用户本人调用 Agent。
    """
    from server.routers.user_router import user_router
    from server.utils.auth_middleware import get_admin_user

    routes = [route for route in user_router.routes if "/apikey" in getattr(route, "path", "")]
    assert len(routes) == 6, [route.path for route in routes]

    for route in routes:
        calls = [dependant.call for dependant in route.dependant.dependencies]
        assert get_admin_user in calls, f"{route.path} 未要求管理员：{calls}"
        loose = [getattr(call, "__name__", repr(call)) for call in calls if call is not get_admin_user]
        assert "get_required_user" not in loose, f"{route.path} 仍挂着任意登录用户依赖：{loose}"
