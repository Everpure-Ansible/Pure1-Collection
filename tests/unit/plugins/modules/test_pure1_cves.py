# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_cves module.

The typical CVE and its insight have the shape of a reading of a real
organisation, under invented names: an array on Purity//FA 6.10.6 that a
medium_cve_detected insight reports two CVEs for, each with releases that fix
it. What that reading cannot show is built by hand in the tests.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
import struct

import jinja2
import pypureclient
import pytest
import yaml
from ansible.plugins.filter.core import FilterModule
from jinja2.nativetypes import NativeEnvironment
from pypureclient import ErrorResponse, PureError

from plugins.module_utils.pure1 import pure1_argument_spec, pure1_time
from plugins.modules import pure1_cves
from tests.unit.conftest import (
    MODELS,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

METHOD = "get_assessment_security_cves"
INSIGHTS = "get_assessment_security_insights_arrays"
# Every time differs in month, day, hour, minute and second
DETECTED = 1612357445000
UPDATED = 1612447506000
FA = "Purity//FA"
FB = "Purity//FB"
KB = "https://support.example.com/cve/{0}.html"
CVE1 = "CVE-2023-52001"
CVE2 = "CVE-2023-24001"
# The filter of the one request for the insights that tell of CVEs
CVE_TYPES = (
    "type=('critical_cve_detected','high_cve_detected','medium_cve_detected',"
    "'low_cve_detected')"
)
NO_RELEASES = {FA: [], FB: []}
EMPTY = {"total": 0, "returned": 0, "truncated": False, "cves": {}}
NO_ID = "CVEs that Pure1 returned without an id are left out of the result: {0}."
SAME_ID = (
    "Pure1 returned {0} CVEs under the id of another CVE: of each such id only "
    "the CVE returned first is in the result."
)
NO_RECORD = (
    "CVEs that the security insights report and Pure1 has no record of are left "
    "out of the result: {0}."
)
NO_SUCH_RECORD = (
    "CVEs that the security insights report with a severity that is asked for and "
    "Pure1 has no record of with such a severity are left out of the result: {0}."
)
UNFILED = (
    "Releases and patches of CVEs that Pure1 returned without an operating "
    "system or without a name are left out of the result: {0}."
)
UNNAMED = (
    "Affected arrays that Pure1 returned without a name are left out of the "
    "result: {0}."
)
SHORT = "Pure1 returned {0} of {1} CVEs: the result may be incomplete."


def _version(os_name, version):
    return sdk_model("CveVersion", os=os_name, version=version)


def _patch(os_name, name):
    return sdk_model("CvePatch", os=os_name, name=name)


def _resource(name="array1", **overrides):
    """Return an array as the record of a CVE lists it."""
    fields = dict(
        id="id-" + name,
        name=name,
        resource_type="arrays",
        fqdn=name + ".example.com",
        os=FA,
        version="6.10.6",
    )
    fields.update(overrides)
    return sdk_model("CveResource", **fields)


def _cve(cve_id=CVE1, **overrides):
    """Return a CVE that affects array1; Pure1 lists the releases in no order."""
    fields = dict(
        as_of=1795575391254,
        id=cve_id,
        severity="medium",
        cvss_score=4.5,
        description="A use-after-free in a library.",
        kb_article=KB.format(cve_id),
        detected_at=DETECTED,
        updated_at=UPDATED,
        affected_resources=[_resource()],
        affected_versions=[
            _version(FA, "6.10.6"),
            _version(FB, "4.7.9"),
            _version(FA, "6.9.7"),
        ],
        fixed_in_patches=[],
        fixed_in_versions=[
            _version(FA, "6.7.9"),
            _version(FA, "6.9.8"),
            _version(FA, "6.10.7"),
            _version(FA, "6.12.0"),
            _version(FA, "6.5.14"),
        ],
    )
    fields.update(overrides)
    return sdk_model("Cve", **fields)


def _bare(cve_id, **fields):
    """Return a CVE with nothing but its id, and what a test adds."""
    return sdk_model("Cve", id=cve_id, **fields)


def _array_row(name="array1", **overrides):
    """The result of the array that _resource(name) builds, with the fixes of _cve()"""
    row = {
        "name": name,
        "id": "id-" + name,
        "fqdn": name + ".example.com",
        "os": FA,
        "version": "6.10.6",
        "fixed_in_later_versions": ["6.10.7", "6.12.0"],
        "in_insights": True,
    }
    row.update(overrides)
    return row


def _insight_only(name="array1", **overrides):
    """The result of an array that only an insight reports a CVE for"""
    return _array_row(
        name, os=None, version=None, fixed_in_later_versions=[], **overrides
    )


def _row(cve_id=CVE1, **overrides):
    """The result of the CVE that _cve(cve_id) builds, reported by an insight"""
    row = {
        "severity": "medium",
        "cvss_score": 4.5,
        "description": "A use-after-free in a library.",
        "kb_article": KB.format(cve_id),
        "detected": "2021-02-03 13:04:05 UTC",
        "detected_epoch": 1612357445000,
        "updated": "2021-02-04 14:05:06 UTC",
        "updated_epoch": 1612447506000,
        "fixed_in_versions": {
            FA: ["6.5.14", "6.7.9", "6.9.8", "6.10.7", "6.12.0"],
            FB: [],
        },
        "fixed_in_patches": {FA: [], FB: []},
        "affected_arrays": [_array_row()],
    }
    row.update(overrides)
    return row


AFFECTED_VERSIONS = {FA: ["6.9.7", "6.10.6"], FB: ["4.7.9"]}
# The result of a CVE with nothing but its id that affects no array
BARE = {
    "severity": None,
    "cvss_score": None,
    "description": None,
    "kb_article": None,
    "detected": None,
    "detected_epoch": None,
    "updated": None,
    "updated_epoch": None,
    "fixed_in_versions": NO_RELEASES,
    "fixed_in_patches": NO_RELEASES,
    "affected_arrays": [],
}


def _reference(name, **overrides):
    """Return the reference an insight has to its array."""
    fields = dict(
        id="id-" + name, name=name, resource_type="arrays", fqdn=name + ".example.com"
    )
    fields.update(overrides)
    return sdk_model("FixedReferenceFqdn", **fields)


def _insight(array="array1", cves=(CVE1,), severity="medium", **overrides):
    """Return the insight that reports CVEs of one severity for an array."""
    fields = dict(
        type=severity + "_cve_detected",
        as_of=DETECTED,
        resource=_reference(array),
        additional_data={
            "cves": [{"id": cve_id, "severity": severity} for cve_id in cves]
        },
    )
    fields.update(overrides)
    return sdk_model("SecurityInsightArray", **fields)


def _received(cve):
    """Return a CVE as the SDK builds it from an answer, which it does not check."""
    return MODELS.Cve.from_dict(cve)


def _catalogue(cves, page_size=1000, **paging):
    """Return a side_effect that answers the CVE listing as Pure1 does

    A request gets the CVEs whose ids its filter names, every CVE when it
    names none, in the order of their ids and in pages. No other term of the
    filter selects: the tests pin the terms. The keywords of every request
    are kept in the attribute requests.
    """
    cves = list(cves)

    def answer(**kwargs):
        answer.requests.append(dict(kwargs))
        wanted = None
        if "id=" in (kwargs.get("filter") or ""):
            wanted = re.findall(r"'(CVE-[^']*)'", kwargs["filter"])
        selected = [
            cve for cve in cves if wanted is None or getattr(cve, "id", None) in wanted
        ]
        selected.sort(key=lambda cve: getattr(cve, "id", ""))
        return paged_answers("CveGetResponse", selected, page_size, **paging)(**kwargs)

    answer.requests = []
    return answer


def _single(number):
    """Return a number as it is in single precision"""
    return struct.unpack("f", struct.pack("f", number))[0]


def _scored(cves):
    """Return a side_effect that selects by the score of a filter as Pure1 does

    Measured on Pure1: the number of a cvss_score>= term counts as it is in
    single precision, and the score as it is. So cvss_score>=9.80 leaves out
    a score of 9.8, as 9.75 or 9.799 do not. A CVE without a score meets no
    number. The keywords of every request are kept in the attribute
    requests.
    """
    cves = list(cves)

    def answer(**kwargs):
        answer.requests.append(dict(kwargs))
        (number,) = re.findall(r"cvss_score>=([0-9.]+)", kwargs["filter"])
        lowest = _single(float(number))
        selected = [
            cve
            for cve in cves
            if getattr(cve, "cvss_score", None) is not None and cve.cvss_score >= lowest
        ]
        selected.sort(key=lambda cve: cve.id)
        return paged_answers("CveGetResponse", selected)(**kwargs)

    answer.requests = []
    return answer


def _serve(client, cves=(), insights=(), **paging):
    """Answer the two listings, and return what the CVE listing is asked"""
    answers = _catalogue(cves, **paging)
    getattr(client, METHOD).side_effect = answers
    getattr(client, INSIGHTS).return_value = valid_response(insights)
    return answers


def _known_arrays(client, *names, **fields):
    """Answer the request that resolves the arrays of a task, after the check
    of get_pure1"""
    arrays = [
        sdk_model("Array", id="id-" + name, name=name, **fields) for name in names
    ]
    client.get_arrays.side_effect = [valid_response(), valid_response(arrays)]


def _result(module):
    return module.exit_json.call_args[1]["pure1_cves"]


def _warnings(module):
    return [call[0][0] for call in module.warn.call_args_list]


def _lookup(*ids):
    """The request that looks CVEs up by their ids"""
    if len(ids) == 1:
        text = "id='{0}'".format(ids[0])
    else:
        text = "id=({0})".format(",".join("'{0}'".format(cve_id) for cve_id in ids))
    return {"filter": text, "sort": ["id"]}


class TestAffectedCves:
    """The default: the CVEs that the security insights report."""

    def test_cves_of_the_insights_are_returned(self, pure1_client):
        answers = _serve(
            pure1_client,
            cves=[_cve(), _cve(CVE2, cvss_score=6.5)],
            insights=[_insight(cves=[CVE2, CVE1])],
        )

        module = run_main(pure1_cves)

        # the check of get_pure1 and nothing else: no array is resolved
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        assert answers.requests == [_lookup(CVE2, CVE1)]
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={
                "total": 2,
                "returned": 2,
                "truncated": False,
                "cves": {CVE2: _row(CVE2, cvss_score=6.5), CVE1: _row()},
            },
        )
        # the higher score comes first
        assert list(_result(module)["cves"]) == [CVE2, CVE1]

    def test_check_mode_does_the_same(self, pure1_client):
        answers = _serve(pure1_client, cves=[_cve()], insights=[_insight()])

        module = run_main(pure1_cves, check_mode=True)

        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        assert answers.requests == [_lookup(CVE1)]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={
                "total": 1,
                "returned": 1,
                "truncated": False,
                "cves": {CVE1: _row()},
            },
        )

    def test_no_insight_means_no_request_for_cves(self, pure1_client):
        answers = _serve(pure1_client, cves=[_cve()])

        module = run_main(pure1_cves)

        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        assert answers.requests == []
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_cves=EMPTY)

    def test_arrays_are_resolved_first_and_named_as_pure1_spells_them(
        self, pure1_client
    ):
        _known_arrays(pure1_client, "array1", "Array2")
        answers = _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource(), _resource("array3")])],
            insights=[_insight(), _insight("array3")],
        )

        module = run_main(pure1_cves, array=["ARRAY1", "array2"])

        assert [call[1] for call in pure1_client.get_arrays.call_args_list] == [
            {"limit": 0},
            {"filter": "name=('ARRAY1','array2')"},
        ]
        getattr(pure1_client, INSIGHTS).assert_called_once_with(
            filter="resource.name=('array1','Array2') and " + CVE_TYPES
        )
        assert answers.requests == [_lookup(CVE1)]
        # array3 is no array of the task, in the insights or in the record
        assert _result(module)["cves"] == {CVE1: _row()}

    def test_array_of_the_same_name_with_another_id_is_not_listed(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        _serve(
            pure1_client,
            cves=[
                _cve(
                    affected_resources=[
                        _resource(id="id-other", version="6.5.0"),
                        _resource(),
                        _resource(id=None, version="6.9.8"),
                    ]
                )
            ],
            insights=[_insight()],
        )

        module = run_main(pure1_cves, array=["array1"])

        # an array without an id is told by its name alone
        assert _result(module)["cves"][CVE1]["affected_arrays"] == [
            _array_row(
                id=None, version="6.9.8", fixed_in_later_versions=["6.10.7", "6.12.0"]
            ),
            _array_row(),
        ]

    def test_array_of_another_name_without_an_id_is_not_listed(self, pure1_client):
        _known_arrays(pure1_client, "array1")
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource("array3", id=None), _resource()])],
            insights=[_insight()],
        )

        module = run_main(pure1_cves, array=["array1"])

        # no id tells array3 from array1: its name does
        assert _result(module)["cves"][CVE1]["affected_arrays"] == [_array_row()]

    def test_unknown_array_fails_before_the_insights_are_read(self, pure1_client):
        pure1_client.get_arrays.side_effect = [valid_response(), valid_response()]
        answers = _serve(pure1_client, cves=[_cve()], insights=[_insight()])

        module = run_main(pure1_cves, array=["array9"])

        module.fail_json.assert_called_once_with(
            msg="Array array9 is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        getattr(pure1_client, INSIGHTS).assert_not_called()
        assert answers.requests == []

    def test_named_array_without_a_cve_gives_an_empty_result(self, pure1_client):
        _known_arrays(pure1_client, "array2")
        answers = _serve(pure1_client, cves=[_cve()])

        module = run_main(pure1_cves, array=["array2"])

        getattr(pure1_client, INSIGHTS).assert_called_once_with(
            filter="resource.name='array2' and " + CVE_TYPES
        )
        assert answers.requests == []
        module.exit_json.assert_called_once_with(changed=False, pure1_cves=EMPTY)

    @pytest.mark.parametrize(
        "names, requests, found",
        [
            pytest.param(["cve-2023-52001"], [_lookup(CVE1)], [CVE1], id="lower case"),
            pytest.param(
                [CVE2, "CVE-2021-44228", CVE2],
                [_lookup(CVE2)],
                [CVE2],
                id="one that no insight reports, one twice",
            ),
            pytest.param(["CVE-2021-44228"], [], [], id="none that an insight reports"),
            pytest.param([], [_lookup(CVE2, CVE1)], [CVE2, CVE1], id="empty list"),
        ],
    )
    def test_name_selects_among_the_cves_of_the_insights(
        self, pure1_client, names, requests, found
    ):
        answers = _serve(
            pure1_client,
            cves=[_cve(), _cve(CVE2, cvss_score=6.5), _cve("CVE-2021-44228")],
            insights=[_insight(cves=[CVE1, CVE2])],
        )

        module = run_main(pure1_cves, name=names)

        assert answers.requests == requests
        assert list(_result(module)["cves"]) == found
        assert _result(module)["total"] == len(found)
        module.warn.assert_not_called()

    def test_name_selects_a_cve_of_the_insights_whatever_its_case(self, pure1_client):
        """The id is asked for as the insight spells it, and Pure1 compares
        the ids of its records exactly: it has none of that spelling"""
        answers = _serve(
            pure1_client, cves=[_cve()], insights=[_insight(cves=[CVE1.lower()])]
        )

        module = run_main(pure1_cves, name=[CVE1])

        assert answers.requests == [_lookup(CVE1.lower())]
        assert _warnings(module) == [NO_RECORD.format(1)]
        module.exit_json.assert_called_once_with(changed=False, pure1_cves=EMPTY)

    @pytest.mark.parametrize(
        "params, terms",
        [
            pytest.param({"severity": ["high"]}, "severity='high'", id="one severity"),
            pytest.param(
                {"severity": ["low", "critical", "low"]},
                "severity=('low','critical')",
                id="severities",
            ),
            pytest.param({"severity": []}, None, id="no severity"),
            pytest.param({"min_cvss": 7.5}, "cvss_score>=7.45", id="score"),
            pytest.param({"min_cvss": 0}, "cvss_score>=0.00", id="score of nought"),
            pytest.param({"min_cvss": 10}, "cvss_score>=9.95", id="highest score"),
            pytest.param(
                {"min_cvss": 9.8},
                "cvss_score>=9.75",
                id="score that Pure1 reads above itself",
            ),
            pytest.param(
                {"min_cvss": 9.81}, "cvss_score>=9.85", id="score with two decimals"
            ),
            pytest.param({"window": 86400}, "detected_at>=1612271045678", id="window"),
            pytest.param({"window": 1}, "detected_at>=1612357444678", id="one second"),
            pytest.param(
                {"window": 1612357446},
                "detected_at>=0",
                id="window that reaches back before 1970",
            ),
            pytest.param(
                {"window": 99999999999}, "detected_at>=0", id="window for ever"
            ),
            pytest.param(
                {"window": 3600, "min_cvss": 9.75, "severity": ["critical", "high"]},
                "severity=('critical','high') and cvss_score>=9.75 and "
                "detected_at>=1612353845678",
                id="all three",
            ),
        ],
    )
    def test_options_become_terms_of_the_filter(
        self, pure1_client, monkeypatch, params, terms
    ):
        monkeypatch.setattr(pure1_cves.time, "time", lambda: 1612357445.678)
        answers = _serve(pure1_client, cves=[_cve()], insights=[_insight()])

        module = run_main(pure1_cves, **params)

        # the insights are read whole: the severity is that of the record
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        expected = "id='{0}'".format(CVE1)
        if terms:
            expected += " and " + terms
        assert answers.requests == [{"filter": expected, "sort": ["id"]}]
        assert _result(module)["cves"] == {CVE1: _row()}

    def test_cves_are_looked_up_a_hundred_at_a_time(self, pure1_client):
        ids = ["CVE-2024-{0}".format(number) for number in range(1000, 1201)]
        answers = _serve(
            pure1_client,
            cves=[_bare(cve_id) for cve_id in ids],
            insights=[_insight(cves=ids)],
        )

        module = run_main(pure1_cves, severity=["medium"])

        assert pure1_cves.IDS_PER_REQUEST == 100
        assert answers.requests == [
            {
                "filter": _lookup(*chunk)["filter"] + " and severity='medium'",
                "sort": ["id"],
            }
            for chunk in (ids[:100], ids[100:200], ids[200:])
        ]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            201,
            201,
            False,
        )
        assert list(result["cves"]) == ids
        module.warn.assert_not_called()

    def test_cve_that_only_an_insight_reports_for_an_array(self, pure1_client):
        """The record of the CVE lists another array, or none"""
        _serve(
            pure1_client,
            cves=[
                _cve(affected_resources=[_resource("array2", version="6.12.0")]),
                _bare(CVE2),
            ],
            insights=[
                _insight(cves=[CVE1, CVE2]),
                _insight("array0", resource=_reference("array0", fqdn=None)),
            ],
        )

        module = run_main(pure1_cves)

        cves = _result(module)["cves"]
        assert cves[CVE1]["affected_arrays"] == [
            _insight_only("array0", fqdn=None),
            _insight_only("array1"),
            _array_row(
                "array2",
                version="6.12.0",
                fixed_in_later_versions=[],
                in_insights=False,
            ),
        ]
        assert cves[CVE2] == dict(BARE, affected_arrays=[_insight_only("array1")])
        module.warn.assert_not_called()

    def test_array_of_an_insight_is_told_by_its_id_as_well(self, pure1_client):
        """Two arrays can have one name: the record lists one, the insight the other"""
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource(id="id-other")])],
            insights=[_insight()],
        )

        module = run_main(pure1_cves)

        assert _result(module)["cves"][CVE1]["affected_arrays"] == [
            _insight_only("array1"),
            _array_row(id="id-other", in_insights=False),
        ]

    @pytest.mark.parametrize(
        "record_id, insight_id",
        [
            pytest.param(None, "id-array1", id="record without id"),
            pytest.param("id-array1", None, id="insight without id"),
            pytest.param(None, None, id="neither has an id"),
        ],
    )
    def test_array_without_an_id_is_told_by_its_name(
        self, pure1_client, record_id, insight_id
    ):
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource(id=record_id)])],
            insights=[_insight(resource=_reference("array1", id=insight_id))],
        )

        module = run_main(pure1_cves)

        assert _result(module)["cves"][CVE1]["affected_arrays"] == [
            _array_row(id=record_id)
        ]

    def test_array_that_pure1_lists_twice_is_one_array(self, pure1_client):
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource(), _resource(version="6.5.0")])],
            insights=[_insight()],
        )

        module = run_main(pure1_cves)

        assert _result(module)["cves"][CVE1]["affected_arrays"] == [_array_row()]

    @pytest.mark.parametrize(
        "fields, later",
        [
            pytest.param({}, ["6.10.7", "6.12.0"], id="releases above"),
            pytest.param({"version": "6.12.0"}, [], id="the highest fix itself"),
            pytest.param({"version": "6.12.0.1"}, [], id="above every fix"),
            pytest.param(
                {"version": "6.5.13"},
                ["6.5.14", "6.7.9", "6.9.8", "6.10.7", "6.12.0"],
                id="below every fix",
            ),
            pytest.param({"version": "6.9.8"}, ["6.10.7", "6.12.0"], id="a fix itself"),
            pytest.param({"version": None}, [], id="no release"),
            pytest.param({"os": None}, [], id="no operating system"),
            pytest.param(
                {"os": FB, "version": "4.1.0"}, [], id="no fix for its system"
            ),
            pytest.param(
                {"os": "Purity//X", "version": "1.0.0"}, [], id="another system"
            ),
        ],
    )
    def test_fix_releases_above_the_release_of_an_array(
        self, pure1_client, fields, later
    ):
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource(**fields)])],
            insights=[_insight()],
        )

        module = run_main(pure1_cves)

        assert _result(module)["cves"][CVE1]["affected_arrays"] == [
            _array_row(fixed_in_later_versions=later, **fields)
        ]

    def test_fix_releases_of_the_system_of_each_array(self, pure1_client):
        fixes = [_version(FB, "4.7.10"), _version(FA, "6.10.7"), _version(FB, "4.8.0")]
        _serve(
            pure1_client,
            cves=[
                _cve(
                    fixed_in_versions=fixes,
                    affected_resources=[
                        _resource("array2", os=FB, version="4.7.9"),
                        _resource(),
                    ],
                )
            ],
            insights=[_insight(), _insight("array2")],
        )

        module = run_main(pure1_cves)

        row = _result(module)["cves"][CVE1]
        assert row["fixed_in_versions"] == {FA: ["6.10.7"], FB: ["4.7.10", "4.8.0"]}
        assert row["affected_arrays"] == [
            _array_row(fixed_in_later_versions=["6.10.7"]),
            _array_row(
                "array2",
                os=FB,
                version="4.7.9",
                fixed_in_later_versions=["4.7.10", "4.8.0"],
            ),
        ]

    def test_cve_without_a_record_gives_a_warning(self, pure1_client):
        answers = _serve(
            pure1_client,
            cves=[_cve()],
            insights=[_insight(cves=[CVE1, CVE2, "CVE-2021-44228"])],
        )

        module = run_main(pure1_cves)

        assert answers.requests == [_lookup("CVE-2021-44228", CVE2, CVE1)]
        assert _warnings(module) == [NO_RECORD.format(2)]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            1,
            1,
            False,
        )
        assert result["cves"] == {CVE1: _row()}

    @pytest.mark.parametrize(
        "params",
        [
            # the insight reports the CVEs as medium
            pytest.param({"severity": ["high"]}, id="severity"),
            pytest.param({"min_cvss": 0}, id="score"),
            pytest.param({"window": 86400}, id="window"),
            pytest.param({"limit": 0}, id="the number alone"),
            pytest.param(
                {"severity": ["medium"], "min_cvss": 0}, id="severity and score"
            ),
            pytest.param(
                {"severity": ["medium"], "window": 86400}, id="severity and window"
            ),
            pytest.param(
                {"severity": ["medium"], "limit": 0},
                id="severity and the number alone",
            ),
        ],
    )
    def test_cve_that_an_option_may_have_left_out_gives_no_warning(
        self, pure1_client, params
    ):
        _serve(pure1_client, cves=[_cve()], insights=[_insight(cves=[CVE1, CVE2])])

        module = run_main(pure1_cves, **params)

        module.warn.assert_not_called()
        assert _result(module)["total"] == 1

    @pytest.mark.parametrize(
        "severities, missing",
        [
            pytest.param(["medium"], 1, id="the severity of one insight"),
            pytest.param(["low", "medium"], 1, id="one of the severities"),
            pytest.param(["critical"], 2, id="the severity of the other insight"),
            # a CVE that two insights report counts once
            pytest.param(["medium", "critical"], 2, id="both"),
        ],
    )
    def test_cve_of_a_severity_asked_for_without_such_a_record_gives_a_warning(
        self, pure1_client, severities, missing
    ):
        """An insight reports the CVE with a severity of the task, and Pure1
        has no record of it or a record of another severity"""
        log4shell = "CVE-2021-44228"
        answers = _serve(
            pure1_client,
            cves=[_cve()],
            insights=[
                _insight(cves=[CVE1, CVE2]),
                _insight("array2", cves=[CVE2, log4shell], severity="critical"),
            ],
        )

        module = run_main(pure1_cves, severity=severities)

        assert answers.requests == [
            {
                "filter": _lookup(log4shell, CVE2, CVE1)["filter"]
                + " and "
                + _lookup(*severities)["filter"].replace("id=", "severity="),
                "sort": ["id"],
            }
        ]
        assert _warnings(module) == [NO_SUCH_RECORD.format(missing)]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            1,
            1,
            False,
        )
        assert list(result["cves"]) == [CVE1]

    def test_cve_of_a_severity_asked_for_that_has_its_record_gives_no_warning(
        self, pure1_client
    ):
        _serve(
            pure1_client,
            cves=[_cve(), _cve(CVE2)],
            insights=[_insight(cves=[CVE1, CVE2]), _insight("array2", cves=[CVE2])],
        )

        module = run_main(pure1_cves, severity=["medium"])

        module.warn.assert_not_called()
        assert list(_result(module)["cves"]) == [CVE2, CVE1]

    def test_warnings_of_the_insights_reach_the_task(self, pure1_client):
        _serve(
            pure1_client,
            cves=[_cve()],
            insights=[_insight(), sdk_model("SecurityInsightArray", as_of=DETECTED)],
        )

        module = run_main(pure1_cves)

        assert _warnings(module) == [
            "Security insights that Pure1 returned without a type or without the "
            "name of their array are left out of the result: 1."
        ]
        assert _result(module)["cves"] == {CVE1: _row()}


class TestCatalogue:
    """affected_only false: the CVEs of the catalogue."""

    def test_cves_of_the_catalogue_are_returned(self, pure1_client):
        answers = _serve(
            pure1_client,
            cves=[
                _cve(),
                _cve(
                    "CVE-2021-44228",
                    severity="critical",
                    cvss_score=10.0,
                    affected_resources=[],
                ),
            ],
            insights=[_insight()],
        )

        module = run_main(
            pure1_cves, affected_only=False, severity=["critical", "medium"]
        )

        pure1_client.get_arrays.assert_called_once_with(limit=0)
        assert answers.requests == [
            {"filter": "severity=('critical','medium')", "sort": ["id"]}
        ]
        # read after the CVEs, for the arrays that the insights report
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={
                "total": 2,
                "returned": 2,
                "truncated": False,
                "cves": {
                    "CVE-2021-44228": _row(
                        "CVE-2021-44228",
                        severity="critical",
                        cvss_score=10.0,
                        affected_arrays=[],
                    ),
                    CVE1: _row(),
                },
            },
        )
        assert list(_result(module)["cves"]) == ["CVE-2021-44228", CVE1]

    def test_check_mode_does_the_same(self, pure1_client):
        answers = _serve(pure1_client, cves=[_cve()], insights=[_insight()])

        module = run_main(pure1_cves, check_mode=True, affected_only=False, name=[CVE1])

        assert answers.requests == [_lookup(CVE1)]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={
                "total": 1,
                "returned": 1,
                "truncated": False,
                "cves": {CVE1: _row()},
            },
        )

    def test_arrays_come_from_the_record_and_from_the_insights(self, pure1_client):
        """As for the CVEs of the insights: the keys mean the same"""
        _serve(
            pure1_client,
            cves=[_cve(affected_resources=[_resource("array2")]), _bare(CVE2)],
            insights=[_insight(cves=[CVE1])],
        )

        module = run_main(pure1_cves, affected_only=False, limit=5)

        cves = _result(module)["cves"]
        assert cves[CVE1]["affected_arrays"] == [
            _insight_only("array1"),
            _array_row("array2", in_insights=False),
        ]
        assert cves[CVE2] == BARE

    def test_nothing_selected_means_no_request_for_insights(self, pure1_client):
        answers = _serve(pure1_client, insights=[_insight()])

        module = run_main(pure1_cves, affected_only=False, min_cvss=10)

        assert answers.requests == [{"filter": "cvss_score>=9.95", "sort": ["id"]}]
        getattr(pure1_client, INSIGHTS).assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_cves=EMPTY)

    @pytest.mark.parametrize(
        "params, cve_filter",
        [
            pytest.param({"limit": 5}, None, id="limit alone"),
            pytest.param({"severity": ["none"]}, "severity='none'", id="not rated"),
            pytest.param({"min_cvss": 0}, "cvss_score>=0.00", id="score of nought"),
            pytest.param(
                {"min_cvss": 9.8},
                "cvss_score>=9.75",
                id="score that Pure1 reads above itself",
            ),
            pytest.param({"window": 604800}, "detected_at>=1611752645678", id="window"),
            pytest.param(
                {"window": 99999999999}, "detected_at>=0", id="window for ever"
            ),
            pytest.param(
                {"name": ["cve-2021-44228", " CVE-2021-44228", CVE1]},
                "id=('CVE-2021-44228','CVE-2023-52001')",
                id="names",
            ),
            pytest.param(
                {"name": [CVE1], "severity": ["high"], "min_cvss": 7, "window": 60},
                "id='CVE-2023-52001' and severity='high' and cvss_score>=6.95 "
                "and detected_at>=1612357385678",
                id="every option",
            ),
        ],
    )
    def test_options_become_the_filter(
        self, pure1_client, monkeypatch, params, cve_filter
    ):
        monkeypatch.setattr(pure1_cves.time, "time", lambda: 1612357445.678)
        answers = _serve(pure1_client, cves=[_cve()], insights=[_insight()])

        module = run_main(pure1_cves, affected_only=False, **params)

        assert answers.requests == [{"filter": cve_filter, "sort": ["id"]}]
        assert _result(module)["cves"] == {CVE1: _row()}

    def test_names_are_looked_up_a_hundred_at_a_time(self, pure1_client):
        ids = ["CVE-2024-{0}".format(number) for number in range(1000, 1250)]
        answers = _serve(pure1_client, cves=[_bare(cve_id) for cve_id in ids[:240]])

        module = run_main(pure1_cves, affected_only=False, name=ids)

        assert answers.requests == [
            _lookup(*chunk) for chunk in (ids[:100], ids[100:200], ids[200:])
        ]
        # read once, after the last of the CVEs
        getattr(pure1_client, INSIGHTS).assert_called_once_with(filter=CVE_TYPES)
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            240,
            240,
            False,
        )
        # a name that Pure1 has no record of is no error and no warning
        assert list(result["cves"]) == ids[:240]
        module.warn.assert_not_called()

    def test_name_given_twice_is_asked_for_once(self, pure1_client, monkeypatch):
        monkeypatch.setattr(pure1_cves, "IDS_PER_REQUEST", 2)
        answers = _serve(pure1_client, cves=[_cve(), _cve(CVE2)])

        module = run_main(
            pure1_cves, affected_only=False, name=[CVE1, "cve-2023-52001", CVE2]
        )

        assert answers.requests == [_lookup(CVE1, CVE2)]
        assert _result(module)["total"] == 2

    def test_number_of_named_cves_is_the_sum_over_the_requests(
        self, pure1_client, monkeypatch
    ):
        monkeypatch.setattr(pure1_cves, "IDS_PER_REQUEST", 1)
        answers = _serve(pure1_client, cves=[_cve(), _cve(CVE2)])

        module = run_main(
            pure1_cves,
            affected_only=False,
            name=[CVE1, CVE2, "CVE-2021-44228"],
            limit=0,
        )

        # pure1_list() asks once more where Pure1 counts nothing
        assert answers.requests == [
            dict(_lookup(CVE1), limit=0),
            dict(_lookup(CVE2), limit=0),
            dict(_lookup("CVE-2021-44228"), limit=0),
            dict(_lookup("CVE-2021-44228"), limit=1),
        ]
        getattr(pure1_client, INSIGHTS).assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={"total": 2, "returned": 0, "truncated": True, "cves": {}},
        )

    def test_every_page_of_the_listing_is_read_by_offset(self, pure1_client):
        ids = ["CVE-2024-100{0}".format(number) for number in range(5)]
        answers = _serve(
            pure1_client, cves=[_bare(cve_id) for cve_id in ids], page_size=2
        )

        module = run_main(pure1_cves, affected_only=False, severity=["none"])

        assert answers.requests == [
            {"filter": "severity='none'", "sort": ["id"]},
            {"filter": "severity='none'", "sort": ["id"], "offset": 2},
            {"filter": "severity='none'", "sort": ["id"], "offset": 4},
        ]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            5,
            5,
            False,
        )
        assert list(result["cves"]) == ids
        module.warn.assert_not_called()


class TestOrderAndLimit:
    """The order of the CVEs, limit and the three numbers of the result."""

    CVES = (
        ("CVE-2024-0002", "critical", 9.8),
        ("CVE-2019-1111", "none", 0),
        ("CVE-2016-1111", None, 9.0),
        ("CVE-2023-10000", "critical", None),
        ("CVE-2020-1111", "high", 10.0),
        ("CVE-2021-1111", "low", 3.1),
        ("CVE-2024-0001", "critical", 9.8),
        ("CVE-2017-1111", "unknown", 10.0),
        ("CVE-2023-9999", "critical", None),
        ("CVE-2018-1111", "none", None),
        ("CVE-2022-1111", "medium", 5),
        ("CVE-2024-0003", "critical", 9.9),
    )
    ORDER = [
        "CVE-2024-0003",
        "CVE-2024-0001",
        "CVE-2024-0002",
        "CVE-2023-9999",
        "CVE-2023-10000",
        "CVE-2020-1111",
        "CVE-2022-1111",
        "CVE-2021-1111",
        "CVE-2019-1111",
        "CVE-2018-1111",
        "CVE-2017-1111",
        "CVE-2016-1111",
    ]

    def _cves(self):
        return [
            _bare(cve_id, severity=severity, cvss_score=score)
            for cve_id, severity, score in self.CVES
        ]

    def test_worst_first_then_by_score_those_without_one_last(self, pure1_client):
        """A severity Pure1 does not document, and none at all, come last"""
        _serve(pure1_client, cves=self._cves())

        module = run_main(pure1_cves, affected_only=False, min_cvss=0)

        result = _result(module)
        assert list(result["cves"]) == self.ORDER
        # a score of nought is a score
        assert result["cves"]["CVE-2019-1111"]["cvss_score"] == 0
        assert result["cves"]["CVE-2018-1111"]["cvss_score"] is None

    def test_order_is_the_same_for_the_cves_of_the_insights(self, pure1_client):
        ids = [cve_id for cve_id, severity, score in self.CVES]
        _serve(pure1_client, cves=self._cves(), insights=[_insight(cves=ids)])

        module = run_main(pure1_cves)

        assert list(_result(module)["cves"]) == self.ORDER

    @pytest.mark.parametrize(
        "limit, returned, truncated",
        [
            pytest.param(1, 1, True, id="one"),
            pytest.param(4, 4, True, id="within a severity"),
            pytest.param(11, 11, True, id="one less than all"),
            pytest.param(12, 12, False, id="all"),
            pytest.param(50, 12, False, id="more than all"),
        ],
    )
    @pytest.mark.parametrize("affected_only", [True, False])
    def test_limit_cuts_after_the_cves_are_put_in_order(
        self, pure1_client, affected_only, limit, returned, truncated
    ):
        ids = [cve_id for cve_id, severity, score in self.CVES]
        answers = _serve(pure1_client, cves=self._cves(), insights=[_insight(cves=ids)])

        module = run_main(pure1_cves, affected_only=affected_only, limit=limit)

        # every selected CVE is asked for, whatever the limit
        assert len(answers.requests) == 1
        assert "limit" not in answers.requests[0]
        module.warn.assert_not_called()
        result = _result(module)
        assert list(result["cves"]) == self.ORDER[:limit]
        assert (result["total"], result["returned"], result["truncated"]) == (
            12,
            returned,
            truncated,
        )

    def test_limit_of_nought_asks_for_the_number_alone(self, pure1_client):
        answers = _serve(pure1_client, cves=self._cves(), insights=[_insight()])

        module = run_main(
            pure1_cves, affected_only=False, severity=["critical"], limit=0
        )

        assert answers.requests == [
            {"filter": "severity='critical'", "sort": ["id"], "limit": 0}
        ]
        getattr(pure1_client, INSIGHTS).assert_not_called()
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={"total": 12, "returned": 0, "truncated": True, "cves": {}},
        )

    def test_limit_of_nought_for_the_cves_of_the_insights(self, pure1_client):
        ids = [cve_id for cve_id, severity, score in self.CVES]
        answers = _serve(pure1_client, cves=self._cves(), insights=[_insight(cves=ids)])

        module = run_main(pure1_cves, limit=0)

        (request,) = answers.requests
        assert request["limit"] == 0
        assert request["filter"].startswith("id=('CVE-2016-1111',")
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={"total": 12, "returned": 0, "truncated": True, "cves": {}},
        )

    def test_limit_of_nought_with_nothing_selected(self, pure1_client):
        """pure1_list() asks once more where Pure1 counts nothing"""
        answers = _serve(pure1_client)

        module = run_main(pure1_cves, affected_only=False, limit=0)

        assert answers.requests == [
            {"filter": None, "sort": ["id"], "limit": 0},
            {"filter": None, "sort": ["id"], "limit": 1},
        ]
        module.warn.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_cves=EMPTY)

    def test_listing_shorter_than_pure1_counted_gives_a_warning(self, pure1_client):
        answers = _serve(pure1_client, cves=[_bare(CVE1)], total_item_count=3)

        module = run_main(pure1_cves, affected_only=False, limit=5)

        assert answers.requests == [
            {"filter": None, "sort": ["id"]},
            {"filter": None, "sort": ["id"], "offset": 1},
        ]
        assert _warnings(module) == [SHORT.format(1, 3)]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            3,
            1,
            True,
        )

    def test_listing_that_is_short_for_earlier_ids_gives_a_warning(
        self, pure1_client, monkeypatch
    ):
        """The CVEs of the first request come short, those of the last whole"""
        monkeypatch.setattr(pure1_cves, "IDS_PER_REQUEST", 1)
        requests = []

        def first_is_short(**kwargs):
            requests.append(kwargs)
            (cve_id,) = re.findall(r"'(CVE-[^']*)'", kwargs["filter"])
            return paged_answers(
                "CveGetResponse",
                [_bare(cve_id)],
                total_item_count=2 if cve_id == CVE2 else 1,
            )(**kwargs)

        getattr(pure1_client, METHOD).side_effect = first_is_short
        getattr(pure1_client, INSIGHTS).return_value = valid_response()

        module = run_main(pure1_cves, affected_only=False, name=[CVE2, CVE1])

        assert requests == [
            _lookup(CVE2),
            dict(_lookup(CVE2), offset=1),
            _lookup(CVE1),
        ]
        assert _warnings(module) == [SHORT.format(2, 3)]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            3,
            2,
            True,
        )

    def test_listing_that_may_be_short_without_a_total_gives_a_warning(
        self, pure1_client, monkeypatch
    ):
        """Pure1 counts nothing and gives the first page again for a later one"""
        monkeypatch.setattr(pure1_cves, "IDS_PER_REQUEST", 1)
        cves = {CVE1: _bare(CVE1), CVE2: _bare(CVE2)}
        requests = []

        def first_page_again(**kwargs):
            requests.append(kwargs)
            (cve_id,) = re.findall(r"'(CVE-[^']*)'", kwargs["filter"])
            return paged_answers(
                "CveGetResponse", [cves[cve_id]], total_item_count=None
            )(filter=kwargs["filter"], sort=kwargs["sort"])

        getattr(pure1_client, METHOD).side_effect = first_page_again
        getattr(pure1_client, INSIGHTS).return_value = valid_response()

        module = run_main(pure1_cves, affected_only=False, name=[CVE2, CVE1])

        assert requests == [
            _lookup(CVE2),
            dict(_lookup(CVE2), offset=1),
            _lookup(CVE1),
            dict(_lookup(CVE1), offset=1),
        ]
        assert _warnings(module) == [SHORT.format(2, "an unknown number of")]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            2,
            2,
            False,
        )

    def test_more_cves_than_pure1_counted_are_all_counted(self, pure1_client):
        """CVEs that arrive while the pages are read: the total is never
        below the number returned"""
        answers = _serve(
            pure1_client, cves=self._cves(), page_size=5, total_item_count=7
        )

        module = run_main(pure1_cves, affected_only=False, limit=12)

        assert answers.requests == [
            {"filter": None, "sort": ["id"]},
            {"filter": None, "sort": ["id"], "offset": 5},
        ]
        module.warn.assert_not_called()
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == (
            10,
            10,
            False,
        )


class TestRows:
    """What is returned of a CVE."""

    def _one(self, pure1_client, cve, **params):
        _serve(pure1_client, cves=[cve])
        module = run_main(pure1_cves, affected_only=False, name=[CVE1], **params)
        return module, _result(module)["cves"][CVE1]

    def test_cve_with_only_its_id(self, pure1_client):
        module, row = self._one(pure1_client, _bare(CVE1))

        assert row == BARE
        module.warn.assert_not_called()

    def test_cve_with_only_its_id_and_the_affected_versions(self, pure1_client):
        module, row = self._one(
            pure1_client, _bare(CVE1), include_affected_versions=True
        )

        assert row == dict(BARE, affected_versions=NO_RELEASES)
        assert list(row)[-1] == "affected_versions"
        module.warn.assert_not_called()

    def test_array_with_only_its_name(self, pure1_client):
        cve = _cve(affected_resources=[sdk_model("CveResource", name="array2")])

        module, row = self._one(pure1_client, cve)

        assert row["affected_arrays"] == [
            {
                "name": "array2",
                "id": None,
                "fqdn": None,
                "os": None,
                "version": None,
                "fixed_in_later_versions": [],
                "in_insights": False,
            }
        ]
        module.warn.assert_not_called()

    def test_affected_versions_are_returned_when_asked_for(self, pure1_client):
        module, row = self._one(pure1_client, _cve(), include_affected_versions=True)

        assert row == _row(
            affected_arrays=[_array_row(in_insights=False)],
            affected_versions=AFFECTED_VERSIONS,
        )

    def test_affected_versions_are_no_key_otherwise(self, pure1_client):
        module, row = self._one(pure1_client, _cve(), include_affected_versions=False)

        assert "affected_versions" not in row
        assert row == _row(affected_arrays=[_array_row(in_insights=False)])

    def test_values_that_are_false_are_values(self, pure1_client):
        cve = _cve(
            severity="none",
            cvss_score=0,
            description="",
            kb_article="",
            detected_at=0,
            updated_at=0,
            affected_resources=[_resource(fqdn="", version="0")],
        )

        module, row = self._one(pure1_client, cve)

        assert row == _row(
            severity="none",
            cvss_score=0,
            description="",
            kb_article="",
            detected="1970-01-01 00:00:00 UTC",
            detected_epoch=0,
            updated="1970-01-01 00:00:00 UTC",
            updated_epoch=0,
            affected_arrays=[
                _array_row(
                    fqdn="",
                    version="0",
                    fixed_in_later_versions=[
                        "6.5.14",
                        "6.7.9",
                        "6.9.8",
                        "6.10.7",
                        "6.12.0",
                    ],
                    in_insights=False,
                )
            ],
        )

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_are_in_utc_whatever_the_time_zone_of_the_host(self, pure1_client):
        module, row = self._one(pure1_client, _cve())

        assert row["detected"] == "2021-02-03 13:04:05 UTC"
        assert row["updated"] == "2021-02-04 14:05:06 UTC"

    def test_releases_and_patches_are_filed_by_operating_system(self, pure1_client):
        cve = _cve(
            fixed_in_versions=[
                _version(FB, "4.8.0"),
                _version(FA, "6.10.7"),
                _version("Purity//X", "1.2.0"),
                _version(FA, "6.9.8"),
                _version(FA, "6.10.7"),
                _version(FB, "4.7.10"),
            ],
            fixed_in_patches=[
                _patch(FA, "security-patch-fa-2025-A"),
                _patch(FB, "security-patch-fb-2024-01-15"),
                _patch(FA, "security-bundle-fa-2024-01-15"),
                _patch(FA, "security-patch-fa-2025-A"),
            ],
            affected_versions=[_version("Purity//X", "1.1.0"), _version(FA, "6.10.6")],
        )

        module, row = self._one(pure1_client, cve, include_affected_versions=True)

        # in the order of their numbers, each once, the two systems always
        assert row["fixed_in_versions"] == {
            FA: ["6.9.8", "6.10.7"],
            FB: ["4.7.10", "4.8.0"],
            "Purity//X": ["1.2.0"],
        }
        assert list(row["fixed_in_versions"]) == [FA, FB, "Purity//X"]
        assert row["fixed_in_patches"] == {
            FA: ["security-bundle-fa-2024-01-15", "security-patch-fa-2025-A"],
            FB: ["security-patch-fb-2024-01-15"],
        }
        assert row["affected_versions"] == {
            FA: ["6.10.6"],
            FB: [],
            "Purity//X": ["1.1.0"],
        }
        module.warn.assert_not_called()

    def test_releases_and_patches_that_cannot_be_filed_are_left_out(self, pure1_client):
        cve = _received(
            {
                "id": CVE1,
                "fixed_in_versions": [
                    {"os": FA, "version": "6.10.7"},
                    {"os": FA},
                    {"version": "6.12.0"},
                    None,
                ],
                "fixed_in_patches": [{"name": "security-patch-fa-2025-A"}, {"os": FB}],
                "affected_versions": [{"os": FA}, {"os": FA, "version": "6.10.6"}],
            }
        )

        module, row = self._one(pure1_client, cve)

        assert row["fixed_in_versions"] == {FA: ["6.10.7"], FB: []}
        assert row["fixed_in_patches"] == NO_RELEASES
        # the affected versions are not read when they are not asked for
        assert _warnings(module) == [UNFILED.format(5)]

    def test_affected_versions_that_cannot_be_filed_are_counted_too(self, pure1_client):
        cve = _received(
            {
                "id": CVE1,
                "fixed_in_versions": [{"os": FA}],
                "affected_versions": [{"os": FA}, {"os": FA, "version": "6.10.6"}],
            }
        )

        module, row = self._one(pure1_client, cve, include_affected_versions=True)

        assert row["affected_versions"] == {FA: ["6.10.6"], FB: []}
        assert _warnings(module) == [UNFILED.format(2)]

    def test_arrays_that_cannot_be_filed_are_left_out(self, pure1_client):
        cve = _received(
            {
                "id": CVE1,
                "affected_resources": [
                    {"id": "id-array1", "os": FA, "version": "6.10.6"},
                    None,
                    {"name": "array2"},
                ],
            }
        )

        module, row = self._one(pure1_client, cve)

        assert [array["name"] for array in row["affected_arrays"]] == ["array2"]
        assert _warnings(module) == [UNNAMED.format(2)]

    def test_what_cannot_be_filed_is_counted_over_all_cves(self, pure1_client):
        broken = {
            "fixed_in_versions": [{"os": FA}],
            "affected_resources": [{"id": "id-array1"}],
        }
        _serve(
            pure1_client,
            cves=[
                _received(dict(broken, id=CVE1)),
                _received(dict(broken, id=CVE2)),
                _received(dict(broken, id="CVE-2021-44228")),
            ],
        )

        module = run_main(pure1_cves, affected_only=False, limit=2)

        # only of the CVEs that are returned
        assert _warnings(module) == [UNFILED.format(2), UNNAMED.format(2)]

    def test_cve_without_an_id_is_left_out(self, pure1_client):
        _serve(
            pure1_client,
            cves=[_cve(), sdk_model("Cve", severity="high"), sdk_model("Cve")],
        )

        module = run_main(pure1_cves, affected_only=False, limit=5)

        assert _warnings(module) == [NO_ID.format(2)]
        result = _result(module)
        assert list(result["cves"]) == [CVE1]
        # Pure1 counted three: not every selected CVE is in the result
        assert (result["total"], result["returned"], result["truncated"]) == (
            3,
            1,
            True,
        )

    def test_cve_that_pure1_returns_twice_is_in_the_result_once(self, pure1_client):
        _serve(
            pure1_client,
            cves=[_cve(), _cve(description="Another one."), _cve(CVE2), _cve(CVE2)],
        )

        module = run_main(pure1_cves, affected_only=False, limit=5)

        assert _warnings(module) == [SAME_ID.format(2)]
        result = _result(module)
        assert result["cves"][CVE1]["description"] == "A use-after-free in a library."
        assert (result["total"], result["returned"], result["truncated"]) == (
            4,
            2,
            True,
        )

    @pytest.mark.parametrize(
        "cves, warning, numbers",
        [
            pytest.param(
                [_cve(), sdk_model("Cve", severity="medium")],
                NO_ID.format(1),
                (2, 1, True),
                id="without an id",
            ),
            pytest.param(
                [_cve(), _cve(), _cve(CVE2)],
                SAME_ID.format(1),
                (3, 2, True),
                id="returned twice",
            ),
        ],
    )
    def test_cve_that_is_left_out_is_missing_from_the_result_without_a_limit(
        self, pure1_client, cves, warning, numbers
    ):
        """truncated tells of it, although no limit cut the result"""
        _serve(pure1_client, cves=cves)

        module = run_main(pure1_cves, affected_only=False, severity=["medium"])

        assert _warnings(module) == [warning]
        result = _result(module)
        assert (result["total"], result["returned"], result["truncated"]) == numbers

    def test_order_of_the_fix_releases_is_worked_out_once_for_all_arrays(
        self, pure1_client, monkeypatch
    ):
        """Each further array of a CVE costs the order of its own release"""
        natural_order = pure1_cves.natural_order

        def ordered_for(count):
            ordered = []
            monkeypatch.setattr(
                pure1_cves,
                "natural_order",
                lambda text: ordered.append(text) or natural_order(text),
            )
            arrays = [
                _resource("array{0}".format(number), version="6.9.{0}".format(number))
                for number in range(1, count + 1)
            ]
            _known_arrays(pure1_client)
            _serve(pure1_client, cves=[_cve(affected_resources=arrays)])
            module = run_main(pure1_cves, affected_only=False, name=[CVE1])
            row = _result(module)["cves"][CVE1]
            assert [
                array["fixed_in_later_versions"] for array in row["affected_arrays"]
            ] == [["6.9.8", "6.10.7", "6.12.0"]] * count
            return ordered

        for_one = ordered_for(1)
        for_six = ordered_for(6)

        assert sorted(for_six) == sorted(
            for_one + ["6.9.2", "6.9.3", "6.9.4", "6.9.5", "6.9.6"]
        )


class TestScore:
    """min_cvss: the number that is sent to Pure1 in place of the score."""

    @pytest.mark.parametrize(
        "min_cvss, number",
        [
            pytest.param(0, "0.00", id="nought"),
            pytest.param(0.0, "0.00", id="nought as a float"),
            pytest.param(0.001, "0.05", id="just above nought"),
            pytest.param(0.1, "0.05", id="the lowest score above nought"),
            pytest.param(0.11, "0.15", id="between two scores"),
            pytest.param(7, "6.95", id="whole number"),
            pytest.param(7.5, "7.45", id="one decimal"),
            pytest.param(9.75, "9.75", id="half a step"),
            pytest.param(9.8, "9.75", id="9.8"),
            pytest.param(9.800001, "9.85", id="just above a score"),
            pytest.param(9.9, "9.85", id="9.9"),
            pytest.param(9.91, "9.95", id="above the last score but one"),
            pytest.param(10, "9.95", id="ten"),
        ],
    )
    def test_number_sent_for_a_lowest_score(self, min_cvss, number):
        assert pure1_cves.score_bound(min_cvss) == number

    @pytest.mark.parametrize(
        "read",
        [
            pytest.param(float, id="as it is written"),
            pytest.param(lambda number: _single(float(number)), id="as Pure1 reads it"),
        ],
    )
    def test_number_sent_selects_the_scores_from_the_lowest_on(self, read):
        """For every lowest score and every score with one decimal"""
        scores = [tenth / 10 for tenth in range(101)]
        for lowest in scores:
            number = read(pure1_cves.score_bound(lowest))
            assert [score for score in scores if score >= number] == [
                score for score in scores if score >= lowest
            ], lowest

    def test_score_itself_is_misread_for_many_scores(self):
        """Why the score is not sent as it is: read in single precision, 42
        of the scores with one decimal are above themselves"""
        scores = [tenth / 10 for tenth in range(101)]
        above = [score for score in scores if _single(score) > score]

        assert len(above) == 42
        assert {9.8, 9.1, 8.8, 7.8, 5.3} <= set(above)
        assert not {10.0, 9.9, 9.0, 7.5, 7.0, 0.0} & set(above)

    @pytest.mark.parametrize(
        "params, found",
        [
            pytest.param({}, ["CVE-2024-1003", "CVE-2024-1002"], id="CVEs"),
            pytest.param({"limit": 0}, [], id="the number alone"),
        ],
    )
    def test_cve_whose_score_is_the_lowest_wanted_is_selected(
        self, pure1_client, params, found
    ):
        answers = _scored(
            [
                _bare("CVE-2024-1001", cvss_score=9.7),
                _bare("CVE-2024-1002", cvss_score=9.8),
                _bare("CVE-2024-1003", cvss_score=9.9),
                _bare("CVE-2024-1004"),
            ]
        )
        getattr(pure1_client, METHOD).side_effect = answers
        getattr(pure1_client, INSIGHTS).return_value = valid_response()

        module = run_main(pure1_cves, affected_only=False, min_cvss=9.8, **params)

        assert [request["filter"] for request in answers.requests] == [
            "cvss_score>=9.75"
        ]
        result = _result(module)
        assert list(result["cves"]) == found
        assert (result["total"], result["returned"]) == (2, len(found))

    def test_score_sent_as_it_is_would_leave_the_cve_out(self):
        """What the stand-in of these tests does with the number 9.80, as
        Pure1 does"""
        answers = _scored(
            [
                _bare("CVE-2024-1002", cvss_score=9.8),
                _bare("CVE-2024-1003", cvss_score=9.9),
            ]
        )

        as_it_is = answers(filter="cvss_score>=9.80", sort=["id"])
        lowered = answers(filter="cvss_score>=9.75", sort=["id"])

        assert [cve.id for cve in as_it_is.items] == ["CVE-2024-1003"]
        assert [cve.id for cve in lowered.items] == ["CVE-2024-1002", "CVE-2024-1003"]


def _failure(module):
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


class TestOptions:
    """The argument spec and the checks of the module."""

    def test_the_options_are_those_of_the_documentation(self, pure1_client):
        _serve(pure1_client)

        module = run_main(pure1_cves)

        options = yaml.safe_load(pure1_cves.DOCUMENTATION)["options"]
        assert set(module.params) - set(pure1_argument_spec()) == set(options)
        assert {name: module.params[name] for name in options} == {
            "array": None,
            "name": None,
            "severity": None,
            "min_cvss": None,
            "affected_only": True,
            "window": None,
            "include_affected_versions": False,
            "limit": None,
        }
        documented = {
            name: option["default"]
            for name, option in options.items()
            if "default" in option
        }
        assert documented == {"affected_only": True, "include_affected_versions": False}
        assert options["severity"]["choices"] == list(pure1_cves.SEVERITIES)

    def test_every_option_goes_through_the_argument_spec(
        self, pure1_client, monkeypatch
    ):
        """A list given as one string and a number given as text are converted"""
        monkeypatch.setattr(pure1_cves.time, "time", lambda: 1612357445.678)
        _known_arrays(pure1_client, "array1", "array2")
        answers = _serve(
            pure1_client,
            cves=[_cve(), _cve(CVE2)],
            insights=[_insight(cves=[CVE1, CVE2])],
        )

        module = run_main(
            pure1_cves,
            array="ARRAY1,array2",
            name="cve-2023-52001, CVE-2021-44228",
            severity="high,medium",
            min_cvss="4",
            affected_only="yes",
            window="86400",
            include_affected_versions="yes",
            limit="5",
        )

        assert [call[1] for call in pure1_client.get_arrays.call_args_list] == [
            {"limit": 0},
            {"filter": "name=('ARRAY1','array2')"},
        ]
        getattr(pure1_client, INSIGHTS).assert_called_once_with(
            filter="resource.name=('array1','array2') and " + CVE_TYPES
        )
        assert answers.requests == [
            {
                "filter": "id='CVE-2023-52001' and severity=('high','medium') and "
                "cvss_score>=3.95 and detected_at>=1612271045678",
                "sort": ["id"],
            }
        ]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_cves={
                "total": 1,
                "returned": 1,
                "truncated": False,
                "cves": {CVE1: _row(affected_versions=AFFECTED_VERSIONS)},
            },
        )

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param(
                {"severity": ["high", "severe"]},
                # up to the choices, which the module gives: ansible-core
                # words the rest
                r"value of severity must be one or more of: critical, high, medium, "
                r"low, none",
                id="severity that is no choice",
            ),
            pytest.param(
                {"affected_only": "perhaps"},
                # ansible-core names the type as <class 'str'> up to 2.18 and
                # as str from 2.19 on
                r"argument 'affected_only' is of type (<class 'str'>|str) and we "
                r"were unable to convert to bool: ",
                id="affected_only is no boolean",
            ),
            pytest.param(
                {"limit": "ten"},
                r"argument 'limit' is of type (<class 'str'>|str) and we were unable "
                r"to convert to int: ",
                id="limit is no number",
            ),
            pytest.param(
                {"min_cvss": "high"},
                r"argument 'min_cvss' is of type (<class 'str'>|str) and we were "
                r"unable to convert to float: ",
                id="min_cvss is no number",
            ),
            pytest.param(
                {"filter": "severity='high'"},
                r"Unsupported parameters for module: filter\.",
                id="no filter option",
            ),
            pytest.param(
                {"sort": "cvss_score-"},
                r"Unsupported parameters for module: sort\.",
                id="no sort option",
            ),
            pytest.param(
                {"cve_ids": [CVE1]},
                r"Unsupported parameters for module: cve_ids\.",
                id="the CVEs are named with name",
            ),
        ],
    )
    def test_option_that_the_argument_spec_refuses(
        self, pure1_client, client_factory, params, message
    ):
        module = run_main(pure1_cves, **params)

        assert re.match(message, _failure(module))
        client_factory.assert_not_called()

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param({"limit": -1}, "limit must be 0 or more, got -1", id="limit"),
            pytest.param({"window": 0}, "window must be 1 or more, got 0", id="window"),
            pytest.param(
                {"window": -60},
                "window must be 1 or more, got -60",
                id="window below 0",
            ),
            pytest.param(
                {"min_cvss": -0.1},
                "min_cvss must be between 0 and 10, got -0.1",
                id="score below 0",
            ),
            pytest.param(
                {"min_cvss": 75},
                "min_cvss must be between 0 and 10, got 75.0",
                id="score above 10",
            ),
            pytest.param(
                {
                    "name": [
                        CVE1,
                        "CVE-2021",
                        "log4shell",
                        "CVE-21-44228",
                        "XCVE-2021-44228",
                    ]
                },
                "name must give the ids of CVEs as CVE-<year>-<number>, got: "
                "CVE-2021, log4shell, CVE-21-44228, XCVE-2021-44228",
                id="names that are no ids",
            ),
            pytest.param(
                {
                    "name": [
                        "CVE-2021-44228x",
                        "CVE-2021-123",
                        "CVE-202-12345",
                        "CVE-20211-1234",
                        CVE1,
                    ]
                },
                "name must give the ids of CVEs as CVE-<year>-<number>, got: "
                "CVE-2021-44228x, CVE-2021-123, CVE-202-12345, CVE-20211-1234",
                id="names that are nearly ids",
            ),
            pytest.param(
                {"name": [""]},
                "name must give the ids of CVEs as CVE-<year>-<number>, got: ",
                id="empty name",
            ),
            pytest.param(
                {"severity": ["high", "none"]},
                "severity none can be given only with affected_only false: the "
                "security insights of the arrays report no CVE of that severity.",
                id="not rated among the affected",
            ),
            pytest.param(
                {"affected_only": False, "array": ["array1"], "limit": 5},
                "array cannot be given with affected_only false: the CVE catalogue "
                "of Pure1 is not kept by array.",
                id="arrays of the catalogue",
            ),
            pytest.param(
                {"affected_only": False},
                "affected_only false reads the CVE catalogue that Pure1 keeps for "
                "every organization: give at least one of name, severity, min_cvss, "
                "window and limit with it.",
                id="the whole catalogue",
            ),
            pytest.param(
                {
                    "affected_only": False,
                    "array": [],
                    "name": [],
                    "severity": [],
                    "include_affected_versions": True,
                },
                "affected_only false reads the CVE catalogue that Pure1 keeps for "
                "every organization: give at least one of name, severity, min_cvss, "
                "window and limit with it.",
                id="the whole catalogue with empty lists",
            ),
        ],
    )
    def test_option_that_the_module_refuses(
        self, pure1_client, client_factory, params, message
    ):
        """Before the client is made: nothing is asked of Pure1"""
        module = run_main(pure1_cves, **params)

        module.fail_json.assert_called_once_with(msg=message)
        module.exit_json.assert_not_called()
        client_factory.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": [CVE1]}, id="name"),
            pytest.param({"severity": ["none"]}, id="severity"),
            pytest.param({"min_cvss": 0}, id="score of nought"),
            pytest.param({"window": 1}, id="window"),
            pytest.param({"limit": 0}, id="limit of nought"),
            pytest.param({"limit": 3, "array": []}, id="an empty list of arrays"),
        ],
    )
    def test_one_option_is_enough_for_the_catalogue(self, pure1_client, params):
        _serve(pure1_client, cves=[_cve()])

        module = run_main(pure1_cves, affected_only=False, **params)

        module.fail_json.assert_not_called()
        assert _result(module)["total"] == 1

    @pytest.mark.parametrize(
        "missing, named",
        [
            pytest.param([METHOD], METHOD, id="the CVEs"),
            pytest.param([INSIGHTS], INSIGHTS, id="the insights"),
            pytest.param([METHOD, INSIGHTS], METHOD, id="both"),
        ],
    )
    def test_client_without_a_method(self, pure1_client, monkeypatch, missing, named):
        _known_arrays(pure1_client, "array1")
        _serve(pure1_client, cves=[_cve()], insights=[_insight()])
        monkeypatch.setattr(pypureclient, "__version__", "1.90.0")
        for method in missing:
            # What a client of an older release has for the name: nothing to call
            setattr(pure1_client, method, None)

        module = run_main(pure1_cves, array=["array1"])

        assert _failure(module) == (
            "py-pure-client 1.91.0 or later is required: this module needs "
            "Client.{0}(), which the installed py-pure-client 1.90.0 does not "
            "have.".format(named)
        )
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)


class TestFailures:
    """A failed request fails the task with the line of the shared helpers."""

    ANSWERS = [
        pytest.param(
            error_response(403, "Access denied"),
            " (HTTP 403): Access denied",
            id="error answer",
        ),
        pytest.param(
            error_response(400, "Unknown filter field", "Filtering is not supported"),
            " (HTTP 400): Unknown filter field: Filtering is not supported",
            id="error answer with context",
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

    @pytest.mark.parametrize(
        "method, params",
        [
            pytest.param(METHOD, {}, id="CVEs of the insights"),
            pytest.param(INSIGHTS, {}, id="insights"),
            pytest.param("get_arrays", {"array": ["array1"]}, id="arrays"),
            pytest.param(
                METHOD, {"affected_only": False, "limit": 5}, id="CVEs of the catalogue"
            ),
            pytest.param(
                METHOD, {"affected_only": False, "limit": 0}, id="number of CVEs"
            ),
            pytest.param(
                INSIGHTS,
                {"affected_only": False, "name": [CVE1]},
                id="insights for the catalogue",
            ),
        ],
    )
    @pytest.mark.parametrize("answer, reason", ANSWERS)
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, params, answer, reason
    ):
        _serve(pure1_client, cves=[_cve()], insights=[_insight()])
        if method == "get_arrays":
            # the first answer is for the check of get_pure1
            pure1_client.get_arrays.side_effect = [valid_response(), answer]
        elif isinstance(answer, Exception):
            getattr(pure1_client, method).side_effect = answer
        else:
            getattr(pure1_client, method).side_effect = None
            getattr(pure1_client, method).return_value = answer

        module = run_main(pure1_cves, **params)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(method, reason)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"name": [CVE1, CVE2]}, id="the next ids"),
            pytest.param({"limit": 5}, id="the next page"),
        ],
    )
    @pytest.mark.parametrize("answer, reason", ANSWERS)
    def test_failed_request_for_later_cves_fails_the_task(
        self, pure1_client, monkeypatch, params, answer, reason
    ):
        monkeypatch.setattr(pure1_cves, "IDS_PER_REQUEST", 1)
        answers = _catalogue([_cve(), _cve(CVE2)], page_size=1)

        def second_fails(**kwargs):
            if answers.requests:
                if isinstance(answer, Exception):
                    raise answer
                return answer
            return answers(**kwargs)

        getattr(pure1_client, METHOD).side_effect = second_fails

        module = run_main(pure1_cves, affected_only=False, **params)

        assert len(answers.requests) == 1
        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(METHOD, reason)
        )
        module.exit_json.assert_not_called()
        getattr(pure1_client, INSIGHTS).assert_not_called()


def _contains(block):
    """Return the keys under a contains block of RETURN, by their path"""
    paths = set()
    for key, entry in block.items():
        paths.add(key)
        for path in _contains(entry.get("contains", {})):
            paths.add(key + "." + path)
    return paths


def _paths(row):
    """Return the keys of the result of a CVE and of its arrays, by their path"""
    paths = set(row)
    for array in row["affected_arrays"]:
        paths.update("affected_arrays." + key for key in array)
    return paths


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


def _condition(expression, variables):
    return bool(_template("{{ " + expression + " }}", variables))


def _play_examples(pure1_client, **served):
    """Run the tasks of EXAMPLES in their order and return what is read

    A task of the module runs through main() with the options of the example
    and registers its result. served gives, by the name the task registers,
    the CVEs and the insights Pure1 has for it. A task that reads a result
    gives a list with its templates rendered for each item of its loop, once
    when it has no loop.
    """
    registered = {}
    read = []
    for task in yaml.safe_load(pure1_cves.EXAMPLES):
        options = task.get("everpure.pure1.pure1_cves")
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            _known_arrays(pure1_client, *options.get("array", []))
            _serve(pure1_client, **served[task["register"]])
            module = run_main(pure1_cves, **options)
            registered[task["register"]] = module.exit_json.call_args[1]
            continue
        (action,) = set(task) - {"name", "when", "loop"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        rendered = []
        for item in items:
            variables = dict(registered, item=item)
            made = {}
            if "when" in task:
                made["when"] = _condition(task["when"], variables)
            for key, value in task[action].items():
                if key == "that":
                    made[key] = [_condition(each, variables) for each in value]
                else:
                    made[key] = _template(value, variables)
            rendered.append(made)
        read.append(rendered)
    return read


LOG4SHELL = "CVE-2021-44228"
# What Pure1 has for each task of EXAMPLES when its arrays are affected
AFFECTED = dict(
    affected=dict(
        cves=[
            _cve(),
            _cve(CVE2, affected_resources=[_resource(), _resource("array2")]),
        ],
        insights=[_insight(cves=[CVE1, CVE2]), _insight("array2", cves=[CVE2])],
    ),
    serious=dict(
        cves=[
            _cve(LOG4SHELL, severity="critical", cvss_score=10.0),
            _cve(CVE2, severity="high"),
        ],
        insights=[_insight(cves=[LOG4SHELL, CVE2], severity="critical")],
    ),
    planned=dict(
        cves=[_cve(affected_versions=[_version(FA, "6.7.4")])], insights=[_insight()]
    ),
    recent=dict(cves=[_cve(), _cve(CVE2, cvss_score=6.5)]),
    looked_up=dict(
        cves=[
            _cve(
                LOG4SHELL,
                fixed_in_versions=[_version(FA, "6.2.6")],
                affected_resources=[],
            ),
            _bare("CVE-2024-3094"),
        ]
    ),
)
# and when nothing affects them
CLEAN = dict(
    affected=dict(),
    serious=dict(insights=[_insight()]),
    planned=dict(cves=[_cve()], insights=[_insight()]),
    recent=dict(),
    looked_up=dict(),
)


class TestDocumentation:
    """RETURN and EXAMPLES against what the module returns."""

    def _result(self, pure1_client, **params):
        _serve(pure1_client, cves=[_cve()], insights=[_insight()])
        module = run_main(pure1_cves, **params)
        return module.exit_json.call_args[1]

    def test_return_names_the_keys_of_the_result(self, pure1_client):
        result = self._result(pure1_client)

        documented = yaml.safe_load(pure1_cves.RETURN)
        assert set(result) - {"changed"} == set(documented) == {"pure1_cves"}
        assert list(result["pure1_cves"]) == list(documented["pure1_cves"]["contains"])

    def test_return_lists_the_keys_of_a_cve_and_of_an_array(self, pure1_client):
        result = self._result(pure1_client, include_affected_versions=True)

        documented = yaml.safe_load(pure1_cves.RETURN)["pure1_cves"]["contains"]
        row = result["pure1_cves"]["cves"][CVE1]
        assert _contains(documented["cves"]["contains"]) == _paths(row)
        # in the order of the result
        assert list(documented["cves"]["contains"]) == list(row)
        assert list(
            documented["cves"]["contains"]["affected_arrays"]["contains"]
        ) == list(row["affected_arrays"][0])

    def test_return_says_which_key_is_not_always_there(self, pure1_client):
        result = self._result(pure1_client)

        keys = yaml.safe_load(pure1_cves.RETURN)["pure1_cves"]["contains"]["cves"]
        sometimes = {
            key for key, entry in keys["contains"].items() if "returned" in entry
        }
        assert sometimes == {"affected_versions"}
        assert (
            set(keys["contains"]) - set(result["pure1_cves"]["cves"][CVE1]) == sometimes
        )
        assert keys["contains"]["affected_versions"]["returned"] == (
            "when I(include_affected_versions) is C(true)"
        )

    def test_every_key_of_return_is_an_identifier(self):
        """validate-modules of ansible-core 2.21 refuses any other key"""
        documented = yaml.safe_load(pure1_cves.RETURN)

        for path in _contains(documented):
            assert all(key.isidentifier() for key in path.split(".")), path

    def test_sample_has_the_shape_of_a_result(self, pure1_client):
        result = self._result(pure1_client)["pure1_cves"]

        sample = yaml.safe_load(pure1_cves.RETURN)["pure1_cves"]["sample"]
        assert list(sample) == list(result)
        assert {key: type(value) for key, value in sample.items()} == {
            key: type(value) for key, value in result.items()
        }
        assert sample["returned"] == len(sample["cves"])
        assert sample["truncated"] is (sample["returned"] < sample["total"])
        (row,) = sample["cves"].values()
        like = result["cves"][CVE1]
        assert list(row) == list(like)
        (array,) = row["affected_arrays"]
        for item, other in ((row, like), (array, like["affected_arrays"][0])):
            assert {key: type(value) for key, value in item.items()} == {
                key: type(value) for key, value in other.items()
            }
        assert row["detected"] == pure1_time(row["detected_epoch"])
        assert row["updated"] == pure1_time(row["updated_epoch"])
        for key in ("fixed_in_versions", "fixed_in_patches"):
            assert list(row[key]) == [FA, FB]
        releases = row["fixed_in_versions"][array["os"]]
        assert releases == sorted(releases, key=pure1_cves.natural_order)
        assert array["fixed_in_later_versions"] == [
            release
            for release in releases
            if pure1_cves.natural_order(release)
            > pure1_cves.natural_order(array["version"])
        ]

    def test_return_gives_the_type_of_every_key(self, pure1_client):
        result = self._result(pure1_client, include_affected_versions=True)

        documented = yaml.safe_load(pure1_cves.RETURN)["pure1_cves"]
        types = {
            "bool": bool,
            "dict": dict,
            "float": float,
            "int": int,
            "list": list,
            "str": str,
        }
        row = result["pure1_cves"]["cves"][CVE1]
        cves = documented["contains"]["cves"]
        assert types[documented["type"]] is type(result["pure1_cves"])
        for keys, values in (
            (documented["contains"], result["pure1_cves"]),
            (cves["contains"], row),
            (
                cves["contains"]["affected_arrays"]["contains"],
                row["affected_arrays"][0],
            ),
        ):
            assert {key: types[entry["type"]] for key, entry in keys.items()} == {
                key: type(value) for key, value in values.items()
            }

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "All arrays when not set or empty.",
                id="an empty list of arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "It cannot be given when I(affected_only) is C(false).",
                id="arrays of the catalogue",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "All CVEs that the other options select when not set or empty.",
                id="an empty list of names",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "in upper or lower case",
                id="case of a name",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.severity",
                "Every severity when not set or empty.",
                id="an empty list of severities",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.severity",
                "It can be given only when I(affected_only) is C(false)",
                id="not rated",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.min_cvss",
                "A CVE without a score is then not returned, whatever the value.",
                id="score of nought",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.affected_only",
                "At least one of I(name), I(severity), I(min_cvss), I(window) and "
                "I(limit) must then be given.",
                id="the whole catalogue",
            ),
            pytest.param(
                "DOCUMENTATION", "options.window", "Must be 1 or more.", id="window"
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.limit",
                "With C(0) no CVE is returned, only C(total).",
                id="limit of nought",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.limit",
                "Pure1 is asked for every selected CVE unless the limit is C(0).",
                id="cost of a limit",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "or that no security insight reports while I(affected_only) is "
                "C(true), is not an error",
                id="name that no insight reports",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.severity",
                "The severity is that of the record Pure1 keeps of the CVE",
                id="severity of the record",
            ),
            pytest.param(
                "RETURN",
                "pure1_cves.contains.truncated",
                "when CVEs that Pure1 counted are left out of the result or did "
                "not arrive, which a warning then says.",
                id="truncated without a limit",
            ),
            pytest.param(
                "RETURN",
                "pure1_cves.contains.cves.contains.fixed_in_versions",
                "The keys C(Purity//FA) and C(Purity//FB) are always there",
                id="operating systems",
            ),
            pytest.param(
                "RETURN",
                "pure1_cves.contains.cves.contains.affected_arrays",
                "With I(array) only the arrays named there are listed.",
                id="arrays of the task",
            ),
            pytest.param(
                "RETURN",
                "pure1_cves.contains.cves.contains.affected_arrays.contains.version",
                "Null for an array that only a security insight reports the CVE for.",
                id="array of an insight",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_cves, block))
        for key in path.split("."):
            entry = entry[key]

        description = entry["description"]
        if isinstance(description, list):
            description = " ".join(description)
        assert says in description

    def test_notes_name_the_release_and_the_methods_the_module_needs(self):
        first, second, third = yaml.safe_load(pure1_cves.DOCUMENTATION)["notes"]

        assert "py-pure-client 1.91.0 or later" in first
        for method, minimum in pure1_cves.REQUIRED_METHODS:
            assert "C({0})".format(method) in first
            assert minimum == "1.91.0"

    def test_notes_say_when_a_cve_without_a_record_gives_a_warning(self):
        """What the tests of the warnings pin"""
        first, second, third = yaml.safe_load(pure1_cves.DOCUMENTATION)["notes"]

        for says in (
            "With I(affected_only) set to C(true), a CVE that a security insight "
            "reports and that Pure1 has no record of is left out of the result and "
            "is not counted.",
            "also when I(severity) alone selects and an insight reports the CVE "
            "with one of the severities.",
            "With I(min_cvss) or I(window), and with a I(limit) of C(0), such a "
            "CVE cannot be told from one that the options leave out, and no "
            "warning is given.",
        ):
            assert says in third

    def test_examples_use_every_option_of_the_module(self):
        options = set(yaml.safe_load(pure1_cves.DOCUMENTATION)["options"])
        used = set()
        for task in yaml.safe_load(pure1_cves.EXAMPLES):
            arguments = task.get("everpure.pure1.pure1_cves")
            if arguments:
                used.update(set(arguments) - {"app_id", "key_file"})
        assert used == options

    def test_examples_read_what_the_module_returns(self, pure1_client):
        """The tasks after a task of the module, on the result it registered"""
        read = _play_examples(pure1_client, **AFFECTED)

        fixes = {FA: ["6.2.6"], FB: []}
        assert read == [
            [
                {"msg": CVE2 + " (medium): array1, array2"},
                {"msg": CVE1 + " (medium): array1"},
            ],
            [
                {
                    "that": [False],
                    "fail_msg": "The arrays are affected by CVE-2021-44228, " + CVE2,
                }
            ],
            [{"that": [False], "fail_msg": "Purity//FA 6.7.4 is affected by " + CVE1}],
            [{"when": True, "msg": [CVE2, CVE1]}],
            [
                {"msg": "CVE-2021-44228: {0}".format(fixes)},
                {"msg": "CVE-2024-3094: {0}".format(NO_RELEASES)},
            ],
        ]

    def test_examples_on_arrays_that_no_cve_affects(self, pure1_client):
        read = _play_examples(pure1_client, **CLEAN)

        assert read == [
            [],
            [{"that": [True], "fail_msg": "The arrays are affected by "}],
            [{"that": [True], "fail_msg": "Purity//FA 6.7.4 is affected by " + CVE1}],
            [{"when": False, "msg": []}],
            [],
        ]

    @pytest.mark.parametrize(
        "template",
        [
            pytest.param("{{ result['pure1_cves']['count'] }}", id="key"),
            pytest.param("{{ result.pure1_cves.cve | length }}", id="filter"),
            pytest.param(
                "{{ result['pure1_cves']['cves'].values() "
                "| map(attribute='affected_versions') | join(', ') }}",
                id="key that is not asked for",
            ),
            pytest.param(
                "{{ result['pure1_cves']['cves'].values() "
                "| map(attribute='affected_arrays') | first "
                "| map(attribute='array') | join(', ') }}",
                id="attribute of an array",
            ),
        ],
    )
    def test_example_that_reads_a_key_the_module_does_not_return_fails(
        self, pure1_client, template
    ):
        result = self._result(pure1_client)

        with pytest.raises(jinja2.UndefinedError):
            _template(template, {"result": result})
