import argparse
import logging
import shutil
from pathlib import Path

import pandas as pd

from generator import CodeBuilder, DocstringLibrary, CodeCopier
from generator import reference as ref
from generator import templates as templ


logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)

logger = logging.getLogger(__name__)

###############################################################################
# ARGUMENT PARSING
###############################################################################


parser = argparse.ArgumentParser(description="Generate py5 library using processing jars",
                                 epilog="this is the epilog")
parser.add_argument('-r', '--repo', action='store', dest='processing_repo_dir',
                    help='location of processing code (github repository)')
parser.add_argument('-p', '--param', action='store', dest='method_parameter_names_data_file',
                    help='location of method parameter names data file created by Py5Doclet')


###############################################################################
# MAIN
###############################################################################


def generate_py5(repo_dir, method_parameter_names_data_file):
    """Generate an installable py5 library using processing jars
    """
    repo_dir = Path(repo_dir)

    logger.info('generating py5 library...')

    logger.info('building classpath')
    core_jars = list(repo_dir.glob('**/core.jar'))
    if len(core_jars) != 1:
        if core_jars:
            msg = f'more than one core.jar found in {repo_dir}'
        else:
            msg = f'core.jar not found in {repo_dir}'
        logger.critical(msg)
        raise RuntimeError(msg)
    core_jar_path = core_jars[0]

    py5_jar_path = Path('py5_jar', 'dist', 'py5.jar')
    if not py5_jar_path.exists():
        msg = f'py5 jar not found at {str(py5_jar_path)}'
        logger.critical(msg)
        raise RuntimeError(msg)

    logger.info('start jnius')
    import jnius_config
    jnius_config.set_classpath(str(py5_jar_path), str(core_jar_path))
    from jnius import autoclass

    logger.info('loading datafile for method parameter names')
    class_method_parameter_names_data = dict()
    with open(method_parameter_names_data_file, 'r') as f:
        for line in f.readlines():
            c, f, types, params, rettype = line.split('|')
            if c not in ['PApplet', 'PShader', 'PShape', 'PFont',
                         'PSurface', 'PGraphics', 'PImage']:
                continue
            if c not in class_method_parameter_names_data: class_method_parameter_names_data[c] = dict()
            if f not in class_method_parameter_names_data[c]: class_method_parameter_names_data[c][f] = dict()
            if types in class_method_parameter_names_data[c][f]: raise RuntimeError(f'assumption violated [{c}] [{f}] [{types}]')
            class_method_parameter_names_data[c][f][types] = (params, rettype)

    logger.info('creating Sketch code')
    py5applet_data = pd.read_csv(Path('py5_resources', 'data', 'py5applet.csv')).fillna('').set_index('processing_name')
    Py5Applet = autoclass('py5.core.Py5Applet', include_protected=False, include_private=False)
    py5applet = Py5Applet()

    py5applet_builder = CodeBuilder(class_method_parameter_names_data['PApplet'], py5applet_data)
    py5applet_builder.code_module_members('Sketch', '_py5sketch')
    py5applet_builder.run_builder(Py5Applet, py5applet)

    # add the methods in the mixin classes as functions in the __init__.py module
    mixin_dir = Path('py5_resources', 'py5_module', 'py5', 'mixins')
    for filename in mixin_dir.glob('*.py'):
        if filename.stem == '__init__':
            continue
        py5applet_builder.code_extra(filename)
    py5applet_builder.code_extra(Path('py5_resources', 'py5_module', 'py5', 'sketch.py'))

    # code the extra pre-run steps so the dynamic variables work right
    run_sketch_pre_run_steps = [
        templ.MODULE_PROPERTY_PRE_RUN_TEMPLATE.format(n) for n in sorted(py5applet_builder.dynamic_variable_names)
    ]

    def run_code_builder(name, clsname, class_name=None):
        logger.info(f'creating {name} code')
        class_name = class_name or clsname.split('.')[-1]
        data = pd.read_csv(Path('py5_resources', 'data', f'{class_name.lower()}.csv')).fillna('').set_index('processing_name')
        Class = autoclass(clsname, include_protected=False, include_private=False)
        instance = Class()

        builder = CodeBuilder(class_method_parameter_names_data[class_name], data)
        builder.run_builder(Class, instance)

        return builder

    py5shader_builder = run_code_builder('Py5Shader', 'processing.opengl.PShader')
    py5shape_builder = run_code_builder('Py5Shape', 'processing.core.PShape')
    py5font_builder = run_code_builder('Py5Font', 'processing.core.PFont')
    py5surface_builder = run_code_builder('Py5Surface', 'py5.core.Py5SurfaceDummy', class_name='PSurface')
    py5graphics_builder = run_code_builder('Py5Graphics', 'py5.core.Py5Graphics', class_name='PGraphics')
    py5image_builder = run_code_builder('Py5Image', 'py5.core.Py5Image', class_name='PImage')

    logger.info('joining code fragments')
    sketch_class_members_code = ''.join(py5applet_builder.class_members)
    sketch_module_members_code = ''.join(py5applet_builder.module_members)
    py5shader_class_members_code = ''.join(py5shader_builder.class_members)
    py5shape_class_members_code = ''.join(py5shape_builder.class_members)
    py5font_class_members_code = ''.join(py5font_builder.class_members)
    py5surface_class_members_code = ''.join(py5surface_builder.class_members)
    py5graphics_class_members_code = ''.join(py5graphics_builder.class_members)
    py5image_class_members_code = ''.join(py5image_builder.class_members)
    run_sketch_pre_run_code = ''.join(run_sketch_pre_run_steps)

    # code the result of the module's __dir__ function and __all__ variable
    py5_dir_names = py5applet_builder.all_names | ref.EXTRA_DIR_NAMES
    py5_dir_str = str(sorted(py5_dir_names, key=lambda x: (x.lower(), x)))
    # don't want import * to import the dynamic variables because they cannot be updated
    py5_all_str = str(sorted(py5_dir_names - py5applet_builder.dynamic_variable_names, key=lambda x: (x.lower(), x)))

    format_params = dict(sketch_class_members_code=sketch_class_members_code,
                         sketch_module_members_code=sketch_module_members_code,
                         py5shader_class_members_code=py5shader_class_members_code,
                         py5shape_class_members_code=py5shape_class_members_code,
                         py5font_class_members_code=py5font_class_members_code,
                         py5surface_class_members_code=py5surface_class_members_code,
                         py5graphics_class_members_code=py5graphics_class_members_code,
                         py5image_class_members_code=py5image_class_members_code,
                         run_sketch_pre_run_code=run_sketch_pre_run_code,
                         py5_dir_str=py5_dir_str,
                         py5_all_str=py5_all_str)
    docstring_library = DocstringLibrary()
    # build complete py5 module in destination directory
    dest_dir = Path('build')
    logger.info(f'building py5 in {dest_dir}')
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    for language in ['en']:  # docstring_library.languages:
        docstrings = docstring_library.docstring_dict(language)
        copier = CodeCopier(format_params, docstrings)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        # TODO: does this work in WSL? if so, wrap in try-catch block for only that exception type
        shutil.copytree(Path('py5_resources', 'py5_module'), dest_dir, copy_function=copier)
        for jar in core_jar_path.parent.glob('*.jar'):
            shutil.copy(jar, dest_dir / 'py5' / 'jars')
        shutil.copy(py5_jar_path, dest_dir / 'py5' / 'jars')

    dest_dir.touch()

    logger.info('py5 build complete!')


def main():
    args = parser.parse_args()
    generate_py5(repo_dir=args.processing_repo_dir,
                 method_parameter_names_data_file=args.method_parameter_names_data_file)


if __name__ == '__main__':
    main()
