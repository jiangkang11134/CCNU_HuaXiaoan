from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.config import UserConfig, UserConfigSchema
from yuxi.config.user import DEFAULT_ENABLE_MEMORY
from yuxi.storage.postgres.models_business import Base, Department, User
from yuxi.storage.postgres.models_business import UserConfig as UserConfigRecord

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name="Config Dept")
        user = User(
            username="Config User",
            uid="config_user",
            password_hash="$argon2id$placeholder",
            role="user",
            department=department,
        )
        db.add_all([department, user])
        await db.commit()
        await db.refresh(user)
        yield db, user
    await engine.dispose()


async def test_user_config_load_returns_defaults_without_creating_row(session):
    db, user = session

    user_config = await UserConfig.load(db, user.uid)
    dumped = user_config.dump_config()

    assert dumped["uid"] == user.uid
    # 无配置行时取系统默认（默认开启），且不应顺带建行
    assert dumped["enable_memory"] is DEFAULT_ENABLE_MEMORY
    assert dumped["enable_memory"] is True

    result = await db.execute(select(UserConfigRecord).filter(UserConfigRecord.uid == user.uid))
    assert result.scalar_one_or_none() is None


async def test_user_config_explicit_disable_is_respected(session):
    """默认开启不能反过来吞掉用户的显式关闭。"""
    db, user = session

    await UserConfig(uid=user.uid, schema=UserConfigSchema(enable_memory=False)).save(db)
    loaded = await UserConfig.load(db, user.uid)

    assert loaded.dump_config()["enable_memory"] is False


async def test_user_config_save_persists_user_specific_values(session):
    db, user = session

    saved = await UserConfig(uid=user.uid, schema=UserConfigSchema(enable_memory=True)).save(db)
    loaded = await UserConfig.load(db, user.uid)

    assert saved.dump_config()["enable_memory"] is True
    assert loaded.dump_config()["enable_memory"] is True
    result = await db.execute(select(UserConfigRecord).filter(UserConfigRecord.uid == user.uid))
    assert result.scalar_one().enable_memory is True


async def test_profile_defaults_are_empty_and_style_defaults_to_normal(session):
    """个人资料默认全空、风格默认 normal——对应"正常就是不定义"。"""
    db, user = session

    dumped = (await UserConfig.load(db, user.uid)).dump_config()

    assert dumped["full_name"] is None
    assert dumped["gender"] is None
    assert dumped["major"] is None
    assert dumped["response_style"] == "normal"


async def test_profile_fields_round_trip(session):
    db, user = session

    await UserConfig(uid=user.uid, schema=UserConfigSchema(
        full_name="张三", gender="男", major="应用化学", response_style="thorough")).save(db)
    dumped = (await UserConfig.load(db, user.uid)).dump_config()

    assert dumped["full_name"] == "张三"
    assert dumped["gender"] == "男"
    assert dumped["major"] == "应用化学"
    assert dumped["response_style"] == "thorough"


async def test_profile_rejects_student_id_and_identity(session):
    """学工号（users.uid）与身份（users.business_role）来自注册信息，不可自改。

    守法是：这两个字段**根本不在** UserConfigSchema 里，且 schema 是 extra="forbid"，
    所以客户端送来就会被 422 挡掉——而不是"接受了再忽略"。
    """
    for forbidden in ("uid", "business_role"):
        assert forbidden not in UserConfigSchema.model_fields
        with pytest.raises(Exception):
            UserConfigSchema(**{forbidden: "20210001"})


async def test_response_style_is_normalized_before_it_reaches_the_row(session):
    """脏值不要落库等着读取侧兜底。"""
    db, user = session

    await UserConfig(uid=user.uid, schema=UserConfigSchema(response_style="ULTRA")).save(db)
    result = await db.execute(select(UserConfigRecord).filter(UserConfigRecord.uid == user.uid))

    assert result.scalar_one().response_style == "normal"
    assert (await UserConfig.load(db, user.uid)).dump_config()["response_style"] == "normal"
