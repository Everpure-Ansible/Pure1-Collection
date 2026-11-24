# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_volume_metrics module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest
from unittest.mock import patch

from pypureclient import ApiError, ErrorResponse

from plugins.module_utils.pure1 import filter_string
from plugins.modules import pure1_volume_metrics
from plugins.modules.pure1_volume_metrics import (
    generate_metrics_dict,
    list_volume_metrics,
    MAX_TIME_SERIES,
)
from tests.unit.conftest import (
    ansible_module,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)

CATALOG = [
    "volume_physical_capacity",
    "volume_provisioned_capacity",
    "volume_write_iops",
]


def _make_volume(serial="ABC123", name="vol1", vid="id-1"):
    return sdk_model("Volume", serial=serial, name=name, id=vid)


def _make_series(vid, vname, metric, data, unit="B"):
    return sdk_model(
        "MetricHistory",
        name=metric,
        unit=unit,
        resources=[sdk_model("FixedReferenceFqdn", id=vid, name=vname)],
        data=data,
    )


def _make_metric(name):
    return sdk_model(
        "Metric",
        name=name,
        unit="B",
        description="Used space",
        resource_types=["volumes"],
        availabilities=[
            sdk_model(
                "MetricAvailability",
                resolution=86400000,
                retention=34128000000,
                aggregations=["avg", "max"],
            ),
        ],
    )


def _client(client, volumes=None, history=None):
    """Give the client stand-in the catalog, the volumes and one history answer.

    A run that asks for the history more than once gives get_metrics_history a
    side_effect: the items of one answer can be read once.
    """
    client.get_metrics.return_value = valid_response(
        [_make_metric(name) for name in CATALOG]
    )
    client.get_volumes.return_value = valid_response(
        [_make_volume()] if volumes is None else volumes
    )
    client.get_metrics_history.return_value = valid_response(
        [] if history is None else history
    )
    return client


def _module(**params):
    defaults = {
        "array": None,
        "volumes": None,
        "metrics": ["volume_physical_capacity"],
        "aggregation": "avg",
        "resolution": 86400000,
        "window": 86400,
        "list_available": False,
    }
    defaults.update(params)
    return ansible_module(**defaults)


def _failure(module, client):
    """Run the module function, expect fail_json and return its message."""
    with pytest.raises(SystemExit):
        generate_metrics_dict(module, client)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


class TestListVolumeMetrics:
    def test_catalog_is_mapped(self, pure1_client):
        client = _client(pure1_client)
        client.get_metrics.return_value = valid_response(
            [_make_metric("volume_physical_capacity")]
        )

        result = list_volume_metrics(_module(), client)

        client.get_metrics.assert_called_once_with(resource_types=["volumes"])
        assert result == [
            {
                "name": "volume_physical_capacity",
                "unit": "B",
                "description": "Used space",
                "resource_types": ["volumes"],
                "availabilities": [
                    {
                        "resolution": 86400000,
                        "retention": 34128000000,
                        "aggregations": ["avg", "max"],
                    }
                ],
            }
        ]

    def test_unset_fields_are_none(self, pure1_client):
        client = _client(pure1_client)
        client.get_metrics.return_value = valid_response(
            [sdk_model("Metric", name="volume_data_reduction")]
        )

        result = list_volume_metrics(_module(), client)

        assert result == [
            {
                "name": "volume_data_reduction",
                "unit": None,
                "description": None,
                "resource_types": None,
                "availabilities": [],
            }
        ]

    def test_unset_availability_fields_are_none(self, pure1_client):
        client = _client(pure1_client)
        client.get_metrics.return_value = valid_response(
            [
                sdk_model(
                    "Metric",
                    name="volume_data_reduction",
                    availabilities=[sdk_model("MetricAvailability")],
                )
            ]
        )

        result = list_volume_metrics(_module(), client)

        assert result[0]["availabilities"] == [
            {"resolution": None, "retention": None, "aggregations": None}
        ]

    def test_metric_without_name_is_left_out(self, pure1_client):
        client = _client(pure1_client)
        client.get_metrics.return_value = valid_response(
            [sdk_model("Metric", unit="B"), _make_metric("volume_write_iops")]
        )

        result = list_volume_metrics(_module(), client)

        assert [metric["name"] for metric in result] == ["volume_write_iops"]

    def test_error_response_fails_with_api_message(self, pure1_client):
        module = _module()
        client = _client(pure1_client)
        client.get_metrics.return_value = error_response(500, "Internal server error")

        with pytest.raises(SystemExit):
            list_volume_metrics(module, client)

        assert "Internal server error" in module.fail_json.call_args[1]["msg"]
        module.fail_json.assert_called_once_with(
            msg="Failed to list volume metrics: Internal server error"
        )


class TestRequestValidation:
    def test_unknown_metric_fails_before_volumes_are_listed(self, pure1_client):
        module = _module(metrics=["volume_physical_capacity", "volume_total_used"])
        client = _client(pure1_client)

        msg = _failure(module, client)

        assert "Unknown volume metric(s): volume_total_used." in msg
        assert ", ".join(CATALOG) in msg
        client.get_volumes.assert_not_called()
        client.get_metrics_history.assert_not_called()

    def test_empty_metrics_list_fails(self, pure1_client):
        module = _module(metrics=[])
        client = _client(pure1_client)

        msg = _failure(module, client)

        assert "at least one volume metric" in msg
        assert ", ".join(CATALOG) in msg
        client.get_volumes.assert_not_called()

    def test_empty_volumes_list_fails(self, pure1_client):
        module = _module(volumes=[])
        client = _client(pure1_client)

        msg = _failure(module, client)

        assert msg == "volumes must name at least one volume"
        client.get_volumes.assert_not_called()


class TestVolumeSelection:
    def test_all_volumes_by_default(self, pure1_client):
        client = _client(pure1_client)

        generate_metrics_dict(_module(), client)

        client.get_volumes.assert_called_once_with()

    def test_array_filter_is_applied(self, pure1_client):
        client = _client(pure1_client)

        generate_metrics_dict(_module(array="myarray"), client)

        client.get_volumes.assert_called_once_with(filter="arrays.name='myarray'")

    def test_array_name_is_escaped_in_the_filter(self, pure1_client):
        client = _client(pure1_client)

        generate_metrics_dict(_module(array="my'arr\\ay"), client)

        client.get_volumes.assert_called_once_with(
            filter="arrays.name='my\\'arr\\\\ay'"
        )

    def test_filter_string_is_the_one_of_module_utils(self):
        assert pure1_volume_metrics.filter_string is filter_string

    def test_volumes_name_filter_is_applied(self, pure1_client):
        client = _client(
            pure1_client,
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")],
        )

        generate_metrics_dict(_module(volumes=["vol1", "vol2"]), client)

        client.get_volumes.assert_called_once_with(names=["vol1", "vol2"])

    def test_array_and_volumes_are_combined(self, pure1_client):
        client = _client(pure1_client)

        result = generate_metrics_dict(
            _module(array="myarray", volumes=["vol1"]), client
        )

        client.get_volumes.assert_called_once_with(
            filter="arrays.name='myarray'", names=["vol1"]
        )
        assert list(result) == ["ABC123"]

    def test_volume_name_in_another_case_is_found_on_the_array(self, pure1_client):
        # Pure1 matches names in any case and returns the stored name.
        client = _client(pure1_client, volumes=[_make_volume(name="Vol1")])

        result = generate_metrics_dict(
            _module(array="myarray", volumes=["vOL1"]), client
        )

        assert list(result) == ["ABC123"]

    def test_named_volume_missing_from_the_array_fails(self, pure1_client):
        module = _module(array="myarray", volumes=["vol1", "vol2"])
        client = _client(pure1_client)

        msg = _failure(module, client)

        assert msg == "Volume(s) vol2 not found on array myarray"
        client.get_metrics_history.assert_not_called()

    def test_unknown_volume_name_fails_with_api_message(self, pure1_client):
        module = _module(volumes=["no-such-volume"])
        client = _client(pure1_client)
        client.get_volumes.return_value = error_response(
            404, "Not found", "No resources with name: no-such-volume"
        )

        msg = _failure(module, client)

        assert "No resources with name: no-such-volume" in msg
        assert msg == (
            "Failed to list volumes: Not found: No resources with name: no-such-volume"
        )
        client.get_metrics_history.assert_not_called()

    def test_error_response_without_errors_reports_the_status(self, pure1_client):
        module = _module()
        client = _client(pure1_client)
        client.get_volumes.return_value = ErrorResponse(403, [], {})

        msg = _failure(module, client)

        assert msg == "Failed to list volumes: HTTP status 403"

    def test_every_error_of_the_answer_is_reported(self, pure1_client):
        module = _module()
        client = _client(pure1_client)
        client.get_volumes.return_value = ErrorResponse(
            400,
            [
                ApiError(context="filter=arrays.name='a", message="Invalid filter"),
                ApiError(context=None, message="Bad request"),
            ],
            {},
        )

        msg = _failure(module, client)

        assert msg == (
            "Failed to list volumes: Invalid filter: filter=arrays.name='a; Bad request"
        )

    def test_volume_listing_exception_fails(self, pure1_client):
        module = _module()
        client = _client(pure1_client)
        client.get_volumes.side_effect = Exception("connection timed out")

        msg = _failure(module, client)

        assert "connection timed out" in msg
        assert msg == "Failed to list volumes: connection timed out"
        client.get_metrics_history.assert_not_called()

    def test_volume_without_serial_or_id_is_left_out(self, pure1_client):
        client = _client(
            pure1_client,
            volumes=[
                _make_volume(),
                sdk_model("Volume", name="vol2", id="id-2"),
                sdk_model("Volume", name="vol3", serial="GHI789"),
            ],
        )

        result = generate_metrics_dict(_module(), client)

        assert list(result) == ["ABC123"]
        assert client.get_metrics_history.call_args[1]["resource_ids"] == ["id-1"]

    def test_volume_without_name_is_returned_with_name_none(self, pure1_client):
        client = _client(
            pure1_client, volumes=[sdk_model("Volume", serial="DEF456", id="id-2")]
        )

        result = generate_metrics_dict(_module(), client)

        assert result == {"DEF456": {"id": "id-2", "name": None, "metrics": {}}}

    def test_volume_without_name_does_not_break_the_array_check(self, pure1_client):
        client = _client(
            pure1_client,
            volumes=[sdk_model("Volume", serial="DEF456", id="id-2"), _make_volume()],
        )

        result = generate_metrics_dict(
            _module(array="myarray", volumes=["vol1"]), client
        )

        assert list(result) == ["DEF456", "ABC123"]


class TestGenerateMetricsDict:
    def test_latest_data_point_is_used(self, pure1_client):
        client = _client(
            pure1_client,
            history=[
                _make_series(
                    "id-1",
                    "vol1",
                    "volume_physical_capacity",
                    [[1000, 10], [2000, 25]],
                ),
            ],
        )

        result = generate_metrics_dict(_module(), client)

        entry = result["ABC123"]
        assert entry["id"] == "id-1"
        assert entry["name"] == "vol1"
        assert entry["metrics"]["volume_physical_capacity"] == {
            "unit": "B",
            "timestamp": 2000,
            "value": 25,
        }

    def test_request_is_built_from_the_options(self, pure1_client):
        metrics = ["volume_physical_capacity", "volume_write_iops"]
        module = _module(
            aggregation="max", resolution=3600000, window=7200, metrics=metrics
        )
        client = _client(pure1_client)

        with patch(
            "plugins.modules.pure1_volume_metrics.time.time",
            return_value=1700000000.9,
        ):
            generate_metrics_dict(module, client)

        client.get_metrics_history.assert_called_once_with(
            aggregation="max",
            resolution=3600000,
            start_time=1699992800000,
            end_time=1700000000000,
            names=metrics,
            resource_ids=["id-1"],
        )

    def test_window_shorter_than_resolution_is_accepted(self, pure1_client):
        module = _module(window=7200)
        client = _client(
            pure1_client,
            history=[
                _make_series("id-1", "vol1", "volume_physical_capacity", [[1000, 10]]),
            ],
        )

        result = generate_metrics_dict(module, client)

        assert result["ABC123"]["metrics"]["volume_physical_capacity"]["value"] == 10
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_run_without_any_data_point_warns(self, pure1_client):
        module = _module(window=600)
        client = _client(
            pure1_client,
            history=[_make_series("id-1", "vol1", "volume_physical_capacity", [])],
        )

        result = generate_metrics_dict(module, client)

        assert result["ABC123"]["metrics"] == {}
        module.warn.assert_called_once()
        assert "window of 600 seconds" in module.warn.call_args[0][0]

    def test_series_without_name_is_ignored(self, pure1_client):
        # A name that is None is a name Pure1 did not send
        series = _make_series("id-1", "vol1", None, [[1000, 10]])
        assert not hasattr(series, "name")
        client = _client(pure1_client, history=[series])

        result = generate_metrics_dict(_module(), client)

        assert result["ABC123"]["metrics"] == {}

    def test_series_without_resources_is_ignored(self, pure1_client):
        series = sdk_model(
            "MetricHistory", name="volume_physical_capacity", data=[[1000, 10]]
        )
        client = _client(pure1_client, history=[series])

        result = generate_metrics_dict(_module(), client)

        assert result["ABC123"]["metrics"] == {}

    def test_series_whose_resource_has_no_id_is_ignored(self, pure1_client):
        # An id that is None is an id Pure1 did not send
        series = _make_series(None, "vol1", "volume_physical_capacity", [[1000, 10]])
        assert not hasattr(series.resources[0], "id")
        client = _client(pure1_client, history=[series])

        result = generate_metrics_dict(_module(), client)

        assert result["ABC123"]["metrics"] == {}

    def test_series_without_data_leaves_no_key(self, pure1_client):
        module = _module(metrics=["volume_physical_capacity", "volume_write_iops"])
        client = _client(
            pure1_client,
            history=[
                _make_series("id-1", "vol1", "volume_physical_capacity", []),
                _make_series("id-1", "vol1", "volume_write_iops", [[1000, 3]], "op/s"),
            ],
        )

        result = generate_metrics_dict(module, client)

        # A series without data points leaves no key behind.
        assert list(result["ABC123"]["metrics"]) == ["volume_write_iops"]
        module.warn.assert_not_called()

    def test_batches_respect_time_series_limit(self, pure1_client):
        # Two metrics => at most 16 volumes per call (32 / 2).
        module = _module(metrics=["volume_physical_capacity", "volume_write_iops"])
        volumes = [_make_volume(serial="S%d" % i, vid="id-%d" % i) for i in range(40)]
        client = _client(pure1_client, volumes=volumes)
        # 40 volumes / 16 per call => 3 calls, each with its own answer
        client.get_metrics_history.side_effect = [
            valid_response(),
            valid_response(),
            valid_response(),
        ]

        generate_metrics_dict(module, client)

        per_call = MAX_TIME_SERIES // 2
        for call in client.get_metrics_history.call_args_list:
            assert len(call[1]["resource_ids"]) <= per_call
        assert client.get_metrics_history.call_count == 3

    def test_bad_request_fails_with_api_message(self, pure1_client):
        module = _module(resolution=30000)
        client = _client(
            pure1_client,
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")],
        )
        client.get_metrics_history.return_value = error_response(
            400,
            "Invalid metric availability",
            "resolution is too fine for metric volume_physical_capacity",
        )

        msg = _failure(module, client)

        assert "resolution is too fine for metric volume_physical_capacity" in msg
        assert msg == (
            "Failed to collect volume metrics: Invalid metric availability: "
            "resolution is too fine for metric volume_physical_capacity"
        )
        # Only a 404 is retried one volume at a time.
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_history_exception_fails(self, pure1_client):
        module = _module()
        client = _client(pure1_client)
        client.get_metrics_history.side_effect = Exception("connection timed out")

        msg = _failure(module, client)

        assert "connection timed out" in msg
        assert msg == "Failed to collect volume metrics: connection timed out"
        module.warn.assert_not_called()

    def test_not_found_fails_for_named_volumes(self, pure1_client):
        module = _module(volumes=["vol1"])
        client = _client(pure1_client)
        client.get_metrics_history.return_value = error_response(
            404, "Not found", "No volumes with id: id-1"
        )

        msg = _failure(module, client)

        assert "No volumes with id: id-1" in msg
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_not_found_fails_for_an_array(self, pure1_client):
        module = _module(array="myarray")
        client = _client(
            pure1_client,
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")],
        )
        client.get_metrics_history.return_value = error_response(
            404, "Not found", "No volumes with id: id-2"
        )

        msg = _failure(module, client)

        assert "No volumes with id: id-2" in msg
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_vanished_volume_is_skipped_when_all_volumes_are_queried(
        self, pure1_client
    ):
        module = _module()
        volumes = [
            _make_volume("S%d" % i, "vol%d" % i, "id-%d" % i) for i in range(1, 4)
        ]
        client = _client(pure1_client, volumes=volumes)

        def history(**kwargs):
            if "id-2" in kwargs["resource_ids"]:
                return error_response(404, "Not found", "No volumes with id: id-2")
            return valid_response(
                [
                    _make_series(vid, "vol", "volume_physical_capacity", [[1000, 5]])
                    for vid in kwargs["resource_ids"]
                ]
            )

        client.get_metrics_history.side_effect = history

        result = generate_metrics_dict(module, client)

        # One call for the batch, then one per volume.
        assert [
            call[1]["resource_ids"]
            for call in client.get_metrics_history.call_args_list
        ] == [["id-1", "id-2", "id-3"], ["id-1"], ["id-2"], ["id-3"]]
        assert sorted(result) == ["S1", "S3"]
        assert result["S1"]["metrics"]["volume_physical_capacity"]["value"] == 5
        assert result["S3"]["metrics"]["volume_physical_capacity"]["value"] == 5
        module.warn.assert_called_once()
        assert "vol2" in module.warn.call_args[0][0]
        module.fail_json.assert_not_called()

    def test_vanished_volumes_that_share_a_serial_are_skipped(self, pure1_client):
        module = _module()
        volumes = [
            _make_volume("S1", "vol1", "id-1"),
            _make_volume("S1", "vol1", "id-2"),
        ]
        client = _client(pure1_client, volumes=volumes)
        client.get_metrics_history.side_effect = lambda **kwargs: error_response(
            404, "Not found", "No volumes with id: %s" % kwargs["resource_ids"][0]
        )

        result = generate_metrics_dict(module, client)

        assert result == {}
        assert module.warn.call_count == 2
        module.fail_json.assert_not_called()

    def test_not_found_that_names_no_volume_fails(self, pure1_client):
        module = _module()
        volumes = [_make_volume(), _make_volume("DEF456", "vol2", "id-2")]
        client = _client(pure1_client, volumes=volumes)
        client.get_metrics_history.return_value = error_response(
            404, "Not found", "No metrics with name: volume_physical_capacity"
        )

        msg = _failure(module, client)

        assert "No metrics with name: volume_physical_capacity" in msg
        module.warn.assert_not_called()


class TestMain:
    """Tests for the pure1_volume_metrics main() flow."""

    def test_list_available_returns_the_catalog(self, pure1_client):
        client = _client(pure1_client)
        client.get_metrics.return_value = valid_response(
            [_make_metric("volume_physical_capacity")]
        )

        module = run_main(pure1_volume_metrics, list_available=True)

        client.get_metrics.assert_called_once_with(resource_types=["volumes"])
        client.get_volumes.assert_not_called()
        client.get_metrics_history.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_volume_metrics={
                "available_metrics": [
                    {
                        "name": "volume_physical_capacity",
                        "unit": "B",
                        "description": "Used space",
                        "resource_types": ["volumes"],
                        "availabilities": [
                            {
                                "resolution": 86400000,
                                "retention": 34128000000,
                                "aggregations": ["avg", "max"],
                            }
                        ],
                    }
                ]
            },
        )

    def test_metric_data_is_returned_by_serial_number(self, pure1_client):
        client = _client(
            pure1_client,
            history=[
                _make_series(
                    "id-1",
                    "vol1",
                    "volume_physical_capacity",
                    [[1000, 10], [2000, 25]],
                ),
            ],
        )

        module = run_main(pure1_volume_metrics, metrics=["volume_physical_capacity"])

        # the one call that lists arrays is the check of get_pure1
        client.get_arrays.assert_called_once_with(limit=0)
        client.get_volumes.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_volume_metrics={
                "serial_numbers": {
                    "ABC123": {
                        "id": "id-1",
                        "name": "vol1",
                        "metrics": {
                            "volume_physical_capacity": {
                                "unit": "B",
                                "timestamp": 2000,
                                "value": 25,
                            }
                        },
                    }
                }
            },
        )

    def test_options_left_out_take_their_defaults(self, pure1_client):
        client = _client(pure1_client)

        run_main(pure1_volume_metrics, metrics=["volume_physical_capacity"])

        client.get_metrics_history.assert_called_once()
        request = client.get_metrics_history.call_args[1]
        assert request["aggregation"] == "avg"
        assert request["resolution"] == 86400000
        # The window of one day, in milliseconds
        assert request["end_time"] - request["start_time"] == 86400000

    def test_metrics_are_required_without_list_available(self):
        module = run_main(pure1_volume_metrics)

        module.fail_json.assert_called_once_with(
            msg="list_available is False but all of the following are missing: metrics"
        )

    def test_aggregation_is_avg_or_max(self):
        module = run_main(
            pure1_volume_metrics,
            metrics=["volume_physical_capacity"],
            aggregation="min",
        )

        module.fail_json.assert_called_once_with(
            msg="value of aggregation must be one of: avg, max, got: min"
        )

    def test_catalog_is_listed_in_check_mode(self, pure1_client):
        _client(pure1_client)

        module = run_main(pure1_volume_metrics, check_mode=True, list_available=True)

        result = module.exit_json.call_args[1]["pure1_volume_metrics"]
        assert [metric["name"] for metric in result["available_metrics"]] == CATALOG


class TestDocumentation:
    """The documentation strings of the module."""

    def test_module_has_no_ansible_metadata(self):
        assert not hasattr(pure1_volume_metrics, "ANSIBLE_METADATA")
