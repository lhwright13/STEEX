"""Equation parsing, rendering, and analysis utilities."""

import re
from typing import Callable, Dict, List, Optional

import numpy as np

try:
    import sympy
    from sympy import symbols, sympify, latex, simplify as sympy_simplify

    SYMPY_AVAILABLE = True
except ImportError:
    SYMPY_AVAILABLE = False

from .models import DiscoveredEquation


def parse_equation(expr_str: str) -> Optional[object]:
    """Parse a PySR expression string into a sympy expression.

    Args:
        expr_str: Expression string from PySR (e.g., "x0 * x1 + x2")

    Returns:
        Sympy expression or None if parsing fails
    """
    if not SYMPY_AVAILABLE:
        return None

    try:
        return sympify(expr_str)
    except Exception:
        return None


def equation_to_latex(expr_str: str) -> str:
    """Convert expression string to LaTeX representation.

    Args:
        expr_str: Expression string from PySR

    Returns:
        LaTeX string, or the original expression if conversion fails
    """
    if not SYMPY_AVAILABLE:
        return expr_str

    try:
        expr = sympify(expr_str)
        return latex(expr)
    except Exception:
        return expr_str


def equation_to_python(
    expr_str: str,
    feature_names: List[str],
) -> Callable:
    """Convert expression string to a callable Python function.

    The returned function accepts a dict of feature values and returns
    the equation's output. Handles both x0-style and named variable formats
    (PySR v1.5+ outputs named variables when variable_names are provided).

    Args:
        expr_str: Expression string from PySR
        feature_names: Ordered list of feature names

    Returns:
        Callable that takes Dict[str, float] and returns float
    """
    # Detect whether expression uses x0-style or named variables
    uses_named = any(name in expr_str for name in feature_names)

    if SYMPY_AVAILABLE:
        try:
            # Define symbols matching what's in the expression
            if uses_named:
                sym_vars = [symbols(name) for name in feature_names]
            else:
                sym_vars = [symbols(f"x{i}") for i in range(len(feature_names))]

            expr = sympify(expr_str, locals={s.name: s for s in sym_vars})
            func = sympy.lambdify(sym_vars, expr, modules=["numpy"])

            def predict(features: Dict[str, float]) -> float:
                args = [features.get(name, 0.0) for name in feature_names]
                result = func(*args)
                if np.isnan(result) or np.isinf(result):
                    return 0.0
                return float(result)

            return predict
        except Exception:
            pass

    def predict_unavailable(features: Dict[str, float]) -> float:
        return 0.0

    return predict_unavailable


def extract_feature_importance(
    equations: List[DiscoveredEquation],
) -> Dict[str, float]:
    """Count feature occurrences across Pareto front weighted by R-squared.

    Args:
        equations: List of discovered equations from Pareto front

    Returns:
        Dict mapping feature name to importance score (higher = more important)
    """
    importance: Dict[str, float] = {}

    for eq in equations:
        weight = max(0.0, eq.r_squared)
        expr = eq.expression

        for i, name in enumerate(eq.feature_names):
            # Check for named variable (PySR v1.5+) or x{i}-style
            count = len(re.findall(rf"\b{re.escape(name)}\b", expr))
            if count == 0:
                count = len(re.findall(rf"\bx{i}\b", expr))
            if count > 0:
                importance[name] = importance.get(name, 0.0) + count * weight

    # Normalize to sum to 1
    total = sum(importance.values())
    if total > 0:
        importance = {k: v / total for k, v in importance.items()}

    return dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))


def simplify_equation(expr_str: str) -> str:
    """Simplify a sympy expression string.

    Args:
        expr_str: Expression string

    Returns:
        Simplified expression string
    """
    if not SYMPY_AVAILABLE:
        return expr_str

    try:
        expr = sympify(expr_str)
        simplified = sympy_simplify(expr)
        return str(simplified)
    except Exception:
        return expr_str


def substitute_feature_names(
    expr_str: str,
    feature_names: List[str],
) -> str:
    """Replace x0, x1, ... with human-readable feature names.

    Args:
        expr_str: Expression with x0, x1, ... variables
        feature_names: Ordered list of feature names

    Returns:
        Expression with readable names
    """
    result = expr_str
    # Replace in reverse order to avoid x1 matching x10, x11, etc.
    for i in range(len(feature_names) - 1, -1, -1):
        result = re.sub(rf"\bx{i}\b", feature_names[i], result)
    return result
