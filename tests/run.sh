#!/bin/sh
set -eu
python3 -m unittest discover -s tests -v
vim --clean -Nu NONE -n -es -S tests/test_cells.vim
