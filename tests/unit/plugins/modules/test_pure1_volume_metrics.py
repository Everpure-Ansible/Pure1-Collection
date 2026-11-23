# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_volume_metrics module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import sys
import types
import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock external dependencies before importing the module
sys.modules["ansible"] = MagicMock()
sys.modules["ansible.module_utils"] = MagicMock()
sys.modules["ansible.module_utils.basic"] = MagicMock()
sys.modules["pypureclient"] = MagicMock()
sys.modules["pypureclient.pure1"] = MagicMock()
sys.modules["ansible_collections"] = MagicMock()
sys.modules["ansible_collections.everpure"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins.module_utils"] = MagicMock()
sys.modules["ansible_collections.everpure.pure1.plugins.module_utils.pure1"] = (
    MagicMock()
)

from plugins.modules.pure1_volume_metrics import (
    generate_metrics_dict,
    list_volume_metrics,
    MAX_TIME_SERIES,
)

CATALOG = [
    "volume_physical_capacity",
    "volume_provisioned_capacity",
    "volume_write_iops",
]


def _make_volume(serial="ABC123", name="vol1", vid="id-1"):
    return types.SimpleNamespace(serial=serial, name=name, id=vid)


def _make_series(vid, vname, metric, data, unit="B"):
    return types.SimpleNamespace(
        name=metric,
        unit=unit,
        resources=[types.SimpleNamespace(id=vid, name=vname)],
        data=data,
    )


def _make_metric(name):
    return types.SimpleNamespace(
        name=name,
        unit="B",
        description="Used space",
        resource_types=["volumes"],
        availabilities=[
            types.SimpleNamespace(
                resolution=86400000,
                retention=34128000000,
                aggregations=["avg", "max"],
            ),
        ],
    )


def _ok(items):
    return Mock(status_code=200, items=items)


def _error(status, message, context=None):
    """An SDK error response: it carries errors and has no items attribute."""
    return types.SimpleNamespace(
        status_code=status,
        errors=[types.SimpleNamespace(message=message, context=context)],
    )


def _client(volumes=None, history=None):
    client = Mock()
    client.get_metrics.return_value = _ok([_make_metric(name) for name in CATALOG])
    client.get_volumes.return_value = _ok(
        [_make_volume()] if volumes is None else volumes
    )
    client.get_metrics_history.return_value = _ok([] if history is None else history)
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
    module = Mock()
    module.params = defaults
    module.fail_json.side_effect = SystemExit("fail_json called")
    return module


def _failure(module, client):
    """Run the module function, expect fail_json and return its message."""
    with pytest.raises(SystemExit):
        generate_metrics_dict(module, client)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


class TestListVolumeMetrics:
    def test_catalog_is_mapped(self):
        client = _client()
        client.get_metrics.return_value = _ok(
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

    def test_unset_fields_are_none(self):
        client = _client()
        client.get_metrics.return_value = _ok(
            [types.SimpleNamespace(name="volume_data_reduction")]
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

    def test_metric_without_name_is_left_out(self):
        client = _client()
        client.get_metrics.return_value = _ok(
            [types.SimpleNamespace(unit="B"), _make_metric("volume_write_iops")]
        )

        result = list_volume_metrics(_module(), client)

        assert [metric["name"] for metric in result] == ["volume_write_iops"]

    def test_error_response_fails_with_api_message(self):
        module = _module()
        client = _client()
        client.get_metrics.return_value = _error(500, "Internal server error")

        with pytest.raises(SystemExit):
            list_volume_metrics(module, client)

        assert "Internal server error" in module.fail_json.call_args[1]["msg"]


class TestRequestValidation:
    def test_unknown_metric_fails_before_volumes_are_listed(self):
        module = _module(metrics=["volume_physical_capacity", "volume_total_used"])
        client = _client()

        msg = _failure(module, client)

        assert "Unknown volume metric(s): volume_total_used." in msg
        assert ", ".join(CATALOG) in msg
        client.get_volumes.assert_not_called()
        client.get_metrics_history.assert_not_called()

    def test_empty_metrics_list_fails(self):
        module = _module(metrics=[])
        client = _client()

        msg = _failure(module, client)

        assert "at least one volume metric" in msg
        assert ", ".join(CATALOG) in msg
        client.get_volumes.assert_not_called()

    def test_empty_volumes_list_fails(self):
        module = _module(volumes=[])
        client = _client()

        msg = _failure(module, client)

        assert msg == "volumes must name at least one volume"
        client.get_volumes.assert_not_called()


class TestVolumeSelection:
    def test_all_volumes_by_default(self):
        client = _client()

        generate_metrics_dict(_module(), client)

        client.get_volumes.assert_called_once_with()

    def test_array_filter_is_applied(self):
        client = _client()

        generate_metrics_dict(_module(array="myarray"), client)

        client.get_volumes.assert_called_once_with(filter="arrays.name='myarray'")

    def test_array_name_is_escaped_in_the_filter(self):
        client = _client()

        generate_metrics_dict(_module(array="my'arr\\ay"), client)

        client.get_volumes.assert_called_once_with(
            filter="arrays.name='my\\'arr\\\\ay'"
        )

    def test_volumes_name_filter_is_applied(self):
        client = _client(
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")]
        )

        generate_metrics_dict(_module(volumes=["vol1", "vol2"]), client)

        client.get_volumes.assert_called_once_with(names=["vol1", "vol2"])

    def test_array_and_volumes_are_combined(self):
        client = _client()

        result = generate_metrics_dict(
            _module(array="myarray", volumes=["vol1"]), client
        )

        client.get_volumes.assert_called_once_with(
            filter="arrays.name='myarray'", names=["vol1"]
        )
        assert list(result) == ["ABC123"]

    def test_volume_name_in_another_case_is_found_on_the_array(self):
        # Pure1 matches names in any case and returns the stored name.
        client = _client(volumes=[_make_volume(name="Vol1")])

        result = generate_metrics_dict(
            _module(array="myarray", volumes=["vOL1"]), client
        )

        assert list(result) == ["ABC123"]

    def test_named_volume_missing_from_the_array_fails(self):
        module = _module(array="myarray", volumes=["vol1", "vol2"])
        client = _client()

        msg = _failure(module, client)

        assert msg == "Volume(s) vol2 not found on array myarray"
        client.get_metrics_history.assert_not_called()

    def test_unknown_volume_name_fails_with_api_message(self):
        module = _module(volumes=["no-such-volume"])
        client = _client()
        client.get_volumes.return_value = _error(
            404, "Not found", "No resources with name: no-such-volume"
        )

        msg = _failure(module, client)

        assert "No resources with name: no-such-volume" in msg
        client.get_metrics_history.assert_not_called()

    def test_error_response_without_errors_reports_the_status(self):
        module = _module()
        client = _client()
        client.get_volumes.return_value = types.SimpleNamespace(
            status_code=403, errors=[]
        )

        msg = _failure(module, client)

        assert msg == "Failed to list volumes: HTTP status 403"

    def test_volume_listing_exception_fails(self):
        module = _module()
        client = _client()
        client.get_volumes.side_effect = Exception("connection timed out")

        msg = _failure(module, client)

        assert "connection timed out" in msg

    def test_volume_without_serial_or_id_is_left_out(self):
        client = _client(
            volumes=[
                _make_volume(),
                types.SimpleNamespace(name="vol2", id="id-2"),
                types.SimpleNamespace(name="vol3", serial="GHI789"),
            ]
        )

        result = generate_metrics_dict(_module(), client)

        assert list(result) == ["ABC123"]
        assert client.get_metrics_history.call_args[1]["resource_ids"] == ["id-1"]


class TestGenerateMetricsDict:
    def test_latest_data_point_is_used(self):
        client = _client(
            history=[
                _make_series(
                    "id-1",
                    "vol1",
                    "volume_physical_capacity",
                    [[1000, 10], [2000, 25]],
                ),
            ]
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

    def test_request_is_built_from_the_options(self):
        metrics = ["volume_physical_capacity", "volume_write_iops"]
        module = _module(
            aggregation="max", resolution=3600000, window=7200, metrics=metrics
        )
        client = _client()

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

    def test_window_shorter_than_resolution_is_accepted(self):
        module = _module(window=7200)
        client = _client(
            history=[
                _make_series("id-1", "vol1", "volume_physical_capacity", [[1000, 10]]),
            ]
        )

        result = generate_metrics_dict(module, client)

        assert result["ABC123"]["metrics"]["volume_physical_capacity"]["value"] == 10
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_run_without_any_data_point_warns(self):
        module = _module(window=600)
        client = _client(
            history=[_make_series("id-1", "vol1", "volume_physical_capacity", [])]
        )

        result = generate_metrics_dict(module, client)

        assert result["ABC123"]["metrics"] == {}
        module.warn.assert_called_once()
        assert "window of 600 seconds" in module.warn.call_args[0][0]

    def test_series_without_name_is_ignored(self):
        series = _make_series("id-1", "vol1", "volume_physical_capacity", [[1000, 10]])
        del series.name
        client = _client(history=[series])

        result = generate_metrics_dict(_module(), client)

        assert result["ABC123"]["metrics"] == {}

    def test_series_without_data_leaves_no_key(self):
        module = _module(metrics=["volume_physical_capacity", "volume_write_iops"])
        client = _client(
            history=[
                _make_series("id-1", "vol1", "volume_physical_capacity", []),
                _make_series("id-1", "vol1", "volume_write_iops", [[1000, 3]], "op/s"),
            ]
        )

        result = generate_metrics_dict(module, client)

        # A series without data points leaves no key behind.
        assert list(result["ABC123"]["metrics"]) == ["volume_write_iops"]
        module.warn.assert_not_called()

    def test_batches_respect_time_series_limit(self):
        # Two metrics => at most 16 volumes per call (32 / 2).
        module = _module(metrics=["volume_physical_capacity", "volume_write_iops"])
        volumes = [_make_volume(serial="S%d" % i, vid="id-%d" % i) for i in range(40)]
        client = _client(volumes=volumes)

        generate_metrics_dict(module, client)

        per_call = MAX_TIME_SERIES // 2
        for call in client.get_metrics_history.call_args_list:
            assert len(call[1]["resource_ids"]) <= per_call
        # 40 volumes / 16 per call => 3 calls
        assert client.get_metrics_history.call_count == 3

    def test_bad_request_fails_with_api_message(self):
        module = _module(resolution=30000)
        client = _client(
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")]
        )
        client.get_metrics_history.return_value = _error(
            400,
            "Invalid metric availability",
            "resolution is too fine for metric volume_physical_capacity",
        )

        msg = _failure(module, client)

        assert "resolution is too fine for metric volume_physical_capacity" in msg
        # Only a 404 is retried one volume at a time.
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_history_exception_fails(self):
        module = _module()
        client = _client()
        client.get_metrics_history.side_effect = Exception("connection timed out")

        msg = _failure(module, client)

        assert "connection timed out" in msg
        module.warn.assert_not_called()

    def test_not_found_fails_for_named_volumes(self):
        module = _module(volumes=["vol1"])
        client = _client()
        client.get_metrics_history.return_value = _error(
            404, "Not found", "No volumes with id: id-1"
        )

        msg = _failure(module, client)

        assert "No volumes with id: id-1" in msg
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_not_found_fails_for_an_array(self):
        module = _module(array="myarray")
        client = _client(
            volumes=[_make_volume(), _make_volume("DEF456", "vol2", "id-2")]
        )
        client.get_metrics_history.return_value = _error(
            404, "Not found", "No volumes with id: id-2"
        )

        msg = _failure(module, client)

        assert "No volumes with id: id-2" in msg
        assert client.get_metrics_history.call_count == 1
        module.warn.assert_not_called()

    def test_vanished_volume_is_skipped_when_all_volumes_are_queried(self):
        module = _module()
        volumes = [
            _make_volume("S%d" % i, "vol%d" % i, "id-%d" % i) for i in range(1, 4)
        ]
        client = _client(volumes=volumes)

        def history(**kwargs):
            if "id-2" in kwargs["resource_ids"]:
                return _error(404, "Not found", "No volumes with id: id-2")
            return _ok(
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

    def test_vanished_volumes_that_share_a_serial_are_skipped(self):
        module = _module()
        volumes = [
            _make_volume("S1", "vol1", "id-1"),
            _make_volume("S1", "vol1", "id-2"),
        ]
        client = _client(volumes=volumes)
        client.get_metrics_history.side_effect = lambda **kwargs: _error(
            404, "Not found", "No volumes with id: %s" % kwargs["resource_ids"][0]
        )

        result = generate_metrics_dict(module, client)

        assert result == {}
        assert module.warn.call_count == 2
        module.fail_json.assert_not_called()

    def test_not_found_that_names_no_volume_fails(self):
        module = _module()
        volumes = [_make_volume(), _make_volume("DEF456", "vol2", "id-2")]
        client = _client(volumes=volumes)
        client.get_metrics_history.return_value = _error(
            404, "Not found", "No metrics with name: volume_physical_capacity"
        )

        msg = _failure(module, client)

        assert "No metrics with name: volume_physical_capacity" in msg
        module.warn.assert_not_called()
