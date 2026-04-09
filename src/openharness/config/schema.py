"""Configuration schema for channels and providers.

This module defines the configuration model consumed by ChannelManager
and channel implementations.
"""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Per-channel config stubs
# ---------------------------------------------------------------------------

class TelegramConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = []


class DiscordConfig(BaseModel):
    enabled: bool = False
    token: str = ""
    allow_from: list[str] = []


class SlackConfig(BaseModel):
    enabled: bool = False
    app_token: str = ""
    bot_token: str = ""
    allow_from: list[str] = []


class WhatsAppConfig(BaseModel):
    enabled: bool = False
    bridge_url: str = "ws://localhost:3000"
    bridge_token: str = ""
    allow_from: list[str] = []


class FeishuConfig(BaseModel):
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = []


class DingTalkConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    allow_from: list[str] = []


class MochatConfig(BaseModel):
    enabled: bool = False
    server_url: str = ""
    token: str = ""
    allow_from: list[str] = []


class EmailConfig(BaseModel):
    enabled: bool = False
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    allow_from: list[str] = []


class QQConfig(BaseModel):
    enabled: bool = False
    appid: str = ""
    token: str = ""
    allow_from: list[str] = []


class MatrixConfig(BaseModel):
    enabled: bool = False
    homeserver: str = ""
    user_id: str = ""
    password: str = ""
    allow_from: list[str] = []


# ---------------------------------------------------------------------------
# API channel config
# ---------------------------------------------------------------------------

class ApiChannelConfig(BaseModel):
    enabled: bool = False
    api_token: str = "ESHkwHjLBSurOCOIwY9DNMbQakLBbhtdyPOUP-pcKvk"
    host: str = "0.0.0.0"
    port: int = 18080
    idle_timeout_minutes: int = 30
    session_retention_days: int = 7
    allow_from: list[str] = ["*"]


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------

class GroqProviderConfig(BaseModel):
    api_key: str = ""


class ProviderConfig(BaseModel):
    groq: GroqProviderConfig = GroqProviderConfig()


class ChannelsConfig(BaseModel):
    send_progress: bool = True
    send_tool_hints: bool = False
    telegram: TelegramConfig = TelegramConfig()
    discord: DiscordConfig = DiscordConfig()
    slack: SlackConfig = SlackConfig()
    whatsapp: WhatsAppConfig = WhatsAppConfig()
    feishu: FeishuConfig = FeishuConfig()
    dingtalk: DingTalkConfig = DingTalkConfig()
    mochat: MochatConfig = MochatConfig()
    email: EmailConfig = EmailConfig()
    qq: QQConfig = QQConfig()
    matrix: MatrixConfig = MatrixConfig()
    api: ApiChannelConfig = ApiChannelConfig()


class Config(BaseModel):
    channels: ChannelsConfig = ChannelsConfig()
    providers: ProviderConfig = ProviderConfig()
