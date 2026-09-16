import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


def validate_password(value: str) -> str:
    if len(value) < 8 or not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"\d", value):
        raise ValueError("密码至少 8 位，且须包含大写字母、小写字母和数字")
    return value


class RegisterStart(BaseModel):
    username: str
    email: EmailStr
    password: str
    confirm_password: str
    invite_code: str = Field(min_length=4, max_length=100)

    @field_validator("username")
    @classmethod
    def username_format(cls, value: str) -> str:
        value = value.strip()
        if not USERNAME_RE.fullmatch(value):
            raise ValueError("用户名须为 3–32 位字母、数字、下划线或短横线")
        return value

    @model_validator(mode="after")
    def password_rules(self):
        validate_password(self.password)
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class RegisterVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class LoginInput(BaseModel):
    username: str
    password: str


class PasswordResetStart(BaseModel):
    email: EmailStr


class PasswordResetVerify(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    password: str
    confirm_password: str

    @model_validator(mode="after")
    def password_rules(self):
        validate_password(self.password)
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class PasswordChange(BaseModel):
    current_password: str
    password: str
    confirm_password: str

    @model_validator(mode="after")
    def password_rules(self):
        validate_password(self.password)
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class NotificationInput(BaseModel):
    enabled: bool
    login_expired: bool
    task_failed: bool
    security_challenge: bool
    daily_incomplete: bool
    daily_summary: bool


class InviteCreate(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class InviteStatusInput(BaseModel):
    active: bool


class ProUpgradeInput(BaseModel):
    period: str = Field(default="year", pattern=r"^(week|month|quarter|year)$")


class AccountCreate(BaseModel):
    name: str = Field(default="我的抖音账号", min_length=1, max_length=80)


class BindingVerification(BaseModel):
    code: str = Field(pattern=r"^\d{4,6}$")


class RecipientInput(BaseModel):
    contact_id: str | None = Field(default=None, max_length=36)
    contact_name: str = Field(min_length=1, max_length=120)
    conversation_type: str = Field(default="unknown", pattern=r"^(friend|group|unknown)$")
    message: str = Field(default="今日火花", min_length=1, max_length=500)


class ContactSelectionInput(BaseModel):
    recipients: list[RecipientInput] = Field(default_factory=list, max_length=5)


class ContactTypeInput(BaseModel):
    conversation_type: str = Field(pattern=r"^(friend|group|unknown)$")


class TaskCreate(BaseModel):
    account_id: str
    name: str = Field(min_length=1, max_length=80)
    send_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = True
    recipients: list[RecipientInput] = Field(min_length=1, max_length=5)


class TaskStatusInput(BaseModel):
    enabled: bool


class TaskOutput(BaseModel):
    id: str
    name: str
    send_time: str
    enabled: bool
    next_run_at: datetime
