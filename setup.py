from setuptools import setup, Extension
import pybind11

ext_modules = [
    Extension(
        'cgo',               # 编译出来的模块名称，我们在 Python 里 import cgo
        ['cpp_src/fast_mcts.cpp'], # 我们的 C++ 源文件
        include_dirs=[pybind11.get_include()], # 告诉编译器去哪里找 pybind11 头文件
        language='c++',
        extra_compile_args=['/O2', '/std:c++17'], # MSVC 编译器的开启最高优化和 C++17 支持
    ),
]

setup(
    name='cgo',
    version='1.0',
    description='C++ fast Go engine and MCTS',
    ext_modules=ext_modules,
)