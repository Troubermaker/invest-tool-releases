"""
Cython 编译脚本（被 build.py 调用，不要直接 python 运行）。

把敏感模块从 .py 编译为 .pyd（机器码），让攻击者反编译 / 解包 PyInstaller
后**拿不到明文**：
    - services/license_service.py 里的 SECRET（HMAC 私钥）
    - api_endpoints/_auth.py 里的所有 Cookie / Token

PyInstaller 之前的步骤：
    venv/Scripts/python.exe setup_cython.py build_ext --inplace

产物：
    services/license_service.cp312-win_amd64.pyd
    api_endpoints/_auth.cp312-win_amd64.pyd

之后 build.py 会临时把对应 .py 改名为 .py.bak，让 PyInstaller 只打包 .pyd。
"""
from setuptools import setup, Extension
from Cython.Build import cythonize


# ============================================
# 要保护的模块清单（按需扩展）
# 注意：写这里的模块若有副作用 / 动态属性 / __getattr__ 等高级用法，
# Cython 编译可能会失败；普通常量/函数模块没问题。
# ============================================
PROTECTED_MODULES = [
    'services/license_service.py',
    'api_endpoints/_auth.py',
]


def _to_extension(py_path):
    """services/license_service.py → Extension('services.license_service', ['services/license_service.py'])"""
    module_name = py_path[:-3].replace('/', '.').replace('\\', '.')
    return Extension(module_name, [py_path])


extensions = [_to_extension(p) for p in PROTECTED_MODULES]

setup(
    # 不影响 pip 安装；这里只是为了驱动 build_ext
    name='invest_tool_protected',
    ext_modules=cythonize(
        extensions,
        language_level=3,
        build_dir='build/cython',           # 中间 .c / .obj 集中放一处
        compiler_directives={
            'language_level':  3,
            'embedsignature':  False,        # 不暴露函数签名（少给反编译者一点信息）
            'always_allow_keywords': True,
        },
    ),
    zip_safe=False,
)
