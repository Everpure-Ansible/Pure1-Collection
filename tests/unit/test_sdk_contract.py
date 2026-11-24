# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Static contract test for the Pure1 client calls made by the plugins.

Every call on the Pure1 client under plugins/ is checked against the def
signatures of the newest Pure1_x_y client in the installed py-pure-client.
Both sides are read from disk with ast: nothing is imported or called.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ast
import importlib.machinery
import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The plugins hold the client under this name, or call it straight off a factory.
CLIENT_NAMES = ("pure_1",)
CLIENT_FACTORIES = ("get_pure1", "Client")


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# pure1.Client() takes its default version in string order, so once a Pure1
# 1.10 exists the plugins get the 1.9 client while this picks 1.10.
def newest_pure1_package(names):
    """Return the Pure1_x_y name with the highest version, compared as numbers."""
    versions = {}
    for name in names:
        match = re.match(r"^Pure1_(\d+)_(\d+)$", name)
        if match:
            versions[(int(match.group(1)), int(match.group(2)))] = name
    return versions[max(versions)] if versions else None


def find_sdk_client():
    """Return the path of the newest Pure1 client.py in py-pure-client, or None.

    PathFinder looks at sys.path only: the SDK is found without importing it.
    """
    spec = importlib.machinery.PathFinder.find_spec("pypureclient")
    for location in getattr(spec, "submodule_search_locations", None) or []:
        pure1_dir = os.path.join(location, "pure1")
        if os.path.isdir(pure1_dir):
            newest = newest_pure1_package(
                name
                for name in os.listdir(pure1_dir)
                if os.path.isfile(os.path.join(pure1_dir, name, "client.py"))
            )
            if newest:
                return os.path.join(pure1_dir, newest, "client.py")
    return None


def client_signatures(source):
    """Return {method name: signature} from the defs of class Client in source."""
    signatures = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == "Client":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    signatures[item.name] = _signature(item.args)
    return signatures


def _signature(args):
    positional = [arg.arg for arg in getattr(args, "posonlyargs", []) + args.args]
    keyword_only = [arg.arg for arg in args.kwonlyargs]
    # Defaults belong to the last positional parameters; the first one is self.
    first_default = len(positional) - len(args.defaults)
    required = positional[1:first_default]
    required += [
        name for name, default in zip(keyword_only, args.kw_defaults) if default is None
    ]
    return {
        "positional": positional[1:],
        "names": set(positional[1:] + keyword_only),
        "required": required,
        "var_keyword": args.kwarg is not None,
    }


def _name(node):
    """Return the last name of a dotted expression, c for a.b.c, or None."""
    return getattr(node, "attr", getattr(node, "id", None))


def _is_client(node):
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "Client":
            # pure1.Client(...), not the Client of another library
            return _name(func.value) == "pure1"
        return _name(func) in CLIENT_FACTORIES
    # pure_1, or the client kept on an object as in self.pure_1
    return _name(node) in CLIENT_NAMES


def _method_calls(source):
    """Return every ast.Call made on an attribute, in source order."""
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    return sorted(calls, key=lambda call: (call.lineno, call.col_offset))


def client_calls(source):
    """Return the ast.Call nodes made on the Pure1 client, in source order."""
    return [call for call in _method_calls(source) if _is_client(call.func.value)]


def aliased_calls(source, signatures):
    """Return the calls of a public SDK method made on anything but the client.

    That is how a client held under a name the collector does not know shows.
    """
    return [
        call
        for call in _method_calls(source)
        if not _is_client(call.func.value)
        and call.func.attr in signatures
        and not call.func.attr.startswith("_")
    ]


def check_call(call, signatures):
    """Return what stops a client call binding to its SDK method, [] if nothing."""
    signature = signatures.get(call.func.attr)
    if signature is None:
        return ["no such method"]
    keywords = [keyword.arg for keyword in call.keywords if keyword.arg is not None]
    problems = []
    if not signature["var_keyword"]:
        problems = [
            "unknown keyword {0}".format(name)
            for name in keywords
            if name not in signature["names"]
        ]
    # A call that unpacks *args or **kwargs hides what it passes, so only its
    # method name and its literal keywords can be checked.
    unpacked = len(keywords) < len(call.keywords) or any(
        isinstance(arg, ast.Starred) for arg in call.args
    )
    if not unpacked:
        given = set(signature["positional"][: len(call.args)]) | set(keywords)
        problems += [
            "missing required parameter {0}".format(name)
            for name in signature["required"]
            if name not in given
        ]
    return problems


def plugin_sources():
    """Return (path relative to the repository, source) for every plugin file."""
    sources = []
    for root, dirs, files in os.walk(os.path.join(REPO_ROOT, "plugins")):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(root, name)
                relative = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
                sources.append((relative, _read(path)))
    return sources


SDK_INSTALLED = importlib.machinery.PathFinder.find_spec("pypureclient") is not None
SDK_CLIENT = find_sdk_client()
SDK_SIGNATURES = client_signatures(_read(SDK_CLIENT)) if SDK_CLIENT else {}
CALL_SITES = [
    ("{0}:{1}".format(path, call.lineno), call)
    for path, source in plugin_sources()
    for call in client_calls(source)
]
NEEDS_SDK = pytest.mark.skipif(
    not SDK_INSTALLED,
    reason="py-pure-client is not installed: no Pure1 client to check against",
)


@NEEDS_SDK
def test_sdk_client_is_found():
    """An installed SDK that cannot be read fails the run instead of skipping."""
    assert SDK_CLIENT, "the installed py-pure-client has no pure1/Pure1_x_y/client.py"
    assert SDK_SIGNATURES, "no method of class Client read from {0}".format(SDK_CLIENT)


@NEEDS_SDK
@pytest.mark.parametrize(
    "where, call", CALL_SITES, ids=[where for where, call in CALL_SITES]
)
def test_client_call_binds_to_sdk(where, call):
    problems = check_call(call, SDK_SIGNATURES)
    if problems:
        pytest.fail(
            "{0}: {1}: {2}".format(where, call.func.attr, "; ".join(problems)),
            pytrace=False,
        )


def test_every_client_user_is_checked():
    """A plugin that gets the client but shows no call on it escapes the check."""
    checked = {where.rsplit(":", 1)[0] for where, call in CALL_SITES}
    assert checked, "no Pure1 client call found under plugins/"
    unchecked = [
        path
        for path, source in plugin_sources()
        if re.search(r"(?<!def )get_pure1\(", source) and path not in checked
    ]
    assert not unchecked, (
        "these plugins get the Pure1 client but no call on it was found; hold the "
        "client as pure_1 or add its name to CLIENT_NAMES: {0}".format(
            ", ".join(unchecked)
        )
    )


@NEEDS_SDK
def test_client_is_not_held_under_another_name():
    """A call on a client held under another name escapes the check."""
    aliased = [
        "{0}:{1}: {2}".format(path, call.lineno, call.func.attr)
        for path, source in plugin_sources()
        for call in aliased_calls(source, SDK_SIGNATURES)
    ]
    assert not aliased, (
        "these calls of a Pure1 client method are not checked; hold the client as "
        "pure_1 or add its name to CLIENT_NAMES: {0}".format(", ".join(aliased))
    )


SDK_SNIPPET = """
class Client(object):
    def __init__(self, app_id=None):
        pass

    def get_things(self, names=None, limit=None):
        pass

    def put_things(self, thing, names=None):
        pass

    def post_things(self, *, thing, names=None):
        pass

    def patch_things(self, thing, other, **kwargs):
        pass
"""


@pytest.mark.parametrize(
    "snippet, expected",
    [
        pytest.param("pure_1.get_things(names=['a'], limit=1)", [[]], id="good call"),
        pytest.param("pure_1.put_things(thing)", [[]], id="positional argument"),
        pytest.param("pure_1.get_thing()", [["no such method"]], id="missing method"),
        pytest.param(
            "pure_1.get_things(name='a')",
            [["unknown keyword name"]],
            id="unknown keyword",
        ),
        pytest.param(
            "pure_1.put_things(names=['a'])",
            [["missing required parameter thing"]],
            id="missing required parameter",
        ),
        pytest.param(
            "pure_1.put_things(names=['a'], tag=kvp)",
            [["unknown keyword tag", "missing required parameter thing"]],
            id="two problems at one call",
        ),
        pytest.param("pure_1.put_things(**kwargs)", [[]], id="kwargs call"),
        pytest.param(
            "pure_1.put_things(name='a', **kwargs)",
            [["unknown keyword name"]],
            id="kwargs call with a bad literal keyword",
        ),
        pytest.param(
            "pure_1.put_thing(**kwargs)",
            [["no such method"]],
            id="kwargs call on a missing method",
        ),
        pytest.param("pure_1.patch_things(*args)", [[]], id="args call"),
        pytest.param(
            "pure_1.post_things(names=['a'])",
            [["missing required parameter thing"]],
            id="missing keyword-only parameter",
        ),
        pytest.param(
            "pure_1.patch_things(thing, other, anything=1)",
            [[]],
            id="method that takes kwargs",
        ),
        pytest.param(
            "things = list(pure_1.get_things(name='a').items)",
            [["unknown keyword name"]],
            id="call nested in an expression",
        ),
        pytest.param(
            "get_pure1(module).get_thing()",
            [["no such method"]],
            id="call chained on the factory",
        ),
        pytest.param(
            "pure1.Client(app_id=a).get_things()\nparamiko.Client(host).connect()",
            [[]],
            id="only the Pure1 Client is a factory",
        ),
        pytest.param(
            "self.pure_1.get_thing()",
            [["no such method"]],
            id="client kept on an object",
        ),
        pytest.param(
            "pure_1.get_things()\npure_1.get_thing()\npure_1.put_things()",
            [[], ["no such method"], ["missing required parameter thing"]],
            id="one result per call",
        ),
        pytest.param(
            "module.exit_json(changed=True)\npure_1._api_client.close()",
            [],
            id="not a client call",
        ),
    ],
)
def test_checker(snippet, expected):
    signatures = client_signatures(SDK_SNIPPET)
    found = [check_call(call, signatures) for call in client_calls(snippet)]
    assert found == expected


@pytest.mark.parametrize(
    "snippet, expected",
    [
        pytest.param(
            "client.get_things(names=['a'])\nself.api.put_things(thing)",
            ["get_things", "put_things"],
            id="client under another name",
        ),
        pytest.param(
            "self.pure_1.get_things()\nget_pure1(module).get_things()",
            [],
            id="recognised client",
        ),
        pytest.param(
            "module.exit_json()\nsuper(Thing, self).__init__()",
            [],
            id="not a public SDK method",
        ),
    ],
)
def test_aliased_calls(snippet, expected):
    signatures = client_signatures(SDK_SNIPPET)
    found = [call.func.attr for call in aliased_calls(snippet, signatures)]
    assert found == expected


def test_newest_pure1_package_is_picked_numerically():
    names = ["Pure1_1_9", "Pure1_1_10", "Pure1_1_2", "models", "client.py"]
    assert newest_pure1_package(names) == "Pure1_1_10"
