if exists('g:loaded_vim_euporie') || &compatible
  finish
endif
let g:loaded_vim_euporie = 1

if !has('channel') || !has('job') || !exists('*json_encode')
  echoerr 'vim-euporie requires Vim with +channel, +job, and JSON support'
  finish
endif

command! EuporieStart call euporie#start()
command! EuporieStop call euporie#stop()
command! EuporieRestart call euporie#restart()
command! EuporieFocus call euporie#focus()
command! EuporieStatus call euporie#status()
command! EuporieDoctor call euporie#doctor()
command! EuporieInterrupt call euporie#interrupt()
command! -range EuporieSend call euporie#send_range(<line1>, <line2>)
command! EuporieSendLine call euporie#send_range(line('.'), line('.'))
command! EuporieSendCell call euporie#send_cell(0)
command! EuporieRunCell call euporie#send_cell(1)
command! EuporieSendFile call euporie#send_range(1, line('$'))

augroup vim_euporie
  autocmd!
  autocmd FileType python call euporie#setup_buffer() | call euporie#attach()
  autocmd VimLeavePre * call euporie#detach_all()
augroup END
