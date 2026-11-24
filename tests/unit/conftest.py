# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Import set-up and shared building blocks of the unit tests.

The code under test runs on the real ansible-core and the real py-pure-client.
Only the connection is replaced: pure1.Client() gives a stand-in that has the
methods and signatures of the SDK client. The test builds what it answers from
the SDK response and model classes, and an answer the test did not give fails
the test when the code reads it. The helpers are plain functions, to be
imported from tests.unit.conftest; what changes state for one test is a
fixture.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import ast
import importlib
import os
import pkgutil
import re
import sys
import time
import types
from unittest.mock import create_autospec, patch

import pytest

try:
    from ansible.module_utils.basic import AnsibleModule
    from ansible.module_utils.common.arg_spec import ArgumentSpecValidator
    from ansible.module_utils.errors import UnsupportedError
    from pypureclient import ApiError, ErrorResponse, ValidResponse
    from pypureclient._transport.api_response import ApiResponse
    from pypureclient.responses import ItemIterator

    # The module itself: the name pure1 in pypureclient starts as a lazy loader
    SDK = importlib.import_module("pypureclient.pure1")
except ImportError as error:
    raise ImportError(
        "the unit tests need ansible-core and py-pure-client: "
        "python -m pip install -r tests/requirements.txt ({0})".format(error)
    ) from error

MODULE_UTILS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "plugins",
    "module_utils",
)


def _imported_names(path, alias, names):
    """Return the names of the modules a file imports under the alias package."""
    with open(path, encoding="utf-8") as source:
        tree = ast.parse(source.read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.append(node.module or "")
            found += ["{0}.{1}".format(node.module, name.name) for name in node.names]
        elif isinstance(node, ast.Import):
            found += [name.name for name in node.names]
    return [name for name in names if "{0}.{1}".format(alias, name) in found]


def alias_module_utils(directory, package, alias):
    """Give package and every module in its directory a second name under alias.

    A module that imports another one of the directory by its alias gets that
    one first, so that the import finds the module the tests import whatever
    the order of the files. Only the files get the name: a package in the
    directory gets none. Returns the names of the modules.
    """
    names = sorted(
        info.name for info in pkgutil.iter_modules([directory]) if not info.ispkg
    )
    started = set()
    # for "from <alias> import <name>"
    sys.modules[alias] = importlib.import_module(package)

    def load(name):
        if name in started:
            return
        started.add(name)
        path = os.path.join(directory, name + ".py")
        for other in _imported_names(path, alias, names):
            load(other)
        module = importlib.import_module("{0}.{1}".format(package, name))
        sys.modules["{0}.{1}".format(alias, name)] = module

    for name in names:
        load(name)
    return names


# The plugins import module_utils under its collection name. Nothing provides
# that name without pytest-ansible, and with it the import would load a second
# copy of the file. Both ways they get the module the tests import, for every
# file of plugins/module_utils.
alias_module_utils(
    MODULE_UTILS,
    "plugins.module_utils",
    "ansible_collections.everpure.pure1.plugins.module_utils",
)


def _newest_pure1_package():
    """Import the Pure1_x_y package of the SDK with the highest version.

    Versions are compared as numbers, as tests/unit/test_sdk_contract.py does.
    """
    versions = {}
    for info in pkgutil.iter_modules(SDK.__path__):
        match = re.match(r"Pure1_(\d+)_(\d+)$", info.name)
        if match:
            versions[(int(match.group(1)), int(match.group(2)))] = info.name
    return importlib.import_module(
        "{0}.{1}".format(SDK.__name__, versions[max(versions)])
    )


NEWEST = _newest_pure1_package()
MODELS = importlib.import_module(NEWEST.__name__ + ".models")
CREDENTIALS = {
    "app_id": "pure1-app-id",
    "key_file": "/run/secrets/pure1.pem",
    "password": None,
    # not a credential: the default of the other option every module takes
    "validate_certs": True,
}
# The rules main() can give AnsibleModule beside the argument spec
_SPEC_RULES = (
    "mutually_exclusive",
    "required_together",
    "required_one_of",
    "required_if",
    "required_by",
)


class _NoAnswer:
    """What a stand-in method returns until the test gives it an answer.

    The code may drop it, as it drops the result of module.warn(). Reading an
    attribute of it, or iterating it, fails the test.
    """

    def __init__(self, method):
        self._method = method

    def _fail(self, read):
        pytest.fail(
            "{0}() was called and the code read {1} of its answer: give {0} "
            "a return_value or a side_effect".format(self._method, read)
        )

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self._fail("." + name)

    def __iter__(self):
        self._fail("the entries")


class _StandIn:
    """Stand-in for an instance of a class of ansible-core or of the SDK.

    A method becomes a mock with the signature of the real one when it is
    first used, and a name that is not a method of the class raises
    AttributeError. create_autospec(cls, instance=True) gives the same but
    mocks every method at once, which takes 50 ms for the Pure1 client.
    """

    def __init__(self, cls):
        self._cls = cls

    def __getattr__(self, name):
        function = getattr(self._cls, name, None)
        if not isinstance(function, types.FunctionType):
            raise AttributeError(
                "{0} is not a method of {1}; if the real object has it as an "
                "attribute, set it on the stand-in".format(name, self._cls.__name__)
            )
        where = "{0}.{1}".format(self._cls.__name__, name)
        method = create_autospec(types.MethodType(function, self))
        if not isinstance(method, types.FunctionType):
            # create_autospec could not read the signature and checks nothing
            pytest.fail(
                "{0} was first used while inspect.signature is patched: give "
                "it its answer before the patch".format(where)
            )
        method.return_value = _NoAnswer(where)
        setattr(self, name, method)
        return method


def ansible_module(check_mode=False, **params):
    """Return an AnsibleModule stand-in with these params and credentials.

    exit_json and fail_json end the call with SystemExit, as the real ones do.
    """
    module = _StandIn(AnsibleModule)
    module.params = dict(CREDENTIALS, **params)
    module.check_mode = check_mode
    # the values AnsibleModule hides in what the task returns
    module.no_log_values = set()
    module.exit_json.side_effect = SystemExit(0)
    module.fail_json.side_effect = SystemExit(1)
    return module


def run_main(plugin, check_mode=False, **params):
    """Run main() of a module up to its exit_json or fail_json.

    The params go through the argument spec main() declares, as AnsibleModule
    does it: defaults and fallbacks are filled in, the values of the no_log
    params are collected in no_log_values, a missing, unknown or refused
    parameter ends in fail_json, and check mode that main() does not declare
    ends in a skipped exit_json. Returns the AnsibleModule stand-in that
    main() was given.
    """
    module = ansible_module(check_mode=check_mode, **params)

    def construct(argument_spec, *, supports_check_mode=False, **options):
        rules = {name: options.get(name) for name in _SPEC_RULES}
        result = ArgumentSpecValidator(argument_spec, **rules).validate(module.params)
        module.params = result.validated_parameters
        # AnsibleModule collects the same for a no_log param that holds a
        # string; the validation result has no public attribute for them
        module.no_log_values.update(
            value
            for name, value in module.params.items()
            if value and argument_spec.get(name, {}).get("no_log")
        )
        if result.error_messages:
            message = result.error_messages[0]
            if isinstance(result.errors[0], UnsupportedError):
                message = "Unsupported parameters for module: " + message
            module.fail_json(msg=message)
        if check_mode and not supports_check_mode:
            module.exit_json(skipped=True, msg="module does not support check mode")
        return module

    constructor = create_autospec(
        types.MethodType(AnsibleModule.__init__, module), side_effect=construct
    )
    with patch.object(plugin, "AnsibleModule", constructor):
        with pytest.raises(SystemExit):
            plugin.main()
    if module.fail_json.called:
        # pytest shows this when the test fails
        print("fail_json: {0}".format(module.fail_json.call_args[1]))
    return module


def _holds_dict(value):
    return isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(entry, dict) for entry in value)
    )


def _model_classes(value):
    """Return the class names of the SDK models in the value of a field."""
    entries = value if isinstance(value, list) else [value]
    return [type(entry).__name__ for entry in entries if hasattr(entry, "__fields__")]


def sdk_model(class_name, **fields):
    """Return an SDK model with these fields set; build a nested model first.

    The SDK models drop a keyword they do not know, also in a dict they are
    given for a nested model, and rebuild a nested model of another class as
    the class of the field. All three are refused here, so that a test object
    is what the test wrote and carries no field that Pure1 data does not have.
    """
    cls = getattr(MODELS, class_name)
    if cls is None:
        raise TypeError("the SDK has no model {0}".format(class_name))
    unknown = sorted(set(fields) - set(cls.__fields__))
    if unknown:
        raise TypeError(
            "{0} has no field {1}; its fields: {2}".format(
                class_name, ", ".join(unknown), ", ".join(cls.__fields__)
            )
        )
    item = cls(**fields)
    for name, value in fields.items():
        kept = vars(item)[name]
        if _holds_dict(value) and not _holds_dict(kept):
            raise TypeError(
                "{0}.{1}: build the nested model with sdk_model(), not from "
                "a dict".format(class_name, name)
            )
        if _model_classes(value) != _model_classes(kept):
            raise TypeError(
                "{0}.{1} is {2} in the SDK, not {3}".format(
                    class_name,
                    name,
                    " and ".join(sorted(set(_model_classes(kept)))),
                    " and ".join(sorted(set(_model_classes(value)))),
                )
            )
    return item


def array_reference(name, **fields):
    """Return a reference to an array, as the arrays of a pod or a volume hold.

    The arrays of a Drive are of another class, DriveArrayStatus.
    """
    return sdk_model("FixedReferenceFqdn", name=name, **fields)


def valid_response(items=(), total_item_count=None):
    """Return the SDK answer to a call that worked: status 200, one page of items."""
    items = list(items)
    if total_item_count is None:
        total_item_count = len(items)
    page = ItemIterator(
        None, {}, None, total_item_count, items, None, more_items_remaining=False
    )
    return ValidResponse(200, None, total_item_count, page, {})


def paged_answers(response_class, items, page_size=1000, total_item_count=-1):
    """Return a side_effect that answers a listing call page by page, as Pure1 does.

    response_class is the SDK model of the answer ("HardwareGetResponse") and
    items everything Pure1 has for the call. Each request gets the items from
    its offset on, at most its limit and never more than page_size of them
    (Pure1: 1000), with the total of all items (total_item_count: another
    number, or None for an answer without one). A request without sort gets a
    continuation token while items remain after its page; one with sort gets
    none, as from Pure1. The answer is built by the SDK's own client code, so
    its items are the SDK iterator, which asks for the page of a token itself.
    Every request, also those of the iterator, is kept as its keywords in the
    attribute requests of the side_effect. Code that asks for page after page
    without an end fails the test.

    The SDK would rebuild an item of another class as the class the answer
    holds and drop fields, and has None for a response class it does not
    know. Both are refused here, as sdk_model does. The items of an answer
    are copies of the items given: equal, not the same objects.
    """
    items = list(items)
    model = getattr(MODELS, response_class, None)
    if model is None or "items" not in getattr(model, "__fields__", {}):
        raise TypeError("the SDK has no model {0} with items".format(response_class))
    item_class = model.__fields__["items"].type_
    for item in items:
        if not isinstance(item, item_class):
            raise TypeError(
                "{0} holds {1}, not {2}".format(
                    response_class, item_class.__name__, type(item).__name__
                )
            )

    def page(kwargs):
        if len(answers.requests) >= 2 * len(items) + 10:
            pytest.fail(
                "{0}: page after page is asked for, the last with {1}".format(
                    response_class, kwargs
                )
            )
        answers.requests.append(dict(kwargs))
        start = kwargs.get("offset") or 0
        if kwargs.get("continuation_token"):
            start = int(kwargs["continuation_token"].rsplit("-", 1)[1])
        size = page_size
        if kwargs.get("limit") is not None:
            size = min(kwargs["limit"], page_size)
        fields = dict(items=items[start : start + size])
        if not kwargs.get("sort") and start + size < len(items):
            fields["continuation_token"] = "page-{0}".format(start + size)
        total = len(items) if total_item_count == -1 else total_item_count
        if total is not None:
            fields["total_item_count"] = total
        return ApiResponse(200, {}, model(**fields))

    def later_page(**kwargs):
        return page(kwargs)

    def answers(**kwargs):
        # as Client._call_api does with the answer to the first request
        return NEWEST.Client._create_valid_response(
            None, page(kwargs), later_page, dict(kwargs)
        )

    answers.requests = []
    return answers


def error_response(status_code, message=None, context=None):
    """Return the SDK answer to a refused call: a status and one error, no items."""
    return ErrorResponse(status_code, [ApiError(context, message)], {})


@pytest.fixture(autouse=True)
def _no_pure1_environment(monkeypatch):
    """Take the Pure1 variables of the shell that started pytest out of the test.

    get_pure1 reads a credential the task gives empty from them, and
    py-pure-client reads PURE1_ID_TOKEN. A test that needs one sets it with
    monkeypatch.setenv.
    """
    for name in (
        "PURE1_APP_ID",
        "PURE1_PRIVATE_KEY_FILE",
        "PURE1_PRIVATE_KEY_PASSWORD",
        "PURE1_PRIVATE_PASSWORD",
        "PURE1_ID_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def _no_client(*args, **kwargs):
    pytest.fail("pure1.Client() was called: ask for the pure1_client fixture")


@pytest.fixture(autouse=True)
def client_factory(monkeypatch):
    """Replace pure1.Client() by a mock with its signature and return the mock.

    It is in place for every test, so that none can open a connection.
    """
    factory = create_autospec(SDK.Client, side_effect=_no_client)
    monkeypatch.setattr(SDK, "Client", factory)
    return factory


@pytest.fixture
def pure1_client(client_factory):
    """Return the stand-in for the SDK client; pure1.Client() gives it in this test."""
    client = _StandIn(NEWEST.Client)
    client._api_client = _StandIn(NEWEST.client.ApiClient)
    # get_pure1 checks every new client with get_arrays(limit=0)
    client.get_arrays.return_value = valid_response()
    client_factory.side_effect = None
    client_factory.return_value = client
    return client


@pytest.fixture
def no_sdk(monkeypatch):
    """Make 'import pypureclient' fail, as on a host without py-pure-client."""
    monkeypatch.setitem(sys.modules, "pypureclient", None)


def _thirteen_hours_ahead(seconds=None):
    if seconds is None:
        seconds = time.time()
    return time.gmtime(seconds + 13 * 3600)


@pytest.fixture
def localtime_ahead_of_utc(monkeypatch):
    """Make time.localtime() run 13 hours ahead of UTC.

    For a test of a time the code must give in UTC: on a host in UTC, as in
    CI, local time is UTC and code that used localtime() would pass.
    """
    monkeypatch.setattr(time, "localtime", _thirteen_hours_ahead)


@pytest.fixture
def host_west_of_utc():
    """Put the host five hours behind UTC, for whatever reads the local time.

    For a time or a date the code must give in UTC whatever the zone of the
    host. localtime_ahead_of_utc cannot show a date in local time: ahead of
    UTC a 00:00 UTC date is the same day, and datetime does not use
    time.localtime().
    """
    try:
        with pytest.MonkeyPatch.context() as host:
            host.setenv("TZ", "EST5")
            time.tzset()
            yield
    finally:
        time.tzset()
