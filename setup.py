from setuptools import find_packages
from setuptools import setup

setup(
    name='reorder_python_imports',
    description='Tool for reordering python imports',
    url='https://github.com/asottile/reorder_python_imports',
    version='0.0.0',
    author='Anthony Sottile',
    author_email='asottile@umich.edu',
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: Implementation :: CPython',
    ],
    packages=find_packages('.', exclude=('tests*', 'testing*')),
    install_requires=[
        'argparse',
        'aspy.refactor_imports>=0.2.3',
        'cached-property',
        'six',
    ],
    entry_points={
        'console_scripts': [
            'reorder-python-imports = reorder_python_imports.main:main',
        ],
    },
)
