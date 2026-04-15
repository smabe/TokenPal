"""Unit conversion via ``pint``.

Pure, offline, no API keys. Handles anything pint understands (length, mass,
time, temperature, energy, currency-less numerics, etc.).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, ClassVar

from tokenpal.actions.base import AbstractAction, ActionResult
from tokenpal.actions.registry import register_action

# Values past this magnitude hit pint's float precision cliff and start
# returning nonsense. Cap and report rather than mislead.
_MAX_ABS_VALUE = 1e15


@lru_cache(maxsize=1)
def _get_ureg() -> Any:
    # UnitRegistry init parses ~400KB of unit definitions. Cache it.
    import pint

    return pint.UnitRegistry()


@register_action
class ConvertAction(AbstractAction):
    action_name = "convert"
    description = (
        "Convert a numeric value between units "
        "(e.g. '10 miles to km', '32 fahrenheit to celsius')."
    )
    parameters: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "value": {
                "type": "number",
                "description": "Numeric amount to convert.",
            },
            "from_unit": {
                "type": "string",
                "description": "Source unit (e.g. 'mi', 'kg', 'fahrenheit').",
            },
            "to_unit": {
                "type": "string",
                "description": "Target unit (e.g. 'km', 'lb', 'celsius').",
            },
        },
        "required": ["value", "from_unit", "to_unit"],
    }
    safe = True
    requires_confirm = False
    platforms = ("windows", "darwin", "linux")

    async def execute(self, **kwargs: Any) -> ActionResult:
        raw_value = kwargs.get("value")
        from_unit = kwargs.get("from_unit")
        to_unit = kwargs.get("to_unit")
        if raw_value is None or not from_unit or not to_unit:
            return ActionResult(
                output="value, from_unit, and to_unit are required.",
                success=False,
            )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return ActionResult(output=f"value must be numeric, got {raw_value!r}.", success=False)
        if abs(value) > _MAX_ABS_VALUE:
            return ActionResult(
                output=f"value too large (|x| must be < {_MAX_ABS_VALUE:g}).",
                success=False,
            )

        import pint  # lazy: most sessions never call convert

        ureg = _get_ureg()
        # Quantity(value, unit) avoids pint's "ambiguous offset unit" error
        # that ``value * ureg(unit)`` raises for Fahrenheit/Celsius.
        try:
            quantity = ureg.Quantity(value, str(from_unit))
            converted = quantity.to(str(to_unit))
        except pint.errors.UndefinedUnitError as e:
            return ActionResult(output=f"Unknown unit: {e}", success=False)
        except pint.errors.DimensionalityError as e:
            return ActionResult(output=f"Incompatible units: {e}", success=False)
        except (AttributeError, ValueError, TypeError) as e:
            # pint raises a wide net for malformed unit strings
            return ActionResult(output=f"Cannot convert: {e}", success=False)

        magnitude = float(converted.magnitude)
        # Collapse floating-point noise near zero (e.g. 32degF -> 0degC lands
        # at ~5.7e-14 rather than 0.0). Anything below 1e-9 is noise given
        # our |x| < 1e15 input cap.
        if abs(magnitude) < 1e-9:
            magnitude = 0.0
        formatted = f"{magnitude:.4g}"
        return ActionResult(output=f"{value:g} {from_unit} approx {formatted} {to_unit}")
