# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_security module.

The typical answer has the shape of a reading of a real organisation, under
invented names: an array with three insights of which one carries two CVEs
and with one anomaly, and an array without a fully qualified domain name.
What that reading cannot show is built by hand in the tests after it.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
from unittest.mock import call

import pypureclient
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


from plugins.module_utils.pure1 import pure1_time
from plugins.module_utils.security import INSIGHT_TYPES
from plugins.modules import pure1_security
from tests.unit.conftest import (
    MODELS,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

MODULE = "everpure.pure1.pure1_security"
INSIGHTS = "get_assessment_security_insights_arrays"
ANOMALIES = "get_assessment_security_anomalies"
# 2026-01-02 03:04:05 UTC, 2021-02-03 13:04:05 UTC and 2021-02-04 14:05:06 UTC
AS_OF = 1767323045000
AS_OF_TIME = "2026-01-02 03:04:05 UTC"
START = 1612357445000
START_TIME = "2021-02-03 13:04:05 UTC"
END = 1612447506000
END_TIME = "2021-02-04 14:05:06 UTC"
PASSWORD = "unsafe_password_configuration"
PROTECTION = "data_protection_needs_review"
DETECTED = "open_anomalies_detected"
MEDIUM_CVE = "medium_cve_detected"
HIGH_CVE = "high_cve_detected"
DESCRIPTION = "An unusual change in the number of volume snapshots of an array."
OPEN = "status='open'"
# ansible-doc takes out of what it prints whatever matches this (ansible/cli/doc.py)
RST_DIRECTIVE = r".. \w+?::"

SHORT = "Pure1 returned {0} of {1} security anomalies: the result may be incomplete."
UNFILED = (
    "Security anomalies that Pure1 returned without the name of an array are "
    "left out of the result: {0}."
)
OTHER_ARRAY = (
    "Security anomalies of an array that has the name of another array are "
    "left out of the result: {0}."
)
# The warnings of the insights are worded in module_utils.security
INSIGHTS_SHORT = (
    "Pure1 returned {0} of {1} security insights: the result may be incomplete."
)
INSIGHTS_UNFILED = (
    "Security insights that Pure1 returned without a type or without the "
    "name of their array are left out of the result: {0}."
)
INSIGHTS_OTHER_ARRAY = (
    "Security insights of an array that has the name of another array are "
    "left out of the result: {0}."
)
CVES_UNREAD = (
    "CVE entries of security insights that Pure1 returned without an id or "
    "without a severity are left out of the result: {0}."
)
NO_METHOD = (
    "py-pure-client 1.91.0 or later is required: this module needs "
    "Client.{0}(), which the installed py-pure-client 1.90.0 does not have."
)


def _no_cves():
    return {"critical": [], "high": [], "medium": [], "low": []}


def _reference(name, **overrides):
    """Return the reference Pure1 gives to an array."""
    fields = dict(
        id="id-" + name,
        name=name,
        resource_type="arrays",
        fqdn=name + ".example.com",
    )
    fields.update(overrides)
    return sdk_model("FixedReferenceFqdn", **fields)


def _array(name, **overrides):
    """Return an array as get_arrays lists it."""
    fields = dict(id="id-" + name, name=name, fqdn=name + ".example.com")
    fields.update(overrides)
    return sdk_model("Array", **fields)


def _insight(array="array1", kind=PASSWORD, as_of=AS_OF, cves=None, **overrides):
    """Return an insight; cves are the (id, severity) entries it carries."""
    fields = dict(type=kind, as_of=as_of, resource=_reference(array))
    if cves is not None:
        fields["additional_data"] = {
            "cves": [{"id": cve_id, "severity": severity} for cve_id, severity in cves]
        }
    fields.update(overrides)
    return sdk_model("SecurityInsightArray", **fields)


def _metric(name="array_snapshot_count", change=0.25):
    """Return a metric of an anomaly; the SDK takes a change from 0 to 1 only."""
    fields = dict(name=name)
    if change is not None:
        fields["additional_data"] = MODELS.AnomalyMetricReferenceAdditionalData(
            sdk_model("AnomalyAdditionalDataMetric", value_change_percentage=change)
        )
    return sdk_model("AnomalyMetricReference", **fields)


def _resource(name="array1", on=None, **overrides):
    """Return an object an anomaly was found on: by default the array itself."""
    fields = dict(
        id="id-" + name,
        name=name,
        resource_type="arrays",
        arrays=[_reference(array) for array in on or [name]],
        metrics=[_metric()],
    )
    fields.update(overrides)
    return sdk_model("AnomalyResource", **fields)


def _anomaly(*arrays, **overrides):
    """Return an anomaly that Pure1 found on these arrays, on array1 by default."""
    fields = dict(
        id="anomaly-1",
        type="volume_snapshot_count",
        status="open",
        description=DESCRIPTION,
        start_time=START,
        end_time=END,
        as_of=AS_OF,
        resources=[_resource(array) for array in arrays or ("array1",)],
    )
    fields.update(overrides)
    return sdk_model("Anomaly", **fields)


def _wire_anomaly(**changes):
    """Return an anomaly as Pure1 sends it: the change of its metric is a fall."""
    wire = {
        "_as_of": AS_OF,
        "id": "anomaly-1",
        "type": "volume_snapshot_count",
        "status": "open",
        "description": DESCRIPTION,
        "start_time": START,
        "end_time": END,
        "resources": [
            {
                "id": "id-array1",
                "name": "array1",
                "resource_type": "arrays",
                "arrays": [
                    {
                        "id": "id-array1",
                        "name": "array1",
                        "resource_type": "arrays",
                        "fqdn": "array1.example.com",
                    }
                ],
                "metrics": [
                    {
                        "name": "array_snapshot_count",
                        "additional_data": {"value_change_percentage": -0.25},
                    }
                ],
            }
        ],
    }
    wire.update(changes)
    return wire


def _received(wire):
    """Return an anomaly as the SDK builds it from an answer, which it does not check."""
    return MODELS.Anomaly.from_dict(wire)


def _resource_row(name="array1", on=None, **changes):
    row = {
        "name": name,
        "resource_type": "arrays",
        "arrays": list(on or [name]),
        "metrics": [{"name": "array_snapshot_count", "value_change_percentage": 0.25}],
    }
    row.update(changes)
    return row


def _anomaly_row(*arrays, **changes):
    """Return the row of _anomaly() on the same arrays."""
    row = {
        "id": "anomaly-1",
        "type": "volume_snapshot_count",
        "status": "open",
        "description": DESCRIPTION,
        "start": START_TIME,
        "start_epoch": START,
        "end": END_TIME,
        "end_epoch": END,
        "resources": [_resource_row(array) for array in arrays or ("array1",)],
    }
    row.update(changes)
    return row


def _entry(name, kinds=(), anomalies=(), **changes):
    """Return the entry of an array whose insights all have the one stamp."""
    entry = {
        "id": "id-" + name,
        "fqdn": name + ".example.com",
        "insight_types": sorted(kinds),
        "insights": [
            {"type": kind, "as_of": AS_OF_TIME, "as_of_epoch": AS_OF}
            for kind in sorted(kinds)
        ],
        "cves": _no_cves(),
        "anomalies": list(anomalies),
    }
    entry.update(changes)
    return entry


def _summary(insight_counts=None, arrays_with_insights=0, open_anomalies=0):
    return {
        "insight_counts": insight_counts or {},
        "arrays_with_insights": arrays_with_insights,
        "open_anomalies": open_anomalies,
    }


def _typical_insights():
    """Return the insights of the typical answer, out of order."""
    return [
        _insight("array2", PROTECTION, resource=_reference("array2", fqdn=None)),
        _insight("array1", PASSWORD),
        _insight(
            "array1",
            MEDIUM_CVE,
            cves=[("CVE-2023-48795", "medium"), ("CVE-2023-2650", "medium")],
        ),
        _insight("array1", DETECTED),
    ]


TYPICAL = {
    "arrays": {
        "array1": {
            "id": "id-array1",
            "fqdn": "array1.example.com",
            "insight_types": [MEDIUM_CVE, DETECTED, PASSWORD],
            "insights": [
                {"type": MEDIUM_CVE, "as_of": AS_OF_TIME, "as_of_epoch": AS_OF},
                {"type": DETECTED, "as_of": AS_OF_TIME, "as_of_epoch": AS_OF},
                {"type": PASSWORD, "as_of": AS_OF_TIME, "as_of_epoch": AS_OF},
            ],
            "cves": {
                "critical": [],
                "high": [],
                "medium": ["CVE-2023-2650", "CVE-2023-48795"],
                "low": [],
            },
            "anomalies": [
                {
                    "id": "anomaly-1",
                    "type": "volume_snapshot_count",
                    "status": "open",
                    "description": DESCRIPTION,
                    "start": "2021-02-03 13:04:05 UTC",
                    "start_epoch": 1612357445000,
                    "end": "2021-02-04 14:05:06 UTC",
                    "end_epoch": 1612447506000,
                    "resources": [
                        {
                            "name": "array1",
                            "resource_type": "arrays",
                            "arrays": ["array1"],
                            "metrics": [
                                {
                                    "name": "array_snapshot_count",
                                    "value_change_percentage": -0.25,
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "array2": {
            "id": "id-array2",
            "fqdn": None,
            "insight_types": [PROTECTION],
            "insights": [
                {"type": PROTECTION, "as_of": AS_OF_TIME, "as_of_epoch": AS_OF}
            ],
            "cves": {"critical": [], "high": [], "medium": [], "low": []},
            "anomalies": [],
        },
    },
    "summary": {
        "insight_counts": {PROTECTION: 1, MEDIUM_CVE: 1, DETECTED: 1, PASSWORD: 1},
        "arrays_with_insights": 2,
        "open_anomalies": 1,
    },
}


def _serve(client, insights=(), anomalies=(), arrays=None):
    """Give the client one page of insights and of anomalies, and the arrays of a task

    arrays are the arrays Pure1 knows by the names of the task, as names or
    as models; None for a task without the option.
    """
    getattr(client, INSIGHTS).side_effect = None
    getattr(client, INSIGHTS).return_value = valid_response(insights)
    getattr(client, ANOMALIES).side_effect = None
    getattr(client, ANOMALIES).return_value = valid_response(anomalies)
    client.get_arrays.reset_mock()
    if arrays is None:
        client.get_arrays.side_effect = None
        client.get_arrays.return_value = valid_response()
    else:
        known = [_array(each) if isinstance(each, str) else each for each in arrays]
        # the first answer is for the check get_pure1 makes
        client.get_arrays.side_effect = [valid_response(), valid_response(known)]


def _run(client, insights=(), anomalies=(), arrays=None, **params):
    _serve(client, insights, anomalies, arrays)
    return run_main(pure1_security, **params)


def _result(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_security"]


def _failure(module):
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _filters(client, method):
    """Return the filter of each request the module made with a method."""
    return [request[1]["filter"] for request in getattr(client, method).call_args_list]


class TestPure1SecurityMain:
    """The whole result of main() and the requests it makes."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_assessment_is_returned_by_array(self, pure1_client):
        module = _run(pure1_client, _typical_insights(), [_received(_wire_anomaly())])

        module.exit_json.assert_called_once_with(changed=False, pure1_security=TYPICAL)
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()
        # two requests, and the check of get_pure1
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=None)
        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=OPEN)
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_keys_are_in_the_order_of_the_documentation(self, pure1_client):
        module = _run(pure1_client, _typical_insights(), [_anomaly()])

        result = _result(module)
        assert list(result) == ["arrays", "summary"]
        assert list(result["arrays"]) == ["array1", "array2"]
        entry = result["arrays"]["array1"]
        assert list(entry) == [
            "id",
            "fqdn",
            "insight_types",
            "insights",
            "cves",
            "anomalies",
        ]
        assert list(entry["insights"][0]) == ["type", "as_of", "as_of_epoch"]
        assert list(entry["cves"]) == ["critical", "high", "medium", "low"]
        anomaly = entry["anomalies"][0]
        assert list(anomaly) == [
            "id",
            "type",
            "status",
            "description",
            "start",
            "start_epoch",
            "end",
            "end_epoch",
            "resources",
        ]
        resource = anomaly["resources"][0]
        assert list(resource) == ["name", "resource_type", "arrays", "metrics"]
        assert list(resource["metrics"][0]) == ["name", "value_change_percentage"]
        assert list(result["summary"]) == [
            "insight_counts",
            "arrays_with_insights",
            "open_anomalies",
        ]
        assert list(result["summary"]["insight_counts"]) == [
            PROTECTION,
            MEDIUM_CVE,
            DETECTED,
            PASSWORD,
        ]

    def test_check_mode_returns_the_same(self, pure1_client):
        module = _run(
            pure1_client,
            _typical_insights(),
            [_received(_wire_anomaly())],
            check_mode=True,
        )

        module.exit_json.assert_called_once_with(changed=False, pure1_security=TYPICAL)
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=None)
        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=OPEN)

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_times_are_in_utc_west_of_it_too(self, pure1_client):
        module = _run(pure1_client, [_insight()], [_anomaly()])

        entry = _result(module)["arrays"]["array1"]
        assert entry["insights"][0]["as_of"] == "2026-01-02 03:04:05 UTC"
        assert entry["anomalies"][0]["start"] == "2021-02-03 13:04:05 UTC"
        assert entry["anomalies"][0]["end"] == "2021-02-04 14:05:06 UTC"

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_option_at_once(self, pure1_client):
        module = _run(
            pure1_client,
            [_insight("Array1", HIGH_CVE, cves=[("CVE-2024-1", "high")])],
            [_anomaly("Array1", status="closed"), _anomaly("array3", id="anomaly-3")],
            arrays=["Array1", "array2"],
            array=["ARRAY1", "array2"],
            gather_subset=["anomalies", "insights"],
            insight_types=[HIGH_CVE, PASSWORD],
            anomaly_status="all",
            check_mode=True,
        )

        closed = _anomaly_row("Array1", status="closed")
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {
                    "Array1": _entry(
                        "Array1",
                        [HIGH_CVE],
                        [closed],
                        cves=dict(_no_cves(), high=["CVE-2024-1"]),
                    ),
                    "array2": _entry("array2"),
                },
                "summary": _summary({HIGH_CVE: 1}, 1, 0),
            },
        )
        module.warn.assert_not_called()
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        getattr(pure1_client, INSIGHTS).assert_called_once_with(
            filter="resource.name=('Array1','array2') and "
            "type=('high_cve_detected','unsafe_password_configuration')"
        )
        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=None)

    def test_nothing_reported_gives_an_empty_result(self, pure1_client):
        module = _run(pure1_client)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {},
                "summary": {
                    "insight_counts": {},
                    "arrays_with_insights": 0,
                    "open_anomalies": 0,
                },
            },
        )
        module.warn.assert_not_called()
        module.fail_json.assert_not_called()

    def test_objects_with_only_their_identifying_fields(self, pure1_client):
        """What Pure1 did not send is null, and an empty list where a list stands"""
        name_only = sdk_model("FixedReferenceFqdn", name="array1")
        insight = sdk_model("SecurityInsightArray", type=PASSWORD, resource=name_only)
        anomaly = sdk_model(
            "Anomaly", resources=[sdk_model("AnomalyResource", arrays=[name_only])]
        )

        module = _run(pure1_client, [insight], [anomaly])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {
                    "array1": {
                        "id": None,
                        "fqdn": None,
                        "insight_types": [PASSWORD],
                        "insights": [
                            {"type": PASSWORD, "as_of": None, "as_of_epoch": None}
                        ],
                        "cves": {"critical": [], "high": [], "medium": [], "low": []},
                        "anomalies": [
                            {
                                "id": None,
                                "type": None,
                                "status": None,
                                "description": None,
                                "start": None,
                                "start_epoch": None,
                                "end": None,
                                "end_epoch": None,
                                "resources": [
                                    {
                                        "name": None,
                                        "resource_type": None,
                                        "arrays": ["array1"],
                                        "metrics": [],
                                    }
                                ],
                            }
                        ],
                    }
                },
                "summary": {
                    "insight_counts": {PASSWORD: 1},
                    "arrays_with_insights": 1,
                    "open_anomalies": 0,
                },
            },
        )
        module.warn.assert_not_called()

    def test_metric_with_only_its_name(self, pure1_client):
        anomaly = _anomaly(resources=[_resource(metrics=[_metric(change=None)])])

        module = _run(pure1_client, anomalies=[anomaly])

        (row,) = _result(module)["arrays"]["array1"]["anomalies"]
        assert row["resources"][0]["metrics"] == [
            {"name": "array_snapshot_count", "value_change_percentage": None}
        ]

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_values_that_are_zero_or_empty_are_returned(self, pure1_client):
        """A value Pure1 sends is no missing value, whatever its truth"""
        empty = _reference("", id="", fqdn="")
        resource = _resource(
            name="",
            resource_type="",
            arrays=[empty],
            metrics=[_metric(name="", change=0)],
        )
        anomaly = _anomaly(
            id="",
            type="",
            status="",
            description="",
            start_time=0,
            end_time=0,
            resources=[resource],
        )
        insight = _insight(kind="", as_of=0, resource=empty)

        module = _run(pure1_client, [insight], [anomaly])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {
                    "": {
                        "id": "",
                        "fqdn": "",
                        "insight_types": [""],
                        "insights": [
                            {
                                "type": "",
                                "as_of": "1970-01-01 00:00:00 UTC",
                                "as_of_epoch": 0,
                            }
                        ],
                        "cves": {"critical": [], "high": [], "medium": [], "low": []},
                        "anomalies": [
                            {
                                "id": "",
                                "type": "",
                                "status": "",
                                "description": "",
                                "start": "1970-01-01 00:00:00 UTC",
                                "start_epoch": 0,
                                "end": "1970-01-01 00:00:00 UTC",
                                "end_epoch": 0,
                                "resources": [
                                    {
                                        "name": "",
                                        "resource_type": "",
                                        "arrays": [""],
                                        "metrics": [
                                            {"name": "", "value_change_percentage": 0}
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                },
                "summary": {
                    "insight_counts": {"": 1},
                    "arrays_with_insights": 1,
                    "open_anomalies": 0,
                },
            },
        )
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "data, expected",
        [
            pytest.param({"value_change_percentage": -0.25}, -0.25, id="a fall"),
            pytest.param({"value_change_percentage": 2.5}, 2.5, id="more than double"),
            pytest.param({"value_change_percentage": 0}, 0, id="zero"),
            pytest.param({"commands": ["purevol destroy vol1"]}, None, id="commands"),
            pytest.param({"unusual_patterns": ["a login"]}, None, id="login pattern"),
            pytest.param({}, None, id="empty"),
            pytest.param(None, None, id="null"),
        ],
    )
    def test_change_of_a_metric_is_what_the_sdk_keeps_of_its_data(
        self, pure1_client, data, expected
    ):
        """The SDK reads every kind of data as a change and drops the rest"""
        wire = _wire_anomaly()
        wire["resources"][0]["metrics"][0]["additional_data"] = data

        module = _run(pure1_client, anomalies=[_received(wire)])

        (row,) = _result(module)["arrays"]["array1"]["anomalies"]
        assert row["resources"][0]["metrics"] == [
            {"name": "array_snapshot_count", "value_change_percentage": expected}
        ]
        module.warn.assert_not_called()

    def test_metric_without_data(self, pure1_client):
        wire = _wire_anomaly()
        del wire["resources"][0]["metrics"][0]["additional_data"]

        module = _run(pure1_client, anomalies=[_received(wire)])

        (row,) = _result(module)["arrays"]["array1"]["anomalies"]
        assert row["resources"][0]["metrics"] == [
            {"name": "array_snapshot_count", "value_change_percentage": None}
        ]


class TestGatherSubset:
    """gather_subset decides which of the two listings is read."""

    @pytest.mark.parametrize(
        "params, insights, anomalies",
        [
            pytest.param({}, True, True, id="default"),
            pytest.param({"gather_subset": ["all"]}, True, True, id="all"),
            pytest.param({"gather_subset": []}, True, True, id="empty list"),
            pytest.param(
                {"gather_subset": ["insights", "anomalies"]}, True, True, id="both"
            ),
            pytest.param(
                {"gather_subset": ["anomalies", "all"]}, True, True, id="all and one"
            ),
            pytest.param({"gather_subset": ["insights"]}, True, False, id="insights"),
            pytest.param({"gather_subset": ["anomalies"]}, False, True, id="anomalies"),
            pytest.param(
                {"gather_subset": ["anomalies", "anomalies"]},
                False,
                True,
                id="one twice",
            ),
        ],
    )
    def test_only_what_is_collected_is_requested(
        self, pure1_client, params, insights, anomalies
    ):
        module = _run(pure1_client, _typical_insights(), [_anomaly()], **params)

        assert _filters(pure1_client, INSIGHTS) == ([None] if insights else [])
        assert _filters(pure1_client, ANOMALIES) == ([OPEN] if anomalies else [])
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        result = _result(module)
        entries = result["arrays"]
        assert [bool(entries[name]["insights"]) for name in entries] == (
            ([True, True] if insights else [False])
        )
        assert [len(entries[name]["anomalies"]) for name in entries][0] == (
            1 if anomalies else 0
        )
        assert result["summary"]["arrays_with_insights"] == (2 if insights else None)
        assert result["summary"]["open_anomalies"] == (1 if anomalies else None)
        module.warn.assert_not_called()

    def test_insights_alone(self, pure1_client):
        module = _run(
            pure1_client,
            [_insight("array1"), _insight("array2", PROTECTION)],
            gather_subset=["insights"],
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {
                    "array1": _entry("array1", [PASSWORD]),
                    "array2": _entry("array2", [PROTECTION]),
                },
                "summary": {
                    "insight_counts": {PROTECTION: 1, PASSWORD: 1},
                    "arrays_with_insights": 2,
                    "open_anomalies": None,
                },
            },
        )
        getattr(pure1_client, ANOMALIES).assert_not_called()

    def test_anomalies_alone(self, pure1_client):
        """An array is in the result for its anomaly, with what Pure1 says of it there"""
        module = _run(pure1_client, anomalies=[_anomaly()], gather_subset=["anomalies"])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_security={
                "arrays": {"array1": _entry("array1", anomalies=[_anomaly_row()])},
                "summary": {
                    "insight_counts": {},
                    "arrays_with_insights": None,
                    "open_anomalies": 1,
                },
            },
        )
        getattr(pure1_client, INSIGHTS).assert_not_called()

    def test_insight_types_are_not_looked_at_without_insights(self, pure1_client):
        module = _run(
            pure1_client,
            anomalies=[_anomaly()],
            gather_subset=["anomalies"],
            insight_types=[PASSWORD],
        )

        getattr(pure1_client, INSIGHTS).assert_not_called()
        assert list(_result(module)["arrays"]) == ["array1"]

    def test_unknown_subset_is_refused(self, pure1_client):
        module = _run(pure1_client, gather_subset=["insights", "cves"])

        # ansible-core words the refusal, with the option, its choices and the value
        message = _failure(module)
        assert "gather_subset" in message
        for choice in ("all", "insights", "anomalies"):
            assert choice in message
        assert "cves" in message
        pure1_client.get_arrays.assert_not_called()


class TestInsightTypes:
    """insight_types selects insights on the side of Pure1."""

    @pytest.mark.parametrize(
        "types, expected",
        [
            pytest.param([PASSWORD], "type='unsafe_password_configuration'", id="one"),
            pytest.param(
                [PASSWORD, HIGH_CVE],
                "type=('unsafe_password_configuration','high_cve_detected')",
                id="two",
            ),
            pytest.param([PASSWORD, PASSWORD], "type='" + PASSWORD + "'", id="twice"),
            pytest.param([], None, id="empty list"),
        ],
    )
    def test_types_are_in_the_filter_of_the_insights_alone(
        self, pure1_client, types, expected
    ):
        module = _run(pure1_client, [_insight()], [_anomaly()], insight_types=types)

        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=expected)
        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=OPEN)
        assert _result(module)["arrays"] == {
            "array1": _entry("array1", [PASSWORD], [_anomaly_row()])
        }

    def test_every_type_the_sdk_documents_can_be_given(self, pure1_client):
        _run(pure1_client, insight_types=list(INSIGHT_TYPES))

        (selection,) = _filters(pure1_client, INSIGHTS)
        assert selection == "type=({0})".format(
            ",".join("'{0}'".format(kind) for kind in INSIGHT_TYPES)
        )

    def test_unknown_type_is_refused(self, pure1_client):
        """Pure1 would answer a type it does not know with nothing"""
        module = _run(pure1_client, insight_types=[PASSWORD, "unsafe_passwords"])

        # ansible-core words the refusal, with the option, its choices and the value
        message = _failure(module)
        assert "insight_types" in message
        for kind in INSIGHT_TYPES:
            assert kind in message
        assert "unsafe_passwords" in message
        getattr(pure1_client, INSIGHTS).assert_not_called()

    def test_documentation_lists_the_types_of_the_argument_spec(self):
        options = yaml.safe_load(pure1_security.DOCUMENTATION)["options"]

        assert options["insight_types"]["choices"] == list(INSIGHT_TYPES)


class TestAnomalyStatus:
    """anomaly_status selects anomalies on the side of Pure1."""

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param({}, "status='open'", id="default"),
            pytest.param({"anomaly_status": "open"}, "status='open'", id="open"),
            pytest.param({"anomaly_status": "closed"}, "status='closed'", id="closed"),
            pytest.param({"anomaly_status": "all"}, None, id="all"),
        ],
    )
    def test_status_is_the_filter_of_the_anomalies_alone(
        self, pure1_client, params, expected
    ):
        _run(pure1_client, **params)

        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=expected)
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=None)

    @pytest.mark.parametrize(
        "status, counted",
        [
            pytest.param("open", 2, id="open"),
            pytest.param("all", 2, id="all"),
            pytest.param("closed", None, id="closed: no open anomaly was asked for"),
        ],
    )
    def test_open_anomalies_are_counted_when_they_are_asked_for(
        self, pure1_client, status, counted
    ):
        anomalies = [
            _anomaly("array1", id="anomaly-1"),
            _anomaly("array1", id="anomaly-2", status="closed"),
            _anomaly("array2", id="anomaly-3"),
        ]

        module = _run(pure1_client, anomalies=anomalies, anomaly_status=status)

        result = _result(module)
        assert result["summary"]["open_anomalies"] == counted
        assert [row["status"] for row in result["arrays"]["array1"]["anomalies"]] == [
            "open",
            "closed",
        ]

    def test_unknown_status_is_refused(self, pure1_client):
        module = _run(pure1_client, anomaly_status="resolved")

        assert _failure(module) == (
            "value of anomaly_status must be one of: open, closed, all, got: resolved"
        )
        getattr(pure1_client, ANOMALIES).assert_not_called()


class TestArrayOption:
    """array is resolved first; the insights are filtered and the anomalies narrowed."""

    def test_array_is_resolved_and_filters_with_the_names_of_pure1(self, pure1_client):
        module = _run(
            pure1_client,
            [_insight("Array1"), _insight("array2", PROTECTION)],
            [_anomaly("array2")],
            arrays=["Array1", "array2"],
            array=["ARRAY1", "array2"],
        )

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        # the insight filter of Pure1 compares the case of a name
        getattr(pure1_client, INSIGHTS).assert_called_once_with(
            filter="resource.name=('Array1','array2')"
        )
        # Pure1 has no filter on the array of an anomaly
        getattr(pure1_client, ANOMALIES).assert_called_once_with(filter=OPEN)
        assert _result(module) == {
            "arrays": {
                "Array1": _entry("Array1", [PASSWORD]),
                "array2": _entry("array2", [PROTECTION], [_anomaly_row("array2")]),
            },
            "summary": _summary({PROTECTION: 1, PASSWORD: 1}, 2, 1),
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize("names", [None, []], ids=["null", "empty list"])
    def test_array_that_is_not_set_returns_every_array(self, pure1_client, names):
        module = _run(pure1_client, [_insight()], [_anomaly("array2")], array=names)

        pure1_client.get_arrays.assert_called_once_with(limit=0)
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=None)
        assert list(_result(module)["arrays"]) == ["array1", "array2"]

    @pytest.mark.parametrize(
        "subset",
        [["all"], ["insights"], ["anomalies"]],
        ids=["all", "insights", "anomalies"],
    )
    def test_every_named_array_has_its_entry(self, pure1_client, subset):
        """With the id and the fqdn Pure1 has for the array"""
        known = [_array("array2", fqdn=None), _array("array1")]

        module = _run(
            pure1_client,
            arrays=known,
            array=["array2", "array1"],
            gather_subset=subset,
        )

        result = _result(module)
        assert result["arrays"] == {
            "array1": _entry("array1"),
            "array2": _entry("array2", fqdn=None),
        }
        assert list(result["arrays"]) == ["array1", "array2"]
        assert result["summary"] == _summary(
            arrays_with_insights=None if subset == ["anomalies"] else 0,
            open_anomalies=None if subset == ["insights"] else 0,
        )
        module.warn.assert_not_called()

    def test_unknown_array_fails_before_anything_is_read(self, pure1_client):
        module = _run(pure1_client, arrays=["array1"], array=["array1", "nosuch"])

        assert _failure(module) == "Array nosuch is not known to Pure1."
        getattr(pure1_client, INSIGHTS).assert_not_called()
        getattr(pure1_client, ANOMALIES).assert_not_called()

    def test_empty_name_fails(self, pure1_client):
        module = _run(pure1_client, array=["array1", " "])

        assert _failure(module) == "The name of an array must not be empty."
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_anomalies_of_other_arrays_are_left_out(self, pure1_client):
        anomalies = [
            _anomaly("array1", id="anomaly-1"),
            _anomaly("array2", id="anomaly-2"),
            _anomaly("array3", id="anomaly-3"),
        ]

        module = _run(
            pure1_client,
            anomalies=anomalies,
            arrays=["array3", "array1"],
            array=["array3", "array1"],
        )

        assert _result(module) == {
            "arrays": {
                "array1": _entry("array1", anomalies=[_anomaly_row("array1")]),
                "array3": _entry(
                    "array3", anomalies=[_anomaly_row("array3", id="anomaly-3")]
                ),
            },
            # the open anomaly of the array that is not named is not counted
            "summary": _summary(open_anomalies=2),
        }
        module.warn.assert_not_called()

    def test_anomaly_on_a_named_array_and_another_keeps_all_its_resources(
        self, pure1_client
    ):
        module = _run(
            pure1_client,
            anomalies=[_anomaly("array1", "array2")],
            arrays=["array2"],
            array=["array2"],
        )

        assert _result(module)["arrays"] == {
            "array2": _entry("array2", anomalies=[_anomaly_row("array1", "array2")])
        }

    def test_anomaly_of_an_array_with_the_name_in_another_case_is_left_out(
        self, pure1_client
    ):
        """The id of the array reference decides, not its name"""
        other = _reference("ARRAY1")
        anomalies = [
            _anomaly(id="anomaly-1", resources=[_resource("ARRAY1", arrays=[other])]),
            _anomaly("array1", id="anomaly-2"),
        ]

        module = _run(
            pure1_client, anomalies=anomalies, arrays=["array1"], array=["array1"]
        )

        assert _result(module) == {
            "arrays": {
                "array1": _entry(
                    "array1", anomalies=[_anomaly_row("array1", id="anomaly-2")]
                )
            },
            "summary": _summary(open_anomalies=1),
        }
        module.warn.assert_not_called()

    def test_anomaly_of_another_array_of_the_same_name_is_left_out(self, pure1_client):
        other = _reference("array1", id="id-other")
        anomaly = _anomaly(resources=[_resource("array1", arrays=[other])])

        module = _run(
            pure1_client, anomalies=[anomaly], arrays=["array1"], array=["array1"]
        )

        assert _result(module) == {
            "arrays": {"array1": _entry("array1")},
            "summary": _summary(),
        }
        module.warn.assert_not_called()

    def test_anomaly_is_filed_under_the_name_of_the_named_array(self, pure1_client):
        """The reference of an anomaly could spell the name in another way"""
        spelled = _reference("Array1", id="id-array1")
        anomaly = _anomaly(resources=[_resource("vol1", arrays=[spelled])])

        module = _run(
            pure1_client, anomalies=[anomaly], arrays=["array1"], array=["ARRAY1"]
        )

        assert _result(module)["arrays"] == {
            "array1": _entry(
                "array1",
                anomalies=[_anomaly_row(resources=[_resource_row("vol1", ["Array1"])])],
            )
        }

    @pytest.mark.parametrize(
        "reference, known",
        [
            pytest.param(
                lambda: sdk_model("FixedReferenceFqdn", name="array1"),
                lambda: _array("array1"),
                id="reference without an id",
            ),
            pytest.param(
                lambda: _reference("array1"),
                lambda: sdk_model("Array", name="array1"),
                id="array without an id",
            ),
        ],
    )
    def test_name_decides_where_an_id_is_missing(self, pure1_client, reference, known):
        anomaly = _anomaly(resources=[_resource("array1", arrays=[reference()])])

        module = _run(
            pure1_client, anomalies=[anomaly], arrays=[known()], array=["array1"]
        )

        (entry,) = _result(module)["arrays"].values()
        assert entry["anomalies"] == [_anomaly_row()]
        module.warn.assert_not_called()

    def test_name_in_another_case_is_another_name_where_an_id_is_missing(
        self, pure1_client
    ):
        """Pure1 can know two arrays whose names differ in their case alone"""
        other = sdk_model("FixedReferenceFqdn", name="ARRAY1")
        anomaly = _anomaly(resources=[_resource("ARRAY1", arrays=[other])])

        module = _run(
            pure1_client, anomalies=[anomaly], arrays=["array1"], array=["array1"]
        )

        assert _result(module) == {
            "arrays": {"array1": _entry("array1")},
            "summary": _summary(),
        }
        module.warn.assert_not_called()

    def test_named_array_has_the_id_and_the_fqdn_of_its_record(self, pure1_client):
        """Whatever the first reference to it lacks, and each of its insights is kept"""
        name_only = sdk_model("FixedReferenceFqdn", name="array1")
        insights = [
            _insight(kind=PASSWORD, resource=name_only),
            _insight(kind=PROTECTION, resource=_reference("array1", id="id-other")),
            _insight(kind=DETECTED),
        ]

        module = _run(pure1_client, insights, arrays=["array1"], array=["array1"])

        assert _result(module) == {
            "arrays": {"array1": _entry("array1", [DETECTED, PASSWORD])},
            "summary": _summary({DETECTED: 1, PASSWORD: 1}, 1, 0),
        }
        module.warn.assert_not_called()

    def test_two_arrays_that_pure1_knows_by_one_name(self, pure1_client):
        """The first has the entry, the anomaly of the other is left out and counted"""
        known = [_array("array1"), _array("array1", id="id-other")]
        other = _reference("array1", id="id-other")
        anomalies = [
            _anomaly(id="anomaly-1", resources=[_resource("array1", arrays=[other])]),
            _anomaly("array1", id="anomaly-2"),
        ]

        module = _run(pure1_client, anomalies=anomalies, arrays=known, array=["array1"])

        assert _result(module) == {
            "arrays": {
                "array1": _entry(
                    "array1", anomalies=[_anomaly_row("array1", id="anomaly-2")]
                )
            },
            "summary": _summary(open_anomalies=1),
        }
        assert _warnings(module) == [OTHER_ARRAY.format(1)]


class TestFiling:
    """Where an anomaly stands in the result."""

    def test_anomaly_on_two_arrays_is_listed_under_each_and_counted_once(
        self, pure1_client
    ):
        module = _run(pure1_client, anomalies=[_anomaly("array2", "array1")])

        row = _anomaly_row("array2", "array1")
        assert _result(module) == {
            "arrays": {
                "array1": _entry("array1", anomalies=[row]),
                "array2": _entry("array2", anomalies=[row]),
            },
            "summary": _summary(open_anomalies=1),
        }
        module.warn.assert_not_called()

    def test_anomaly_with_two_resources_on_one_array_is_listed_once(self, pure1_client):
        resources = [
            _resource("vol1", ["array1"], resource_type="volumes"),
            _resource("vol2", ["array1"], resource_type="volumes"),
        ]

        module = _run(pure1_client, anomalies=[_anomaly(resources=resources)])

        rows = [
            _resource_row("vol1", ["array1"], resource_type="volumes"),
            _resource_row("vol2", ["array1"], resource_type="volumes"),
        ]
        assert _result(module) == {
            "arrays": {
                "array1": _entry("array1", anomalies=[_anomaly_row(resources=rows)])
            },
            "summary": _summary(open_anomalies=1),
        }

    def test_resource_on_two_arrays_names_both(self, pure1_client):
        """As a volume of a pod that is stretched over two arrays"""
        resource = _resource("pod1::vol1", ["array2", "array1", "array2"])

        module = _run(pure1_client, anomalies=[_anomaly(resources=[resource])])

        row = _anomaly_row(
            resources=[_resource_row("pod1::vol1", ["array2", "array1"])]
        )
        assert _result(module)["arrays"] == {
            "array1": _entry("array1", anomalies=[row]),
            "array2": _entry("array2", anomalies=[row]),
        }

    def test_array_with_an_anomaly_alone_stands_among_the_others(self, pure1_client):
        """In the order of the names, with the fqdn of the anomaly's reference"""
        bare = sdk_model("FixedReferenceFqdn", id="id-array2", name="array2")
        anomaly = _anomaly(resources=[_resource("array2", arrays=[bare])])

        module = _run(pure1_client, [_insight("array3"), _insight("array1")], [anomaly])

        result = _result(module)
        assert list(result["arrays"]) == ["array1", "array2", "array3"]
        assert result["arrays"]["array2"] == _entry(
            "array2", fqdn=None, anomalies=[_anomaly_row("array2")]
        )
        assert result["summary"] == _summary({PASSWORD: 2}, 2, 1)

    def test_anomalies_of_an_array_are_in_the_order_of_their_start(self, pure1_client):
        anomalies = [
            _anomaly(id="anomaly-3", start_time=START + 1),
            _anomaly(id="anomaly-2", start_time=START),
            _anomaly(id="anomaly-4", start_time=None),
            _anomaly(id="anomaly-1", start_time=START),
            _anomaly(id=None, start_time=START),
        ]

        module = _run(pure1_client, anomalies=anomalies)

        rows = _result(module)["arrays"]["array1"]["anomalies"]
        assert [(row["id"], row["start_epoch"]) for row in rows] == [
            ("anomaly-4", None),
            (None, START),
            ("anomaly-1", START),
            ("anomaly-2", START),
            ("anomaly-3", START + 1),
        ]

    def test_anomaly_that_pure1_lists_twice_is_filed_once(self, pure1_client):
        """A listing that moves while it is read can give an anomaly twice"""
        module = _run(pure1_client, anomalies=[_anomaly(), _anomaly()])

        assert _result(module) == {
            "arrays": {"array1": _entry("array1", anomalies=[_anomaly_row()])},
            "summary": _summary(open_anomalies=1),
        }
        module.warn.assert_not_called()

    def test_anomalies_without_an_id_are_all_kept(self, pure1_client):
        module = _run(pure1_client, anomalies=[_anomaly(id=None), _anomaly(id=None)])

        result = _result(module)
        assert result["arrays"]["array1"]["anomalies"] == [
            _anomaly_row(id=None),
            _anomaly_row(id=None),
        ]
        assert result["summary"]["open_anomalies"] == 2

    def test_empty_id_is_an_id(self, pure1_client):
        module = _run(pure1_client, anomalies=[_anomaly(id=""), _anomaly(id="")])

        result = _result(module)
        assert result["arrays"]["array1"]["anomalies"] == [_anomaly_row(id="")]
        assert result["summary"]["open_anomalies"] == 1

    def test_null_entries_of_the_lists_of_an_anomaly_are_passed_over(
        self, pure1_client
    ):
        """The SDK does not check an answer, so a null can stand in a list"""
        anomaly = _anomaly()
        anomaly.resources[0].arrays.append(None)
        anomaly.resources[0].metrics.append(None)
        anomaly.resources.append(None)

        module = _run(pure1_client, anomalies=[anomaly])

        assert _result(module)["arrays"] == {
            "array1": _entry("array1", anomalies=[_anomaly_row()])
        }
        module.warn.assert_not_called()

    def test_insights_and_anomalies_of_two_arrays_of_one_name(self, pure1_client):
        """The array whose insight comes first keeps the name"""
        other = _reference("array1", id="id-other", fqdn="other.example.com")
        anomalies = [
            _anomaly(id="anomaly-1", resources=[_resource("array1", arrays=[other])]),
            _anomaly("array1", id="anomaly-2"),
            _anomaly(
                id="anomaly-3",
                resources=[_resource("array1", arrays=[other]), _resource("array2")],
            ),
        ]

        module = _run(pure1_client, [_insight("array1")], anomalies)

        both = _anomaly_row("array1", "array2", id="anomaly-3")
        assert _result(module) == {
            "arrays": {
                "array1": _entry(
                    "array1",
                    [PASSWORD],
                    [_anomaly_row("array1", id="anomaly-2")],
                ),
                "array2": _entry("array2", anomalies=[both]),
            },
            # the first anomaly is in no entry and is not counted
            "summary": _summary({PASSWORD: 1}, 1, 2),
        }
        assert _warnings(module) == [OTHER_ARRAY.format(2)]

    @pytest.mark.parametrize("first", ["other", "own"])
    def test_anomaly_on_both_arrays_of_one_name_is_under_the_name_once(
        self, pure1_client, first
    ):
        """Whichever of the two arrays Pure1 lists first, and nothing is left out"""
        other = _reference("array1", id="id-other")
        resources = [
            _resource("vol1", arrays=[other], resource_type="volumes"),
            _resource("vol2", arrays=[other], resource_type="volumes"),
            _resource("vol3", ["array1"], resource_type="volumes"),
        ]
        if first == "own":
            resources.reverse()

        module = _run(
            pure1_client, [_insight("array1")], [_anomaly(resources=resources)]
        )

        rows = [
            _resource_row(name, ["array1"], resource_type="volumes")
            for name in ("vol1", "vol2", "vol3")
        ]
        if first == "own":
            rows.reverse()
        # the anomaly is under the name for its resource on the array of the entry
        assert _result(module) == {
            "arrays": {
                "array1": _entry("array1", [PASSWORD], [_anomaly_row(resources=rows)])
            },
            "summary": _summary({PASSWORD: 1}, 1, 1),
        }
        module.warn.assert_not_called()

    def test_anomaly_with_two_resources_on_the_other_array_is_counted_once(
        self, pure1_client
    ):
        other = _reference("array1", id="id-other")
        resources = [
            _resource("vol1", arrays=[other], resource_type="volumes"),
            _resource("vol2", arrays=[other], resource_type="volumes"),
        ]

        module = _run(
            pure1_client, [_insight("array1")], [_anomaly(resources=resources)]
        )

        assert _result(module) == {
            "arrays": {"array1": _entry("array1", [PASSWORD])},
            "summary": _summary({PASSWORD: 1}, 1, 0),
        }
        assert _warnings(module) == [OTHER_ARRAY.format(1)]

    def test_array_reference_without_an_id_is_of_the_array_of_its_name(
        self, pure1_client
    ):
        name_only = sdk_model("FixedReferenceFqdn", name="array1")
        anomaly = _anomaly(resources=[_resource("array1", arrays=[name_only])])

        module = _run(pure1_client, [_insight("array1")], [anomaly])

        assert _result(module)["arrays"] == {
            "array1": _entry("array1", [PASSWORD], [_anomaly_row()])
        }
        module.warn.assert_not_called()

    def test_entry_made_without_an_id_takes_the_id_of_a_later_reference(
        self, pure1_client
    ):
        """With its fqdn, and another array of the name is told apart after that"""
        name_only = sdk_model("FixedReferenceFqdn", name="array1")
        other = _reference("array1", id="id-other", fqdn="other.example.com")
        anomalies = [
            _anomaly(
                id="anomaly-1", resources=[_resource("array1", arrays=[name_only])]
            ),
            _anomaly("array1", id="anomaly-2"),
            _anomaly(id="anomaly-3", resources=[_resource("array1", arrays=[other])]),
        ]

        module = _run(pure1_client, anomalies=anomalies)

        assert _result(module) == {
            "arrays": {
                "array1": _entry(
                    "array1",
                    anomalies=[
                        _anomaly_row(id="anomaly-1"),
                        _anomaly_row(id="anomaly-2"),
                    ],
                )
            },
            "summary": _summary(open_anomalies=2),
        }
        assert _warnings(module) == [OTHER_ARRAY.format(1)]


def _no_resources():
    return sdk_model("Anomaly", id="anomaly-9", status="open")


def _empty_resources():
    anomaly = _anomaly(id="anomaly-9")
    del anomaly.resources[:]
    return anomaly


def _null_resource():
    anomaly = _anomaly(id="anomaly-9")
    anomaly.resources[0] = None
    return anomaly


def _resource_without_arrays():
    resource = sdk_model("AnomalyResource", name="vol1", resource_type="volumes")
    return _anomaly(id="anomaly-9", resources=[resource])


def _resource_with_empty_arrays():
    anomaly = _anomaly(id="anomaly-9")
    del anomaly.resources[0].arrays[:]
    return anomaly


def _resource_with_null_array():
    anomaly = _anomaly(id="anomaly-9")
    anomaly.resources[0].arrays[0] = None
    return anomaly


def _array_without_name():
    nameless = sdk_model(
        "FixedReferenceFqdn", id="id-array1", fqdn="array1.example.com"
    )
    return _anomaly(id="anomaly-9", resources=[_resource(arrays=[nameless])])


class TestWarnings:
    """What cannot be filed is left out and counted."""

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_no_resources, id="no resources"),
            pytest.param(_empty_resources, id="empty resources"),
            pytest.param(_null_resource, id="null resource"),
            pytest.param(_resource_without_arrays, id="resource without arrays"),
            pytest.param(_resource_with_empty_arrays, id="empty arrays"),
            pytest.param(_resource_with_null_array, id="null array"),
            pytest.param(_array_without_name, id="array without name"),
        ],
    )
    @pytest.mark.parametrize("named", [False, True], ids=["all arrays", "named"])
    def test_anomaly_without_the_name_of_an_array_is_left_out(
        self, pure1_client, broken, named
    ):
        params = dict(arrays=["array1"], array=["array1"]) if named else {}

        module = _run(pure1_client, anomalies=[broken(), _anomaly()], **params)

        assert _result(module) == {
            "arrays": {"array1": _entry("array1", anomalies=[_anomaly_row()])},
            # an anomaly that is left out is not counted
            "summary": _summary(open_anomalies=1),
        }
        assert _warnings(module) == [UNFILED.format(1)]

    def test_anomalies_left_out_give_one_warning(self, pure1_client):
        anomalies = [_no_resources(), _anomaly(), _array_without_name()]
        anomalies[2].id = "anomaly-8"

        module = _run(pure1_client, anomalies=anomalies)

        assert list(_result(module)["arrays"]) == ["array1"]
        assert _warnings(module) == [UNFILED.format(2)]

    def test_every_warning_is_given_once_in_a_fixed_order(self, pure1_client):
        other = _reference("array1", id="id-other")
        insights = [
            _insight("array1", MEDIUM_CVE, cves=[(None, "medium")]),
            _insight("array1", PASSWORD, resource=other),
            sdk_model("SecurityInsightArray", resource=_reference("array1")),
        ]
        anomalies = [
            _anomaly(id="anomaly-1", resources=[_resource("array1", arrays=[other])]),
            _no_resources(),
            _anomaly(id="anomaly-2"),
        ]
        getattr(pure1_client, INSIGHTS).side_effect = paged_answers(
            "SecurityInsightArrayGetResponse", insights, total_item_count=7
        )
        getattr(pure1_client, ANOMALIES).side_effect = paged_answers(
            "AnomalyGetResponse", anomalies, total_item_count=5
        )

        module = run_main(pure1_security)

        assert _warnings(module) == [
            INSIGHTS_SHORT.format(3, 7),
            INSIGHTS_UNFILED.format(1),
            INSIGHTS_OTHER_ARRAY.format(1),
            CVES_UNREAD.format(1),
            SHORT.format(3, 5),
            UNFILED.format(1),
            OTHER_ARRAY.format(1),
        ]
        assert _result(module) == {
            "arrays": {
                "array1": _entry("array1", [MEDIUM_CVE], [_anomaly_row(id="anomaly-2")])
            },
            "summary": _summary({MEDIUM_CVE: 1}, 1, 1),
        }
        for warning in _warnings(module):
            assert warning.endswith(".")


class TestListings:
    """Each listing is read to its end, and one that came short is told."""

    def test_anomalies_are_read_across_pages(self, pure1_client):
        anomalies = [
            _anomaly("array{0}".format(number), id="anomaly-{0}".format(number))
            for number in range(1, 6)
        ]
        answers = paged_answers("AnomalyGetResponse", anomalies, page_size=2)
        _serve(pure1_client)
        getattr(pure1_client, ANOMALIES).side_effect = answers

        module = run_main(pure1_security, anomaly_status="closed")

        assert answers.requests == [
            {"filter": "status='closed'"},
            {"filter": "status='closed'", "continuation_token": "page-2"},
            {"filter": "status='closed'", "continuation_token": "page-4"},
        ]
        assert list(_result(module)["arrays"]) == [
            "array1",
            "array2",
            "array3",
            "array4",
            "array5",
        ]
        module.warn.assert_not_called()

    def test_insights_are_read_across_pages(self, pure1_client):
        insights = [_insight("array{0}".format(number)) for number in range(1, 4)]
        answers = paged_answers(
            "SecurityInsightArrayGetResponse", insights, page_size=2
        )
        _serve(pure1_client)
        getattr(pure1_client, INSIGHTS).side_effect = answers

        module = run_main(pure1_security, insight_types=[PASSWORD])

        selection = "type='unsafe_password_configuration'"
        assert answers.requests == [
            {"filter": selection},
            {"filter": selection, "continuation_token": "page-2"},
        ]
        assert _result(module)["summary"] == _summary({PASSWORD: 3}, 3, 0)
        module.warn.assert_not_called()

    def test_anomalies_that_came_short_are_asked_for_the_rest(self, pure1_client):
        """The request function hands the paging keywords on, and the module warns"""
        anomalies = [
            _anomaly("array{0}".format(number), id="anomaly-{0}".format(number))
            for number in range(1, 4)
        ]
        answers = paged_answers(
            "AnomalyGetResponse", anomalies, page_size=2, total_item_count=5
        )
        _serve(pure1_client)
        getattr(pure1_client, ANOMALIES).side_effect = answers

        module = run_main(pure1_security, anomaly_status="all")

        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
            {"filter": None, "offset": 3},
        ]
        assert list(_result(module)["arrays"]) == ["array1", "array2", "array3"]
        assert _warnings(module) == [
            "Pure1 returned 3 of 5 security anomalies: the result may be incomplete."
        ]

    def test_anomalies_without_a_total_that_came_short(self, pure1_client):
        answers = paged_answers(
            "AnomalyGetResponse",
            [_anomaly("array1"), _anomaly("array2", id="anomaly-2")],
            total_item_count=None,
        )

        def ignoring_the_offset(**keywords):
            keywords.pop("offset", None)
            return answers(**keywords)

        _serve(pure1_client)
        getattr(pure1_client, ANOMALIES).side_effect = ignoring_the_offset

        module = run_main(pure1_security)

        assert list(_result(module)["arrays"]) == ["array1", "array2"]
        assert _warnings(module) == [
            "Pure1 returned 2 of an unknown number of security anomalies: the "
            "result may be incomplete."
        ]

    def test_insights_that_came_short_give_the_warning_of_the_helper(
        self, pure1_client
    ):
        _serve(pure1_client)
        getattr(pure1_client, INSIGHTS).side_effect = paged_answers(
            "SecurityInsightArrayGetResponse", [_insight()], total_item_count=4
        )

        module = run_main(pure1_security)

        assert _warnings(module) == [INSIGHTS_SHORT.format(1, 4)]


class TestFailures:
    """A request that fails ends the task with the line of the shared helpers."""

    @pytest.mark.parametrize("method", [INSIGHTS, ANOMALIES])
    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request {0} failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 9"
                ),
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
        ],
    )
    def test_failed_listing_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, expected
    ):
        _serve(pure1_client, [_insight()], [_anomaly()])
        getattr(pure1_client, method).return_value = None
        setattr(getattr(pure1_client, method), attribute, answer)

        module = run_main(pure1_security)

        module.fail_json.assert_called_once_with(msg=expected.format(method))
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_anomalies_are_not_asked_for_after_the_insights_failed(self, pure1_client):
        _serve(pure1_client)
        getattr(pure1_client, INSIGHTS).return_value = error_response(500, "boom")

        module = run_main(pure1_security)

        assert _failure(module) == (
            "Pure1 request get_assessment_security_insights_arrays failed "
            "(HTTP 500): boom"
        )
        getattr(pure1_client, ANOMALIES).assert_not_called()

    def test_later_page_of_the_anomalies_fails(self, pure1_client):
        anomalies = [
            _anomaly(id="anomaly-{0}".format(number)) for number in range(1, 4)
        ]
        answers = paged_answers(
            "AnomalyGetResponse", anomalies, page_size=2, total_item_count=5
        )

        def refusing_the_offset(**keywords):
            if "offset" in keywords:
                return error_response(500, "Internal server error")
            return answers(**keywords)

        _serve(pure1_client)
        getattr(pure1_client, ANOMALIES).side_effect = refusing_the_offset

        module = run_main(pure1_security)

        assert _failure(module) == (
            "Pure1 request get_assessment_security_anomalies failed (HTTP 500): "
            "Internal server error"
        )

    def test_failed_request_for_the_arrays_fails_the_task(self, pure1_client):
        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(500, "Internal server error"),
        ]

        module = run_main(pure1_security, array=["array1"])

        assert _failure(module) == (
            "Pure1 request get_arrays failed (HTTP 500): Internal server error"
        )
        getattr(pure1_client, INSIGHTS).assert_not_called()
        getattr(pure1_client, ANOMALIES).assert_not_called()

    @pytest.mark.parametrize("missing", [INSIGHTS, ANOMALIES])
    @pytest.mark.parametrize(
        "params",
        [{}, {"gather_subset": ["insights"]}, {"gather_subset": ["anomalies"]}],
        ids=["all", "insights", "anomalies"],
    )
    def test_client_without_a_method_fails_before_any_request(
        self, pure1_client, monkeypatch, missing, params
    ):
        _serve(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.90.0")
        # What a client of an older release has for the name: nothing to call
        setattr(pure1_client, missing, None)

        module = run_main(pure1_security, array=["array1"], **params)

        assert _failure(module) == NO_METHOD.format(missing)
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)


def _contains(block, prefix=""):
    """Return the paths of the keys a RETURN block documents under contains"""
    paths = set()
    for key, entry in block.items():
        paths.add(prefix + key)
        paths |= _contains(entry.get("contains", {}), prefix + key + ".")
    return paths


def _keys(value, prefix=""):
    """Return the paths of the keys of a dictionary, through its lists as well"""
    paths = set()
    for key, inner in value.items():
        paths.add(prefix + key)
        for each in inner if isinstance(inner, list) else [inner]:
            if isinstance(each, dict):
                paths |= _keys(each, prefix + key + ".")
    return paths


def _paths(result):
    """Return the paths of the keys of a result, without the names it is keyed by"""
    paths = set(result) | {"summary." + key for key in result["summary"]}
    for entry in result["arrays"].values():
        paths |= _keys(entry, "arrays.")
    return paths


def _at(value, path):
    """Return the first value at a path of keys, through the first entry of a list"""
    for key in path.split("."):
        if isinstance(value, list):
            value = value[0]
        value = value[key]
    return value


def _template(text, variables):
    """Return what ansible-core makes of a template of a task"""
    return Templar(loader=None, variables=variables).template(trust_as_template(text))


def _condition(expression, variables):
    return _template("{{ " + expression + " }}", variables)


def _play_examples(pure1_client, **served):
    """Run the tasks of EXAMPLES in their order and return what is read

    A task of the module runs through main() with the options of the example
    and registers its result. served gives, by the name the task registers,
    the insights and the anomalies Pure1 has for it, and Pure1 knows every
    array the task names. A task that reads a result gives a list with its
    templates rendered for each item of its loop, once when it has no loop.
    """
    registered = {}
    read = []
    for task in yaml.safe_load(pure1_security.EXAMPLES):
        options = task.get(MODULE)
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            insights, anomalies = served[task["register"]]
            module = _run(
                pure1_client, insights, anomalies, options.get("array"), **options
            )
            module.fail_json.assert_not_called()
            registered[task["register"]] = module.exit_json.call_args[1]
            continue
        (action,) = set(task) - {"name", "when", "loop"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        rendered = []
        for item in items:
            variables = dict(registered, item=item)
            made = {}
            for key, value in task[action].items():
                if key == "that":
                    made[key] = [_condition(each, variables) for each in value]
                else:
                    made[key] = _template(value, variables)
            rendered.append(made)
        read.append(rendered)
    return read


class TestDocumentation:
    """DOCUMENTATION, RETURN and EXAMPLES against what the module does."""

    def _typical(self, pure1_client):
        module = _run(pure1_client, _typical_insights(), [_received(_wire_anomaly())])
        return module.exit_json.call_args[1]

    def test_return_names_the_one_key_of_the_result(self, pure1_client):
        result = self._typical(pure1_client)

        documented = yaml.safe_load(pure1_security.RETURN)
        assert set(result) - {"changed"} == set(documented) == {"pure1_security"}

    def test_return_lists_the_keys_of_the_result(self, pure1_client):
        """In both directions, at every level"""
        result = self._typical(pure1_client)["pure1_security"]

        documented = yaml.safe_load(pure1_security.RETURN)["pure1_security"]
        assert _contains(documented["contains"]) == _paths(result)
        assert len(_paths(result)) == 33

    def test_return_lists_the_keys_in_the_order_of_the_result(self, pure1_client):
        result = self._typical(pure1_client)["pure1_security"]

        contains = yaml.safe_load(pure1_security.RETURN)["pure1_security"]["contains"]
        entry = result["arrays"]["array1"]
        keys = contains["arrays"]["contains"]
        assert list(contains) == list(result)
        assert list(keys) == list(entry)
        assert list(keys["insights"]["contains"]) == list(entry["insights"][0])
        assert list(keys["cves"]["contains"]) == list(entry["cves"])
        anomaly = keys["anomalies"]["contains"]
        assert list(anomaly) == list(entry["anomalies"][0])
        resource = anomaly["resources"]["contains"]
        assert list(resource) == list(entry["anomalies"][0]["resources"][0])
        assert list(resource["metrics"]["contains"]) == [
            "name",
            "value_change_percentage",
        ]
        assert list(contains["summary"]["contains"]) == list(result["summary"])

    def test_return_gives_the_type_of_every_key(self, pure1_client):
        result = self._typical(pure1_client)["pure1_security"]
        # array1 has a value for every key
        value = {"arrays": [result["arrays"]["array1"]], "summary": result["summary"]}

        documented = yaml.safe_load(pure1_security.RETURN)["pure1_security"]
        types = {"dict": dict, "float": float, "int": int, "list": list, "str": str}
        entries = {}

        def collect(block, prefix=""):
            for key, entry in block.items():
                entries[prefix + key] = entry
                collect(entry.get("contains", {}), prefix + key + ".")

        collect(documented["contains"])
        assert types[documented["type"]] is type(result)
        assert types[entries["arrays"]["type"]] is type(result["arrays"])
        for path, entry in entries.items():
            if path == "arrays":
                continue
            found = _at(value, path)
            assert types[entry["type"]] is type(found), path
            if entry["type"] == "list":
                for each in found:
                    assert types[entry["elements"]] is type(each), path
        assert value["arrays"][0]["cves"]["medium"] and value["arrays"][0]["fqdn"]

    def test_every_key_of_return_is_an_identifier(self):
        """validate-modules of ansible-core 2.21 refuses any other key"""
        documented = yaml.safe_load(pure1_security.RETURN)

        for path in _contains(documented):
            assert all(key.isidentifier() for key in path.split(".")), path

    def test_sample_has_the_shape_of_a_result(self, pure1_client):
        result = self._typical(pure1_client)["pure1_security"]

        sample = yaml.safe_load(pure1_security.RETURN)["pure1_security"]["sample"]
        assert _paths(sample) == _paths(result)
        assert list(sample) == ["arrays", "summary"]
        assert list(sample["arrays"]) == ["array1", "array2"]
        for entry in sample["arrays"].values():
            assert list(entry) == list(result["arrays"]["array1"])
            assert list(entry["cves"]) == ["critical", "high", "medium", "low"]
        array1 = sample["arrays"]["array1"]
        assert array1["insight_types"] == [row["type"] for row in array1["insights"]]
        assert array1["insight_types"] == sorted(array1["insight_types"])
        for row in array1["insights"]:
            assert row["as_of"] == pure1_time(row["as_of_epoch"])
        (anomaly,) = array1["anomalies"]
        assert anomaly["start"] == pure1_time(anomaly["start_epoch"])
        assert anomaly["end"] == pure1_time(anomaly["end_epoch"])
        assert sample["arrays"]["array2"] == {
            "id": "0f8fad5b-d9cb-469f-a165-70867728950e",
            "fqdn": None,
            "insight_types": [],
            "insights": [],
            "cves": {"critical": [], "high": [], "medium": [], "low": []},
            "anomalies": [],
        }
        # the counts of the sample are those of its arrays
        assert sample["summary"] == {
            "insight_counts": dict.fromkeys(array1["insight_types"], 1),
            "arrays_with_insights": 1,
            "open_anomalies": 1,
        }

    @pytest.mark.parametrize("block", ["DOCUMENTATION", "RETURN"])
    def test_ansible_doc_prints_the_documentation_as_it_is(self, block):
        """ansible-doc takes what looks like an RST directive out of a text"""
        documented = yaml.safe_load(getattr(pure1_security, block))
        printed = yaml.dump(documented, default_flow_style=False, width=1000)

        assert re.findall(RST_DIRECTIVE, printed) == []

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "is returned when it is not set or empty.",
                id="an empty list of arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "Every array named here is in the result, with empty lists when "
                "Pure1 reports nothing for it.",
                id="a named array without anything",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "The module reads the anomalies of all arrays and keeps those of "
                "the arrays named here.",
                id="anomalies are narrowed in the module",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.gather_subset",
                "An empty list is C(all) as well.",
                id="an empty list of subsets",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.gather_subset",
                "Without the insights C(summary.insight_counts) is empty and "
                "C(summary.arrays_with_insights) is null, and without the "
                "anomalies C(summary.open_anomalies) is null.",
                id="what is not collected",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.insight_types",
                "are returned when it is not set or empty.",
                id="an empty list of types",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.anomaly_status",
                "Pure1 calls an anomaly open while its end is less than one week "
                "old",
                id="what open means",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.anomaly_status",
                "An anomaly closes by itself one week after it ended.",
                id="what closed means",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.arrays",
                "With I(array) every named array is present, with empty lists "
                "when nothing is reported for it.",
                id="the entry of a named array",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.arrays",
                "what Pure1 reports for the other one is left out and the module "
                "gives a warning.",
                id="two arrays of one name",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.arrays.contains.anomalies",
                "is listed under each of them",
                id="an anomaly on several arrays",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.summary.contains.arrays_with_insights",
                "Null when insights are not collected.",
                id="count of what is not collected",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.summary.contains.open_anomalies",
                "Null when the task does not read the open anomalies",
                id="open anomalies that were not asked for",
            ),
            pytest.param(
                "RETURN",
                "pure1_security.contains.summary.contains.open_anomalies",
                "An anomaly that is left out of the result with a warning is not "
                "counted.",
                id="open anomalies that are left out",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_security, block))
        for key in path.split("."):
            entry = entry[key]
        description = entry["description"]
        if isinstance(description, list):
            description = " ".join(description)

        assert says in " ".join(description.split())

    def test_description_says_how_old_the_insights_can_be(self):
        description = yaml.safe_load(pure1_security.DOCUMENTATION)["description"]

        assert (
            "Pure1 works the insights out periodically, so they can be some "
            "hours old." in " ".join(" ".join(description).split())
        )

    def test_notes_name_the_release_of_py_pure_client(self):
        notes = " ".join(yaml.safe_load(pure1_security.DOCUMENTATION)["notes"])

        assert "requires py-pure-client 1.91.0 or later" in notes
        assert "C({0})".format(INSIGHTS) in notes
        assert "C({0})".format(ANOMALIES) in notes

    def test_documentation_gives_the_choices_and_defaults_of_the_spec(self):
        options = yaml.safe_load(pure1_security.DOCUMENTATION)["options"]

        assert set(options) == {
            "array",
            "gather_subset",
            "insight_types",
            "anomaly_status",
        }
        assert options["gather_subset"]["choices"] == ["all", "insights", "anomalies"]
        assert options["gather_subset"]["default"] == ["all"]
        assert options["anomaly_status"]["choices"] == ["open", "closed", "all"]
        assert options["anomaly_status"]["default"] == "open"

    def test_examples_use_every_option_of_the_module(self):
        options = set(yaml.safe_load(pure1_security.DOCUMENTATION)["options"])
        used = set()
        for task in yaml.safe_load(pure1_security.EXAMPLES):
            arguments = task.get(MODULE)
            if arguments:
                used.update(set(arguments) - {"app_id", "key_file"})

        assert used == options

    def test_examples_read_what_the_module_returns(self, pure1_client):
        """The tasks after a task of the module, on the result it registered"""
        read = _play_examples(
            pure1_client,
            security=([_insight("array2", PROTECTION), _insight("array1")], []),
            posture=([], []),
            history=([], [_anomaly(), _anomaly(id="anomaly-2", status="closed")]),
        )

        assert read == [
            [
                {"msg": "array1 has ['unsafe_password_configuration']"},
                {"msg": "array2 has ['data_protection_needs_review']"},
            ],
            [{"that": [True], "fail_msg": "Pure1 reports open security anomalies"}],
            [
                {"that": [True], "fail_msg": "array1 has [] with the CVEs []"},
                {"that": [True], "fail_msg": "array2 has [] with the CVEs []"},
            ],
            [
                {
                    "msg": "volume_snapshot_count from 2021-02-03 13:04:05 UTC to "
                    "2021-02-04 14:05:06 UTC is open"
                },
                {
                    "msg": "volume_snapshot_count from 2021-02-03 13:04:05 UTC to "
                    "2021-02-04 14:05:06 UTC is closed"
                },
            ],
        ]

    def test_checks_of_the_examples_fail_on_what_they_look_for(self, pure1_client):
        cves = [("CVE-2024-2", "high"), ("CVE-2024-1", "critical")]
        posture = [
            _insight("array2", HIGH_CVE, cves=cves),
            _insight("array2", PASSWORD),
        ]

        read = _play_examples(
            pure1_client,
            security=([], [_anomaly()]),
            posture=(posture, []),
            history=([], []),
        )

        assert read[1] == [
            {"that": [False], "fail_msg": "Pure1 reports open security anomalies"}
        ]
        assert read[2] == [
            {"that": [True], "fail_msg": "array1 has [] with the CVEs []"},
            {
                "that": [False],
                "fail_msg": "array2 has ['high_cve_detected', "
                "'unsafe_password_configuration'] with the CVEs "
                "['CVE-2024-1', 'CVE-2024-2']",
            },
        ]
        # the array has no anomaly: the loop of the last example has no item
        assert read[3] == []

    def test_examples_ask_pure1_for_what_they_say(self, pure1_client):
        _play_examples(
            pure1_client, security=([], []), posture=([], []), history=([], [])
        )

        # the last task of the module in EXAMPLES: the anomalies of one array
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='array1'"),
        ]
        # the first task reads both listings, the second and the third one each
        assert _filters(pure1_client, INSIGHTS) == [
            None,
            "resource.name=('array1','array2') and type=("
            "'unsafe_password_configuration','critical_cve_detected',"
            "'high_cve_detected')",
        ]
        assert _filters(pure1_client, ANOMALIES) == [OPEN, None]
