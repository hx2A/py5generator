# *****************************************************************************
#
#   Part of the py5 library
#   Copyright (C) 2020-2021 Jim Schmitz
#
#   This library is free software: you can redistribute it and/or modify it
#   under the terms of the GNU Lesser General Public License as published by
#   the Free Software Foundation, either version 2.1 of the License, or (at
#   your option) any later version.
#
#   This library is distributed in the hope that it will be useful, but
#   WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser
#   General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public License
#   along with this library. If not, see <https://www.gnu.org/licenses/>.
#
# *****************************************************************************
import sys
import ast
import re
from pathlib import Path
import tempfile

from IPython.display import display
from IPython.core.magic import Magics, magics_class, cell_magic
from IPython.core.magic_arguments import parse_argstring, argument, magic_arguments, kwds

import stackprinter

from .. import parsing
from .. import split_setup
from ..magics.util import CellMagicHelpFormatter, filename_check, variable_name_check


PY5BOT_CODE_STARTUP = """
import py5_tools
py5_tools.set_imported_mode(True)
from py5 import *

import py5_tools.parsing as _PY5BOT_parsing
import ast as _PY5BOT_ast


_PY5_VALIDATE_RENDERER = \"\"\"
def _PY5BOT_size_validate_renderer(f):
    import sys
    import functools

    @functools.wraps(f)
    def validate_renderer(*args):
        if len(args) == 2:
            args = *args, HIDDEN
        elif len(args) >= 3 and isinstance(renderer := args[2], str):
            renderers = [HIDDEN, JAVA2D] if sys.platform == 'darwin' else [HIDDEN, JAVA2D, P2D, P3D]
            if renderer not in renderers:
                renderer_name = {SVG: 'SVG', PDF: 'PDF', DXF: 'DXF', P2D: 'P2D', P3D: 'P3D'}.get(renderer, renderer)
                print(f'Sorry, py5bot does not support the {renderer_name} renderer' + (' on OSX.' if sys.platform == 'darwin' else '.'), file=sys.stderr)
                args = *args[:2], HIDDEN, *args[3:]
            if sys.platform == 'darwin':
                args = *args[:2], HIDDEN, *args[3:]
        f(*args)
    return validate_renderer
\"\"\"

exec(compile(_PY5_VALIDATE_RENDERER, filename='<py5bot>', mode='exec'), globals(), locals())
_PY5BOT_altered_size = _PY5BOT_size_validate_renderer(size)

del _PY5_VALIDATE_RENDERER
del _PY5BOT_size_validate_renderer
"""


PY5BOT_CODE = """
_PY5BOT_OUTPUT_ = None

def _py5bot_settings():
    size = _PY5BOT_altered_size

    with open('{0}', 'r') as f:
        exec(
            compile(
                _PY5BOT_parsing.transform_py5_code(
                    _PY5BOT_ast.parse(f.read(), filename='{0}', mode='exec'),
                ),
                filename='{0}',
                mode='exec'
            )
        )


def _py5bot_setup():
    global _PY5BOT_OUTPUT_

    with open('{1}', 'r') as f:
        exec(
            compile(
                _PY5BOT_parsing.transform_py5_code(
                    _PY5BOT_ast.parse(f.read(), filename='{1}', mode='exec'),
                ),
                filename='{1}',
                mode='exec'
            )
        )

    from PIL import Image
    load_np_pixels()
    _PY5BOT_OUTPUT_ = Image.fromarray(np_pixels()[:, :, 1:])

    exit_sketch()


run_sketch(sketch_functions=dict(settings=_py5bot_settings, setup=_py5bot_setup), block=True)
if is_dead_from_error:
    exit_sketch()

_PY5BOT_OUTPUT_
"""


def check_for_problems(code, filename):
    # does the code parse? if not, return an error message
    try:
        sketch_ast = ast.parse(code, filename=filename, mode='exec')
    except Exception as e:
        msg = stackprinter.format(e)
        m = re.search(r'^SyntaxError:', msg, flags=re.MULTILINE)
        if m:
            msg = msg[m.start(0):]
        msg = 'There is a problem with your code:\n' + msg
        return False, msg

    # check for assignments to or deletions of reserved words
    problems = parsing.check_reserved_words(code, sketch_ast)
    if problems:
        msg = 'There ' + ('is a problem' if len(problems) == 1 else f'are {len(problems)} problems') + ' with your code.\n'
        msg += '=' * len(msg) + '\n' + '\n'.join(problems)
        return False, msg

    cutoff = split_setup.find_cutoff(code, 'imported')
    py5bot_settings = '\n'.join(code.splitlines()[:cutoff])
    py5bot_setup = '\n'.join(code.splitlines()[cutoff:])

    # check for calls to size, etc, that were not at the beginning of the code
    problems = split_setup.check_for_special_functions(py5bot_setup, 'imported')
    if problems:
        msg = 'There ' + ('is a problem' if len(problems) == 1 else f'are {len(problems)} problems') + ' with your code.\n'
        msg += 'The function ' + ('call' if len(problems) == 1 else 'calls') + ' to '
        problems = [f'{name} (on line {i + 1})' for i, name in problems]
        if len(problems) == 1:
            msg += problems[0]
        elif len(problems) == 2:
            msg += f'{problems[0]} and {problems[1]}'
        else:
            msg += ', and '.join(', '.join(problems).rsplit(', ', maxsplit=1))
        msg += ' must be moved to the beginning of your code, before any other code.'
        return False, msg

    return True, (py5bot_settings, py5bot_setup)


class Py5BotManager:

    def __init__(self):
        self.tempdir = Path(tempfile.TemporaryDirectory().name)
        self.tempdir.mkdir(parents=True, exist_ok=True)
        self.settings_filename = self.tempdir / '_PY5_STATIC_SETTINGS_CODE_.py'
        self.setup_filename = self.tempdir / '_PY5_STATIC_SETUP_CODE_.py'
        self.startup_code = PY5BOT_CODE_STARTUP
        self.run_code = PY5BOT_CODE.format(self.settings_filename, self.setup_filename)

    def write_code(self, settings_code, setup_code, orig_line_count):
        with open(self.settings_filename, 'w') as f:
            f.write(settings_code)

        with open(self.setup_filename, 'w') as f:
            f.write('\n' * (orig_line_count - len(setup_code.splitlines())))
            f.write(setup_code)


@magics_class
class Py5BotMagics(Magics):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._py5bot_mgr = Py5BotManager()

    @magic_arguments()
    @argument(""" DELETE
    $arguments_Py5Magics_py5bot_arguments
    """)  # DELETE
    @kwds(formatter_class=CellMagicHelpFormatter)
    @cell_magic
    def py5bot(self, line, cell):
        """$class_Py5Magics_py5bot"""
        args = parse_argstring(self.py5bot, line)

        success, result = check_for_problems(cell, "<py5bot>")
        if success:
            py5bot_settings, py5bot_setup = result
            if split_setup.count_noncomment_lines(py5bot_settings) == 0:
                py5bot_settings = 'size(100, 100, HIDDEN)'
            self._py5bot_mgr.write_code(py5bot_settings, py5bot_setup, len(cell.splitlines()))

            ns = self.shell.user_ns
            exec(self._py5bot_mgr.startup_code + self._py5bot_mgr.run_code, ns)
            png = ns['_PY5BOT_OUTPUT_']

            if args.filename:
                filename = filename_check(args.filename)
                png.save(filename)
                print(f'PNG file written to {filename}')
            if args.variable:
                if variable_name_check(args.variable):
                    self.shell.user_ns[args.variable] = png
                    print(f'PIL Image assigned to {args.variable}')
                else:
                    print(f'Invalid variable name {args.variable}', file=sys.stderr)

            display(png)
            del ns['_PY5BOT_OUTPUT_']
        else:
            print(result, file=sys.stderr)
