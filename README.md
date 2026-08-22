# protocli

[![pypi](https://img.shields.io/pypi/v/protocli.svg)](https://pypi.org/project/protocli/)

Signature-driven CLI dispatcher: typed function signatures become argparse parsers, modules become lazily-imported subcommands, with nested dispatch, `--help-all` and lazy shell completions.

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

## Dynamic completions

`Literal` fixes a closed set the parser enforces. `Complete` carries candidates the parser never sees — computed on demand, when the shell asks and only then:

```python
from typing import Annotated
from protocli import Complete, FILES

Machine = Annotated[str, Complete(inventory.get_names)]   # any () -> Sequence[str]

def deploy(*names: Machine, config: Annotated[str, FILES] = ""): ...
```

`deploy <TAB>` offers the machine names, dropping the ones already typed (`Complete(fn, repeat=True)` keeps them); `--config <TAB>` defers to the shell's native pathname completion.

The completer runs on the `--complete` path alone — never on dispatch, never under `--help-all`. Its values are advisory and never become argparse `choices`, so an unknown value reaches the function and is rejected there, with the function's own error. That is the difference from `Literal`, and the reason the two are exclusive on one parameter.

Completion resolves which parameter the cursor sits on, so positionals, variadics and flag values all complete. A module can instead expose a `get_completions(rest)` hook when candidates depend on more than one argument, or its own nested `_dispatcher = Dispatcher(...)`.

`mytool --help-all` prints every parser in the tree; `mytool --complete <args…>`
emits completion candidates for shell integration.

Auto-discovery over a package's public modules:

```python
Dispatcher.from_package("mytool").run()
```
