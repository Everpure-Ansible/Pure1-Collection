# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_nics module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from pypureclient import ErrorResponse, PureError

from plugins.modules import pure1_nics
from plugins.modules.pure1_nics import generate_nics_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

NIC0 = {
    "ct0.eth0": {
        "address": "10.0.0.10",
        "gateway": "10.0.0.1",
        "hwaddr": "aa:bb:cc:dd:ee:ff",
        "netmask": "255.255.255.0",
        "mtu": 1500,
        "speed": 10,
        "speed_bps": 10000000000,
        "enabled": True,
        "services": [],
        "subinterfaces": [],
    }
}


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


def _nic_without_name():
    return sdk_model(
        "NetworkInterface", arrays=[sdk_model("FixedReferenceFqdn", name="array1")]
    )


def _nic_without_arrays():
    return sdk_model("NetworkInterface", name="ct1.eth0")


def _nic_with_empty_arrays():
    return _make_nic(name="ct1.eth0", arrays=[])


def _nic_with_null_array():
    # The SDK reads a response without validating it, so a null entry of
    # the list arrives as None; the constructor of the model refuses one
    nic = _make_nic(name="ct1.eth0")
    nic.arrays[0] = None
    return nic


def _nic_on_array_without_name():
    return _make_nic(name="ct1.eth0", arrays=[sdk_model("FixedReferenceFqdn")])


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

    @pytest.mark.parametrize(
        "array, expected",
        [
            pytest.param("o'brien", "arrays.name='o\\'brien'", id="single quote"),
            pytest.param("back\\slash", "arrays.name='back\\\\slash'", id="backslash"),
        ],
    )
    def test_array_name_is_escaped_in_the_filter(self, pure1_client, array, expected):
        module = ansible_module(array=array)
        pure1_client.get_network_interfaces.return_value = valid_response([])

        result = generate_nics_dict(module, pure1_client)

        pure1_client.get_network_interfaces.assert_called_once_with(filter=expected)
        # a name Pure1 does not know gives an empty result and no warning
        assert result == {}
        module.warn.assert_not_called()

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

    @pytest.mark.parametrize(
        "reported, speed, speed_bps",
        [
            pytest.param(9600000000, 10, 9600000000, id="reported"),
            pytest.param(100000000, 0, 100000000, id="below half a gigabit"),
            pytest.param(0, 0, 0, id="reported as 0"),
            pytest.param(None, 0, None, id="not reported"),
        ],
    )
    def test_speed_bps_is_the_speed_as_pure1_reports_it(
        self, pure1_client, reported, speed, speed_bps
    ):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic(speed=reported)]
        )

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        details = result["array1"][0]["ct0.eth0"]
        assert details["speed"] == speed
        # None, not 0, when Pure1 reports no speed
        assert [details["speed_bps"]] == [speed_bps]
        assert (details["speed_bps"] is None) == (reported is None)

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
                        "speed_bps": 10000000000,
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
            "speed_bps": None,
            "enabled": False,
            "services": ["data"],
            "subinterfaces": [],
        }

    def test_interface_with_only_a_name_gets_every_default(self, pure1_client):
        nic = sdk_model(
            "NetworkInterface",
            name="data-vip1",
            arrays=[sdk_model("FixedReferenceFqdn", name="blade1")],
        )
        pure1_client.get_network_interfaces.return_value = valid_response([nic])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert result == {
            "blade1": [
                {
                    "data-vip1": {
                        "address": None,
                        "gateway": None,
                        "hwaddr": None,
                        "netmask": None,
                        "mtu": None,
                        "speed": 0,
                        "speed_bps": None,
                        "enabled": None,
                        "services": [],
                        "subinterfaces": [],
                    }
                }
            ]
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

    def test_interface_with_two_array_references_is_filed_under_the_first(
        self, pure1_client
    ):
        nic = _make_nic(
            arrays=[
                sdk_model("FixedReferenceFqdn", name="array1"),
                sdk_model("FixedReferenceFqdn", name="array2"),
            ]
        )
        pure1_client.get_network_interfaces.return_value = valid_response([nic])

        result = generate_nics_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [NIC0]}

    @pytest.mark.parametrize(
        "array, name",
        [
            pytest.param("array1", "", id="empty name"),
            pytest.param("", "ct0.eth0", id="empty array name"),
        ],
    )
    def test_interface_with_an_empty_name_is_filed(self, pure1_client, array, name):
        """Only a name that Pure1 did not send leaves an interface out."""
        module = ansible_module(array=None)
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic(array, name=name)]
        )

        result = generate_nics_dict(module, pure1_client)

        assert result == {array: [{name: NIC0["ct0.eth0"]}]}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_nic_without_name, id="no name"),
            pytest.param(_nic_without_arrays, id="no arrays"),
            pytest.param(_nic_with_empty_arrays, id="empty arrays"),
            pytest.param(_nic_with_null_array, id="null array"),
            pytest.param(_nic_on_array_without_name, id="array without name"),
        ],
    )
    def test_interface_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = ansible_module(array=None)
        pure1_client.get_network_interfaces.return_value = valid_response(
            [broken(), _make_nic()]
        )

        result = generate_nics_dict(module, pure1_client)

        assert result == {"array1": [NIC0]}
        module.warn.assert_called_once_with(
            "Network interfaces that Pure1 returned without a name or without "
            "an array are left out of the result: 1"
        )

    def test_interfaces_left_out_give_one_warning(self, pure1_client):
        module = ansible_module(array="array1")
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_nic_without_name(), _make_nic(), _nic_without_arrays()]
        )

        result = generate_nics_dict(module, pure1_client)

        assert result == {"array1": [NIC0]}
        module.warn.assert_called_once_with(
            "Network interfaces that Pure1 returned without a name or without "
            "an array are left out of the result: 2"
        )


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
                                "speed_bps": 10000000000,
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

    def test_interfaces_that_are_all_filed_give_no_warning(self, pure1_client):
        pure1_client.get_network_interfaces.return_value = valid_response(
            [_make_nic(), _make_nic(name="ct1.eth0")]
        )

        module = run_main(pure1_nics, array="array1")

        module.warn.assert_not_called()
        nics = module.exit_json.call_args[1]["pure1_nics"]["nics"]
        assert [list(nic) for nic in nics["array1"]] == [["ct0.eth0"], ["ct1.eth0"]]

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

    @pytest.mark.parametrize("params", [{}, {"array": "myarray"}], ids=["all", "named"])
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
        self, pure1_client, params, attribute, answer, expected
    ):
        setattr(pure1_client.get_network_interfaces, attribute, answer)

        module = run_main(pure1_nics, **params)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
