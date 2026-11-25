# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_hardware module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import time
from unittest.mock import call

import pytest
import yaml
from ansible.template import Templar
from pypureclient import ErrorResponse, PureError

try:
    from ansible.template import trust_as_template
except ImportError:
    # ansible-core before 2.19 templates every string
    def trust_as_template(value):
        return value


from plugins.modules import pure1_hardware
from plugins.modules.pure1_hardware import name_order
from tests.unit.conftest import (
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# 2021-02-03 13:04:05 UTC, and a day and a second before it
NEWER = 1612357445000
OLDER = 1612271044000
UNHEALTHY_FILTER = "not(status=('ok','healthy','not_installed','unused','identifying'))"
EMPTY = {"hardware": {}, "arrays": {}, "unhealthy": [], "identifying": []}


def _component(name="CT0", array="array1", reference=None, **fields):
    """Return a hardware component; reference holds more fields of its array."""
    values = dict(name=name, arrays=[array_reference(array, **(reference or {}))])
    values.update(fields)
    return sdk_model("Hardware", **values)


def _connector(name="CH1.FM1.ETH1", array="array1", reference=None, **fields):
    values = dict(name=name, arrays=[array_reference(array, **(reference or {}))])
    values.update(fields)
    return sdk_model("HardwareConnector", **values)


def _row(**fields):
    """Return what the result holds for a component, every unset key null."""
    row = dict(
        id=None,
        type=None,
        status=None,
        details=None,
        model=None,
        serial=None,
        slot=None,
        speed=None,
        temperature=None,
        voltage=None,
        identify_enabled=None,
        as_of=None,
        as_of_epoch=None,
    )
    row.update(fields)
    return row


def _summary(**fields):
    summary = dict(
        id=None, fqdn=None, components=1, by_status={}, as_of=None, as_of_epoch=None
    )
    summary.update(fields)
    return summary


def _known_arrays(pure1_client, *names):
    """Answer the check of get_pure1 and then the request that resolves names."""
    pure1_client.get_arrays.side_effect = [
        valid_response(),
        valid_response([sdk_model("Array", name=name) for name in names]),
    ]


def _result(module):
    return module.exit_json.call_args[1]["pure1_hardware"]


def _typical_components():
    """Components of a FlashArray and a FlashBlade, in no order"""
    array1 = dict(id="array-id-1", fqdn="array1.example.com")
    array2 = dict(id="array-id-2")
    return [
        _component(
            "CH1.FB10",
            "array2",
            array2,
            id="blade-id-10",
            type="flash_blade",
            status="critical",
            details="Blade failed",
            model="FB-17TB",
            serial="SERIAL10",
            slot=10,
            temperature=31,
            identify_enabled=True,
            as_of=NEWER,
        ),
        _component(
            "CH0.BAY10",
            "array1",
            array1,
            id="bay-id-10",
            type="drive_bay",
            status="ok",
            model="DFSC1",
            serial="SERIAL1",
            identify_enabled=False,
            as_of=NEWER,
        ),
        _component(
            "CT0.ETH4",
            "array1",
            array1,
            type="eth_port",
            status="ok",
            slot=0,
            speed=25000000000,
            identify_enabled=False,
            as_of=OLDER,
        ),
        _component(
            "CH0.BAY2",
            "array1",
            array1,
            type="drive_bay",
            status="not_installed",
            identify_enabled=False,
            as_of=NEWER,
        ),
        _component(
            "CH0.PWR0",
            "array1",
            array1,
            id="power-id-0",
            type="power_supply",
            status="ok",
            model="PS-1200",
            serial="SERIAL2",
            voltage=239,
            identify_enabled=False,
            as_of=NEWER,
        ),
        _component(
            "CH1.FB2",
            "array2",
            array2,
            type="flash_blade",
            status="unused",
            slot=2,
            identify_enabled=False,
            as_of=NEWER,
        ),
    ]


NEWER_TEXT = "2021-02-03 13:04:05 UTC"
TYPICAL_RESULT = {
    "hardware": {
        "array1": {
            "CH0.BAY2": _row(
                type="drive_bay",
                status="not_installed",
                identify_enabled=False,
                as_of=NEWER_TEXT,
                as_of_epoch=NEWER,
            ),
            "CH0.BAY10": _row(
                id="bay-id-10",
                type="drive_bay",
                status="ok",
                model="DFSC1",
                serial="SERIAL1",
                identify_enabled=False,
                as_of=NEWER_TEXT,
                as_of_epoch=NEWER,
            ),
            "CH0.PWR0": _row(
                id="power-id-0",
                type="power_supply",
                status="ok",
                model="PS-1200",
                serial="SERIAL2",
                voltage=239,
                identify_enabled=False,
                as_of=NEWER_TEXT,
                as_of_epoch=NEWER,
            ),
            "CT0.ETH4": _row(
                type="eth_port",
                status="ok",
                slot=0,
                speed=25000000000,
                identify_enabled=False,
                as_of="2021-02-02 13:04:04 UTC",
                as_of_epoch=OLDER,
            ),
        },
        "array2": {
            "CH1.FB2": _row(
                type="flash_blade",
                status="unused",
                slot=2,
                identify_enabled=False,
                as_of=NEWER_TEXT,
                as_of_epoch=NEWER,
            ),
            "CH1.FB10": _row(
                id="blade-id-10",
                type="flash_blade",
                status="critical",
                details="Blade failed",
                model="FB-17TB",
                serial="SERIAL10",
                slot=10,
                temperature=31,
                identify_enabled=True,
                as_of=NEWER_TEXT,
                as_of_epoch=NEWER,
            ),
        },
    },
    "arrays": {
        "array1": {
            "id": "array-id-1",
            "fqdn": "array1.example.com",
            "components": 4,
            "by_status": {"not_installed": 1, "ok": 3},
            "as_of": "2021-02-02 13:04:04 UTC",
            "as_of_epoch": OLDER,
        },
        "array2": {
            "id": "array-id-2",
            "fqdn": None,
            "components": 2,
            "by_status": {"critical": 1, "unused": 1},
            "as_of": NEWER_TEXT,
            "as_of_epoch": NEWER,
        },
    },
    "unhealthy": [
        {
            "array": "array2",
            "name": "CH1.FB10",
            "type": "flash_blade",
            "status": "critical",
            "details": "Blade failed",
            "model": "FB-17TB",
            "serial": "SERIAL10",
        }
    ],
    "identifying": [
        {
            "array": "array2",
            "name": "CH1.FB10",
            "type": "flash_blade",
            "status": "critical",
        }
    ],
}


class TestMain:
    """The whole result of main()."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_typical_answer(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(_typical_components())

        module = run_main(pure1_hardware)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_hardware=TYPICAL_RESULT
        )
        module.warn.assert_not_called()
        # A default run costs the check of get_pure1 and one listing
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_hardware.assert_called_once_with(filter=None)
        pure1_client.get_hardware_connectors.assert_not_called()

    def test_check_mode_returns_the_same(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(_typical_components())

        module = run_main(pure1_hardware, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_hardware=TYPICAL_RESULT
        )
        pure1_client.get_hardware.assert_called_once_with(filter=None)

    def test_result_is_in_the_order_of_the_names(self, pure1_client):
        """Pure1 sorts nothing: the module does, numbers as numbers."""
        pure1_client.get_hardware.return_value = valid_response(_typical_components())

        result = _result(run_main(pure1_hardware))

        assert list(result["hardware"]) == ["array1", "array2"]
        assert list(result["arrays"]) == ["array1", "array2"]
        assert list(result["hardware"]["array1"]) == [
            "CH0.BAY2",
            "CH0.BAY10",
            "CH0.PWR0",
            "CT0.ETH4",
        ]
        assert list(result["hardware"]["array2"]) == ["CH1.FB2", "CH1.FB10"]
        assert list(result["arrays"]["array1"]["by_status"]) == ["not_installed", "ok"]

    def test_statuses_of_the_summary_are_in_their_own_order(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", status="unused"),
                _component("CT1", status="ok"),
                _component("CT2", status="critical"),
                _component("CT3", status="ok"),
            ]
        )

        result = _result(run_main(pure1_hardware))

        assert result["arrays"]["array1"]["by_status"] == {
            "critical": 1,
            "ok": 2,
            "unused": 1,
        }
        assert list(result["arrays"]["array1"]["by_status"]) == [
            "critical",
            "ok",
            "unused",
        ]

    def test_lists_follow_the_order_of_hardware(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CH1.FB10", "array2", status="critical"),
                _component("CH1.FB2", "array2", status="identifying"),
                _component("CT1", "array1", status="unknown", identify_enabled=True),
                _component("CT0", "array10", status="critical"),
            ]
        )

        result = _result(run_main(pure1_hardware))

        assert [(entry["array"], entry["name"]) for entry in result["unhealthy"]] == [
            ("array1", "CT1"),
            ("array2", "CH1.FB10"),
            ("array10", "CT0"),
        ]
        assert [(entry["array"], entry["name"]) for entry in result["identifying"]] == [
            ("array1", "CT1"),
            ("array2", "CH1.FB2"),
        ]

    def test_no_component_gives_every_key_empty(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware)

        module.exit_json.assert_called_once_with(changed=False, pure1_hardware=EMPTY)
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()

    def test_component_with_only_its_name_and_array(self, pure1_client):
        """Every field Pure1 did not send is null, and nothing is counted."""
        pure1_client.get_hardware.return_value = valid_response([_component()])

        module = run_main(pure1_hardware)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_hardware={
                "hardware": {
                    "array1": {
                        "CT0": {
                            "id": None,
                            "type": None,
                            "status": None,
                            "details": None,
                            "model": None,
                            "serial": None,
                            "slot": None,
                            "speed": None,
                            "temperature": None,
                            "voltage": None,
                            "identify_enabled": None,
                            "as_of": None,
                            "as_of_epoch": None,
                        }
                    }
                },
                "arrays": {
                    "array1": {
                        "id": None,
                        "fqdn": None,
                        "components": 1,
                        "by_status": {},
                        "as_of": None,
                        "as_of_epoch": None,
                    }
                },
                "unhealthy": [],
                "identifying": [],
            },
        )
        module.warn.assert_not_called()

    def test_zero_is_a_value(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component(slot=0, speed=0, temperature=0, voltage=0, as_of=0)]
        )

        result = _result(run_main(pure1_hardware))

        assert result["hardware"]["array1"]["CT0"] == _row(
            slot=0,
            speed=0,
            temperature=0,
            voltage=0,
            as_of="1970-01-01 00:00:00 UTC",
            as_of_epoch=0,
        )
        assert result["arrays"]["array1"]["as_of_epoch"] == 0
        assert result["arrays"]["array1"]["as_of"] == "1970-01-01 00:00:00 UTC"

    def test_summary_takes_the_oldest_time_of_the_array(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", as_of=NEWER),
                _component("CT1", as_of=OLDER),
                _component("CT2"),
                _component("CT3", as_of=NEWER + 1),
                _component("CT0", "array2", as_of=NEWER),
            ]
        )

        result = _result(run_main(pure1_hardware))

        assert result["arrays"]["array1"] == _summary(
            components=4, as_of="2021-02-02 13:04:04 UTC", as_of_epoch=OLDER
        )
        assert result["arrays"]["array2"] == _summary(
            as_of=NEWER_TEXT, as_of_epoch=NEWER
        )

    def test_array_of_the_summary_comes_from_the_reference(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component(reference=dict(id="array-id-1", fqdn="array1.example.com")),
                _component("CT0", "array2", dict(id="array-id-2")),
                _component("CT0", "array3", dict(fqdn="array3.example.com")),
            ]
        )

        result = _result(run_main(pure1_hardware))

        assert result["arrays"] == {
            "array1": _summary(id="array-id-1", fqdn="array1.example.com"),
            "array2": _summary(id="array-id-2"),
            "array3": _summary(fqdn="array3.example.com"),
        }

    def test_array_of_the_summary_is_that_of_the_first_component(self, pure1_client):
        """The first in the order of the names, whatever the others carry."""
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT1"),
                _component("CT2", reference=dict(fqdn="other.example.com")),
                _component(
                    "CT0", reference=dict(id="array-id-1", fqdn="array1.example.com")
                ),
            ]
        )

        module = run_main(pure1_hardware)

        assert _result(module)["arrays"] == {
            "array1": _summary(id="array-id-1", fqdn="array1.example.com", components=3)
        }
        module.warn.assert_not_called()


class TestListing:
    """The listing is read unsorted, page by page."""

    def test_every_page_is_read_without_sort(self, pure1_client):
        components = [_component("CT{0}".format(number)) for number in range(5)]
        answers = paged_answers("HardwareGetResponse", components, page_size=2)
        pure1_client.get_hardware.side_effect = answers

        module = run_main(pure1_hardware)

        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "continuation_token": "page-4"},
        ]
        pure1_client.get_hardware.assert_called_once_with(filter=None)
        assert list(_result(module)["hardware"]["array1"]) == [
            "CT0",
            "CT1",
            "CT2",
            "CT3",
            "CT4",
        ]
        module.warn.assert_not_called()

    def test_filter_goes_with_every_page(self, pure1_client):
        components = [_component("CT{0}".format(number)) for number in range(3)]
        answers = paged_answers("HardwareGetResponse", components, page_size=2)
        pure1_client.get_hardware.side_effect = answers

        run_main(pure1_hardware, type=["controller"])

        assert answers.requests == [
            {"filter": "type='controller'"},
            {"filter": "type='controller'", "continuation_token": "page-2"},
        ]

    def test_fewer_components_than_pure1_counted_give_a_warning(self, pure1_client):
        components = [_component("CT{0}".format(number)) for number in range(5)]
        answers = paged_answers(
            "HardwareGetResponse", components, page_size=2, total_item_count=7
        )
        pure1_client.get_hardware.side_effect = answers

        module = run_main(pure1_hardware)

        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "continuation_token": "page-4"},
            {"filter": None, "offset": 5},
        ]
        module.warn.assert_called_once_with(
            "Pure1 returned 5 of 7 hardware components: the result may be incomplete."
        )
        assert _result(module)["arrays"]["array1"]["components"] == 5

    def test_listing_without_a_total_that_cannot_be_completed_warns(self, pure1_client):
        components = [_component("CT{0}".format(number)) for number in range(3)]
        answers = paged_answers(
            "HardwareGetResponse", components, total_item_count=None
        )

        def ignore_the_offset(**kwargs):
            kwargs.pop("offset", None)
            return answers(**kwargs)

        pure1_client.get_hardware.side_effect = ignore_the_offset

        module = run_main(pure1_hardware)

        module.warn.assert_called_once_with(
            "Pure1 returned 3 of an unknown number of hardware components: the "
            "result may be incomplete."
        )
        assert _result(module)["arrays"]["array1"]["components"] == 3


class TestOptions:
    """Each option and the filter it makes."""

    def test_array_is_resolved_and_filtered_as_pure1_spells_it(self, pure1_client):
        _known_arrays(pure1_client, "array1", "Array2")
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", "array1"),
                _component("CT0", "Array2", status="critical", identify_enabled=True),
            ]
        )

        module = run_main(pure1_hardware, array=["ARRAY1", "array2"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        pure1_client.get_hardware.assert_called_once_with(
            filter="arrays.name=('array1','Array2')"
        )
        result = _result(module)
        assert list(result["hardware"]) == ["array1", "Array2"]
        assert list(result["arrays"]) == ["array1", "Array2"]
        assert [entry["array"] for entry in result["unhealthy"]] == ["Array2"]
        assert [entry["array"] for entry in result["identifying"]] == ["Array2"]
        module.warn.assert_not_called()

    def test_array_that_pure1_does_not_know_fails_the_task(self, pure1_client):
        _known_arrays(pure1_client, "array1")

        module = run_main(pure1_hardware, array=["array1", "foo"])

        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")
        module.exit_json.assert_not_called()
        pure1_client.get_hardware.assert_not_called()

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param({"type": ["controller"]}, "type='controller'", id="one type"),
            pytest.param(
                {"type": ["controller", "Chassis"]},
                "type=('controller','Chassis')",
                id="two types",
            ),
            pytest.param({"type": []}, None, id="no type"),
            pytest.param(
                {"status": ["critical"]}, "status='critical'", id="one status"
            ),
            pytest.param(
                {"status": ["critical", "unused"]},
                "status=('critical','unused')",
                id="two statuses",
            ),
            pytest.param({"unhealthy_only": True}, UNHEALTHY_FILTER, id="unhealthy"),
            pytest.param({"unhealthy_only": False}, None, id="not only unhealthy"),
            pytest.param(
                {"type": ["eth_port", "fc_port"], "status": ["unused"]},
                "type=('eth_port','fc_port') and status='unused'",
                id="type and status",
            ),
            pytest.param(
                {"type": ["flash_blade"], "unhealthy_only": True},
                "type='flash_blade' and " + UNHEALTHY_FILTER,
                id="type and unhealthy",
            ),
            pytest.param(
                {"type": ["o'brien", "back\\slash"]},
                "type=('o\\'brien','back\\\\slash')",
                id="escaped",
            ),
            pytest.param({"connectors": True}, None, id="connectors"),
        ],
    )
    def test_selection_becomes_one_filter(self, pure1_client, params, expected):
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware, **params)

        pure1_client.get_hardware.assert_called_once_with(filter=expected)
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        module.exit_json.assert_called_once_with(changed=False, pure1_hardware=EMPTY)
        module.warn.assert_not_called()

    def test_array_joins_the_other_selections(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(
            pure1_hardware, array=["array1"], type=["eth_port"], status=["unused"]
        )

        pure1_client.get_hardware.assert_called_once_with(
            filter="arrays.name='array1' and type='eth_port' and status='unused'"
        )
        module.warn.assert_not_called()

    @pytest.mark.parametrize("unhealthy_only", [True, False])
    def test_status_with_unhealthy_only_is_refused(self, pure1_client, unhealthy_only):
        module = run_main(
            pure1_hardware, status=["critical"], unhealthy_only=unhealthy_only
        )

        module.fail_json.assert_called_once_with(
            msg="parameters are mutually exclusive: status|unhealthy_only"
        )
        pure1_client.get_hardware.assert_not_called()

    def test_value_that_is_no_boolean_is_refused(self, pure1_client):
        module = run_main(pure1_hardware, connectors="perhaps")

        module.fail_json.assert_called_once()
        # ansible-core names the type as <class 'str'> up to 2.18 and as str
        # from 2.19 on
        message = module.fail_json.call_args[1]["msg"]
        assert message.startswith("argument 'connectors' is of type ")
        assert (
            " and we were unable to convert to bool: "
            "The value 'perhaps' is not a valid boolean." in message
        )
        pure1_client.get_hardware.assert_not_called()

    def test_unknown_option_is_refused(self, pure1_client):
        module = run_main(pure1_hardware, sort=["name"])

        module.fail_json.assert_called_once()
        assert module.fail_json.call_args[1]["msg"].startswith(
            "Unsupported parameters for module: sort."
        )

    def test_unhealthy_only_keeps_to_the_rule_of_the_module(self, pure1_client):
        """Whatever Pure1 returns for the filter, only what the rule calls
        unhealthy is in the result, and an array left without one is absent."""
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", status="critical"),
                _component("CT1", status="OK"),
                _component("CT2"),
                _component("CT0", "array2", status="unused"),
            ]
        )

        module = run_main(pure1_hardware, unhealthy_only=True)

        assert _result(module) == {
            "hardware": {"array1": {"CT0": _row(status="critical")}},
            "arrays": {"array1": _summary(by_status={"critical": 1})},
            "unhealthy": [
                {
                    "array": "array1",
                    "name": "CT0",
                    "type": None,
                    "status": "critical",
                    "details": None,
                    "model": None,
                    "serial": None,
                }
            ],
            "identifying": [],
        }
        module.warn.assert_not_called()


class TestHealth:
    """Which components are unhealthy and which are identifying."""

    @pytest.mark.parametrize(
        "status",
        ["ok", "healthy", "not_installed", "unused", "identifying", "OK", "Healthy"],
    )
    def test_healthy_status_is_not_unhealthy(self, pure1_client, status):
        pure1_client.get_hardware.return_value = valid_response(
            [_component(status=status)]
        )

        result = _result(run_main(pure1_hardware))

        assert result["unhealthy"] == []
        assert result["arrays"]["array1"]["by_status"] == {status: 1}

    @pytest.mark.parametrize(
        "status",
        [
            "critical",
            "unhealthy",
            "unknown",
            "unrecognized",
            "unclaimed",
            "device_off",
            "a_status_of_tomorrow",
            "",
        ],
    )
    def test_any_other_status_is_unhealthy(self, pure1_client, status):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component(
                    status=status,
                    type="nvram_bay",
                    details="No power",
                    model="NVB",
                    serial="SERIAL3",
                )
            ]
        )

        result = _result(run_main(pure1_hardware))

        assert result["unhealthy"] == [
            {
                "array": "array1",
                "name": "CT0",
                "type": "nvram_bay",
                "status": status,
                "details": "No power",
                "model": "NVB",
                "serial": "SERIAL3",
            }
        ]
        assert result["identifying"] == []
        assert result["arrays"]["array1"]["by_status"] == {status: 1}

    def test_component_without_status_is_counted_in_components_only(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CT0"), _component("CT1", status="ok")]
        )

        result = _result(run_main(pure1_hardware))

        assert result["unhealthy"] == []
        assert result["arrays"]["array1"] == _summary(components=2, by_status={"ok": 1})

    @pytest.mark.parametrize(
        "fields, expected",
        [
            pytest.param({"identify_enabled": True, "status": "ok"}, "ok", id="light"),
            pytest.param({"identify_enabled": True}, None, id="light, no status"),
            pytest.param({"status": "identifying"}, "identifying", id="status"),
            pytest.param(
                {"identify_enabled": False, "status": "Identifying"},
                "Identifying",
                id="status in another case",
            ),
        ],
    )
    def test_component_with_its_light_lit_is_identifying(
        self, pure1_client, fields, expected
    ):
        pure1_client.get_hardware.return_value = valid_response(
            [_component(type="drive_bay", **fields), _component("CT1", status="ok")]
        )

        result = _result(run_main(pure1_hardware))

        assert result["identifying"] == [
            {"array": "array1", "name": "CT0", "type": "drive_bay", "status": expected}
        ]
        assert result["unhealthy"] == []

    def test_component_with_its_light_off_is_not_identifying(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component(identify_enabled=False, status="critical")]
        )

        result = _result(run_main(pure1_hardware))

        assert result["identifying"] == []
        assert len(result["unhealthy"]) == 1


def _without_name():
    return sdk_model("Hardware", arrays=[array_reference("array1")], status="ok")


def _without_arrays():
    return sdk_model("Hardware", name="CT1", status="ok")


def _with_empty_arrays():
    # The SDK reads an answer without validating it, so an empty list arrives
    # as it is; the constructor of the model refuses one
    component = _component("CT1")
    del component.arrays[:]
    return component


def _with_null_array():
    component = _component("CT1")
    component.arrays[0] = None
    return component


def _on_array_without_name():
    return sdk_model(
        "Hardware",
        name="CT1",
        arrays=[sdk_model("FixedReferenceFqdn", id="array-id-1")],
    )


class TestFiling:
    """Components that cannot be filed, and names that come twice."""

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_without_name, id="no name"),
            pytest.param(_without_arrays, id="no arrays"),
            pytest.param(_with_empty_arrays, id="empty arrays"),
            pytest.param(_with_null_array, id="null array"),
            pytest.param(_on_array_without_name, id="array without name"),
        ],
    )
    def test_component_without_identity_is_left_out(self, pure1_client, broken):
        pure1_client.get_hardware.return_value = valid_response(
            [broken(), _component(status="ok")]
        )

        module = run_main(pure1_hardware)

        assert _result(module) == {
            "hardware": {"array1": {"CT0": _row(status="ok")}},
            "arrays": {"array1": _summary(by_status={"ok": 1})},
            "unhealthy": [],
            "identifying": [],
        }
        module.warn.assert_called_once_with(
            "Hardware components that Pure1 returned without a name or without "
            "an array are left out of the result: 1."
        )

    def test_components_left_out_give_one_warning(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_without_name(), _component(), _without_arrays(), _with_null_array()]
        )

        module = run_main(pure1_hardware)

        assert list(_result(module)["hardware"]["array1"]) == ["CT0"]
        module.warn.assert_called_once_with(
            "Hardware components that Pure1 returned without a name or without "
            "an array are left out of the result: 3."
        )

    def test_only_components_without_identity_give_an_empty_result(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response([_without_name()])

        module = run_main(pure1_hardware, connectors=True)

        assert _result(module) == EMPTY
        pure1_client.get_hardware_connectors.assert_not_called()

    def test_first_of_two_components_with_one_name_is_kept(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", status="ok", serial="FIRST"),
                _component("CT1", status="ok"),
                _component("CT0", status="critical", serial="SECOND"),
                _component("CT0", status="critical", serial="THIRD"),
                _component("CT0", "array2", status="ok", serial="OTHER"),
            ]
        )

        module = run_main(pure1_hardware)

        result = _result(module)
        assert result["hardware"] == {
            "array1": {
                "CT0": _row(status="ok", serial="FIRST"),
                "CT1": _row(status="ok"),
            },
            "array2": {"CT0": _row(status="ok", serial="OTHER")},
        }
        assert result["unhealthy"] == []
        assert result["arrays"]["array1"]["components"] == 2
        module.warn.assert_called_once_with(
            "Hardware components that Pure1 returned with the name of another "
            "one of the same array are left out of the result: 2."
        )

    def test_second_array_of_one_name_is_left_out(self, pure1_client):
        """Two arrays can carry one name: the result is that of the first,
        not a mixture of the two."""
        first = dict(id="array-id-1", fqdn="first.example.com")
        second = dict(id="array-id-2", fqdn="second.example.com")
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0", reference=first, status="ok", serial="FIRST"),
                _component("CT0", reference=second, status="critical"),
                _component("SH0.BAY1", reference=second, status="ok"),
                _component("CH0.BAY1", reference=first, status="ok"),
                _component("CT0", "array2", second, status="ok"),
            ]
        )

        module = run_main(pure1_hardware)

        assert _result(module) == {
            "hardware": {
                "array1": {
                    "CH0.BAY1": _row(status="ok"),
                    "CT0": _row(status="ok", serial="FIRST"),
                },
                "array2": {"CT0": _row(status="ok")},
            },
            "arrays": {
                "array1": _summary(
                    id="array-id-1",
                    fqdn="first.example.com",
                    components=2,
                    by_status={"ok": 2},
                ),
                "array2": _summary(
                    id="array-id-2", fqdn="second.example.com", by_status={"ok": 1}
                ),
            },
            "unhealthy": [],
            "identifying": [],
        }
        module.warn.assert_called_once_with(
            "Hardware components of arrays that carry the name of another array "
            "are left out of the result: 2."
        )

    def test_component_without_the_id_of_its_array_is_filed_by_name(self, pure1_client):
        """Only two ids that differ show two arrays of one name."""
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CT0"),
                _component("CT1", reference=dict(id="array-id-1")),
                _component("CT2"),
                _component("CT3", reference=dict(id="array-id-1")),
            ]
        )

        module = run_main(pure1_hardware)

        assert list(_result(module)["hardware"]["array1"]) == [
            "CT0",
            "CT1",
            "CT2",
            "CT3",
        ]
        module.warn.assert_not_called()

    def test_names_differ_by_case(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("ct0"), _component("CT0")]
        )

        module = run_main(pure1_hardware)

        assert list(_result(module)["hardware"]["array1"]) == ["CT0", "ct0"]
        module.warn.assert_not_called()

    def test_component_with_two_arrays_is_filed_under_the_first(self, pure1_client):
        component = _component(reference=dict(id="array-id-1"))
        component.arrays.append(array_reference("array2"))
        pure1_client.get_hardware.return_value = valid_response([component])

        result = _result(run_main(pure1_hardware))

        assert result["hardware"] == {"array1": {"CT0": _row()}}
        assert result["arrays"] == {"array1": _summary(id="array-id-1")}

    def test_both_warnings(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component(), _without_name(), _component()]
        )

        module = run_main(pure1_hardware)

        assert module.warn.call_args_list == [
            call(
                "Hardware components that Pure1 returned without a name or "
                "without an array are left out of the result: 1."
            ),
            call(
                "Hardware components that Pure1 returned with the name of "
                "another one of the same array are left out of the result: 1."
            ),
        ]

    def test_all_three_warnings(self, pure1_client):
        first = dict(id="array-id-1")
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component(reference=first),
                _without_name(),
                _component(reference=dict(id="array-id-2")),
                _component(reference=first),
            ]
        )

        module = run_main(pure1_hardware)

        assert module.warn.call_args_list == [
            call(
                "Hardware components that Pure1 returned without a name or "
                "without an array are left out of the result: 1."
            ),
            call(
                "Hardware components of arrays that carry the name of another "
                "array are left out of the result: 1."
            ),
            call(
                "Hardware components that Pure1 returned with the name of "
                "another one of the same array are left out of the result: 1."
            ),
        ]


class TestAbsentArrays:
    """A named array for which Pure1 returns no component."""

    def test_named_array_without_components_is_absent_with_a_warning(
        self, pure1_client
    ):
        _known_arrays(pure1_client, "array1", "Array2")
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CT0", "array1")]
        )

        module = run_main(pure1_hardware, array=["array1", "ARRAY2"])

        result = _result(module)
        assert list(result["hardware"]) == ["array1"]
        assert list(result["arrays"]) == ["array1"]
        # the name as the task gave it
        module.warn.assert_called_once_with(
            "Pure1 returned no hardware component for array ARRAY2, which is "
            "absent from the result."
        )

    def test_several_arrays_without_components_give_one_warning(self, pure1_client):
        _known_arrays(pure1_client, "array1", "array2", "array3")
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CT0", "array2")]
        )

        module = run_main(
            pure1_hardware, array=["array3", "array2", "Array1", "ARRAY3", "array1"]
        )

        assert list(_result(module)["hardware"]) == ["array2"]
        module.warn.assert_called_once_with(
            "Pure1 returned no hardware component for arrays array3, Array1, "
            "which are absent from the result."
        )

    def test_no_component_at_all_for_the_named_array(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware, array=["array1"], connectors=True)

        module.exit_json.assert_called_once_with(changed=False, pure1_hardware=EMPTY)
        module.warn.assert_called_once_with(
            "Pure1 returned no hardware component for array array1, which is "
            "absent from the result."
        )
        pure1_client.get_hardware_connectors.assert_not_called()

    def test_empty_lists_are_no_selection(self, pure1_client):
        """With them a named array without components still gives the warning."""
        _known_arrays(pure1_client, "array1")
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware, array=["array1"], type=[], status=[])

        pure1_client.get_hardware.assert_called_once_with(filter="arrays.name='array1'")
        module.warn.assert_called_once_with(
            "Pure1 returned no hardware component for array array1, which is "
            "absent from the result."
        )

    def test_array_found_in_another_case_is_not_absent(self, pure1_client):
        _known_arrays(pure1_client, "Array1")
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CT0", "Array1")]
        )

        module = run_main(pure1_hardware, array=["ARRAY1"])

        assert list(_result(module)["hardware"]) == ["Array1"]
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"type": ["fc_port"]}, id="type"),
            pytest.param({"status": ["critical"]}, id="status"),
            pytest.param({"unhealthy_only": True}, id="unhealthy_only"),
        ],
    )
    def test_no_warning_when_the_task_selects_components(self, pure1_client, params):
        """An array without a matching component is what the task asked for."""
        _known_arrays(pure1_client, "array1")
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware, array=["array1"], **params)

        module.exit_json.assert_called_once_with(changed=False, pure1_hardware=EMPTY)
        module.warn.assert_not_called()

    def test_no_warning_without_the_array_option(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware)

        module.warn.assert_not_called()


CONNECTOR = {
    "connector_type": "QSFP",
    "lane_speed": 25000000000,
    "port_count": 1,
    "transceiver_type": "100GBASE-CR4",
}


def _typical_connector(name="CH1.FM1.ETH1", array="array1"):
    return _connector(
        name,
        array,
        id="connector-id",
        as_of=NEWER,
        connector_type="QSFP",
        lane_speed=25000000000,
        port_count=1,
        transceiver_type="100GBASE-CR4",
    )


class TestConnectors:
    """The connectors option."""

    def test_connector_is_joined_by_array_and_name(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CH1.FM1.ETH1", "array1", type="eth_port"),
                _component("CH1.FM1.ETH1", "array2", type="eth_port"),
                _component("CH1.FM1.ETH2", "array1", type="eth_port"),
                _component("CH1", "array1", type="chassis"),
            ]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [
                _typical_connector("CH1.FM1.ETH1", "array1"),
                # a port that is not among the components
                _typical_connector("CH1.FM2.ETH1", "array1"),
                # an array that is not among the components
                _typical_connector("CH1.FM1.ETH1", "array3"),
            ]
        )

        module = run_main(pure1_hardware, connectors=True)

        # One more request than a default run
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_hardware.assert_called_once_with(filter=None)
        pure1_client.get_hardware_connectors.assert_called_once_with(filter=None)
        assert _result(module)["hardware"] == {
            "array1": {
                "CH1": _row(type="chassis", connector=None),
                "CH1.FM1.ETH1": _row(type="eth_port", connector=CONNECTOR),
                "CH1.FM1.ETH2": _row(type="eth_port", connector=None),
            },
            "array2": {"CH1.FM1.ETH1": _row(type="eth_port", connector=None)},
        }
        module.warn.assert_not_called()

    def test_whole_result_with_connectors_in_check_mode(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1", type="eth_port", status="healthy")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [_typical_connector()]
        )

        module = run_main(pure1_hardware, check_mode=True, connectors=True)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_hardware={
                "hardware": {
                    "array1": {
                        "CH1.FM1.ETH1": {
                            "id": None,
                            "type": "eth_port",
                            "status": "healthy",
                            "details": None,
                            "model": None,
                            "serial": None,
                            "slot": None,
                            "speed": None,
                            "temperature": None,
                            "voltage": None,
                            "identify_enabled": None,
                            "as_of": None,
                            "as_of_epoch": None,
                            "connector": {
                                "connector_type": "QSFP",
                                "lane_speed": 25000000000,
                                "port_count": 1,
                                "transceiver_type": "100GBASE-CR4",
                            },
                        }
                    }
                },
                "arrays": {"array1": _summary(by_status={"healthy": 1})},
                "unhealthy": [],
                "identifying": [],
            },
        )

    def test_without_the_option_no_component_has_the_key(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1")]
        )

        module = run_main(pure1_hardware, connectors=False)

        assert "connector" not in _result(module)["hardware"]["array1"]["CH1.FM1.ETH1"]
        pure1_client.get_hardware_connectors.assert_not_called()

    def test_connector_with_only_its_name_and_array(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [_connector()]
        )

        result = _result(run_main(pure1_hardware, connectors=True))

        assert result["hardware"]["array1"]["CH1.FM1.ETH1"] == _row(
            connector={
                "connector_type": None,
                "lane_speed": None,
                "port_count": None,
                "transceiver_type": None,
            }
        )

    def test_zero_is_a_value_of_a_connector(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [_connector(connector_type="RJ-45", lane_speed=0, port_count=0)]
        )

        result = _result(run_main(pure1_hardware, connectors=True))

        assert result["hardware"]["array1"]["CH1.FM1.ETH1"]["connector"] == {
            "connector_type": "RJ-45",
            "lane_speed": 0,
            "port_count": 0,
            "transceiver_type": None,
        }

    def test_connectors_are_read_for_the_named_arrays_only(self, pure1_client):
        _known_arrays(pure1_client, "array1", "Array2")
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1", "Array2")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [_typical_connector("CH1.FM1.ETH1", "Array2")]
        )

        module = run_main(
            pure1_hardware,
            array=["array1", "array2"],
            type=["eth_port"],
            connectors=True,
        )

        pure1_client.get_hardware.assert_called_once_with(
            filter="arrays.name=('array1','Array2') and type='eth_port'"
        )
        # the selection of components does not go into this request
        pure1_client.get_hardware_connectors.assert_called_once_with(
            filter="arrays.name=('array1','Array2')"
        )
        assert _result(module)["hardware"] == {
            "Array2": {"CH1.FM1.ETH1": _row(connector=CONNECTOR)}
        }

    def test_no_request_for_connectors_without_components(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response([])

        module = run_main(pure1_hardware, connectors=True)

        module.exit_json.assert_called_once_with(changed=False, pure1_hardware=EMPTY)
        pure1_client.get_hardware_connectors.assert_not_called()

    def test_every_page_of_connectors_is_read(self, pure1_client):
        ports = ["CH1.FM1.ETH{0}".format(number) for number in range(1, 4)]
        pure1_client.get_hardware.return_value = valid_response(
            [_component(port) for port in ports]
        )
        answers = paged_answers(
            "HardwareConnectorGetResponse",
            [_typical_connector(port) for port in ports],
            page_size=2,
        )
        pure1_client.get_hardware_connectors.side_effect = answers

        module = run_main(pure1_hardware, connectors=True)

        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
        ]
        assert _result(module)["hardware"]["array1"] == {
            port: _row(connector=CONNECTOR) for port in ports
        }

    def test_fewer_connectors_than_pure1_counted_give_a_warning(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1")]
        )
        answers = paged_answers(
            "HardwareConnectorGetResponse", [_typical_connector()], total_item_count=3
        )
        pure1_client.get_hardware_connectors.side_effect = answers

        module = run_main(pure1_hardware, connectors=True)

        # the paging keywords of the helper reach Pure1
        assert answers.requests == [{"filter": None}, {"filter": None, "offset": 1}]
        module.warn.assert_called_once_with(
            "Pure1 returned 1 of 3 hardware connectors: the result may be incomplete."
        )
        assert _result(module)["hardware"]["array1"]["CH1.FM1.ETH1"] == _row(
            connector=CONNECTOR
        )

    def test_no_connector_at_all(self, pure1_client):
        """What a FlashArray gives: components, and no connector in Pure1."""
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CT0.ETH4", type="eth_port")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response([])

        module = run_main(pure1_hardware, connectors=True)

        pure1_client.get_hardware_connectors.assert_called_once_with(filter=None)
        assert _result(module)["hardware"] == {
            "array1": {"CT0.ETH4": _row(type="eth_port", connector=None)}
        }
        module.warn.assert_not_called()

    def test_connectors_of_a_second_array_of_one_name_are_left_out(self, pure1_client):
        """The connectors are those of the array whose components are kept,
        whichever array the listing of connectors names first."""
        first = dict(id="array-id-1")
        second = dict(id="array-id-2")
        pure1_client.get_hardware.return_value = valid_response(
            [
                _component("CH1.FM1.ETH1", reference=first),
                _component("CH1.FM1.ETH2", reference=first),
                _component("CH1.FM1.ETH1", reference=second),
            ]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [
                _connector("CH1.FM1.ETH1", reference=second, connector_type="RJ-45"),
                _connector("CH1.FM1.ETH2", reference=second, connector_type="RJ-45"),
                _connector("CH1.FM1.ETH1", reference=first, connector_type="QSFP"),
            ]
        )

        module = run_main(pure1_hardware, connectors=True)

        ports = _result(module)["hardware"]["array1"]
        assert ports["CH1.FM1.ETH1"]["connector"] == {
            "connector_type": "QSFP",
            "lane_speed": None,
            "port_count": None,
            "transceiver_type": None,
        }
        assert ports["CH1.FM1.ETH2"]["connector"] is None
        assert module.warn.call_args_list == [
            call(
                "Hardware components of arrays that carry the name of another "
                "array are left out of the result: 1."
            ),
            call(
                "Hardware connectors of arrays that carry the name of another "
                "array are left out of the result: 2."
            ),
        ]

    def test_connectors_that_cannot_be_filed_give_warnings(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [
                sdk_model("HardwareConnector", name="CH1.FM1.ETH1"),
                sdk_model("HardwareConnector", arrays=[array_reference("array1")]),
                _typical_connector(),
                _connector(connector_type="RJ-45"),
            ]
        )

        module = run_main(pure1_hardware, connectors=True)

        # the first of the two with one name
        assert _result(module)["hardware"]["array1"]["CH1.FM1.ETH1"] == _row(
            connector=CONNECTOR
        )
        assert module.warn.call_args_list == [
            call(
                "Hardware connectors that Pure1 returned without a name or "
                "without an array are left out of the result: 2."
            ),
            call(
                "Hardware connectors that Pure1 returned with the name of "
                "another one of the same array are left out of the result: 1."
            ),
        ]

    def test_unhealthy_only_with_connectors(self, pure1_client):
        pure1_client.get_hardware.return_value = valid_response(
            [_component("CH1.FM1.ETH1", status="critical")]
        )
        pure1_client.get_hardware_connectors.return_value = valid_response(
            [_typical_connector()]
        )

        module = run_main(pure1_hardware, unhealthy_only=True, connectors=True)

        pure1_client.get_hardware.assert_called_once_with(filter=UNHEALTHY_FILTER)
        pure1_client.get_hardware_connectors.assert_called_once_with(filter=None)
        assert _result(module)["hardware"]["array1"]["CH1.FM1.ETH1"] == _row(
            status="critical", connector=CONNECTOR
        )


# The message of each failure, {0} for the client method
FAILED_REQUESTS = [
    pytest.param(
        "return_value",
        error_response(403, "Access denied"),
        "Pure1 request {0} failed (HTTP 403): Access denied",
        id="error answer",
    ),
    pytest.param(
        "return_value",
        error_response(400, "Illegal filter format", "Unexpected input at character 9"),
        "Pure1 request {0} failed (HTTP 400): Illegal filter format: "
        "Unexpected input at character 9",
        id="error answer with context",
    ),
    pytest.param(
        "return_value",
        ErrorResponse(503, [], {}),
        "Pure1 request {0} failed (HTTP 503): Service Unavailable",
        id="error answer without message",
    ),
    pytest.param(
        "side_effect",
        PureError("token refused"),
        "Pure1 request {0} failed: PureError: token refused",
        id="PureError",
    ),
    pytest.param(
        "side_effect",
        TimeoutError("The read operation timed out"),
        "Pure1 request {0} failed: TimeoutError: The read operation timed out",
        id="transport error",
    ),
]


class TestFailures:
    """A request that fails ends the task with one line."""

    @pytest.mark.parametrize("attribute, answer, expected", FAILED_REQUESTS)
    def test_failed_listing_of_hardware(
        self, pure1_client, attribute, answer, expected
    ):
        setattr(pure1_client.get_hardware, attribute, answer)

        module = run_main(pure1_hardware, connectors=True)

        module.fail_json.assert_called_once_with(msg=expected.format("get_hardware"))
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        pure1_client.get_hardware_connectors.assert_not_called()

    @pytest.mark.parametrize("attribute, answer, expected", FAILED_REQUESTS)
    def test_failed_listing_of_connectors(
        self, pure1_client, attribute, answer, expected
    ):
        pure1_client.get_hardware.return_value = valid_response([_component()])
        setattr(pure1_client.get_hardware_connectors, attribute, answer)

        module = run_main(pure1_hardware, connectors=True)

        module.fail_json.assert_called_once_with(
            msg=expected.format("get_hardware_connectors")
        )
        module.exit_json.assert_not_called()

    def test_failed_later_page(self, pure1_client):
        components = [_component("CT{0}".format(number)) for number in range(5)]
        answers = paged_answers(
            "HardwareGetResponse", components, page_size=2, total_item_count=7
        )

        def refuse_the_offset(**kwargs):
            if "offset" in kwargs:
                return error_response(500, "Internal server error")
            return answers(**kwargs)

        pure1_client.get_hardware.side_effect = refuse_the_offset

        module = run_main(pure1_hardware)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_hardware failed (HTTP 500): Internal server error"
        )
        module.exit_json.assert_not_called()

    def test_failed_request_for_the_named_arrays(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(500, "Internal server error"),
        ]

        module = run_main(pure1_hardware, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 500): Internal server error"
        )
        pure1_client.get_hardware.assert_not_called()

    def test_empty_array_name_fails_before_any_request(self, pure1_client):
        module = run_main(pure1_hardware, array=["array1", " "])

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_hardware.assert_not_called()


class TestExamples:
    """What the examples compute."""

    @pytest.mark.usefixtures("host_west_of_utc")
    @pytest.mark.parametrize(
        "hours, expected",
        [
            pytest.param(23, False, id="an hour less than a day"),
            pytest.param(25, True, id="an hour more than a day"),
        ],
    )
    def test_data_older_than_a_day_is_found_on_a_host_that_is_not_in_utc(
        self, hours, expected
    ):
        """The when of the example, as ansible-core evaluates it."""
        task = next(
            task
            for task in yaml.safe_load(pure1_hardware.EXAMPLES)
            if task["name"] == "Show the arrays whose hardware data is older than a day"
        )
        as_of = int(time.time() * 1000) - hours * 3600 * 1000
        templar = Templar(
            loader=None, variables={"item": {"value": {"as_of_epoch": as_of}}}
        )

        older = templar.template(trust_as_template("{{ " + task["when"] + " }}"))

        assert older is expected


class TestNameOrder:
    """The order of the names in the result."""

    def test_numbers_are_ordered_as_numbers(self):
        names = ["CH0.BAY10", "CT1.ETH2", "CH0.BAY2", "CH0", "CT0.FC12", "CT0.FC2"]

        assert sorted(names, key=name_order) == [
            "CH0",
            "CH0.BAY2",
            "CH0.BAY10",
            "CT0.FC2",
            "CT0.FC12",
            "CT1.ETH2",
        ]

    def test_case_does_not_count_and_the_order_is_fixed(self):
        assert sorted(["b1", "A2", "a2", "B1", "a10"], key=name_order) == [
            "A2",
            "a2",
            "a10",
            "B1",
            "b1",
        ]

    @pytest.mark.parametrize(
        "names, expected",
        [
            pytest.param(["CT0", ""], ["", "CT0"], id="empty name"),
            pytest.param(["CT0", "12", "1"], ["1", "12", "CT0"], id="only a number"),
            # a superscript two is a digit to str.isdigit() and no number to int()
            pytest.param(
                [chr(178), "a"], ["a", chr(178)], id="digit that is no number"
            ),
        ],
    )
    def test_any_name_can_be_ordered(self, names, expected):
        assert sorted(names, key=name_order) == expected
