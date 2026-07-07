import os
from glob import glob

from setuptools import setup
from Cython.Build import cythonize
from Cython.Distutils import Extension

extra_compile_args = ['-O3']

lib_modules = []

lib_modules.append(
    Extension('dezip',
              ['dezip.pyx'],
              language='c',
              extra_compile_args=extra_compile_args,),
)

setup(
    name='dezip',
    zip_safe=False,
    ext_modules=cythonize(lib_modules, language_level=3, compiler_directives={'always_allow_keywords': True}),
)