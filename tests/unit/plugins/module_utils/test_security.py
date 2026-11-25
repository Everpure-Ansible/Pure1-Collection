# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the security insights helper of module_utils.security.

The first fixture has the shape of a reading of a real organisation, under
invented names: an array with four insights of which one carries two CVEs,
and an array without a fully qualified domain name. What that reading cannot
show is built by hand in the tests after it.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import copy
import re

import pytest
from pypureclient import ApiError, ErrorResponse, PureError

from plugins.module_utils.pure1 import pure1_resolve_arrays
from plugins.module_utils.security import (
    CVE_INSIGHT_TYPES,
    CVE_SEVERITIES,
    INSIGHT_TYPES,
    pure1_security_insights,
)
from tests.unit.conftest import (
    MODELS,
    ansible_module,
    error_response,
    paged_answers,
    sdk_model,
    valid_response,
)

AS_OF = 1612357445000
TIME = "2021-02-03 13:04:05 UTC"
LATER = 1612447506000
LATER_TIME = "2021-02-04 14:05:06 UTC"
ALERTS = "open_critical_alerts_detected"
PASSWORD = "unsafe_password_configuration"
PROTECTION = "data_protection_needs_review"
MEDIUM_CVE = "medium_cve_detected"
HIGH_CVE = "high_cve_detected"
METHOD = "get_assessment_security_insights_arrays"
NO_CVES = {"critical": [], "high": [], "medium": [], "low": []}
UNFILED = (
    "Security insights that Pure1 returned without a type or without the "
    "name of their array are left out of the result: {0}."
)
OTHER_ARRAY = (
    "Security insights of an array that has the name of another array are "
    "left out of the result: {0}."
)
UNREAD = (
    "CVE entries of security insights that Pure1 returned without an id or "
    "without a severity are left out of the result: {0}."
)


def _reference(name, **overrides):
    """Return the reference an insight has to its array."""
    fields = dict(
        id="id-" + name,
        name=name,
        resource_type="arrays",
        fqdn=name + ".example.com",
    )
    fields.update(overrides)
    return sdk_model("FixedReferenceFqdn", **fields)


def _insight(array="array1", kind=PASSWORD, as_of=AS_OF, cves=None, **overrides):
    """Return an insight; cves are the (id, severity) entries it carries."""
    fields = dict(type=kind, as_of=as_of, resource=_reference(array))
    if cves is not None:
        fields["additional_data"] = {
            "cves": [{"id": cve_id, "severity": severity} for cve_id, severity in cves]
        }
    fields.update(overrides)
    return sdk_model("SecurityInsightArray", **fields)


def _received(additional_data, kind=MEDIUM_CVE):
    """Return an insight as the SDK builds it from an answer, which it does not check."""
    return MODELS.SecurityInsightArray.from_dict(
        {
            "_as_of": AS_OF,
            "type": kind,
            "resource": {"id": "id-array1", "name": "array1"},
            "additional_data": additional_data,
        }
    )


def _record(name, **overrides):
    """Return an array as pure1_resolve_arrays returns it."""
    record = dict(
        id="id-" + name,
        name=name,
        fqdn=name + ".example.com",
        os="Purity//FA",
        model="FA-X70R4",
        version="6.5.0",
    )
    record.update(overrides)
    return record


def _row(kind=PASSWORD, as_of=AS_OF, time=TIME):
    return dict(type=kind, as_of=time, as_of_epoch=as_of)


def _entry(name, kinds=(PASSWORD,), **overrides):
    """Return the entry of an array whose insights all have the first stamp."""
    entry = dict(
        id="id-" + name,
        fqdn=name + ".example.com",
        insight_types=list(kinds),
        insights=[_row(kind) for kind in kinds],
        cves=copy.deepcopy(NO_CVES),
    )
    entry.update(overrides)
    return entry


def _cves(**found):
    return dict(NO_CVES, **found)


def _serve(client, insights, **response):
    """Answer the listing with one page."""
    getattr(client, METHOD).return_value = valid_response(insights, **response)


def _read(client, insights, *args, **kwargs):
    """Return the module and what the helper makes of these insights."""
    module = ansible_module()
    _serve(client, insights)
    return module, pure1_security_insights(module, client, *args, **kwargs)


def _warnings(module):
    return [call[0][0] for call in module.warn.call_args_list]


def _reading():
    """Return six insights that have every shape of that reading, out of order."""
    return [
        _insight("array2", PROTECTION, LATER, resource=_reference("array2", fqdn=None)),
        _insight("array1", PASSWORD),
        _insight(
            "array1",
            MEDIUM_CVE,
            LATER,
            cves=[("CVE-2023-48795", "medium"), ("CVE-2023-2650", "medium")],
        ),
        _insight("array1", ALERTS),
        _insight("array2", PASSWORD, resource=_reference("array2", fqdn=None)),
        _insight("array1", PROTECTION, LATER),
    ]


READING = {
    "arrays": {
        "array1": {
            "id": "id-array1",
            "fqdn": "array1.example.com",
            "insight_types": [PROTECTION, MEDIUM_CVE, ALERTS, PASSWORD],
            "insights": [
                {"type": PROTECTION, "as_of": LATER_TIME, "as_of_epoch": LATER},
                {"type": MEDIUM_CVE, "as_of": LATER_TIME, "as_of_epoch": LATER},
                {"type": ALERTS, "as_of": TIME, "as_of_epoch": AS_OF},
                {"type": PASSWORD, "as_of": TIME, "as_of_epoch": AS_OF},
            ],
            "cves": {
                "critical": [],
                "high": [],
                "medium": ["CVE-2023-2650", "CVE-2023-48795"],
                "low": [],
            },
        },
        "array2": {
            "id": "id-array2",
            "fqdn": None,
            "insight_types": [PROTECTION, PASSWORD],
            "insights": [
                {"type": PROTECTION, "as_of": LATER_TIME, "as_of_epoch": LATER},
                {"type": PASSWORD, "as_of": TIME, "as_of_epoch": AS_OF},
            ],
            "cves": {"critical": [], "high": [], "medium": [], "low": []},
        },
    },
    "cves": {"CVE-2023-2650": ["array1"], "CVE-2023-48795": ["array1"]},
}


class TestConstants:
    """The names a module takes its choices from."""

    def test_insight_types_are_those_the_sdk_documents(self):
        documented = MODELS.SecurityInsightArray.__fields__["type"].field_info
        assert list(INSIGHT_TYPES) == re.findall(r"`(\w+)`", documented.description)
        assert len(INSIGHT_TYPES) == 16

    def test_cve_severities_are_the_worst_first(self):
        assert CVE_SEVERITIES == ("critical", "high", "medium", "low")

    def test_every_cve_severity_has_its_insight_type(self):
        assert CVE_INSIGHT_TYPES == {
            "critical": "critical_cve_detected",
            "high": "high_cve_detected",
            "medium": "medium_cve_detected",
            "low": "low_cve_detected",
        }
        assert list(CVE_INSIGHT_TYPES) == list(CVE_SEVERITIES)
        assert set(CVE_INSIGHT_TYPES.values()) <= set(INSIGHT_TYPES)


class TestRequest:
    """The one listing the helper asks Pure1 for."""

    def test_every_insight_is_asked_for_without_a_filter(self, pure1_client):
        module, result = _read(pure1_client, [])

        getattr(pure1_client, METHOD).assert_called_once_with(filter=None)
        assert result == {"arrays": {}, "cves": {}}
        module.warn.assert_not_called()
        module.exit_json.assert_not_called()
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize(
        "names, expected",
        [
            pytest.param(["array1"], "resource.name='array1'", id="one"),
            pytest.param(
                ["array2", "Array1"], "resource.name=('array2','Array1')", id="two"
            ),
            pytest.param(["o'brien"], "resource.name='o\\'brien'", id="quote"),
        ],
    )
    def test_arrays_are_named_in_the_filter_as_their_records_spell_them(
        self, pure1_client, names, expected
    ):
        records = [_record(name) for name in names]
        given = copy.deepcopy(records)

        _read(pure1_client, [], records)

        getattr(pure1_client, METHOD).assert_called_once_with(filter=expected)
        # the records of the caller stay as they were
        assert records == given

    def test_two_records_of_one_name_are_named_once(self, pure1_client):
        records = [_record("array1"), _record("array1", id="id-other")]

        _read(pure1_client, [], records)

        getattr(pure1_client, METHOD).assert_called_once_with(
            filter="resource.name='array1'"
        )

    @pytest.mark.parametrize(
        "types, expected",
        [
            pytest.param([PASSWORD], "type='unsafe_password_configuration'", id="one"),
            pytest.param(
                [PASSWORD, ALERTS],
                "type=('unsafe_password_configuration',"
                "'open_critical_alerts_detected')",
                id="two",
            ),
            pytest.param(PASSWORD, "type='unsafe_password_configuration'", id="string"),
            pytest.param(
                [CVE_INSIGHT_TYPES[severity] for severity in ("high", "critical")],
                "type=('high_cve_detected','critical_cve_detected')",
                id="two severities",
            ),
            pytest.param(
                CVE_INSIGHT_TYPES.values(),
                "type=('critical_cve_detected','high_cve_detected',"
                "'medium_cve_detected','low_cve_detected')",
                id="the CVE types",
            ),
            pytest.param([], None, id="empty list"),
            pytest.param(None, None, id="none"),
        ],
    )
    def test_types_are_selected_in_the_filter(self, pure1_client, types, expected):
        _read(pure1_client, [], types=types)

        getattr(pure1_client, METHOD).assert_called_once_with(filter=expected)

    def test_arrays_and_types_are_both_in_the_filter(self, pure1_client):
        _read(pure1_client, [], [_record("array1")], [PASSWORD, ALERTS])

        getattr(pure1_client, METHOD).assert_called_once_with(
            filter="resource.name='array1' and "
            "type=('unsafe_password_configuration','open_critical_alerts_detected')"
        )

    def test_empty_list_of_arrays_asks_for_nothing(self, pure1_client):
        module = ansible_module()

        result = pure1_security_insights(module, pure1_client, [], types=[PASSWORD])

        getattr(pure1_client, METHOD).assert_not_called()
        assert result == {"arrays": {}, "cves": {}}
        module.warn.assert_not_called()

    def test_names_are_those_pure1_resolve_arrays_returned(self, pure1_client):
        module = ansible_module()
        pure1_client.get_arrays.return_value = valid_response(
            [sdk_model("Array", id="id-Array1", name="Array1", os="Purity//FA")]
        )
        _serve(pure1_client, [_insight("Array1")])

        records = pure1_resolve_arrays(module, pure1_client, ["ARRAY1"])
        result = pure1_security_insights(module, pure1_client, records)

        pure1_client.get_arrays.assert_called_once_with(filter="name='ARRAY1'")
        getattr(pure1_client, METHOD).assert_called_once_with(
            filter="resource.name='Array1'"
        )
        assert result["arrays"] == {"Array1": _entry("Array1")}
        module.warn.assert_not_called()

    def test_listing_is_read_across_pages(self, pure1_client):
        insights = [_insight("array{0}".format(number)) for number in range(1, 6)]
        answers = paged_answers(
            "SecurityInsightArrayGetResponse", insights, page_size=2
        )
        getattr(pure1_client, METHOD).side_effect = answers
        module = ansible_module()

        result = pure1_security_insights(module, pure1_client, types=[PASSWORD, ALERTS])

        selection = (
            "type=('unsafe_password_configuration','open_critical_alerts_detected')"
        )
        assert answers.requests == [
            {"filter": selection},
            {"filter": selection, "continuation_token": "page-2"},
            {"filter": selection, "continuation_token": "page-4"},
        ]
        assert list(result["arrays"]) == [
            "array1",
            "array2",
            "array3",
            "array4",
            "array5",
        ]
        module.warn.assert_not_called()

    def test_listing_that_came_short_is_asked_for_the_rest(self, pure1_client):
        """The request function hands the paging keywords on, and the helper warns."""
        insights = [_insight("array{0}".format(number)) for number in range(1, 4)]
        answers = paged_answers(
            "SecurityInsightArrayGetResponse",
            insights,
            page_size=2,
            total_item_count=5,
        )
        getattr(pure1_client, METHOD).side_effect = answers
        module = ansible_module()

        result = pure1_security_insights(module, pure1_client, [_record("array2")])

        assert answers.requests == [
            {"filter": "resource.name='array2'"},
            {"filter": "resource.name='array2'", "continuation_token": "page-2"},
            {"filter": "resource.name='array2'", "offset": 3},
        ]
        assert result["arrays"] == {"array2": _entry("array2")}
        assert _warnings(module) == [
            "Pure1 returned 3 of 5 security insights: the result may be incomplete."
        ]

    def test_listing_without_a_total_that_came_short(self, pure1_client):
        answers = paged_answers(
            "SecurityInsightArrayGetResponse",
            [_insight("array1"), _insight("array2")],
            total_item_count=None,
        )

        def ignoring_the_offset(**keywords):
            keywords.pop("offset", None)
            return answers(**keywords)

        getattr(pure1_client, METHOD).side_effect = ignoring_the_offset
        module = ansible_module()

        result = pure1_security_insights(module, pure1_client)

        assert list(result["arrays"]) == ["array1", "array2"]
        assert _warnings(module) == [
            "Pure1 returned 2 of an unknown number of security insights: the "
            "result may be incomplete."
        ]

    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request get_assessment_security_insights_arrays failed "
                "(HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(
                    400, [ApiError(None, "Response is not a valid JSON")], {}
                ),
                "Pure1 request get_assessment_security_insights_arrays failed "
                "(HTTP 400): The request is too long for Pure1: give fewer "
                "names or IDs in one task.",
                id="too many names",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_assessment_security_insights_arrays failed: "
                "PureError: token refused",
                id="raised error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_the_line_of_the_helpers(
        self, pure1_client, attribute, answer, expected
    ):
        module = ansible_module()
        setattr(getattr(pure1_client, METHOD), attribute, answer)

        with pytest.raises(SystemExit):
            pure1_security_insights(module, pure1_client, [_record("array1")])

        module.fail_json.assert_called_once_with(msg=expected)
        module.warn.assert_not_called()


class TestResult:
    """The insights by array."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_reading_of_an_organisation(self, pure1_client):
        module, result = _read(pure1_client, _reading())

        assert result == READING
        assert list(result) == ["arrays", "cves"]
        assert list(result["arrays"]["array1"]) == [
            "id",
            "fqdn",
            "insight_types",
            "insights",
            "cves",
        ]
        assert list(result["arrays"]["array1"]["cves"]) == list(CVE_SEVERITIES)
        getattr(pure1_client, METHOD).assert_called_once_with(filter=None)
        module.warn.assert_not_called()
        module.exit_json.assert_not_called()
        module.fail_json.assert_not_called()

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_time_is_in_utc_west_of_it_too(self, pure1_client):
        result = _read(pure1_client, [_insight(as_of=LATER)])[1]

        assert result["arrays"]["array1"]["insights"] == [
            {"type": PASSWORD, "as_of": LATER_TIME, "as_of_epoch": LATER}
        ]

    def test_reading_of_named_arrays_is_the_same(self, pure1_client):
        module, result = _read(
            pure1_client, _reading(), [_record("array2", fqdn=None), _record("array1")]
        )

        assert result == READING
        module.warn.assert_not_called()

    def test_insight_with_only_its_type_and_the_name_of_its_array(self, pure1_client):
        bare = sdk_model(
            "SecurityInsightArray",
            type=PASSWORD,
            resource=sdk_model("FixedReferenceFqdn", name="array1"),
        )

        module, result = _read(pure1_client, [bare])

        assert result == {
            "arrays": {
                "array1": {
                    "id": None,
                    "fqdn": None,
                    "insight_types": [PASSWORD],
                    "insights": [
                        {"type": PASSWORD, "as_of": None, "as_of_epoch": None}
                    ],
                    "cves": {"critical": [], "high": [], "medium": [], "low": []},
                }
            },
            "cves": {},
        }
        module.warn.assert_not_called()

    def test_empty_values_are_values(self, pure1_client):
        """Only what Pure1 did not send leaves an insight out or gives a null."""
        empty = _insight(
            kind="", as_of=0, resource=_reference("", id="", fqdn=""), cves=[]
        )

        module, result = _read(pure1_client, [empty])

        assert result == {
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
                    "cves": NO_CVES,
                }
            },
            "cves": {},
        }
        module.warn.assert_not_called()

    def test_arrays_are_in_the_order_of_their_names(self, pure1_client):
        result = _read(
            pure1_client,
            [_insight("array3"), _insight("array1"), _insight("array2")],
        )[1]

        assert list(result["arrays"]) == ["array1", "array2", "array3"]

    def test_insights_are_in_the_order_of_type_and_time(self, pure1_client):
        untimed = _insight(kind=PASSWORD, as_of=None)
        insights = [
            _insight(kind=PASSWORD, as_of=LATER),
            _insight(kind=PROTECTION, as_of=LATER),
            _insight(kind=PASSWORD, as_of=0),
            untimed,
            _insight(kind=ALERTS, as_of=LATER),
            _insight(kind=PASSWORD, as_of=AS_OF),
        ]

        result = _read(pure1_client, insights)[1]

        assert result["arrays"]["array1"]["insights"] == [
            _row(PROTECTION, LATER, LATER_TIME),
            _row(ALERTS, LATER, LATER_TIME),
            _row(PASSWORD, None, None),
            _row(PASSWORD, 0, "1970-01-01 00:00:00 UTC"),
            _row(PASSWORD, AS_OF, TIME),
            _row(PASSWORD, LATER, LATER_TIME),
        ]
        # each type once, however many insights have it
        assert result["arrays"]["array1"]["insight_types"] == [
            PROTECTION,
            ALERTS,
            PASSWORD,
        ]

    def test_each_array_holds_its_own_insights(self, pure1_client):
        insights = [
            _insight("array1", PASSWORD),
            _insight("array2", HIGH_CVE, cves=[("CVE-2024-1", "high")]),
            _insight("array1", ALERTS),
        ]

        result = _read(pure1_client, insights)[1]

        assert result == {
            "arrays": {
                "array1": _entry("array1", [ALERTS, PASSWORD]),
                "array2": _entry("array2", [HIGH_CVE], cves=_cves(high=["CVE-2024-1"])),
            },
            "cves": {"CVE-2024-1": ["array2"]},
        }
        # no list of one array is that of another
        result["arrays"]["array1"]["cves"]["low"].append("CVE-2024-2")
        assert result["arrays"]["array2"]["cves"]["low"] == []


class TestCves:
    """The CVE ids that the insights carry."""

    def test_cve_is_under_the_severity_its_entry_names(self, pure1_client):
        cves = [
            ("CVE-2024-4", "low"),
            ("CVE-2024-1", "critical"),
            ("CVE-2024-3", "medium"),
            ("CVE-2024-2", "high"),
        ]

        module, result = _read(pure1_client, [_insight(kind=HIGH_CVE, cves=cves)])

        assert result["arrays"]["array1"]["cves"] == {
            "critical": ["CVE-2024-1"],
            "high": ["CVE-2024-2"],
            "medium": ["CVE-2024-3"],
            "low": ["CVE-2024-4"],
        }
        assert result["cves"] == {
            "CVE-2024-1": ["array1"],
            "CVE-2024-2": ["array1"],
            "CVE-2024-3": ["array1"],
            "CVE-2024-4": ["array1"],
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "severity",
        [
            pytest.param(None, id="none sent"),
            pytest.param("none", id="none"),
            pytest.param("HIGH", id="another case"),
            pytest.param("", id="empty"),
            pytest.param(7, id="number"),
            pytest.param(["high"], id="list"),
        ],
    )
    def test_entry_that_names_none_of_the_four_severities_has_that_of_its_insight(
        self, pure1_client, severity
    ):
        entry = {"id": "CVE-2024-1"}
        if severity is not None:
            entry["severity"] = severity
        insights = [
            _received({"cves": [entry]}, kind="critical_cve_detected"),
            _received({"cves": [entry]}, kind="low_cve_detected"),
        ]

        module, result = _read(pure1_client, insights)

        assert result["arrays"]["array1"]["cves"] == _cves(
            critical=["CVE-2024-1"], low=["CVE-2024-1"]
        )
        # one array, however many of its lists have the CVE
        assert result["cves"] == {"CVE-2024-1": ["array1"]}
        module.warn.assert_not_called()

    def test_insight_of_another_type_can_carry_cves(self, pure1_client):
        insights = [
            _insight(
                kind=ALERTS,
                cves=[("CVE-2024-1", "high"), ("CVE-2024-2", "none")],
            ),
            _received({"cves": [{"id": "CVE-2024-3"}]}, kind=PASSWORD),
        ]

        module, result = _read(pure1_client, insights)

        assert result["arrays"]["array1"]["cves"] == _cves(high=["CVE-2024-1"])
        assert result["cves"] == {"CVE-2024-1": ["array1"]}
        assert _warnings(module) == [UNREAD.format(2)]

    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param({"severity": "medium"}, id="no id"),
            pytest.param({"id": None, "severity": "medium"}, id="null id"),
            pytest.param({"id": "", "severity": "medium"}, id="empty id"),
            pytest.param({"id": 2024, "severity": "medium"}, id="number as id"),
            pytest.param({"id": ["CVE-2024-9"], "severity": "medium"}, id="list as id"),
            pytest.param(None, id="null entry"),
            pytest.param("CVE-2024-9", id="string entry"),
            pytest.param(["CVE-2024-9", "medium"], id="list entry"),
        ],
    )
    def test_entry_without_an_id_is_left_out(self, pure1_client, entry):
        data = {"cves": [entry, {"id": "CVE-2024-1", "severity": "medium"}]}

        module, result = _read(pure1_client, [_received(data)])

        assert result["arrays"]["array1"]["cves"] == _cves(medium=["CVE-2024-1"])
        assert result["cves"] == {"CVE-2024-1": ["array1"]}
        assert _warnings(module) == [UNREAD.format(1)]

    @pytest.mark.parametrize(
        "listed",
        [
            pytest.param({"id": "CVE-2024-1", "severity": "medium"}, id="dictionary"),
            pytest.param("CVE-2024-1", id="string"),
            pytest.param(0, id="zero"),
            pytest.param("", id="empty string"),
        ],
    )
    def test_cves_that_are_no_list_count_as_one_entry(self, pure1_client, listed):
        module, result = _read(pure1_client, [_received({"cves": listed})])

        assert result == {
            "arrays": {
                "array1": _entry("array1", [MEDIUM_CVE], fqdn=None),
            },
            "cves": {},
        }
        assert _warnings(module) == [UNREAD.format(1)]

    @pytest.mark.parametrize(
        "data",
        [
            pytest.param(None, id="no additional data"),
            pytest.param({}, id="empty"),
            pytest.param({"cves": None}, id="null list"),
            pytest.param({"cves": []}, id="empty list"),
            pytest.param({"other": [{"id": "CVE-2024-1"}]}, id="another key"),
            pytest.param([{"id": "CVE-2024-1"}], id="a list"),
            pytest.param("cves", id="a string"),
        ],
    )
    def test_insight_without_cves_has_none(self, pure1_client, data):
        module, result = _read(pure1_client, [_received(data)])

        assert result == {
            "arrays": {
                "array1": _entry("array1", [MEDIUM_CVE], fqdn=None),
            },
            "cves": {},
        }
        module.warn.assert_not_called()

    def test_ids_are_in_the_order_of_year_and_number_each_once(self, pure1_client):
        insights = [
            _insight(
                "array2",
                MEDIUM_CVE,
                cves=[
                    ("CVE-2024-10000", "medium"),
                    ("CVE-2024-9999", "medium"),
                    ("CVE-2024-10000", "medium"),
                    ("CVE-2023-51384", "medium"),
                    ("CVE-2024-123", "high"),
                ],
            ),
            _insight(
                "array1",
                HIGH_CVE,
                cves=[("CVE-2024-9999", "high"), ("CVE-2021-3", "high")],
            ),
            _insight("array2", HIGH_CVE, cves=[("CVE-2021-3", "high")]),
        ]

        module, result = _read(pure1_client, insights)

        assert result["arrays"]["array1"]["cves"] == _cves(
            high=["CVE-2021-3", "CVE-2024-9999"]
        )
        assert result["arrays"]["array2"]["cves"] == _cves(
            high=["CVE-2021-3", "CVE-2024-123"],
            medium=["CVE-2023-51384", "CVE-2024-9999", "CVE-2024-10000"],
        )
        assert list(result["cves"].items()) == [
            ("CVE-2021-3", ["array1", "array2"]),
            ("CVE-2023-51384", ["array2"]),
            ("CVE-2024-123", ["array2"]),
            ("CVE-2024-9999", ["array1", "array2"]),
            ("CVE-2024-10000", ["array2"]),
        ]
        module.warn.assert_not_called()

    def test_id_of_another_form_is_kept_as_it_is(self, pure1_client):
        cves = [("PURE-2", "low"), ("CVE-2024-1", "low"), ("10", "low"), ("9", "low")]

        result = _read(pure1_client, [_insight(kind="low_cve_detected", cves=cves)])[1]

        assert list(result["cves"]) == ["9", "10", "CVE-2024-1", "PURE-2"]
        assert result["arrays"]["array1"]["cves"]["low"] == [
            "9",
            "10",
            "CVE-2024-1",
            "PURE-2",
        ]

    def test_cves_of_the_arrays_given_alone(self, pure1_client):
        insights = [
            _insight("array1", HIGH_CVE, cves=[("CVE-2024-1", "high")]),
            _insight(
                "array2",
                HIGH_CVE,
                cves=[("CVE-2024-2", "high")],
                resource=_reference("array2", id="id-other"),
            ),
        ]

        module, result = _read(
            pure1_client,
            insights,
            [_record("array1"), _record("array2")],
            types=CVE_INSIGHT_TYPES.values(),
        )

        assert result == {
            "arrays": {
                "array1": _entry("array1", [HIGH_CVE], cves=_cves(high=["CVE-2024-1"]))
            },
            "cves": {"CVE-2024-1": ["array1"]},
        }
        module.warn.assert_not_called()


class TestSelection:
    """With arrays given, only their insights are in the result."""

    @pytest.mark.parametrize(
        "reference",
        [
            pytest.param(
                _reference("ARRAY1", id="id-array1"), id="name in another case"
            ),
            pytest.param(_reference("array1", id="id-other"), id="another id"),
            pytest.param(_reference("array3"), id="another array"),
        ],
    )
    def test_insight_of_an_array_that_was_not_given_is_not_kept(
        self, pure1_client, reference
    ):
        insights = [
            _insight(kind=ALERTS, resource=reference),
            _insight("array1", PASSWORD),
        ]

        module, result = _read(
            pure1_client, insights, [_record("array1"), _record("array2")]
        )

        assert result == {"arrays": {"array1": _entry("array1")}, "cves": {}}
        module.warn.assert_not_called()

    def test_insight_without_the_id_of_its_array_is_kept_by_the_name(
        self, pure1_client
    ):
        insight = _insight(
            resource=sdk_model(
                "FixedReferenceFqdn", name="array1", fqdn="a.example.com"
            )
        )

        module, result = _read(pure1_client, [insight], [_record("array1")])

        # the id is that of the record, the fqdn is the one the reference has
        assert result["arrays"] == {"array1": _entry("array1", fqdn="a.example.com")}
        module.warn.assert_not_called()

    def test_what_a_reference_lacks_is_taken_from_the_record(self, pure1_client):
        """The first reference to an array given need not have its id or its fqdn"""
        name_only = sdk_model("FixedReferenceFqdn", name="array1")
        insights = [
            _insight(kind=PASSWORD, resource=name_only),
            _insight(kind=PROTECTION, resource=_reference("array1", id="id-other")),
            _insight(kind=ALERTS, resource=_reference("array1", fqdn="a.example.com")),
        ]

        module, result = _read(pure1_client, insights, [_record("array1")])

        # the insight with the id of the record is of the same array
        assert result["arrays"] == {"array1": _entry("array1", [ALERTS, PASSWORD])}
        module.warn.assert_not_called()

    def test_record_with_the_id_of_the_reference_is_the_one_read(self, pure1_client):
        """Of two records of one name, of which the first has no id"""
        records = [
            _record("array1", id=None, fqdn="first.example.com"),
            _record("array1", id="id-other", fqdn="other.example.com"),
        ]
        bare = sdk_model("FixedReferenceFqdn", id="id-other", name="array1")

        module, result = _read(pure1_client, [_insight(resource=bare)], records)

        assert result["arrays"] == {
            "array1": _entry("array1", id="id-other", fqdn="other.example.com")
        }
        module.warn.assert_not_called()

    def test_array_given_without_an_id_keeps_the_insights_of_its_name(
        self, pure1_client
    ):
        module, result = _read(
            pure1_client, [_insight("array1")], [_record("array1", id=None)]
        )

        assert result["arrays"] == {"array1": _entry("array1")}
        module.warn.assert_not_called()

    def test_named_array_without_insights_is_no_key(self, pure1_client):
        records = [_record("array2", fqdn=None), _record("array1")]
        given = copy.deepcopy(records)

        module, result = _read(pure1_client, [_insight("array1")], records)

        assert result == {"arrays": {"array1": _entry("array1")}, "cves": {}}
        # the records of the caller are read, not changed
        assert records == given
        module.warn.assert_not_called()

    def test_keep_named_is_no_parameter(self, pure1_client):
        """No module used it: pure1_security adds the arrays it names itself."""
        with pytest.raises(TypeError, match="keep_named"):
            pure1_security_insights(
                ansible_module(), pure1_client, [_record("array1")], keep_named=True
            )

        getattr(pure1_client, METHOD).assert_not_called()


class TestSameName:
    """Two arrays of one name are never merged."""

    def test_array_seen_first_keeps_the_name(self, pure1_client):
        other = _reference("array1", id="id-other", fqdn="other.example.com")
        insights = [
            _insight("array1", PASSWORD),
            _insight("array1", ALERTS, resource=other),
            _insight("array1", PROTECTION),
            _insight("array1", HIGH_CVE, cves=[("CVE-2024-1", "high")], resource=other),
            _insight("array2", ALERTS),
        ]

        module, result = _read(pure1_client, insights)

        assert result == {
            "arrays": {
                "array1": _entry("array1", [PROTECTION, PASSWORD]),
                "array2": _entry("array2", [ALERTS]),
            },
            "cves": {},
        }
        assert _warnings(module) == [OTHER_ARRAY.format(2)]

    def test_two_arrays_given_under_one_name(self, pure1_client):
        other = _reference("array1", id="id-other", fqdn="other.example.com")
        insights = [
            _insight("array1", ALERTS, resource=other),
            _insight("array1", PASSWORD),
        ]
        records = [_record("array1"), _record("array1", id="id-other")]

        module, result = _read(pure1_client, insights, records)

        assert result == {
            "arrays": {
                "array1": _entry(
                    "array1", [ALERTS], id="id-other", fqdn="other.example.com"
                )
            },
            "cves": {},
        }
        assert _warnings(module) == [OTHER_ARRAY.format(1)]

    def test_reference_without_an_id_is_to_the_first_of_two_arrays_given(
        self, pure1_client
    ):
        records = [_record("array1"), _record("array1", id="id-other")]
        name_only = sdk_model("FixedReferenceFqdn", name="array1")

        module, result = _read(pure1_client, [_insight(resource=name_only)], records)

        assert result["arrays"] == {"array1": _entry("array1")}
        module.warn.assert_not_called()

    def test_insights_without_an_array_id_are_of_one_array(self, pure1_client):
        reference = sdk_model("FixedReferenceFqdn", name="array1")
        insights = [
            _insight(kind=PASSWORD, resource=reference),
            _insight(kind=ALERTS, resource=reference),
        ]

        module, result = _read(pure1_client, insights)

        assert result["arrays"] == {
            "array1": _entry("array1", [ALERTS, PASSWORD], id=None, fqdn=None)
        }
        module.warn.assert_not_called()

    @pytest.mark.parametrize("first", ["without an id", "with an id"])
    def test_reference_without_an_id_is_to_the_array_of_its_name(
        self, pure1_client, first
    ):
        """Whichever comes first, and the entry has the id and the fqdn of the other"""
        insights = [
            _insight(
                kind=PASSWORD, resource=sdk_model("FixedReferenceFqdn", name="array1")
            ),
            _insight(kind=ALERTS),
        ]
        if first == "with an id":
            insights.reverse()

        module, result = _read(pure1_client, insights)

        assert result["arrays"] == {"array1": _entry("array1", [ALERTS, PASSWORD])}
        module.warn.assert_not_called()

    def test_entry_made_without_an_id_takes_the_first_id_that_comes(self, pure1_client):
        """The insight of another array of the name is left out after that"""
        other = _reference("array1", id="id-other", fqdn="other.example.com")
        insights = [
            _insight(
                kind=PASSWORD, resource=sdk_model("FixedReferenceFqdn", name="array1")
            ),
            _insight(kind=ALERTS),
            _insight(kind=PROTECTION, resource=other),
        ]

        module, result = _read(pure1_client, insights)

        assert result["arrays"] == {"array1": _entry("array1", [ALERTS, PASSWORD])}
        assert _warnings(module) == [OTHER_ARRAY.format(1)]


def _no_type():
    return _insight(kind=None)


def _no_reference():
    return sdk_model("SecurityInsightArray", type=PASSWORD, as_of=AS_OF)


def _empty_reference():
    return _insight(resource=sdk_model("FixedReferenceFqdn"))


def _reference_without_a_name():
    return _insight(
        resource=sdk_model(
            "FixedReferenceFqdn", id="id-array1", fqdn="array1.example.com"
        )
    )


def _nothing():
    return None


class TestLeftOut:
    """What cannot be filed is counted, never guessed."""

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(_no_type, id="no type"),
            pytest.param(_no_reference, id="no array reference"),
            pytest.param(_empty_reference, id="empty array reference"),
            pytest.param(_reference_without_a_name, id="array without name"),
            pytest.param(_nothing, id="null item"),
        ],
    )
    def test_insight_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        module, result = _read(pure1_client, [broken(), _insight("array1")])

        assert result == {"arrays": {"array1": _entry("array1")}, "cves": {}}
        assert _warnings(module) == [UNFILED.format(1)]

    def test_insights_left_out_give_one_warning(self, pure1_client):
        module, result = _read(
            pure1_client,
            [_no_type(), _insight("array1"), _no_reference(), _empty_reference()],
            [_record("array1")],
        )

        assert result == {"arrays": {"array1": _entry("array1")}, "cves": {}}
        assert _warnings(module) == [UNFILED.format(3)]

    def test_every_warning_is_given_once_in_a_fixed_order(self, pure1_client):
        other = _reference("array1", id="id-other")
        insights = [
            _insight("array1", MEDIUM_CVE, cves=[(None, "medium"), ("", "medium")]),
            _no_type(),
            _insight("array1", ALERTS, resource=other),
            _insight("array1", PASSWORD, resource=other),
            _received({"cves": {}}, kind=HIGH_CVE),
            _no_reference(),
        ]
        module = ansible_module()
        _serve(pure1_client, insights, total_item_count=9)

        result = pure1_security_insights(module, pure1_client)

        assert result == {
            "arrays": {"array1": _entry("array1", [HIGH_CVE, MEDIUM_CVE])},
            "cves": {},
        }
        assert _warnings(module) == [
            "Pure1 returned 6 of 9 security insights: the result may be incomplete.",
            UNFILED.format(2),
            OTHER_ARRAY.format(2),
            UNREAD.format(3),
        ]
