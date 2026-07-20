# vim-euporie

Notebook-style Python in classic Vim, with a rich [Euporie](https://github.com/joouha/euporie) console managed automatically in a neighboring tmux pane.

The primary path is **Vim 9 → tmux → Ghostty**. Edit ordinary `.py` files, divide them into VS Code-style `# %%` cells, and send a cell without leaving Vim. The sidecar uses the current [`uv`](https://docs.astral.sh/uv/) project, while Euporie shows text, tracebacks, Matplotlib figures, LaTeX, and supported Jupyter widgets inline.

```python
# %%
import matplotlib.pyplot as plt

x = range(20)
plt.plot(x, [n**2 for n in x])
plt.show()

# %% [markdown]
# ## A rendered equation
#
# $E = mc^2$
```

Press `\er` with the cursor in either cell. The first execution opens an Euporie pane automatically; subsequent cells reuse the same kernel and namespace.

## Why this design

vim-euporie composes existing tools instead of forking Vim, Euporie, or Jupyter:

- Vim handles editing and `# %%` cell boundaries.
- `uv run` supplies the project's interpreter and dependencies.
- An IPython kernel maintains the interactive namespace.
- Euporie attaches as a second Jupyter client and renders rich output.
- tmux owns the pane layout and survives ordinary redraws.

Vim talks to the sidecar through its built-in channel API. It does **not** need `+python3`, Neovim, a Vim terminal buffer, or a globally installed Jupyter stack.

## Requirements

- Vim 9.0 or newer with `+channel`, `+job`, and JSON support
- tmux 3.3a or newer
- `uv`
- Ghostty, Kitty, or another terminal supporting Kitty graphics
- Python 3.10 or newer for current Euporie releases

Debian 12 (Bookworm) supplies Vim 9.0, tmux 3.3a, and Python 3.11. Install `uv` separately, then let the plugin resolve Euporie and ipykernel on first use. No permanent Python tool installation is required.

For reliable Kitty graphics through tmux, add this to `~/.tmux.conf`:

```tmux
set -g allow-passthrough on
```

The plugin also enables that option in the running tmux server by default.

## Installation

With Vim 8/9 native packages:

```sh
git clone https://github.com/krystophny/vim-euporie \
  ~/.vim/pack/plugins/start/vim-euporie
vim -u NONE -c 'helptags ~/.vim/pack/plugins/start/vim-euporie/doc' -c quit
```

Or with vim-plug:

```vim
Plug 'krystophny/vim-euporie'
```

For a new notebook-style directory, first create a minimal uv project and add
plotting support:

```sh
uv init --bare
uv add matplotlib
```

If you prefer a single self-contained script with PEP 723 metadata, use:

```sh
uv add --script analysis.py matplotlib
```

vim-euporie detects the inline metadata and launches the kernel with those
dependencies. Each metadata-bearing script receives its own pane and kernel.
If you change its dependency metadata while Vim is open, run
`:EuporieRestart`.

Then start Vim from inside tmux and edit a Python file:

```sh
tmux
uv run vim analysis.py
```

Starting Vim through `uv run` is optional. The plugin itself launches the
kernel through the detected uv project or the current script's inline
metadata.

## Everyday use

The default Python-buffer mappings use `maplocalleader` (a backslash unless you changed it):

| Mapping | Action |
|---|---|
| `\er` | Run current cell and advance |
| `\ec` | Run current cell without moving |
| `\el` | Run current line |
| `\e` in Visual mode | Run selection |
| `\ei` | Interrupt the kernel |
| `\ef` | Focus the Euporie pane |

Commands include `:EuporieStart`, `:EuporieStop`, `:EuporieRestart`, `:EuporieFocus`, `:EuporieStatus`, `:EuporieInterrupt`, `:EuporieSendCell`, `:EuporieRunCell`, `:[range]EuporieSend`, and `:EuporieSendFile`.

The Euporie pane is a real interactive console: focus it with `\ef`, type exploratory code directly, inspect and scroll output, use completions, or work with widgets. Input sent from Vim and input typed into Euporie share the same kernel. When the final attached Vim exits, the pane and kernel close automatically after a short grace period.

## Configuration

Defaults are deliberately tuned for Ghostty inside tmux:

```vim
let g:vim_euporie_auto_start = 1
let g:vim_euporie_graphics = 'kitty-unicode'
let g:vim_euporie_split = 'horizontal'
let g:vim_euporie_pane_percent = 40
let g:vim_euporie_idle_timeout = 20
```

Set `g:vim_euporie_split = 'vertical'` for a pane below Vim. To keep only `<Plug>` mappings and define your own:

```vim
let g:vim_euporie_no_mappings = 1
nmap <silent> <Leader>x <Plug>(EuporieRunCell)
xmap <silent> <Leader>x <Plug>(EuporieSendVisual)
```

Add packages to the ephemeral uv overlay if they are not project dependencies:

```vim
let g:vim_euporie_with = ['euporie', 'ipykernel', 'matplotlib', 'ipywidgets']
```

Pass additional arguments directly to Euporie:

```vim
let g:vim_euporie_euporie_args = ['--edit-mode', 'vi']
```

Project detection uses the nearest `pyproject.toml`, `uv.lock`, or `.git` directory. Override it with `b:vim_euporie_root` or `g:vim_euporie_root`.

## Lifecycle model

The sharing key is `(tmux window, project root)` for uv projects, so related
Python buffers share one pane and one live namespace. Standalone PEP 723
scripts are keyed separately because they may declare different dependencies.
Vim clients register and send heartbeats. A second Vim in the same tmux window
can attach to the existing sidecar; stale clients expire, and the sidecar
cleans up the kernel, connection file, control state, and tmux pane when
unused.

Control traffic is bound to `127.0.0.1` and authenticated with a random token stored in a mode-0600 runtime file. No Jupyter port is exposed beyond its normal localhost connection sockets.

## Status

This is an initial implementation. Python/uv projects, code cells, Markdown cells, rich display output, interruption, pane reuse, and automatic cleanup are in scope. Other Jupyter kernels can be added later without changing the Vim-facing cell model.

MIT licensed.
