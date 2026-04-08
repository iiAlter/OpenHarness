import pytest
from openharness.config.schema import ApiChannelConfig, ChannelsConfig, Config


def test_api_channel_config_defaults():
    cfg = ApiChannelConfig()
    assert cfg.enabled is False
    assert cfg.api_token == ""
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 8080
    assert cfg.idle_timeout_minutes == 30
    assert cfg.session_retention_days == 7
    assert cfg.allow_from == ["*"]


def test_api_channel_config_custom():
    cfg = ApiChannelConfig(
        enabled=True,
        api_token="secret",
        port=9090,
        allow_from=["client-1"],
    )
    assert cfg.enabled is True
    assert cfg.api_token == "secret"
    assert cfg.port == 9090
    assert cfg.allow_from == ["client-1"]


def test_channels_config_has_api():
    cfg = ChannelsConfig()
    assert hasattr(cfg, "api")
    assert isinstance(cfg.api, ApiChannelConfig)


def test_config_has_channels():
    cfg = Config()
    assert hasattr(cfg, "channels")
    assert isinstance(cfg.channels, ChannelsConfig)


def test_config_channels_has_existing_platforms():
    cfg = Config()
    assert hasattr(cfg.channels, "telegram")
    assert hasattr(cfg.channels, "discord")
    assert hasattr(cfg.channels, "slack")
    assert hasattr(cfg.channels, "whatsapp")
    assert hasattr(cfg.channels, "feishu")
    assert hasattr(cfg.channels, "dingtalk")
    assert hasattr(cfg.channels, "mochat")
    assert hasattr(cfg.channels, "email")
    assert hasattr(cfg.channels, "qq")
    assert hasattr(cfg.channels, "matrix")
