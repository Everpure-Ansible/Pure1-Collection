# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the (deprecated) pure1_network_interfaces module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from pypureclient import ErrorResponse, PureError

from plugins.modules import pure1_network_interfaces
from tests.unit.conftest import error_response, run_main, sdk_model, valid_response


def _make_iface(**overrides):
    fields = dict(
        name="ct0.eth0",
        services=["management"],
        enabled=True,
        gateway="10.0.0.1",
        mtu=1500,
        netmask="255.255.255.0",
        address="10.0.0.10",
        subinterfaces=[],
        hwaddr="aa:bb:cc:dd:ee:ff",
        speed=25000000000,  # 25 Gb/s in bits/s
        arrays=[sdk_model("FixedReferenceFqdn", name="foo")],
    )
    fields.update(overrides)
    return sdk_model("NetworkInterface", **fields)


class TestPure1NetworkInterfacesMain:
    """Tests for the pure1_network_interfaces main() flow."""

    def test_collects_interface_details(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_iface()]
        )

        module = run_main(pure1_network_interfaces, name="foo")

        pure1_client.get_network_interfaces.assert_called_once_with(
            filter="arrays.name='foo'"
        )
        module.exit_json.assert_called_once()
        network_info = module.exit_json.call_args[1]["network_info"]
        port = network_info["foo"]["ct0.eth0"]
        assert port["address"] == "10.0.0.10"
        assert port["mac_address"] == "aa:bb:cc:dd:ee:ff"
        assert port["speed"] == 25  # 25000000000 / 1e9 rounded
        assert port["services"] == ["management"]
        assert port["enabled"] is True

    @pytest.mark.parametrize(
        "name, expected",
        [
            pytest.param("o'brien", "arrays.name='o\\'brien'", id="single quote"),
            pytest.param("back\\slash", "arrays.name='back\\\\slash'", id="backslash"),
        ],
    )
    def test_array_name_is_escaped_in_the_filter(self, pure1_client, name, expected):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_iface()]
        )

        module = run_main(pure1_network_interfaces, name=name)

        pure1_client.get_network_interfaces.assert_called_once_with(filter=expected)
        # the result is keyed by the name as the task gave it
        assert list(module.exit_json.call_args[1]["network_info"]) == [name]

    def test_no_interfaces_fails(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([])

        module = run_main(pure1_network_interfaces, name="foo")

        module.fail_json.assert_called_once()
        # the message shows that the module failed and not get_pure1 before it
        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to get network interfaces information. Check provided array name."
        )
        module.exit_json.assert_not_called()

    def test_interface_row_holds_the_fields_of_the_interface(self, pure1_client):
        iface = _make_iface(subinterfaces=["ct0.eth1"])
        pure1_client.get_network_interfaces.return_value = valid_response([iface])

        module = run_main(pure1_network_interfaces, name="foo")

        module.exit_json.assert_called_once_with(
            changed=False,
            network_info={
                "foo": {
                    "ct0.eth0": {
                        "services": ["management"],
                        "enabled": True,
                        "gateway": "10.0.0.1",
                        "mtu": 1500,
                        "netmask": "255.255.255.0",
                        "address": "10.0.0.10",
                        "subinterfaces": ["ct0.eth1"],
                        "mac_address": "aa:bb:cc:dd:ee:ff",
                        "speed": 25,
                    }
                }
            },
        )

    def test_fields_pure1_does_not_report_get_their_defaults(self, pure1_client):
        """Pure1 leaves a field out when it has no value: speed on a FlashBlade."""
        iface = sdk_model(
            "NetworkInterface",
            name="data-vip1",
            services=["data"],
            enabled=False,
            subinterfaces=[],
            arrays=[sdk_model("FixedReferenceFqdn", name="foo")],
        )
        pure1_client.get_network_interfaces.return_value = valid_response([iface])

        module = run_main(pure1_network_interfaces, name="foo")

        network_info = module.exit_json.call_args[1]["network_info"]
        assert network_info["foo"]["data-vip1"] == {
            "services": ["data"],
            "enabled": False,
            "gateway": "",
            "mtu": "",
            "netmask": "",
            "address": "",
            "subinterfaces": [],
            "mac_address": "",
            "speed": 0,
        }

    def test_interface_with_only_a_name_gets_every_default(self, pure1_client):
        iface = sdk_model(
            "NetworkInterface",
            name="data-vip1",
            arrays=[sdk_model("FixedReferenceFqdn", name="foo")],
        )
        pure1_client.get_network_interfaces.return_value = valid_response([iface])

        module = run_main(pure1_network_interfaces, name="foo")

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            network_info={
                "foo": {
                    "data-vip1": {
                        "services": None,
                        "enabled": None,
                        "gateway": "",
                        "mtu": "",
                        "netmask": "",
                        "address": "",
                        "subinterfaces": None,
                        "mac_address": "",
                        "speed": 0,
                    }
                }
            },
        )

    @pytest.mark.parametrize("missing", ["services", "enabled", "subinterfaces"])
    def test_interface_without_one_field_gets_null_for_it(self, pure1_client, missing):
        iface = _make_iface(**{missing: None})
        pure1_client.get_network_interfaces.return_value = valid_response([iface])

        module = run_main(pure1_network_interfaces, name="foo")

        row = module.exit_json.call_args[1]["network_info"]["foo"]["ct0.eth0"]
        expected = {"services": ["management"], "enabled": True, "subinterfaces": []}
        expected[missing] = None
        assert {key: row[key] for key in expected} == expected

    def test_interface_without_name_is_left_out(self, pure1_client):
        nameless = sdk_model(
            "NetworkInterface", arrays=[sdk_model("FixedReferenceFqdn", name="foo")]
        )
        pure1_client.get_network_interfaces.return_value = valid_response(
            [nameless, _make_iface(), nameless]
        )

        module = run_main(pure1_network_interfaces, name="foo")

        module.warn.assert_called_once_with(
            "Network interfaces that Pure1 returned without a name are left "
            "out of the result: 2"
        )
        network_info = module.exit_json.call_args[1]["network_info"]
        assert list(network_info) == ["foo"]
        assert list(network_info["foo"]) == ["ct0.eth0"]

    def test_interface_with_an_empty_name_is_listed(self, pure1_client):
        """Only a name that Pure1 did not send leaves an interface out."""
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_iface(name="")]
        )

        module = run_main(pure1_network_interfaces, name="foo")

        module.warn.assert_not_called()
        network_info = module.exit_json.call_args[1]["network_info"]
        assert list(network_info["foo"]) == [""]
        assert network_info["foo"][""]["address"] == "10.0.0.10"

    def test_speed_is_rounded_to_whole_gigabits(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_iface(speed=9600000000)]
        )

        module = run_main(pure1_network_interfaces, name="foo")

        network_info = module.exit_json.call_args[1]["network_info"]
        speed = network_info["foo"]["ct0.eth0"]["speed"]
        assert speed == 10
        assert isinstance(speed, int)

    def test_every_interface_of_the_array_is_listed(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [
                _make_iface(),
                _make_iface(name="ct1.eth0", address="10.0.0.11"),
            ]
        )

        module = run_main(pure1_network_interfaces, name="foo")

        network_info = module.exit_json.call_args[1]["network_info"]
        assert list(network_info) == ["foo"]
        assert list(network_info["foo"]) == ["ct0.eth0", "ct1.eth0"]
        assert network_info["foo"]["ct0.eth0"]["address"] == "10.0.0.10"
        assert network_info["foo"]["ct1.eth0"]["address"] == "10.0.0.11"

    def test_interfaces_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_iface()]
        )

        module = run_main(pure1_network_interfaces, check_mode=True, name="foo")

        pure1_client.get_network_interfaces.assert_called_once_with(
            filter="arrays.name='foo'"
        )
        network_info = module.exit_json.call_args[1]["network_info"]
        assert network_info["foo"]["ct0.eth0"]["address"] == "10.0.0.10"

    def test_name_is_required(self, pure1_client):
        module = run_main(pure1_network_interfaces)

        module.fail_json.assert_called_once_with(msg="missing required arguments: name")
        pure1_client.get_network_interfaces.assert_not_called()

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = error_response(
            403, "Access denied"
        )

        module = run_main(pure1_network_interfaces, name="foo")

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message

    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request get_network_interfaces failed (HTTP 403): "
                "Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "Pure1 request get_network_interfaces failed (HTTP 400): Illegal "
                "filter format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request get_network_interfaces failed (HTTP 503): "
                "Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_network_interfaces failed: "
                "PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request get_network_interfaces failed: "
                "TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, attribute, answer, expected
    ):
        setattr(pure1_client.get_network_interfaces, attribute, answer)

        module = run_main(pure1_network_interfaces, name="foo")

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
