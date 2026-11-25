# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_metrics module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
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


from plugins.modules import pure1_metrics
from plugins.modules.pure1_metrics import batches
from tests.unit.conftest import (
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

DAY = 86400000
HOUR = 3600000
# The time of the run, 2026-01-02 03:04:05 UTC, and the window before it
NOW = 1767323045
END = NOW * 1000
START = END - DAY
# 2021-02-03 13:04:05 UTC
STAMP = 1612357445000
POINT = {
    "timestamp": "2021-02-03 13:04:05 UTC",
    "timestamp_epoch": 1612357445000,
    "value": 9600000000.5,
}
NO_POINT = {"timestamp": None, "timestamp_epoch": None, "value": None}
ARRAY1 = {"id": "array-id-1", "name": "array1", "fqdn": "array1.example.com"}
ARRAY2 = {"id": "array-id-2", "name": "array2", "fqdn": "array2.example.com"}
# ansible-doc takes out of what it prints whatever matches this (ansible/cli/doc.py)
RST_DIRECTIVE = r".. \w+?::"

# resource_type: the client method that lists it, the SDK model of a listed
# resource, the SDK model of the answer and the first part of its metric names
TYPES = {
    "arrays": ("get_arrays", "Array", "ArrayGetResponse", "array"),
    "volumes": ("get_volumes", "Volume", "VolumeGetResponse", "volume"),
    "pods": ("get_pods", "Pod", "PodGetResponse", "pod"),
    "file-systems": (
        "get_file_systems",
        "FileSystem",
        "FileSystemGetResponse",
        "file_system",
    ),
    "directories": (
        "get_directories",
        "Directory",
        "DirectoryGetResponse",
        "directory",
    ),
    "buckets": ("get_buckets", "Bucket", "BucketGetResponse", "bucket"),
    "object-store-accounts": (
        "get_object_store_accounts",
        "ObjectStoreAccount",
        "ObjectStoreAccountGetResponse",
        "object_store_account",
    ),
    "subscription-licenses": (
        "get_subscription_licenses",
        "SubscriptionLicense",
        "SubscriptionLicenseGetResponse",
        "subscription_license",
    ),
}
ON_ARRAYS = (
    "volumes",
    "pods",
    "file-systems",
    "directories",
    "buckets",
    "object-store-accounts",
)
# How a warning names the resources of each type
KIND = {
    "arrays": "arrays",
    "volumes": "volumes",
    "pods": "pods",
    "file-systems": "file systems",
    "directories": "directories",
    "buckets": "buckets",
    "object-store-accounts": "object store accounts",
    "subscription-licenses": "subscription licenses",
}

NO_DATA = (
    "Pure1 returned no data point in the time range: every value is null, and the "
    "range may be too short or too recent for the delay Pure1 publishes data "
    "points with."
)
NOT_OF_THE_TASK = (
    "Series that Pure1 returned for no resource and metric of the task are left "
    "out of the result: {0}."
)
SAME_NAME = (
    "Series that Pure1 returned for another array of the same name in one pod "
    "are left out of the result: {0}."
)
# The two warnings that name a kind of resource start with it: "Volumes", "File
# systems"
GONE = (
    "{0} that Pure1 listed and no longer knows are named in skipped and are not "
    "in resources: {1}."
)
NO_ID = "{0} that Pure1 returned without an ID are left out of the result: {1}."
NO_NAME = "Metrics that Pure1 returned without a name are left out of the result: {0}."
INCOMPLETE = "Pure1 returned {0} of {1} {2}: the result may be incomplete."
TOO_MANY = (
    "The task needs {0} series (volumes: {1}, metrics: {2}), more than max_series "
    "allows ({3}): select fewer with name, ids or array, or raise max_series."
)
# What a listing without name and ids asks for at most: one resource more than
# the default of max_series allows for one metric
LIMIT = 3201
TOO_LONG = "The request is too long for Pure1: give fewer names or IDs in one task."


@pytest.fixture(autouse=True)
def _time_of_the_run(monkeypatch):
    """Every test runs at NOW and a fraction of a second"""
    monkeypatch.setattr(pure1_metrics.time, "time", lambda: NOW + 0.9)


def _metric(name, unit="B", types=("volumes",), resolution=DAY):
    return sdk_model(
        "Metric",
        name=name,
        unit=unit,
        description="What {0} is".format(name),
        resource_types=list(types),
        availabilities=[
            sdk_model(
                "MetricAvailability",
                resolution=resolution,
                retention=34128000000,
                aggregations=["avg", "max"],
            )
        ],
    )


def _available(name, unit="B", types=("volumes",), resolution=DAY):
    """Return the entry of _metric() in available_metrics"""
    return {
        "name": name,
        "unit": unit,
        "description": "What {0} is".format(name),
        "resource_types": list(types),
        "availabilities": [
            {
                "resolution": resolution,
                "retention": 34128000000,
                "aggregations": ["avg", "max"],
            }
        ],
    }


def _catalog(resource_type):
    """Return what Pure1 lists for a type

    A rate and a size, and for pods and arrays a metric of the arrays of a pod.
    """
    prefix = TYPES[resource_type][3]
    metrics = [
        _metric(prefix + "_read_iops", "op/s", (resource_type,), 30000),
        _metric(prefix + "_space", "B", (resource_type,)),
    ]
    if resource_type in ("arrays", "pods"):
        metrics.append(
            _metric("pod_array_read_iops", "op/s", ("pods", "arrays"), 30000)
        )
    return metrics


def _array_ref(name="array1", array_id="array-id-1"):
    return array_reference(
        name, id=array_id, fqdn=name.lower() + ".example.com", resource_type="arrays"
    )


def _array(number=1, **overrides):
    """Return an array as get_arrays gives it"""
    fields = dict(id="array-id-{0}".format(number), name="array{0}".format(number))
    fields.update(overrides)
    return sdk_model("Array", **fields)


def _listed(resource_type="volumes", number=1, **overrides):
    """Return a resource as its listing gives it: volume1 with the ID volume-id-1"""
    prefix = TYPES[resource_type][3]
    fields = dict(
        id="{0}-id-{1}".format(prefix, number), name="{0}{1}".format(prefix, number)
    )
    if resource_type == "volumes":
        fields["serial"] = "SERIAL{0}".format(number)
    if resource_type in ON_ARRAYS:
        fields["arrays"] = [_array_ref()]
    fields.update(overrides)
    return sdk_model(TYPES[resource_type][1], **fields)


def _row(resource_type="volumes", number=1, metrics=None, **changes):
    """Return the row of _listed() in the result"""
    row = {
        "name": "{0}{1}".format(TYPES[resource_type][3], number),
        "resource_type": resource_type,
    }
    if resource_type == "volumes":
        row["serial"] = "SERIAL{0}".format(number)
    if resource_type in ON_ARRAYS:
        row["arrays"] = [ARRAY1]
    row["metrics"] = metrics or {}
    row.update(changes)
    return row


def _reference(resource_id, name=None, resource_type=None):
    return sdk_model(
        "FixedReferenceFqdn", id=resource_id, name=name, resource_type=resource_type
    )


def _series(
    name, resource_id="volume-id-1", data=((STAMP, 9600000000.5),), **overrides
):
    fields = dict(
        name=name,
        unit="B",
        aggregation="avg",
        resolution=DAY,
        data=[list(point) for point in data],
        resources=[_reference(resource_id)],
    )
    fields.update(overrides)
    return sdk_model("MetricHistory", **fields)


def _pod_series(
    array="array1",
    array_id="array-id-1",
    pod_first=True,
    metric="pod_array_read_iops",
    **overrides
):
    """Return the series of a metric of the arrays of a pod for one array of pod-id-1"""
    resources = [
        _reference("pod-id-1", "pod1", "pods"),
        _reference(array_id, array, "arrays"),
    ]
    if not pod_first:
        resources.reverse()
    fields = dict(unit="op/s", resources=resources)
    fields.update(overrides)
    return _series(metric, **fields)


def _metric_row(unit="B", aggregation="avg", resolution=DAY, **keys):
    """Return the row of a metric in the result, with no data point unless keys has one"""
    row = {"unit": unit, "aggregation": aggregation, "resolution": resolution}
    row.update(NO_POINT)
    row.update(keys)
    return row


def _point(value):
    """Return the keys of POINT with another value"""
    point = dict(POINT)
    point["value"] = value
    return point


def _run(pure1_client, listed=(), series=(), catalog=None, arrays=None, **params):
    """Run main() against these answers and return the module stand-in

    listed is what the listing of the resource type answers, series the
    answers to get_metrics_history in order (a list of series, or an error
    answer or an exception as it is), and arrays what get_arrays answers
    when the names of the array option are resolved.
    """
    params.setdefault("resource_type", "volumes")
    resource_type = params["resource_type"]
    if catalog is None:
        catalog = _catalog(resource_type)
    pure1_client.get_metrics.return_value = valid_response(catalog)
    # the first answer is for the check get_pure1 makes
    known = [valid_response()]
    if arrays is not None:
        known.append(valid_response(arrays))
    if resource_type == "arrays":
        known.append(valid_response(listed))
    else:
        lister = getattr(pure1_client, TYPES[resource_type][0])
        lister.return_value = valid_response(listed)
    pure1_client.get_arrays.side_effect = known
    pure1_client.get_metrics_history.side_effect = [
        valid_response(answer) if isinstance(answer, list) else answer
        for answer in series
    ]
    return run_main(pure1_metrics, **params)


def _result(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_metrics"]


def _failure(module):
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _history_call(names, resource_ids, **changes):
    keywords = dict(
        aggregation="avg",
        end_time=END,
        resolution=DAY,
        start_time=START,
        names=names,
        resource_ids=resource_ids,
    )
    keywords.update(changes)
    return call(**keywords)


def _not_found(*resource_ids):
    """Return the answer of Pure1 to a request for resources it does not know"""
    return error_response(
        404,
        "Not found",
        "No volumes with id{0}: {1}".format(
            "" if len(resource_ids) == 1 else "s", ", ".join(resource_ids)
        ),
    )


class TestMain:
    """The whole result of main() and the requests it makes."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_latest_values_of_all_volumes(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[
                _listed(),
                _listed(number=2, arrays=[_array_ref("array2", "array-id-2")]),
            ],
            # not in the order of the request: a series is filed by its resource
            series=[
                [
                    _series(
                        "volume_space",
                        "volume-id-2",
                        data=[(STAMP - DAY, 1), (STAMP, 0)],
                    ),
                    _series("volume_read_iops", unit="op/s", data=[(STAMP, 2.5)]),
                    _series("volume_space"),
                    _series("volume_read_iops", "volume-id-2", unit="op/s", data=[]),
                ]
            ],
            metrics=["volume_space", "volume_read_iops"],
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={
                "available_metrics": [],
                "resources": {
                    "volume-id-1": _row(
                        metrics={
                            "volume_space": _metric_row(**POINT),
                            "volume_read_iops": _metric_row("op/s", **_point(2.5)),
                        }
                    ),
                    "volume-id-2": _row(
                        number=2,
                        arrays=[ARRAY2],
                        metrics={
                            # a value of 0 is a value
                            "volume_space": _metric_row(**_point(0)),
                            "volume_read_iops": _metric_row("op/s"),
                        },
                    ),
                },
                "skipped": [],
            },
        )
        module.warn.assert_not_called()
        # the check of get_pure1, the metrics, the volumes and one request for
        # the four series. max_series allows 1600 volumes with two metrics:
        # no more than one above that are asked for
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_metrics.assert_called_once_with(resource_types=["volumes"])
        pure1_client.get_volumes.assert_called_once_with(filter=None, limit=1601)
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space", "volume_read_iops"], ["volume-id-1", "volume-id-2"]
            )
        ]

    def test_check_mode_returns_the_same(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
            check_mode=True,
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={
                "available_metrics": [],
                "resources": {
                    "volume-id-1": _row(metrics={"volume_space": _metric_row(**POINT)})
                },
                "skipped": [],
            },
        )
        pure1_client.get_metrics_history.assert_called_once()

    @pytest.mark.parametrize("check_mode", [False, True])
    def test_list_available_returns_the_metrics_by_name(self, pure1_client, check_mode):
        module = _run(
            pure1_client,
            resource_type="pods",
            list_available=True,
            check_mode=check_mode,
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={
                "available_metrics": [
                    _available(
                        "pod_array_read_iops", "op/s", ("pods", "arrays"), 30000
                    ),
                    _available("pod_read_iops", "op/s", ("pods",), 30000),
                    _available("pod_space", "B", ("pods",)),
                ],
                "resources": {},
                "skipped": [],
            },
        )
        module.warn.assert_not_called()
        # one request, and the check of get_pure1
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_metrics.assert_called_once_with(resource_types=["pods"])
        pure1_client.get_pods.assert_not_called()
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_option_at_once(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(arrays=[_array_ref("Array1")])],
            series=[
                [
                    _series(
                        "volume_read_iops",
                        unit="op/s",
                        aggregation="max",
                        resolution=HOUR,
                        data=[(STAMP - HOUR, 7), (STAMP, 2.5)],
                    )
                ]
            ],
            arrays=[_array(name="Array1")],
            array=["ARRAY1"],
            name=["VOLUME1"],
            metrics=["volume_read_iops"],
            aggregation="max",
            resolution=HOUR,
            start_time=STAMP - DAY,
            end_time=STAMP + 1,
            history=True,
            max_series=1,
            check_mode=True,
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={
                "available_metrics": [],
                "resources": {
                    "volume-id-1": _row(
                        arrays=[dict(ARRAY1, name="Array1")],
                        metrics={
                            "volume_read_iops": _metric_row(
                                "op/s",
                                "max",
                                HOUR,
                                data=[[STAMP - HOUR, 7], [STAMP, 2.5]],
                                **_point(2.5)
                            )
                        },
                    )
                },
                "skipped": [],
            },
        )
        module.warn.assert_not_called()
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='ARRAY1'"),
        ]
        pure1_client.get_volumes.assert_called_once_with(
            filter="arrays.name='Array1' and name='VOLUME1'"
        )
        assert pure1_client.get_metrics_history.call_args_list == [
            call(
                aggregation="max",
                end_time=STAMP + 1,
                resolution=HOUR,
                start_time=STAMP - DAY,
                names=["volume_read_iops"],
                resource_ids=["volume-id-1"],
            )
        ]

    def test_nothing_listed_gives_an_empty_result(self, pure1_client):
        module = _run(pure1_client, metrics=["volume_space"])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={"available_metrics": [], "resources": {}, "skipped": []},
        )
        module.warn.assert_not_called()
        # no resource, no request for a series
        pure1_client.get_metrics_history.assert_not_called()

    def test_no_metric_listed_gives_an_empty_list(self, pure1_client):
        module = _run(pure1_client, catalog=[], list_available=True)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={"available_metrics": [], "resources": {}, "skipped": []},
        )
        module.warn.assert_not_called()


class TestResourceTypes:
    """Each resource type: its listing, its rows and its messages."""

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_type_is_listed_with_its_own_method(self, pure1_client, resource_type):
        lister, prefix = TYPES[resource_type][0], TYPES[resource_type][3]
        metric = prefix + "_space"
        resource_id = prefix + "-id-1"
        # a FlashBlade has the metrics of an account at one hour only
        resolution = HOUR if resource_type == "object-store-accounts" else DAY
        module = _run(
            pure1_client,
            listed=[_listed(resource_type)],
            series=[[_series(metric, resource_id, resolution=resolution)]],
            resource_type=resource_type,
            metrics=[metric],
        )

        assert _result(module)["resources"] == {
            resource_id: _row(
                resource_type,
                metrics={metric: _metric_row(resolution=resolution, **POINT)},
            )
        }
        module.warn.assert_not_called()
        pure1_client.get_metrics.assert_called_once_with(resource_types=[resource_type])
        if resource_type == "arrays":
            assert pure1_client.get_arrays.call_args_list == [
                call(limit=0),
                call(filter=None, limit=LIMIT),
            ]
        else:
            getattr(pure1_client, lister).assert_called_once_with(
                filter=None, limit=LIMIT
            )
            pure1_client.get_arrays.assert_called_once_with(limit=0)
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call([metric], [resource_id], resolution=resolution)
        ]

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_rows_have_the_keys_of_their_type(self, pure1_client, resource_type):
        """serial for a volume, arrays where the listing has them"""
        prefix = TYPES[resource_type][3]
        module = _run(
            pure1_client,
            listed=[_listed(resource_type)],
            series=[[]],
            resource_type=resource_type,
            metrics=[prefix + "_space"],
        )

        (row,) = _result(module)["resources"].values()
        expected = ["name", "resource_type"]
        if resource_type == "volumes":
            expected.append("serial")
        if resource_type in ON_ARRAYS:
            expected.append("arrays")
        assert list(row) == expected + ["metrics"]

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_listing_is_read_across_pages(self, pure1_client, resource_type):
        lister, answer, prefix = [TYPES[resource_type][part] for part in (0, 2, 3)]
        answers = paged_answers(
            answer,
            [_listed(resource_type, number) for number in (1, 2, 3)],
            page_size=2,
        )

        def first_or_listing(**kwargs):
            # get_pure1 checks the client with get_arrays(limit=0)
            if kwargs.get("limit") == 0:
                return valid_response()
            return answers(**kwargs)

        pure1_client.get_metrics.return_value = valid_response(_catalog(resource_type))
        getattr(pure1_client, lister).side_effect = first_or_listing
        pure1_client.get_metrics_history.return_value = valid_response([])

        module = run_main(
            pure1_metrics, resource_type=resource_type, metrics=[prefix + "_space"]
        )

        assert answers.requests == [
            {"filter": None, "limit": LIMIT},
            {"filter": None, "limit": LIMIT, "continuation_token": "page-2"},
        ]
        assert list(_result(module)["resources"]) == [
            "{0}-id-{1}".format(prefix, number) for number in (1, 2, 3)
        ]
        assert _warnings(module) == [NO_DATA]

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_listing_without_a_token_is_read_by_offset(
        self, pure1_client, resource_type
    ):
        """Two of three and no token: the rest is asked for with the paging keywords

        max_series 0 lists without a limit, so the first request has none.
        """
        lister, prefix = TYPES[resource_type][0], TYPES[resource_type][3]
        listed = [_listed(resource_type, number) for number in (1, 2, 3)]
        requests = []

        def two_at_a_time(**kwargs):
            # get_pure1 checks the client with get_arrays(limit=0)
            if kwargs.get("limit") == 0:
                return valid_response()
            requests.append(kwargs)
            start = kwargs.get("offset", 0)
            return valid_response(listed[start : start + 2], total_item_count=3)

        pure1_client.get_metrics.return_value = valid_response(_catalog(resource_type))
        getattr(pure1_client, lister).side_effect = two_at_a_time
        pure1_client.get_metrics_history.return_value = valid_response([])

        module = run_main(
            pure1_metrics,
            resource_type=resource_type,
            metrics=[prefix + "_space"],
            max_series=0,
        )

        assert requests == [{"filter": None}, {"filter": None, "offset": 2}]
        assert list(_result(module)["resources"]) == [
            "{0}-id-{1}".format(prefix, number) for number in (1, 2, 3)
        ]
        assert _warnings(module) == [NO_DATA]

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_listing_that_comes_short_gives_a_warning(
        self, pure1_client, resource_type
    ):
        lister, answer, prefix = [TYPES[resource_type][part] for part in (0, 2, 3)]
        answers = paged_answers(answer, [_listed(resource_type)], total_item_count=3)

        def first_or_listing(**kwargs):
            if kwargs.get("limit") == 0:
                return valid_response()
            return answers(**kwargs)

        pure1_client.get_metrics.return_value = valid_response(_catalog(resource_type))
        getattr(pure1_client, lister).side_effect = first_or_listing
        pure1_client.get_metrics_history.return_value = valid_response(
            [_series(prefix + "_space", prefix + "-id-1")]
        )

        module = run_main(
            pure1_metrics, resource_type=resource_type, metrics=[prefix + "_space"]
        )

        assert list(_result(module)["resources"]) == [prefix + "-id-1"]
        assert _warnings(module) == [INCOMPLETE.format(1, 3, KIND[resource_type])]
        # asked for the rest once, and not listed a second time
        assert answers.requests == [
            {"filter": None, "limit": LIMIT},
            {"filter": None, "limit": LIMIT - 1, "offset": 1},
        ]

    def test_listing_without_a_total_that_cannot_be_paged_gives_a_warning(
        self, pure1_client
    ):
        answers = paged_answers("VolumeGetResponse", [_listed()], total_item_count=None)

        def without_offset(**kwargs):
            # Pure1 ignores the offset and answers with the first page again
            kwargs.pop("offset", None)
            return answers(**kwargs)

        pure1_client.get_metrics.return_value = valid_response(_catalog("volumes"))
        pure1_client.get_volumes.side_effect = without_offset
        pure1_client.get_metrics_history.return_value = valid_response(
            [_series("volume_space")]
        )

        module = run_main(
            pure1_metrics, resource_type="volumes", metrics=["volume_space"]
        )

        assert list(_result(module)["resources"]) == ["volume-id-1"]
        assert _warnings(module) == [
            INCOMPLETE.format(1, "an unknown number of", "volumes")
        ]

    def test_resource_type_is_required(self, pure1_client):
        module = run_main(pure1_metrics, metrics=["volume_space"])

        assert _failure(module) == "missing required arguments: resource_type"

    def test_unknown_resource_type_is_refused(self, pure1_client):
        module = run_main(pure1_metrics, resource_type="hosts", metrics=["host_space"])

        assert _failure(module) == (
            "value of resource_type must be one of: arrays, volumes, pods, "
            "file-systems, directories, buckets, object-store-accounts, "
            "subscription-licenses, got: hosts"
        )


class TestMetrics:
    """The metrics option and the list of metrics."""

    def test_metrics_is_required_unless_list_available(self, client_factory):
        module = run_main(pure1_metrics, resource_type="volumes")

        # the argument spec refuses it, and ansible-core words the message
        assert "metrics" in _failure(module)
        assert "list_available" in _failure(module)
        assert "must name at least one metric" not in _failure(module)
        client_factory.assert_not_called()

    @pytest.mark.parametrize("metrics", [[], None], ids=["empty", "null"])
    def test_empty_metrics_counts_as_not_set(self, client_factory, metrics):
        module = run_main(pure1_metrics, resource_type="volumes", metrics=metrics)

        assert _failure(module) == (
            "metrics must name at least one metric unless list_available is true."
        )
        # refused before the client is created
        client_factory.assert_not_called()

    @pytest.mark.parametrize(
        "metrics, expected",
        [
            pytest.param(
                ["volume_total_used"],
                "volume_total_used is not a metric of resource_type volumes. "
                "Valid names: volume_read_iops, volume_space.",
                id="one",
            ),
            pytest.param(
                ["volume_space", "Volume_Space", "volume_total_used"],
                "Volume_Space, volume_total_used are not metrics of resource_type "
                "volumes. Valid names: volume_read_iops, volume_space.",
                id="two, and the case of a name counts",
            ),
        ],
    )
    def test_unknown_metric_fails_with_the_valid_names(
        self, pure1_client, metrics, expected
    ):
        # Pure1 lists the metrics in no order, the message has them by name
        module = _run(
            pure1_client,
            listed=[_listed()],
            catalog=list(reversed(_catalog("volumes"))),
            metrics=metrics,
        )

        assert _failure(module) == expected
        # before anything else is asked for
        pure1_client.get_volumes.assert_not_called()
        pure1_client.get_metrics_history.assert_not_called()

    def test_metric_of_the_arrays_of_a_pod_is_no_metric_of_arrays(self, pure1_client):
        """Pure1 lists it for arrays and refuses the ID of an array for it"""
        module = _run(
            pure1_client,
            listed=[_listed("arrays")],
            resource_type="arrays",
            metrics=["pod_array_read_iops"],
        )

        assert _failure(module) == (
            "pod_array_read_iops is not a metric of resource_type arrays. "
            "Valid names: array_read_iops, array_space."
        )

    def test_list_available_of_arrays_leaves_out_the_metrics_of_pods(
        self, pure1_client
    ):
        module = _run(pure1_client, resource_type="arrays", list_available=True)

        assert _result(module)["available_metrics"] == [
            _available("array_read_iops", "op/s", ("arrays",), 30000),
            _available("array_space", "B", ("arrays",)),
        ]

    def test_metric_named_twice_is_asked_for_once(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[_series("volume_space")]],
            metrics=["volume_space", "volume_space"],
        )

        assert list(_result(module)["resources"]["volume-id-1"]["metrics"]) == [
            "volume_space"
        ]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["volume_space"], ["volume-id-1"])
        ]

    def test_metric_with_only_a_name_has_null_for_the_rest(self, pure1_client):
        module = _run(
            pure1_client,
            catalog=[sdk_model("Metric", name="volume_space")],
            list_available=True,
        )

        assert _result(module)["available_metrics"] == [
            {
                "name": "volume_space",
                "unit": None,
                "description": None,
                "resource_types": None,
                "availabilities": [],
            }
        ]
        module.warn.assert_not_called()

    def test_empty_values_of_a_metric_are_kept(self, pure1_client):
        """A ratio has the unit "", which is not null"""
        metric = _metric("volume_data_reduction", "")
        metric.description = ""
        metric.availabilities[0].resolution = 0
        metric.availabilities[0].retention = 0
        # what the SDK reads is not validated: an empty list and a null arrive
        del metric.availabilities[0].aggregations[:]
        metric.availabilities.append(None)
        module = _run(pure1_client, catalog=[metric], list_available=True)

        assert _result(module)["available_metrics"] == [
            {
                "name": "volume_data_reduction",
                "unit": "",
                "description": "",
                "resource_types": ["volumes"],
                "availabilities": [
                    {"resolution": 0, "retention": 0, "aggregations": []}
                ],
            }
        ]

    def test_metric_without_a_name_is_left_out(self, pure1_client):
        module = _run(
            pure1_client,
            catalog=[
                sdk_model("Metric", unit="B"),
                _metric("volume_space"),
                sdk_model("Metric", unit="op/s"),
            ],
            list_available=True,
        )

        assert _result(module)["available_metrics"] == [_available("volume_space")]
        assert _warnings(module) == [NO_NAME.format(2)]

    def test_metric_with_only_a_name_is_collected(self, pure1_client):
        """With a series that has only its name and its resource"""
        module = _run(
            pure1_client,
            listed=[_listed()],
            catalog=[sdk_model("Metric", name="volume_space")],
            series=[
                [
                    sdk_model(
                        "MetricHistory",
                        name="volume_space",
                        resources=[_reference("volume-id-1")],
                    )
                ]
            ],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(None)
        }
        assert _warnings(module) == [NO_DATA]

    def test_list_of_metrics_is_read_across_pages(self, pure1_client):
        answers = paged_answers("MetricGetResponse", _catalog("volumes"), page_size=1)
        pure1_client.get_metrics.side_effect = answers

        module = run_main(pure1_metrics, resource_type="volumes", list_available=True)

        assert answers.requests == [
            {"resource_types": ["volumes"]},
            {"resource_types": ["volumes"], "continuation_token": "page-1"},
        ]
        assert [metric["name"] for metric in _result(module)["available_metrics"]] == [
            "volume_read_iops",
            "volume_space",
        ]
        module.warn.assert_not_called()

    def test_list_of_metrics_without_a_token_is_read_by_offset(self, pure1_client):
        """Two of three and no token: the rest is asked for with the paging keywords"""
        metrics = [_metric("volume_a"), _metric("volume_b"), _metric("volume_c")]
        requests = []

        def two_at_a_time(**kwargs):
            requests.append(kwargs)
            start = kwargs.get("offset", 0)
            return valid_response(metrics[start : start + 2], total_item_count=3)

        pure1_client.get_metrics.side_effect = two_at_a_time

        module = run_main(pure1_metrics, resource_type="volumes", list_available=True)

        assert requests == [
            {"resource_types": ["volumes"]},
            {"resource_types": ["volumes"], "offset": 2},
        ]
        assert [metric["name"] for metric in _result(module)["available_metrics"]] == [
            "volume_a",
            "volume_b",
            "volume_c",
        ]
        module.warn.assert_not_called()

    def test_list_of_metrics_that_comes_short_gives_a_warning(self, pure1_client):
        pure1_client.get_metrics.side_effect = paged_answers(
            "MetricGetResponse", _catalog("volumes"), total_item_count=5
        )

        module = run_main(pure1_metrics, resource_type="volumes", list_available=True)

        assert len(_result(module)["available_metrics"]) == 2
        assert _warnings(module) == [INCOMPLETE.format(2, 5, "metrics")]

    def test_list_available_uses_no_other_option(self, pure1_client):
        """Not even what is refused for a task that collects"""
        module = _run(
            pure1_client,
            resource_type="subscription-licenses",
            list_available=True,
            array=["array1"],
            metrics=[],
            name=["license1"],
            max_series=-1,
            window=0,
        )

        assert [metric["name"] for metric in _result(module)["available_metrics"]] == [
            "subscription_license_read_iops",
            "subscription_license_space",
        ]
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_subscription_licenses.assert_not_called()


class TestSelection:
    """The options name, ids and array."""

    def test_name_filters_the_listing_and_keeps_the_spelling_of_pure1(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed(name="Vol1"), _listed(number=2, name="pod1::vol2")],
            series=[[]],
            name=["VOL1", "pod1::VOL2", "vol1"],
            metrics=["volume_space"],
        )

        resources = _result(module)["resources"]
        assert [row["name"] for row in resources.values()] == ["Vol1", "pod1::vol2"]
        pure1_client.get_volumes.assert_called_once_with(
            filter="name=('VOL1','pod1::VOL2','vol1')"
        )

    def test_name_selects_every_resource_of_that_name(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[
                _listed("pods"),
                _listed(
                    "pods", 2, name="POD1", arrays=[_array_ref("array2", "array-id-2")]
                ),
            ],
            series=[[]],
            resource_type="pods",
            name=["pod1"],
            metrics=["pod_space"],
        )

        assert list(_result(module)["resources"]) == ["pod-id-1", "pod-id-2"]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["pod_space"], ["pod-id-1", "pod-id-2"])
        ]

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param(
                {"name": ["volume1", "Vol9"]},
                "Volume Vol9 is not known to Pure1.",
                id="one name",
            ),
            pytest.param(
                {"name": ["vol8", "volume1", "vol9", "vol8"]},
                "Volumes vol8, vol9 are not known to Pure1.",
                id="two names",
            ),
            pytest.param(
                {"ids": ["volume-id-9", "VOLUME-ID-1"]},
                "Volume ID volume-id-9 is not known to Pure1.",
                id="one ID",
            ),
            pytest.param(
                {"ids": ["volume-id-8", "volume-id-9"]},
                "Volume IDs volume-id-8, volume-id-9 are not known to Pure1.",
                id="two IDs",
            ),
            pytest.param(
                {"name": ["vol9"], "array": ["ARRAY1"]},
                "Volume vol9 is not known to Pure1 on array ARRAY1.",
                id="name on one array",
            ),
            pytest.param(
                {"ids": ["volume-id-9"], "array": ["ARRAY1", "array2"]},
                "Volume ID volume-id-9 is not known to Pure1 on arrays ARRAY1, "
                "array2.",
                id="ID on two arrays",
            ),
            pytest.param(
                {"name": ["vol9"], "array": ["array2", "ARRAY1"]},
                "Volume vol9 is not known to Pure1 on arrays array2, ARRAY1.",
                id="arrays in the order of the task",
            ),
        ],
    )
    def test_name_or_id_that_selects_nothing_fails(
        self, pure1_client, params, expected
    ):
        arrays = None
        if "array" in params:
            arrays = [_array(), _array(2)][: len(params["array"])]
        module = _run(
            pure1_client,
            listed=[_listed()],
            arrays=arrays,
            metrics=["volume_space"],
            **params
        )

        assert _failure(module) == expected
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.parametrize(
        "resource_type, expected",
        [
            ("arrays", "Array x1 is not known to Pure1."),
            ("pods", "Pod x1 is not known to Pure1."),
            ("file-systems", "File system x1 is not known to Pure1."),
            ("directories", "Directory x1 is not known to Pure1."),
            ("buckets", "Bucket x1 is not known to Pure1."),
            ("object-store-accounts", "Object store account x1 is not known to Pure1."),
            ("subscription-licenses", "Subscription license x1 is not known to Pure1."),
        ],
    )
    def test_each_type_is_named_in_the_failure(
        self, pure1_client, resource_type, expected
    ):
        module = _run(
            pure1_client,
            resource_type=resource_type,
            name=["x1"],
            metrics=[TYPES[resource_type][3] + "_space"],
        )

        assert _failure(module) == expected

    def test_ids_filter_the_listing(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[[]],
            ids=["VOLUME-ID-1", "volume-id-2", "volume-id-2"],
            metrics=["volume_space"],
        )

        assert list(_result(module)["resources"]) == ["volume-id-1", "volume-id-2"]
        pure1_client.get_volumes.assert_called_once_with(
            filter="id=('VOLUME-ID-1','volume-id-2')"
        )

    def test_id_is_matched_whatever_the_case_pure1_lists_it_in(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(id="VOLUME-ID-1")],
            series=[[_series("volume_space", "VOLUME-ID-1")]],
            ids=["volume-id-1"],
            metrics=["volume_space"],
        )

        # the result and the request for the series have the ID of Pure1
        assert _result(module)["resources"] == {
            "VOLUME-ID-1": _row(metrics={"volume_space": _metric_row(**POINT)})
        }
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["volume_space"], ["VOLUME-ID-1"])
        ]

    @pytest.mark.parametrize(
        "option, field, said",
        [
            ("name", "name", "which holds about 200 names of 20 characters"),
            ("ids", "id", "which holds about 130 of them"),
        ],
    )
    def test_selection_too_long_for_one_request_fails(
        self, pure1_client, option, field, said
    ):
        """Every name or ID goes into the one listing request, as the option says"""
        values = ["{0}-{1:04d}".format(option, number) for number in range(150)]
        pure1_client.get_metrics.return_value = valid_response(_catalog("volumes"))
        # what py-pure-client makes of the answer of Pure1 to such a request
        pure1_client.get_volumes.return_value = error_response(
            400, "Response is not a valid JSON"
        )

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            **{option: values}
        )

        assert _failure(module) == (
            "Pure1 request get_volumes failed (HTTP 400): " + TOO_LONG
        )
        pure1_client.get_volumes.assert_called_once_with(
            filter="{0}=({1})".format(
                field, ",".join("'{0}'".format(value) for value in values)
            )
        )
        text = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"][option]
        text = " ".join(text["description"]).replace("\n", " ")
        assert said in text
        assert "fails with the message that the request is too long" in text

    def test_name_and_ids_exclude_each_other(self, pure1_client):
        module = _run(
            pure1_client,
            name=["volume1"],
            ids=["volume-id-1"],
            metrics=["volume_space"],
        )

        assert _failure(module) == "parameters are mutually exclusive: name|ids"

    @pytest.mark.parametrize(
        "name, ids",
        [
            pytest.param([], ["volume-id-1"], id="name empty"),
            pytest.param(["volume1"], [], id="ids empty"),
            pytest.param([], [], id="both empty"),
        ],
    )
    def test_name_and_ids_exclude_each_other_also_when_one_is_empty(
        self, pure1_client, name, ids
    ):
        """An empty list is given too, and the description of each option says so"""
        module = _run(pure1_client, name=name, ids=ids, metrics=["volume_space"])

        assert _failure(module) == "parameters are mutually exclusive: name|ids"
        options = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"]
        for option, other in (("name", "ids"), ("ids", "name")):
            assert (
                "Mutually exclusive with I({0}). A task that gives both options "
                "fails, also when one of them is an empty list.".format(other)
            ) in " ".join(" ".join(options[option]["description"]).split())

    @pytest.mark.parametrize("option", ["name", "ids", "array"])
    def test_empty_list_counts_as_not_set(self, pure1_client, option):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[[]],
            metrics=["volume_space"],
            **{option: []}
        )

        assert list(_result(module)["resources"]) == ["volume-id-1", "volume-id-2"]
        pure1_client.get_volumes.assert_called_once_with(filter=None, limit=LIMIT)
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    @pytest.mark.parametrize(
        "option, said",
        [
            (
                "name",
                "when neither I(name) nor I(ids) is set, or the one that is given "
                "is empty",
            ),
            ("ids", "An empty list counts as not set."),
            ("array", "are selected when it is not set or empty."),
            ("metrics", "an empty list counts as not set."),
        ],
    )
    def test_documentation_says_what_an_empty_list_selects(self, option, said):
        """test_empty_list_counts_as_not_set pins what it does"""
        options = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"]

        assert said in " ".join(options[option]["description"]).replace("\n", " ")

    def test_array_is_resolved_and_filters_with_the_names_of_pure1(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[
                _listed(arrays=[_array_ref("Array1")]),
                _listed(number=2, arrays=[_array_ref("array2", "array-id-2")]),
            ],
            series=[[]],
            arrays=[_array(name="Array1"), _array(2)],
            array=["ARRAY1", "array2"],
            metrics=["volume_space"],
        )

        resources = _result(module)["resources"]
        assert [row["arrays"][0]["name"] for row in resources.values()] == [
            "Array1",
            "array2",
        ]
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        pure1_client.get_volumes.assert_called_once_with(
            filter="arrays.name=('Array1','array2')", limit=LIMIT
        )
        assert _warnings(module) == [NO_DATA]

    def test_array_that_pure1_does_not_know_fails(self, pure1_client):
        module = _run(
            pure1_client, arrays=[], array=["array9"], metrics=["volume_space"]
        )

        assert _failure(module) == "Array array9 is not known to Pure1."
        pure1_client.get_volumes.assert_not_called()

    def test_rows_of_an_array_of_the_same_name_in_another_case_are_left_out(
        self, pure1_client
    ):
        """The name filter of Pure1 ignores case: the array is told by its ID"""
        module = _run(
            pure1_client,
            listed=[
                _listed(),
                _listed(number=2, arrays=[_array_ref("ARRAY1", "array-id-2")]),
            ],
            series=[[]],
            arrays=[_array(), _array(2, name="ARRAY1")],
            array=["array1"],
            metrics=["volume_space"],
        )

        assert list(_result(module)["resources"]) == ["volume-id-1"]
        pure1_client.get_volumes.assert_called_once_with(
            filter="arrays.name='array1'", limit=LIMIT
        )

    def test_array_without_an_id_is_told_by_its_name(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[
                _listed(arrays=[array_reference("array1")]),
                _listed(number=2, arrays=[array_reference("ARRAY1")]),
            ],
            series=[[]],
            arrays=[sdk_model("Array", name="array1")],
            array=["array1"],
            metrics=["volume_space"],
        )

        assert list(_result(module)["resources"]) == ["volume-id-1"]

    def test_stretched_pod_is_selected_by_either_of_its_arrays(self, pure1_client):
        pod = _listed("pods", arrays=[_array_ref(), _array_ref("array2", "array-id-2")])
        module = _run(
            pure1_client,
            listed=[pod],
            series=[[]],
            arrays=[_array(2)],
            resource_type="pods",
            array=["array2"],
            metrics=["pod_space"],
        )

        assert _result(module)["resources"]["pod-id-1"]["arrays"] == [ARRAY1, ARRAY2]
        pure1_client.get_pods.assert_called_once_with(
            filter="arrays.name='array2'", limit=LIMIT
        )

    def test_array_selects_the_arrays_themselves_without_a_second_listing(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            series=[[_series("array_space", "array-id-2")]],
            arrays=[_array(name="Array1"), _array(2)],
            resource_type="arrays",
            array=["ARRAY1", "array2"],
            metrics=["array_space"],
        )

        assert _result(module)["resources"] == {
            "array-id-1": {
                "name": "Array1",
                "resource_type": "arrays",
                "metrics": {"array_space": _metric_row()},
            },
            "array-id-2": {
                "name": "array2",
                "resource_type": "arrays",
                "metrics": {"array_space": _metric_row(**POINT)},
            },
        }
        # the check of get_pure1 and the names of the task: no listing after it
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["array_space"], ["array-id-1", "array-id-2"])
        ]

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param({"name": ["ARRAY2"]}, ["array-id-2"], id="name"),
            pytest.param({"ids": ["ARRAY-ID-1"]}, ["array-id-1"], id="ID"),
        ],
    )
    def test_named_arrays_are_narrowed_by_name_and_by_id(
        self, pure1_client, params, expected
    ):
        module = _run(
            pure1_client,
            series=[[]],
            arrays=[_array(), _array(2)],
            resource_type="arrays",
            array=["array1", "array2"],
            metrics=["array_space"],
            **params
        )

        assert list(_result(module)["resources"]) == expected
        assert pure1_client.get_arrays.call_count == 2

    def test_named_array_without_an_id_is_left_out(self, pure1_client):
        module = _run(
            pure1_client,
            series=[[_series("array_space", "array-id-2")]],
            arrays=[sdk_model("Array", name="array1"), _array(2)],
            resource_type="arrays",
            array=["array1", "array2"],
            metrics=["array_space"],
        )

        assert list(_result(module)["resources"]) == ["array-id-2"]
        assert _warnings(module) == [NO_ID.format("Arrays", 1)]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["array_space"], ["array-id-2"])
        ]

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param(
                {"name": ["array3"]},
                "Array array3 is not among the arrays named in array.",
                id="name",
            ),
            pytest.param(
                {"ids": ["array-id-3", "array-id-4"]},
                "Array IDs array-id-3, array-id-4 are not among the arrays named "
                "in array.",
                id="IDs",
            ),
        ],
    )
    def test_array_that_is_not_among_the_named_arrays_fails(
        self, pure1_client, params, expected
    ):
        module = _run(
            pure1_client,
            arrays=[_array()],
            resource_type="arrays",
            array=["array1"],
            metrics=["array_space"],
            **params
        )

        assert _failure(module) == expected

    def test_arrays_are_selected_by_name_with_a_filter(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed("arrays", name="Array1")],
            series=[[]],
            resource_type="arrays",
            name=["ARRAY1"],
            metrics=["array_space"],
        )

        assert _result(module)["resources"]["array-id-1"]["name"] == "Array1"
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='ARRAY1'"),
        ]

    def test_array_is_not_valid_for_subscription_licenses(self, client_factory):
        module = run_main(
            pure1_metrics,
            resource_type="subscription-licenses",
            array=["array1"],
            metrics=["subscription_license_space"],
        )

        assert _failure(module) == (
            "array cannot be used with resource_type subscription-licenses: a "
            "subscription license is not on an array."
        )
        client_factory.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": ["LICENSE2"]}, id="name"),
            pytest.param({"ids": ["SUBSCRIPTION_LICENSE-ID-2"]}, id="ID"),
        ],
    )
    def test_licenses_are_all_listed_and_narrowed_by_the_module(
        self, pure1_client, params
    ):
        """The filter of this listing compares the case, the module does not"""
        module = _run(
            pure1_client,
            listed=[
                _listed("subscription-licenses", name="license1"),
                _listed("subscription-licenses", 2, name="License2"),
            ],
            series=[[]],
            resource_type="subscription-licenses",
            metrics=["subscription_license_space"],
            **params
        )

        assert _result(module)["resources"] == {
            "subscription_license-id-2": {
                "name": "License2",
                "resource_type": "subscription-licenses",
                "metrics": {"subscription_license_space": _metric_row()},
            }
        }
        pure1_client.get_subscription_licenses.assert_called_once_with(filter=None)
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["subscription_license_space"], ["subscription_license-id-2"])
        ]


class TestListedResources:
    """What the listing of a resource type can hold."""

    def test_resource_with_only_an_id_has_null_for_the_rest(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[sdk_model("Volume", id="volume-id-1")],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"] == {
            "volume-id-1": {
                "name": None,
                "resource_type": "volumes",
                "serial": None,
                "arrays": [],
                "metrics": {"volume_space": _metric_row(**POINT)},
            }
        }
        module.warn.assert_not_called()

    def test_empty_values_of_a_resource_are_kept(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[
                _listed(
                    name="",
                    serial="",
                    arrays=[array_reference("", id="", fqdn="")],
                )
            ],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
        )

        row = _result(module)["resources"]["volume-id-1"]
        assert (row["name"], row["serial"]) == ("", "")
        assert row["arrays"] == [{"id": "", "name": "", "fqdn": ""}]

    @pytest.mark.parametrize("resource_type", list(TYPES))
    def test_resource_without_an_id_is_left_out(self, pure1_client, resource_type):
        prefix = TYPES[resource_type][3]
        nameless = sdk_model(TYPES[resource_type][1], name="x1")
        module = _run(
            pure1_client,
            listed=[nameless, _listed(resource_type), nameless],
            series=[[_series(prefix + "_space", prefix + "-id-1")]],
            resource_type=resource_type,
            metrics=[prefix + "_space"],
        )

        assert list(_result(module)["resources"]) == [prefix + "-id-1"]
        # "File systems that Pure1 returned ...": the kind starts the sentence
        assert _warnings(module) == [NO_ID.format(KIND[resource_type].capitalize(), 2)]
        (request,) = pure1_client.get_metrics_history.call_args_list
        assert request[1]["resource_ids"] == [prefix + "-id-1"]

    def test_array_references_that_name_nothing_are_left_out(self, pure1_client):
        """A null in the list and a reference with no field set"""
        volume = _listed(
            arrays=[
                _array_ref(),
                sdk_model("FixedReferenceFqdn"),
                array_reference("array2"),
                sdk_model("FixedReferenceFqdn", id="array-id-3"),
            ]
        )
        volume.arrays.append(None)
        module = _run(
            pure1_client,
            listed=[volume],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["arrays"] == [
            ARRAY1,
            {"id": None, "name": "array2", "fqdn": None},
            {"id": "array-id-3", "name": None, "fqdn": None},
        ]

    def test_resource_listed_twice_is_one_row(self, pure1_client):
        """A listing that moved while it was read can repeat a resource"""
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2), _listed()],
            series=[[]],
            metrics=["volume_space"],
        )

        assert list(_result(module)["resources"]) == ["volume-id-1", "volume-id-2"]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["volume_space"], ["volume-id-1", "volume-id-2"])
        ]


class TestTimeRange:
    """aggregation, resolution, window, start_time and end_time."""

    def _history_keywords(self, pure1_client, **params):
        """Return the keywords of the one request for series and the row of the metric"""
        params.setdefault("resource_type", "volumes")
        prefix = TYPES[params["resource_type"]][3]
        module = _run(
            pure1_client,
            listed=[_listed(params["resource_type"])],
            series=[[]],
            metrics=[prefix + "_space"],
            **params
        )
        resources = _result(module)["resources"]
        row = resources[prefix + "-id-1"]["metrics"][prefix + "_space"]
        (request,) = pure1_client.get_metrics_history.call_args_list
        return request[1], row

    def test_defaults_are_the_last_day_at_one_point_a_day(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client)

        assert keywords == dict(
            aggregation="avg",
            end_time=1767323045000,
            resolution=86400000,
            start_time=1767236645000,
            names=["volume_space"],
            resource_ids=["volume-id-1"],
        )
        assert row == _metric_row("B", "avg", 86400000)

    def test_accounts_default_to_one_point_an_hour(self, pure1_client):
        keywords, row = self._history_keywords(
            pure1_client, resource_type="object-store-accounts"
        )

        assert keywords["resolution"] == 3600000
        assert row["resolution"] == 3600000
        assert (keywords["start_time"], keywords["end_time"]) == (START, END)

    @pytest.mark.parametrize("resource_type", ["volumes", "object-store-accounts"])
    def test_resolution_of_the_task_is_used(self, pure1_client, resource_type):
        keywords, row = self._history_keywords(
            pure1_client, resource_type=resource_type, resolution=30000
        )

        assert keywords["resolution"] == 30000
        assert row["resolution"] == 30000

    def test_resolution_of_zero_is_left_to_pure1(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client, resolution=0)

        assert keywords["resolution"] == 0
        assert row["resolution"] == 0

    def test_aggregation_max(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client, aggregation="max")

        assert keywords["aggregation"] == "max"
        assert row["aggregation"] == "max"

    def test_unknown_aggregation_is_refused(self, pure1_client):
        module = _run(pure1_client, metrics=["volume_space"], aggregation="min")

        assert _failure(module) == (
            "value of aggregation must be one of: avg, max, got: min"
        )

    def test_window_looks_back_from_now(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client, window=3600)

        assert (keywords["start_time"], keywords["end_time"]) == (END - 3600000, END)

    def test_window_of_one_second_is_the_shortest(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client, window=1)

        assert (keywords["start_time"], keywords["end_time"]) == (END - 1000, END)

    @pytest.mark.parametrize("window", [0, -60])
    def test_window_below_one_second_is_refused(self, client_factory, window):
        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            window=window,
        )

        assert _failure(module) == "window must be 1 or more, got {0}".format(window)
        client_factory.assert_not_called()

    def test_start_and_end_time_are_passed_as_they_are(self, pure1_client):
        keywords, row = self._history_keywords(
            pure1_client, start_time=STAMP, end_time=STAMP + 5
        )

        assert (keywords["start_time"], keywords["end_time"]) == (STAMP, STAMP + 5)

    def test_start_time_of_zero_is_a_time(self, pure1_client):
        keywords, row = self._history_keywords(pure1_client, start_time=0, end_time=5)

        assert (keywords["start_time"], keywords["end_time"]) == (0, 5)

    def test_window_excludes_start_and_end_time(self, pure1_client):
        module = _run(
            pure1_client,
            metrics=["volume_space"],
            window=60,
            start_time=STAMP,
            end_time=STAMP + 5,
        )

        assert _failure(module) == (
            "parameters are mutually exclusive: window|start_time, window|end_time"
        )
        pure1_client.get_metrics.assert_not_called()

    @pytest.mark.parametrize(
        "given, missing", [("start_time", "end_time"), ("end_time", "start_time")]
    )
    def test_start_and_end_time_are_required_together(
        self, pure1_client, given, missing
    ):
        module = _run(pure1_client, metrics=["volume_space"], **{given: STAMP})

        # the argument spec refuses it, and ansible-core words the message
        assert given in _failure(module)
        assert missing in _failure(module)
        assert "must be set together" not in _failure(module)
        pure1_client.get_metrics.assert_not_called()

    @pytest.mark.parametrize(
        "given, null", [("start_time", "end_time"), ("end_time", "start_time")]
    )
    def test_null_for_one_of_start_and_end_time_is_refused(
        self, client_factory, given, null
    ):
        """The argument spec takes a null for a time that is set"""
        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            **{given: 0, null: None}
        )

        assert _failure(module) == "start_time and end_time must be set together."
        client_factory.assert_not_called()

    def test_null_for_both_times_is_the_window(self, pure1_client):
        keywords, row = self._history_keywords(
            pure1_client, start_time=None, end_time=None
        )

        assert (keywords["start_time"], keywords["end_time"]) == (START, END)

    def test_documented_defaults_are_those_of_the_module(self):
        """The default of resolution depends on the type and is in its text"""
        options = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"]

        assert options["window"]["default"] == 86400
        assert options["aggregation"]["default"] == "avg"
        assert options["max_series"]["default"] == 3200
        assert "default" not in options["resolution"]
        text = " ".join(options["resolution"]["description"]).replace("\n", " ")
        assert "it is C(86400000), one day, and C(3600000), one hour, with" in text


class TestDataPoints:
    """The option history and what a series can hold."""

    def test_history_adds_every_point_as_pure1_sends_it(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[
                [
                    _series(
                        "volume_space", data=[(STAMP - DAY, 0), (STAMP, 9600000000.5)]
                    ),
                    _series("volume_space", "volume-id-2", data=[]),
                ]
            ],
            metrics=["volume_space"],
            history=True,
        )

        resources = _result(module)["resources"]
        assert resources["volume-id-1"]["metrics"]["volume_space"] == _metric_row(
            data=[[STAMP - DAY, 0], [STAMP, 9600000000.5]], **POINT
        )
        assert resources["volume-id-2"]["metrics"]["volume_space"] == _metric_row(
            data=[]
        )
        module.warn.assert_not_called()

    def test_without_history_there_is_no_data(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
            history=False,
        )

        row = _result(module)["resources"]["volume-id-1"]["metrics"]["volume_space"]
        assert "data" not in row

    def test_resource_that_pure1_sends_no_series_for_has_null_values(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[[_series("volume_space", "volume-id-2")]],
            metrics=["volume_space", "volume_read_iops"],
            history=True,
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(data=[]),
            # the unit is that of the list of metrics
            "volume_read_iops": _metric_row("op/s", data=[]),
        }

    def test_last_point_is_the_last_one_pure1_sends(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[_series("volume_space", data=[(STAMP, 1.5), (0, 0)])]],
            metrics=["volume_space"],
        )

        # a time of 0 and a value of 0 are a point
        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(
                timestamp="1970-01-01 00:00:00 UTC", timestamp_epoch=0, value=0
            )
        }
        module.warn.assert_not_called()

    def test_points_that_are_null_or_empty_are_left_out(self, pure1_client):
        """What the SDK reads is not validated"""
        series = _series("volume_space", data=[(STAMP, 1.5)])
        series.data.insert(0, None)
        series.data.append([])
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[series]],
            metrics=["volume_space"],
            history=True,
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(data=[[STAMP, 1.5]], **_point(1.5))
        }

    def test_point_without_a_value_has_a_null_value(self, pure1_client):
        series = _series("volume_space")
        del series.data[0][1]
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[series]],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(**_point(None))
        }

    def test_unit_aggregation_and_resolution_are_those_of_the_series(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[
                [_series("volume_space", unit="", aggregation="max", resolution=0)]
            ],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row("", "max", 0, **POINT)
        }

    def test_series_that_are_not_of_the_task_are_left_out(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[
                [
                    _series("volume_space", "volume-id-9"),
                    _series("volume_read_iops"),
                    sdk_model("MetricHistory", resources=[_reference("volume-id-1")]),
                    sdk_model("MetricHistory", name="volume_space"),
                    sdk_model("MetricHistory"),
                    _series("volume_space"),
                ]
            ],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(**POINT)
        }
        assert _warnings(module) == [NOT_OF_THE_TASK.format(5)]

    def test_null_among_the_resources_of_a_series_is_passed_over(self, pure1_client):
        series = _series("volume_space")
        series.resources.insert(0, None)
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[[series]],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"]["volume-id-1"]["metrics"] == {
            "volume_space": _metric_row(**POINT)
        }
        module.warn.assert_not_called()

    def test_no_data_point_at_all_gives_a_warning(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[[_series("volume_space", data=[])]],
            metrics=["volume_space"],
        )

        assert len(_result(module)["resources"]) == 2
        assert _warnings(module) == [NO_DATA]


class TestPodArrays:
    """The metrics that have one series for each array of a pod."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_series_are_filed_by_the_array_of_the_pod(self, pure1_client):
        pod = _listed("pods", arrays=[_array_ref(), _array_ref("array2", "array-id-2")])
        module = _run(
            pure1_client,
            listed=[pod],
            series=[
                [
                    # Pure1 names the pod first, the SDK documents the array first
                    _pod_series(data=[(STAMP - DAY, 3), (STAMP, 0)]),
                    _pod_series("array2", "array-id-2", pod_first=False, data=[]),
                ]
            ],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
            history=True,
        )

        assert _result(module)["resources"] == {
            "pod-id-1": _row(
                "pods",
                arrays=[ARRAY1, ARRAY2],
                metrics={
                    "pod_array_read_iops": _metric_row(
                        "op/s",
                        data=[],
                        arrays={
                            "array1": dict(
                                POINT, value=0, data=[[STAMP - DAY, 3], [STAMP, 0]]
                            ),
                            "array2": dict(NO_POINT, data=[]),
                        },
                    )
                },
            )
        }
        module.warn.assert_not_called()
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["pod_array_read_iops"], ["pod-id-1"])
        ]

    def test_without_history_an_array_has_its_last_point(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed("pods")],
            series=[[_pod_series(data=[(STAMP, 2.5)])]],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
        )

        assert _result(module)["resources"]["pod-id-1"]["metrics"] == {
            "pod_array_read_iops": _metric_row(
                "op/s", arrays={"array1": dict(POINT, value=2.5)}
            )
        }
        module.warn.assert_not_called()

    def test_metrics_of_a_pod_and_of_its_arrays_go_in_requests_of_their_own(
        self, pure1_client
    ):
        """Pure1 refuses both kinds in one request"""
        module = _run(
            pure1_client,
            listed=[_listed("pods")],
            series=[
                [
                    _series("pod_space", "pod-id-1"),
                    _series("pod_read_iops", "pod-id-1"),
                ],
                [_pod_series()],
            ],
            resource_type="pods",
            metrics=["pod_read_iops", "pod_array_read_iops", "pod_space"],
        )

        metrics = _result(module)["resources"]["pod-id-1"]["metrics"]
        assert list(metrics) == ["pod_read_iops", "pod_array_read_iops", "pod_space"]
        assert [list(row) for row in metrics.values()] == [
            ["unit", "aggregation", "resolution", "timestamp", "timestamp_epoch"]
            + ["value"],
            ["unit", "aggregation", "resolution", "timestamp", "timestamp_epoch"]
            + ["value", "arrays"],
            ["unit", "aggregation", "resolution", "timestamp", "timestamp_epoch"]
            + ["value"],
        ]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["pod_read_iops", "pod_space"], ["pod-id-1"]),
            _history_call(["pod_array_read_iops"], ["pod-id-1"]),
        ]

    def test_array_that_pure1_sends_no_series_for_is_not_a_key(self, pure1_client):
        """Pure1 answers for a pod without such data with a series that names no array"""
        module = _run(
            pure1_client,
            listed=[_listed("pods"), _listed("pods", 2)],
            series=[
                [
                    _series(
                        "pod_array_read_iops",
                        "pod-id-2",
                        unit="op/s",
                        data=[],
                        resources=[_reference("pod-id-2", "pod2", "pods")],
                    ),
                    _pod_series(),
                ]
            ],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
        )

        resources = _result(module)["resources"]
        assert resources["pod-id-2"]["metrics"] == {
            "pod_array_read_iops": _metric_row("op/s", arrays={})
        }
        assert list(
            resources["pod-id-1"]["metrics"]["pod_array_read_iops"]["arrays"]
        ) == ["array1"]
        module.warn.assert_not_called()

    def test_series_with_points_that_names_no_array_is_left_out(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed("pods")],
            series=[
                [
                    _series("pod_array_read_iops", "pod-id-1"),
                    _pod_series(
                        resources=[
                            _reference("pod-id-1"),
                            sdk_model("FixedReferenceFqdn", id="array-id-1"),
                        ]
                    ),
                    _pod_series("array2", "array-id-2"),
                ]
            ],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
        )

        metric = _result(module)["resources"]["pod-id-1"]["metrics"][
            "pod_array_read_iops"
        ]
        assert list(metric["arrays"]) == ["array2"]
        assert _warnings(module) == [NOT_OF_THE_TASK.format(2)]

    def test_second_array_of_one_name_in_a_pod_is_left_out(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed("pods")],
            series=[
                [
                    _pod_series(data=[(STAMP, 1)]),
                    _pod_series("array1", "array-id-2", data=[(STAMP, 2)]),
                    _pod_series("array1", "array-id-3", data=[(STAMP, 3)]),
                    # the same array again takes the place of its first series
                    _pod_series(data=[(STAMP, 4)]),
                ]
            ],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
        )

        metric = _result(module)["resources"]["pod-id-1"]["metrics"][
            "pod_array_read_iops"
        ]
        assert metric["arrays"] == {"array1": dict(POINT, value=4)}
        assert _warnings(module) == [SAME_NAME.format(2)]

    def test_no_data_point_for_any_array_gives_a_warning(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed("pods")],
            series=[[_pod_series(data=[])]],
            resource_type="pods",
            metrics=["pod_array_read_iops"],
        )

        assert _result(module)["resources"]["pod-id-1"]["metrics"] == {
            "pod_array_read_iops": _metric_row("op/s", arrays={"array1": NO_POINT})
        }
        assert _warnings(module) == [NO_DATA]


class TestBatches:
    """At most 32 series in a request, and as few requests as that allows."""

    @pytest.mark.parametrize(
        "metrics, resources, expected",
        [
            pytest.param(1, 1, [(1, 1)], id="one series"),
            pytest.param(1, 32, [(1, 32)], id="32 resources"),
            pytest.param(1, 33, [(1, 32), (1, 1)], id="33 resources"),
            pytest.param(2, 14, [(2, 14)], id="28 series"),
            pytest.param(2, 17, [(2, 16), (2, 1)], id="34 series"),
            pytest.param(3, 14, [(3, 10), (3, 4)], id="42 series"),
            pytest.param(32, 1, [(32, 1)], id="32 metrics"),
            pytest.param(33, 1, [(32, 1), (1, 1)], id="33 metrics"),
            pytest.param(17, 3, [(10, 3), (7, 3)], id="17 metrics"),
            pytest.param(17, 1, [(17, 1)], id="17 metrics of one resource"),
            pytest.param(40, 2, [(16, 2), (16, 2), (8, 2)], id="40 metrics"),
            pytest.param(0, 3, [], id="no metric"),
            pytest.param(3, 0, [], id="no resource"),
        ],
    )
    def test_sizes_of_the_requests(self, metrics, resources, expected):
        names = ["metric{0}".format(number) for number in range(metrics)]
        resource_ids = ["id{0}".format(number) for number in range(resources)]

        found = batches(names, resource_ids)

        assert [(len(some), len(ids)) for some, ids in found] == expected

    @pytest.mark.parametrize("metrics", [1, 2, 3, 5, 7, 16, 17, 31, 32, 33, 40, 70])
    @pytest.mark.parametrize("resources", [1, 2, 3, 6, 10, 31, 32, 33, 65])
    def test_every_series_is_asked_for_once(self, metrics, resources):
        names = ["metric{0}".format(number) for number in range(metrics)]
        resource_ids = ["id{0}".format(number) for number in range(resources)]

        found = batches(names, resource_ids)

        asked = [(name, one) for some, ids in found for name in some for one in ids]
        assert sorted(asked) == sorted(
            (name, one) for name in names for one in resource_ids
        )
        assert all(len(some) * len(ids) <= 32 for some, ids in found)
        # never more requests than one metric at a time or one resource at a time
        assert len(found) <= metrics * -(-resources // 32)
        assert len(found) <= resources * -(-metrics // 32)

    def test_33_volumes_take_two_requests(self, pure1_client):
        listed = [_listed(number=number) for number in range(1, 34)]
        module = _run(
            pure1_client,
            listed=listed,
            series=[
                [_series("volume_space", "volume-id-32")],
                [_series("volume_space", "volume-id-33", data=[(STAMP, 2.5)])],
            ],
            metrics=["volume_space"],
        )

        resources = _result(module)["resources"]
        assert len(resources) == 33
        assert resources["volume-id-32"]["metrics"]["volume_space"]["value"] == (
            9600000000.5
        )
        assert resources["volume-id-33"]["metrics"]["volume_space"]["value"] == 2.5
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space"],
                ["volume-id-{0}".format(number) for number in range(1, 33)],
            ),
            _history_call(["volume_space"], ["volume-id-33"]),
        ]

    def test_two_metrics_of_17_volumes_take_two_requests(self, pure1_client):
        listed = [_listed(number=number) for number in range(1, 18)]
        module = _run(
            pure1_client,
            listed=listed,
            series=[[_series("volume_space")], []],
            metrics=["volume_space", "volume_read_iops"],
        )

        assert len(_result(module)["resources"]) == 17
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space", "volume_read_iops"],
                ["volume-id-{0}".format(number) for number in range(1, 17)],
            ),
            _history_call(["volume_space", "volume_read_iops"], ["volume-id-17"]),
        ]


class TestMaxSeries:
    """The option max_series."""

    def test_more_series_than_allowed_fail_before_any_is_read(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            metrics=["volume_space", "volume_read_iops"],
            max_series=3,
        )

        assert _failure(module) == TOO_MANY.format(4, 2, 2, 3)
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.parametrize("max_series", [4, 5, 0])
    def test_as_many_series_as_allowed_are_read(self, pure1_client, max_series):
        """0 switches the check off"""
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[[]],
            metrics=["volume_space", "volume_read_iops"],
            max_series=max_series,
        )

        assert len(_result(module)["resources"]) == 2
        pure1_client.get_metrics_history.assert_called_once()

    def test_default_allows_3200_series(self, pure1_client):
        listed = [_listed(number=number) for number in range(1, 3202)]
        module = _run(pure1_client, listed=listed, metrics=["volume_space"])

        assert _failure(module) == TOO_MANY.format(3201, 3201, 1, 3200)

        pure1_client.get_metrics_history.assert_not_called()

    def test_resources_left_out_do_not_count(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), sdk_model("Volume", name="volume2")],
            series=[[_series("volume_space")]],
            metrics=["volume_space"],
            max_series=1,
        )

        assert list(_result(module)["resources"]) == ["volume-id-1"]

    @staticmethod
    def _paged(pure1_client, listed, **keywords):
        """Answer get_volumes in pages of two and return the side_effect"""
        answers = paged_answers("VolumeGetResponse", listed, page_size=2, **keywords)
        pure1_client.get_metrics.return_value = valid_response(_catalog("volumes"))
        pure1_client.get_volumes.side_effect = answers
        return answers

    @pytest.mark.parametrize(
        "max_series, metrics, requests, expected",
        [
            pytest.param(
                1,
                ["volume_space"],
                [{"filter": None, "limit": 2}],
                TOO_MANY.format(7, 7, 1, 1),
                id="first page",
            ),
            pytest.param(
                3,
                ["volume_space"],
                [
                    {"filter": None, "limit": 4},
                    {"filter": None, "limit": 4, "continuation_token": "page-2"},
                ],
                TOO_MANY.format(7, 7, 1, 3),
                id="two pages",
            ),
            pytest.param(
                5,
                ["volume_space", "volume_read_iops"],
                [
                    {"filter": None, "limit": 3},
                    {"filter": None, "limit": 3, "continuation_token": "page-2"},
                ],
                TOO_MANY.format(14, 7, 2, 5),
                id="two metrics",
            ),
            pytest.param(
                1,
                ["volume_space", "volume_read_iops"],
                [{"filter": None, "limit": 1}],
                TOO_MANY.format(14, 7, 2, 1),
                id="not even one resource",
            ),
        ],
    )
    def test_listing_is_read_no_further_than_it_takes_to_refuse_the_task(
        self, pure1_client, max_series, metrics, requests, expected
    ):
        """Seven volumes in pages of two: the count of Pure1 is in the message"""
        answers = self._paged(
            pure1_client, [_listed(number=number) for number in range(1, 8)]
        )

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=metrics,
            max_series=max_series,
        )

        assert _failure(module) == expected
        assert answers.requests == requests
        # the listing is not complete, and that is no warning
        module.warn.assert_not_called()
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.parametrize(
        "max_series, expected",
        [
            pytest.param(6, [{"filter": None, "limit": 7}], id="one too many"),
            pytest.param(7, [{"filter": None, "limit": 8}], id="as many as allowed"),
            pytest.param(0, [{"filter": None}], id="no check"),
        ],
    )
    def test_listing_within_one_more_than_allowed_is_read_whole(
        self, pure1_client, max_series, expected
    ):
        answers = self._paged(
            pure1_client, [_listed(number=number) for number in range(1, 8)]
        )
        pure1_client.get_metrics_history.return_value = valid_response([])

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            max_series=max_series,
        )

        # the pages after the first are asked for by the token of the one before
        assert [
            {key: request[key] for key in request if key != "continuation_token"}
            for request in answers.requests
        ] == expected * 4
        if max_series == 6:
            assert _failure(module) == TOO_MANY.format(7, 7, 1, 6)
        else:
            assert len(_result(module)["resources"]) == 7

    @pytest.mark.parametrize(
        "listed, warnings",
        [
            pytest.param(
                lambda: [
                    sdk_model("Volume", name="x1"),
                    _listed(),
                    _listed(number=2),
                    sdk_model("Volume", name="x2"),
                ],
                [NO_ID.format("Volumes", 2), NO_DATA],
                id="without an ID",
            ),
            pytest.param(
                lambda: [_listed(), _listed(), _listed(), _listed(number=2)],
                [NO_DATA],
                id="listed twice",
            ),
        ],
    )
    def test_resources_left_out_of_the_first_ones_do_not_count(
        self, pure1_client, listed, warnings
    ):
        """Four are listed and two are allowed: the whole listing tells that two count"""
        answers = self._paged(pure1_client, listed())
        pure1_client.get_metrics_history.return_value = valid_response([])

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            max_series=2,
        )

        assert list(_result(module)["resources"]) == ["volume-id-1", "volume-id-2"]
        assert _warnings(module) == warnings
        assert answers.requests == [
            {"filter": None, "limit": 3},
            {"filter": None, "limit": 3, "continuation_token": "page-2"},
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
        ]

    def test_resources_of_another_array_among_the_first_ones_do_not_count(
        self, pure1_client
    ):
        """The name filter of Pure1 also lists the volumes of ARRAY1 for array1"""
        other = [_array_ref("ARRAY1", "array-id-2")]
        answers = self._paged(
            pure1_client,
            [
                _listed(),
                _listed(number=2, arrays=other),
                _listed(number=3, arrays=other),
                _listed(number=4),
            ],
        )
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response([_array(), _array(2, name="ARRAY1")]),
        ]
        pure1_client.get_metrics_history.return_value = valid_response([])

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            array=["array1"],
            max_series=2,
        )

        assert list(_result(module)["resources"]) == ["volume-id-1", "volume-id-4"]
        assert answers.requests == [
            {"filter": "arrays.name='array1'", "limit": 3},
            {
                "filter": "arrays.name='array1'",
                "limit": 3,
                "continuation_token": "page-2",
            },
            {"filter": "arrays.name='array1'"},
            {"filter": "arrays.name='array1'", "continuation_token": "page-2"},
        ]

    def test_listing_without_a_total_is_read_whole_for_the_count(self, pure1_client):
        answers = self._paged(
            pure1_client,
            [_listed(number=number) for number in range(1, 5)],
            total_item_count=None,
        )

        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            max_series=2,
        )

        assert _failure(module) == TOO_MANY.format(4, 4, 1, 2)
        assert [
            {key: request[key] for key in request if key != "continuation_token"}
            for request in answers.requests
        ] == [
            {"filter": None, "limit": 3},
            {"filter": None, "limit": 3},
            {"filter": None},
            {"filter": None},
            {"filter": None, "offset": 4},
        ]
        module.warn.assert_not_called()

    def test_selection_by_name_is_listed_whole_and_counted(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2, name="VOLUME1")],
            name=["volume1"],
            metrics=["volume_space"],
            max_series=1,
        )

        assert _failure(module) == TOO_MANY.format(2, 2, 1, 1)
        pure1_client.get_volumes.assert_called_once_with(filter="name='volume1'")

    def test_documentation_says_how_far_the_listing_is_read(self):
        options = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"]
        text = " ".join(options["max_series"]["description"]).replace("\n", " ")

        assert (
            "A task without I(name) and I(ids) lists no more resources than it "
            "takes to see that they are too many."
        ) in text

    def test_negative_max_series_is_refused(self, client_factory):
        module = run_main(
            pure1_metrics,
            resource_type="volumes",
            metrics=["volume_space"],
            max_series=-1,
        )

        assert _failure(module) == "max_series must be 0 or more, got -1"
        client_factory.assert_not_called()


class TestResourcesThatWentAway:
    """A resource that Pure1 listed and does not know when its series are read."""

    def test_task_that_names_no_resource_skips_it(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2), _listed(number=3)],
            series=[
                _not_found("volume-id-2"),
                [
                    _series("volume_space"),
                    _series("volume_space", "volume-id-3", data=[(STAMP, 2.5)]),
                ],
            ],
            metrics=["volume_space"],
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_metrics={
                "available_metrics": [],
                "resources": {
                    "volume-id-1": _row(metrics={"volume_space": _metric_row(**POINT)}),
                    "volume-id-3": _row(
                        number=3,
                        metrics={"volume_space": _metric_row(**_point(2.5))},
                    ),
                },
                "skipped": ["volume-id-2"],
            },
        )
        assert _warnings(module) == [GONE.format("Volumes", 1)]
        # the others are asked for again
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space"], ["volume-id-1", "volume-id-2", "volume-id-3"]
            ),
            _history_call(["volume_space"], ["volume-id-1", "volume-id-3"]),
        ]

    def test_task_with_array_skips_it_too(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[
                _not_found("volume-id-1"),
                [_series("volume_space", "volume-id-2")],
            ],
            arrays=[_array()],
            array=["array1"],
            metrics=["volume_space"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["volume-id-2"]
        assert result["skipped"] == ["volume-id-1"]
        assert _warnings(module) == [GONE.format("Volumes", 1)]

    def test_every_resource_of_a_request_gone_asks_for_nothing_again(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[_not_found("volume-id-1", "volume-id-2")],
            metrics=["volume_space"],
        )

        assert _result(module) == {
            "available_metrics": [],
            "resources": {},
            "skipped": ["volume-id-1", "volume-id-2"],
        }
        # no resource is left: no warning that there is no data point
        assert _warnings(module) == [GONE.format("Volumes", 2)]
        pure1_client.get_metrics_history.assert_called_once()

    def test_answer_that_names_them_one_after_the_other(self, pure1_client):
        """A long list of IDs is cut in the message: the next answer names more"""
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2), _listed(number=3)],
            series=[
                _not_found("volume-id-3"),
                _not_found("volume-id-1"),
                [_series("volume_space", "volume-id-2")],
            ],
            metrics=["volume_space"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["volume-id-2"]
        assert result["skipped"] == ["volume-id-3", "volume-id-1"]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space"], ["volume-id-1", "volume-id-2", "volume-id-3"]
            ),
            _history_call(["volume_space"], ["volume-id-1", "volume-id-2"]),
            _history_call(["volume_space"], ["volume-id-2"]),
        ]

    def test_resource_that_is_gone_is_not_asked_for_in_a_later_request(
        self, pure1_client
    ):
        pods = [_listed("pods"), _listed("pods", 2)]
        module = _run(
            pure1_client,
            listed=pods,
            series=[
                error_response(404, "Not found", "No pods with id: pod-id-1"),
                [_series("pod_space", "pod-id-2")],
                [
                    _pod_series(
                        resources=[_reference("pod-id-2"), _reference("a", "array1")]
                    )
                ],
            ],
            resource_type="pods",
            metrics=["pod_space", "pod_array_read_iops"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["pod-id-2"]
        assert result["skipped"] == ["pod-id-1"]
        assert _warnings(module) == [GONE.format("Pods", 1)]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["pod_space"], ["pod-id-1", "pod-id-2"]),
            _history_call(["pod_space"], ["pod-id-2"]),
            _history_call(["pod_array_read_iops"], ["pod-id-2"]),
        ]

    def test_only_an_id_that_the_answer_names_as_a_whole_is_gone(self, pure1_client):
        """Not one that is the start or the end of a named one, or looks like it"""
        ids = ["vol-1", "vol-10", "vol-100", "xvol-100", "vol.5", "volx5"]
        module = _run(
            pure1_client,
            listed=[
                _listed(number=number, id=resource_id)
                for number, resource_id in enumerate(ids, 1)
            ],
            series=[
                _not_found("vol-10", "xvol-100", "volx5"),
                [_series("volume_space", "vol-1"), _series("volume_space", "vol.5")],
            ],
            metrics=["volume_space"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["vol-1", "vol-100", "vol.5"]
        assert result["skipped"] == ["vol-10", "xvol-100", "volx5"]
        assert _warnings(module) == [GONE.format("Volumes", 3)]
        # the others are asked for again
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["volume_space"], ids),
            _history_call(["volume_space"], ["vol-1", "vol-100", "vol.5"]),
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "No volumes with ids: volume-id-1, volume-id-2",
            "No volumes with ids: 'volume-id-1','volume-id-2'.",
            "No volumes with ids: [volume-id-2,volume-id-1]",
            "volume-id-1 and volume-id-2 are not known",
        ],
    )
    def test_ids_are_found_however_the_answer_sets_them_apart(self, pure1_client, text):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2), _listed(number=3)],
            series=[
                error_response(404, "Not found", text),
                [_series("volume_space", "volume-id-3")],
            ],
            metrics=["volume_space"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["volume-id-3"]
        assert result["skipped"] == ["volume-id-1", "volume-id-2"]

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": ["volume1", "volume2"]}, id="name"),
            pytest.param({"ids": ["volume-id-1", "volume-id-2"]}, id="ids"),
        ],
    )
    def test_task_that_names_the_resource_fails(self, pure1_client, params):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[_not_found("volume-id-2")],
            metrics=["volume_space"],
            **params
        )

        assert _failure(module) == (
            "Pure1 request get_metrics_history failed (HTTP 404): Not found: No "
            "volumes with id: volume-id-2"
        )
        pure1_client.get_metrics_history.assert_called_once()

    def test_not_found_that_names_no_resource_of_the_request_fails(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[
                error_response(404, "Not found", "No metrics with name: volume_space")
            ],
            metrics=["volume_space"],
        )

        assert _failure(module) == (
            "Pure1 request get_metrics_history failed (HTTP 404): Not found: No "
            "metrics with name: volume_space"
        )

    def test_other_error_that_names_a_resource_of_the_request_fails(self, pure1_client):
        """Only a 404 tells of a resource that went away"""
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[error_response(400, "Invalid value", "volume-id-1 has no metrics")],
            metrics=["volume_space"],
        )

        assert _failure(module) == (
            "Pure1 request get_metrics_history failed (HTTP 400): Invalid value: "
            "volume-id-1 has no metrics"
        )
        pure1_client.get_metrics_history.assert_called_once()


class TestSeriesThatCameShort:
    """An answer to get_metrics_history with fewer series than Pure1 counted."""

    def test_answer_with_fewer_series_than_its_total_gives_a_warning(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[valid_response([_series("volume_space")], total_item_count=2)],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"] == {
            "volume-id-1": _row(metrics={"volume_space": _metric_row(**POINT)}),
            "volume-id-2": _row(number=2, metrics={"volume_space": _metric_row()}),
        }
        assert _warnings(module) == [INCOMPLETE.format(1, 2, "metric series")]
        # the call takes no paging keyword: nothing is asked for a second time
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(["volume_space"], ["volume-id-1", "volume-id-2"])
        ]

    def test_series_of_all_requests_are_counted_in_one_warning(self, pure1_client):
        """33 volumes take two requests, and the answer to the first is short"""
        listed = [_listed(number=number) for number in range(1, 34)]
        first = [
            _series("volume_space", "volume-id-{0}".format(number))
            for number in range(1, 32)
        ]
        module = _run(
            pure1_client,
            listed=listed,
            series=[
                valid_response(first, total_item_count=32),
                [_series("volume_space", "volume-id-33")],
            ],
            metrics=["volume_space"],
        )

        assert len(_result(module)["resources"]) == 33
        assert _warnings(module) == [INCOMPLETE.format(32, 33, "metric series")]
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_space"],
                ["volume-id-{0}".format(number) for number in range(1, 33)],
            ),
            _history_call(["volume_space"], ["volume-id-33"]),
        ]

    def test_answer_without_a_total_gives_a_warning(self, pure1_client):
        """Nothing tells whether Pure1 has more series than it sent"""
        answers = paged_answers(
            "MetricHistoryGetResponse", [_series("volume_space")], total_item_count=None
        )
        pure1_client.get_metrics.return_value = valid_response(_catalog("volumes"))
        pure1_client.get_volumes.return_value = valid_response([_listed()])
        pure1_client.get_metrics_history.side_effect = answers

        module = run_main(
            pure1_metrics, resource_type="volumes", metrics=["volume_space"]
        )

        assert _result(module)["resources"] == {
            "volume-id-1": _row(metrics={"volume_space": _metric_row(**POINT)})
        }
        assert _warnings(module) == [
            INCOMPLETE.format(1, "an unknown number of", "metric series")
        ]
        assert answers.requests == [
            {
                "aggregation": "avg",
                "end_time": END,
                "resolution": DAY,
                "start_time": START,
                "names": ["volume_space"],
                "resource_ids": ["volume-id-1"],
            }
        ]

    def test_answer_with_more_series_than_its_total_is_read_whole(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2)],
            series=[
                valid_response(
                    [_series("volume_space"), _series("volume_space", "volume-id-2")],
                    total_item_count=1,
                )
            ],
            metrics=["volume_space"],
        )

        assert _result(module)["resources"] == {
            "volume-id-1": _row(metrics={"volume_space": _metric_row(**POINT)}),
            "volume-id-2": _row(
                number=2, metrics={"volume_space": _metric_row(**POINT)}
            ),
        }
        module.warn.assert_not_called()

    def test_warning_comes_before_the_one_for_a_resource_that_went_away(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            listed=[_listed(), _listed(number=2), _listed(number=3)],
            series=[
                _not_found("volume-id-2"),
                valid_response([_series("volume_space")], total_item_count=2),
            ],
            metrics=["volume_space"],
        )

        result = _result(module)
        assert list(result["resources"]) == ["volume-id-1", "volume-id-3"]
        assert result["skipped"] == ["volume-id-2"]
        # the answer that was refused counts no series
        assert _warnings(module) == [
            INCOMPLETE.format(1, 2, "metric series"),
            GONE.format("Volumes", 1),
        ]


class TestFailedRequests:
    """An error answer and a raised error, for each request the module makes."""

    ANSWERS = [
        pytest.param(
            error_response(
                400,
                "Invalid metric availability",
                "resolution is too fine for metric volume_space",
            ),
            " (HTTP 400): Invalid metric availability: resolution is too fine for "
            "metric volume_space",
            id="error answer with context",
        ),
        pytest.param(
            error_response(500, "Internal server error"),
            " (HTTP 500): Internal server error",
            id="error answer",
        ),
        pytest.param(
            ErrorResponse(503, [], {}),
            " (HTTP 503): Service Unavailable",
            id="error answer without message",
        ),
        pytest.param(
            PureError("token refused"), ": PureError: token refused", id="PureError"
        ),
        pytest.param(
            TimeoutError("The read operation timed out"),
            ": TimeoutError: The read operation timed out",
            id="transport error",
        ),
    ]

    @staticmethod
    def _answer_with(method, answer):
        if isinstance(answer, Exception):
            method.side_effect = answer
        else:
            method.return_value = answer

    @pytest.mark.parametrize("answer, expected", ANSWERS)
    @pytest.mark.parametrize("params", [{"list_available": True}, {"metrics": ["x"]}])
    def test_list_of_metrics_fails(self, pure1_client, params, answer, expected):
        self._answer_with(pure1_client.get_metrics, answer)

        module = run_main(pure1_metrics, resource_type="volumes", **params)

        assert _failure(module) == "Pure1 request get_metrics failed" + expected
        module.warn.assert_not_called()
        pure1_client.get_volumes.assert_not_called()

    @pytest.mark.parametrize("answer, expected", ANSWERS)
    @pytest.mark.parametrize(
        "resource_type", [name for name in TYPES if name != "arrays"]
    )
    def test_listing_fails(self, pure1_client, resource_type, answer, expected):
        lister, prefix = TYPES[resource_type][0], TYPES[resource_type][3]
        pure1_client.get_metrics.return_value = valid_response(_catalog(resource_type))
        self._answer_with(getattr(pure1_client, lister), answer)

        module = run_main(
            pure1_metrics, resource_type=resource_type, metrics=[prefix + "_space"]
        )

        assert _failure(module) == "Pure1 request {0} failed{1}".format(
            lister, expected
        )
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.parametrize("answer, expected", ANSWERS)
    @pytest.mark.parametrize(
        "params", [{}, {"array": ["array1"]}], ids=["all", "named"]
    )
    def test_listing_of_arrays_fails(self, pure1_client, params, answer, expected):
        """The second get_arrays: the listing, or the names of the array option"""
        pure1_client.get_metrics.return_value = valid_response(_catalog("arrays"))
        pure1_client.get_arrays.side_effect = [valid_response(), answer]

        module = run_main(
            pure1_metrics, resource_type="arrays", metrics=["array_space"], **params
        )

        assert _failure(module) == "Pure1 request get_arrays failed" + expected
        pure1_client.get_metrics_history.assert_not_called()

    @pytest.mark.parametrize("answer, expected", ANSWERS)
    @pytest.mark.parametrize(
        "params", [{}, {"name": ["volume1"]}], ids=["all", "named"]
    )
    def test_request_for_series_fails(self, pure1_client, params, answer, expected):
        module = _run(
            pure1_client,
            listed=[_listed()],
            series=[answer],
            metrics=["volume_space"],
            **params
        )

        assert _failure(module) == "Pure1 request get_metrics_history failed" + expected
        module.warn.assert_not_called()

    def test_second_request_for_series_fails(self, pure1_client):
        module = _run(
            pure1_client,
            listed=[_listed(number=number) for number in range(1, 34)],
            series=[
                [_series("volume_space")],
                error_response(500, "Internal server error"),
            ],
            metrics=["volume_space"],
        )

        assert _failure(module) == (
            "Pure1 request get_metrics_history failed (HTTP 500): Internal server error"
        )


class TestDocumentation:
    """RETURN and EXAMPLES against what the module does."""

    def test_result_has_the_keys_that_return_documents(self, pure1_client):
        """In both directions"""
        documented = yaml.safe_load(pure1_metrics.RETURN)["pure1_metrics"]
        top = documented["contains"]
        available = top["available_metrics"]["contains"]

        result = _result(_run(pure1_client, resource_type="pods", list_available=True))
        assert list(result) == list(top)
        assert list(result["available_metrics"][0]) == list(available)
        assert list(result["available_metrics"][0]["availabilities"][0]) == list(
            available["availabilities"]["contains"]
        )

    def test_rows_have_the_keys_that_return_documents(self, pure1_client):
        documented = yaml.safe_load(pure1_metrics.RETURN)["pure1_metrics"]
        top = documented["contains"]
        keys = top["resources"]["contains"]
        metric_keys = keys["metrics"]["contains"]

        result = _result(
            _run(
                pure1_client,
                listed=[_listed()],
                series=[[_series("volume_space")]],
                metrics=["volume_space"],
                history=True,
            )
        )
        assert list(result) == list(top)
        row = result["resources"]["volume-id-1"]
        assert list(row) == list(keys)
        assert list(row["arrays"][0]) == list(keys["arrays"]["contains"])
        assert list(row["metrics"]["volume_space"]) == [
            key for key in metric_keys if key != "arrays"
        ]

    def test_rows_of_a_pod_have_the_keys_that_return_documents(self, pure1_client):
        documented = yaml.safe_load(pure1_metrics.RETURN)["pure1_metrics"]
        keys = documented["contains"]["resources"]["contains"]
        metric_keys = keys["metrics"]["contains"]

        result = _result(
            _run(
                pure1_client,
                listed=[_listed("pods")],
                series=[[_pod_series()]],
                resource_type="pods",
                metrics=["pod_array_read_iops"],
                history=True,
            )
        )
        row = result["resources"]["pod-id-1"]
        assert list(row) == [key for key in keys if key != "serial"]
        metric = row["metrics"]["pod_array_read_iops"]
        assert list(metric) == list(metric_keys)
        assert list(metric["arrays"]["array1"]) == list(
            metric_keys["arrays"]["contains"]
        )

    def test_sample_has_the_keys_of_a_result(self):
        documented = yaml.safe_load(pure1_metrics.RETURN)["pure1_metrics"]
        keys = documented["contains"]["resources"]["contains"]
        sample = documented["sample"]

        assert list(sample) == list(documented["contains"])
        (row,) = sample["resources"].values()
        assert list(row) == list(keys)
        assert list(row["arrays"][0]) == list(keys["arrays"]["contains"])
        (metric,) = row["metrics"].values()
        assert list(metric) == [
            key for key in keys["metrics"]["contains"] if key not in ("data", "arrays")
        ]
        # the time of the sample in both forms
        assert metric["timestamp"] == "2026-01-02 03:04:05 UTC"
        assert metric["timestamp_epoch"] == END

    def test_choices_of_the_documentation_are_those_of_the_module(self):
        options = yaml.safe_load(pure1_metrics.DOCUMENTATION)["options"]

        assert options["resource_type"]["choices"] == list(TYPES)
        assert options["resource_type"]["choices"] == list(pure1_metrics.KINDS)

    @pytest.mark.parametrize("block", ["DOCUMENTATION", "RETURN", "EXAMPLES"])
    def test_ansible_doc_prints_the_documentation_as_it_is(self, block):
        """Text such as pod1::vol1 after a space is taken for RST"""
        documented = yaml.safe_load(getattr(pure1_metrics, block))
        printed = yaml.dump(documented, default_flow_style=False, width=1000)

        assert re.findall(RST_DIRECTIVE, printed) == []


def _example(name):
    """Return the task of EXAMPLES with this name and the one after it"""
    tasks = yaml.safe_load(pure1_metrics.EXAMPLES)
    names = [task["name"] for task in tasks]
    return tasks[names.index(name)], tasks[names.index(name) + 1]


def _template(text, **variables):
    return Templar(loader=None, variables=variables).template(trust_as_template(text))


def _run_example(pure1_client, task, **answers):
    """Run the module task of an example and return what it registers"""
    params = dict(task["everpure.pure1.pure1_metrics"])
    # the credentials of the harness stand in for those of the example
    params.pop("app_id", None)
    params.pop("key_file", None)
    for option in ("start_time", "end_time"):
        if option in params:
            params[option] = _template(params[option])
    module = _run(pure1_client, **dict(answers, **params))
    return {task["register"]: {"pure1_metrics": _result(module)}}, module


def _messages(task, registered):
    """Return the messages of a debug task with a loop, as ansible-core evaluates them

    Of the items that the when of the task takes, if it has one.
    """
    items = _template(task["loop"], **registered)
    if "when" in task:
        items = [
            item
            for item in items
            if _template("{{ " + task["when"] + " }}", item=item) is True
        ]
    return [
        _template(task["ansible.builtin.debug"]["msg"], item=item) for item in items
    ]


class TestExamples:
    """Each module task of EXAMPLES through main(), and what the task after it reads."""

    def test_every_example_is_run_here(self):
        tasks = yaml.safe_load(pure1_metrics.EXAMPLES)

        assert [
            task["name"] for task in tasks if "everpure.pure1.pure1_metrics" in task
        ] == [
            "List the metrics Pure1 has for arrays",
            "Collect the size and the data reduction of every array",
            "Collect the highest load of two arrays in the last day",
            "Collect the hourly read latency of two volumes of an array over a week",
            "Collect the latest hourly write latency of a stretched pod on each of "
            "its arrays",
            "Collect the daily on-demand usage of every subscription license over "
            "30 days",
        ]
        assert len(tasks) == 12

    def test_list_of_the_array_metrics(self, pure1_client):
        task, reader = _example("List the metrics Pure1 has for arrays")

        registered, module = _run_example(pure1_client, task)

        assert _messages(reader, registered) == [
            "array_read_iops [op/s]",
            "array_space [B]",
        ]

    def test_size_of_every_array(self, pure1_client):
        task, reader = _example(
            "Collect the size and the data reduction of every array"
        )

        registered, module = _run_example(
            pure1_client,
            task,
            catalog=[
                _metric("array_total_capacity", "B", ("arrays",)),
                _metric("array_data_reduction", "", ("arrays",)),
            ],
            listed=[_listed("arrays"), _listed("arrays", 2), _listed("arrays", 3)],
            series=[
                [
                    _series("array_total_capacity", "array-id-1", data=[(STAMP, 1024)]),
                    _series("array_data_reduction", "array-id-1", data=[(STAMP, 4.5)]),
                    _series("array_total_capacity", "array-id-3", data=[(STAMP, 0)]),
                ]
            ],
        )

        # array2 has no data point, which the task passes over: 0 is a size
        assert _messages(reader, registered) == [
            "array1 has 1024 bytes",
            "array3 has 0 bytes",
        ]
        # three days back, for the last daily point Pure1 has
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["array_total_capacity", "array_data_reduction"],
                ["array-id-1", "array-id-2", "array-id-3"],
                start_time=END - 3 * DAY,
            )
        ]

    @pytest.mark.parametrize(
        "loads, expected",
        [
            pytest.param([0.25, 0.8], True, id="at most 80 percent"),
            pytest.param([0.25, 0.81], False, id="above 80 percent"),
            pytest.param([0.25, None], True, id="no data point"),
        ],
    )
    def test_check_of_the_load_of_two_arrays(self, pure1_client, loads, expected):
        task, reader = _example(
            "Collect the highest load of two arrays in the last day"
        )
        series = [
            _series(
                "array_total_load",
                "array-id-{0}".format(number),
                unit="",
                aggregation="max",
                data=[] if load is None else [(STAMP, load)],
            )
            for number, load in enumerate(loads, 1)
        ]

        registered, module = _run_example(
            pure1_client,
            task,
            catalog=[_metric("array_total_load", "", ("arrays",), 180000)],
            arrays=[_array(), _array(2)],
            series=[series],
        )

        (condition,) = reader["ansible.builtin.assert"]["that"]
        assert _template("{{ " + condition + " }}", **registered) is expected
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["array_total_load"], ["array-id-1", "array-id-2"], aggregation="max"
            )
        ]

    def test_hourly_latency_of_two_volumes(self, pure1_client):
        task, reader = _example(
            "Collect the hourly read latency of two volumes of an array over a week"
        )

        registered, module = _run_example(
            pure1_client,
            task,
            catalog=[_metric("volume_read_latency_us", "us/op", ("volumes",), 30000)],
            arrays=[_array()],
            listed=[_listed(name="vol1"), _listed(number=2, name="vol2")],
            series=[
                [
                    _series(
                        "volume_read_latency_us",
                        data=[(STAMP - HOUR, 250), (STAMP, 300.5)],
                    )
                ]
            ],
        )

        assert _messages(reader, registered) == [
            "vol1 has 2 points",
            "vol2 has 0 points",
        ]
        pure1_client.get_volumes.assert_called_once_with(
            filter="arrays.name='array1' and name=('vol1','vol2')"
        )
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["volume_read_latency_us"],
                ["volume-id-1", "volume-id-2"],
                resolution=3600000,
                start_time=END - 604800000,
            )
        ]

    def test_latency_of_a_pod_on_each_of_its_arrays(self, pure1_client):
        task, reader = _example(
            "Collect the latest hourly write latency of a stretched pod on each of "
            "its arrays"
        )

        registered, module = _run_example(
            pure1_client,
            task,
            catalog=[
                _metric(
                    "pod_array_write_latency_us", "us/op", ("pods", "arrays"), 30000
                )
            ],
            listed=[_listed("pods")],
            series=[
                [
                    _pod_series(
                        metric="pod_array_write_latency_us", data=[(STAMP, 250)]
                    ),
                ]
            ],
        )

        (message,) = _messages(reader, registered)
        # ansible-core words the dictionary
        assert message.startswith("pod1: ")
        assert "array1" in message
        assert "250" in message
        loop = _template(reader["loop"], **registered)
        assert loop[0]["metrics"]["pod_array_write_latency_us"]["arrays"] == {
            "array1": dict(POINT, value=250)
        }
        pure1_client.get_pods.assert_called_once_with(filter="name='pod1'")
        assert pure1_client.get_metrics_history.call_args_list == [
            _history_call(
                ["pod_array_write_latency_us"], ["pod-id-1"], resolution=3600000
            )
        ]

    def test_usage_of_every_subscription_license(self, pure1_client):
        task, reader = _example(
            "Collect the daily on-demand usage of every subscription license over "
            "30 days"
        )

        registered, module = _run_example(
            pure1_client,
            task,
            catalog=[
                _metric(
                    "subscription_license_on_demand_space",
                    "B",
                    ("subscription-licenses",),
                )
            ],
            listed=[_listed("subscription-licenses", name="license1")],
            series=[
                [
                    _series(
                        "subscription_license_on_demand_space",
                        "subscription_license-id-1",
                        data=[(STAMP - DAY, 5), (STAMP, 7)],
                    )
                ]
            ],
        )

        assert _messages(reader, registered) == ["license1: 2 days"]
        (request,) = pure1_client.get_metrics_history.call_args_list
        # 30 days up to the time the task is templated, in milliseconds
        assert request[1]["end_time"] - request[1]["start_time"] == 30 * DAY
        assert request[1]["end_time"] % 1000 == 0
        assert request[1]["end_time"] > 1700000000000
