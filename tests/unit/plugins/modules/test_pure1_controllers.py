# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_controllers module."""

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


from plugins.modules import pure1_controllers
from plugins.modules.pure1_controllers import version_key
from tests.unit.conftest import (
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# 2021-02-03 13:04:05 UTC: month, day, hour, minute and second all differ
AS_OF = 1612357445000
# The roles of the two controllers that _pair() builds
CT0 = dict(name="CT0", id="ct0-id", mode="primary")
CT1 = dict(name="CT1", id="ct1-id", mode="secondary")


def _array(name="array1"):
    return array_reference(
        name, id=name + "-id", fqdn=name + ".example.com", resource_type="arrays"
    )


def _controller(array="array1", **overrides):
    """Return a ready primary array controller CT0; a field given as None is unset"""
    fields = dict(
        CT0,
        arrays=[_array(array)],
        status="ready",
        type="array_controller",
        model="FA-X70R4",
        version="6.5.0",
        as_of=AS_OF,
    )
    fields.update(overrides)
    return sdk_model("Controller", **fields)


def _pair(ct0=None, ct1=None, array="array1"):
    """Return the two controllers of an array, each with the changes given for it"""
    return [
        _controller(array, **dict(CT0, **(ct0 or {}))),
        _controller(array, **dict(CT1, **(ct1 or {}))),
    ]


def _row(**overrides):
    """Return what the result holds for the controller that _controller() builds"""
    row = {
        "id": "ct0-id",
        "mode": "primary",
        "status": "ready",
        "type": "array_controller",
        "model": "FA-X70R4",
        "version": "6.5.0",
        "as_of": "2021-02-03 13:04:05 UTC",
        "as_of_epoch": AS_OF,
    }
    row.update(overrides)
    return row


def _rollup(array="array1", **overrides):
    """Return the roll-up of the array that _pair() builds"""
    rollup = {
        "id": array + "-id",
        "fqdn": array + ".example.com",
        "as_of": "2021-02-03 13:04:05 UTC",
        "as_of_epoch": AS_OF,
        "controller_count": 2,
        "primary": "CT0",
        "secondary": "CT1",
        "all_ready": True,
        "redundant": True,
        "versions": ["6.5.0"],
    }
    rollup.update(overrides)
    return rollup


# What the result holds for the array that _pair() builds
PAIR_ROWS = {"CT0": _row(), "CT1": _row(id="ct1-id", mode="secondary")}
# The roll-up of an array none of whose controllers counts
NOTHING_COUNTS = dict(
    as_of=None,
    as_of_epoch=None,
    controller_count=0,
    primary=None,
    secondary=None,
    all_ready=False,
    redundant=False,
    versions=[],
)


def _known(*names):
    """Return the answers to the two get_arrays calls of a task that names arrays

    The first is the check that get_pure1 makes, the second holds the arrays
    Pure1 knows by the names of the task.
    """
    arrays = [
        sdk_model("Array", name=name, id=name + "-id", os="Purity//FA")
        for name in names
    ]
    return [valid_response(), valid_response(arrays)]


def _result(module):
    return module.exit_json.call_args[1]["pure1_controllers"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


class TestPure1ControllersMain:
    """Tests of main(): the requests it makes and the whole result"""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_controller_is_returned_by_array_with_a_rollup(self, pure1_client):
        cloud = sdk_model("FixedReferenceFqdn", name="array2", id="array2-id")
        answers = paged_answers(
            "ControllerGetResponse",
            [
                _controller(
                    name="CT1", id="ct3-id", arrays=[cloud], model="CBS-V10MP2R2"
                ),
                _controller(
                    name="CT0",
                    id="ct2-id",
                    arrays=[cloud],
                    mode="secondary",
                    status="not ready",
                    model="CBS-V10MP2R2",
                    version="6.10.7",
                    as_of=AS_OF - 4000,
                ),
            ]
            + list(reversed(_pair())),
        )
        pure1_client.get_controllers.side_effect = answers

        # array is left to the argument spec
        module = run_main(pure1_controllers)

        assert answers.requests == [{"filter": None}]
        pure1_client.get_controllers.assert_called_once_with(filter=None)
        # the check of get_pure1 and no other request
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {
                    "array1": {
                        "CT0": {
                            "id": "ct0-id",
                            "mode": "primary",
                            "status": "ready",
                            "type": "array_controller",
                            "model": "FA-X70R4",
                            "version": "6.5.0",
                            "as_of": "2021-02-03 13:04:05 UTC",
                            "as_of_epoch": 1612357445000,
                        },
                        "CT1": {
                            "id": "ct1-id",
                            "mode": "secondary",
                            "status": "ready",
                            "type": "array_controller",
                            "model": "FA-X70R4",
                            "version": "6.5.0",
                            "as_of": "2021-02-03 13:04:05 UTC",
                            "as_of_epoch": 1612357445000,
                        },
                    },
                    "array2": {
                        "CT0": {
                            "id": "ct2-id",
                            "mode": "secondary",
                            "status": "not ready",
                            "type": "array_controller",
                            "model": "CBS-V10MP2R2",
                            "version": "6.10.7",
                            "as_of": "2021-02-03 13:04:01 UTC",
                            "as_of_epoch": 1612357441000,
                        },
                        "CT1": {
                            "id": "ct3-id",
                            "mode": "primary",
                            "status": "ready",
                            "type": "array_controller",
                            "model": "CBS-V10MP2R2",
                            "version": "6.5.0",
                            "as_of": "2021-02-03 13:04:05 UTC",
                            "as_of_epoch": 1612357445000,
                        },
                    },
                },
                "arrays": {
                    "array1": {
                        "id": "array1-id",
                        "fqdn": "array1.example.com",
                        "as_of": "2021-02-03 13:04:05 UTC",
                        "as_of_epoch": 1612357445000,
                        "controller_count": 2,
                        "primary": "CT0",
                        "secondary": "CT1",
                        "all_ready": True,
                        "redundant": True,
                        "versions": ["6.5.0"],
                    },
                    "array2": {
                        "id": "array2-id",
                        "fqdn": None,
                        "as_of": "2021-02-03 13:04:01 UTC",
                        "as_of_epoch": 1612357441000,
                        "controller_count": 2,
                        "primary": "CT1",
                        "secondary": "CT0",
                        "all_ready": False,
                        "redundant": False,
                        "versions": ["6.5.0", "6.10.7"],
                    },
                },
            },
        )
        # arrays and controllers are in the order of their names
        result = _result(module)
        assert list(result["controllers"]) == ["array1", "array2"]
        assert list(result["arrays"]) == ["array1", "array2"]
        assert [list(rows) for rows in result["controllers"].values()] == [
            ["CT0", "CT1"],
            ["CT0", "CT1"],
        ]

    def test_check_mode_returns_the_same(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(_pair())

        module = run_main(pure1_controllers, check_mode=True)

        pure1_client.get_controllers.assert_called_once_with(filter=None)
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"array1": PAIR_ROWS},
                "arrays": {"array1": _rollup()},
            },
        )

    def test_no_controllers_gives_both_keys_empty(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response([])

        module = run_main(pure1_controllers)

        pure1_client.get_controllers.assert_called_once_with(filter=None)
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_controllers={"controllers": {}, "arrays": {}}
        )

    def test_listing_is_read_across_pages(self, pure1_client):
        controllers = [
            _controller("array{0}".format(number), id="id-{0}".format(number))
            for number in range(1, 6)
        ]
        answers = paged_answers("ControllerGetResponse", controllers, page_size=2)
        pure1_client.get_controllers.side_effect = answers

        module = run_main(pure1_controllers)

        # one call of the module; the SDK reads the pages after the first
        pure1_client.get_controllers.assert_called_once_with(filter=None)
        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "continuation_token": "page-4"},
        ]
        module.warn.assert_not_called()
        assert _result(module)["controllers"] == {
            "array{0}".format(number): {"CT0": _row(id="id-{0}".format(number))}
            for number in range(1, 6)
        }
        assert sorted(_result(module)["arrays"]) == sorted(
            _result(module)["controllers"]
        )


class TestArrayOption:
    """Tests of the array option"""

    def test_named_arrays_are_resolved_and_filtered(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("array1", "Array2")
        pure1_client.get_controllers.return_value = valid_response(
            _pair() + _pair(array="Array2")
        )

        module = run_main(pure1_controllers, array=["ARRAY1", "array2"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        # the filter holds the names as Pure1 spells them
        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name=('array1','Array2')"
        )
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"Array2": PAIR_ROWS, "array1": PAIR_ROWS},
                "arrays": {"Array2": _rollup("Array2"), "array1": _rollup()},
            },
        )

    def test_one_named_array(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("array1")
        pure1_client.get_controllers.return_value = valid_response(_pair())

        # a string is a list of one name
        module = run_main(pure1_controllers, array="array1")

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='array1'"),
        ]
        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name='array1'"
        )
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup()},
        }

    def test_name_is_escaped_in_the_filter(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("o'brien")
        pure1_client.get_controllers.return_value = valid_response(
            _pair(array="o'brien")
        )

        module = run_main(pure1_controllers, array=["o'brien"])

        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name='o\\'brien'"
        )
        assert list(_result(module)["controllers"]) == ["o'brien"]

    def test_empty_list_selects_every_array(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(_pair())

        module = run_main(pure1_controllers, array=[])

        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_controllers.assert_called_once_with(filter=None)
        module.warn.assert_not_called()
        assert list(_result(module)["controllers"]) == ["array1"]

    def test_unknown_array_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("array1")

        module = run_main(pure1_controllers, array=["array1", "nosuch"])

        module.fail_json.assert_called_once_with(
            msg="Array nosuch is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        pure1_client.get_controllers.assert_not_called()

    def test_empty_name_fails_the_task(self, pure1_client):
        module = run_main(pure1_controllers, array=["array1", " "])

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_controllers.assert_not_called()

    def test_named_array_without_controllers_is_absent_with_a_warning(
        self, pure1_client
    ):
        """Pure1 has no controller for a FlashBlade"""
        pure1_client.get_arrays.side_effect = _known("blade1", "array1")
        pure1_client.get_controllers.return_value = valid_response(_pair())

        module = run_main(pure1_controllers, array=["BLADE1", "array1"])

        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name=('blade1','array1')"
        )
        assert _warnings(module) == [
            "Pure1 reports no controller for array blade1: it is not in the result."
        ]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"array1": PAIR_ROWS},
                "arrays": {"array1": _rollup()},
            },
        )

    def test_named_arrays_without_controllers_give_one_warning(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("blade1", "blade2")
        pure1_client.get_controllers.return_value = valid_response([])

        module = run_main(pure1_controllers, array=["blade1", "blade2"])

        assert _warnings(module) == [
            "Pure1 reports no controller for arrays blade1, blade2: they are not "
            "in the result."
        ]
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False, pure1_controllers={"controllers": {}, "arrays": {}}
        )

    def test_array_that_was_not_named_is_left_out(self, pure1_client):
        """Pure1 matches a name whatever its case, so the filter can match
        an array whose name differs from the wanted one in case only."""
        pure1_client.get_arrays.side_effect = _known("array1")
        pure1_client.get_controllers.return_value = valid_response(
            _pair(array="ARRAY1") + _pair()
        )

        module = run_main(pure1_controllers, array=["array1"])

        module.warn.assert_not_called()
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup()},
        }

    def test_named_array_is_told_from_another_array_by_its_id(self, pure1_client):
        """The task names one array: the controllers of another array that
        carries its name are not asked for, also when they come first"""
        other = [array_reference("array1", id="other-id", fqdn="other.example.com")]
        failed = dict(arrays=other, status="not ready")
        pure1_client.get_arrays.side_effect = _known("array1")
        pure1_client.get_controllers.return_value = valid_response(
            _pair(ct0=failed, ct1=failed) + _pair()
        )

        module = run_main(pure1_controllers, array=["array1"])

        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name='array1'"
        )
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"array1": PAIR_ROWS},
                "arrays": {"array1": _rollup()},
            },
        )

    def test_controller_without_the_id_of_its_array_is_kept_by_name(self, pure1_client):
        """Without an ID to compare, the name as Pure1 spells it decides"""
        bare = [array_reference("array1")]
        pure1_client.get_arrays.side_effect = _known("array1")
        pure1_client.get_controllers.return_value = valid_response(
            _pair(ct0=dict(arrays=bare), ct1=dict(arrays=bare))
            + _pair(
                ct0=dict(arrays=[array_reference("ARRAY1")]),
                ct1=dict(arrays=[array_reference("ARRAY1")]),
            )
        )

        module = run_main(pure1_controllers, array=["array1"])

        module.warn.assert_not_called()
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup(id=None, fqdn=None)},
        }


class TestRows:
    """Tests of what is filed and of the defaults for what Pure1 did not send"""

    def test_controller_with_only_its_identifying_fields(self, pure1_client):
        bare = sdk_model("Controller", name="CT0", arrays=[array_reference("array1")])
        pure1_client.get_controllers.return_value = valid_response([bare])

        module = run_main(pure1_controllers)

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {
                    "array1": {
                        "CT0": {
                            "id": None,
                            "mode": None,
                            "status": None,
                            "type": None,
                            "model": None,
                            "version": None,
                            "as_of": None,
                            "as_of_epoch": None,
                        }
                    }
                },
                "arrays": {
                    "array1": {
                        "id": None,
                        "fqdn": None,
                        "as_of": None,
                        "as_of_epoch": None,
                        "controller_count": 0,
                        "primary": None,
                        "secondary": None,
                        "all_ready": False,
                        "redundant": False,
                        "versions": [],
                    }
                },
            },
        )

    @staticmethod
    def _without_name():
        return sdk_model("Controller", id="ct9-id", arrays=[_array()])

    @staticmethod
    def _without_arrays():
        return sdk_model("Controller", name="CT9")

    @staticmethod
    def _with_empty_arrays():
        # The SDK reads an answer without validating it, so an empty list
        # arrives as it is; the constructor of the model refuses one
        controller = _controller(name="CT9")
        del controller.arrays[:]
        return controller

    @staticmethod
    def _with_null_array():
        controller = _controller(name="CT9")
        controller.arrays[0] = None
        return controller

    @staticmethod
    def _on_array_without_name():
        reference = sdk_model("FixedReferenceFqdn", id="array9-id")
        return _controller(name="CT9", arrays=[reference])

    @pytest.mark.parametrize(
        "broken",
        [
            "_without_name",
            "_without_arrays",
            "_with_empty_arrays",
            "_with_null_array",
            "_on_array_without_name",
        ],
    )
    def test_controller_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        pure1_client.get_controllers.return_value = valid_response(
            [getattr(self, broken)()] + _pair()
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [
            "Controllers that Pure1 returned without a name or without an array "
            "are left out of the result: 1."
        ]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup()},
        }

    def test_controllers_left_out_give_one_warning(self, pure1_client):
        pure1_client.get_arrays.side_effect = _known("array1")
        pure1_client.get_controllers.return_value = valid_response(
            [self._without_name(), self._without_arrays(), self._with_null_array()]
        )

        module = run_main(pure1_controllers, array=["array1"])

        # and the named array has no controller left
        assert _warnings(module) == [
            "Controllers that Pure1 returned without a name or without an array "
            "are left out of the result: 3.",
            "Pure1 reports no controller for array array1: it is not in the result.",
        ]
        assert _result(module) == {"controllers": {}, "arrays": {}}

    def test_controller_is_filed_under_its_first_named_array(self, pure1_client):
        controller = _controller(
            arrays=[
                sdk_model("FixedReferenceFqdn", id="array9-id"),
                _array("array2"),
                _array("array3"),
            ]
        )
        controller.arrays.insert(0, None)
        pure1_client.get_controllers.return_value = valid_response([controller])

        module = run_main(pure1_controllers)

        module.warn.assert_not_called()
        assert _result(module) == {
            "controllers": {"array2": {"CT0": _row()}},
            "arrays": {
                "array2": _rollup(
                    "array2", controller_count=1, secondary=None, redundant=False
                )
            },
        }

    @pytest.mark.parametrize(
        "array, name",
        [
            pytest.param("array1", "", id="empty name"),
            pytest.param("", "CT0", id="empty array name"),
        ],
    )
    def test_controller_with_an_empty_name_is_filed(self, pure1_client, array, name):
        """Only a name that Pure1 did not send leaves a controller out"""
        reference = array_reference(array)
        pure1_client.get_controllers.return_value = valid_response(
            [_controller(name=name, arrays=[reference])]
        )

        module = run_main(pure1_controllers)

        module.warn.assert_not_called()
        assert _result(module)["controllers"] == {array: {name: _row()}}
        assert list(_result(module)["arrays"]) == [array]

    def test_repeated_name_on_an_array_keeps_the_first(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(
            _pair()
            + [
                _controller(id="other-id", status="not ready"),
                _controller(name="CT1", id="other-id", mode="offline"),
                # the same name on another array is another controller
                _controller("array2"),
            ]
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [
            "Controllers that Pure1 returned under the name of another controller "
            "of the same array are left out of the result: 2."
        ]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS, "array2": {"CT0": _row()}},
            "arrays": {
                "array1": _rollup(),
                "array2": _rollup(
                    "array2", controller_count=1, secondary=None, redundant=False
                ),
            },
        }

    def test_one_repeated_name_adds_nothing_to_the_rollup(self, pure1_client):
        """The row left out is counted, and the ID and the FQDN of its array
        reference are not taken for the array"""
        bare = array_reference("array1")
        pure1_client.get_controllers.return_value = valid_response(
            _pair(ct0=dict(arrays=[bare]), ct1=dict(arrays=[bare]))
            + [_controller(id="other-id", status="not ready")]
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [
            "Controllers that Pure1 returned under the name of another controller "
            "of the same array are left out of the result: 1."
        ]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup(id=None, fqdn=None)},
        }


class TestArraysOfOneName:
    """Tests of arrays that have the same name: the result has one of them"""

    WARNING = (
        "Arrays that Pure1 returned under the name of another array are left "
        "out of the result: {0}."
    )

    @staticmethod
    def _other(array_id="other-id", **changes):
        """Return the two controllers of another array whose name is array1"""
        changes["arrays"] = [
            array_reference("array1", id=array_id, fqdn=array_id + ".example.com")
        ]
        return _pair(ct0=dict(changes, id="ct2-id"), ct1=dict(changes, id="ct3-id"))

    @staticmethod
    def _known_twice(name):
        """Return the answers to the two get_arrays calls of a task that names
        an array, when Pure1 knows two arrays by that name"""
        arrays = [
            sdk_model("Array", name=name, id=array_id, os="Purity//FA")
            for array_id in (name + "-id", "other-id")
        ]
        return [valid_response(), valid_response(arrays)]

    def test_failed_array_is_not_hidden_in_a_healthy_namesake(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(
            _pair() + self._other(status="not ready")
        )

        module = run_main(pure1_controllers)

        # and its controllers are not repeated controllers of the first array
        assert _warnings(module) == [self.WARNING.format(1)]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"array1": PAIR_ROWS},
                "arrays": {"array1": _rollup()},
            },
        )

    def test_array_whose_controller_comes_first_is_kept(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(
            self._other(status="not ready") + _pair()
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [self.WARNING.format(1)]
        assert _result(module) == {
            "controllers": {
                "array1": {
                    "CT0": _row(id="ct2-id", status="not ready"),
                    "CT1": _row(id="ct3-id", mode="secondary", status="not ready"),
                }
            },
            "arrays": {
                "array1": _rollup(
                    id="other-id",
                    fqdn="other-id.example.com",
                    all_ready=False,
                    redundant=False,
                )
            },
        }

    def test_array_is_not_completed_by_a_namesake(self, pure1_client):
        """An array that reports its primary only is not redundant"""
        pure1_client.get_controllers.return_value = valid_response(
            [_controller()] + self._other()
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [self.WARNING.format(1)]
        assert _result(module) == {
            "controllers": {"array1": {"CT0": _row()}},
            "arrays": {
                "array1": _rollup(controller_count=1, secondary=None, redundant=False)
            },
        }

    def test_warning_counts_the_arrays_left_out(self, pure1_client):
        """Four controllers of two arrays are left out, in any order of the
        rows, and the FQDN of such an array is not taken for the first"""
        cloud = array_reference("array1", id="array1-id")
        first = _pair(ct0=dict(arrays=[cloud]), ct1=dict(arrays=[cloud]))
        second = self._other()
        third = self._other("third-id")
        pure1_client.get_controllers.return_value = valid_response(
            [first[0], second[0], third[0], second[1], third[1], first[1]]
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [self.WARNING.format(2)]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup(fqdn=None)},
        }

    def test_reference_without_id_is_of_the_array_that_is_kept(self, pure1_client):
        """Only an ID that Pure1 sent tells two arrays of one name apart"""
        bare = array_reference("array1")
        pure1_client.get_controllers.return_value = valid_response(
            [
                _controller(arrays=[bare]),
                _controller(
                    **dict(CT1, arrays=[array_reference("array1", id="some-id")])
                ),
            ]
            + self._other()
            + [_controller(name="CT2", id="ct4-id", mode="offline", arrays=[bare])]
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [self.WARNING.format(1)]
        assert _result(module) == {
            "controllers": {
                "array1": dict(PAIR_ROWS, CT2=_row(id="ct4-id", mode="offline"))
            },
            "arrays": {"array1": _rollup(id="some-id", fqdn=None, controller_count=3)},
        }

    def test_repeated_controller_of_the_array_that_is_kept(self, pure1_client):
        """Each kind of row left out has its own warning"""
        pure1_client.get_controllers.return_value = valid_response(
            _pair() + self._other() + [_controller(id="again-id", mode="offline")]
        )

        module = run_main(pure1_controllers)

        assert _warnings(module) == [
            "Controllers that Pure1 returned under the name of another controller "
            "of the same array are left out of the result: 1.",
            self.WARNING.format(1),
        ]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup()},
        }

    def test_named_array_that_pure1_knows_twice(self, pure1_client):
        pure1_client.get_arrays.side_effect = self._known_twice("array1")
        pure1_client.get_controllers.return_value = valid_response(
            _pair() + self._other()
        )

        module = run_main(pure1_controllers, array=["array1"])

        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name='array1'"
        )
        assert _warnings(module) == [self.WARNING.format(1)]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_controllers={
                "controllers": {"array1": PAIR_ROWS},
                "arrays": {"array1": _rollup()},
            },
        )

    def test_named_array_that_pure1_knows_twice_without_controllers(self, pure1_client):
        """The warning names the array once"""
        pure1_client.get_arrays.side_effect = self._known_twice("blade1")
        pure1_client.get_controllers.return_value = valid_response([])

        module = run_main(pure1_controllers, array=["blade1"])

        pure1_client.get_controllers.assert_called_once_with(
            filter="arrays.name='blade1'"
        )
        assert _warnings(module) == [
            "Pure1 reports no controller for array blade1: it is not in the result."
        ]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_controllers={"controllers": {}, "arrays": {}}
        )


class TestRollup:
    """Tests of the roll-up of an array"""

    @pytest.mark.parametrize(
        "controllers, expected",
        [
            pytest.param(_pair, {}, id="primary and secondary ready"),
            pytest.param(
                lambda: _pair(ct1=dict(status="not ready")),
                dict(all_ready=False, redundant=False),
                id="secondary not ready",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(status="updating")),
                dict(all_ready=False, redundant=False),
                id="primary updating",
            ),
            pytest.param(
                lambda: _pair(ct1=dict(status=None)),
                dict(all_ready=False, redundant=False),
                id="secondary without status",
            ),
            pytest.param(
                lambda: _pair(ct1=dict(mode="primary")),
                dict(primary=None, secondary=None, redundant=False),
                id="two primaries",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(mode="secondary")),
                dict(primary=None, secondary=None, redundant=False),
                id="two secondaries",
            ),
            pytest.param(
                lambda: _pair(ct1=dict(mode="offline", status="unknown")),
                dict(secondary=None, all_ready=False, redundant=False),
                id="secondary offline",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(mode=None)),
                dict(primary=None, redundant=False),
                id="controller without mode",
            ),
            pytest.param(
                lambda: _pair()
                + [_controller(name="CT2", mode="not present", status="not ready")],
                dict(controller_count=3, all_ready=False),
                id="third controller not present",
            ),
            pytest.param(
                # two controllers are ready, and they are not the pair
                lambda: _pair(ct1=dict(status="not ready"))
                + [_controller(name="CT2", mode="not present")],
                dict(controller_count=3, all_ready=False, redundant=False),
                id="secondary not ready beside a third that is ready",
            ),
            pytest.param(
                lambda: _pair()
                + [
                    _controller(
                        name="SH0.SC0",
                        type="shelf_controller",
                        mode="primary",
                        status="not ready",
                        version="1.0.0",
                        as_of=AS_OF - 60000,
                    )
                ],
                {},
                id="shelf controller does not count",
            ),
            pytest.param(
                lambda: _pair(ct1=dict(type=None, version="6.4.0", as_of=AS_OF - 1000)),
                dict(controller_count=1, secondary=None, redundant=False),
                id="controller without type does not count",
            ),
            pytest.param(
                lambda: _pair(
                    ct0=dict(type="shelf_controller"), ct1=dict(type="shelf_controller")
                ),
                NOTHING_COUNTS,
                id="no array controller",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(version="6.10.7"), ct1=dict(version="6.9.2")),
                dict(versions=["6.9.2", "6.10.7"]),
                id="two versions, the lowest first",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(version=None)),
                {},
                id="one controller without version",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(version=None), ct1=dict(version=None)),
                dict(versions=[]),
                id="no version",
            ),
            pytest.param(
                lambda: _pair(ct1=dict(as_of=AS_OF - 4000)),
                dict(as_of="2021-02-03 13:04:01 UTC", as_of_epoch=AS_OF - 4000),
                id="the older time",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(as_of=None)),
                {},
                id="one controller without time",
            ),
            pytest.param(
                lambda: _pair(ct0=dict(as_of=None), ct1=dict(as_of=None)),
                dict(as_of=None, as_of_epoch=None),
                id="no time",
            ),
        ],
    )
    def test_rollup(self, pure1_client, controllers, expected):
        pure1_client.get_controllers.return_value = valid_response(controllers())

        module = run_main(pure1_controllers)

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {"array1": _rollup(**expected)}

    def test_controller_that_does_not_count_is_still_returned(self, pure1_client):
        shelf = _controller(name="SH0.SC0", id="shelf-id", type="shelf_controller")
        pure1_client.get_controllers.return_value = valid_response([shelf] + _pair())

        module = run_main(pure1_controllers)

        assert _result(module)["controllers"] == {
            "array1": dict(
                PAIR_ROWS, **{"SH0.SC0": _row(id="shelf-id", type="shelf_controller")}
            )
        }
        assert _result(module)["arrays"] == {"array1": _rollup()}

    @pytest.mark.parametrize(
        "ct0, ct1, expected",
        [
            pytest.param(
                dict(id="some-id"), dict(), ("some-id", None), id="id only, first"
            ),
            pytest.param(
                dict(), dict(id="some-id"), ("some-id", None), id="id only, second"
            ),
            pytest.param(
                dict(fqdn="a.example.com"),
                dict(id="some-id"),
                ("some-id", "a.example.com"),
                id="fqdn first, id second",
            ),
            pytest.param(
                dict(id="some-id"),
                dict(fqdn="a.example.com"),
                ("some-id", "a.example.com"),
                id="id first, fqdn second",
            ),
            # two IDs under one name are two arrays: TestArraysOfOneName
            pytest.param(
                dict(id="some-id", fqdn="first.example.com"),
                dict(id="some-id", fqdn="second.example.com"),
                ("some-id", "first.example.com"),
                id="the first that has it",
            ),
            pytest.param(dict(), dict(), (None, None), id="neither"),
        ],
    )
    def test_id_and_fqdn_come_from_the_array_references(
        self, pure1_client, ct0, ct1, expected
    ):
        pure1_client.get_controllers.return_value = valid_response(
            _pair(
                ct0=dict(arrays=[array_reference("array1", **ct0)]),
                ct1=dict(arrays=[array_reference("array1", **ct1)]),
            )
        )

        module = run_main(pure1_controllers)

        assert _result(module)["arrays"] == {
            "array1": _rollup(id=expected[0], fqdn=expected[1])
        }

    @pytest.mark.parametrize(
        "versions, expected",
        [
            pytest.param(
                ["6.10.7", "6.9.2", "6.9.10"],
                ["6.9.2", "6.9.10", "6.10.7"],
                id="numbers",
            ),
            pytest.param(
                ["6.5.0.a", "6.5.0", "6.5.0-rc2"],
                ["6.5.0", "6.5.0-rc2", "6.5.0.a"],
                id="text after the numbers",
            ),
            pytest.param(["beta", "10", ""], ["", "10", "beta"], id="text and empty"),
        ],
    )
    def test_version_key_orders_the_numbers_of_a_version(self, versions, expected):
        assert sorted(versions, key=version_key) == expected


class TestIncompleteListing:
    """Tests of the warning for a listing that Pure1 cut short"""

    def test_fewer_controllers_than_pure1_counted(self, pure1_client):
        answers = paged_answers("ControllerGetResponse", _pair(), total_item_count=5)
        pure1_client.get_controllers.side_effect = answers

        module = run_main(pure1_controllers)

        assert answers.requests == [{"filter": None}, {"filter": None, "offset": 2}]
        assert _warnings(module) == [
            "Pure1 returned 2 of 5 controllers: the result may be incomplete."
        ]
        assert _result(module) == {
            "controllers": {"array1": PAIR_ROWS},
            "arrays": {"array1": _rollup()},
        }

    def test_no_total_and_the_first_page_again(self, pure1_client):
        answers = paged_answers("ControllerGetResponse", _pair(), total_item_count=None)

        def ignores_offset(**kwargs):
            return answers(filter=kwargs["filter"])

        pure1_client.get_controllers.side_effect = ignores_offset

        module = run_main(pure1_controllers)

        assert pure1_client.get_controllers.call_args_list == [
            call(filter=None),
            call(filter=None, offset=2),
        ]
        assert _warnings(module) == [
            "Pure1 returned 2 of an unknown number of controllers: the result "
            "may be incomplete."
        ]
        assert list(_result(module)["controllers"]) == ["array1"]


class TestFailures:
    """Tests of a request that fails"""

    @pytest.mark.parametrize(
        "params", [{}, {"array": ["array1"]}], ids=["all", "named"]
    )
    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request get_controllers failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "Pure1 request get_controllers failed (HTTP 400): Illegal filter "
                "format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request get_controllers failed (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_controllers failed: PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request get_controllers failed: "
                "TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, params, attribute, answer, expected
    ):
        if params:
            pure1_client.get_arrays.side_effect = _known("array1")
        setattr(pure1_client.get_controllers, attribute, answer)

        module = run_main(pure1_controllers, **params)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_failed_lookup_of_the_arrays_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(500, "Internal server error"),
        ]

        module = run_main(pure1_controllers, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 500): Internal server error"
        )
        pure1_client.get_controllers.assert_not_called()

    def test_error_for_a_later_page_fails_the_task(self, pure1_client):
        answers = paged_answers("ControllerGetResponse", _pair(), total_item_count=5)

        def refuses_offset(**kwargs):
            if "offset" in kwargs:
                return error_response(500, "Internal server error")
            return answers(**kwargs)

        pure1_client.get_controllers.side_effect = refuses_offset

        module = run_main(pure1_controllers)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_controllers failed (HTTP 500): "
            "Internal server error"
        )
        module.exit_json.assert_not_called()


class TestDocumentation:
    """Tests that RETURN describes what the module returns"""

    @staticmethod
    def _described():
        return yaml.safe_load(pure1_controllers.RETURN)["pure1_controllers"]

    def test_return_names_every_key_of_the_result(self, pure1_client):
        pure1_client.get_controllers.return_value = valid_response(_pair())

        result = _result(run_main(pure1_controllers))

        contains = self._described()["contains"]
        assert sorted(contains) == sorted(result)
        for rows in result["controllers"].values():
            for row in rows.values():
                assert sorted(row) == sorted(contains["controllers"]["contains"])
        for rollup in result["arrays"].values():
            assert sorted(rollup) == sorted(contains["arrays"]["contains"])

    def test_sample_is_what_the_module_returns(self, pure1_client):
        sample = self._described()["sample"]
        controllers = []
        for array, rows in sample["controllers"].items():
            rollup = sample["arrays"][array]
            reference = array_reference(array, id=rollup["id"], fqdn=rollup["fqdn"])
            for name, row in rows.items():
                controllers.append(
                    sdk_model(
                        "Controller",
                        name=name,
                        arrays=[reference],
                        id=row["id"],
                        mode=row["mode"],
                        status=row["status"],
                        type=row["type"],
                        model=row["model"],
                        version=row["version"],
                        as_of=row["as_of_epoch"],
                    )
                )
        pure1_client.get_controllers.return_value = valid_response(controllers)

        module = run_main(pure1_controllers)

        assert len(controllers) == 2
        module.exit_json.assert_called_once_with(
            changed=False, pure1_controllers=sample
        )


MODULE = "everpure.pure1.pure1_controllers"
NOT_REDUNDANT = (
    "An array has no redundant pair of ready controllers, or its data is more "
    "than ten minutes old"
)


def _template(text, variables):
    """Return what ansible-core makes of a template of a task"""
    templar = Templar(loader=None, variables=variables)
    return templar.template(trust_as_template(text))


def _play_examples(client, **served):
    """Run the tasks of EXAMPLES in their order and return what they make

    A task of the module runs through main() with the options of the example
    and registers its result. Pure1 knows the arrays that the task names and
    answers with the controllers that served gives under the name the task
    registers. Every other task is returned under the name that the task of
    the module before it registered, as a list with one entry for each item
    of its loop, one without a loop: its when, and unless that is false the
    templates of its arguments, as ansible-core evaluates them. The
    conditions of an assert end with the first that is false: ansible-core
    does not evaluate those after it.
    """
    registered = {}
    made = {}
    reads = None
    for task in yaml.safe_load(pure1_controllers.EXAMPLES):
        options = task.get(MODULE)
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            reads = task["register"]
            if "array" in options:
                client.get_arrays.side_effect = _known(*options["array"])
            else:
                # The check that get_pure1 makes
                client.get_arrays.side_effect = [valid_response()]
            client.get_controllers.return_value = valid_response(served[reads])
            module = run_main(pure1_controllers, **options)
            registered[reads] = dict(module.exit_json.call_args[1])
            made[reads] = []
            continue
        (action,) = set(task) - {"name", "when", "loop", "vars"}
        variables = dict(registered)
        items = _template(task["loop"], variables) if "loop" in task else [None]
        entries = []
        for item in items:
            scope = dict(variables, item=item)
            entry = {}
            if "when" in task:
                entry["when"] = _template("{{ " + task["when"] + " }}", scope)
            if entry.get("when", True):
                for key, value in task[action].items():
                    if key == "that":
                        entry[key] = []
                        for each in value:
                            holds = _template("{{ " + each + " }}", scope)
                            entry[key].append(holds)
                            if not holds:
                                break
                    elif isinstance(value, str):
                        entry[key] = _template(value, scope)
            entries.append(entry)
        made[reads].append(entries)
    return made


def _fresh_pairs(**changes):
    """Return the controllers of array1 and array2, refreshed a minute ago

    changes are those of the second controller of array2.
    """
    fresh = dict(as_of=int(time.time() * 1000) - 60000)
    return _pair(ct0=fresh, ct1=fresh) + _pair(
        ct0=fresh, ct1=dict(fresh, **changes), array="array2"
    )


class TestExamples:
    """The tasks of EXAMPLES, on what the module returns"""

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_examples_read_what_the_module_returns(self, pure1_client):
        everything = (
            _pair()
            + _pair(ct1=dict(status="not ready"), array="array2")
            + [_controller("array3")]
        )

        made = _play_examples(pure1_client, controllers=everything, pair=_fresh_pairs())

        assert list(made) == ["controllers", "pair"]
        shown, not_redundant = made["controllers"]
        assert shown == [
            {
                "msg": {
                    "array1": PAIR_ROWS,
                    "array2": {
                        "CT0": _row(),
                        "CT1": _row(id="ct1-id", mode="secondary", status="not ready"),
                    },
                    "array3": {"CT0": _row()},
                }
            }
        ]
        assert not_redundant == [
            {"when": False},
            {"when": True, "msg": "array2 primary CT0 secondary CT1"},
            {"when": True, "msg": "array3 primary CT0 secondary none"},
        ]
        # Both arrays are there, both are redundant and their data is fresh
        assert made["pair"] == [
            [{"that": [True, True, True], "fail_msg": NOT_REDUNDANT}]
        ]

    def test_examples_when_pure1_has_no_controller(self, pure1_client):
        """What the examples name is not there: no task fails to evaluate"""
        made = _play_examples(pure1_client, controllers=[], pair=[])

        assert made["controllers"] == [[{"msg": {}}], []]
        # The first condition is false: the assert ends there
        assert made["pair"] == [[{"that": [False], "fail_msg": NOT_REDUNDANT}]]

    @pytest.mark.parametrize(
        "changes, expected",
        [
            pytest.param(
                dict(status="not ready"), [True, False], id="controller not ready"
            ),
            pytest.param(
                dict(as_of=int(time.time() * 1000) - 3600000),
                [True, True, False],
                id="data of an hour ago",
            ),
        ],
    )
    def test_check_before_an_upgrade_fails(self, pure1_client, changes, expected):
        made = _play_examples(
            pure1_client, controllers=[], pair=_fresh_pairs(**changes)
        )

        assert made["pair"] == [[{"that": expected, "fail_msg": NOT_REDUNDANT}]]

    def test_check_before_an_upgrade_fails_for_data_without_a_time(self, pure1_client):
        """A time that is null counts as the oldest there is"""
        timeless = dict(as_of=None)
        rows = _pair(ct0=timeless, ct1=timeless) + _fresh_pairs()[2:]

        made = _play_examples(pure1_client, controllers=[], pair=rows)

        assert made["pair"] == [
            [{"that": [True, True, False], "fail_msg": NOT_REDUNDANT}]
        ]
