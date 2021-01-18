from pathlib import Path

import pandas as pd


NEW_TEMPLATE = """@@ meta
name = {0}
type = {1}
{2}
@@ description
The documentation for this field or method has not yet been written. If you know what it does, please help out with a pull request to the relevant file in https://github.com/hx2A/py5generator/tree/master/py5_docs/Reference/api_en/.

"""

PY5_API_EN = Path('py5_docs/Reference/api_en/')

PY5_CLASS_LOOKUP = {
    'PApplet': 'Sketch',
    'PFont': 'Py5Font',
    'PGraphics': 'Py5Graphics',
    'PImage': 'Py5Image',
    'PShader': 'Py5Shader',
    'PShape': 'Py5Shape',
    'PSurface': 'Py5Surface',
}

# read the class datafiles so I know what methods and fields are relevant
class_data_info = dict()
class_resource_data = Path('py5_resources', 'data')
papplet_category_data = None
for pclass in PY5_CLASS_LOOKUP.keys():
    filename = 'py5applet.csv' if pclass == 'PApplet' else pclass.lower() + '.csv'
    class_data = pd.read_csv(class_resource_data / filename)
    class_data = class_data.fillna('').set_index('processing_name')
    class_data_info[pclass] = class_data.query("available_in_py5==True")
    if pclass == 'PApplet':
        papplet_category_data = class_data_info[pclass].set_index('py5_name')[['category', 'subcategory']]

# go through the class data info and for each relevant method and field and 
# identify the new files that must be created
new_xml_files = []
for pclass, class_data in class_data_info.items():
    for processing_name, data in class_data.iterrows():
        py5_name = data['py5_name']
        item_type = data['type']
        if item_type in ['static field', 'unknown']:
            continue

        new_docfile = PY5_API_EN / f'{PY5_CLASS_LOOKUP[pclass]}_{py5_name}.txt'
        if new_docfile.exists():
            continue

        # check if usable alternate docfiles exist
        if processing_name and pclass in ['PApplet', 'PGraphics', 'PImage']:
            alt_docfiles = [PY5_API_EN / f'{x}_{py5_name}.txt' for x in ['Sketch', 'Py5Graphics', 'Py5Image']]
            if any(f.exists() for f in alt_docfiles):
                continue

        # new documentation that I must write. skip pgraphics so I don't duplicate work
        if pclass not in ['PGraphics', 'PImage']:
            new_xml_files.append((pclass, py5_name, item_type, processing_name, new_docfile))

for num, new_file_data in enumerate(new_xml_files):
    pclass, py5_name, item_type, processing_name, new_docfile = new_file_data
    if item_type == 'dynamic variable':
        doc_type = 'field'
        name = py5_name
    elif item_type == 'class':
        doc_type = 'class'
        name = py5_name
    else:
        doc_type = 'method'
        name = py5_name + '()'

    print(f"creating {new_docfile}")
    with open(new_docfile, 'w') as f:
        extra = f'pclass = {pclass}\nprocessing_name = {processing_name}\n' if processing_name else ''
        if pclass == 'PApplet' and py5_name in papplet_category_data.index:
            category = papplet_category_data.loc[py5_name, 'category'] or 'None'
            subcategory = papplet_category_data.loc[py5_name, 'subcategory'] or 'None'
            extra += f'category = {category}\nsubcategory = {subcategory}\n'
        f.write(NEW_TEMPLATE.format(name, doc_type, extra))

print(f'created {len(new_xml_files)} new files.')