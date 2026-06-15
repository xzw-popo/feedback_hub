"""ws_bot config 模块测试。"""
import os
import importlib
import pytest
from unittest.mock import patch


def test_load_config_required_fields():
    """必填字段缺失时抛 SystemExit。"""
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("WECOM_BOT_ID", None)
        os.environ.pop("WECOM_BOT_SECRET", None)
        from feedback_hub.ws_bot import config as ws_config
        importlib.reload(ws_config)
        with pytest.raises(SystemExit):
            ws_config.load_config()


def test_load_config_defaults():
    """提供必填字段后，可选字段使用默认值。"""
    env = {
        "WECOM_BOT_ID": "test_bot_id",
        "WECOM_BOT_SECRET": "test_secret",
    }
    with patch.dict(os.environ, env, clear=True):
        from feedback_hub.ws_bot import config as ws_config
        importlib.reload(ws_config)
        cfg = ws_config.load_config()
        assert cfg.bot_id == "test_bot_id"
        assert cfg.secret == "test_secret"
        assert cfg.ws_url == "wss://openws.work.weixin.qq.com"
        assert cfg.push_cron == "0 10 * * *"
        assert cfg.trigger_port == 8081
        assert cfg.chat_ids == []


def test_load_config_chat_ids_parsing():
    """WECOM_CHAT_IDS 逗号分隔正确解析。"""
    env = {
        "WECOM_BOT_ID": "bot1",
        "WECOM_BOT_SECRET": "sec1",
        "WECOM_CHAT_IDS": "chat_a, chat_b ,chat_c",
    }
    with patch.dict(os.environ, env, clear=True):
        from feedback_hub.ws_bot import config as ws_config
        importlib.reload(ws_config)
        cfg = ws_config.load_config()
        assert cfg.chat_ids == ["chat_a", "chat_b", "chat_c"]
