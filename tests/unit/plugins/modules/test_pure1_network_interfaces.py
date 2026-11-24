# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the (deprecated) pure1_network_interfaces module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

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

    def test_no_interfaces_fails(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([])

        module = run_main(pure1_network_interfaces, name="foo")

        module.fail_json.assert_called_once()
        # the message, misspelt as the module has it, shows that the module
        # failed and not get_pure1 before it
        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to get netowrk interfaces information. Check provided array name."
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

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_network_interfaces reads .items of an error answer, "
        "which has none",
    )
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
