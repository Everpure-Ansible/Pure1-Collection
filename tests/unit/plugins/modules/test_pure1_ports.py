# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_ports module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from pypureclient import ErrorResponse, PureError

from plugins.modules import pure1_ports
from plugins.modules.pure1_ports import generate_ports_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

PORT0 = {
    "CT0.FC0": {
        "iqn": None,
        "nqn": None,
        "wwn": "21000024FF000001",
        "portal": None,
        "failover": None,
    }
}


def _make_port(array_name="array1", **overrides):
    # None leaves a field unset, as Pure1 does for what a port does not have
    fields = dict(
        name="CT0.FC0",
        iqn=None,
        nqn=None,
        wwn="21000024FF000001",
        portal=None,
        failover=None,
        arrays=[sdk_model("FixedReferenceFqdn", name=array_name)],
    )
    fields.update(overrides)
    return sdk_model("Port", **fields)


def _port_without_name():
    return sdk_model("Port", arrays=[sdk_model("FixedReferenceFqdn", name="array1")])


def _port_without_arrays():
    return sdk_model("Port", name="CT1.FC0")


def _port_with_empty_arrays():
    return _make_port(name="CT1.FC0", arrays=[])


def _port_with_null_array():
    # The SDK reads a response without validating it, so a null entry of
    # the list arrives as None; the constructor of the model refuses one
    port = _make_port(name="CT1.FC0")
    port.arrays[0] = None
    return port


def _port_on_array_without_name():
    return _make_port(name="CT1.FC0", arrays=[sdk_model("FixedReferenceFqdn")])


class TestGeneratePortsDict:
    """Tests for generate_ports_dict."""

    def test_basic_mapping(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([_make_port()])

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_ports.assert_called_once_with()
        assert result["array1"] == [
            {
                "CT0.FC0": {
                    "iqn": None,
                    "nqn": None,
                    "wwn": "21000024FF000001",
                    "portal": None,
                    "failover": None,
                }
            }
        ]

    def test_iscsi_port_fields(self, pure1_client):
        port = _make_port(
            name="CT0.ETH4",
            iqn="iqn.2010-06.com.purestorage:flasharray.1",
            wwn=None,
            portal="10.0.0.20:3260",
        )
        pure1_client.get_ports.return_value = valid_response([port])

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        details = result["array1"][0]["CT0.ETH4"]
        assert details["iqn"] == "iqn.2010-06.com.purestorage:flasharray.1"
        assert details["portal"] == "10.0.0.20:3260"
        assert details["wwn"] is None

    def test_array_filter_applied(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([_make_port("myarray")])

        generate_ports_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_ports.assert_called_once_with(filter="arrays.name='myarray'")

    @pytest.mark.parametrize(
        "array, expected",
        [
            pytest.param("o'brien", "arrays.name='o\\'brien'", id="single quote"),
            pytest.param("back\\slash", "arrays.name='back\\\\slash'", id="backslash"),
        ],
    )
    def test_array_name_is_escaped_in_the_filter(self, pure1_client, array, expected):
        module = ansible_module(array=array)
        pure1_client.get_ports.return_value = valid_response([])

        result = generate_ports_dict(module, pure1_client)

        pure1_client.get_ports.assert_called_once_with(filter=expected)
        # a name Pure1 does not know gives an empty result and no warning
        assert result == {}
        module.warn.assert_not_called()

    def test_grouping_across_arrays(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response(
            [_make_port("array1"), _make_port("array2")]
        )

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}

    def test_nvme_port_fields(self, pure1_client):
        port = _make_port(
            name="CT0.ETH10",
            nqn="nqn.2010-06.com.purestorage:flasharray.1",
            wwn=None,
            portal="10.0.0.30:4420",
        )
        pure1_client.get_ports.return_value = valid_response([port])

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["CT0.ETH10"] == {
            "iqn": None,
            "nqn": "nqn.2010-06.com.purestorage:flasharray.1",
            "wwn": None,
            "portal": "10.0.0.30:4420",
            "failover": None,
        }

    def test_failed_over_port_names_its_failover_port(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response(
            [_make_port(failover="CT1.FC0")]
        )

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["CT0.FC0"] == {
            "iqn": None,
            "nqn": None,
            "wwn": "21000024FF000001",
            "portal": None,
            "failover": "CT1.FC0",
        }

    def test_each_array_holds_its_own_ports(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response(
            [
                _make_port("array1", name="CT0.FC0"),
                _make_port("array2", name="CT0.FC1", wwn="21000024FF000002"),
                _make_port("array1", name="CT1.FC0"),
            ]
        )

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}
        assert [list(port) for port in result["array1"]] == [["CT0.FC0"], ["CT1.FC0"]]
        assert [list(port) for port in result["array2"]] == [["CT0.FC1"]]
        assert result["array2"][0]["CT0.FC1"]["wwn"] == "21000024FF000002"

    def test_port_with_two_array_references_is_filed_under_the_first(
        self, pure1_client
    ):
        port = _make_port(
            arrays=[
                sdk_model("FixedReferenceFqdn", name="array1"),
                sdk_model("FixedReferenceFqdn", name="array2"),
            ]
        )
        pure1_client.get_ports.return_value = valid_response([port])

        result = generate_ports_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [PORT0]}

    @pytest.mark.parametrize(
        "array, name",
        [
            pytest.param("array1", "", id="empty name"),
            pytest.param("", "CT0.FC0", id="empty array name"),
        ],
    )
    def test_port_with_an_empty_name_is_filed(self, pure1_client, array, name):
        """Only a name that Pure1 did not send leaves a port out."""
        module = ansible_module(array=None)
        pure1_client.get_ports.return_value = valid_response(
            [_make_port(array, name=name)]
        )

        result = generate_ports_dict(module, pure1_client)

        assert result == {array: [{name: PORT0["CT0.FC0"]}]}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_port_without_name, id="no name"),
            pytest.param(_port_without_arrays, id="no arrays"),
            pytest.param(_port_with_empty_arrays, id="empty arrays"),
            pytest.param(_port_with_null_array, id="null array"),
            pytest.param(_port_on_array_without_name, id="array without name"),
        ],
    )
    def test_port_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = ansible_module(array=None)
        pure1_client.get_ports.return_value = valid_response([broken(), _make_port()])

        result = generate_ports_dict(module, pure1_client)

        assert result == {"array1": [PORT0]}
        module.warn.assert_called_once_with(
            "Ports that Pure1 returned without a name or without an array "
            "are left out of the result: 1"
        )

    def test_ports_left_out_give_one_warning(self, pure1_client):
        module = ansible_module(array="array1")
        pure1_client.get_ports.return_value = valid_response(
            [_port_without_name(), _make_port(), _port_without_arrays()]
        )

        result = generate_ports_dict(module, pure1_client)

        assert result == {"array1": [PORT0]}
        module.warn.assert_called_once_with(
            "Ports that Pure1 returned without a name or without an array "
            "are left out of the result: 2"
        )


class TestPure1PortsMain:
    """Tests for the pure1_ports main() flow."""

    def test_all_ports_are_returned(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([_make_port()])

        # array is left to the argument spec
        module = run_main(pure1_ports)

        pure1_client.get_ports.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_ports={
                "ports": {
                    "array1": [
                        {
                            "CT0.FC0": {
                                "iqn": None,
                                "nqn": None,
                                "wwn": "21000024FF000001",
                                "portal": None,
                                "failover": None,
                            }
                        }
                    ]
                }
            },
        )

    def test_ports_of_named_array_are_returned(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([_make_port("myarray")])

        module = run_main(pure1_ports, array="myarray")

        pure1_client.get_ports.assert_called_once_with(filter="arrays.name='myarray'")
        ports = module.exit_json.call_args[1]["pure1_ports"]["ports"]
        assert list(ports) == ["myarray"]
        assert list(ports["myarray"][0]) == ["CT0.FC0"]

    def test_ports_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([_make_port()])

        module = run_main(pure1_ports, check_mode=True)

        pure1_client.get_ports.assert_called_once_with()
        ports = module.exit_json.call_args[1]["pure1_ports"]["ports"]
        assert ports["array1"][0]["CT0.FC0"]["wwn"] == "21000024FF000001"

    def test_no_ports_gives_an_empty_result(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response([])

        module = run_main(pure1_ports, array="array1")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_ports={"ports": {}}
        )

    def test_ports_that_are_all_filed_give_no_warning(self, pure1_client):
        pure1_client.get_ports.return_value = valid_response(
            [_make_port(), _make_port(name="CT1.FC0")]
        )

        module = run_main(pure1_ports, array="array1")

        module.warn.assert_not_called()
        ports = module.exit_json.call_args[1]["pure1_ports"]["ports"]
        assert [list(port) for port in ports["array1"]] == [["CT0.FC0"], ["CT1.FC0"]]

    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_ports.return_value = error_response(403, "Access denied")

        module = run_main(pure1_ports)

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
                "Pure1 request get_ports failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "Pure1 request get_ports failed (HTTP 400): Illegal filter "
                "format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request get_ports failed (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_ports failed: PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request get_ports failed: "
                "TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, params, attribute, answer, expected
    ):
        setattr(pure1_client.get_ports, attribute, answer)

        module = run_main(pure1_ports, **params)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
