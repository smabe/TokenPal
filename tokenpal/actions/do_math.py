"""Do-math action — safe arithmetic evaluator.

Proof-of-concept for the action/tool registry: a tiny pure function the LLM
(or the /math slash command) can call with zero side effects. Uses an ast
walker restricted to arithmetic nodes so an expression like `__import__('os')`
never becomes code.
"""

from __future__ import annotations

import ast
import operator
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_MAX_EXPR_LEN = 200
_MAX_POW_EXPONENT = 64


class MathError(ValueError):
    """Raised when an expression is outside the allowed grammar."""


def safe_eval(expr: str) -> float | int:
    if len(expr) > _MAX_EXPR_LEN:
        raise MathError(f"expression too long (max {_MAX_EXPR_LEN} chars)")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise MathError(f"syntax error: {e.msg}") from e
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise MathError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise MathError(f"operator not allowed: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW_EXPONENT:
            raise MathError(f"exponent too large (max {_MAX_POW_EXPONENT})")
        result = op(left, right)
        return result  # type: ignore[no-any-return]
    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise MathError(f"unary operator not allowed: {type(node.op).__name__}")
        return unary(_eval_node(node.operand))  # type: ignore[no-any-return]
    raise MathError(f"node not allowed: {type(node).__name__}")


@register_action
class DoMathAction(AbstractAction):
    action_name = "do_math"
    description = "Evaluate a pure arithmetic expression (+, -, *, /, //, %, **)."
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "expr": {
                "type": "string",
                "description": "Arithmetic expression, e.g. '2 + 2 * 3'.",
            },
        },
        "required": ["expr"],
    }
    safe = True
    requires_confirm = False

    async def execute(self, **kwargs: Any) -> ActionResult:
        expr = kwargs.get("expr", "")
        if not isinstance(expr, str) or not expr.strip():
            return ActionResult(output="Expression is required.", success=False)
        try:
            result = safe_eval(expr)
        except MathError as e:
            return ActionResult(output=f"Cannot evaluate: {e}", success=False)
        except ZeroDivisionError:
            return ActionResult(output="Cannot evaluate: division by zero", success=False)
        except OverflowError:
            return ActionResult(output="Cannot evaluate: result too large", success=False)
        return ActionResult(output=f"{expr.strip()} = {result}")
