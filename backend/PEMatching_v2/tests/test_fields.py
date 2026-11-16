# -*- coding: utf-8 -*-
"""字段取值与可用性标记测试。

`0` 与 `null` 的区分是全期最容易写错的地方，本文件是硬门槛。
"""

from __future__ import annotations

from service import fields as F


# --------------------------------------------------------------------------
# is_missing：0 和 False 不是缺失
# --------------------------------------------------------------------------


def test_数字0不是缺失():
    """ipClass1Count=0 表示确认没有 I 类知产，是已核验的真实取值。"""
    assert F.is_missing(0) is False
    assert F.is_missing(0.0) is False


def test_False不是缺失():
    assert F.is_missing(False) is False


def test_None是缺失():
    assert F.is_missing(None) is True


def test_空串与空白串是缺失():
    assert F.is_missing("") is True
    assert F.is_missing("   ") is True


def test_接口的各种空值写法都识别为缺失():
    for raw in ("null", "NULL", "None", "nan", "-", "--", "无", "未知", "暂无"):
        assert F.is_missing(raw) is True, raw


def test_空列表与空字典是缺失():
    assert F.is_missing([]) is True
    assert F.is_missing({}) is True


def test_非空列表不是缺失():
    assert F.is_missing([1]) is False


def test_字符串0不是缺失():
    assert F.is_missing("0") is False


# --------------------------------------------------------------------------
# to_int / to_float：None 与 0 必须区分
# --------------------------------------------------------------------------


def test_to_int把0解析为0而非None():
    assert F.to_int(0) == 0
    assert F.to_int("0") == 0


def test_to_int把缺失解析为None():
    assert F.to_int(None) is None
    assert F.to_int("") is None
    assert F.to_int("null") is None


def test_to_int无法解析时返回None():
    assert F.to_int("abc") is None


def test_to_int支持带千分位的字符串():
    assert F.to_int("1,234") == 1234


def test_to_int把浮点截断为整数():
    assert F.to_int(3.9) == 3


def test_to_float把0解析为0():
    assert F.to_float(0) == 0.0
    assert F.to_float("0") == 0.0


def test_to_float去掉百分号():
    assert F.to_float("50.0%") == 50.0


def test_to_float缺失返回None():
    assert F.to_float(None) is None


# --------------------------------------------------------------------------
# yuan_to_wan
# --------------------------------------------------------------------------


def test_元转万元():
    """接口经营信息金额单位是元，内部统一万元。"""
    assert F.yuan_to_wan(23995200.0) == 2399.52
    assert F.yuan_to_wan(7353900.0) == 735.39


def test_元转万元保留0():
    assert F.yuan_to_wan(0) == 0.0


def test_元转万元缺失返回None():
    assert F.yuan_to_wan(None) is None


def test_元转万元支持负数():
    """净资产可以为负（实测样本里有 netAssets=-13521500）。"""
    assert F.yuan_to_wan(-13521500.0) == -1352.15


# --------------------------------------------------------------------------
# yes_no：null 不当作 False
# --------------------------------------------------------------------------


def test_yes_no解析是与否():
    assert F.yes_no("是") is True
    assert F.yes_no("否") is False


def test_yes_no对null返回None而非False():
    """资质字段实测大面积为 null，当 False 会把「不知道」误判成「确认没有」。"""
    assert F.yes_no(None) is None
    assert F.yes_no("") is None


def test_yes_no支持英文与数字():
    assert F.yes_no("yes") is True
    assert F.yes_no("Y") is True
    assert F.yes_no("true") is True
    assert F.yes_no("1") is True
    assert F.yes_no("no") is False
    assert F.yes_no("0") is False


def test_yes_no对无法识别的值返回None():
    assert F.yes_no("待确认") is None


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------


def test_mark按值是否缺失登记状态():
    av = F.Availability()
    av.mark("a", "值")
    av.mark("b", None)

    assert av.state("a") == F.AVAILABLE
    assert av.state("b") == F.UNKNOWN


def test_mark对0登记为available():
    """这是 0 vs null 区分在可用性登记上的体现。"""
    av = F.Availability()
    av.mark("zero", 0)

    assert av.state("zero") == F.AVAILABLE
    assert av.is_available("zero") is True


def test_mark_resolved对None登记unknown对0登记available():
    av = F.Availability()
    av.mark_resolved("x", 0)
    av.mark_resolved("y", None)

    assert av.state("x") == F.AVAILABLE
    assert av.state("y") == F.UNKNOWN


def test_mark_resolved对False登记available():
    av = F.Availability()
    av.mark_resolved("flag", False)

    assert av.state("flag") == F.AVAILABLE


def test_未登记字段默认unknown():
    av = F.Availability()
    assert av.state("never") == F.UNKNOWN


def test_any_available与all_available():
    av = F.Availability()
    av.mark("a", "值")
    av.mark("b", None)

    assert av.any_available(["a", "b"]) is True
    assert av.all_available(["a", "b"]) is False
    assert av.all_available(["a"]) is True


def test_all_available对空列表返回False():
    av = F.Availability()
    assert av.all_available([]) is False


def test_completeness汇总():
    av = F.Availability()
    av.mark("a", "值")
    av.mark("b", 0)
    av.mark("c", None)

    result = av.completeness()

    assert result == {"available": 2, "unknown": 1, "total": 3}


def test_unknown_fields与available_fields排序稳定():
    av = F.Availability()
    av.mark("z", "值")
    av.mark("a", "值")
    av.mark("m", None)

    assert av.available_fields() == ["a", "z"]
    assert av.unknown_fields() == ["m"]


def test_mark返回原值便于链式使用():
    av = F.Availability()
    assert av.mark("x", "值") == "值"
    assert av.mark("y", 0) == 0


# --------------------------------------------------------------------------
# first_available：多源回退
# --------------------------------------------------------------------------


def test_first_available按顺序取第一个有值的():
    source, value = F.first_available(("a", None), ("b", 5), ("c", 9))

    assert source == "b"
    assert value == 5


def test_first_available把0视为有值():
    """ipClass1+2 求和为 0 时应命中该源，而非跳到下一个回退源。"""
    source, value = F.first_available(("sum", 0), ("count", 7))

    assert source == "sum"
    assert value == 0


def test_first_available全部无值返回空():
    source, value = F.first_available(("a", None), ("b", None))

    assert source == ""
    assert value is None
