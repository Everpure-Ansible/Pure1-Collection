# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_ports module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from plugins.modules import pure1_ports
from plugins.modules.pure1_ports import generate_ports_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


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

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_ports reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_ports.return_value = error_response(403, "Access denied")

        module = run_main(pure1_ports)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message
