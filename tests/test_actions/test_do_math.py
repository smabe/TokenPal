"""Tests for the do_math action + safe arithmetic evaluator."""

from __future__ import annotations

import pytest

from tokenpal.actions.do_math import DoMathAction, MathError, safe_eval


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2 + 2", 4),
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("10 / 4", 2.5),
        ("10 // 4", 2),
        ("10 % 3", 1),
        ("2 ** 10", 1024),
        ("-5 + 3", -2),
        ("+7", 7),
        ("1.5 * 2", 3.0),
    ],
)
def test_safe_eval_arithmetic(expr: str, expected: float) -> None:
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "open('/etc/passwd')",
        "1 + abs(-2)",
        "[1, 2]",
        "1 if True else 0",
        "True and False",
        "'a' + 'b'",
        "lambda x: x",
        "x = 1",
    ],
)
def test_safe_eval_rejects_non_arithmetic(expr: str) -> None:
    with pytest.raises(MathError):
        safe_eval(expr)


def test_safe_eval_rejects_huge_exponent() -> None:
    with pytest.raises(MathError):
        safe_eval("2 ** 9999")


def test_safe_eval_rejects_long_expr() -> None:
    with pytest.raises(MathError):
        safe_eval("1" + " + 1" * 100)


async def test_do_math_action_success() -> None:
    action = DoMathAction({})
    result = await action.execute(expr="7 * 6")
    assert result.success is True
    assert "42" in result.output


async def test_do_math_action_invalid() -> None:
    action = DoMathAction({})
    result = await action.execute(expr="import os")
    assert result.success is False


async def test_do_math_action_empty() -> None:
    action = DoMathAction({})
    result = await action.execute(expr="")
    assert result.success is False


async def test_do_math_action_division_by_zero() -> None:
    action = DoMathAction({})
    result = await action.execute(expr="1 / 0")
    assert result.success is False
    assert "zero" in result.output.lower()


def test_do_math_action_flags() -> None:
    assert DoMathAction.safe is True
    assert DoMathAction.requires_confirm is False
