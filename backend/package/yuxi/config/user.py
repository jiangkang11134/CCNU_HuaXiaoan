"""用户级配置模块。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import UserConfig as UserConfigRecord
from yuxi.utils.datetime_utils import format_naive_utc_datetime, utc_now_naive


DEFAULT_ENABLE_MEMORY = True
"""用户未显式配置（user_config 无该行）时的记忆开关取值。

记忆与纠错的默认策略是"默认开启、可显式关闭"，因此这里是 True。
chat_service 的热路径直接查表（拿不到行时是 None），必须与此处同源，
否则会出现"设置页显示已开启、实际注入仍关闭"的不一致。
"""


def _normalize_style(value: object) -> str:
    """收敛回答风格取值。

    延迟 import：`yuxi.services` 与配置模块互相可见，模块级 import 容易成环，
    而这里只在读写配置时用到。
    """
    from yuxi.services.user_profile import normalize_response_style

    return normalize_response_style(value)


class UserConfigSchema(BaseModel):
    """用户专属配置 schema。"""

    enable_memory: bool = Field(default=DEFAULT_ENABLE_MEMORY, description="是否启用 Memory")

    # --- 个人资料（用户可编辑）---
    # 学工号与身份**不在这里**：学工号=users.uid、身份=users.business_role，
    # 均来自注册信息且不可由用户修改，放在这里就等于给它造了第二个可写来源。
    full_name: str | None = Field(default=None, max_length=64, description="姓名")
    gender: str | None = Field(default=None, max_length=16, description="性别")
    major: str | None = Field(default=None, max_length=64, description="专业")
    response_style: str = Field(default="normal", description="回答风格：concise/normal/thorough")

    @field_validator("response_style")
    @classmethod
    def _validate_response_style(cls, value: object) -> str:
        """入库前就收敛：脏值不该落到库里等着读取侧兜底。"""
        return _normalize_style(value)

    model_config = ConfigDict(extra="forbid")


class UserConfig:
    """用户级配置访问器。每次加载都从 PostgreSQL 查询，不做进程缓存。"""

    def __init__(self, uid: str, schema: UserConfigSchema | None = None, updated_at: datetime | None = None):
        self.uid = uid
        self.schema = schema or UserConfigSchema()
        self.updated_at = updated_at

    @classmethod
    async def load(cls, db: AsyncSession, uid: str) -> UserConfig:
        result = await db.execute(select(UserConfigRecord).filter(UserConfigRecord.uid == uid))
        record = result.scalar_one_or_none()
        if record is None:
            return cls(uid=uid)
        return cls(
            uid=uid,
            schema=UserConfigSchema(
                enable_memory=bool(record.enable_memory),
                full_name=record.full_name,
                gender=record.gender,
                major=record.major,
                response_style=_normalize_style(record.response_style),
            ),
            updated_at=record.updated_at,
        )

    async def save(self, db: AsyncSession) -> UserConfig:
        now = utc_now_naive()
        values = {
            "enable_memory": self.schema.enable_memory,
            "full_name": self.schema.full_name,
            "gender": self.schema.gender,
            "major": self.schema.major,
            "response_style": _normalize_style(self.schema.response_style),
            "updated_at": now,
        }
        result = await db.execute(
            update(UserConfigRecord)
            .where(UserConfigRecord.uid == self.uid)
            .values(**values)
        )
        if result.rowcount == 0:
            db.add(UserConfigRecord(uid=self.uid, created_at=now, **values))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            now = utc_now_naive()
            retry_result = await db.execute(
                update(UserConfigRecord)
                .where(UserConfigRecord.uid == self.uid)
                .values(**{**values, "updated_at": now})
            )
            if retry_result.rowcount == 0:
                raise
            await db.commit()
        return type(self)(uid=self.uid, schema=self.schema, updated_at=now)

    def dump_config(self) -> dict[str, str | bool | None]:
        return {
            "uid": self.uid,
            "enable_memory": self.schema.enable_memory,
            "full_name": self.schema.full_name,
            "gender": self.schema.gender,
            "major": self.schema.major,
            "response_style": self.schema.response_style,
            "updated_at": format_naive_utc_datetime(self.updated_at),
        }
