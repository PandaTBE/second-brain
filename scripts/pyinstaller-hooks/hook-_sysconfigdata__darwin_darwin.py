"""Remove builder paths from Python's compile-time configuration metadata.

PyInstaller anonymizes code filenames, but sysconfig's build_time_vars also
contain compiler paths as string constants. They describe the build machine,
not resources required by this frozen runtime. PostGraphAPI.module is a public
mutable hook interface; no installed Python files are changed.
"""

import re
import types

HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s'\";:]+")
TEMP_PATH = re.compile(r"/(?:private/)?var/folders/[^\s'\";:]+")


def _sanitize_constant(value):
    if isinstance(value, str):
        return TEMP_PATH.sub("/tmp/second-brain-build", HOME_PATH.sub("/Users/build", value))
    if isinstance(value, types.CodeType):
        return sanitize_code(value)
    if isinstance(value, tuple):
        return tuple(_sanitize_constant(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_sanitize_constant(item) for item in value)
    return value


def sanitize_code(code):
    return code.replace(
        co_filename=_sanitize_constant(code.co_filename),
        co_consts=tuple(_sanitize_constant(value) for value in code.co_consts),
    )


def hook(hook_api):
    hook_api.module.code = sanitize_code(hook_api.module.code)
