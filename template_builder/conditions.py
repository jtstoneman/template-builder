"""The template's tiny condition language, and its safe evaluator.

Conditions gate clauses (``include_when``) and variants (``when``). They are
a strict subset of Python expressions:

    is_mutual
    not include_non_solicit
    governing_law == "New York"
    is_mutual and term_years >= 3
    governing_law in ["New York", "Delaware"]

Names are questionnaire variables; constants are strings, numbers and
booleans. Nothing else — no calls, no attributes, no subscripts. Conditions
are data, not code, so evaluation walks an AST whitelist and never eval()s.
``and``/``or`` short-circuit exactly like Python, including skipping the
evaluation (and unknown-variable checks) of unreached operands.
"""

import ast
import functools
from collections.abc import Callable, Mapping

type Answers = Mapping[str, object]

# Conditions are one-liners written by humans; anything longer is either a
# mistake or an attempt to make the parser work too hard.
MAX_LENGTH = 1000


class ConditionError(ValueError):
    pass


_COMPARE_OPS: dict[type[ast.cmpop], Callable[[object, object], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,        # type: ignore[operator]
    ast.LtE: lambda a, b: a <= b,      # type: ignore[operator]
    ast.Gt: lambda a, b: a > b,        # type: ignore[operator]
    ast.GtE: lambda a, b: a >= b,      # type: ignore[operator]
    ast.In: lambda a, b: a in b,       # type: ignore[operator]
    ast.NotIn: lambda a, b: a not in b,  # type: ignore[operator]
}


def parse(expr: object) -> ast.Expression:
    """Parse a condition, rejecting anything outside the whitelist."""
    if not isinstance(expr, str) or not expr.strip():
        raise ConditionError("condition must be a non-empty string")
    if len(expr) > MAX_LENGTH:
        raise ConditionError(f"condition is too long ({len(expr)} characters; max {MAX_LENGTH})")
    return _parse_checked(expr)


@functools.lru_cache(maxsize=4096)
def _parse_checked(expr: str) -> ast.Expression:
    # Cached: the validation sweep evaluates the same handful of conditions
    # across hundreds of configurations. Consumers only READ the tree, so
    # sharing one instance is safe; failed parses raise and are not cached.
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ConditionError(f"invalid condition {expr!r}: {e.msg}") from None
    except (ValueError, RecursionError, MemoryError) as e:
        # e.g. NUL bytes raise a bare ValueError from the CPython parser
        raise ConditionError(f"invalid condition: {e}") from None
    try:
        _check(tree.body, expr)
    except RecursionError:
        raise ConditionError(f"condition {expr!r} is nested too deeply") from None
    return tree


def _check(node: ast.expr, expr: str) -> None:
    match node:
        case ast.BoolOp(values=values):
            for value in values:
                _check(value, expr)
        case ast.UnaryOp(op=ast.Not(), operand=operand):
            _check(operand, expr)
        case ast.UnaryOp(op=ast.USub() | ast.UAdd(),
                         operand=ast.Constant(value=value)) \
                if isinstance(value, (int, float)) and not isinstance(value, bool):
            pass  # a signed numeric literal, e.g. -1
        case ast.UnaryOp(op=op):
            raise ConditionError(
                f"condition {expr!r}: unary {type(op).__name__} is not allowed (only "
                f"'not', and +/- on number literals)")
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            _check(left, expr)
            for op, comparator in zip(ops, comparators):
                if type(op) not in _COMPARE_OPS:
                    raise ConditionError(
                        f"condition {expr!r}: comparison {type(op).__name__} is not allowed")
                if (type(op) in (ast.In, ast.NotIn)
                        and not isinstance(comparator, (ast.List, ast.Tuple))):
                    # `x in "New York"` would be substring matching — a trap
                    # that silently gates clauses on answers like "York"
                    raise ConditionError(
                        f"condition {expr!r}: the right side of 'in' must be a "
                        f"list, e.g. x in [\"a\", \"b\"]")
            for comparator in comparators:
                _check(comparator, expr)
        case ast.List(elts=elts) | ast.Tuple(elts=elts):
            for element in elts:
                _check(element, expr)
        case ast.Name():
            pass
        case ast.Constant(value=value) if isinstance(value, (str, int, float, bool)):
            pass
        case ast.Constant(value=value):
            raise ConditionError(f"condition {expr!r}: constant {value!r} is not allowed")
        case _:
            raise ConditionError(f"condition {expr!r}: {type(node).__name__} is not allowed")


def variables_in(expr: object) -> set[str]:
    """The set of variable names a condition reads. Parses (and so validates) it."""
    tree = parse(expr)
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def evaluate(expr: object, answers: Answers) -> bool:
    """Evaluate a condition against a mapping of questionnaire answers."""
    tree = parse(expr)
    try:
        return bool(_eval(tree.body, answers, expr))
    except ConditionError:
        raise
    except Exception as e:  # defence in depth: conditions are data, never crash callers
        raise ConditionError(f"condition {expr!r} failed to evaluate: {e}") from None


def _eval(node: ast.expr, answers: Answers, expr: str) -> object:
    match node:
        case ast.BoolOp(op=ast.And(), values=values):
            result: object = True
            for value in values:  # short-circuits like Python, returning the operand
                result = _eval(value, answers, expr)
                if not result:
                    return result
            return result
        case ast.BoolOp(values=values):  # Or
            result = False
            for value in values:
                result = _eval(value, answers, expr)
                if result:
                    return result
            return result
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_eval(operand, answers, expr)   # type: ignore[operator]
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return +_eval(operand, answers, expr)   # type: ignore[operator]
        case ast.UnaryOp(operand=operand):  # only Not survives _check
            return not _eval(operand, answers, expr)
        case ast.Compare(left=left, ops=ops, comparators=comparators):
            left_value = _eval(left, answers, expr)
            for op, comparator in zip(ops, comparators):
                right_value = _eval(comparator, answers, expr)
                try:
                    if not _COMPARE_OPS[type(op)](left_value, right_value):
                        return False
                except TypeError:
                    raise ConditionError(
                        f"condition {expr!r}: cannot compare {left_value!r} with {right_value!r}"
                    ) from None
                left_value = right_value
            return True
        case ast.List(elts=elts):
            return [_eval(e, answers, expr) for e in elts]
        case ast.Tuple(elts=elts):
            return tuple(_eval(e, answers, expr) for e in elts)
        case ast.Name(id=name):
            if name not in answers:
                raise ConditionError(f"condition {expr!r} uses unknown variable {name!r}")
            return answers[name]
        case ast.Constant(value=value):
            return value
        case _:  # unreachable after _check; kept as a hard stop
            raise ConditionError(f"condition {expr!r}: {type(node).__name__} is not allowed")
