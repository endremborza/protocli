import sys
import types
import typing

import pytest

from protocli import (
    FILE_COMPLETION,
    Dispatcher,
    _build_parser,
    _unwrap_annotation,
)


def test_unwrap_annotation() -> None:
    assert _unwrap_annotation(int) == (int, None)
    assert _unwrap_annotation(typing.Literal["a", "b"]) == (str, ["a", "b"])
    assert _unwrap_annotation(typing.Optional[int]) == (int, None)
    assert _unwrap_annotation(str | None) == (str, None)


def test_parser_shapes() -> None:
    def fn(
        name: str,
        opt: str | None = None,
        *files: str,
        force: bool = False,
        n: int = 5,
        mode: typing.Literal["a", "b"] = "a",
    ) -> None: ...

    parser = _build_parser("prog", fn)
    ns = parser.parse_args(["x", "y", "f1", "f2", "--force", "--n", "7", "--mode", "b"])
    assert (ns.name, ns.opt, ns.files) == ("x", "y", ["f1", "f2"])
    assert (ns.force, ns.n, ns.mode) == (True, 7, "b")
    ns = parser.parse_args(["x"])
    assert (ns.opt, ns.files, ns.force, ns.n, ns.mode) == (None, [], False, 5, "a")
    with pytest.raises(SystemExit):
        parser.parse_args(["x", "--mode", "c"])


def test_list_flags() -> None:
    def fn(*, bins: list[float] = [], procs: list[int] = [16, 1]) -> None: ...

    parser = _build_parser("prog", fn)
    ns = parser.parse_args(["--bins", "1.5,3.5", "--procs", "4"])
    assert ns.bins == [1.5, 3.5] and ns.procs == [4]
    ns = parser.parse_args(["--bins", ""])
    assert ns.bins == [] and ns.procs == [16, 1]


def test_list_must_be_keyword_only() -> None:
    def fn(bins: list[float] = []) -> None: ...

    with pytest.raises(TypeError, match="keyword-only"):
        _build_parser("prog", fn)


def test_bool_constraints() -> None:
    def positional(flag: bool) -> None: ...

    def defaulted_true(*, flag: bool = True) -> None: ...

    for fn in (positional, defaulted_true):
        with pytest.raises(TypeError, match="keyword-only with default False"):
            _build_parser("prog", fn)


def _run(disp: Dispatcher, *argv: str, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    disp.run()


def test_callable_dispatch(monkeypatch) -> None:
    calls = []

    def greet(name: str, *, loud: bool = False) -> None:
        calls.append((name, loud))

    disp = Dispatcher("prog", {"greet": greet})
    _run(disp, "greet", "ada", "--loud", monkeypatch=monkeypatch)
    assert calls == [("ada", True)]


def test_module_and_nested_dispatch(monkeypatch, capsys) -> None:
    leaf = types.ModuleType("fake_leaf")
    leaf.__doc__ = "Leaf docs."
    calls = []
    leaf.main = lambda: calls.append("ran")
    sys.modules["fake_leaf"] = leaf

    inner = types.ModuleType("fake_nested")
    inner.ping = lambda: calls.append("pong")
    inner._dispatcher = Dispatcher("prog nest", {"ping": inner.ping})
    sys.modules["fake_nested"] = inner

    disp = Dispatcher("prog", {"leaf": "fake_leaf", "nest": "fake_nested"})
    _run(disp, "leaf", monkeypatch=monkeypatch)
    _run(disp, "nest", "ping", monkeypatch=monkeypatch)
    assert calls == ["ran", "pong"]
    assert disp.tree() == {"leaf": None, "nest": {"ping": None}}

    _run(disp, "leaf", "-h", monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "Usage: prog leaf [args...]" in out and "Leaf docs." in out


def test_unknown_command_exits(monkeypatch) -> None:
    disp = Dispatcher("prog", {})
    with pytest.raises(SystemExit):
        _run(disp, "ghost", monkeypatch=monkeypatch)


def test_unavailable_module_command(monkeypatch, capsys) -> None:
    # A listed command whose module cannot import (missing optional dep or a
    # module from an unmerged branch) fails cleanly, not with a traceback.
    disp = Dispatcher("prog", {"soon": "not_yet_merged_module"})
    with pytest.raises(SystemExit):
        _run(disp, "soon", monkeypatch=monkeypatch)
    assert "unavailable" in capsys.readouterr().err
    assert disp.get_completions(["soon"]) == []


def test_top_help_lists_commands(monkeypatch, capsys) -> None:
    def doer() -> None:
        """Does things."""

    disp = Dispatcher("prog", {"do": doer})
    _run(disp, monkeypatch=monkeypatch)
    out = capsys.readouterr().out
    assert "Usage: prog <command>" in out and "do  Does things." in out


def test_completions() -> None:
    def fn(*, mode: typing.Literal["x", "y"] = "x", n: int = 1) -> None: ...

    disp = Dispatcher("prog", {"cmd": fn})
    assert disp.get_completions([]) == ["cmd"]
    assert sorted(disp.get_completions(["cmd"])) == ["--mode", "--n"]
    assert disp.get_completions(["cmd", "--mode"]) == ["x", "y"]
    assert disp.get_completions(["ghost"]) == []
    assert FILE_COMPLETION.startswith("\x1b")


def test_from_package(tmp_path, monkeypatch) -> None:
    pkg = tmp_path / "fakepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hello.py").write_text('"""Say hi."""\ndef main():\n    print("hi")\n')
    (pkg / "_private.py").write_text("def main():\n    pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    disp = Dispatcher.from_package("fakepkg")
    assert disp.commands() == ["hello"]
    monkeypatch.setattr(sys, "argv", ["fakepkg", "hello"])
    disp.run()
