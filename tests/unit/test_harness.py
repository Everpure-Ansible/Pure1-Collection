# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Checks of the test harness in conftest.py itself."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import types
from unittest.mock import patch

import pytest
from ansible.module_utils.basic import AnsibleModule, env_fallback

from plugins.module_utils import pure1 as pure1_utils
from plugins.modules import pure1_alerts
from tests.unit.conftest import (
    CREDENTIALS,
    ansible_module,
    run_main,
    sdk_model,
    valid_response,
)


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
