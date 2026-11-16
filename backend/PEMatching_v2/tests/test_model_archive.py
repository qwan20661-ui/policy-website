# -*- coding: utf-8 -*-
"""模型输入输出归档默认关闭，开启后必须脱敏。"""

from __future__ import annotations

import json

from service import model_archive


def test_归档关闭时不创建文件(monkeypatch, tmp_path):
    monkeypatch.setattr(model_archive, "MODEL_ARCHIVE_DIR", tmp_path)
    monkeypatch.setenv("PEMATCHING_V2_MODEL_ARCHIVE_ENABLED", "false")

    assert model_archive.archive_model_exchange({"enterpriseName": "测试企业"}) is False
    assert list(tmp_path.iterdir()) == []


def test_归档开启后脱敏联系方式和企业标识(monkeypatch, tmp_path):
    monkeypatch.setattr(model_archive, "MODEL_ARCHIVE_DIR", tmp_path)
    monkeypatch.setenv("PEMATCHING_V2_MODEL_ARCHIVE_ENABLED", "true")

    assert model_archive.archive_model_exchange({
        "enterpriseName": "测试企业", "creditCode": "9135", "registerAddress": "厦门市某路",
        "policy": {"contactInfo": "123456"}, "output": {"value": 1},
    }) is True

    payload = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert payload["enterpriseName"] == "***"
    assert payload["creditCode"] == "***"
    assert payload["registerAddress"] == "***"
    assert payload["policy"]["contactInfo"] == "***"
