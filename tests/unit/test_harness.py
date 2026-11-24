# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Checks of the test harness in conftest.py itself."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import calendar
import importlib
import os
import sys
import time
import types
from unittest.mock import patch

import pytest
from ansible.module_utils.basic import AnsibleModule, env_fallback

from plugins.module_utils import pure1 as pure1_utils
from plugins.modules import pure1_alerts
from tests.unit.conftest import (
    CREDENTIALS,
    MODULE_UTILS,
    alias_module_utils,
    ansible_module,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# The name the plugins import module_utils under
COLLECTION_UTILS = "ansible_collections.everpure.pure1.plugins.module_utils"

# The variables get_pure1 and py-pure-client read credentials from
PURE1_VARIABLES = [
    "PURE1_APP_ID",
    "PURE1_PRIVATE_KEY_FILE",
    "PURE1_PRIVATE_KEY_PASSWORD",
    "PURE1_PRIVATE_PASSWORD",
    "PURE1_ID_TOKEN",
]


@pytest.fixture(scope="class")
def exported_pure1_variables():
    """Export the Pure1 variables, as the shell that starts pytest may have them."""
    with pytest.MonkeyPatch.context() as shell:
        for name in PURE1_VARIABLES:
            shell.setenv(name, "from-the-shell")
        yield


@pytest.fixture
def zone_is_restored():
    """Check after the test that the host is back in its own time zone.

    Asked for before the fixture that changes the zone, it ends after it.
    The zone it finds must be the one time.tzset() reads from the
    environment, whatever an earlier test left. A host that is itself five
    hours behind UTC in winter differs in its summer time only.
    """

    def zone():
        winter = time.localtime(1612357445)[:6]
        summer = time.localtime(1625140800)[:6]
        return winter, summer, time.timezone, time.altzone, time.tzname

    yield
    found = zone()
    time.tzset()
    assert found == zone()


def _plugin(**options):
    """Return a module whose main() builds its AnsibleModule and exits."""
    plugin = types.ModuleType("pure1_example")
    plugin.AnsibleModule = AnsibleModule

    def main():
        argument_spec = pure1_utils.pure1_argument_spec()
        argument_spec.update(
            dict(
                name=dict(type="str", required=True),
                array=dict(type="str", fallback=(env_fallback, ["PURE1_EXAMPLE"])),
                state=dict(type="str", default="open", choices=["open", "closed"]),
                limit=dict(type="int"),
            )
        )
        module = plugin.AnsibleModule(argument_spec, **options)
        module.exit_json(changed=False)

    plugin.main = main
    return plugin


class TestStandIns:
    """The stand-ins of the harness take only what the SDK and ansible-core have."""

    def test_module_runs_on_the_module_utils_under_test(self):
        """Not on a second copy of the file, which the tests could not reach."""
        assert pure1_alerts.get_pure1 is pure1_utils.get_pure1

    def test_client_has_only_the_methods_of_the_sdk(self, pure1_client):
        with pytest.raises(AttributeError):
            pure1_client.get_alert()

    def test_client_method_takes_only_the_keywords_of_the_sdk(self, pure1_client):
        with pytest.raises(TypeError):
            pure1_client.get_alerts(filters="state='open'")

    def test_client_factory_takes_only_the_keywords_of_the_sdk(self, client_factory):
        with pytest.raises(TypeError):
            client_factory(app_id="pure1-app-id", user_agent="Ansible")

    def test_no_client_without_the_fixture(self):
        with pytest.raises(pytest.fail.Exception):
            pure1_utils.get_pure1(ansible_module())

    def test_answer_the_test_did_not_give_cannot_be_read(self, pure1_client):
        answer = pure1_client.get_alerts(filter="state='open'")
        with pytest.raises(pytest.fail.Exception, match="Client.get_alerts"):
            getattr(answer, "items")
        # a default does not hide it
        with pytest.raises(pytest.fail.Exception):
            getattr(answer, "errors", None)
        with pytest.raises(pytest.fail.Exception):
            list(answer)
        with pytest.raises(pytest.fail.Exception, match="AnsibleModule.run_command"):
            list(ansible_module().run_command("ls"))

    def test_method_first_used_under_a_signature_patch_fails(self, pure1_client):
        """create_autospec checks no call when it cannot read the signature."""
        pure1_client.get_ports.return_value = valid_response()
        with pytest.raises(pytest.fail.Exception, match="inspect.signature"):
            with patch("inspect.signature", side_effect=ValueError("no signature")):
                getattr(pure1_client, "get_alerts")
        with pytest.raises(TypeError):
            pure1_client.get_alerts(filters="state='open'")
        # a method used before the patch keeps its signature under it
        with patch("inspect.signature", side_effect=ValueError("no signature")):
            with pytest.raises(TypeError):
                pure1_client.get_ports(filters="arrays.name='foo'")

    def test_model_has_only_the_fields_of_the_sdk(self):
        with pytest.raises(TypeError):
            sdk_model("Alert", severty="critical")
        with pytest.raises(TypeError):
            sdk_model("Alert", arrays=[{"name": "foo", "site": "lab"}])

    def test_nested_model_keeps_its_class(self):
        """The SDK would rebuild it as the class of the field and drop model."""
        array = sdk_model("Array", name="foo", model="FA-X70R4")
        with pytest.raises(TypeError, match="FixedReferenceFqdn in the SDK, not Array"):
            sdk_model("Alert", arrays=[array])

    def test_unset_model_field_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            getattr(sdk_model("Alert", code=42), "closed")

    def test_module_has_only_the_methods_of_ansible(self):
        with pytest.raises(AttributeError):
            ansible_module().warning("text")


class TestModuleUtilsAlias:
    """Every file of plugins/module_utils is the same module under both its names."""

    def test_every_file_has_its_collection_name(self):
        names = sorted(
            name[:-3]
            for name in os.listdir(MODULE_UTILS)
            if name.endswith(".py") and name != "__init__.py"
        )

        assert "pure1" in names
        for name in names:
            module = importlib.import_module("plugins.module_utils." + name)
            assert sys.modules["{0}.{1}".format(COLLECTION_UTILS, name)] is module

    def test_both_forms_of_the_import_give_the_module_under_test(self):
        from ansible_collections.everpure.pure1.plugins.module_utils import pure1
        from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
            get_pure1,
        )

        assert pure1 is pure1_utils
        assert get_pure1 is pure1_utils.get_pure1

    def test_new_file_needs_no_line_in_conftest(self, tmp_path, monkeypatch):
        """Also one that imports, by the alias, a file that sorts after it."""
        package = tmp_path / "harness_utils"
        package.mkdir()
        (package / "arrays.py").write_text(
            "from harness_collection.module_utils.shared import VALUE\n"
            "from harness_collection.module_utils import tags\n"
        )
        (package / "shared.py").write_text("VALUE = ['shared']\n")
        (package / "tags.py").write_text("VALUE = ['tags']\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        before = set(sys.modules)
        try:
            names = alias_module_utils(
                str(package), "harness_utils", "harness_collection.module_utils"
            )

            assert names == ["arrays", "shared", "tags"]
            for name in names:
                module = sys.modules["harness_utils." + name]
                assert sys.modules["harness_collection.module_utils." + name] is module
            # what arrays imported is the one module, not a second copy of the file
            arrays = sys.modules["harness_utils.arrays"]
            assert arrays.VALUE is sys.modules["harness_utils.shared"].VALUE
            assert arrays.tags is sys.modules["harness_utils.tags"]
        finally:
            for name in set(sys.modules) - before:
                del sys.modules[name]

    def test_package_in_the_directory_gets_no_name(self, tmp_path, monkeypatch):
        """Only the files do: a package has no file of its own name to read."""
        package = tmp_path / "harness_files"
        (package / "common").mkdir(parents=True)
        (package / "common" / "__init__.py").write_text("VALUE = ['common']\n")
        (package / "shared.py").write_text("VALUE = ['shared']\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        before = set(sys.modules)
        try:
            names = alias_module_utils(
                str(package), "harness_files", "harness_collection.files"
            )

            assert names == ["shared"]
            assert "harness_collection.files.shared" in sys.modules
            assert "harness_collection.files.common" not in sys.modules
        finally:
            for name in set(sys.modules) - before:
                del sys.modules[name]


class TestPagedAnswers:
    """paged_answers pages as Pure1 does, and the SDK iterator reads its answers."""

    @pytest.fixture
    def pods(self):
        return [sdk_model("Pod", name="pod-{0}".format(number)) for number in range(8)]

    def test_answer_is_the_first_page_with_the_total_and_a_token(self, pods):
        answers = paged_answers("PodGetResponse", pods, page_size=3)

        answer = answers(filter="name='pod-*'")

        assert answer.status_code == 200
        assert answer.total_item_count == 8
        assert answer.continuation_token == "page-3"
        assert not hasattr(answer, "errors")
        assert len(answer.items) == 8
        assert answers.requests == [{"filter": "name='pod-*'"}]

    def test_sdk_reads_the_pages_of_the_tokens(self, pods):
        answers = paged_answers("PodGetResponse", pods, page_size=3)

        assert list(answers(filter="name='pod-*'").items) == pods
        assert answers.requests == [
            {"filter": "name='pod-*'"},
            {"filter": "name='pod-*'", "continuation_token": "page-3"},
            {"filter": "name='pod-*'", "continuation_token": "page-6"},
        ]

    def test_page_that_ends_with_the_last_item_has_no_token(self, pods):
        """The SDK asks for no page after it."""
        answers = paged_answers("PodGetResponse", pods, page_size=4)

        assert list(answers().items) == pods
        assert answers.requests == [{}, {"continuation_token": "page-4"}]
        assert answers(continuation_token="page-4").continuation_token is None

    def test_items_of_an_answer_are_copies(self, pods):
        """Equal to the items given, and not the same objects."""
        (item,) = list(paged_answers("PodGetResponse", pods[:1])().items)

        assert item == pods[0]
        assert item is not pods[0]

    def test_response_class_the_sdk_does_not_have_is_refused(self, pods):
        """The SDK has None for it, and the first request would raise."""
        with pytest.raises(TypeError) as refused:
            paged_answers("PodsGetResponse", pods)

        assert str(refused.value) == "the SDK has no model PodsGetResponse with items"
        with pytest.raises(TypeError, match="the SDK has no model Pod with items"):
            paged_answers("Pod", pods)

    def test_item_of_another_class_than_the_answer_holds_is_refused(self):
        """The SDK would rebuild it as a Volume and drop suffix."""
        snapshot = sdk_model("VolumeSnapshot", name="v1.snap", suffix="snap")

        with pytest.raises(TypeError) as refused:
            paged_answers("VolumeGetResponse", [snapshot])

        assert str(refused.value) == (
            "VolumeGetResponse holds Volume, not VolumeSnapshot"
        )

    def test_sorted_answer_has_no_token_and_the_sdk_stops_after_it(self, pods):
        """What Pure1 and py-pure-client 1.99.0 do: no error, and five pods short."""
        answers = paged_answers("PodGetResponse", pods, page_size=3)

        answer = answers(sort=["name"])

        assert answer.continuation_token is None
        assert answer.total_item_count == 8
        assert list(answer.items) == pods[:3]
        assert answers.requests == [{"sort": ["name"]}]

    @pytest.mark.parametrize(
        "limit, count, requests",
        [
            pytest.param(2, 2, 1, id="below a page: the SDK stops at it"),
            pytest.param(3, 3, 1, id="a page: the SDK stops at it"),
            pytest.param(4, 8, 3, id="above a page: the SDK reads everything"),
            pytest.param(0, 0, 1, id="0: no item"),
        ],
    )
    def test_limit_as_pure1_and_the_sdk_treat_it(self, pods, limit, count, requests):
        """Pure1 gives at most a page, and the SDK counts its limit within a page."""
        answers = paged_answers("PodGetResponse", pods, page_size=3)

        answer = answers(limit=limit)

        assert answer.total_item_count == 8
        assert answer.continuation_token == "page-{0}".format(min(limit, 3))
        assert list(answer.items) == pods[:count]
        assert len(answers.requests) == requests

    def test_offset_is_where_the_page_starts(self, pods):
        answers = paged_answers("PodGetResponse", pods, page_size=3)

        assert list(answers(sort=["name"], offset=3).items) == pods[3:6]
        assert list(answers(sort=["name"], offset=6, limit=1).items) == pods[6:7]
        last = answers(offset=6)
        assert last.continuation_token is None
        assert list(last.items) == pods[6:]
        beyond = answers(offset=8)
        assert beyond.total_item_count == 8
        assert list(beyond.items) == []

    def test_page_holds_1000_items_unless_told(self):
        pods = [
            sdk_model("Pod", name="pod-{0}".format(number)) for number in range(1001)
        ]
        answers = paged_answers("PodGetResponse", pods)

        answer = answers(sort=["name"], limit=2000)

        assert list(answer.items) == pods[:1000]

    @pytest.mark.parametrize("total", [None, 5, 0])
    def test_total_can_be_another_or_none(self, pods, total):
        answers = paged_answers("PodGetResponse", pods[:2], total_item_count=total)

        assert answers().total_item_count == total

    def test_sdk_reads_no_item_beyond_the_total(self, pods):
        """The SDK loses the items of an answer whose total is too low."""
        nothing = paged_answers("PodGetResponse", pods[:2], total_item_count=0)
        one = paged_answers("PodGetResponse", pods[:2], total_item_count=1)

        assert list(nothing().items) == []
        assert list(one().items) == pods[:1]

    def test_no_items(self):
        answer = paged_answers("PodGetResponse", [])()

        assert answer.total_item_count == 0
        assert answer.continuation_token is None
        assert list(answer.items) == []

    def test_code_that_does_not_stop_asking_fails_the_test(self, pods):
        """Instead of a test run that never ends."""
        answers = paged_answers("PodGetResponse", pods[:2])
        for dummy in range(14):
            answers(offset=2)

        with pytest.raises(pytest.fail.Exception, match="page after page"):
            answers(offset=2)

    def test_answers_a_method_of_the_client(self, pure1_client, pods):
        """As a side_effect: the call is still bound to the signature of the SDK."""
        pure1_client.get_pods.side_effect = paged_answers("PodGetResponse", pods, 3)

        assert list(pure1_client.get_pods(filter="name='pod-*'").items) == pods
        with pytest.raises(TypeError):
            pure1_client.get_pods(filters="name='pod-*'")


class TestFixtures:
    """What the fixtures of the harness change for a test."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_localtime_runs_ahead_of_utc(self):
        assert time.localtime(0)[:6] == (1970, 1, 1, 13, 0, 0)
        assert time.gmtime(0)[:6] == (1970, 1, 1, 0, 0, 0)
        # without an argument it is the time now, as for the real localtime
        ahead = calendar.timegm(time.localtime()) - time.time()
        assert 13 * 3600 - 2 < ahead < 13 * 3600 + 2

    def test_host_is_five_hours_behind_utc(self, zone_is_restored, host_west_of_utc):
        assert time.localtime(1612357445)[:6] == (2021, 2, 3, 8, 4, 5)
        # 00:00 UTC is the evening before
        assert time.localtime(1612310400)[:4] == (2021, 2, 2, 19)
        assert time.gmtime(1612357445)[:6] == (2021, 2, 3, 13, 4, 5)

    @pytest.mark.usefixtures("exported_pure1_variables")
    def test_pure1_variables_of_the_shell_are_not_in_a_test(self):
        """get_pure1 would read a credential the test leaves empty from them."""
        assert [name for name in PURE1_VARIABLES if name in os.environ] == []


class TestRunMain:
    """run_main puts the params through the argument spec, as AnsibleModule does."""

    def test_defaults_and_types_come_from_the_argument_spec(self, monkeypatch):
        monkeypatch.delenv("PURE1_EXAMPLE", raising=False)

        module = run_main(_plugin(), name="foo", limit="5")

        module.exit_json.assert_called_once_with(changed=False)
        assert module.params == dict(
            CREDENTIALS, name="foo", array=None, state="open", limit=5
        )

    def test_fallback_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PURE1_EXAMPLE", "from-the-environment")

        module = run_main(_plugin(), name="foo")

        assert module.params["array"] == "from-the-environment"

    def test_no_log_values_come_from_the_argument_spec(self, monkeypatch):
        """What AnsibleModule hides in a result: also what a fallback read."""
        monkeypatch.delitem(CREDENTIALS, "app_id")
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")

        module = run_main(_plugin(), name="foo", password="keypass")

        assert module.no_log_values == {"env-app-id", "keypass"}
        # without the argument spec no param is known to be secret
        assert ansible_module(password="keypass").no_log_values == set()

    @pytest.mark.parametrize(
        "params, text",
        [
            pytest.param({}, "missing required arguments: name", id="required"),
            pytest.param({"name": "foo", "state": "shut"}, "shut", id="choice"),
            pytest.param({"name": "foo", "limit": "five"}, "limit", id="type"),
            pytest.param(
                {"name": "foo", "nmae": "bar"},
                "Unsupported parameters for module: nmae",
                id="unknown parameter",
            ),
        ],
    )
    def test_refused_params_end_in_fail_json(self, params, text, capsys):
        module = run_main(_plugin(), **params)

        module.exit_json.assert_not_called()
        assert text in module.fail_json.call_args[1]["msg"]
        # run_main shows why main() failed, for the test that did not expect it
        assert "fail_json: {'msg': " in capsys.readouterr().out

    def test_rules_beside_the_argument_spec_are_applied(self):
        plugin = _plugin(mutually_exclusive=[("name", "array")])

        module = run_main(plugin, name="foo", array="bar")

        assert "mutually exclusive" in module.fail_json.call_args[1]["msg"]

    def test_check_mode_needs_supports_check_mode(self):
        module = run_main(_plugin(), check_mode=True, name="foo")

        module.exit_json.assert_called_once_with(
            skipped=True, msg="module does not support check mode"
        )

        plugin = _plugin(supports_check_mode=True)

        module = run_main(plugin, check_mode=True, name="foo")

        module.exit_json.assert_called_once_with(changed=False)
