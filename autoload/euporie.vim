let s:contexts = {}
let s:heartbeat_timer = -1
let s:keyboard_protocol_configured = 0
let s:plugin_root = fnamemodify(resolve(expand('<sfile>:p')), ':h:h')

function! s:setting(name, default) abort
  return get(g:, 'vim_euporie_' . a:name, a:default)
endfunction

function! s:runtime_dir() abort
  let base = empty($XDG_RUNTIME_DIR)
        \ ? '/tmp/vim-euporie-' . getuid()
        \ : $XDG_RUNTIME_DIR . '/vim-euporie'
  call mkdir(base, 'p', 0700)
  return base
endfunction

function! s:tmux(args) abort
  " Vim 9.0 as shipped by Debian Bookworm only accepts a String here; list
  " support arrived later. Quote every argument so project paths remain safe.
  return system(join(map(['tmux'] + copy(a:args),
        \ {_, value -> shellescape(value)}), ' '))
endfunction

function! s:warn(message) abort
  echohl WarningMsg
  echomsg 'vim-euporie: ' . a:message
  echohl None
endfunction

function! s:tmux_sixel_capable() abort
  if exists('s:sixel_capable')
    return s:sixel_capable
  endif
  " Assume the native path when the probe cannot run, so an unexpected tmux
  " failure never downgrades a working setup.
  let s:sixel_capable = 1
  if !executable('tmux')
    return s:sixel_capable
  endif

  " tmux copies the terminal's DA1 sixel flag into #{client_termfeatures}
  " without checking its own build, so that string claims sixel even on a tmux
  " compiled without --enable-sixel, which then discards every image and shows
  " nothing. Probe the parser instead: a build that really understands sixel
  " consumes cell rows for the image, so the cursor leaves the first line.
  let probe = tempname()
  call writefile(["\<Esc>Pq#0;2;100;0;0#0~~~~~~~~\<Esc>\\"], probe, 'b')
  let socket = 'vim-euporie-probe-' . getpid()
  let server = ['-L', socket, '-f', '/dev/null']
  call s:tmux(server + ['new-session', '-d', '-x', '80', '-y', '24',
        \ 'cat ' . shellescape(probe) . '; sleep 5'])
  " The pane needs a moment to run cat, so poll rather than read once.
  let s:sixel_capable = 0
  for _ in range(20)
    let row = trim(s:tmux(server + ['display-message', '-p', '#{cursor_y}']))
    if row =~# '^\d\+$' && str2nr(row) > 0
      let s:sixel_capable = 1
      break
    endif
    sleep 50m
  endfor
  call s:tmux(server + ['kill-server'])
  call delete(probe)
  return s:sixel_capable
endfunction

" Resolve "auto" to the protocol this tmux can actually render.
function! euporie#graphics_mode() abort
  let mode = s:setting('graphics', 'auto')
  if mode !=# 'auto'
    if mode ==# 'sixel' && !s:tmux_sixel_capable()
      call s:warn('this tmux was built without --enable-sixel and silently '
            \ . 'discards images, so figures will not appear. Rebuild tmux '
            \ . 'with sixel support, or set g:vim_euporie_graphics to '
            \ . '"kitty-unicode".')
    endif
    return mode
  endif
  if s:tmux_sixel_capable()
    return 'sixel'
  endif
  call s:warn('this tmux was built without --enable-sixel; using kitty-unicode '
        \ . 'graphics instead. Rebuild tmux with sixel support for the native '
        \ . 'path.')
  return 'kitty-unicode'
endfunction

function! s:tmux_scope() abort
  if empty($TMUX)
    return ''
  endif
  return trim(s:tmux(['display-message', '-p', '#{session_id}:#{window_id}']))
endfunction

function! s:configure_keyboard() abort
  if s:keyboard_protocol_configured
        \ || empty($TMUX)
        \ || !s:setting('configure_keyboard', 1)
    return
  endif

  " Vim sees TERM=tmux-256color, so it does not select the Ghostty keyboard
  " protocol itself. Ask tmux for xterm modifyOtherKeys level 2, which Vim 9.0
  " understands and uses to distinguish Shift-Enter from Enter.
  call s:tmux(['set-option', '-s', 'extended-keys', 'on'])
  if exists('+keyprotocol') && &term =~# '^\%(tmux\|screen\)'
        \ && &keyprotocol !~# '\%(^\|,\)tmux:mok2\%($\|,\)'
    let &keyprotocol = 'tmux:mok2,screen:mok2,' . &keyprotocol
    " Re-evaluate termcap strings after changing 'keyprotocol'.
    let &term = &term
  endif
  let enable = "\<Esc>[>4;2m"
  let disable = "\<Esc>[>4;m"
  if stridx(&t_TI, enable) < 0
    let &t_TI .= enable
  endif
  if stridx(&t_TE, disable) < 0
    let &t_TE .= disable
  endif
  if exists('*echoraw')
    call echoraw(enable)
  endif
  let s:keyboard_protocol_configured = 1
endfunction

function! s:map_legacy_shift_enter() abort
  if v:version >= 901
    return
  endif
  " tmux before 3.5 emits CSI-u for extended keys, while other terminals may
  " emit xterm's modifyOtherKeys form. Vim 9.0 understands neither form for
  " Shift-Enter natively, so teach it two private terminal key codes.
  for [name, key] in [['E1', "\<Esc>[13;2u"], ['E2', "\<Esc>[27;2;13~"]]
    execute 'set t_' . name . '=' . key
    let term_key = '<t_' . name . '>'
    if empty(maparg(term_key, 'n'))
      execute 'nmap <buffer> <silent> ' . term_key . ' <Plug>(EuporieRunCell)'
    endif
    if empty(maparg(term_key, 'i'))
      execute 'imap <buffer> <silent> ' . term_key . ' <Plug>(EuporieRunCell)'
    endif
    if empty(maparg(term_key, 'x'))
      execute 'xmap <buffer> <silent> ' . term_key . ' <Plug>(EuporieSendVisual)'
    endif
  endfor
endfunction

function! euporie#project_root() abort
  if exists('b:vim_euporie_root')
    return fnamemodify(b:vim_euporie_root, ':p')[:-2]
  endif
  if exists('g:vim_euporie_root')
    return fnamemodify(g:vim_euporie_root, ':p')[:-2]
  endif

  let start = expand('%:p:h')
  if empty(start)
    let start = getcwd()
  endif
  let candidates = []
  for marker in ['pyproject.toml', 'uv.lock', '.git']
    let found = marker ==# '.git'
          \ ? finddir(marker, start . ';')
          \ : findfile(marker, start . ';')
    if !empty(found)
      call add(candidates, fnamemodify(found, ':p:h'))
    endif
  endfor
  if empty(candidates)
    return fnamemodify(start, ':p')[:-2]
  endif
  call sort(candidates, {a, b -> strlen(b) - strlen(a)})
  return candidates[0]
endfunction

function! euporie#has_inline_metadata() abort
  let last = min([line('$'), 100])
  return !empty(filter(getline(1, last),
        \ {_, text -> text =~# '^#\s*///\s*script\s*$'}))
endfunction

function! s:inline_script() abort
  if &filetype !=# 'python' || !euporie#has_inline_metadata()
    return ''
  endif
  return expand('%:p')
endfunction

function! s:context() abort
  let root = euporie#project_root()
  let scope = s:tmux_scope()
  let script = s:inline_script()
  let key = sha256(scope . "\n" . root . "\n" . script)[0:19]
  let state = s:runtime_dir() . '/' . key . '.json'
  if !has_key(s:contexts, key)
    let s:contexts[key] = {
          \ 'key': key,
          \ 'root': root,
          \ 'script': script,
          \ 'scope': scope,
          \ 'state': state,
          \ 'client': printf('vim-%d-%s', getpid(), key),
          \ 'attached': 0,
          \ 'pane': '',
          \ }
  endif
  return s:contexts[key]
endfunction

function! s:read_state(ctx) abort
  if !filereadable(a:ctx.state)
    return {}
  endif
  try
    let state = json_decode(join(readfile(a:ctx.state), "\n"))
    if type(state) != v:t_dict || !has_key(state, 'port') || !has_key(state, 'token')
      return {}
    endif
    return state
  catch
    return {}
  endtry
endfunction

function! s:pane_alive(pane) abort
  if empty(a:pane)
    return 0
  endif
  call s:tmux(['display-message', '-p', '-t', a:pane, '#{pane_id}'])
  return v:shell_error == 0
endfunction

function! s:request(ctx, payload, ...) abort
  let quiet = a:0 ? a:1 : 0
  let state = s:read_state(a:ctx)
  if empty(state)
    return {}
  endif
  let message = copy(a:payload)
  let message.token = state.token
  if has_key(message, 'client')
    let message.pid = getpid()
  endif
  let address = '127.0.0.1:' . state.port
  try
    let channel = ch_open(address, {'mode': 'raw', 'timeout': 2500})
    if ch_status(channel) !=# 'open'
      throw 'control channel did not open'
    endif
    call ch_sendraw(channel, json_encode(message) . "\n")
    let raw = ch_readraw(channel, {'timeout': 2500})
    call ch_close(channel)
    let reply = json_decode(trim(raw))
    if !get(reply, 'ok', 0) && !quiet
      echoerr 'vim-euporie: ' . get(reply, 'error', 'request failed')
    endif
    return reply
  catch
    if !quiet
      echoerr 'vim-euporie: ' . v:exception
    endif
    return {}
  endtry
endfunction

function! s:wait_for_state(ctx) abort
  let timeout = s:setting('start_timeout', 90) * 10
  for _ in range(timeout)
    let state = s:read_state(a:ctx)
    if !empty(state)
      return state
    endif
    if !empty(a:ctx.pane) && !s:pane_alive(a:ctx.pane)
      break
    endif
    sleep 100m
  endfor
  echoerr 'vim-euporie: sidecar did not become ready; inspect ' . a:ctx.state . '.log'
  return {}
endfunction

function! s:uv_command(ctx) abort
  let command = [s:setting('uv_command', 'uv'), 'run']
  if !empty(a:ctx.script)
    call extend(command, ['--no-project', '--with-requirements', a:ctx.script])
  elseif filereadable(a:ctx.root . '/pyproject.toml')
    call extend(command, ['--project', a:ctx.root])
  else
    call add(command, '--no-project')
  endif
  for package in s:setting('with', ['euporie', 'ipykernel'])
    call extend(command, ['--with', package])
  endfor
  call extend(command, s:setting('uv_args', []))
  call extend(command, [
        \ 'python', s:plugin_root . '/python/vim_euporie_sidecar.py',
        \ '--state-file', a:ctx.state,
        \ '--root', a:ctx.root,
        \ '--owner-client', a:ctx.client,
        \ '--owner-pid', string(getpid()),
        \ '--idle-timeout', string(s:setting('idle_timeout', 0)),
        \ '--graphics', euporie#graphics_mode(),
        \ '--euporie-args-json', json_encode(s:setting('euporie_args', [])),
        \ ])
  return command
endfunction

function! s:shell_join(parts) abort
  return join(map(copy(a:parts), {_, value -> shellescape(value)}), ' ')
endfunction

function! euporie#start() abort
  if empty($TMUX)
    echoerr 'vim-euporie: start Vim inside tmux'
    return 0
  endif
  if !executable('tmux') || !executable(s:setting('uv_command', 'uv'))
    echoerr 'vim-euporie: tmux and uv must be available on PATH'
    return 0
  endif

  let ctx = s:context()
  if !empty(ctx.script) && (!filereadable(ctx.script) || &modified)
    echoerr 'vim-euporie: save the PEP 723 script before starting its kernel'
    return 0
  endif
  let state = s:read_state(ctx)
  if !empty(state) && s:pane_alive(get(state, 'pane_id', ''))
    let ctx.pane = state.pane_id
    call s:register(ctx)
    return 1
  endif
  if filereadable(ctx.state)
    call delete(ctx.state)
  endif

  if s:setting('configure_tmux', 1)
    call s:tmux(['set-option', '-g', 'allow-passthrough', 'on'])
  endif

  let split = s:setting('split', 'horizontal') ==# 'vertical' ? '-v' : '-h'
  let args = ['split-window', split, '-d', '-P', '-F', '#{pane_id}',
        \ '-p', string(s:setting('pane_percent', 40)), '-c', ctx.root,
        \ s:shell_join(s:uv_command(ctx))]
  let pane = trim(s:tmux(args))
  if v:shell_error != 0 || empty(pane)
    echoerr 'vim-euporie: failed to create tmux pane: ' . pane
    return 0
  endif
  let ctx.pane = pane
  " The sidecar receives this client at launch, before its control socket is
  " ready. This also lets it notice a Vim which exits during startup.
  let ctx.attached = 1
  call s:tmux(['select-pane', '-t', pane, '-T', 'euporie:' . fnamemodify(ctx.root, ':t')])
  call s:ensure_heartbeat()
  return 1
endfunction

function! s:register(ctx) abort
  let reply = s:request(a:ctx, {'action': 'attach', 'client': a:ctx.client}, 1)
  if get(reply, 'ok', 0)
    let a:ctx.attached = 1
    let state = s:read_state(a:ctx)
    let a:ctx.pane = get(state, 'pane_id', a:ctx.pane)
  endif
  return a:ctx.attached
endfunction

function! s:ensure_heartbeat() abort
  if s:heartbeat_timer == -1 && exists('*timer_start')
    let s:heartbeat_timer = timer_start(10000, function('euporie#_heartbeat'), {'repeat': -1})
  endif
endfunction

function! euporie#setup_buffer() abort
  nnoremap <buffer> <silent> <Plug>(EuporieRunCell) :EuporieRunCell<CR>
  inoremap <buffer> <silent> <Plug>(EuporieRunCell) <C-G>u<C-O>:EuporieRunCell<CR>
  nnoremap <buffer> <silent> <Plug>(EuporieSendCell) :EuporieSendCell<CR>
  nnoremap <buffer> <silent> <Plug>(EuporieSendLine) :EuporieSendLine<CR>
  xnoremap <buffer> <silent> <Plug>(EuporieSendVisual) :<C-U>'<,'>EuporieSend<CR>
  nnoremap <buffer> <silent> <Plug>(EuporieInterrupt) :EuporieInterrupt<CR>
  nnoremap <buffer> <silent> <Plug>(EuporieFocus) :EuporieFocus<CR>

  call s:configure_keyboard()

  if !s:setting('no_mappings', 0)
    " VTE terminals such as XFCE Terminal implement neither modifyOtherKeys
    " nor the kitty keyboard protocol, so they send Shift-Enter as a plain
    " Enter which no downstream layer can recover. They do send ESC CR for
    " Alt-Enter, which tmux re-encodes as a modified key, so both keys run
    " the current cell.
    for key in ['<S-CR>', '<M-CR>']
      if empty(maparg(key, 'n'))
        execute 'nmap <buffer> <silent> ' . key . ' <Plug>(EuporieRunCell)'
      endif
      if empty(maparg(key, 'i'))
        execute 'imap <buffer> <silent> ' . key . ' <Plug>(EuporieRunCell)'
      endif
      if empty(maparg(key, 'x'))
        execute 'xmap <buffer> <silent> ' . key . ' <Plug>(EuporieSendVisual)'
      endif
    endfor
    call s:map_legacy_shift_enter()
    if !hasmapto('<Plug>(EuporieRunCell)', 'n')
      nmap <buffer> <localleader>er <Plug>(EuporieRunCell)
    endif
    if !hasmapto('<Plug>(EuporieSendCell)', 'n')
      nmap <buffer> <localleader>ec <Plug>(EuporieSendCell)
    endif
    if !hasmapto('<Plug>(EuporieSendLine)', 'n')
      nmap <buffer> <localleader>el <Plug>(EuporieSendLine)
    endif
    if !hasmapto('<Plug>(EuporieSendVisual)', 'x')
      xmap <buffer> <localleader>e <Plug>(EuporieSendVisual)
    endif
    if !hasmapto('<Plug>(EuporieInterrupt)', 'n')
      nmap <buffer> <localleader>ei <Plug>(EuporieInterrupt)
    endif
    if !hasmapto('<Plug>(EuporieFocus)', 'n')
      nmap <buffer> <localleader>ef <Plug>(EuporieFocus)
    endif
  endif
endfunction

function! euporie#_heartbeat(timer) abort
  for ctx in values(s:contexts)
    if ctx.attached
      call s:request(ctx, {'action': 'heartbeat', 'client': ctx.client}, 1)
    elseif !empty(s:read_state(ctx))
      call s:register(ctx)
    endif
  endfor
endfunction

function! euporie#attach() abort
  if !s:setting('auto_start', 1) || empty($TMUX)
    return
  endif
  let ctx = s:context()
  call s:ensure_heartbeat()
  if empty(s:read_state(ctx))
    call euporie#start()
  else
    call s:register(ctx)
  endif
endfunction

function! s:ready_context() abort
  let ctx = s:context()
  if empty(s:read_state(ctx))
    if !euporie#start()
      return {}
    endif
    if empty(s:wait_for_state(ctx))
      return {}
    endif
  endif
  if !ctx.attached
    call s:register(ctx)
  endif
  return ctx
endfunction

function! euporie#send(code, ...) abort
  if empty(trim(a:code))
    return 0
  endif
  let kind = a:0 ? a:1 : 'code'
  let ctx = s:ready_context()
  if empty(ctx)
    return 0
  endif
  let reply = s:request(ctx, {
        \ 'action': 'execute',
        \ 'client': ctx.client,
        \ 'code': a:code,
        \ 'kind': kind,
        \ })
  if get(reply, 'ok', 0)
    echo 'Sent to Euporie'
    return 1
  endif
  return 0
endfunction

function! euporie#send_range(first, last) abort
  return euporie#send(join(getline(a:first, a:last), "\n"))
endfunction

function! euporie#cell_range(...) abort
  let cursor_line = a:0 ? a:1 : line('.')
  let separator = '^\s*#\s*%%\%($\|\s\)'
  let start = 1
  let marker = ''
  for number in reverse(range(1, cursor_line))
    if getline(number) =~# separator
      let marker = getline(number)
      let start = number + 1
      break
    endif
  endfor
  let end = line('$')
  if cursor_line < line('$')
    for number in range(cursor_line + 1, line('$'))
      if getline(number) =~# separator
        let end = number - 1
        break
      endif
    endfor
  endif
  let kind = marker =~? '\[markdown\]' ? 'markdown' : 'code'
  return {'first': start, 'last': end, 'kind': kind, 'marker': marker}
endfunction

function! s:markdown(lines) abort
  let output = []
  for text in a:lines
    call add(output, substitute(text, '^\s*#\s\?', '', ''))
  endfor
  return join(output, "\n")
endfunction

function! euporie#send_cell(advance) abort
  let cell = euporie#cell_range()
  let lines = cell.last >= cell.first ? getline(cell.first, cell.last) : []
  let code = cell.kind ==# 'markdown' ? s:markdown(lines) : join(lines, "\n")
  let sent = euporie#send(code, cell.kind)
  if sent && a:advance
    let next = cell.last + 1
    if next <= line('$') && getline(next) =~# '^\s*#\s*%%\%($\|\s\)'
      let next += 1
    endif
    call cursor(min([next, line('$')]), 1)
  endif
  return sent
endfunction

function! euporie#interrupt() abort
  let ctx = s:ready_context()
  if !empty(ctx)
    call s:request(ctx, {'action': 'interrupt', 'client': ctx.client})
  endif
endfunction

function! euporie#focus() abort
  let ctx = s:ready_context()
  if empty(ctx)
    return
  endif
  let state = s:read_state(ctx)
  let pane = get(state, 'pane_id', ctx.pane)
  if s:pane_alive(pane)
    call s:tmux(['select-pane', '-t', pane])
  else
    echoerr 'vim-euporie: Euporie pane is no longer running'
  endif
endfunction

function! euporie#stop() abort
  let ctx = s:context()
  if !empty(s:read_state(ctx))
    call s:request(ctx, {'action': 'shutdown', 'client': ctx.client}, 1)
  elseif s:pane_alive(ctx.pane)
    call s:tmux(['kill-pane', '-t', ctx.pane])
  endif
  let ctx.attached = 0
endfunction

function! euporie#restart() abort
  call euporie#stop()
  let ctx = s:context()
  for _ in range(50)
    if !filereadable(ctx.state)
      break
    endif
    sleep 100m
  endfor
  call euporie#start()
endfunction

function! euporie#status() abort
  let ctx = s:context()
  let reply = s:request(ctx, {'action': 'status', 'client': ctx.client}, 1)
  if get(reply, 'ok', 0)
    echo printf('vim-euporie: ready, pane %s, kernel pid %s, %d client(s)',
          \ get(reply, 'pane_id', '?'), get(reply, 'kernel_pid', '?'),
          \ get(reply, 'clients', 0))
  else
    echo 'vim-euporie: stopped for ' . ctx.root
  endif
endfunction

function! euporie#detach_all() abort
  for ctx in values(s:contexts)
    " Always attempt the detach. A context can be registered by the sidecar's
    " launch arguments before Vim has observed the state file.
    call s:request(ctx, {'action': 'detach', 'client': ctx.client}, 1)
    let ctx.attached = 0
  endfor
endfunction
