"""Signature-driven CLI dispatcher.

Command values are either module paths (``str``) for lazy import, or callables
for direct invocation. Module-backed commands support nested dispatch (set a
module-level ``_dispatcher``) or signature-driven argument parsing.

Argument handling: if the leaf's ``main`` (or a callable target) takes no
parameters, the dispatcher intercepts ``-h``/``--help`` and prints
``Usage: <prog> [args...]`` + the docstring. If it takes parameters, the
dispatcher introspects the signature and builds an ``argparse`` parser from
it. Signature → CLI shape:

- ``name: str``                → required positional
- ``name: str | None = None``  → optional positional (``nargs="?"``)
- ``*files: str``              → variadic positional (``nargs="*"``)
- ``*, force: bool = False``   → ``--force`` (``store_true``); bools must be
                                  keyword-only with default ``False`` (name with
                                  negation built in if needed, e.g. ``no_color``)
- ``*, n: int = 5``            → ``--n N`` with default
- ``*, m: Literal["a","b"]``   → ``--m`` restricted to choices
- ``*, xs: list[float] = []``  → ``--xs 1.5,3.5`` (comma-separated; ``""`` → [];
                                  keyword-only, like bools)

Argparse owns ``--help`` and error messages for signature-dispatched leaves.
"""

import argparse
import importlib
import inspect
import pkgutil
import sys
import types
import typing
from collections.abc import Callable

__version__ = "0.1.0"

_SKIP = frozenset({"__main__", "__init__", "constants", "cli"})
DISP_VAR = "_dispatcher"
COMP_FUN = "get_completions"
_HELP_FLAGS = (["-h"], ["--help"])

# Sentinel a leaf's ``get_completions`` may return (as the sole candidate) to
# request the shell's native pathname completion — handles ``~``, ``/``, ``$VAR``
# and absolute/relative paths, which a static candidate list cannot. Recognised
# by the ``_proto_complete`` bash function; keep the two literals in sync.
FILE_COMPLETION = "\x1bFILES"


def _unwrap_annotation(ann: object) -> tuple[object, list | None]:
    """Return ``(base_type, choices_or_None)``.

    Handles ``Literal[...]`` (returns element type + values as choices) and
    ``Optional[T]``/``T | None`` (returns ``T``, no choices).
    """
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin is typing.Literal:
        return type(args[0]), list(args)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _unwrap_annotation(non_none[0])
    return ann, None


def _csv_of(elem: type) -> Callable[[str], list]:
    """Parser for a comma-separated ``list[elem]`` flag value; ``""`` → []."""

    def parse(s: str) -> list:
        return [elem(e) for e in s.split(",") if e]

    return parse


def _build_parser(prog: str, fn: Callable) -> argparse.ArgumentParser:
    """Generate an argparse parser from ``fn``'s signature."""
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    doc = (fn.__doc__ or "").strip().split("\n", 1)[0]
    parser = argparse.ArgumentParser(prog=prog, description=doc)
    for name, param in sig.parameters.items():
        raw_ann = hints.get(name, str)
        ann, choices = _unwrap_annotation(raw_ann)
        kwargs: dict = {}
        if choices is not None:
            kwargs["choices"] = choices
        kind = param.kind
        if kind is inspect.Parameter.VAR_POSITIONAL:
            kwargs["type"] = ann
            kwargs["nargs"] = "*"
            parser.add_argument(name, **kwargs)
            continue
        if ann is bool:
            if kind is not inspect.Parameter.KEYWORD_ONLY or param.default is not False:
                raise TypeError(
                    f"bool param {name!r} must be keyword-only with default False"
                )
            kwargs["action"] = "store_true"
            parser.add_argument(f"--{name.replace('_', '-')}", **kwargs)
            continue
        if typing.get_origin(ann) is list:
            if kind is not inspect.Parameter.KEYWORD_ONLY:
                raise TypeError(f"list param {name!r} must be keyword-only")
            elem = (typing.get_args(ann) or (str,))[0]
            kwargs["type"] = _csv_of(elem)
            kwargs["default"] = param.default
            parser.add_argument(f"--{name.replace('_', '-')}", **kwargs)
            continue
        kwargs["type"] = ann
        if kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs["default"] = param.default
            parser.add_argument(f"--{name.replace('_', '-')}", **kwargs)
        elif param.default is not inspect.Parameter.empty:
            kwargs["default"] = param.default
            kwargs["nargs"] = "?"
            parser.add_argument(name, **kwargs)
        else:
            parser.add_argument(name, **kwargs)
    return parser


def _sig_help(prog: str, fn: Callable) -> list[str] | None:
    """Return ``[parser.format_help().rstrip()]`` if ``fn`` has parameters."""
    if not inspect.signature(fn).parameters:
        return None
    return [_build_parser(prog, fn).format_help().rstrip()]


def _sig_completions(fn: Callable, rest: list[str]) -> list[str]:
    """Derive shell-completion candidates from ``fn``'s signature.

    Returns the keyword-only flag names; if the previous token is a flag whose
    annotation is ``Literal[...]``, returns those choices instead.
    """
    sig = inspect.signature(fn)
    if not sig.parameters:
        return []
    hints = typing.get_type_hints(fn)
    if rest:
        last = rest[-1]
        for name, param in sig.parameters.items():
            if param.kind is not inspect.Parameter.KEYWORD_ONLY:
                continue
            if last != f"--{name.replace('_', '-')}":
                continue
            _, choices = _unwrap_annotation(hints.get(name, str))
            return choices or []
    return [
        f"--{name.replace('_', '-')}"
        for name, param in sig.parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    ]


class Dispatcher:
    """Unified CLI dispatcher.

    Construct directly with an explicit command dict, or via ``from_package``
    for auto-discovery. Dict values that are ``str`` are treated as module
    paths (lazy-imported on dispatch); callables are invoked directly. Leaf
    ``main()`` / callable targets either take no params (dispatcher handles
    ``--help`` via docstring) or accept signature-driven args (argparse owns
    ``--help`` and parsing).
    """

    def __init__(self, prog: str, commands: dict[str, str | Callable]):
        self._prog = prog
        self._commands = commands

    @classmethod
    def from_package(cls, package: str, prog: str | None = None) -> "Dispatcher":
        mod = importlib.import_module(package)
        commands: dict[str, str | Callable] = {
            m.name: f"{package}.{m.name}"
            for m in pkgutil.iter_modules(mod.__path__)
            if m.name not in _SKIP and not m.name.startswith("_")
        }
        return cls(prog=prog or package.rsplit(".", 1)[-1], commands=commands)

    def commands(self) -> list[str]:
        return sorted(self._commands)

    def tree(self) -> dict[str, dict | None]:
        """Return nested command tree. Leaves are ``None``."""
        result: dict[str, dict | None] = {}
        for cmd in self.commands():
            target = self._commands[cmd]
            if callable(target):
                result[cmd] = None
                continue
            try:
                mod = importlib.import_module(target)
            except ImportError:
                result[cmd] = None
                continue
            child = getattr(mod, DISP_VAR, None)
            result[cmd] = child.tree() if child is not None else None
        return result

    def _get_doc(self, cmd: str) -> str:
        target = self._commands[cmd]
        if callable(target):
            return (target.__doc__ or "").strip().split("\n")[0]
        try:
            mod = importlib.import_module(target)
            return (mod.__doc__ or "").strip().split("\n")[0]
        except ImportError:
            return ""

    def _help_lines(self) -> list[str]:
        cmds = self.commands()
        lines = [f"Usage: {self._prog} <command> [args...]"]
        if cmds:
            width = max(len(c) for c in cmds)
            lines.append("Commands:")
            for cmd in cmds:
                doc = self._get_doc(cmd)
                suffix = f"  {doc}" if doc else ""
                lines.append(f"  {cmd:<{width}}{suffix}")
        return lines

    def _collect_help_all(self, sections: list[list[str]]) -> None:
        sections.append(self._help_lines())
        for cmd in self.commands():
            prog = f"{self._prog} {cmd}"
            target = self._commands[cmd]
            if callable(target):
                sig = _sig_help(prog, target)
                if sig:
                    sections.append(sig)
                continue
            try:
                mod = importlib.import_module(target)
            except ImportError:
                continue
            child = getattr(mod, DISP_VAR, None)
            if child is not None:
                child._collect_help_all(sections)
                continue
            sig = _sig_help(prog, mod.main)
            if sig:
                sections.append(sig)
                continue
            complete_fn = getattr(mod, COMP_FUN, None)
            if complete_fn:
                subs = complete_fn([])
                if subs:
                    sections.append([prog, f"  {', '.join(subs)}"])

    @staticmethod
    def _print_leaf_help(prog: str, doc: str | None) -> None:
        print(f"Usage: {prog} [args...]")
        text = (doc or "").strip()
        if text:
            print(text)

    def _invoke(
        self, prog: str, fn: Callable, rest: list[str], doc: str | None
    ) -> None:
        sys.argv = [prog, *rest]
        if not inspect.signature(fn).parameters:
            if rest[:1] in _HELP_FLAGS:
                self._print_leaf_help(prog, doc)
                return
            fn()
            return
        parser = _build_parser(prog, fn)
        parsed = parser.parse_args(rest)
        call_args, call_kwargs = [], {}
        for pname, param in inspect.signature(fn).parameters.items():
            val = getattr(parsed, pname)
            if param.kind is inspect.Parameter.VAR_POSITIONAL:
                call_args.extend(val)
            elif param.kind is inspect.Parameter.KEYWORD_ONLY:
                call_kwargs[pname] = val
            else:
                call_args.append(val)
        fn(*call_args, **call_kwargs)

    def run(self) -> None:
        argv = sys.argv[1:]
        cmds = self.commands()

        if not argv or [argv[0]] in _HELP_FLAGS:
            print("\n".join(self._help_lines()))
            return

        if argv[0] == "--help-all":
            sections: list[list[str]] = []
            self._collect_help_all(sections)
            print("\n\n".join("\n".join(s) for s in sections))
            return

        if argv[0] == "--complete":
            for line in self.get_completions(argv[1:]):
                print(line)
            return

        cmd, *rest = argv
        if cmd not in cmds:
            print(f"Unknown command: {cmd}", file=sys.stderr)
            raise SystemExit(1)

        target = self._commands[cmd]
        prog = f"{self._prog} {cmd}"

        if callable(target):
            self._invoke(prog, target, rest, target.__doc__)
            return

        try:
            mod = importlib.import_module(target)
        except ImportError as e:
            print(f"command {cmd!r} is unavailable: {e}", file=sys.stderr)
            raise SystemExit(1)
        child = getattr(mod, DISP_VAR, None)
        if child is not None:
            sys.argv = [prog, *rest]
            child.run()
            return
        self._invoke(prog, mod.main, rest, mod.__doc__)

    def get_completions(self, args: list[str]) -> list[str]:
        cmds = self.commands()
        if not args:
            return cmds
        cmd, *rest = args
        if cmd not in cmds:
            return []
        target = self._commands[cmd]
        if callable(target):
            return _sig_completions(target, rest)
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return []
        child = getattr(mod, DISP_VAR, None)
        if child is not None:
            return child.get_completions(rest)
        complete_fn = getattr(mod, COMP_FUN, None)
        if complete_fn is not None:
            return complete_fn(rest)
        return _sig_completions(mod.main, rest)
