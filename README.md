# vim-euporie

Notebook-style Python in classic Vim, with a rich [Euporie](https://github.com/joouha/euporie) console managed automatically in a neighboring tmux pane.

The primary path is **Vim 9 → tmux → XFCE Terminal with Sixel**. Edit ordinary `.py` files, divide them into VS Code-style `# %%` cells, and send a cell without leaving Vim. The sidecar uses the current [`uv`](https://docs.astral.sh/uv/) project, while Euporie shows text, tracebacks, Matplotlib figures, LaTeX, and supported Jupyter widgets inline.

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
- tmux 3.4 or newer, **built with `--enable-sixel`** (see below)
- `uv`
- a Sixel-capable terminal, such as XFCE Terminal built against a Sixel-enabled VTE
- Python 3.10 or newer for current Euporie releases

Install `uv` separately, then let the plugin resolve Euporie and ipykernel on first use. No permanent Python tool installation is required. Stable VTE releases currently omit Sixel support, so XFCE Terminal needs a Sixel-enabled VTE build. tmux 3.4 and newer can manage Sixel images as pane content, including scrolling, clipping, and redraws.

The default Sixel path deliberately disables Euporie's multiplexer passthrough. Euporie sends Sixel to tmux, and tmux parses and redraws it before XFCE Terminal renders it. This avoids the inactive-pane passthrough and repaint problems of terminal-specific graphics protocols.

### tmux must be built with Sixel support

Sixel in tmux is a compile-time option, not a runtime one. A tmux built
without `--enable-sixel` still reports `sixel` in `#{client_termfeatures}`,
because that string only mirrors the outer terminal's DA1 response and is
never checked against tmux's own build. Such a tmux then discards every image
it receives, so everything else works and figures simply never appear. This is
the usual cause of "no plots" on macOS, where Homebrew's tmux is built without
the flag.

### Figure size

Euporie scales each figure to the width of the output area, computed from the
terminal's cell size in pixels. Inside tmux, TIOCGWINSZ and `CSI 14 t` both
answer with tmux's own rounded geometry rather than the attached terminal's,
and where tmux reports no pixel size at all Euporie falls back to a hardcoded
10x20 guess, which is what makes figures look small on HiDPI screens.
vim-euporie takes the real size from tmux's `#{client_cell_width}` instead, so
figures fill the pane.

Figures are therefore as wide as the Euporie pane: widen it with
`g:vim_euporie_pane_percent` to make them larger. Matplotlib's `figsize`
controls the aspect ratio, so it changes a figure's height rather than its
width.

`g:vim_euporie_graphics = 'auto'` (the default) probes the running tmux for
this directly: it renders a 27-byte Sixel into a throwaway tmux server and
checks whether the cursor advanced. A build that really parses Sixel consumes
cell rows; one that does not leaves the cursor on the first line. When the
probe fails, vim-euporie warns and falls back to `kitty-unicode` instead of
silently rendering nothing. To keep the native path, build tmux with
`--enable-sixel`.

Kitty graphics remain available as a compatibility mode. For that mode, add this to `~/.tmux.conf`:

```tmux
set -g allow-passthrough on
```

tmux drops graphics passthrough emitted by an inactive pane. For
`kitty-unicode`, vim-euporie automatically sends the position-independent
image upload and virtual-placement commands to the attached tmux client's TTY;
the Unicode placeholder cells still pass through tmux, so images scroll and
redraw with the surrounding output while Vim keeps focus.

The plugin also enables that option in the running tmux server by default. It is harmless in Sixel mode.

Ghostty 1.3.1 has upstream Kitty Unicode-placement bugs inside tmux. Use a
post-1.3.1 build containing the graphics cache and cell-geometry fixes (or the
next stable release) for reliable `kitty-unicode` output. As a compatibility
fallback, set `g:vim_euporie_graphics = 'kitty'`.

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
| `Alt+Enter` | Run the current cell and advance (or run the Visual selection) |
| `Shift+Enter` | Same, on terminals whose keyboard protocol can report it |
| `\er` | Run current cell and advance |
| `\ec` | Run current cell without moving |
| `\el` | Run current line |
| `\e` in Visual mode | Run selection |
| `\ei` | Interrupt the kernel |
| `\ef` | Focus the Euporie pane |

Commands include `:EuporieStart`, `:EuporieStop`, `:EuporieRestart`, `:EuporieFocus`, `:EuporieStatus`, `:EuporieInterrupt`, `:EuporieSendCell`, `:EuporieRunCell`, `:[range]EuporieSend`, and `:EuporieSendFile`.

The Euporie pane is a real interactive console: focus it with `\ef`, type exploratory code directly, inspect and scroll output, use completions, or work with widgets. Input sent from Vim and input typed into Euporie share the same kernel. When the final attached Vim exits, the pane and kernel close automatically. Set a positive idle timeout if you want a grace period for reopening Vim.

## Configuration

Defaults are tuned for native Sixel inside tmux:

```vim
let g:vim_euporie_auto_start = 1
let g:vim_euporie_graphics = 'auto'
let g:vim_euporie_split = 'horizontal'
let g:vim_euporie_pane_percent = 40
let g:vim_euporie_idle_timeout = 0
let g:vim_euporie_configure_keyboard = 1
```

Alt+Enter and Shift+Enter work in Normal and Insert mode, and send the
selected text in Visual mode. Inside tmux, the plugin enables `extended-keys`
and Vim's modifyOtherKeys level 2 so modified Enter keys remain distinct from
Enter between tmux and Vim. Set `g:vim_euporie_configure_keyboard = 0` to
manage that protocol yourself. A terminal-key fallback covers the CSI-u
encoding emitted by Debian Bookworm's tmux 3.3a for Vim 9.0.

Whether Shift+Enter reaches tmux at all depends on the outermost terminal.
VTE-based terminals such as XFCE Terminal implement neither xterm's
modifyOtherKeys nor the kitty keyboard protocol
([vte#2601](https://gitlab.gnome.org/GNOME/vte/-/issues/2601)), so they send
Shift+Enter as a plain Enter that no tmux or Vim setting can recover. On the
primary XFCE Terminal path, use Alt+Enter, which VTE reports distinctly and
which behaves identically. Shift+Enter additionally works under outer
terminals with a modern keyboard protocol, such as kitty, Ghostty, WezTerm,
foot, and xterm.

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
