# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_audits module."""

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


from plugins.modules import pure1_audits
from plugins.modules.pure1_audits import check_options, time_range
from tests.unit.conftest import (
    ansible_module,
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# The module starts at 2021-02-03 14:04:05.678 UTC
NOW = 1612361045678
# One day before, 2021-02-02 14:04:05.678 UTC
DAY_AGO = 1612274645678
PERIOD = "time>=1612274645678 and time<1612361045678"
SORT = ["time-", "id"]
# 2021-02-03 13:04:05 UTC
TIME = 1612357445000
MODULE = "everpure.pure1.pure1_audits"
# ansible-doc takes out of what it prints whatever matches this (ansible/cli/doc.py)
RST_DIRECTIVE = r".. \w+?::"

ROW = {
    "id": "audit-1",
    "time": "2021-02-03 13:04:05 UTC",
    "time_epoch": 1612357445000,
    "array": {"name": "array1", "id": "array-id-1", "fqdn": "array1.example.com"},
    "user": "pureuser",
    "command": "purevol",
    "subcommand": "destroy",
    "arguments": "vol1",
    "origin": "array",
}
# A subcommand of two words, an array for which Pure1 has no fqdn, the other origin
BLADE_ROW = {
    "id": "audit-2",
    "time": "2021-02-03 13:04:04 UTC",
    "time_epoch": 1612357444000,
    "array": {"name": "array2", "id": "array-id-2", "fqdn": None},
    "user": "svc-automation",
    "command": "purefs",
    "subcommand": "snapshot destroy",
    "arguments": "fs1.daily",
    "origin": "Pure1",
}
BARE_ROW = {
    "id": None,
    "time": None,
    "time_epoch": None,
    "array": {"name": None, "id": None, "fqdn": None},
    "user": None,
    "command": None,
    "subcommand": None,
    "arguments": None,
    "origin": None,
}
EMPTY_RESULT = {
    "total": 0,
    "returned": 0,
    "truncated": False,
    "start": "2021-02-02 14:04:05 UTC",
    "start_epoch": DAY_AGO,
    "end": "2021-02-03 14:04:05 UTC",
    "end_epoch": NOW,
    "by_array": {},
    "by_user": {},
    "records": [],
}

WITHOUT_ARRAY = (
    "Audit records that Pure1 returned without an array are not counted in "
    "by_array: {0}."
)
WITHOUT_USER = (
    "Audit records that Pure1 returned without a user are not counted in "
    "by_user: {0}."
)
NAMESAKE = (
    "Audit records of an array that carries the name of another array are "
    "not counted in by_array: {0}."
)
EXCLUDE_QUOTE = (
    "Pure1 cannot leave a user whose name holds a single quote out of the "
    "audit records: exclude_users must not hold such a name."
)
SEVERAL_QUOTE = (
    "Pure1 cannot filter audit records on several values when one of them "
    "holds a single quote: give subcommand one such value alone, which works."
)
SEARCH_QUOTE = (
    "Pure1 cannot search audit records for a text that holds a single quote: "
    "{0} must not hold one."
)
TOO_LONG = (
    "Pure1 request get_audits failed (HTTP 400): Invalid value: The selected "
    "time range is longer than maximum 30 days."
)


@pytest.fixture(autouse=True)
def _module_starts_at_a_fixed_time(monkeypatch):
    monkeypatch.setattr(pure1_audits.time, "time", lambda: NOW / 1000.0)


def _audit(number=1, array="array1", **overrides):
    fields = dict(
        id="audit-{0}".format(number),
        name="audit-{0}".format(number),
        as_of=NOW,
        time=TIME,
        arrays=[
            array_reference(
                array,
                id="array-id-{0}".format(array[-1]),
                fqdn="{0}.example.com".format(array),
                resource_type="arrays",
            )
        ],
        user="pureuser",
        command="purevol",
        subcommand="destroy",
        arguments="vol1",
        origin="array",
    )
    fields.update(overrides)
    return sdk_model("Audit", **fields)


def _blade_audit():
    return _audit(
        2,
        time=TIME - 1000,
        arrays=[array_reference("array2", id="array-id-2", resource_type="arrays")],
        user="svc-automation",
        command="purefs",
        subcommand="snapshot destroy",
        arguments="fs1.daily",
        origin="Pure1",
    )


def _row(number=1, **overrides):
    return dict(ROW, id="audit-{0}".format(number), **overrides)


def _array(name, array_id):
    return sdk_model("Array", name=name, id=array_id)


def _run(pure1_client, audits=(), page_size=1000, arrays=None, **params):
    """Run main() against Pure1 holding these audits, return the module and the answers

    arrays is what Pure1 answers to the request that resolves the option
    array.
    """
    answers = paged_answers("AuditsGetResponse", list(audits), page_size=page_size)
    pure1_client.get_audits.side_effect = answers
    if arrays is not None:
        pure1_client.get_arrays.side_effect = [valid_response(), valid_response(arrays)]
    return run_main(pure1_audits, **params), answers


def _answer(pure1_client, audits, **params):
    """Run main() against one answer that holds these audits as they are"""
    pure1_client.get_audits.return_value = valid_response(audits)
    return run_main(pure1_audits, **params)


def _result(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_audits"]


def _filter(pure1_client, **params):
    """Return the filter of the one get_audits request the params lead to"""
    module, answers = _run(pure1_client, [], **params)
    module.fail_json.assert_not_called()
    (request,) = answers.requests
    assert request == {"filter": request["filter"], "sort": SORT, "limit": 1000}
    return request["filter"]


def _refused(pure1_client, client_factory, **params):
    """Return the message of a task that fails before it asks Pure1 for anything"""
    module, answers = _run(pure1_client, [_audit()], **params)
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    client_factory.assert_not_called()
    assert answers.requests == []
    return module.fail_json.call_args[1]["msg"]


class TestPure1AuditsMain:
    """The whole result of main()."""

    def test_typical_answer(self, pure1_client):
        module, answers = _run(pure1_client, [_audit(), _blade_audit()])

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_audits={
                "total": 2,
                "returned": 2,
                "truncated": False,
                "start": "2021-02-02 14:04:05 UTC",
                "start_epoch": 1612274645678,
                "end": "2021-02-03 14:04:05 UTC",
                "end_epoch": 1612361045678,
                "by_array": {"array1": 1, "array2": 1},
                "by_user": {"pureuser": 1, "svc-automation": 1},
                "records": [
                    {
                        "id": "audit-1",
                        "time": "2021-02-03 13:04:05 UTC",
                        "time_epoch": 1612357445000,
                        "array": {
                            "name": "array1",
                            "id": "array-id-1",
                            "fqdn": "array1.example.com",
                        },
                        "user": "pureuser",
                        "command": "purevol",
                        "subcommand": "destroy",
                        "arguments": "vol1",
                        "origin": "array",
                    },
                    {
                        "id": "audit-2",
                        "time": "2021-02-03 13:04:04 UTC",
                        "time_epoch": 1612357444000,
                        "array": {"name": "array2", "id": "array-id-2", "fqdn": None},
                        "user": "svc-automation",
                        "command": "purefs",
                        "subcommand": "snapshot destroy",
                        "arguments": "fs1.daily",
                        "origin": "Pure1",
                    },
                ],
            },
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        # the check of get_pure1, and no other request for arrays
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_audits.assert_called_once_with(
            filter=PERIOD, sort=["time-", "id"], limit=1000
        )
        assert answers.requests == [
            {"filter": PERIOD, "sort": ["time-", "id"], "limit": 1000}
        ]

    def test_check_mode_reads_the_same(self, pure1_client):
        module, answers = _run(
            pure1_client, [_audit(), _blade_audit()], check_mode=True
        )

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_audits=dict(
                EMPTY_RESULT,
                total=2,
                returned=2,
                by_array={"array1": 1, "array2": 1},
                by_user={"pureuser": 1, "svc-automation": 1},
                records=[ROW, BLADE_ROW],
            ),
        )
        assert answers.requests == [{"filter": PERIOD, "sort": SORT, "limit": 1000}]

    def test_period_without_records_is_no_failure(self, pure1_client):
        module, answers = _run(pure1_client, [])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_audits=EMPTY_RESULT
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        assert answers.requests == [{"filter": PERIOD, "sort": SORT, "limit": 1000}]

    @pytest.mark.usefixtures("localtime_ahead_of_utc", "host_west_of_utc")
    def test_times_are_utc_whatever_the_zone_of_the_host(self, pure1_client):
        result = _result(_run(pure1_client, [_audit()])[0])

        assert result["start"] == "2021-02-02 14:04:05 UTC"
        assert result["end"] == "2021-02-03 14:04:05 UTC"
        assert result["records"][0]["time"] == "2021-02-03 13:04:05 UTC"


class TestRecord:
    """What a record holds for what Pure1 sends."""

    def test_audit_with_only_its_id(self, pure1_client):
        module = _answer(pure1_client, [sdk_model("Audit", id="audit-1")])

        result = _result(module)
        assert result["records"] == [dict(BARE_ROW, id="audit-1")]
        assert (result["total"], result["returned"]) == (1, 1)
        assert result["by_array"] == {}
        assert result["by_user"] == {}
        assert module.warn.call_args_list == [
            call(WITHOUT_ARRAY.format(1)),
            call(WITHOUT_USER.format(1)),
        ]

    def test_audits_without_an_id_are_all_returned(self, pure1_client):
        module = _answer(pure1_client, [sdk_model("Audit"), sdk_model("Audit")])

        result = _result(module)
        assert result["records"] == [BARE_ROW, BARE_ROW]
        assert (result["total"], result["returned"]) == (2, 2)
        assert result["truncated"] is False
        assert module.warn.call_args_list == [
            call(WITHOUT_ARRAY.format(2)),
            call(WITHOUT_USER.format(2)),
        ]

    def test_values_that_are_empty_or_zero_are_kept(self, pure1_client):
        audit = sdk_model(
            "Audit",
            id="",
            time=0,
            arrays=[array_reference("", id="", fqdn="")],
            user="",
            command="",
            subcommand="",
            arguments="",
            origin="",
        )
        module = _answer(pure1_client, [audit])

        result = _result(module)
        assert result["records"] == [
            {
                "id": "",
                "time": "1970-01-01 00:00:00 UTC",
                "time_epoch": 0,
                "array": {"name": "", "id": "", "fqdn": ""},
                "user": "",
                "command": "",
                "subcommand": "",
                "arguments": "",
                "origin": "",
            }
        ]
        assert result["by_array"] == {"": 1}
        assert result["by_user"] == {"": 1}
        module.warn.assert_not_called()

    def test_array_reference_with_a_name_only(self, pure1_client):
        module = _answer(pure1_client, [_audit(arrays=[array_reference("array1")])])

        result = _result(module)
        assert result["records"] == [
            _row(array={"name": "array1", "id": None, "fqdn": None})
        ]
        assert result["by_array"] == {"array1": 1}
        module.warn.assert_not_called()

    def test_first_of_two_arrays_is_the_array_of_the_record(self, pure1_client):
        audit = _audit(
            arrays=[
                array_reference("array1", id="array-id-1"),
                array_reference("array2", id="array-id-2"),
            ]
        )

        result = _result(_answer(pure1_client, [audit]))

        assert result["records"][0]["array"] == {
            "name": "array1",
            "id": "array-id-1",
            "fqdn": None,
        }
        assert result["by_array"] == {"array1": 1}

    @pytest.mark.parametrize("broken", ["no arrays", "empty arrays", "null array"])
    def test_record_without_an_array_is_returned_and_not_counted(
        self, pure1_client, broken
    ):
        audit = _audit(2)
        if broken == "no arrays":
            audit = _audit(2, arrays=None)
        elif broken == "empty arrays":
            # The SDK reads an answer without validating it
            del audit.arrays[:]
        else:
            audit.arrays[0] = None
        module = _answer(pure1_client, [_audit(), audit])

        result = _result(module)
        assert result["records"] == [
            ROW,
            _row(2, array={"name": None, "id": None, "fqdn": None}),
        ]
        assert result["returned"] == 2
        assert result["by_array"] == {"array1": 1}
        assert result["by_user"] == {"pureuser": 2}
        module.warn.assert_called_once_with(WITHOUT_ARRAY.format(1))

    def test_null_entry_before_the_array_is_passed_over(self, pure1_client):
        """The SDK reads an answer without validating it"""
        audit = _audit(arrays=[array_reference("array2", id="array-id-2")])
        audit.arrays.insert(0, None)
        module = _answer(pure1_client, [audit])

        result = _result(module)
        assert result["records"] == [
            _row(array={"name": "array2", "id": "array-id-2", "fqdn": None})
        ]
        assert result["by_array"] == {"array2": 1}
        module.warn.assert_not_called()

    def test_array_reference_without_a_name_is_not_counted(self, pure1_client):
        audit = _audit(arrays=[sdk_model("FixedReferenceFqdn", id="array-id-1")])
        module = _answer(pure1_client, [audit])

        result = _result(module)
        assert result["records"][0]["array"] == {
            "name": None,
            "id": "array-id-1",
            "fqdn": None,
        }
        assert result["by_array"] == {}
        module.warn.assert_called_once_with(WITHOUT_ARRAY.format(1))

    def test_record_without_a_user_is_returned_and_not_counted(self, pure1_client):
        module = _answer(
            pure1_client, [_audit(user=None), _audit(2), _audit(3, user=None)]
        )

        result = _result(module)
        assert [record["user"] for record in result["records"]] == [
            None,
            "pureuser",
            None,
        ]
        assert result["by_user"] == {"pureuser": 1}
        assert result["by_array"] == {"array1": 3}
        module.warn.assert_called_once_with(WITHOUT_USER.format(2))


class TestCounts:
    """by_array and by_user."""

    def test_records_are_counted_by_array_and_by_user(self, pure1_client):
        audits = [
            _audit(1),
            _audit(2, array="array2", user="root"),
            _audit(3, user="root"),
            _audit(4, array="array2", user="root"),
            _audit(5, array="array3", user="Root"),
        ]

        result = _result(_run(pure1_client, audits)[0])

        assert result["by_array"] == {"array1": 2, "array2": 2, "array3": 1}
        assert result["by_user"] == {"pureuser": 1, "root": 3, "Root": 1}
        assert result["returned"] == 5

    def test_counts_are_of_the_records_returned(self, pure1_client):
        audits = [_audit(1), _audit(2, array="array2"), _audit(3, user="root")]

        result = _result(_run(pure1_client, audits, limit=2)[0])

        assert result["total"] == 3
        assert result["by_array"] == {"array1": 1, "array2": 1}
        assert result["by_user"] == {"pureuser": 2}

    def test_array_seen_first_keeps_a_name_that_two_arrays_carry(self, pure1_client):
        audits = [
            _audit(1),
            _audit(2, arrays=[array_reference("array1", id="other-id")]),
            _audit(3, array="array2"),
            _audit(4, arrays=[array_reference("array2", id="third-id")]),
            _audit(5, array="array3"),
            _audit(6),
            _audit(7, arrays=[array_reference("array1", id="other-id")]),
        ]
        module = _run(pure1_client, audits)[0]

        result = _result(module)
        assert result["by_array"] == {"array1": 2, "array2": 1, "array3": 1}
        # every record is returned, and its array id tells the arrays apart
        assert [record["array"]["id"] for record in result["records"]] == [
            "array-id-1",
            "other-id",
            "array-id-2",
            "third-id",
            "array-id-3",
            "array-id-1",
            "other-id",
        ]
        assert (result["total"], result["returned"]) == (7, 7)
        assert result["truncated"] is False
        assert result["by_user"] == {"pureuser": 7}
        module.warn.assert_called_once_with(NAMESAKE.format(3))

    def test_one_record_of_a_second_array_of_one_name(self, pure1_client):
        audits = [
            _audit(1, arrays=[array_reference("array1", id="other-id")]),
            _audit(2),
        ]
        module = _run(pure1_client, audits)[0]

        assert _result(module)["by_array"] == {"array1": 1}
        module.warn.assert_called_once_with(NAMESAKE.format(1))

    def test_reference_without_an_id_is_not_another_array(self, pure1_client):
        audits = [_audit(1), _audit(2, arrays=[array_reference("array1")])]
        module = _run(pure1_client, audits)[0]

        assert _result(module)["by_array"] == {"array1": 2}
        module.warn.assert_not_called()

    def test_reference_without_an_id_does_not_take_the_name(self, pure1_client):
        audits = [
            _audit(1, arrays=[array_reference("array1")]),
            _audit(2),
            _audit(3, arrays=[array_reference("array1", id="other-id")]),
        ]
        module = _run(pure1_client, audits)[0]

        assert _result(module)["by_array"] == {"array1": 2}
        module.warn.assert_called_once_with(NAMESAKE.format(1))

    def test_every_warning_at_once(self, pure1_client):
        audits = [
            _audit(1),
            _audit(2, arrays=None),
            _audit(3, arrays=[array_reference("array1", id="other-id")]),
            _audit(4, user=None),
        ]
        module = _run(pure1_client, audits)[0]

        result = _result(module)
        assert result["by_array"] == {"array1": 2}
        assert result["by_user"] == {"pureuser": 3}
        assert module.warn.call_args_list == [
            call(WITHOUT_ARRAY.format(1)),
            call(NAMESAKE.format(1)),
            call(WITHOUT_USER.format(1)),
        ]


class TestLimit:
    """limit, total, truncated and the requests of a listing."""

    def test_newest_records_are_read_across_pages(self, pure1_client):
        audits = [_audit(number) for number in range(1, 6)]

        module, answers = _run(pure1_client, audits, page_size=2, limit=3)

        assert answers.requests == [
            {"filter": PERIOD, "sort": ["time-", "id"], "limit": 3},
            {"filter": PERIOD, "sort": ["time-", "id"], "limit": 1, "offset": 2},
        ]
        result = _result(module)
        assert result["records"] == [_row(1), _row(2), _row(3)]
        assert (result["total"], result["returned"]) == (5, 3)
        assert result["truncated"] is True

    def test_every_record_is_read_when_the_limit_allows(self, pure1_client):
        audits = [_audit(number) for number in range(1, 6)]

        module, answers = _run(pure1_client, audits, page_size=2, limit=10)

        assert answers.requests == [
            {"filter": PERIOD, "sort": SORT, "limit": 10},
            {"filter": PERIOD, "sort": SORT, "limit": 8, "offset": 2},
            {"filter": PERIOD, "sort": SORT, "limit": 6, "offset": 4},
        ]
        result = _result(module)
        assert [record["id"] for record in result["records"]] == [
            "audit-1",
            "audit-2",
            "audit-3",
            "audit-4",
            "audit-5",
        ]
        assert (result["total"], result["returned"]) == (5, 5)
        assert result["truncated"] is False

    @pytest.mark.parametrize(
        "limit, requests, returned, truncated",
        [
            pytest.param(1, [{"limit": 1}], 1, True, id="below a page"),
            pytest.param(2, [{"limit": 2}], 2, True, id="a page"),
            pytest.param(
                4,
                [{"limit": 4}, {"limit": 2, "offset": 2}],
                4,
                False,
                id="the total",
            ),
            pytest.param(
                5,
                [{"limit": 5}, {"limit": 3, "offset": 2}],
                4,
                False,
                id="above the total",
            ),
        ],
    )
    def test_limit_around_a_page_and_the_total(
        self, pure1_client, limit, requests, returned, truncated
    ):
        audits = [_audit(number) for number in range(1, 5)]

        module, answers = _run(pure1_client, audits, page_size=2, limit=limit)

        assert answers.requests == [
            dict(filter=PERIOD, sort=SORT, **paging) for paging in requests
        ]
        result = _result(module)
        assert (result["total"], result["returned"]) == (4, returned)
        assert result["truncated"] is truncated
        assert len(result["records"]) == returned

    def test_limit_zero_returns_the_count_alone(self, pure1_client):
        audits = [_audit(number) for number in range(1, 6)]

        module, answers = _run(pure1_client, audits, page_size=2, limit=0)

        assert answers.requests == [{"filter": PERIOD, "sort": SORT, "limit": 0}]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_audits=dict(EMPTY_RESULT, total=5, truncated=True),
        )
        module.warn.assert_not_called()

    def test_limit_zero_and_no_record_asks_once_more(self, pure1_client):
        module, answers = _run(pure1_client, [], limit=0)

        assert answers.requests == [
            {"filter": PERIOD, "sort": SORT, "limit": 0},
            {"filter": PERIOD, "sort": SORT, "limit": 1},
        ]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_audits=EMPTY_RESULT
        )

    def test_limit_below_zero_fails_before_any_request(
        self, pure1_client, client_factory
    ):
        message = _refused(pure1_client, client_factory, limit=-1)

        assert message == "limit must be 0 or more, got -1"

    @pytest.mark.parametrize("limit", ["many", None])
    def test_limit_that_is_no_number_is_refused(
        self, pure1_client, client_factory, limit
    ):
        message = _refused(pure1_client, client_factory, limit=limit)

        assert message.startswith("argument 'limit' is of type ")
        assert " and we were unable to convert to int" in message

    def test_limit_of_null_past_the_argument_spec_reads_nothing(self):
        """Without a limit the listing would read every record of the period"""
        module = ansible_module(limit=None)

        with pytest.raises(SystemExit):
            check_options(module)

        module.fail_json.assert_called_once_with(
            msg="limit must be 0 or more, got None"
        )

    def test_record_that_a_later_page_repeats_is_returned_once(self, pure1_client):
        """A record that arrives between two requests moves the next page down"""
        pure1_client.get_audits.side_effect = [
            valid_response([_audit(1), _audit(2)], total_item_count=3),
            valid_response([_audit(2), _audit(3)], total_item_count=4),
        ]

        module = run_main(pure1_audits, limit=10)

        assert pure1_client.get_audits.call_args_list == [
            call(filter=PERIOD, sort=SORT, limit=10),
            call(filter=PERIOD, sort=SORT, limit=8, offset=2),
        ]
        result = _result(module)
        assert result["records"] == [_row(1), _row(2), _row(3)]
        assert (result["total"], result["returned"]) == (3, 3)
        assert result["truncated"] is False
        assert result["by_user"] == {"pureuser": 3}

    def test_record_is_told_from_another_by_its_id_alone(self, pure1_client):
        """Pure1 names a record by its id, and the name is not what is compared"""
        pure1_client.get_audits.side_effect = [
            valid_response(
                [_audit(1, name="one name"), _audit(2, name="one name")],
                total_item_count=3,
            ),
            valid_response(
                [_audit(2, name="another name"), _audit(3, name="one name")],
                total_item_count=4,
            ),
        ]

        result = _result(run_main(pure1_audits, limit=10))

        assert result["records"] == [_row(1), _row(2), _row(3)]
        assert (result["total"], result["returned"]) == (3, 3)
        assert result["truncated"] is False

    def test_repeated_record_leaves_a_limited_result_one_short(self, pure1_client):
        pure1_client.get_audits.side_effect = [
            valid_response([_audit(1), _audit(2)], total_item_count=5),
            valid_response([_audit(2), _audit(3)], total_item_count=6),
        ]

        result = _result(run_main(pure1_audits, limit=4))

        assert result["records"] == [_row(1), _row(2), _row(3)]
        assert (result["total"], result["returned"]) == (5, 3)
        assert result["truncated"] is True

    def test_repeated_record_leaves_a_whole_listing_one_short(self, pure1_client):
        """The last of the records Pure1 counted was moved past the last page"""
        pure1_client.get_audits.side_effect = [
            valid_response([_audit(1), _audit(2)], total_item_count=4),
            valid_response([_audit(2), _audit(3)], total_item_count=5),
        ]

        result = _result(run_main(pure1_audits, limit=10))

        assert pure1_client.get_audits.call_count == 2
        assert result["records"] == [_row(1), _row(2), _row(3)]
        assert (result["total"], result["returned"]) == (4, 3)
        assert result["truncated"] is True

    def test_answer_without_a_total(self, pure1_client):
        answers = paged_answers(
            "AuditsGetResponse", [_audit(1), _audit(2)], total_item_count=None
        )
        pure1_client.get_audits.side_effect = answers

        result = _result(run_main(pure1_audits))

        assert answers.requests == [
            {"filter": PERIOD, "sort": SORT, "limit": 1000},
            {"filter": PERIOD, "sort": SORT, "limit": 998, "offset": 2},
        ]
        assert result["total"] is None
        assert result["returned"] == 2
        assert result["truncated"] is False

    def test_answer_without_a_total_cut_by_the_limit(self, pure1_client):
        answers = paged_answers(
            "AuditsGetResponse", [_audit(1), _audit(2)], total_item_count=None
        )
        pure1_client.get_audits.side_effect = answers

        result = _result(run_main(pure1_audits, limit=2))

        assert result["total"] is None
        assert result["returned"] == 2
        assert result["truncated"] is True


class TestPeriod:
    """window, start_time and end_time."""

    def test_default_is_the_day_before_the_module_started(self, pure1_client):
        assert _filter(pure1_client) == "time>=1612274645678 and time<1612361045678"

    def test_window_looks_back_from_the_start_of_the_module(self, pure1_client):
        module, answers = _run(pure1_client, [], window=3600)

        assert answers.requests == [
            {
                "filter": "time>=1612357445678 and time<1612361045678",
                "sort": SORT,
                "limit": 1000,
            }
        ]
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_audits=dict(
                EMPTY_RESULT, start="2021-02-03 13:04:05 UTC", start_epoch=1612357445678
            ),
        )

    def test_window_looks_back_from_end_time(self, pure1_client):
        module, answers = _run(pure1_client, [], window=60, end_time=1612357445000)

        assert answers.requests[0]["filter"] == (
            "time>=1612357385000 and time<1612357445000"
        )
        result = _result(module)
        assert (result["start"], result["start_epoch"]) == (
            "2021-02-03 13:03:05 UTC",
            1612357385000,
        )
        assert (result["end"], result["end_epoch"]) == (
            "2021-02-03 13:04:05 UTC",
            1612357445000,
        )

    def test_start_time_alone_ends_when_the_module_started(self, pure1_client):
        module, answers = _run(pure1_client, [], start_time=1612357445000)

        assert answers.requests[0]["filter"] == (
            "time>=1612357445000 and time<1612361045678"
        )
        result = _result(module)
        assert (result["start_epoch"], result["end_epoch"]) == (1612357445000, NOW)

    def test_start_time_and_end_time(self, pure1_client):
        module, answers = _run(
            pure1_client, [], start_time=1612357445000, end_time=1612357446000
        )

        assert answers.requests[0]["filter"] == (
            "time>=1612357445000 and time<1612357446000"
        )
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_audits=dict(
                EMPTY_RESULT,
                start="2021-02-03 13:04:05 UTC",
                start_epoch=1612357445000,
                end="2021-02-03 13:04:06 UTC",
                end_epoch=1612357446000,
            ),
        )

    def test_times_given_as_text_are_numbers(self, pure1_client):
        """As a template gives them under ansible-core before 2.19"""
        assert _filter(pure1_client, start_time="1000", end_time="2000") == (
            "time>=1000 and time<2000"
        )

    def test_period_of_no_length_is_asked_for(self, pure1_client):
        assert _filter(pure1_client, start_time=0, end_time=0) == "time>=0 and time<0"

    def test_window_longer_than_the_time_since_1970_starts_in_1970(self, pure1_client):
        module, answers = _run(pure1_client, [], window=10, end_time=5000)

        assert answers.requests[0]["filter"] == "time>=0 and time<5000"
        assert _result(module)["start"] == "1970-01-01 00:00:00 UTC"

    def test_start_time_after_end_time_fails_before_any_request(
        self, pure1_client, client_factory
    ):
        message = _refused(pure1_client, client_factory, start_time=2001, end_time=2000)

        assert message == "start_time must not be after end_time: 2001 is after 2000"

    def test_start_time_after_the_start_of_the_module_fails(
        self, pure1_client, client_factory
    ):
        """The task gave no end_time, so the message names none"""
        message = _refused(pure1_client, client_factory, start_time=NOW + 1)

        assert message == (
            "start_time must not be after the time the module started, by the "
            "clock of its host: 1612361045679 is after 1612361045678"
        )

    @pytest.mark.parametrize("window", [0, -60])
    def test_window_below_one_fails_before_any_request(
        self, pure1_client, client_factory, window
    ):
        message = _refused(pure1_client, client_factory, window=window)

        assert message == "window must be 1 or more, got {0}".format(window)

    def test_window_of_null_is_refused(self, pure1_client, client_factory):
        message = _refused(pure1_client, client_factory, window=None)

        assert message.startswith("argument 'window' is of type ")
        assert " and we were unable to convert to int" in message

    def test_window_of_null_past_the_argument_spec_gives_no_period(self):
        module = ansible_module(window=None, start_time=None, end_time=None)

        with pytest.raises(SystemExit):
            time_range(module)

        module.fail_json.assert_called_once_with(
            msg="window must be 1 or more, got None"
        )

    def test_window_is_not_read_beside_a_start_time(self):
        """The argument spec gives window its default beside a start_time"""
        module = ansible_module(window=86400, start_time=1000, end_time=None)

        assert time_range(module) == (1000, NOW)
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize("option", ["start_time", "end_time"])
    def test_time_before_1970_fails_before_any_request(
        self, pure1_client, client_factory, option
    ):
        message = _refused(pure1_client, client_factory, **{option: -1})

        assert message == "{0} must be 0 or more, got -1".format(option)

    def test_window_and_start_time_are_mutually_exclusive(self, pure1_client):
        module, answers = _run(pure1_client, [_audit()], window=60, start_time=1000)

        module.fail_json.assert_called_once_with(
            msg="parameters are mutually exclusive: window|start_time"
        )
        assert answers.requests == []

    def test_period_that_pure1_finds_too_long_fails_with_its_message(
        self, pure1_client
    ):
        pure1_client.get_audits.return_value = error_response(
            400,
            "Invalid value",
            "The selected time range is longer than maximum 30 days.",
        )

        module = run_main(pure1_audits, window=2592001)

        pure1_client.get_audits.assert_called_once_with(
            filter="time>=1609769044678 and time<1612361045678", sort=SORT, limit=1000
        )
        module.fail_json.assert_called_once_with(msg=TOO_LONG)
        module.exit_json.assert_not_called()


class TestFilters:
    """The filter each option adds, and the values Pure1 cannot take."""

    @pytest.mark.parametrize(
        "params, term",
        [
            pytest.param({"user": "pureuser"}, "user='pureuser'", id="user"),
            pytest.param(
                {"user": "o'brien"}, "user='o\\'brien'", id="user with a quote"
            ),
            pytest.param(
                {"exclude_users": ["svc-automation"]},
                "not(user='svc-automation')",
                id="one user left out",
            ),
            pytest.param(
                {"exclude_users": ["svc-automation", "root", "root"]},
                "not(user=('svc-automation','root'))",
                id="users left out",
            ),
            pytest.param({"command": "purevol"}, "command='purevol'", id="command"),
            pytest.param(
                {"command": "o'x"}, "command='o\\'x'", id="command with a quote"
            ),
            pytest.param(
                {"subcommand": ["destroy"]},
                "subcommand='destroy'",
                id="one subcommand",
            ),
            pytest.param(
                {"subcommand": ["destroy", "eradicate"]},
                "subcommand=('destroy','eradicate')",
                id="subcommands",
            ),
            pytest.param(
                {"subcommand": ["o'x", "o'x"]},
                "subcommand='o\\'x'",
                id="one subcommand with a quote, given twice",
            ),
            pytest.param(
                {"subcommand": ["a\\b", 'c"d']},
                "subcommand=('a\\\\b','c\"d')",
                id="subcommands with a backslash and a double quote",
            ),
            pytest.param(
                {"subcommand_contains": "snap"},
                "contains(subcommand,'snap')",
                id="subcommand_contains",
            ),
            pytest.param(
                {"subcommand_contains": " "},
                "contains(subcommand,' ')",
                id="subcommand_contains a blank",
            ),
            pytest.param(
                {"arguments": "vol1"}, "contains(arguments,'vol1')", id="arguments"
            ),
            pytest.param(
                {"arguments": "a\\b or (c"},
                "contains(arguments,'a\\\\b or (c')",
                id="arguments with a backslash, an or and a parenthesis",
            ),
        ],
    )
    def test_option_is_a_term_of_the_filter(self, pure1_client, params, term):
        assert _filter(pure1_client, **params) == term + " and " + PERIOD

    def test_every_option_at_once(self, pure1_client):
        module, answers = _run(
            pure1_client,
            [_audit()],
            arrays=[_array("array1", "array-id-1")],
            array=["array1"],
            user="pureuser",
            command="purevol",
            subcommand=["destroy", "eradicate"],
            arguments="vol1",
            start_time=1612357445000,
            end_time=1612357446000,
            limit=5,
        )

        assert answers.requests == [
            {
                "filter": "arrays.id='array-id-1' and user='pureuser' and "
                "command='purevol' and subcommand=('destroy','eradicate') and "
                "contains(arguments,'vol1') and time>=1612357445000 and "
                "time<1612357446000",
                "sort": SORT,
                "limit": 5,
            }
        ]
        assert _result(module)["records"] == [ROW]

    def test_the_other_two_options_at_once(self, pure1_client):
        assert _filter(
            pure1_client, exclude_users=["root"], subcommand_contains="snap"
        ) == ("not(user='root') and contains(subcommand,'snap') and " + PERIOD)

    @pytest.mark.parametrize("option", ["array", "exclude_users", "subcommand"])
    def test_empty_list_counts_as_not_set(self, pure1_client, option):
        assert _filter(pure1_client, **{option: []}) == PERIOD
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param(
                {"user": "root", "exclude_users": ["pureuser"]},
                "user|exclude_users",
                id="user and exclude_users",
            ),
            pytest.param(
                {"subcommand": ["destroy"], "subcommand_contains": "snap"},
                "subcommand|subcommand_contains",
                id="subcommand and subcommand_contains",
            ),
            pytest.param(
                {"user": "root", "exclude_users": []},
                "user|exclude_users",
                id="user and an empty exclude_users",
            ),
            pytest.param(
                {"subcommand": [], "subcommand_contains": "snap"},
                "subcommand|subcommand_contains",
                id="an empty subcommand and subcommand_contains",
            ),
        ],
    )
    def test_options_that_are_mutually_exclusive(self, pure1_client, params, expected):
        module, answers = _run(pure1_client, [_audit()], **params)

        module.fail_json.assert_called_once_with(
            msg="parameters are mutually exclusive: " + expected
        )
        assert answers.requests == []

    @pytest.mark.parametrize(
        "option", ["user", "command", "subcommand_contains", "arguments"]
    )
    def test_empty_text_fails_before_any_request(
        self, pure1_client, client_factory, option
    ):
        message = _refused(pure1_client, client_factory, **{option: ""})

        assert message == "{0} must not be empty.".format(option)

    @pytest.mark.parametrize("option", ["exclude_users", "subcommand"])
    def test_empty_value_in_a_list_fails_before_any_request(
        self, pure1_client, client_factory, option
    ):
        message = _refused(pure1_client, client_factory, **{option: ["root", ""]})

        assert message == "{0} must not hold an empty value.".format(option)

    @pytest.mark.parametrize(
        "users",
        [
            pytest.param(["o'brien"], id="alone"),
            pytest.param(["root", "o'brien"], id="beside another"),
        ],
    )
    def test_user_with_a_quote_cannot_be_left_out(
        self, pure1_client, client_factory, users
    ):
        message = _refused(pure1_client, client_factory, exclude_users=users)

        assert message == EXCLUDE_QUOTE

    @pytest.mark.parametrize(
        "subcommands",
        [
            pytest.param(["destroy", "o'x"], id="last"),
            pytest.param(["o'x", "destroy"], id="first"),
            pytest.param(["o'x", "a'b"], id="both"),
        ],
    )
    def test_several_subcommands_of_which_one_has_a_quote_fail(
        self, pure1_client, client_factory, subcommands
    ):
        message = _refused(pure1_client, client_factory, subcommand=subcommands)

        assert message == SEVERAL_QUOTE

    @pytest.mark.parametrize("option", ["subcommand_contains", "arguments"])
    def test_text_with_a_quote_cannot_be_searched_for(
        self, pure1_client, client_factory, option
    ):
        message = _refused(pure1_client, client_factory, **{option: "it's"})

        assert message == SEARCH_QUOTE.format(option)

    @pytest.mark.parametrize("option", ["filter", "sort", "name"])
    def test_option_the_module_does_not_have_is_refused(self, pure1_client, option):
        module, answers = _run(pure1_client, [_audit()], **{option: "id"})

        message = module.fail_json.call_args[1]["msg"]
        assert message.startswith(
            "Unsupported parameters for module: {0}.".format(option)
        )
        assert answers.requests == []


class TestArray:
    """The option array."""

    def test_arrays_are_resolved_and_selected_by_their_id(self, pure1_client):
        module, answers = _run(
            pure1_client,
            [_audit(arrays=[array_reference("Array1", id="array-id-1")])],
            arrays=[_array("Array1", "array-id-1"), _array("array2", "array-id-2")],
            array=["ARRAY1", "array2"],
        )

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY1','array2')"),
        ]
        assert answers.requests == [
            {
                "filter": "arrays.id=('array-id-1','array-id-2') and " + PERIOD,
                "sort": SORT,
                "limit": 1000,
            }
        ]
        result = _result(module)
        # the name as Pure1 spells it
        assert result["by_array"] == {"Array1": 1}
        assert result["records"][0]["array"]["name"] == "Array1"
        module.warn.assert_not_called()

    def test_one_array(self, pure1_client):
        module, answers = _run(
            pure1_client,
            [],
            arrays=[_array("array1", "array-id-1")],
            array=["array1"],
            limit=0,
        )

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name='array1'"),
        ]
        assert answers.requests[0]["filter"] == "arrays.id='array-id-1' and " + PERIOD
        assert _result(module) == EMPTY_RESULT

    def test_array_without_an_id_selects_by_the_names_of_pure1(self, pure1_client):
        module, answers = _run(
            pure1_client,
            [],
            arrays=[
                _array("Array1", "array-id-1"),
                sdk_model("Array", name="array2"),
            ],
            array=["array1", "ARRAY2"],
        )

        module.fail_json.assert_not_called()
        assert answers.requests == [
            {
                "filter": "arrays.name=('Array1','array2') and " + PERIOD,
                "sort": SORT,
                "limit": 1000,
            }
        ]

    def test_array_that_pure1_does_not_know_fails_the_task(self, pure1_client):
        module, answers = _run(
            pure1_client,
            [_audit()],
            arrays=[_array("array1", "array-id-1")],
            array=["array1", "foo"],
        )

        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")
        module.exit_json.assert_not_called()
        assert answers.requests == []

    def test_failed_request_for_the_arrays_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(403, "Access denied"),
        ]

        module = run_main(pure1_audits, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 403): Access denied"
        )
        pure1_client.get_audits.assert_not_called()


class TestFailures:
    """A request that fails."""

    @pytest.mark.parametrize("params", [{}, {"limit": 0}], ids=["records", "count"])
    @pytest.mark.parametrize(
        "attribute, answer, expected",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                "Pure1 request get_audits failed (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(500, "Internal server error"),
                "Pure1 request get_audits failed (HTTP 500): Internal server error",
                id="internal error",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 7"
                ),
                "Pure1 request get_audits failed (HTTP 400): Illegal filter "
                "format: Unexpected input at character 7",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                "Pure1 request get_audits failed (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                "Pure1 request get_audits failed: PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                "Pure1 request get_audits failed: "
                "TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, params, attribute, answer, expected
    ):
        setattr(pure1_client.get_audits, attribute, answer)

        module = run_main(pure1_audits, **params)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_failed_request_for_a_later_page_fails_the_task(self, pure1_client):
        answers = paged_answers(
            "AuditsGetResponse", [_audit(1), _audit(2), _audit(3)], page_size=2
        )

        def answer(**kwargs):
            if "offset" in kwargs:
                return error_response(500, "boom")
            return answers(**kwargs)

        pure1_client.get_audits.side_effect = answer

        module = run_main(pure1_audits)

        assert pure1_client.get_audits.call_args_list == [
            call(filter=PERIOD, sort=SORT, limit=1000),
            call(filter=PERIOD, sort=SORT, limit=998, offset=2),
        ]
        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_audits failed (HTTP 500): boom"
        )
        module.exit_json.assert_not_called()

    def test_failed_check_of_the_client_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.return_value = error_response(401, "Unauthorized")

        module = run_main(pure1_audits)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 401): Unauthorized"
        )
        pure1_client.get_audits.assert_not_called()


def _template(text, **variables):
    """Return what ansible-core makes of a template of a task"""
    return Templar(loader=None, variables=variables).template(trust_as_template(text))


def _play_examples(pure1_client, audits):
    """Run the tasks of EXAMPLES in their order against Pure1 holding these audits

    Returns what each task that is no module task reads, by the name of the
    task, and the requests for audits.
    """

    def arrays(**kwargs):
        if kwargs == {"limit": 0}:
            return valid_response()
        return valid_response(
            [_array("array1", "array-id-1"), _array("array2", "array-id-2")]
        )

    pure1_client.get_arrays.side_effect = arrays
    answers = paged_answers("AuditsGetResponse", list(audits))
    pure1_client.get_audits.side_effect = answers
    registered = {}
    read = {}
    for task in yaml.safe_load(pure1_audits.EXAMPLES):
        if MODULE in task:
            params = {
                name: _template(value) if isinstance(value, str) else value
                for name, value in task[MODULE].items()
                if name not in ("app_id", "key_file")
            }
            module = run_main(pure1_audits, **params)
            registered[task["register"]] = {"pure1_audits": _result(module)}
        elif "ansible.builtin.assert" in task:
            read[task["name"]] = [
                _template("{{ " + condition + " }}", **registered)
                for condition in task["ansible.builtin.assert"]["that"]
            ]
        elif "loop" in task:
            read[task["name"]] = [
                _template(task["ansible.builtin.debug"]["msg"], item=item)
                for item in _template(task["loop"], **registered)
            ]
        else:
            read[task["name"]] = _template(
                task["ansible.builtin.debug"]["msg"], **registered
            )
    return read, answers.requests


class TestDocumentation:
    """RETURN, EXAMPLES and DOCUMENTATION against what the module does."""

    def test_return_documents_the_keys_of_the_result(self, pure1_client):
        documented = yaml.safe_load(pure1_audits.RETURN)["pure1_audits"]
        keys = documented["contains"]

        result = _result(_run(pure1_client, [_audit()])[0])

        assert list(result) == list(keys)
        (record,) = result["records"]
        assert list(record) == list(keys["records"]["contains"])
        assert list(record["array"]) == list(
            keys["records"]["contains"]["array"]["contains"]
        )
        assert list(BARE_ROW) == list(record)

    def test_sample_is_a_result_the_module_can_return(self):
        documented = yaml.safe_load(pure1_audits.RETURN)["pure1_audits"]
        keys = documented["contains"]
        sample = documented["sample"]

        assert list(sample) == list(keys)
        for record in sample["records"]:
            assert list(record) == list(keys["records"]["contains"])
            assert list(record["array"]) == ["name", "id", "fqdn"]
            assert record["time"] == pure1_audits.pure1_time(record["time_epoch"])
        assert sample["returned"] == len(sample["records"])
        assert sample["truncated"] is (sample["returned"] < sample["total"])
        assert sample["start"] == pure1_audits.pure1_time(sample["start_epoch"])
        assert sample["end"] == pure1_audits.pure1_time(sample["end_epoch"])
        assert sample["by_array"] == {"array1": 1, "array2": 1}
        assert sample["by_user"] == {"pureuser": 2}
        assert [record["array"]["name"] for record in sample["records"]] == [
            "array1",
            "array2",
        ]
        assert [record["user"] for record in sample["records"]] == ["pureuser"] * 2

    @pytest.mark.parametrize("block", ["DOCUMENTATION", "RETURN"])
    def test_ansible_doc_prints_the_documentation_as_it_is(self, block):
        documented = yaml.safe_load(getattr(pure1_audits, block))
        printed = yaml.dump(documented, default_flow_style=False, width=1000)

        assert re.findall(RST_DIRECTIVE, printed) == []

    @pytest.mark.parametrize("option", ["array", "exclude_users", "subcommand"])
    def test_documentation_says_what_an_empty_list_selects(self, option):
        """test_empty_list_counts_as_not_set pins what it does"""
        options = yaml.safe_load(pure1_audits.DOCUMENTATION)["options"]

        assert "when it is not set or empty." in options[option]["description"][
            0
        ].replace("\n", " ")

    @pytest.mark.parametrize(
        "option, partner",
        [("exclude_users", "user"), ("subcommand", "subcommand_contains")],
        ids=["exclude_users", "subcommand"],
    )
    def test_documentation_says_that_an_empty_list_still_excludes(
        self, option, partner
    ):
        """test_options_that_are_mutually_exclusive pins what it does"""
        options = yaml.safe_load(pure1_audits.DOCUMENTATION)["options"]
        text = " ".join(options[option]["description"]).replace("\n", " ")

        assert (
            "Mutually exclusive with I({0}), also when it is empty.".format(partner)
            in text
        )

    @pytest.mark.parametrize(
        "key, text",
        [
            # test_answer_without_a_total
            pytest.param("total", "null when Pure1 does not report it.", id="total"),
            # test_repeated_record_leaves_a_whole_listing_one_short
            pytest.param(
                "truncated",
                "Either I(limit) cut the result, or the records in Pure1 "
                "changed while the module read them page by page.",
                id="truncated",
            ),
            # test_array_seen_first_keeps_a_name_that_two_arrays_carry
            pytest.param(
                "by_array",
                "Where two arrays carry one name, the records of the array that "
                "comes first under C(records) are counted, and the module gives "
                "a warning that counts the records of the other one.",
                id="by_array",
            ),
        ],
    )
    def test_return_says_what_a_key_holds_in_a_rare_answer(self, key, text):
        keys = yaml.safe_load(pure1_audits.RETURN)["pure1_audits"]["contains"]
        description = keys[key]["description"]
        if isinstance(description, list):
            description = " ".join(description)

        assert text in description.replace("\n", " ")

    @pytest.mark.parametrize("option", ["window", "start_time"])
    def test_documentation_names_the_longest_period_of_pure1(self, option):
        options = yaml.safe_load(pure1_audits.DOCUMENTATION)["options"]
        text = " ".join(options[option]["description"]).replace("\n", " ")

        assert "Pure1 refuses a period longer than 30 days" in text

    def test_documented_defaults_are_those_of_a_run_without_options(self, pure1_client):
        options = yaml.safe_load(pure1_audits.DOCUMENTATION)["options"]

        module, answers = _run(pure1_client, [])

        assert options["window"]["default"] == 86400
        assert options["limit"]["default"] == 1000
        assert module.params["window"] == 86400
        assert module.params["limit"] == 1000
        assert NOW - DAY_AGO == 86400 * 1000
        assert answers.requests[0]["limit"] == 1000

    def test_examples_read_what_the_module_returns(self, pure1_client):
        read, requests = _play_examples(
            pure1_client, [_audit(), _audit(2, array="array2")]
        )

        last_week = "time>={0} and time<{1}".format(NOW - 604800000, NOW)
        twelve_hours = "time>={0} and time<{1}".format(NOW - 43200000, NOW)
        assert [dict(request, filter=None) for request in requests] == [
            {"filter": None, "sort": SORT, "limit": 1000},
            {"filter": None, "sort": SORT, "limit": 0},
            {"filter": None, "sort": SORT, "limit": 1000},
            {"filter": None, "sort": SORT, "limit": 1000},
            {"filter": None, "sort": SORT, "limit": 0},
        ]
        filters = [request["filter"] for request in requests]
        assert filters[0] == PERIOD
        assert filters[1] == (
            "not(user=('svc-automation','svc-backup')) and " + last_week
        )
        assert filters[2] == (
            "arrays.id=('array-id-1','array-id-2') and "
            "subcommand=('destroy','eradicate') and " + PERIOD
        )
        found = re.fullmatch(
            r"command='purevol' and contains\(arguments,'vol1'\) and "
            r"time>=(\d+) and time<(\d+)",
            filters[3],
        )
        start, end = int(found.group(1)), int(found.group(2))
        # two hours, to the second, that ended three hours before the real time
        assert end - start in (7200000, 7201000)
        assert start % 1000 == end % 1000 == 0
        assert filters[4] == (
            "user='pureuser' and contains(subcommand,'snap') and " + twelve_hours
        )
        assert read == {
            "Show how many of the records returned each user caused": {"pureuser": 2},
            "Check that nobody but the service users ran a command": [False],
            "Show who destroyed or eradicated what": [
                "2021-02-03 13:04:05 UTC array1 pureuser: purevol destroy vol1",
                "2021-02-03 13:04:05 UTC array2 pureuser: purevol destroy vol1",
            ],
            "Check that every record of those two hours was read": [True],
            "Show the number of snapshot commands": read[
                "Show the number of snapshot commands"
            ],
        }
        # a number as text under ansible-core before 2.19
        assert str(read["Show the number of snapshot commands"]) == "2"

    def test_examples_on_an_organization_without_records(self, pure1_client):
        read, requests = _play_examples(pure1_client, [])

        # a count of nothing is asked for once more
        assert [request["limit"] for request in requests] == [
            1000,
            0,
            1,
            1000,
            1000,
            0,
            1,
        ]
        assert read["Show how many of the records returned each user caused"] == {}
        assert read["Check that nobody but the service users ran a command"] == [True]
        assert read["Show who destroyed or eradicated what"] == []
        assert read["Check that every record of those two hours was read"] == [True]
        assert str(read["Show the number of snapshot commands"]) == "0"
