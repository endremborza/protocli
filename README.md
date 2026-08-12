# protocli

[![pypi](https://img.shields.io/pypi/v/protocli.svg)](https://pypi.org/project/protocli/)

Signature-driven CLI dispatcher: typed function signatures become argparse parsers, modules become lazily-imported subcommands, with nested dispatch, `--help-all` and shell completions.

```python
# mytool/__main__.py
from protocli import Dispatcher

Dispatcher(
    "mytool",
    {
        "greet": greet,          # a callable: its signature IS the parser
        "db": "mytool.db",       # a module path: lazy-imported on dispatch
    },
).run()
```

A callable (or a module's `main`) maps to CLI arguments by signature:

```python
def greet(
    name: str,                        # required positional
    title: str | None = None,         # optional positional
    *rest: str,                       # variadic
    loud: bool = False,               # --loud   (keyword-only, default False)
    times: int = 1,                   # --times N
    lang: Literal["en", "hu"] = "en", # --lang restricted to choices
    tags: list[str] = [],             # --tags a,b,c  (comma-separated)
): ...
```

A module can instead expose its own nested `_dispatcher = Dispatcher(...)`,
or a `get_completions(rest)` hook for custom shell completion (return
`[FILE_COMPLETION]` to request native pathname completion).

`mytool --help-all` prints every parser in the tree; `mytool --complete <args…>`
emits completion candidates for shell integration.

Auto-discovery over a package's public modules:

```python
Dispatcher.from_package("mytool").run()
```
