# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_simulation module.

Pure1 was never asked for a simulation while the module was written: the
request is a POST. The answers of these tests are built by the client code of
the SDK from its response models, and in TestThroughTheSdk from a JSON
document in the names of the REST reference, which the SDK itself reads.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import json
import types

import jinja2
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule
from jinja2.nativetypes import NativeEnvironment
from pypureclient import ErrorResponse, PureError, ValidResponse
from pypureclient._transport.api_response import ApiResponse
from pypureclient._transport.configuration import Configuration

from plugins.module_utils.pure1 import pure1_time
from plugins.modules import pure1_simulation
from tests.unit.conftest import (
    MODELS,
    NEWEST,
    array_reference,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

DAY = 86400000
# 2026-11-24 00:00:00 UTC, and a time of that day as time.time() gives it
MIDNIGHT = 1795478400000
NOW = 1795515330.25
# Every time differs in month, day, hour, minute and second
LOAD_AS_OF = 1795515330000
CAPACITY_AS_OF = 1612357445000
# The first, the highest, the lowest and the last value are four points
LOAD_DATA = [
    [1795478400000, 0.41],
    [1795564800000, 0.44],
    [1795651200000, 0.39],
    [1795737600000, 0.42],
]
# Whole numbers, at times that are no midnight
CAPACITY_DATA = [
    [1612357445000, 5000],
    [1612447506000, 5200],
    [1612537567000, 4900],
]
BOTH = [{"name": "projected_load"}, {"name": "projected_capacity"}]
LOAD_ROW = {
    "unit": "",
    "aggregation": "blended_max",
    "resolution": 86400000,
    "as_of": "2026-11-24 10:15:30 UTC",
    "as_of_epoch": 1795515330000,
    "first": {
        "time": "2026-11-24 00:00:00 UTC",
        "time_epoch": 1795478400000,
        "value": 0.41,
    },
    "last": {
        "time": "2026-11-27 00:00:00 UTC",
        "time_epoch": 1795737600000,
        "value": 0.42,
    },
    "min": {
        "time": "2026-11-26 00:00:00 UTC",
        "time_epoch": 1795651200000,
        "value": 0.39,
    },
    "max": {
        "time": "2026-11-25 00:00:00 UTC",
        "time_epoch": 1795564800000,
        "value": 0.44,
    },
    "data": [
        [1795478400000, 0.41],
        [1795564800000, 0.44],
        [1795651200000, 0.39],
        [1795737600000, 0.42],
    ],
}
CAPACITY_ROW = {
    "unit": "B",
    "aggregation": "average",
    "resolution": 90061000,
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "first": {
        "time": "2021-02-03 13:04:05 UTC",
        "time_epoch": 1612357445000,
        "value": 5000,
    },
    "last": {
        "time": "2021-02-05 15:06:07 UTC",
        "time_epoch": 1612537567000,
        "value": 4900,
    },
    "min": {
        "time": "2021-02-05 15:06:07 UTC",
        "time_epoch": 1612537567000,
        "value": 4900,
    },
    "max": {
        "time": "2021-02-04 14:05:06 UTC",
        "time_epoch": 1612447506000,
        "value": 5200,
    },
    "data": [
        [1612357445000, 5000],
        [1612447506000, 5200],
        [1612537567000, 4900],
    ],
}
EMPTY_ROW = {
    "unit": None,
    "aggregation": None,
    "resolution": None,
    "as_of": None,
    "as_of_epoch": None,
    "first": None,
    "last": None,
    "min": None,
    "max": None,
    "data": [],
}
# The request of a task that sets no option but arrays, for array1
DEFAULT_REQUEST = {
    "resources": ["array1"],
    "metrics": [
        {"name": "projected_load", "aggregation": None},
        {"name": "projected_capacity", "aggregation": None},
    ],
    "projection_end_time": None,
    "projection_end_time_epoch": None,
    "training_start_time": None,
    "training_start_time_epoch": None,
    "resolution": None,
}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Stop the clock of the module at NOW; a test moves it with clock.now"""
    stopped = types.SimpleNamespace(now=NOW)
    stopped.time = lambda: stopped.now
    monkeypatch.setattr(pure1_simulation, "time", stopped)
    return stopped


def _reference(array="array1", **overrides):
    """Return the reference a projection has to its array"""
    fields = dict(id=array + "-id", fqdn=array + ".example.com", resource_type="arrays")
    fields.update(overrides)
    return array_reference(array, **fields)


def _load(array="array1", **overrides):
    fields = dict(
        as_of=LOAD_AS_OF,
        id="load-of-" + array,
        name="projected_load",
        aggregation="blended_max",
        data=[list(point) for point in LOAD_DATA],
        resolution=86400000,
        resources=[_reference(array)],
        unit="",
    )
    fields.update(overrides)
    return sdk_model("PlanningSimulationMetric", **fields)


def _capacity(array="array1", **overrides):
    fields = dict(
        as_of=CAPACITY_AS_OF,
        id="capacity-of-" + array,
        name="projected_capacity",
        aggregation="average",
        data=[list(point) for point in CAPACITY_DATA],
        resolution=90061000,
        resources=[_reference(array)],
        unit="B",
    )
    fields.update(overrides)
    return sdk_model("PlanningSimulationMetric", **fields)


def _load_with_empty_resources():
    # The SDK reads an answer without validating it, so an empty list and a
    # null in a list arrive as they are; the constructor of the model
    # refuses both
    load = _load()
    del load.resources[:]
    return load


def _load_with_null_resource():
    load = _load()
    load.resources[0] = None
    return load


def _array_row(name="array1", **overrides):
    row = {
        "id": name + "-id",
        "fqdn": name + ".example.com",
        "projected_load": {},
        "projected_capacity": {},
    }
    row.update(overrides)
    return row


def _no_later_page(**kwargs):
    pytest.fail("the SDK asked for another page of a simulation: {0}".format(kwargs))


def _simulation_answer(items=(), total_item_count=-1):
    """Return what Client.post_arrays_simulations() gives for a 200 answer

    The answer is built by the client code of the SDK from the response
    model of the call. total_item_count is the number of items unless it is
    given: None for an answer without one.
    """
    items = list(items)
    fields = {"items": items}
    if total_item_count == -1:
        total_item_count = len(items)
    if total_item_count is not None:
        fields["total_item_count"] = total_item_count
    body = MODELS.PlanningSimulationResponse(**fields)
    return NEWEST.Client._create_valid_response(
        None, ApiResponse(200, {}, body), _no_later_page, {}
    )


def _known_arrays(pure1_client, *arrays):
    """Answer the request that resolves the arrays of a task, after the check
    of get_pure1. An array is its name or an Array model."""
    models = [
        (
            sdk_model(
                "Array", id=array + "-id", name=array, fqdn=array + ".example.com"
            )
            if isinstance(array, str)
            else array
        )
        for array in arrays
    ]
    pure1_client.get_arrays.side_effect = [valid_response(), valid_response(models)]


def _run(pure1_client, items=(), check_mode=False, **params):
    """Run main() for a task whose arrays Pure1 knows and whose answer holds
    these projections; arrays is array1 unless the task names others"""
    params.setdefault("arrays", ["array1"])
    _known_arrays(pure1_client, *params["arrays"])
    pure1_client.post_arrays_simulations.return_value = _simulation_answer(items)
    return run_main(pure1_simulation, check_mode=check_mode, **params)


def _body(pure1_client):
    """Return the body of the one simulation request that was made"""
    pure1_client.post_arrays_simulations.assert_called_once()
    arguments = pure1_client.post_arrays_simulations.call_args
    assert arguments[0] == () and list(arguments[1]) == ["planning_simulation_post"]
    return arguments[1]["planning_simulation_post"]


def _result(module):
    module.exit_json.assert_called_once()
    assert list(module.exit_json.call_args[1]) == ["changed", "pure1_simulation"]
    return module.exit_json.call_args[1]["pure1_simulation"]


def _requests(pure1_client):
    """Return the methods of the client that were called, with their number of calls"""
    return {
        name: method.call_count
        for name, method in vars(pure1_client).items()
        if getattr(method, "called", False)
    }


class TestPure1SimulationMain:
    """The whole result of main()."""

    def test_projections_of_two_arrays_are_returned(self, pure1_client):
        module = _run(
            pure1_client,
            [_load(), _capacity(), _capacity("array2"), _load("array2", unit="%")],
            arrays=["array1", "array2"],
        )

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_simulation={
                "request": {
                    "resources": ["array1", "array2"],
                    "metrics": [
                        {"name": "projected_load", "aggregation": None},
                        {"name": "projected_capacity", "aggregation": None},
                    ],
                    "projection_end_time": None,
                    "projection_end_time_epoch": None,
                    "training_start_time": None,
                    "training_start_time_epoch": None,
                    "resolution": None,
                },
                "arrays": {
                    "array1": {
                        "id": "array1-id",
                        "fqdn": "array1.example.com",
                        "projected_load": LOAD_ROW,
                        "projected_capacity": CAPACITY_ROW,
                    },
                    "array2": {
                        "id": "array2-id",
                        "fqdn": "array2.example.com",
                        "projected_load": dict(LOAD_ROW, unit="%"),
                        "projected_capacity": CAPACITY_ROW,
                    },
                },
            },
        )

    def test_every_option_is_in_the_request_of_the_result(self, pure1_client):
        module = _run(
            pure1_client,
            [_load(aggregation="average")],
            metrics=["projected_load"],
            aggregation="average",
            projection_days=30,
            training_days=60,
            resolution=3600000,
        )

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_simulation={
                "request": {
                    "resources": ["array1"],
                    "metrics": [{"name": "projected_load", "aggregation": "average"}],
                    "projection_end_time": "2026-12-24 00:00:00 UTC",
                    "projection_end_time_epoch": 1798070400000,
                    "training_start_time": "2026-09-25 00:00:00 UTC",
                    "training_start_time_epoch": 1790294400000,
                    "resolution": 3600000,
                },
                "arrays": {
                    "array1": _array_row(
                        projected_load=dict(LOAD_ROW, aggregation="average")
                    )
                },
            },
        )

    def test_request_is_sent_in_check_mode(self, pure1_client):
        """The request stores nothing in Pure1, so check mode gives the same"""
        module = _run(pure1_client, [_load(), _capacity()], check_mode=True)

        assert _body(pure1_client) == {
            "resources": [{"name": "array1"}],
            "metrics": BOTH,
        }
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_simulation={
                "request": DEFAULT_REQUEST,
                "arrays": {
                    "array1": _array_row(
                        projected_load=LOAD_ROW, projected_capacity=CAPACITY_ROW
                    )
                },
            },
        )

    def test_answer_without_projections_lists_every_array(self, pure1_client):
        module = _run(pure1_client, [], arrays=["array1", "array2"])

        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_simulation={
                "request": dict(DEFAULT_REQUEST, resources=["array1", "array2"]),
                "arrays": {"array1": _array_row(), "array2": _array_row("array2")},
            },
        )

    def test_answer_without_a_body_lists_every_array(self, pure1_client):
        """The SDK gives items None for a 200 answer that has no body"""
        _known_arrays(pure1_client, "array1")
        pure1_client.post_arrays_simulations.return_value = ValidResponse(
            200, None, None, None, {}
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        module.warn.assert_not_called()
        assert _result(module) == {
            "request": DEFAULT_REQUEST,
            "arrays": {"array1": _array_row()},
        }

    def test_array_without_a_projection_is_listed(self, pure1_client):
        module = _run(pure1_client, [_load("array2")], arrays=["array1", "array2"])

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {
            "array1": _array_row(),
            "array2": _array_row("array2", projected_load=LOAD_ROW),
        }


class TestRequestBody:
    """The body of the simulation request, for every option."""

    def test_default_body_names_the_array_and_both_metrics(self, pure1_client):
        _run(pure1_client)

        pure1_client.post_arrays_simulations.assert_called_once_with(
            planning_simulation_post={
                "resources": [{"name": "array1"}],
                "metrics": [{"name": "projected_load"}, {"name": "projected_capacity"}],
            }
        )

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param(
                {"metrics": ["projected_load"]},
                {"metrics": [{"name": "projected_load"}]},
                id="load",
            ),
            pytest.param(
                {"metrics": ["projected_capacity"]},
                {"metrics": [{"name": "projected_capacity"}]},
                id="capacity",
            ),
            pytest.param(
                {"metrics": ["projected_capacity", "projected_load"]},
                {
                    "metrics": [
                        {"name": "projected_capacity"},
                        {"name": "projected_load"},
                    ]
                },
                id="both in the order of the task",
            ),
            pytest.param(
                {"metrics": ["projected_load", "projected_capacity", "projected_load"]},
                {"metrics": BOTH},
                id="a metric named twice",
            ),
            pytest.param({"metrics": []}, {"metrics": BOTH}, id="no metric"),
            pytest.param(
                {"aggregation": "average"},
                {
                    "metrics": [
                        {"name": "projected_load", "aggregation": "average"},
                        {"name": "projected_capacity"},
                    ]
                },
                id="average",
            ),
            pytest.param(
                {"aggregation": "blended_max"},
                {
                    "metrics": [
                        {"name": "projected_load", "aggregation": "blended_max"},
                        {"name": "projected_capacity"},
                    ]
                },
                id="blended_max",
            ),
            pytest.param(
                {"metrics": ["projected_load"], "aggregation": "average"},
                {"metrics": [{"name": "projected_load", "aggregation": "average"}]},
                id="load with average",
            ),
            pytest.param(
                {
                    "metrics": ["projected_capacity", "projected_load"],
                    "aggregation": "blended_max",
                },
                {
                    "metrics": [
                        {"name": "projected_capacity"},
                        {"name": "projected_load", "aggregation": "blended_max"},
                    ]
                },
                id="capacity first with blended_max",
            ),
            pytest.param(
                {"projection_days": 30},
                {"metrics": BOTH, "projection_end_time": 1798070400000},
                id="projection_days",
            ),
            pytest.param(
                {"projection_days": 1},
                {"metrics": BOTH, "projection_end_time": 1795564800000},
                id="one day projected",
            ),
            pytest.param(
                {"training_days": 60},
                {"metrics": BOTH, "training_start_time": 1790294400000},
                id="training_days",
            ),
            pytest.param(
                {"training_days": 1},
                {"metrics": BOTH, "training_start_time": 1795392000000},
                id="one day of history",
            ),
            pytest.param(
                {"projection_days": 365, "training_days": 7},
                {
                    "metrics": BOTH,
                    "projection_end_time": 1827014400000,
                    "training_start_time": 1794873600000,
                },
                id="both periods",
            ),
            pytest.param(
                {"resolution": 3600000},
                {"metrics": BOTH, "resolution": 3600000},
                id="resolution",
            ),
            pytest.param(
                {"resolution": 1}, {"metrics": BOTH, "resolution": 1}, id="resolution 1"
            ),
            pytest.param(
                {
                    "metrics": ["projected_load"],
                    "aggregation": "average",
                    "projection_days": 30,
                    "training_days": 60,
                    "resolution": 604800000,
                },
                {
                    "metrics": [{"name": "projected_load", "aggregation": "average"}],
                    "projection_end_time": 1798070400000,
                    "training_start_time": 1790294400000,
                    "resolution": 604800000,
                },
                id="every option",
            ),
        ],
    )
    def test_body_of_an_option(self, pure1_client, params, expected):
        module = _run(pure1_client, **params)

        body = _body(pure1_client)
        assert body == dict(expected, resources=[{"name": "array1"}])
        module.warn.assert_not_called()
        # the SDK model of the request takes every field as it is: no field
        # is unknown to it, none has another type, and none is changed
        assert MODELS.PlanningSimulationPost(**body).as_request_dict() == body
        # and the body is a document that can be sent
        assert json.loads(json.dumps(body)) == body
        # whatever the options, two requests after the check of get_pure1
        assert _requests(pure1_client) == {
            "get_arrays": 2,
            "post_arrays_simulations": 1,
        }

    def test_body_has_the_fields_of_the_request_model_only(self, pure1_client):
        """Field by field against PlanningSimulationPost and the models in it"""
        _run(
            pure1_client,
            metrics=["projected_load"],
            aggregation="average",
            projection_days=30,
            training_days=60,
            resolution=3600000,
        )

        body = _body(pure1_client)
        request = MODELS.PlanningSimulationPost.__fields__
        assert set(body) == set(request) - {
            # no what-if step in this release, and Pure1 starts the
            # projection and ends the history at the last midnight itself
            "steps",
            "projection_start_time",
            "training_end_time",
        }
        for name in ("projection_end_time", "training_start_time", "resolution"):
            assert request[name].type_.__name__ == "StrictInt"
            # an int and no bool, which StrictInt refuses
            assert {type(body[name])} == {int}
        assert request["resources"].type_ is MODELS.PlanningSimulationArrayReference
        assert set(body["resources"][0]) == {"name"}
        assert set(MODELS.PlanningSimulationArrayReference.__fields__) == {
            "id",
            "name",
        }
        assert request["metrics"].type_ is MODELS.PlanningSimulationMetricRequest
        assert set(body["metrics"][0]) == set(
            MODELS.PlanningSimulationMetricRequest.__fields__
        )

    def test_aggregation_without_the_load_is_not_sent(self, pure1_client):
        module = _run(
            pure1_client,
            [_capacity()],
            metrics=["projected_capacity"],
            aggregation="average",
        )

        assert _body(pure1_client) == {
            "resources": [{"name": "array1"}],
            "metrics": [{"name": "projected_capacity"}],
        }
        module.warn.assert_called_once_with(
            "aggregation applies to projected_load only, which metrics does "
            "not name: it is not sent to Pure1."
        )
        assert _result(module)["request"]["metrics"] == [
            {"name": "projected_capacity", "aggregation": None}
        ]

    def test_arrays_are_named_as_pure1_spells_them(self, pure1_client):
        _known_arrays(pure1_client, "Array1", "ARRAY-two")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [_load("Array1")]
        )

        module = run_main(pure1_simulation, arrays=["array-TWO", "ARRAY1"])

        pure1_client.get_arrays.assert_called_with(filter="name=('array-TWO','ARRAY1')")
        # in the order of the task
        assert _body(pure1_client)["resources"] == [
            {"name": "ARRAY-two"},
            {"name": "Array1"},
        ]
        result = _result(module)
        assert result["request"]["resources"] == ["ARRAY-two", "Array1"]
        assert result["arrays"] == {
            "ARRAY-two": _array_row("ARRAY-two"),
            "Array1": _array_row("Array1", projected_load=LOAD_ROW),
        }

    def test_array_named_twice_is_sent_once(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer([])

        module = run_main(pure1_simulation, arrays=["array1", "ARRAY1", "array1"])

        assert _body(pure1_client)["resources"] == [{"name": "array1"}]
        assert list(_result(module)["arrays"]) == ["array1"]

    @pytest.mark.parametrize(
        "now, midnight",
        [
            pytest.param(1795478400.0, 1795478400000, id="at midnight"),
            pytest.param(1795478399.999, 1795392000000, id="just before midnight"),
            pytest.param(1795564799.999, 1795478400000, id="end of the day"),
            pytest.param(1795515330, 1795478400000, id="whole seconds"),
        ],
    )
    @pytest.mark.usefixtures("host_west_of_utc")
    def test_periods_are_counted_from_the_last_midnight_utc(
        self, pure1_client, clock, now, midnight
    ):
        """Whatever the time zone of the host: here it is five hours behind UTC"""
        clock.now = now

        _run(pure1_client, projection_days=2, training_days=3)

        body = _body(pure1_client)
        assert body["projection_end_time"] == midnight + 2 * DAY
        assert body["training_start_time"] == midnight - 3 * DAY

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_of_the_request_are_given_in_utc(self, pure1_client):
        module = _run(pure1_client, projection_days=2, training_days=3)

        request = _result(module)["request"]
        assert request["projection_end_time"] == "2026-11-26 00:00:00 UTC"
        assert request["projection_end_time_epoch"] == 1795651200000
        assert request["training_start_time"] == "2026-11-21 00:00:00 UTC"
        assert request["training_start_time_epoch"] == 1795219200000

    def test_longest_periods_are_sent_and_shown(self, pure1_client):
        """36500 days each way, the most that the options take"""
        module = _run(pure1_client, projection_days=36500, training_days=36500)

        assert _body(pure1_client) == {
            "resources": [{"name": "array1"}],
            "metrics": BOTH,
            "projection_end_time": 4949078400000,
            "training_start_time": -1358121600000,
        }
        request = _result(module)["request"]
        assert request["projection_end_time"] == "2126-10-31 00:00:00 UTC"
        assert request["projection_end_time_epoch"] == 4949078400000
        assert request["training_start_time"] == "1926-12-19 00:00:00 UTC"
        assert request["training_start_time_epoch"] == -1358121600000

    def test_resolution_has_no_upper_bound(self, pure1_client):
        """It is sent as a number and never shown as a time"""
        module = _run(pure1_client, resolution=10**15)

        assert _body(pure1_client)["resolution"] == 10**15
        assert _result(module)["request"]["resolution"] == 10**15

    def test_clock_is_not_read_without_a_period(self, pure1_client, clock):
        clock.time = None

        _run(pure1_client, resolution=3600000)

        assert _body(pure1_client) == {
            "resources": [{"name": "array1"}],
            "metrics": BOTH,
            "resolution": 3600000,
        }


class TestRequests:
    """The requests of a run, and what stops a task before one is made."""

    def test_default_run_makes_two_requests_after_the_check(
        self, pure1_client, client_factory
    ):
        module = _run(pure1_client, arrays=["array1", "array2", "array3"])

        module.fail_json.assert_not_called()
        client_factory.assert_called_once()
        # the check of get_pure1, then one request for all the names
        assert pure1_client.get_arrays.call_args_list == [
            ((), {"limit": 0}),
            ((), {"filter": "name=('array1','array2','array3')"}),
        ]
        # and one simulation for all the arrays
        pure1_client.post_arrays_simulations.assert_called_once_with(
            planning_simulation_post={
                "resources": [
                    {"name": "array1"},
                    {"name": "array2"},
                    {"name": "array3"},
                ],
                "metrics": BOTH,
            }
        )
        assert _requests(pure1_client) == {
            "get_arrays": 2,
            "post_arrays_simulations": 1,
        }

    def test_unknown_array_fails_before_the_simulation(self, pure1_client):
        _known_arrays(pure1_client, "array1")

        module = run_main(pure1_simulation, arrays=["array1", "Foo"])

        module.fail_json.assert_called_once_with(msg="Array Foo is not known to Pure1.")
        module.exit_json.assert_not_called()
        assert _requests(pure1_client) == {"get_arrays": 2}

    def test_empty_array_name_fails_before_any_request_of_the_module(
        self, pure1_client
    ):
        module = run_main(pure1_simulation, arrays=["array1", " "])

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        assert _requests(pure1_client) == {"get_arrays": 1}

    def test_arrays_is_required(self, pure1_client, client_factory):
        module = run_main(pure1_simulation)

        module.fail_json.assert_called_once_with(
            msg="missing required arguments: arrays"
        )
        client_factory.assert_not_called()

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param(
                {"arrays": []}, "arrays must name at least one array.", id="no array"
            ),
            pytest.param(
                {"projection_days": 0},
                "projection_days must be 1 or more, got 0",
                id="projection_days 0",
            ),
            pytest.param(
                {"projection_days": -30},
                "projection_days must be 1 or more, got -30",
                id="projection_days negative",
            ),
            pytest.param(
                {"training_days": 0},
                "training_days must be 1 or more, got 0",
                id="training_days 0",
            ),
            pytest.param(
                {"training_days": -1},
                "training_days must be 1 or more, got -1",
                id="training_days negative",
            ),
            pytest.param(
                {"projection_days": 36501},
                "projection_days must be 36500 or less, got 36501",
                id="projection_days above the bound",
            ),
            pytest.param(
                {"training_days": 10**12},
                "training_days must be 36500 or less, got 1000000000000",
                id="training_days above the bound",
            ),
            pytest.param(
                {"resolution": 0},
                "resolution must be 1 or more, got 0",
                id="resolution 0",
            ),
            pytest.param(
                {"resolution": -86400000},
                "resolution must be 1 or more, got -86400000",
                id="resolution negative",
            ),
            pytest.param(
                {"metrics": ["projected_load", "projected_iops"]},
                "value of metrics must be one or more of: projected_load, "
                "projected_capacity. Got no match for: projected_iops",
                id="metric that is no choice",
            ),
            pytest.param(
                {"aggregation": "median"},
                "value of aggregation must be one of: blended_max, average, "
                "got: median",
                id="aggregation that is no choice",
            ),
        ],
    )
    def test_refused_value_fails_before_the_client_is_created(
        self, pure1_client, client_factory, params, message
    ):
        params = dict({"arrays": ["array1"]}, **params)

        module = run_main(pure1_simulation, **params)

        module.fail_json.assert_called_once_with(msg=message)
        module.exit_json.assert_not_called()
        client_factory.assert_not_called()
        assert _requests(pure1_client) == {}

    @pytest.mark.parametrize(
        "option", ["projection_days", "training_days", "resolution"]
    )
    def test_number_of_another_type_is_refused(self, pure1_client, option):
        module = run_main(pure1_simulation, arrays=["array1"], **{option: "soon"})

        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        # ansible-core names the type differently from 2.19 on
        assert message.startswith("argument '{0}' is of type ".format(option))
        assert " and we were unable to convert to int" in message

    @pytest.mark.parametrize(
        "option",
        ["timeout", "steps", "array", "projection_start_time", "training_end_time"],
    )
    def test_module_has_no_other_option(self, pure1_client, client_factory, option):
        module = run_main(pure1_simulation, arrays=["array1"], **{option: 60})

        module.fail_json.assert_called_once()
        assert module.fail_json.call_args[1]["msg"].startswith(
            "Unsupported parameters for module: {0}.".format(option)
        )
        client_factory.assert_not_called()

    def test_options_are_documented_as_the_tests_of_this_class_pin_them(self):
        """Type, choices, default and required of each option; validate-modules
        of ansible-test compares the documentation with the argument spec"""
        documented = yaml.safe_load(pure1_simulation.DOCUMENTATION)["options"]

        assert {
            name: {key: value for key, value in entry.items() if key != "description"}
            for name, entry in documented.items()
        } == {
            "arrays": {"type": "list", "elements": "str", "required": True},
            "metrics": {
                "type": "list",
                "elements": "str",
                "choices": ["projected_load", "projected_capacity"],
                "default": ["projected_load", "projected_capacity"],
            },
            "aggregation": {"type": "str", "choices": ["blended_max", "average"]},
            "projection_days": {"type": "int"},
            "training_days": {"type": "int"},
            "resolution": {"type": "int"},
        }

    def test_older_sdk_fails_before_any_request_of_the_module(self, pure1_client):
        """A py-pure-client before 1.99.0 has no such method of the client"""
        pure1_client.post_arrays_simulations = None

        module = run_main(pure1_simulation, arrays=["array1"])

        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert message.startswith(
            "py-pure-client 1.99.0 or later is required: this module needs "
            "Client.post_arrays_simulations(), which the installed py-pure-client"
        )
        assert message.endswith(" does not have.")
        pure1_client.get_arrays.assert_called_once_with(limit=0)


class TestProjections:
    """How the projections of an answer are read and filed."""

    def test_projection_with_only_its_identity(self, pure1_client):
        """Every other field is null, and the summary with them"""
        bare = sdk_model(
            "PlanningSimulationMetric",
            name="projected_capacity",
            resources=[array_reference("array1")],
        )

        module = _run(pure1_client, [bare])

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_capacity=EMPTY_ROW)
        }

    def test_values_that_are_false_are_kept(self, pure1_client):
        """0, 0.0 and an empty string are values, not missing ones"""
        load = _load(
            unit="",
            aggregation="",
            resolution=0,
            as_of=0,
            data=[[0, 0], [1000, 0.0]],
        )

        module = _run(pure1_client, [load])

        assert _result(module)["arrays"]["array1"]["projected_load"] == {
            "unit": "",
            "aggregation": "",
            "resolution": 0,
            "as_of": "1970-01-01 00:00:00 UTC",
            "as_of_epoch": 0,
            "first": {"time": "1970-01-01 00:00:00 UTC", "time_epoch": 0, "value": 0},
            "last": {
                "time": "1970-01-01 00:00:01 UTC",
                "time_epoch": 1000,
                "value": 0.0,
            },
            "min": {"time": "1970-01-01 00:00:00 UTC", "time_epoch": 0, "value": 0},
            "max": {"time": "1970-01-01 00:00:00 UTC", "time_epoch": 0, "value": 0},
            "data": [[0, 0], [1000, 0.0]],
        }

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_of_a_projection_are_given_in_utc(self, pure1_client):
        module = _run(pure1_client, [_capacity()])

        row = _result(module)["arrays"]["array1"]["projected_capacity"]
        assert row["as_of"] == "2021-02-03 13:04:05 UTC"
        assert row["first"]["time"] == "2021-02-03 13:04:05 UTC"
        assert row["max"]["time"] == "2021-02-04 14:05:06 UTC"
        assert row["last"]["time"] == "2021-02-05 15:06:07 UTC"

    def test_summary_goes_by_the_time_of_the_points(self, pure1_client):
        """The points are returned as they came, in whatever order"""
        data = [[3000, 7.5], [1000, -2], [4000, 7.5], [2000, -2]]

        module = _run(pure1_client, [_load(data=data)])

        row = _result(module)["arrays"]["array1"]["projected_load"]
        assert row["data"] == [[3000, 7.5], [1000, -2], [4000, 7.5], [2000, -2]]
        assert row["first"] == {
            "time": "1970-01-01 00:00:01 UTC",
            "time_epoch": 1000,
            "value": -2,
        }
        assert row["last"] == {
            "time": "1970-01-01 00:00:04 UTC",
            "time_epoch": 4000,
            "value": 7.5,
        }
        # of several points with one value the earliest
        assert row["min"] == {
            "time": "1970-01-01 00:00:01 UTC",
            "time_epoch": 1000,
            "value": -2,
        }
        assert row["max"] == {
            "time": "1970-01-01 00:00:03 UTC",
            "time_epoch": 3000,
            "value": 7.5,
        }

    def test_projection_of_one_point(self, pure1_client):
        module = _run(pure1_client, [_load(data=[[1612357445000, 0.5]])])

        row = _result(module)["arrays"]["array1"]["projected_load"]
        point = {
            "time": "2021-02-03 13:04:05 UTC",
            "time_epoch": 1612357445000,
            "value": 0.5,
        }
        assert row["data"] == [[1612357445000, 0.5]]
        assert [row[key] for key in ("first", "last", "min", "max")] == [point] * 4

    def test_projection_without_points(self, pure1_client):
        load = _load()
        # the SDK reads an answer without validating it
        del load.data[:]

        module = _run(pure1_client, [load, _capacity(data=None)])

        module.warn.assert_not_called()
        array = _result(module)["arrays"]["array1"]
        for row, like in (
            (array["projected_load"], LOAD_ROW),
            (array["projected_capacity"], CAPACITY_ROW),
        ):
            assert row == dict(like, first=None, last=None, min=None, max=None, data=[])

    def test_point_without_a_time_or_a_value_does_not_count(self, pure1_client):
        """Pure1 sends what the SDK does not validate: such a point stays in
        data when it is a list, and the summary is of the other points"""
        load = _load()
        load.data[:] = [
            None,
            [],
            [1000],
            [2000, None],
            [None, 9.0],
            [3000, True],
            "4000",
            [5000, 0.5, "more"],
            [6000, 0.25],
        ]

        module = _run(pure1_client, [load])

        row = _result(module)["arrays"]["array1"]["projected_load"]
        assert row["data"] == [
            [],
            [1000],
            [2000, None],
            [None, 9.0],
            [3000, True],
            [5000, 0.5, "more"],
            [6000, 0.25],
        ]
        assert (
            row["first"]
            == row["max"]
            == {
                "time": "1970-01-01 00:00:05 UTC",
                "time_epoch": 5000,
                "value": 0.5,
            }
        )
        assert (
            row["last"]
            == row["min"]
            == {
                "time": "1970-01-01 00:00:06 UTC",
                "time_epoch": 6000,
                "value": 0.25,
            }
        )

    def test_projection_has_the_fields_of_the_response_model(self, pure1_client):
        """Field by field against PlanningSimulationMetric and its reference"""
        module = _run(pure1_client, [_load()])

        row = _result(module)["arrays"]["array1"]["projected_load"]
        fields = set(MODELS.PlanningSimulationMetric.__fields__)
        assert fields == {
            "as_of",
            "id",
            "name",
            "aggregation",
            "data",
            "resolution",
            "resources",
            "unit",
        }
        # name is the key of the row and resources tells its array; the id
        # of the projection is left out on purpose
        assert set(row) - {"as_of_epoch", "first", "last", "min", "max"} == fields - {
            "id",
            "name",
            "resources",
        }
        reference = MODELS.PlanningSimulationMetric.__fields__["resources"].type_
        assert reference is MODELS.FixedReferenceFqdn
        assert set(reference.__fields__) == {"id", "name", "resource_type", "fqdn"}
        assert set(MODELS.PlanningSimulationResponse.__fields__) == {
            "items",
            "total_item_count",
        }

    def test_array_is_told_by_the_id_of_the_reference(self, pure1_client):
        """Whatever name the projection has for it, or none"""
        projections = [
            _load(resources=[_reference("ARRAY1", id="array1-id")]),
            _capacity(
                resources=[
                    sdk_model("FixedReferenceFqdn", id="array2-id", fqdn="other")
                ]
            ),
        ]

        module = _run(pure1_client, projections, arrays=["array1", "array2"])

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW),
            "array2": _array_row("array2", projected_capacity=CAPACITY_ROW),
        }

    @pytest.mark.parametrize(
        "reference",
        [
            pytest.param(lambda: array_reference("array1"), id="no id"),
            pytest.param(
                lambda: array_reference("array1", id="another-id"),
                id="an id that no array of the task has",
            ),
        ],
    )
    def test_array_is_told_by_its_name_where_the_id_does_not(
        self, pure1_client, reference
    ):
        module = _run(pure1_client, [_load(resources=[reference()])])

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW)
        }

    def test_id_and_fqdn_of_an_array_are_those_of_get_arrays(self, pure1_client):
        _known_arrays(pure1_client, sdk_model("Array", id="a-1", name="array1"))
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [_load(resources=[_reference(id="a-1", fqdn="other.example.com")])]
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {
            "array1": {
                "id": "a-1",
                "fqdn": None,
                "projected_load": LOAD_ROW,
                "projected_capacity": {},
            }
        }

    def test_array_without_an_id_does_not_take_what_has_no_id(self, pure1_client):
        """No id is no match: a projection without one goes by its name"""
        _known_arrays(pure1_client, sdk_model("Array", name="array1"), "array2")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [
                _load(resources=[array_reference("array2")]),
                _capacity(resources=[array_reference("array1")]),
            ]
        )

        module = run_main(pure1_simulation, arrays=["array1", "array2"])

        module.warn.assert_not_called()
        assert _result(module)["arrays"] == {
            "array1": {
                "id": None,
                "fqdn": None,
                "projected_load": {},
                "projected_capacity": CAPACITY_ROW,
            },
            "array2": _array_row("array2", projected_load=LOAD_ROW),
        }

    def test_second_array_of_one_name_without_an_id(self, pure1_client):
        """It is left out, and a projection without an id is not taken for it"""
        _known_arrays(pure1_client, "array1", sdk_model("Array", name="array1"))
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [_load(resources=[array_reference("array1")])]
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW)
        }
        module.warn.assert_called_once_with(
            "Pure1 lists 1 arrays under a name that an earlier array of this "
            "task has: they and their projections are left out of the result."
        )

    def test_first_array_of_a_projection_counts(self, pure1_client):
        """A projection that names several arrays is not filed under each of
        them, and no array stays empty without a word"""
        load = _load(resources=[_reference("array2"), _reference("array1")])
        load.resources.insert(0, None)
        capacity = _capacity(resources=[_reference(), _reference("array2")])

        module = _run(pure1_client, [load, capacity], arrays=["array1", "array2"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_capacity=CAPACITY_ROW),
            "array2": _array_row("array2", projected_load=LOAD_ROW),
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 2 projections that name more than one array: each "
            "is in the result of the first array it names only."
        )

    def test_projection_that_names_several_arrays_and_is_left_out(self, pure1_client):
        """It is counted once, with what is left out"""
        projections = [
            _load(resources=[_reference("array9"), _reference()]),
            _capacity(),
            _capacity(resources=[_reference(), _reference("array9")]),
        ]

        module = _run(pure1_client, projections)

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_capacity=CAPACITY_ROW)
        }
        assert [call[0][0] for call in module.warn.call_args_list] == [
            "Pure1 returned 1 projections without a metric or without an array "
            "of this task: they are left out of the result.",
            "Pure1 returned 1 projections of a metric that the array already "
            "has a projection of: only the first of each is in the result.",
        ]

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(lambda: _load(name=None), id="no metric"),
            pytest.param(lambda: _load(name="projected_iops"), id="unknown metric"),
            pytest.param(
                lambda: _load(name="Projected_Load"), id="metric in another case"
            ),
            pytest.param(lambda: _load(resources=None), id="no resources"),
            pytest.param(_load_with_empty_resources, id="empty resources"),
            pytest.param(_load_with_null_resource, id="null resource"),
            pytest.param(
                lambda: _load(resources=[sdk_model("FixedReferenceFqdn", fqdn="a")]),
                id="array without id and name",
            ),
            pytest.param(
                lambda: _load("array9"), id="array that the task does not name"
            ),
            pytest.param(
                lambda: _load(resources=[array_reference("ARRAY1")]),
                id="name in another case and no id",
            ),
        ],
    )
    def test_projection_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module = _run(pure1_client, [broken(), _capacity()])

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_capacity=CAPACITY_ROW)
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 1 projections without a metric or without an array "
            "of this task: they are left out of the result."
        )

    def test_null_among_the_projections_is_left_out(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        answer = MODELS.PlanningSimulationResponse(items=[_load()], total_item_count=2)
        # the SDK reads an answer without validating it
        answer.items.append(None)
        pure1_client.post_arrays_simulations.return_value = (
            NEWEST.Client._create_valid_response(
                None, ApiResponse(200, {}, answer), _no_later_page, {}
            )
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW)
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 1 projections without a metric or without an array "
            "of this task: they are left out of the result."
        )

    def test_metric_that_was_not_asked_for_is_left_out(self, pure1_client):
        module = _run(
            pure1_client, [_load(), _capacity()], metrics=["projected_capacity"]
        )

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_capacity=CAPACITY_ROW)
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 1 projections without a metric or without an array "
            "of this task: they are left out of the result."
        )

    def test_projections_left_out_give_one_warning(self, pure1_client):
        projections = [
            _load(name=None),
            _load(),
            _capacity("array9"),
            _capacity(resources=None),
        ]

        module = _run(pure1_client, projections)

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW)
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 3 projections without a metric or without an array "
            "of this task: they are left out of the result."
        )

    def test_second_projection_of_a_metric_is_left_out(self, pure1_client):
        """No projection is lost without a word: the first one stays"""
        projections = [
            _load(),
            _load(aggregation="average", unit="%"),
            _capacity(),
            _load("array2"),
            _load(data=[[1000, 1]]),
        ]

        module = _run(pure1_client, projections, arrays=["array1", "array2"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(
                projected_load=LOAD_ROW, projected_capacity=CAPACITY_ROW
            ),
            "array2": _array_row("array2", projected_load=LOAD_ROW),
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 2 projections of a metric that the array already "
            "has a projection of: only the first of each is in the result."
        )

    def test_second_array_of_one_name_is_left_out(self, pure1_client):
        """Two arrays of one name are not merged: the first keeps the name"""
        _known_arrays(
            pure1_client,
            sdk_model("Array", id="first-id", name="array1", fqdn="first.example.com"),
            sdk_model(
                "Array", id="second-id", name="array1", fqdn="second.example.com"
            ),
            "array2",
        )
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [
                _capacity(resources=[_reference(id="second-id")]),
                _load(resources=[_reference(id="second-id")]),
                _load(resources=[_reference(id="first-id")]),
                _load("array2"),
            ]
        )

        module = run_main(pure1_simulation, arrays=["array1", "array2"])

        # the name is sent once
        assert _body(pure1_client)["resources"] == [
            {"name": "array1"},
            {"name": "array2"},
        ]
        result = _result(module)
        assert result["request"]["resources"] == ["array1", "array2"]
        assert result["arrays"] == {
            "array1": {
                "id": "first-id",
                "fqdn": "first.example.com",
                "projected_load": LOAD_ROW,
                "projected_capacity": {},
            },
            "array2": _array_row("array2", projected_load=LOAD_ROW),
        }
        module.warn.assert_called_once_with(
            "Pure1 lists 1 arrays under a name that an earlier array of this "
            "task has: they and their projections are left out of the result."
        )

    @pytest.mark.parametrize(
        "total, warning",
        [
            pytest.param(
                5,
                "Pure1 returned 2 of 5 projections: the result may be incomplete.",
                id="more than came",
            ),
            pytest.param(
                3,
                "Pure1 returned 2 of 3 projections: the result may be incomplete.",
                id="one more than came",
            ),
            pytest.param(2, None, id="as many as came"),
            pytest.param(None, None, id="no total"),
        ],
    )
    def test_fewer_projections_than_pure1_counts_give_a_warning(
        self, pure1_client, total, warning
    ):
        _known_arrays(pure1_client, "array1")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [_load(), _capacity()], total_item_count=total
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(
                projected_load=LOAD_ROW, projected_capacity=CAPACITY_ROW
            )
        }
        if warning is None:
            module.warn.assert_not_called()
        else:
            module.warn.assert_called_once_with(warning)

    def test_projections_that_pure1_counts_as_none_give_a_warning(self, pure1_client):
        """py-pure-client hands on no more items than Pure1 counts: with a
        count of 0 no projection of the answer can be read"""
        _known_arrays(pure1_client, "array1")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [_load(), _capacity()], total_item_count=0
        )

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {"array1": _array_row()}
        module.warn.assert_called_once_with(
            "Pure1 sent 2 projections and counted none: they cannot be read "
            "and are not in the result."
        )

    def test_every_warning_of_a_run(self, pure1_client):
        """Each kind that one answer can give, once, in one order, and each a
        sentence"""
        _known_arrays(
            pure1_client,
            "array1",
            sdk_model("Array", id="second-id", name="array1"),
        )
        pure1_client.post_arrays_simulations.return_value = _simulation_answer(
            [
                _capacity(resources=[_reference(), _reference("array9")]),
                _capacity(),
                _capacity("array9"),
                _capacity(resources=[_reference(id="second-id")]),
            ],
            total_item_count=7,
        )

        module = run_main(
            pure1_simulation,
            arrays=["array1"],
            metrics=["projected_capacity"],
            aggregation="average",
        )

        warnings = [call[0][0] for call in module.warn.call_args_list]
        assert warnings == [
            "aggregation applies to projected_load only, which metrics does "
            "not name: it is not sent to Pure1.",
            "Pure1 returned 4 of 7 projections: the result may be incomplete.",
            "Pure1 lists 1 arrays under a name that an earlier array of this "
            "task has: they and their projections are left out of the result.",
            "Pure1 returned 1 projections without a metric or without an array "
            "of this task: they are left out of the result.",
            "Pure1 returned 1 projections of a metric that the array already "
            "has a projection of: only the first of each is in the result.",
            "Pure1 returned 1 projections that name more than one array: each "
            "is in the result of the first array it names only.",
        ]
        assert all(warning.endswith(".") for warning in warnings)


class TestFailures:
    """A request that fails ends the task with one line of the shared helpers."""

    @pytest.mark.parametrize("method", ["get_arrays", "post_arrays_simulations"])
    @pytest.mark.parametrize(
        "answer, reason",
        [
            pytest.param(
                error_response(403, "Access denied"),
                " (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                error_response(400, "Bad request", "resources must not be empty"),
                " (HTTP 400): Bad request: resources must not be empty",
                id="error answer with context",
            ),
            pytest.param(
                error_response(404, "Not found", "No resources with name: array1"),
                " (HTTP 404): Not found: No resources with name: array1",
                id="array that Pure1 does not find",
            ),
            pytest.param(
                ErrorResponse(503, [], {}),
                " (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                error_response(429, "API rate limit exceeded"),
                " (HTTP 429): API rate limit exceeded",
                id="rate limit",
            ),
            pytest.param(
                PureError("token refused"), ": PureError: token refused", id="PureError"
            ),
            pytest.param(
                TimeoutError("The read operation timed out"),
                ": TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, answer, reason
    ):
        _known_arrays(pure1_client, "array1")
        pure1_client.post_arrays_simulations.return_value = _simulation_answer([])
        if method == "get_arrays":
            # the first answer is for the check of get_pure1
            pure1_client.get_arrays.side_effect = [valid_response(), answer]
        elif isinstance(answer, Exception):
            pure1_client.post_arrays_simulations.side_effect = answer
        else:
            pure1_client.post_arrays_simulations.return_value = answer

        module = run_main(pure1_simulation, arrays=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(method, reason)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        if method == "get_arrays":
            pure1_client.post_arrays_simulations.assert_not_called()

    def test_answer_that_has_a_body_without_items_fails_the_task(self, pure1_client):
        """py-pure-client 1.99.0 raises a bare AttributeError for such an
        answer, out of the call itself"""
        _known_arrays(pure1_client, "array1")

        def without_items(**kwargs):
            body = MODELS.PlanningSimulationResponse(total_item_count=0)
            return NEWEST.Client._create_valid_response(
                None, ApiResponse(200, {}, body), _no_later_page, kwargs
            )

        pure1_client.post_arrays_simulations.side_effect = without_items

        module = run_main(pure1_simulation, arrays=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request post_arrays_simulations failed: AttributeError"
        )
        module.exit_json.assert_not_called()


class _WireAnswer:
    """What the REST client of the SDK returns for a request: a status and a body"""

    def __init__(self, status, document):
        self.status = status
        self.reason = "OK"
        self.data = json.dumps(document).encode("utf-8")

    def getheaders(self):
        return {"content-type": "application/json"}

    def getheader(self, name, default=None):
        return self.getheaders().get(name, default)


class _Wire:
    """Stand-in for the REST client of the SDK, the last step before the network

    It keeps what the SDK hands it to send and answers every POST with one
    JSON document.
    """

    def __init__(self, document):
        self.document = document
        self.sent = []

    def post_request(self, url, **arguments):
        self.sent.append(dict(arguments, url=url))
        return _WireAnswer(200, self.document)


def _through_the_sdk(wire):
    """Return a side_effect that makes the call as the SDK client makes it

    The request goes through the API class of the SDK and its serialization
    down to wire, and the JSON document of the answer back through the
    deserialization of the SDK and the client code that builds the answer.
    """
    configuration = Configuration()
    configuration.host = "https://pure1.example.com"
    api_client = NEWEST.client.ApiClient(configuration, models_package=MODELS)
    api_client.rest_client = wire
    planning = NEWEST.client.api.PlanningApi(api_client)
    # api17_arrays_simulations_post_with_http_info in the client of REST 1.7
    (function,) = [
        name
        for name in dir(planning)
        if name.endswith("_arrays_simulations_post_with_http_info")
    ]

    def call(**kwargs):
        response = getattr(planning, function)(**kwargs)
        return NEWEST.Client._create_valid_response(
            None, response, _no_later_page, kwargs
        )

    return call


# An answer in the names of the REST reference, as the response models of
# the SDK describe it: _as_of is the name of as_of on the wire
WIRE_ANSWER = {
    "total_item_count": 2,
    "items": [
        {
            "_as_of": 1795515330000,
            "id": "load-of-array1",
            "name": "projected_load",
            "aggregation": "blended_max",
            "data": [
                [1795478400000, 0.41],
                [1795564800000, 0.44],
                [1795651200000, 0.39],
                [1795737600000, 0.42],
            ],
            "resolution": 86400000,
            "resources": [
                {
                    "id": "array1-id",
                    "name": "array1",
                    "resource_type": "arrays",
                    "fqdn": "array1.example.com",
                }
            ],
            "unit": "",
        },
        {
            "_as_of": 1612357445000,
            "id": "capacity-of-array1",
            "name": "projected_capacity",
            "aggregation": "average",
            "data": [
                [1612357445000, 5000],
                [1612447506000, 5200],
                [1612537567000, 4900],
            ],
            "resolution": 90061000,
            "resources": [
                {
                    "id": "array1-id",
                    "name": "array1",
                    "resource_type": "arrays",
                    "fqdn": "array1.example.com",
                }
            ],
            "unit": "B",
        },
    ],
}


class TestThroughTheSdk:
    """The request down to what the SDK would send, and an answer from JSON."""

    def test_body_reaches_the_wire_as_the_module_built_it(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        wire = _Wire(WIRE_ANSWER)
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        run_main(
            pure1_simulation,
            arrays=["array1"],
            aggregation="average",
            projection_days=30,
            training_days=60,
            resolution=3600000,
        )

        (sent,) = wire.sent
        assert sent["url"].startswith("https://pure1.example.com/api/1.")
        assert sent["url"].endswith("/arrays/simulations")
        assert sent["body"] == {
            "resources": [{"name": "array1"}],
            "metrics": [
                {"name": "projected_load", "aggregation": "average"},
                {"name": "projected_capacity"},
            ],
            "projection_end_time": 1798070400000,
            "training_start_time": 1790294400000,
            "resolution": 3600000,
        }
        assert sent["body"] == _body(pure1_client)
        assert not sent["query_params"]
        assert not sent["post_params"]

    def test_answer_in_the_names_of_the_rest_reference_is_read(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        wire = _Wire(WIRE_ANSWER)
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1"])

        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_simulation={
                "request": DEFAULT_REQUEST,
                "arrays": {
                    "array1": _array_row(
                        projected_load=LOAD_ROW, projected_capacity=CAPACITY_ROW
                    )
                },
            },
        )

    def test_answer_with_nulls_and_without_fields_is_read(self, pure1_client):
        """What the SDK does not validate reaches the module as it came"""
        _known_arrays(pure1_client, "array1", "array2")
        document = {
            "items": [
                {"name": "projected_load", "resources": [{"name": "array1"}]},
                {
                    "name": "projected_capacity",
                    "resources": [None, {"id": "array2-id"}],
                    "data": [[1000, None], None, [2000, 3]],
                    "unit": None,
                },
                {"name": "projected_load", "resources": None, "data": None},
                {"resources": [{"id": "array1-id"}]},
            ]
        }
        wire = _Wire(document)
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1", "array2"])

        point = {"time": "1970-01-01 00:00:02 UTC", "time_epoch": 2000, "value": 3}
        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=EMPTY_ROW),
            "array2": _array_row(
                "array2",
                projected_capacity=dict(
                    EMPTY_ROW,
                    first=point,
                    last=point,
                    min=point,
                    max=point,
                    data=[[1000, None], [2000, 3]],
                ),
            ),
        }
        module.warn.assert_called_once_with(
            "Pure1 returned 2 projections without a metric or without an array "
            "of this task: they are left out of the result."
        )

    def test_answer_with_no_projection_is_read(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        wire = _Wire({"items": [], "total_item_count": 0})
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1"])

        module.warn.assert_not_called()
        assert _result(module) == {
            "request": DEFAULT_REQUEST,
            "arrays": {"array1": _array_row()},
        }

    def test_answer_that_counts_no_projection_and_sends_two(self, pure1_client):
        """The SDK hands on no item of such an answer: the task is told"""
        _known_arrays(pure1_client, "array1")
        wire = _Wire(dict(WIRE_ANSWER, total_item_count=0))
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {"array1": _array_row()}
        module.warn.assert_called_once_with(
            "Pure1 sent 2 projections and counted none: they cannot be read "
            "and are not in the result."
        )

    def test_count_between_one_and_the_projections_sent_is_not_seen(self, pure1_client):
        """A limit of py-pure-client 1.99.0, pinned to be seen when it moves:
        the SDK hands on as many items as Pure1 counts, and nothing that it
        makes public tells that the answer held more"""
        _known_arrays(pure1_client, "array1")
        wire = _Wire(dict(WIRE_ANSWER, total_item_count=1))
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {
            "array1": _array_row(projected_load=LOAD_ROW)
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "projection, row, warnings",
        [
            pytest.param(
                {"resources": [{"name": {"is": "array1"}}]},
                {},
                [
                    "Pure1 returned 1 projections without a metric or without "
                    "an array of this task: they are left out of the result."
                ],
                id="array name that is no string",
            ),
            pytest.param(
                {"resources": [{"id": ["array1-id"]}]},
                {},
                [
                    "Pure1 returned 1 projections without a metric or without "
                    "an array of this task: they are left out of the result."
                ],
                id="array id that is no string",
            ),
            pytest.param(
                {"resources": [{"id": ["array1-id"], "name": "array1"}]},
                EMPTY_ROW,
                [],
                id="array id that is no string beside the name",
            ),
            pytest.param(
                {"resources": [{"id": "array1-id"}], "data": 5},
                EMPTY_ROW,
                [],
                id="points that are no list",
            ),
        ],
    )
    def test_answer_that_breaks_the_types_of_the_model_is_read(
        self, pure1_client, projection, row, warnings
    ):
        """The SDK builds its models from an answer without validating it"""
        _known_arrays(pure1_client, "array1")
        wire = _Wire({"items": [dict(projection, name="projected_load")]})
        pure1_client.post_arrays_simulations.side_effect = _through_the_sdk(wire)

        module = run_main(pure1_simulation, arrays=["array1"])

        assert _result(module)["arrays"] == {"array1": _array_row(projected_load=row)}
        assert [call[0][0] for call in module.warn.call_args_list] == warnings


def _contains(block):
    """Return the keys under a contains block of RETURN, by their path"""
    paths = set()
    for key, entry in block.items():
        paths.add(key)
        for path in _contains(entry.get("contains", {})):
            paths.add(key + "." + path)
    return paths


def _keys(value):
    """Return the keys of a value of the result, by their path

    A list of dictionaries counts as its first entry.
    """
    if isinstance(value, list) and value and isinstance(value[0], dict):
        value = value[0]
    if not isinstance(value, dict):
        return set()
    paths = set()
    for key, entry in value.items():
        paths.add(key)
        paths.update(key + "." + path for path in _keys(entry))
    return paths


def _typed(documented, value, path="pure1_simulation"):
    """Check the type RETURN gives for a value and for every key under it

    A float is a number as Pure1 sent it, which RETURN says: a whole number
    is an int.
    """
    types_of = {
        "dict": (dict,),
        "float": (float, int),
        "int": (int,),
        "list": (list,),
        "str": (str,),
    }
    assert type(value) in types_of[documented["type"]], path
    if isinstance(value, list):
        elements = types_of[documented["elements"]]
        assert value and all(type(entry) in elements for entry in value), path
        value = value[0]
    for key, entry in documented.get("contains", {}).items():
        _typed(entry, value[key], path + "." + key)


def _template(text, variables):
    """Return what a template of a task gives, in the type of its value

    As in a playbook, a key that a variable does not have is an error
    (jinja2.UndefinedError).
    """
    environment = NativeEnvironment(undefined=jinja2.StrictUndefined)
    environment.filters["dict2items"] = FilterModule().filters()["dict2items"]
    value = environment.from_string(text).render(**variables)
    if isinstance(value, jinja2.Undefined):
        # a template that is one expression hands a missing value on
        raise jinja2.UndefinedError(text)
    return value


def _conditions(expressions, variables):
    """Return the results of the conditions of a when or a that, in order

    As Ansible does, the conditions after one that is false are not looked at.
    """
    if isinstance(expressions, str):
        expressions = [expressions]
    results = []
    for expression in expressions:
        results.append(bool(_template("{{ " + expression + " }}", variables)))
        if not results[-1]:
            break
    return results


def _play_examples(pure1_client, **projections):
    """Run the tasks of EXAMPLES in their order and return what is read

    A task of the module runs through main() with the options of the example
    and registers its result; projections gives, by the name the task
    registers, the projections Pure1 answers it with. A task that reads a
    result gives a list with its templates rendered for each item of its
    loop, once when it has no loop: when and that as the results of their
    conditions, and nothing else for an item that when leaves out.
    """
    registered = {}
    read = []
    for task in yaml.safe_load(pure1_simulation.EXAMPLES):
        options = task.get("everpure.pure1.pure1_simulation")
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            module = _run(pure1_client, projections[task["register"]], **options)
            registered[task["register"]] = module.exit_json.call_args[1]
            continue
        (action,) = set(task) - {"name", "when", "loop", "vars"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        rendered = []
        for item in items:
            variables = dict(registered, item=item)
            for name, value in task.get("vars", {}).items():
                variables[name] = _template(value, variables)
            made = {}
            if "when" in task:
                made["when"] = _conditions(task["when"], variables)
            if all(made.get("when", [True])):
                for key, value in task[action].items():
                    if key == "that":
                        made[key] = _conditions(value, variables)
                    else:
                        made[key] = _template(value, variables)
            rendered.append(made)
        read.append(rendered)
    return read


class TestDocumentation:
    """RETURN and EXAMPLES against what the module returns."""

    def _full(self, pure1_client):
        """Return a result in which every key has a value"""
        module = _run(
            pure1_client,
            [_load(aggregation="average"), _capacity()],
            aggregation="average",
            projection_days=30,
            training_days=60,
            resolution=3600000,
        )
        return module.exit_json.call_args[1]

    def test_return_names_the_one_key_of_the_result(self, pure1_client):
        result = self._full(pure1_client)

        documented = yaml.safe_load(pure1_simulation.RETURN)
        assert set(result) - {"changed"} == set(documented) == {"pure1_simulation"}
        assert set(result["pure1_simulation"]) == set(
            documented["pure1_simulation"]["contains"]
        )
        assert documented["pure1_simulation"]["returned"] == "always"

    def test_return_lists_every_key_of_the_result_and_no_other(self, pure1_client):
        result = self._full(pure1_client)["pure1_simulation"]

        documented = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]
        # arrays is keyed by array name: its contains tells the keys of one
        assert _contains(documented["contains"]) == _keys(
            {"request": result["request"], "arrays": result["arrays"]["array1"]}
        )

    def test_both_projections_are_documented_with_the_same_keys(self):
        documented = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]
        array = documented["contains"]["arrays"]["contains"]

        load = array["projected_load"]["contains"]
        capacity = array["projected_capacity"]["contains"]
        assert _contains(load) == _contains(capacity)
        assert {key: entry["type"] for key, entry in load.items()} == {
            key: entry["type"] for key, entry in capacity.items()
        }

    def test_every_key_of_return_is_an_identifier(self):
        """validate-modules of ansible-core 2.21 refuses any other key"""
        documented = yaml.safe_load(pure1_simulation.RETURN)

        for path in _contains(documented):
            assert all(key.isidentifier() for key in path.split(".")), path

    def test_return_gives_the_type_of_every_key(self, pure1_client):
        result = self._full(pure1_client)["pure1_simulation"]

        documented = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]
        assert documented["type"] == "dict"
        _typed(documented["contains"]["request"], result["request"], "request")
        array = dict(documented["contains"]["arrays"], type="dict")
        _typed(array, result["arrays"]["array1"], "arrays")
        # the values of the load are floats and those of the capacity whole
        # numbers, and RETURN gives one type for both
        row = result["arrays"]["array1"]
        assert isinstance(row["projected_load"]["max"]["value"], float)
        assert isinstance(row["projected_capacity"]["max"]["value"], int)

    def test_sample_has_the_shape_of_a_result(self, pure1_client):
        result = self._full(pure1_client)["pure1_simulation"]

        sample = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]["sample"]
        assert list(sample) == ["request", "arrays"]
        assert list(sample["arrays"]) == ["array1"]
        array = sample["arrays"]["array1"]
        # the sample asks for the load only: the capacity is empty
        assert array["projected_capacity"] == {}
        assert sample["request"]["metrics"] == [
            {"name": "projected_load", "aggregation": "average"}
        ]
        like = result["arrays"]["array1"]
        assert _keys({"request": sample["request"], "arrays": array}) == _keys(
            {
                "request": result["request"],
                "arrays": dict(like, projected_capacity={}),
            }
        )
        documented = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]
        _typed(documented["contains"]["request"], sample["request"], "request")
        entry = dict(documented["contains"]["arrays"], type="dict")
        del entry["contains"]["projected_capacity"]
        _typed(entry, dict(array), "arrays")

    def test_sample_is_what_the_module_makes_of_its_points(self, pure1_client):
        """The times of the sample and its summary fit its data"""
        sample = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]["sample"]
        request = sample["request"]
        load = sample["arrays"]["array1"]["projected_load"]
        projection = _load(
            unit=load["unit"],
            aggregation=load["aggregation"],
            resolution=load["resolution"],
            as_of=load["as_of_epoch"],
            data=load["data"],
        )
        # the sample is of a task that ran on the day of its first point
        days = (request["projection_end_time_epoch"] - MIDNIGHT) // DAY
        history = (MIDNIGHT - request["training_start_time_epoch"]) // DAY

        module = _run(
            pure1_client,
            [projection],
            metrics=["projected_load"],
            aggregation="average",
            projection_days=days,
            training_days=history,
            resolution=request["resolution"],
        )

        assert load["data"][0][0] == MIDNIGHT
        assert _result(module) == {
            "request": request,
            "arrays": {
                "array1": dict(
                    sample["arrays"]["array1"], id="array1-id", projected_load=load
                )
            },
        }

    def test_sample_names_no_real_array(self):
        sample = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]["sample"]

        assert sample["request"]["resources"] == ["array1"]
        assert sample["arrays"]["array1"]["fqdn"] == "array1.example.com"

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "description",
                "C(changed) is therefore always false, and the request is sent "
                "in check mode as well.",
                id="check mode",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.arrays.description",
                "At least one name is required.",
                id="an empty list of arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.arrays.description",
                "A name that Pure1 does not know fails the task before any "
                "projection is asked for.",
                id="an unknown array",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.metrics.description",
                "Both when not set or empty.",
                id="an empty list of metrics",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.aggregation.description",
                "When I(metrics) does not name C(projected_load) it is not sent "
                "and the module gives a warning.",
                id="aggregation without the load",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.projection_days.description",
                "The number of days to project, 1 to 36500.",
                id="projection_days",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.training_days.description",
                "The number of days of history to compute the projection from, "
                "1 to 36500.",
                id="training_days",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.resolution.description",
                "in milliseconds, 1 or more.",
                id="resolution",
            ),
            pytest.param(
                "DOCUMENTATION",
                "notes",
                "This module needs py-pure-client 1.99.0 or later.",
                id="the release of the SDK",
            ),
            pytest.param(
                "DOCUMENTATION",
                "notes",
                "The module makes two requests to Pure1 whatever the options are",
                id="the requests",
            ),
            pytest.param(
                "RETURN",
                "pure1_simulation.contains.arrays.description",
                "Every array of I(arrays) is listed, also one that Pure1 "
                "returned no projection for.",
                id="an array without a projection",
            ),
            pytest.param(
                "RETURN",
                "pure1_simulation.contains.arrays.contains.projected_load.description",
                "An empty dictionary when I(metrics) does not name "
                "C(projected_load) or Pure1 returned no such projection for the "
                "array.",
                id="no projection of the load",
            ),
            pytest.param(
                "RETURN",
                "pure1_simulation.contains.arrays.contains.projected_capacity"
                ".contains.min.description",
                "the earliest of them when several points have it.",
                id="several points of one value",
            ),
            pytest.param(
                "RETURN",
                "pure1_simulation.contains.arrays.contains.projected_load"
                ".contains.first.description",
                "Null when the projection has no point with a time and a value.",
                id="no point",
            ),
            pytest.param(
                "RETURN",
                "pure1_simulation.contains.arrays.contains.projected_capacity"
                ".contains.last.contains.value.description",
                "an integer when Pure1 sends a whole number.",
                id="a whole number",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_simulation, block))
        for key in path.split("."):
            entry = entry[key]
        if isinstance(entry, str):
            entry = [entry]

        assert says in " ".join(" ".join(entry).split())

    def test_examples_use_every_option_of_the_module(self):
        options = set(yaml.safe_load(pure1_simulation.DOCUMENTATION)["options"])
        used = set()
        for task in yaml.safe_load(pure1_simulation.EXAMPLES):
            arguments = task.get("everpure.pure1.pure1_simulation")
            if arguments:
                used.update(set(arguments) - {"app_id", "key_file"})
        assert used == options

    def test_first_example_shows_the_credentials(self):
        first = yaml.safe_load(pure1_simulation.EXAMPLES)[0]

        arguments = first["everpure.pure1.pure1_simulation"]
        assert arguments["app_id"] == "pure1:apikey:dssf2331sd"
        assert arguments["key_file"] == "/home/user/private.pem"

    def test_examples_read_what_the_module_returns(self, pure1_client):
        """The tasks after a task of the module, on the result it registered"""
        read = _play_examples(
            pure1_client,
            projection=[_load(), _capacity()],
            month=[_load(aggregation="average")],
            capacity=[
                _capacity(data=[[1612357445000, 5000], [1612537567000, 5600]]),
                _capacity("array2"),
            ],
        )

        assert read == [
            [
                {"msg": "array1: {0}".format(CAPACITY_ROW["last"])},
                {"msg": "array2: no projection"},
            ],
            [
                {
                    "that": [True, True],
                    "fail_msg": "The load of array1 has no projection or is "
                    "projected to rise by more than half",
                }
            ],
            [
                {
                    "when": [True, True],
                    "msg": "array1 is projected to grow from 5000 to 5600 by "
                    "2021-02-05 15:06:07 UTC",
                },
                # from 5000 to 4900
                {"when": [True, False]},
            ],
        ]

    def test_checks_of_the_examples_fail_on_what_they_look_for(self, pure1_client):
        read = _play_examples(
            pure1_client,
            # a projection without a point, whose last point is null, for
            # array1 and no projection for array2
            projection=[_capacity(data=[])],
            month=[_load(data=[[1000, 0.2], [2000, 0.31], [3000, 0.25]])],
            capacity=[_capacity("array2", data=[])],
        )

        assert [task[0].get("that") for task in read] == [None, [True, False], None]
        assert read[0] == [
            {"msg": "array1: no projection"},
            {"msg": "array2: no projection"},
        ]
        # an array without a projection and one without a point are left out
        assert read[2] == [{"when": [False]}, {"when": [False]}]

    def test_check_of_the_example_fails_without_a_projection(self, pure1_client):
        read = _play_examples(pure1_client, projection=[], month=[], capacity=[])

        assert read[1][0]["that"] == [False]

    def test_examples_ask_for_what_their_names_say(self, pure1_client):
        """The body of the request of each example"""
        _play_examples(pure1_client, projection=[], month=[], capacity=[])

        calls = pure1_client.post_arrays_simulations.call_args_list
        assert [call[1]["planning_simulation_post"] for call in calls] == [
            {
                "resources": [{"name": "array1"}, {"name": "array2"}],
                "metrics": BOTH,
            },
            {
                "resources": [{"name": "array1"}],
                "metrics": [{"name": "projected_load", "aggregation": "average"}],
                "projection_end_time": MIDNIGHT + 30 * DAY,
                "training_start_time": MIDNIGHT - 60 * DAY,
                "resolution": 86400000,
            },
            {
                "resources": [{"name": "array1"}, {"name": "array2"}],
                "metrics": [{"name": "projected_capacity"}],
                "projection_end_time": MIDNIGHT + 90 * DAY,
            },
        ]

    def test_times_of_the_documentation_are_those_of_pure1_time(self):
        sample = yaml.safe_load(pure1_simulation.RETURN)["pure1_simulation"]["sample"]
        load = sample["arrays"]["array1"]["projected_load"]

        assert load["as_of"] == pure1_time(load["as_of_epoch"])
        for key in ("first", "last", "min", "max"):
            assert load[key]["time"] == pure1_time(load[key]["time_epoch"])
        for key in ("projection_end_time", "training_start_time"):
            assert sample["request"][key] == pure1_time(
                sample["request"][key + "_epoch"]
            )
