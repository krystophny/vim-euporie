set nomore
execute 'set runtimepath^=' . fnameescape(fnamemodify(expand('<sfile>'), ':p:h:h'))
runtime plugin/euporie.vim

new
setfiletype python
call setline(1, [
      \ '# %%',
      \ 'x = 1',
      \ 'x + 1',
      \ '# %% [markdown]',
      \ '# A heading',
      \ '#',
      \ '# $x^2$',
      \ '#%%',
      \ 'print(x)',
      \ ])

let cell = euporie#cell_range(2)
call assert_equal(2, cell.first)
call assert_equal(3, cell.last)
call assert_equal('code', cell.kind)

let cell = euporie#cell_range(4)
call assert_equal(5, cell.first)
call assert_equal(7, cell.last)
call assert_equal('markdown', cell.kind)

let cell = euporie#cell_range(9)
call assert_equal(9, cell.first)
call assert_equal(9, cell.last)

call append(0, ['# /// script', '# dependencies = ["matplotlib"]', '# ///'])
call assert_true(euporie#has_inline_metadata())

call assert_equal('<Plug>(EuporieRunCell)', maparg('<S-CR>', 'n'))
call assert_equal('<Plug>(EuporieRunCell)', maparg('<S-CR>', 'i'))
call assert_equal('<Plug>(EuporieSendVisual)', maparg('<S-CR>', 'x'))

" VTE terminals report Shift-Enter as a plain Enter, so Alt-Enter has to run
" cells too.
call assert_equal('<Plug>(EuporieRunCell)', maparg('<M-CR>', 'n'))
call assert_equal('<Plug>(EuporieRunCell)', maparg('<M-CR>', 'i'))
call assert_equal('<Plug>(EuporieSendVisual)', maparg('<M-CR>', 'x'))

" The graphics mode must resolve to a concrete protocol Euporie understands,
" never the "auto" placeholder.
let s:mode = euporie#graphics_mode()
call assert_true(index(['sixel', 'kitty-unicode', 'kitty'], s:mode) >= 0,
      \ 'unexpected graphics mode: ' . s:mode)
call assert_match('EuporieRunCell', maparg('<Plug>(EuporieRunCell)', 'i'))
if v:version < 901
  call assert_equal('<Plug>(EuporieRunCell)', maparg('<t_E1>', 'n'))
  call assert_equal('<Plug>(EuporieRunCell)', maparg('<t_E2>', 'i'))
  call assert_equal('<Plug>(EuporieSendVisual)', maparg('<t_E1>', 'x'))
endif

if len(v:errors)
  call writefile(v:errors, '/dev/stderr')
  cquit
endif
qa!
