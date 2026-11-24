# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_nics module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from plugins.modules import pure1_nics
from plugins.modules.pure1_nics import generate_nics_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _make_nic(array_name="array1", **overrides):
    fields = dict(
        name="ct0.eth0",
        address="10.0.0.10",
        gateway="10.0.0.1",
        hwaddr="aa:bb:cc:dd:ee:ff",
        netmask="255.255.255.0",
        mtu=1500,
        speed=10000000000,  # 10 Gb/s in bits/s
        enabled=True,
        arrays=[sdk_model("FixedReferenceFqdn", name=array_name)],
    )
    fields.update(overrides)
    return sdk_model("NetworkInterface", **fields)


class TestGenerateNicsDict:
    """Tests for generate_nics_dict."""

    def test_basic_mapping_and_speed_rounding(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([_make_nic()])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_network_interfaces.assert_called_once_with()
        details = result["array1"][0]["ct0.eth0"]
        assert details["address"] == "10.0.0.10"
        assert details["mtu"] == 1500
        assert details["speed"] == 10  # 10000000000 / 1e9 rounded
        assert details["enabled"] is True
        assert details["services"] == []
        assert details["subinterfaces"] == []

    def test_services_and_subinterfaces_populated(self, pure1_client):
        nic = _make_nic(services=["management"], subinterfaces=["ct0.eth1"])
        pure1_client.get_network_interfaces.return_value = valid_response([nic])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        details = result["array1"][0]["ct0.eth0"]
        assert details["services"] == ["management"]
        assert details["subinterfaces"] == ["ct0.eth1"]

    def test_array_filter_applied(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic("myarray")]
        )

        generate_nics_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_network_interfaces.assert_called_once_with(
            filter="arrays.name='myarray'"
        )

    def test_grouping_across_arrays(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic("array1"), _make_nic("array2")]
        )

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}

    def test_zero_speed_rounds_to_zero(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic(speed=0)]
        )

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["ct0.eth0"]["speed"] == 0

    def test_speed_is_rounded_to_whole_gigabits(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic(speed=9600000000)]
        )

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        speed = result["array1"][0]["ct0.eth0"]["speed"]
        assert speed == 10
        assert isinstance(speed, int)

    def test_nic_row_holds_the_fields_of_the_interface(self, pure1_client):
        nic = _make_nic(services=["management"], subinterfaces=["ct0.eth1"])
        pure1_client.get_network_interfaces.return_value = valid_response([nic])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert result == {
            "array1": [
                {
                    "ct0.eth0": {
                        "address": "10.0.0.10",
                        "gateway": "10.0.0.1",
                        "hwaddr": "aa:bb:cc:dd:ee:ff",
                        "netmask": "255.255.255.0",
                        "mtu": 1500,
                        "speed": 10,
                        "enabled": True,
                        "services": ["management"],
                        "subinterfaces": ["ct0.eth1"],
                    }
                }
            ]
        }

    def test_fields_pure1_does_not_report_get_their_defaults(self, pure1_client):
        """Pure1 leaves a field out when it has no value: speed on a FlashBlade."""
        nic = sdk_model(
            "NetworkInterface",
            name="data-vip1",
            enabled=False,
            services=["data"],
            subinterfaces=[],
            arrays=[sdk_model("FixedReferenceFqdn", name="blade1")],
        )
        pure1_client.get_network_interfaces.return_value = valid_response([nic])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert result["blade1"][0]["data-vip1"] == {
            "address": None,
            "gateway": None,
            "hwaddr": None,
            "netmask": None,
            "mtu": None,
            "speed": 0,
            "enabled": False,
            "services": ["data"],
            "subinterfaces": [],
        }

    def test_each_array_holds_its_own_interfaces(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [
                _make_nic("array1", name="ct0.eth0"),
                _make_nic("array2", name="ct0.eth1", address="10.0.0.20"),
                _make_nic("array1", name="ct1.eth0"),
            ]
        )

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}
        assert [list(nic) for nic in result["array1"]] == [["ct0.eth0"], ["ct1.eth0"]]
        assert [list(nic) for nic in result["array2"]] == [["ct0.eth1"]]
        assert result["array2"][0]["ct0.eth1"]["address"] == "10.0.0.20"


class TestPure1NicsMain:
    """Tests for the pure1_nics main() flow."""

    def test_all_interfaces_are_returned(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([_make_nic()])

        # array is left to the argument spec
        module = run_main(pure1_nics)

        pure1_client.get_network_interfaces.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_nics={
                "nics": {
                    "array1": [
                        {
                            "ct0.eth0": {
                                "address": "10.0.0.10",
                                "gateway": "10.0.0.1",
                                "hwaddr": "aa:bb:cc:dd:ee:ff",
                                "netmask": "255.255.255.0",
                                "mtu": 1500,
                                "speed": 10,
                                "enabled": True,
                                "services": [],
                                "subinterfaces": [],
                            }
                        }
                    ]
                }
            },
        )

    def test_interfaces_of_named_array_are_returned(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic("myarray")]
        )

        module = run_main(pure1_nics, array="myarray")

        pure1_client.get_network_interfaces.assert_called_once_with(
            filter="arrays.name='myarray'"
        )
        nics = module.exit_json.call_args[1]["pure1_nics"]["nics"]
        assert list(nics) == ["myarray"]
        assert list(nics["myarray"][0]) == ["ct0.eth0"]

    def test_interfaces_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([_make_nic()])

        module = run_main(pure1_nics, check_mode=True)

        pure1_client.get_network_interfaces.assert_called_once_with()
        nics = module.exit_json.call_args[1]["pure1_nics"]["nics"]
        assert nics["array1"][0]["ct0.eth0"]["address"] == "10.0.0.10"

    def test_no_interfaces_gives_an_empty_result(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response([])

        module = run_main(pure1_nics, array="array1")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_nics={"nics": {}})

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_nics reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = error_response(
            403, "Access denied"
        )

        module = run_main(pure1_nics)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message
