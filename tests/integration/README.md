# Integration probes

These drive the real stack instead of mocking it, so a pass means the thing
actually works rather than that a unit test agrees with itself. They need an X
server (`Xvfb`), `xdotool`, `tmux` and the patched VTE, so they are not part of
`tests/run.sh`; run them by hand when the key or graphics path changes.

## `vte_keys.py`

Checks what the terminal itself sends. A real `VteTerminal` on a headless X
server runs a child that asks for `modifyOtherKeys` and records its stdin;
`xdotool` delivers genuine key events. Confirms Shift+Enter arrives as
`CSI 27 ; 2 ; 13 ~` while plain Enter, Tab and ordinary characters are
unchanged — an encoding that broke those would be worse than the bug it fixes.

```
python3 tests/integration/vte_keys.py
```

## `shift_enter_chain.py`

The whole chain: patched VTE → tmux → Vim → this plugin. Presses Shift+Enter
for real and reports which mapping fired.

```
python3 tests/integration/shift_enter_chain.py on              # expect S-CR
python3 tests/integration/shift_enter_chain.py on --no-extkeys # expect plain-CR
```

The second form is the negative control, and it matters: without the
`xterm*:extkeys` terminal-feature tmux never asks the terminal to report
modified keys, so Shift+Enter arrives as a bare Enter. A run where both forms
pass is a broken test, not a working stack.

## `flicker.py`

Measures the byte stream tmux sends **to the terminal**, which is where flicker
is visible. Note that `pipe-pane` is the wrong tap for this: it captures what
Euporie writes *into* tmux, whereas the synchronized-output wrapping and the
image re-emissions are added by tmux on the way *out*. This attaches the tmux
client to a pty and records that side.

```
python3 tests/integration/flicker.py tests/integration/cells.py
```

Caveat worth keeping in mind: the "windows that erase but carry no image"
figure counts any erase anywhere in a synchronized window, including status
line repaints that have nothing to do with the figure. It is too loose to
judge a fix by, and needs narrowing to the image's own rows before it can
support a conclusion.
