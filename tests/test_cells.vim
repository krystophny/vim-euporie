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

if len(v:errors)
  call writefile(v:errors, '/dev/stderr')
  cquit
endif
qa!
