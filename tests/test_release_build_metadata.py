"""Frozen compile-time Python metadata must not expose the builder's paths."""

import importlib.util
import types
import weakref
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "sysconfig_build_hook",
    Path(__file__).parents[1] / "scripts/pyinstaller-hooks/hook-_sysconfigdata__darwin_darwin.py",
)
hook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hook)


def test_compile_time_paths_are_removed_without_changing_runtime_flags():
    source = """
build_time_vars = {
    'CFLAGS': '-I/Users/your_user/.venv/include -O3',
    'CONFIG_ARGS': "'--prefix=/Users/your_user/build/python' '--enable-shared'",
    'TEMP': '/private/var/folders/aa/session/T/cpython',
    'MACOSX_DEPLOYMENT_TARGET': '14.0',
    'MULTIARCH': 'darwin',
    'Py_ENABLE_SHARED': 1,
    'CC': 'clang -isysroot /Library/Developer/SDKs/MacOSX.sdk',
}
"""
    original = compile(source, "/Users/your_user/build/_sysconfigdata.py", "exec")
    sanitized = hook.sanitize_code(original)
    namespace = {}
    exec(sanitized, namespace)
    values = namespace["build_time_vars"]
    assert values["CFLAGS"] == "-I/Users/build/.venv/include -O3"
    assert values["CONFIG_ARGS"] == "'--prefix=/Users/build/build/python' '--enable-shared'"
    assert values["TEMP"] == "/tmp/second-brain-build"
    assert values["MACOSX_DEPLOYMENT_TARGET"] == "14.0"
    assert values["MULTIARCH"] == "darwin" and values["Py_ENABLE_SHARED"] == 1
    assert values["CC"] == "clang -isysroot /Library/Developer/SDKs/MacOSX.sdk"
    assert sanitized.co_filename == "/Users/build/build/_sysconfigdata.py"
    assert original.co_filename == "/Users/your_user/build/_sysconfigdata.py"


def test_nested_code_and_container_constants_are_sanitized():
    nested = compile("def data(): return '/home/your_user/python'", "metadata.py", "exec")
    value = nested.replace(co_consts=(
        *nested.co_consts,
        ("/Users/your_user/source", 7),
        frozenset({"/private/var/folders/aa/session/T/metadata"}),
    ))
    sanitized = hook.sanitize_code(value)
    namespace = {}
    exec(sanitized, namespace)
    assert namespace["data"]() == "/Users/build/python"
    assert sanitized.co_consts[-2] == ("/Users/build/source", 7)
    assert sanitized.co_consts[-1] == frozenset({"/tmp/second-brain-build"})


def test_post_graph_updates_only_the_hooked_module():
    code = compile("value = '/Users/your_user/python'", "metadata.py", "exec")
    module = types.SimpleNamespace(code=code)
    api = types.SimpleNamespace(module=module)
    hook.hook(api)
    namespace = {}
    exec(module.code, namespace)
    assert namespace["value"] == "/Users/build/python"
    assert api.module is module and code.co_consts != module.code.co_consts


def test_pyinstaller_invokes_the_sanitizer_through_its_real_hook_loader():
    pytest.importorskip("PyInstaller")
    from PyInstaller.depend.imphook import ModuleHook
    from PyInstaller.lib.modulegraph.modulegraph import ModuleGraph, SourceModule

    name = "_sysconfigdata__darwin_darwin"
    graph = ModuleGraph()
    module = graph.createNode(SourceModule, name, "metadata.py")
    module.code = compile("value = '/Users/your_user/python'", "metadata.py", "exec")
    hook_file = Path(__file__).parents[1] / "scripts/pyinstaller-hooks" / f"hook-{name}.py"
    installed_hook = ModuleHook(weakref.proxy(graph), name, str(hook_file), "__release_test", 0)
    installed_hook.post_graph(types.SimpleNamespace())
    namespace = {}
    exec(module.code, namespace)
    assert namespace["value"] == "/Users/build/python"
