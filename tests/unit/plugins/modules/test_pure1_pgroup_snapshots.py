# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_pgroup_snapshots module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

import pypureclient
import pytest
import yaml
from ansible.module_utils.common.arg_spec import ArgumentSpecValidator
from ansible.template import Templar
from pypureclient import ErrorResponse, PureError

try:
    from ansible.template import trust_as_template
except ImportError:
    # ansible-core before 2.19 templates every string
    def trust_as_template(value):
        return value


from plugins.modules import pure1_pgroup_snapshots
from plugins.modules.pure1_pgroup_snapshots import (
    generate_snapshots_dict,
    id_chunks,
    read_transfers,
    split_name,
)
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
# 2021-02-03 13:04:05 UTC
CREATED = 1612357445000
# 2021-02-03 13:06:12 UTC
AS_OF = 1612357572000
BOUND = "created<1612361045678"
SORT = ["created-", "id"]
LEFT_OUT = (
    "Protection group snapshots that Pure1 returned without a name or "
    "without the array they are stored on are left out of the result: "
)
OTHER_ARRAY = (
    "Protection group snapshots that are stored on an array with the name of "
    "another array are left out of the result: "
)
SNAPSHOTS_FAILED = "Pure1 request get_protection_group_snapshots failed"
TRANSFERS_FAILED = "Pure1 request get_protection_group_snapshots_transfer failed"


@pytest.fixture(autouse=True)
def _module_starts_at_a_fixed_time(monkeypatch):
    monkeypatch.setattr(pure1_pgroup_snapshots.time, "time", lambda: NOW / 1000.0)


def _snapshot(
    suffix,
    created=CREATED,
    array="array1",
    group="pg1",
    source_array="array2",
    pod=None,
    **overrides
):
    """Return a snapshot of a protection group as Pure1 gives it

    Its name is source_array:group.suffix, or pod:group.suffix for a group in a pod,
    whose protection group is pod::group. The array it is stored on has the
    id that _known() gives the array of that name.
    """
    name = "{0}:{1}.{2}".format(pod or source_array, group, suffix)
    fields = dict(
        id="f0-{0}:{1}:{2}".format(array, name, created),
        name=name,
        created=created,
        as_of=AS_OF,
        on=array_reference(array, id="id-" + array),
        source=sdk_model(
            "FixedReference",
            id="{0}-{1}".format(pod or source_array, group),
            name="{0}::{1}".format(pod, group) if pod else group,
        ),
    )
    if pod:
        fields["pod"] = sdk_model("FixedReference", name=pod)
    fields.update(overrides)
    return sdk_model("ProtectionGroupSnapshot", **fields)


def _transfer(snapshot, **overrides):
    """Return the transfer of a snapshot, done 42.7 seconds after its start"""
    fields = dict(
        id=snapshot.id,
        name=snapshot.name,
        started=snapshot.created + 1000,
        completed=snapshot.created + 43700,
        data_transferred=73400320,
        progress=1.0,
    )
    fields.update(overrides)
    return sdk_model("ProtectionGroupSnapshotTransfer", **fields)


def _known(pure1_client, *names):
    """Answer the check of get_pure1 and then the request that resolves array

    A name stands for the array of that name with the id id-<name>;
    anything else is the array itself.
    """
    arrays = [
        (
            sdk_model("Array", name=name, id="id-" + name)
            if isinstance(name, str)
            else name
        )
        for name in names
    ]
    pure1_client.get_arrays.side_effect = [valid_response(), valid_response(arrays)]


def _refused_by_the_spec(spec, params, **rules):
    """Return the message that ansible-core has for params a spec refuses

    ansible-core words it, and not the same in every release: a test takes
    the text from the release it runs on.
    """
    (message,) = ArgumentSpecValidator(spec, **rules).validate(params).error_messages
    return message


def _latest_only_refused_by_the_spec():
    """Return what ansible-core says of latest_only without protection_group"""
    return _refused_by_the_spec(
        {"latest_only": {"type": "bool"}, "protection_group": {"type": "list"}},
        {"latest_only": True},
        required_if=[["latest_only", True, ["protection_group"]]],
    )


def _answer(pure1_client, snapshots=(), transfers=(), page_size=1000):
    """Answer both listings page by page, whatever their filter"""
    snapshot_answers = paged_answers(
        "ProtectionGroupSnapshotGetResponse", snapshots, page_size=page_size
    )
    transfer_answers = paged_answers(
        "ProtectionGroupSnapshotTransferGetResponse", transfers, page_size=page_size
    )
    pure1_client.get_protection_group_snapshots.side_effect = snapshot_answers
    pure1_client.get_protection_group_snapshots_transfer.side_effect = transfer_answers
    return snapshot_answers, transfer_answers


def _requests(method):
    """Return the keywords of every call of a client method, in order"""
    return [call.kwargs for call in method.call_args_list]


def _comparisons(filter_text):
    """Count the comparisons of a filter as Pure1 does: a tuple of values is one"""
    without_strings = re.sub(r"'(?:\\.|[^'\\])*'", "''", filter_text)
    return len(re.findall(r"contains\(|>=|<|=", without_strings))


def _selects(filter_text, snapshot):
    """Tell whether a filter selects a snapshot by its group and its array

    It reads the terms on source.name, source.id and on.id, each alone or
    inside not(), and takes every other term as met.
    """
    fields = {
        "source.name": snapshot.source.name,
        "source.id": snapshot.source.id,
        "on.id": snapshot.on.id,
    }
    for term in filter_text.split(" and "):
        negated = term.startswith("not(")
        field = term.replace("not(", "", 1).partition("=")[0]
        if field in fields:
            if (fields[field] in re.findall(r"'([^']*)'", term)) is negated:
                return False
    return True


def _answer_as_pure1_filters(pure1_client, snapshots):
    """Answer the listing with the snapshots its filter selects, as Pure1 would

    A filter of more than 20 comparisons is refused, as Pure1 refuses it.
    Returns the list that gets the number of comparisons of every request.
    """
    counts = []

    def answer(**kwargs):
        counts.append(_comparisons(kwargs["filter"]))
        if counts[-1] > 20:
            return error_response(
                400,
                "Too many filters",
                "At most 20 filters allowed but received {0}".format(counts[-1]),
            )
        selected = [
            snapshot for snapshot in snapshots if _selects(kwargs["filter"], snapshot)
        ]
        return valid_response(
            selected[: kwargs["limit"]], total_item_count=len(selected)
        )

    pure1_client.get_protection_group_snapshots.side_effect = answer
    return counts


def _result(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_pgroup_snapshots"]


def _names(result):
    """Return the snapshot names under each array of a result"""
    return {
        array: [snapshot["name"] for snapshot in snapshots]
        for array, snapshots in result["arrays"].items()
    }


def _params(**overrides):
    """Return the options as the argument spec fills them in"""
    params = dict(
        array=None,
        source_array=None,
        protection_group=None,
        window=None,
        latest_only=False,
        incomplete_only=False,
        transfer=True,
        limit=100,
    )
    params.update(overrides)
    return params


def _typical(pure1_client):
    """Three snapshots on two arrays, one of a group in a pod and not finished"""
    newest = _snapshot("4721")
    in_pod = _snapshot("118", CREATED - 3600000, "array2", "pg2", pod="pod1")
    oldest = _snapshot("SP-2-77", CREATED - 7200999, group="pg3", source_array="array3")
    transfers = [
        _transfer(newest),
        sdk_model(
            "ProtectionGroupSnapshotTransfer",
            id=in_pod.id,
            name=in_pod.name,
            started=in_pod.created + 1000,
            data_transferred=0,
            progress=0.0,
        ),
        _transfer(oldest, data_transferred=9600000000, progress=1),
    ]
    return _answer(pure1_client, [newest, in_pod, oldest], transfers)


TYPICAL = {
    "total": 3,
    "returned": 3,
    "truncated": False,
    "missing_protection_groups": [],
    "arrays": {
        "array1": [
            {
                "name": "array2:pg1.4721",
                "id": "f0-array1:array2:pg1.4721:1612357445000",
                "protection_group": "pg1",
                "source_array": "array2",
                "pod": None,
                "suffix": "4721",
                "created": "2021-02-03 13:04:05 UTC",
                "created_epoch": 1612357445000,
                "age_seconds": 3600,
                "as_of": "2021-02-03 13:06:12 UTC",
                "as_of_epoch": 1612357572000,
                "transfer": {
                    "started": "2021-02-03 13:04:06 UTC",
                    "started_epoch": 1612357446000,
                    "completed": "2021-02-03 13:04:48 UTC",
                    "completed_epoch": 1612357488700,
                    "duration_seconds": 42,
                    "data_transferred": 73400320,
                    "progress": 1.0,
                    "complete": True,
                },
            },
            {
                "name": "array3:pg3.SP-2-77",
                "id": "f0-array1:array3:pg3.SP-2-77:1612350244001",
                "protection_group": "pg3",
                "source_array": "array3",
                "pod": None,
                "suffix": "SP-2-77",
                "created": "2021-02-03 11:04:04 UTC",
                "created_epoch": 1612350244001,
                "age_seconds": 10801,
                "as_of": "2021-02-03 13:06:12 UTC",
                "as_of_epoch": 1612357572000,
                "transfer": {
                    "started": "2021-02-03 11:04:05 UTC",
                    "started_epoch": 1612350245001,
                    "completed": "2021-02-03 11:04:47 UTC",
                    "completed_epoch": 1612350287701,
                    "duration_seconds": 42,
                    "data_transferred": 9600000000,
                    "progress": 1,
                    "complete": True,
                },
            },
        ],
        "array2": [
            {
                "name": "pod1:pg2.118",
                "id": "f0-array2:pod1:pg2.118:1612353845000",
                "protection_group": "pod1::pg2",
                "source_array": None,
                "pod": "pod1",
                "suffix": "118",
                "created": "2021-02-03 12:04:05 UTC",
                "created_epoch": 1612353845000,
                "age_seconds": 7200,
                "as_of": "2021-02-03 13:06:12 UTC",
                "as_of_epoch": 1612357572000,
                "transfer": {
                    "started": "2021-02-03 12:04:06 UTC",
                    "started_epoch": 1612353846000,
                    "completed": None,
                    "completed_epoch": None,
                    "duration_seconds": None,
                    "data_transferred": 0,
                    "progress": 0.0,
                    "complete": False,
                },
            }
        ],
    },
}


@pytest.mark.usefixtures("localtime_ahead_of_utc")
class TestPure1PgroupSnapshotsMain:
    """The whole result of main() and the requests of a default run."""

    def test_snapshots_are_returned_with_their_transfers(self, pure1_client):
        snapshots, transfers = _typical(pure1_client)

        module = run_main(pure1_pgroup_snapshots)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_pgroup_snapshots=TYPICAL
        )
        module.warn.assert_not_called()
        # one request for the snapshots and one for their transfers
        assert snapshots.requests == [{"filter": BOUND, "sort": SORT, "limit": 100}]
        assert transfers.requests == [{"filter": BOUND, "sort": SORT, "limit": 3}]
        # the check of get_pure1 and nothing else
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_snapshots_are_returned_in_check_mode(self, pure1_client):
        snapshots, transfers = _typical(pure1_client)

        module = run_main(pure1_pgroup_snapshots, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_pgroup_snapshots=TYPICAL
        )
        assert len(snapshots.requests) == 1
        assert len(transfers.requests) == 1

    def test_no_snapshots_gives_every_key_empty(self, pure1_client):
        snapshots, transfers = _answer(pure1_client)

        module = run_main(pure1_pgroup_snapshots)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_pgroup_snapshots={
                "total": 0,
                "returned": 0,
                "truncated": False,
                "missing_protection_groups": [],
                "arrays": {},
            },
        )
        module.warn.assert_not_called()
        assert snapshots.requests == [{"filter": BOUND, "sort": SORT, "limit": 100}]
        # nothing to join
        assert transfers.requests == []

    def test_snapshot_with_only_its_name_and_array(self, pure1_client):
        bare = sdk_model(
            "ProtectionGroupSnapshot", name="pg1", on=array_reference("array1")
        )
        _answer(pure1_client, [bare])

        result = _result(run_main(pure1_pgroup_snapshots))

        assert result["arrays"] == {
            "array1": [
                {
                    "name": "pg1",
                    "id": None,
                    "protection_group": None,
                    "source_array": None,
                    "pod": None,
                    "suffix": None,
                    "created": None,
                    "created_epoch": None,
                    "age_seconds": None,
                    "as_of": None,
                    "as_of_epoch": None,
                    "transfer": None,
                }
            ]
        }
        assert (result["total"], result["returned"]) == (1, 1)

    def test_transfer_with_only_its_id(self, pure1_client):
        snapshot = _snapshot("1")
        bare = sdk_model("ProtectionGroupSnapshotTransfer", id=snapshot.id)
        _answer(pure1_client, [snapshot], [bare])

        result = _result(run_main(pure1_pgroup_snapshots))

        assert result["arrays"]["array1"][0]["transfer"] == {
            "started": None,
            "started_epoch": None,
            "completed": None,
            "completed_epoch": None,
            "duration_seconds": None,
            "data_transferred": None,
            "progress": None,
            "complete": False,
        }

    @pytest.mark.parametrize(
        "fields, complete, duration",
        [
            pytest.param(dict(progress=0.99), False, 42, id="progress below 1"),
            pytest.param(dict(completed=None), False, None, id="no completion time"),
            pytest.param(dict(progress=None), False, 42, id="no progress"),
            pytest.param(dict(started=None), True, None, id="no start time"),
            pytest.param(dict(progress=1), True, 42, id="progress as an integer"),
        ],
    )
    def test_transfer_is_complete_with_a_completion_time_and_full_progress(
        self, pure1_client, fields, complete, duration
    ):
        snapshot = _snapshot("1")
        _answer(pure1_client, [snapshot], [_transfer(snapshot, **fields)])

        result = _result(run_main(pure1_pgroup_snapshots))

        transfer = result["arrays"]["array1"][0]["transfer"]
        assert transfer["complete"] is complete
        assert transfer["duration_seconds"] == duration

    def test_age_is_in_whole_seconds_before_the_start_of_the_module(self, pure1_client):
        _answer(pure1_client, [_snapshot("1", NOW - 1999), _snapshot("2", NOW - 2000)])

        result = _result(run_main(pure1_pgroup_snapshots, transfer=False))

        ages = [snapshot["age_seconds"] for snapshot in result["arrays"]["array1"]]
        assert ages == [1, 2]


class TestSnapshotsLeftOut:
    """A snapshot that cannot be filed is left out, with one warning."""

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(dict(name=None), id="no name"),
            pytest.param(dict(on=None), id="no array"),
            pytest.param(
                dict(on=sdk_model("FixedReferenceFqdn", fqdn="a.example.com")),
                id="array without name",
            ),
        ],
    )
    def test_snapshot_that_cannot_be_filed_is_left_out(self, pure1_client, broken):
        kept = _snapshot("2", CREATED - 1000)
        _answer(
            pure1_client,
            [_snapshot("1", **broken), kept],
            [_transfer(kept)],
        )

        module = run_main(pure1_pgroup_snapshots)

        result = _result(module)
        assert _names(result) == {"array1": ["array2:pg1.2"]}
        # Pure1 counts it, the result does not hold it
        assert (result["total"], result["returned"]) == (2, 1)
        assert result["truncated"] is False
        module.warn.assert_called_once_with(LEFT_OUT + "1.")

    def test_snapshots_left_out_give_one_warning(self, pure1_client):
        _answer(
            pure1_client,
            [_snapshot("1", name=None), _snapshot("2"), _snapshot("3", on=None)],
        )

        module = run_main(pure1_pgroup_snapshots, transfer=False)

        assert _names(_result(module)) == {"array1": ["array2:pg1.2"]}
        module.warn.assert_called_once_with(LEFT_OUT + "2.")

    def test_snapshot_with_an_empty_name_is_filed(self, pure1_client):
        _answer(pure1_client, [_snapshot("1", name="")])

        module = run_main(pure1_pgroup_snapshots, transfer=False)

        row = _result(module)["arrays"]["array1"][0]
        assert (row["name"], row["source_array"], row["suffix"]) == ("", None, None)
        module.warn.assert_not_called()

    def test_snapshot_listed_twice_is_returned_once(self, pure1_client):
        """A snapshot that arrives between two pages moves the later page down."""
        first, second = _snapshot("1"), _snapshot("2", CREATED - 1000)
        again = _snapshot("1")
        _answer(pure1_client, [first, second, again])

        result = _result(run_main(pure1_pgroup_snapshots, transfer=False))

        assert _names(result) == {"array1": ["array2:pg1.1", "array2:pg1.2"]}
        assert (result["total"], result["returned"]) == (3, 2)

    def test_snapshots_without_an_id_are_all_returned(self, pure1_client):
        """Only its id tells that a snapshot was listed twice."""
        _answer(
            pure1_client,
            [_snapshot("1", id=None), _snapshot("2", CREATED - 1000, id=None)],
        )

        result = _result(run_main(pure1_pgroup_snapshots, transfer=False))

        assert _names(result) == {"array1": ["array2:pg1.1", "array2:pg1.2"]}
        assert (result["total"], result["returned"]) == (2, 2)


class TestLimit:
    """limit, total, returned and truncated, and the pages of both listings."""

    def _five(self):
        return [_snapshot(str(number), CREATED - number * 1000) for number in range(5)]

    def test_listing_is_read_page_by_page_up_to_the_limit(self, pure1_client):
        five = self._five()
        snapshots, transfers = _answer(
            pure1_client, five, [_transfer(snapshot) for snapshot in five], page_size=2
        )

        result = _result(run_main(pure1_pgroup_snapshots, limit=3))

        assert snapshots.requests == [
            {"filter": BOUND, "sort": SORT, "limit": 3},
            {"filter": BOUND, "sort": SORT, "limit": 1, "offset": 2},
        ]
        assert transfers.requests == [
            {"filter": BOUND, "sort": SORT, "limit": 3},
            {"filter": BOUND, "sort": SORT, "limit": 1, "offset": 2},
        ]
        assert _names(result) == {
            "array1": ["array2:pg1.0", "array2:pg1.1", "array2:pg1.2"]
        }
        assert (result["total"], result["returned"]) == (5, 3)
        assert result["truncated"] is True
        assert all(row["transfer"]["complete"] for row in result["arrays"]["array1"])

    @pytest.mark.parametrize(
        "limit, pages, returned, truncated",
        [
            pytest.param(1, 1, 1, True, id="below a page"),
            pytest.param(2, 1, 2, True, id="a page"),
            pytest.param(4, 2, 4, True, id="two pages"),
            pytest.param(5, 3, 5, False, id="every snapshot"),
            pytest.param(100, 3, 5, False, id="default"),
        ],
    )
    def test_limit_against_the_page_size(
        self, pure1_client, limit, pages, returned, truncated
    ):
        snapshots, transfers = _answer(pure1_client, self._five(), page_size=2)

        result = _result(run_main(pure1_pgroup_snapshots, limit=limit, transfer=False))

        assert len(snapshots.requests) == pages
        assert transfers.requests == []
        assert (result["total"], result["returned"]) == (5, returned)
        assert result["truncated"] is truncated

    def test_limit_defaults_to_100(self, pure1_client):
        snapshots = _answer(pure1_client)[0]

        run_main(pure1_pgroup_snapshots)

        assert snapshots.requests[0]["limit"] == 100

    def test_limit_0_returns_the_count_alone(self, pure1_client):
        """Pure1 counts 0 when no snapshot is asked for: one is asked for."""
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([]),
            valid_response([_snapshot("1")], total_item_count=48650),
        ]

        module = run_main(pure1_pgroup_snapshots, limit=0)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_pgroup_snapshots={
                "total": 48650,
                "returned": 0,
                "truncated": True,
                "missing_protection_groups": [],
                "arrays": {},
            },
        )
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": BOUND, "sort": SORT, "limit": 0},
            {"filter": BOUND, "sort": SORT, "limit": 1},
        ]
        pure1_client.get_protection_group_snapshots_transfer.assert_not_called()

    @pytest.mark.parametrize(
        "params, message",
        [
            pytest.param(
                dict(limit=-1), "limit must be 0 or more, got -1", id="limit below 0"
            ),
            pytest.param(
                dict(limit=-1, latest_only=True, protection_group=["pg1"]),
                "limit must be 0 or more, got -1",
                id="limit below 0 with latest_only",
            ),
            pytest.param(
                dict(window=0), "window must be 1 or more, got 0", id="window of 0"
            ),
            pytest.param(
                dict(window=-60),
                "window must be 1 or more, got -60",
                id="window below 0",
            ),
            pytest.param(
                dict(source_array=" "),
                "The name of a source array must not be empty.",
                id="blank source_array",
            ),
            pytest.param(
                dict(source_array=""),
                "The name of a source array must not be empty.",
                id="empty source_array",
            ),
            # the required_if of the argument spec, which ansible-core words
            pytest.param(
                dict(latest_only=True),
                None,
                id="latest_only without protection_group",
            ),
            # the same for an empty list, which the module words itself
            pytest.param(
                dict(latest_only=True, protection_group=[]),
                "latest_only is True but all of the following are missing: "
                "protection_group",
                id="latest_only with an empty protection_group",
            ),
        ],
    )
    def test_refused_value_fails_before_any_request(
        self, pure1_client, params, message
    ):
        if message is None:
            message = _latest_only_refused_by_the_spec()

        module = run_main(pure1_pgroup_snapshots, **params)

        module.fail_json.assert_called_once_with(msg=message)
        module.exit_json.assert_not_called()
        pure1_client.get_protection_group_snapshots.assert_not_called()
        pure1_client.get_protection_group_snapshots_transfer.assert_not_called()

    def test_limit_of_null_is_refused_by_the_spec(self, pure1_client, client_factory):
        """What a task gets: ansible-core refuses null for an option with a default."""
        refused = (
            ArgumentSpecValidator({"limit": {"type": "int", "default": 100}})
            .validate({"limit": None})
            .error_messages
        )

        module = run_main(pure1_pgroup_snapshots, limit=None)

        assert len(refused) == 1
        assert "unable to convert to int" in refused[0]
        module.fail_json.assert_called_once_with(msg=refused[0])
        module.exit_json.assert_not_called()
        # before the client is made
        client_factory.assert_not_called()
        pure1_client.get_protection_group_snapshots.assert_not_called()

    def test_limit_of_null_is_refused(self, pure1_client):
        """Without a limit the listing would have no end.

        No task gets this far, the argument spec refuses null first: this is
        the module's own check, for a call that goes past the spec.
        """
        module = ansible_module(**_params(limit=None))

        with pytest.raises(SystemExit):
            generate_snapshots_dict(module, pure1_client)

        module.fail_json.assert_called_once_with(
            msg="limit must be 0 or more, got None"
        )
        pure1_client.get_protection_group_snapshots.assert_not_called()

    def test_latest_only_without_protection_group_is_refused_by_the_spec(
        self, pure1_client, client_factory
    ):
        refused = _latest_only_refused_by_the_spec()

        module = run_main(pure1_pgroup_snapshots, latest_only=True)

        # as ansible-core words it, and both options are in the text
        assert "latest_only" in refused and "protection_group" in refused
        module.fail_json.assert_called_once_with(msg=refused)
        module.exit_json.assert_not_called()
        # before the client is made, where the module's own check comes after
        client_factory.assert_not_called()
        pure1_client.get_arrays.assert_not_called()


class TestArray:
    """array: resolved first, then the filter on the array a snapshot is stored on."""

    def test_snapshots_are_filtered_by_the_names_pure1_returned(self, pure1_client):
        """By the ids of the arrays that Pure1 returned for the names"""
        _known(pure1_client, "Array1")
        snapshots, transfers = _answer(pure1_client, [_snapshot("1", array="Array1")])

        result = _result(run_main(pure1_pgroup_snapshots, array=["ARRAY1"]))

        assert _requests(pure1_client.get_arrays) == [
            {"limit": 0},
            {"filter": "name='ARRAY1'"},
        ]
        selection = "on.id='id-Array1' and " + BOUND
        assert snapshots.requests == [{"filter": selection, "sort": SORT, "limit": 100}]
        assert transfers.requests[0] == {"filter": selection, "sort": SORT, "limit": 1}
        # the name is returned as Pure1 spells it
        assert list(result["arrays"]) == ["Array1"]

    def test_several_arrays_are_one_filter_term(self, pure1_client):
        _known(pure1_client, "array2", "array1")
        snapshots = _answer(pure1_client)[0]

        run_main(pure1_pgroup_snapshots, array=["array1", "array2", "array1"])

        assert snapshots.requests == [
            {
                "filter": "on.id=('id-array1','id-array2') and " + BOUND,
                "sort": SORT,
                "limit": 100,
            }
        ]

    def test_array_without_an_id_is_filtered_by_its_name(self, pure1_client):
        """Pure1 sends every array with its id; without one only the name is left"""
        _known(pure1_client, "array1", sdk_model("Array", name="Array2"))
        on_array2 = _snapshot("2", CREATED - 1000, array="Array2")
        other_case = _snapshot("3", CREATED - 2000, array="ARRAY2")
        snapshots = _answer(pure1_client, [_snapshot("1"), on_array2, other_case])[0]

        module = run_main(
            pure1_pgroup_snapshots, array=["array1", "array2"], transfer=False
        )

        assert snapshots.requests == [
            {
                "filter": "on.name=('array1','Array2') and " + BOUND,
                "sort": SORT,
                "limit": 100,
            }
        ]
        # an array without an id is the one of the name as Pure1 spells it
        assert _names(_result(module)) == {
            "array1": ["array2:pg1.1"],
            "Array2": ["array2:pg1.2"],
        }
        module.warn.assert_not_called()

    def test_snapshots_of_another_array_are_not_of_the_selection(self, pure1_client):
        """Whatever Pure1 sends: only the arrays of the task are returned,
        and an array is told from another one of its name by its id"""
        _known(pure1_client, "array1")
        same_name = _snapshot(
            "2", CREATED - 1000, on=array_reference("array1", id="id-other")
        )
        other_case = _snapshot("3", CREATED - 2000, array="ARRAY1")
        without_id = _snapshot("4", CREATED - 3000, on=array_reference("array1"))
        other_without_id = _snapshot("5", CREATED - 4000, on=array_reference("ARRAY1"))
        _answer(
            pure1_client,
            [_snapshot("1"), same_name, other_case, without_id, other_without_id],
        )

        module = run_main(pure1_pgroup_snapshots, array=["Array1"], transfer=False)
        result = _result(module)

        # a reference without an id is of the array whose name it has
        assert _names(result) == {"array1": ["array2:pg1.1", "array2:pg1.4"]}
        assert (result["total"], result["returned"]) == (5, 2)
        module.warn.assert_not_called()

    def test_arrays_of_one_name_are_not_merged(self, pure1_client):
        """The array of the newest snapshot keeps the name in the result"""
        other = array_reference("array1", id="id-other")
        without_id = array_reference("array1")
        _answer(
            pure1_client,
            [
                _snapshot("1"),
                _snapshot("2", CREATED - 1000, on=other),
                _snapshot("3", CREATED - 2000, array="array3"),
                _snapshot("4", CREATED - 3000, on=without_id),
                _snapshot("5", CREATED - 4000, on=other),
                _snapshot("6", CREATED - 5000),
            ],
        )

        module = run_main(pure1_pgroup_snapshots, transfer=False)
        result = _result(module)

        assert _names(result) == {
            "array1": ["array2:pg1.1", "array2:pg1.4", "array2:pg1.6"],
            "array3": ["array2:pg1.3"],
        }
        # Pure1 counts the snapshots of both arrays
        assert (result["total"], result["returned"]) == (6, 4)
        assert result["truncated"] is False
        module.warn.assert_called_once_with(OTHER_ARRAY + "2.")

    def test_both_arrays_of_one_name_are_of_the_task(self, pure1_client):
        """Pure1 has two arrays for the name of the task: both are asked
        for, and the one of the newest snapshot keeps the name"""
        _known(
            pure1_client,
            sdk_model("Array", name="array1", id="id-array1"),
            sdk_model("Array", name="array1", id="id-other"),
        )
        other = array_reference("array1", id="id-other")
        snapshots = _answer(
            pure1_client,
            [
                _snapshot("1", on=other),
                _snapshot("2", CREATED - 1000),
                _snapshot("3", CREATED - 2000, on=other),
            ],
        )[0]

        module = run_main(pure1_pgroup_snapshots, array=["array1"], transfer=False)
        result = _result(module)

        assert snapshots.requests[0]["filter"] == (
            "on.id=('id-array1','id-other') and " + BOUND
        )
        assert _names(result) == {"array1": ["array2:pg1.1", "array2:pg1.3"]}
        assert (result["total"], result["returned"]) == (3, 2)
        module.warn.assert_called_once_with(OTHER_ARRAY + "1.")

    def test_snapshots_left_out_for_either_reason_give_two_warnings(self, pure1_client):
        _answer(
            pure1_client,
            [
                _snapshot("1"),
                _snapshot("2", CREATED - 1000, name=None),
                _snapshot(
                    "3", CREATED - 2000, on=array_reference("array1", id="id-other")
                ),
            ],
        )

        module = run_main(pure1_pgroup_snapshots, transfer=False)

        assert _names(_result(module)) == {"array1": ["array2:pg1.1"]}
        assert [warning.args[0] for warning in module.warn.call_args_list] == [
            LEFT_OUT + "1.",
            OTHER_ARRAY + "1.",
        ]

    def test_array_that_pure1_does_not_know_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [valid_response(), valid_response([])]

        module = run_main(pure1_pgroup_snapshots, array=["foo"])

        module.fail_json.assert_called_once_with(msg="Array foo is not known to Pure1.")
        pure1_client.get_protection_group_snapshots.assert_not_called()

    @pytest.mark.parametrize("params", [{}, {"array": []}], ids=["not set", "empty"])
    def test_empty_array_list_selects_every_array(self, pure1_client, params):
        snapshots = _answer(
            pure1_client,
            [_snapshot("1"), _snapshot("2", CREATED - 1000, array="array3")],
        )[0]

        result = _result(run_main(pure1_pgroup_snapshots, transfer=False, **params))

        assert snapshots.requests == [{"filter": BOUND, "sort": SORT, "limit": 100}]
        assert sorted(result["arrays"]) == ["array1", "array3"]
        # no request to resolve an array
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_array_that_is_no_list_becomes_one(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response([sdk_model("Array", name="array1")]),
        ]
        snapshots = _answer(pure1_client)[0]

        run_main(pure1_pgroup_snapshots, array="array1")

        assert snapshots.requests[0]["filter"] == "on.name='array1' and " + BOUND


class TestSourceArray:
    """source_array: looked for in the name, checked exactly in the module."""

    SELECTION = "contains(name,'array2:') and " + BOUND

    def test_filter_and_exact_match_whatever_the_case(self, pure1_client):
        snapshot = _snapshot("1", source_array="Array2")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([snapshot])
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(snapshot)])
        ]

        result = _result(run_main(pure1_pgroup_snapshots, source_array="ARRAY2"))

        selection = "contains(name,'ARRAY2:') and " + BOUND
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": selection, "sort": SORT, "limit": 100}
        ]
        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {"filter": selection, "sort": SORT, "limit": 1}
        ]
        # as Pure1 spells it
        assert result["arrays"]["array1"][0]["source_array"] == "Array2"

    def test_name_is_escaped_in_the_filter(self, pure1_client):
        pure1_client.get_protection_group_snapshots.side_effect = [valid_response([])]

        run_main(pure1_pgroup_snapshots, source_array="o'brien\\x")

        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {
                "filter": "contains(name,'o\\'brien\\\\x:') and " + BOUND,
                "sort": SORT,
                "limit": 100,
            }
        ]

    def test_other_sources_that_end_alike_are_left_out_by_a_second_request(
        self, pure1_client
    ):
        """Pure1 finds array2: also in dr-array2: and in the pod xarray2."""
        wanted = _snapshot("1")
        other_array = _snapshot("7", CREATED - 1000, source_array="dr-array2")
        other_pod = _snapshot("3", CREATED - 2000, group="pg9", pod="xarray2")
        same_array_again = _snapshot("8", CREATED - 3000, source_array="dr-array2")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([wanted, other_array, other_pod, same_array_again]),
            valid_response([wanted]),
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(wanted)])
        ]

        module = run_main(pure1_pgroup_snapshots, source_array="array2")

        narrowed = (
            self.SELECTION
            + " and not(contains(name,'dr-array2:')) and not(pod.name='xarray2')"
        )
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": self.SELECTION, "sort": SORT, "limit": 100},
            {"filter": narrowed, "sort": SORT, "limit": 100},
        ]
        # the transfers of the listing that was kept
        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {"filter": narrowed, "sort": SORT, "limit": 1}
        ]
        result = _result(module)
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert (result["total"], result["returned"]) == (1, 1)
        assert result["truncated"] is False
        module.warn.assert_not_called()

    def test_pod_of_the_same_name_is_not_the_source_array(self, pure1_client):
        in_pod = _snapshot("3", group="pg9", pod="array2")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([in_pod]),
            valid_response([]),
        ]

        result = _result(run_main(pure1_pgroup_snapshots, source_array="array2"))

        assert _requests(pure1_client.get_protection_group_snapshots)[1]["filter"] == (
            self.SELECTION + " and not(pod.name='array2')"
        )
        assert result["arrays"] == {}
        assert result["total"] == 0
        pure1_client.get_protection_group_snapshots_transfer.assert_not_called()

    def test_source_that_pure1_returns_again_is_dropped_without_a_third_request(
        self, pure1_client
    ):
        wanted = _snapshot("1")
        other = _snapshot("7", CREATED - 1000, source_array="dr-array2")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([wanted, other]),
            valid_response(
                [wanted, _snapshot("7", CREATED - 1000, source_array="dr-array2")]
            ),
        ]

        result = _result(
            run_main(pure1_pgroup_snapshots, source_array="array2", transfer=False)
        )

        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 2
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert result["returned"] == 1

    def test_snapshot_without_a_source_in_its_name_is_dropped(self, pure1_client):
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1", name="pg1.1"), _snapshot("2")])
        ]

        result = _result(
            run_main(pure1_pgroup_snapshots, source_array="array2", transfer=False)
        )

        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 1
        assert _names(result) == {"array1": ["array2:pg1.2"]}

    def test_snapshot_without_a_name_is_left_out_with_the_warning(self, pure1_client):
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1", name=None), _snapshot("2")])
        ]

        module = run_main(pure1_pgroup_snapshots, source_array="array2", transfer=False)

        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 1
        assert _names(_result(module)) == {"array1": ["array2:pg1.2"]}
        module.warn.assert_called_once_with(LEFT_OUT + "1.")


class TestProtectionGroup:
    """protection_group: the filter on the source, and the names without a snapshot."""

    def test_names_are_one_filter_term(self, pure1_client):
        snapshots = _answer(
            pure1_client,
            [_snapshot("1"), _snapshot("2", CREATED - 1000, group="PG2")],
        )[0]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "Pg2", "PG1", "pod1::pg3"],
                transfer=False,
            )
        )

        assert snapshots.requests == [
            {
                "filter": BOUND + " and source.name=('pg1','Pg2','pod1::pg3')",
                "sort": SORT,
                "limit": 100,
            }
        ]
        # the whole selection was read: no request to find what is missing
        assert result["missing_protection_groups"] == ["pod1::pg3"]

    def test_protection_group_that_is_no_list_becomes_one(self, pure1_client):
        snapshots = _answer(pure1_client)[0]

        result = _result(run_main(pure1_pgroup_snapshots, protection_group="pg1"))

        assert snapshots.requests[0]["filter"] == BOUND + " and source.name='pg1'"
        assert result["missing_protection_groups"] == ["pg1"]

    def test_empty_list_selects_every_group(self, pure1_client):
        snapshots = _answer(pure1_client, [_snapshot("1")])[0]

        result = _result(
            run_main(pure1_pgroup_snapshots, protection_group=[], transfer=False)
        )

        assert snapshots.requests[0]["filter"] == BOUND
        assert result["missing_protection_groups"] == []
        assert result["returned"] == 1

    def test_name_not_among_a_truncated_result_is_asked_for(self, pure1_client):
        newest = _snapshot("1")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([newest], total_item_count=7),
            valid_response([_snapshot("9", CREATED - 5000, group="pg2")]),
            valid_response([]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2", "pg3"],
                limit=1,
                transfer=False,
            )
        )

        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {
                "filter": BOUND + " and source.name=('pg1','pg2','pg3')",
                "sort": SORT,
                "limit": 1,
            },
            {"filter": BOUND + " and source.name='pg2'", "sort": SORT, "limit": 1},
            {"filter": BOUND + " and source.name='pg3'", "sort": SORT, "limit": 1},
        ]
        assert result["missing_protection_groups"] == ["pg3"]
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert (result["total"], result["truncated"]) == (7, True)

    def test_group_without_unfinished_transfer_is_not_missing(self, pure1_client):
        """What is missing is asked for without the term of incomplete_only."""
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([]),
            valid_response([_snapshot("1")]),
            valid_response([]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2"],
                incomplete_only=True,
                window=60,
            )
        )

        selection = "created>=1612360985678 and " + BOUND
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {
                "filter": selection + " and source.name=('pg1','pg2') and progress<1.0",
                "sort": SORT,
                "limit": 100,
            },
            {"filter": selection + " and source.name='pg1'", "sort": SORT, "limit": 1},
            {"filter": selection + " and source.name='pg2'", "sort": SORT, "limit": 1},
        ]
        assert result["missing_protection_groups"] == ["pg2"]
        assert result["arrays"] == {}

    def test_missing_names_are_in_the_order_of_the_task(self, pure1_client):
        _answer(pure1_client, [_snapshot("1")])

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg9", "pg1", "PG8"],
                transfer=False,
            )
        )

        assert result["missing_protection_groups"] == ["pg9", "PG8"]


class TestWindowIncompleteTransfer:
    """window, incomplete_only and transfer."""

    @pytest.mark.parametrize(
        "window, start",
        [
            pytest.param(1612361045, 678, id="a second less than since 1970"),
            pytest.param(1612361046, 0, id="a second more than since 1970"),
            pytest.param(3153600000, 0, id="a hundred years"),
        ],
    )
    def test_window_does_not_start_before_1970(self, pure1_client, window, start):
        """Pure1 refuses a filter with a negative time."""
        snapshots = _answer(pure1_client)[0]

        run_main(pure1_pgroup_snapshots, window=window)

        assert snapshots.requests == [
            {
                "filter": "created>={0} and {1}".format(start, BOUND),
                "sort": SORT,
                "limit": 100,
            }
        ]

    def test_window_is_a_term_on_the_creation_time(self, pure1_client):
        snapshots = _answer(pure1_client)[0]

        run_main(pure1_pgroup_snapshots, window=86400)

        assert snapshots.requests == [
            {
                "filter": "created>=1612274645678 and " + BOUND,
                "sort": SORT,
                "limit": 100,
            }
        ]

    def test_incomplete_only_is_a_term_on_the_progress(self, pure1_client):
        snapshot = _snapshot("1")
        snapshots, transfers = _answer(
            pure1_client,
            [snapshot],
            [_transfer(snapshot, completed=None, progress=0.5)],
        )

        result = _result(run_main(pure1_pgroup_snapshots, incomplete_only=True))

        selection = BOUND + " and progress<1.0"
        assert snapshots.requests == [{"filter": selection, "sort": SORT, "limit": 100}]
        assert transfers.requests == [{"filter": selection, "sort": SORT, "limit": 1}]
        assert result["arrays"]["array1"][0]["transfer"]["progress"] == 0.5

    def test_without_transfer_no_transfer_is_read(self, pure1_client):
        snapshots, transfers = _answer(pure1_client, [_snapshot("1")])

        result = _result(
            run_main(pure1_pgroup_snapshots, transfer=False, incomplete_only=True)
        )

        assert len(snapshots.requests) == 1
        assert transfers.requests == []
        assert result["arrays"]["array1"][0]["transfer"] is None

    def test_every_option_is_one_filter(self, pure1_client):
        _known(pure1_client, "array1")
        snapshots = _answer(pure1_client, [_snapshot("1")])[0]

        run_main(
            pure1_pgroup_snapshots,
            array=["array1"],
            source_array="array2",
            protection_group=["pg1"],
            window=3600,
            incomplete_only=True,
            transfer=False,
            limit=7,
        )

        assert snapshots.requests == [
            {
                "filter": "on.id='id-array1' and contains(name,'array2:') and "
                "created>=1612357445678 and created<1612361045678 and "
                "source.name='pg1' and progress<1.0",
                "sort": SORT,
                "limit": 7,
            }
        ]

    def test_transfer_missing_from_the_listing_is_asked_for_by_id(self, pure1_client):
        """The transfers moved between the two listings: one is not among them."""
        first, second = _snapshot("1"), _snapshot("2", CREATED - 1000)
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([first, second])
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(first), _transfer(_snapshot("0", CREATED + 9))]),
            valid_response([_transfer(second, data_transferred=5)]),
        ]

        result = _result(run_main(pure1_pgroup_snapshots))

        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {"filter": BOUND, "sort": SORT, "limit": 2},
            {"filter": "id='f0-array1:array2:pg1.2:1612357444000'"},
        ]
        transferred = [
            row["transfer"]["data_transferred"] for row in result["arrays"]["array1"]
        ]
        assert transferred == [73400320, 5]

    def test_snapshot_without_a_transfer_has_none(self, pure1_client):
        first, second = _snapshot("1"), _snapshot("2", CREATED - 1000)
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([first, second])
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(first)]),
            valid_response([]),
        ]

        module = run_main(pure1_pgroup_snapshots)

        rows = _result(module)["arrays"]["array1"]
        assert rows[0]["transfer"]["complete"] is True
        assert rows[1]["transfer"] is None
        module.warn.assert_not_called()

    def test_transfer_without_id_belongs_to_no_snapshot(self, pure1_client):
        without_id = _snapshot("1", id=None)
        stray = sdk_model(
            "ProtectionGroupSnapshotTransfer", name=without_id.name, progress=1.0
        )
        _answer(pure1_client, [without_id], [stray])

        result = _result(run_main(pure1_pgroup_snapshots))

        assert result["arrays"]["array1"][0]["id"] is None
        assert result["arrays"]["array1"][0]["transfer"] is None


class TestLatestOnly:
    """latest_only: the newest snapshot of each source group on each array."""

    GROUP1 = BOUND + " and source.name='pg1'"
    GROUP2 = BOUND + " and source.name='pg2'"
    GROUP9 = BOUND + " and source.name='pg9'"
    # the groups of that name, by their ids
    ARRAY2_PG1 = BOUND + " and source.id='array2-pg1'"
    ARRAY4_PG1 = BOUND + " and source.id='array4-pg1'"
    ARRAY2_PG2 = BOUND + " and source.id='array2-pg2'"

    def _answers(self, pure1_client):
        """pg1 of array2 on two arrays, pg1 of array4 on one, pg2 on one, no pg9"""
        on_array1 = _snapshot("11", CREATED - 1000)
        on_array3 = _snapshot("9", CREATED - 9000, array="array3")
        of_array4 = _snapshot("70", CREATED - 5000, source_array="array4")
        of_group2 = _snapshot("3", CREATED, group="pg2")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([on_array1], total_item_count=30),
            valid_response([on_array3], total_item_count=10),
            valid_response([]),
            valid_response([of_array4], total_item_count=20),
            valid_response([]),
            valid_response([]),
            valid_response([of_group2], total_item_count=4),
            valid_response([]),
            valid_response([]),
            valid_response([]),
        ]
        return [of_group2, on_array1, of_array4, on_array3]

    def _expected_requests(self, tail=""):
        """Each name without the groups found, each group without the arrays found"""
        return [
            {"filter": filter_text, "sort": SORT, "limit": 1}
            for filter_text in (
                self.GROUP1 + tail,
                self.ARRAY2_PG1 + tail + " and not(on.id='id-array1')",
                self.ARRAY2_PG1 + tail + " and not(on.id=('id-array1','id-array3'))",
                self.GROUP1 + tail + " and not(source.id='array2-pg1')",
                self.ARRAY4_PG1 + tail + " and not(on.id='id-array1')",
                self.GROUP1 + tail + " and not(source.id=('array2-pg1','array4-pg1'))",
                self.GROUP2 + tail,
                self.ARRAY2_PG2 + tail + " and not(on.id='id-array1')",
                self.GROUP2 + tail + " and not(source.id='array2-pg2')",
                self.GROUP9 + tail,
            )
        ]

    def test_newest_of_each_array_and_source_group(self, pure1_client):
        newest_first = self._answers(pure1_client)
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(snapshot) for snapshot in newest_first])
        ]

        module = run_main(
            pure1_pgroup_snapshots,
            protection_group=["pg1", "PG1", "pg2", "pg9"],
            latest_only=True,
        )

        assert (
            _requests(pure1_client.get_protection_group_snapshots)
            == self._expected_requests()
        )
        # the transfers by id, the newest snapshot first
        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {
                "filter": "id=('f0-array1:array2:pg2.3:1612357445000',"
                "'f0-array1:array2:pg1.11:1612357444000',"
                "'f0-array1:array4:pg1.70:1612357440000',"
                "'f0-array3:array2:pg1.9:1612357436000')"
            }
        ]
        result = _result(module)
        assert _names(result) == {
            "array1": ["array2:pg2.3", "array2:pg1.11", "array4:pg1.70"],
            "array3": ["array2:pg1.9"],
        }
        assert (result["total"], result["returned"]) == (4, 4)
        assert result["truncated"] is False
        assert result["missing_protection_groups"] == ["pg9"]
        assert all(
            row["transfer"]["complete"]
            for rows in result["arrays"].values()
            for row in rows
        )
        module.warn.assert_not_called()

    def test_limit_cuts_the_newest_after_every_group_is_read(self, pure1_client):
        self._answers(pure1_client)
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([])
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2", "pg9"],
                latest_only=True,
                limit=2,
            )
        )

        # a limit that cuts the result costs no request for the missing name
        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 10
        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {
                "filter": "id=('f0-array1:array2:pg2.3:1612357445000',"
                "'f0-array1:array2:pg1.11:1612357444000')"
            }
        ]
        assert _names(result) == {"array1": ["array2:pg2.3", "array2:pg1.11"]}
        assert (result["total"], result["returned"]) == (4, 2)
        assert result["truncated"] is True
        assert result["missing_protection_groups"] == ["pg9"]

    def test_limit_0_counts_the_newest_and_reads_no_transfer(self, pure1_client):
        self._answers(pure1_client)

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2", "pg9"],
                latest_only=True,
                limit=0,
            )
        )

        assert result == {
            "total": 4,
            "returned": 0,
            "truncated": True,
            "missing_protection_groups": ["pg9"],
            "arrays": {},
        }
        pure1_client.get_protection_group_snapshots_transfer.assert_not_called()

    def test_other_options_narrow_what_the_newest_is_taken_from(self, pure1_client):
        """With incomplete_only a name without a snapshot is asked for once more."""
        self._answers(pure1_client)
        answers = list(pure1_client.get_protection_group_snapshots.side_effect)
        # pg9 has a snapshot, but none that is unfinished
        pure1_client.get_protection_group_snapshots.side_effect = answers + [
            valid_response([_snapshot("1", group="pg9")])
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2", "pg9"],
                latest_only=True,
                incomplete_only=True,
                transfer=False,
            )
        )

        assert _requests(
            pure1_client.get_protection_group_snapshots
        ) == self._expected_requests(" and progress<1.0") + [
            {"filter": self.GROUP9, "sort": SORT, "limit": 1}
        ]
        assert result["missing_protection_groups"] == []
        assert result["total"] == 4

    def test_snapshot_that_cannot_be_left_out_ends_its_group(self, pure1_client):
        """Without the id of its source group the next answer would be the same."""
        no_source_id = _snapshot("1", source=sdk_model("FixedReference", name="pg1"))
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([no_source_id], total_item_count=9)
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1"],
                latest_only=True,
                transfer=False,
            )
        )

        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 1
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert result["total"] == 1

    def test_snapshot_without_its_array_ends_its_source_group(self, pure1_client):
        """Without its array the next answer for the source group would be the same."""
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1", on=None)], total_item_count=9),
            valid_response([]),
        ]

        module = run_main(
            pure1_pgroup_snapshots,
            protection_group=["pg1"],
            latest_only=True,
            transfer=False,
        )

        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": self.GROUP1, "sort": SORT, "limit": 1},
            {
                "filter": self.GROUP1 + " and not(source.id='array2-pg1')",
                "sort": SORT,
                "limit": 1,
            },
        ]
        result = _result(module)
        assert result["arrays"] == {}
        assert (result["total"], result["returned"]) == (1, 0)
        module.warn.assert_called_once_with(LEFT_OUT + "1.")

    def test_snapshot_without_the_id_of_its_array_ends_its_source_group(
        self, pure1_client
    ):
        """An array is left out by its id: without one the next answer for
        the source group would be the same"""
        without_id = _snapshot("1", on=array_reference("array1"))
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([without_id], total_item_count=9),
            valid_response([]),
        ]

        module = run_main(
            pure1_pgroup_snapshots,
            protection_group=["pg1"],
            latest_only=True,
            transfer=False,
        )

        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": self.GROUP1, "sort": SORT, "limit": 1},
            {
                "filter": self.GROUP1 + " and not(source.id='array2-pg1')",
                "sort": SORT,
                "limit": 1,
            },
        ]
        result = _result(module)
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert (result["total"], result["returned"]) == (1, 1)
        module.warn.assert_not_called()

    def test_second_array_of_one_name_is_asked_for_and_left_out(self, pure1_client):
        """The arrays that are found are left out by their ids, so the other
        array of the name is found as well; the result has one of them"""
        on_other = _snapshot(
            "9", CREATED - 9000, on=array_reference("array1", id="id-other")
        )
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("11")]),
            valid_response([on_other]),
            valid_response([]),
            valid_response([]),
        ]

        module = run_main(
            pure1_pgroup_snapshots,
            protection_group=["pg1"],
            latest_only=True,
            transfer=False,
        )

        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": filter_text, "sort": SORT, "limit": 1}
            for filter_text in (
                self.GROUP1,
                self.ARRAY2_PG1 + " and not(on.id='id-array1')",
                self.ARRAY2_PG1 + " and not(on.id=('id-array1','id-other'))",
                self.GROUP1 + " and not(source.id='array2-pg1')",
            )
        ]
        result = _result(module)
        assert _names(result) == {"array1": ["array2:pg1.11"]}
        assert (result["total"], result["returned"]) == (2, 1)
        module.warn.assert_called_once_with(OTHER_ARRAY + "1.")

    def test_array_of_the_task_is_in_every_request(self, pure1_client):
        _known(pure1_client, "array1")
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("11")]),
            valid_response([]),
            valid_response([]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                array=["array1"],
                protection_group=["pg1"],
                latest_only=True,
                transfer=False,
            )
        )

        selection = "on.id='id-array1' and " + BOUND
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": filter_text, "sort": SORT, "limit": 1}
            for filter_text in (
                selection + " and source.name='pg1'",
                selection + " and source.id='array2-pg1' and not(on.id='id-array1')",
                selection + " and source.name='pg1' and not(source.id='array2-pg1')",
            )
        ]
        assert _names(result) == {"array1": ["array2:pg1.11"]}

    def test_snapshot_that_pure1_returns_again_ends_its_group(self, pure1_client):
        """Once as another array of its source group, once as another source group."""
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1")]),
            valid_response([_snapshot("1")]),
            valid_response([_snapshot("1")]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1"],
                latest_only=True,
                transfer=False,
            )
        )

        assert len(_requests(pure1_client.get_protection_group_snapshots)) == 3
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert (result["total"], result["returned"]) == (1, 1)

    def test_source_array_is_checked_for_each_newest_snapshot(self, pure1_client):
        """What is left out for one request is left out for every request after it."""
        other = _snapshot("7", source_array="dr-array2")
        wanted = _snapshot("1", CREATED - 1000)
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([other]),
            valid_response([wanted]),
            valid_response([]),
            valid_response([]),
            valid_response([]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1", "pg2"],
                source_array="array2",
                latest_only=True,
                transfer=False,
            )
        )

        selection = "contains(name,'array2:') and " + BOUND
        group = selection + " and source.name='pg1'"
        left_out = " and not(contains(name,'dr-array2:'))"
        assert _requests(pure1_client.get_protection_group_snapshots) == [
            {"filter": filter_text, "sort": SORT, "limit": 1}
            for filter_text in (
                group,
                group + left_out,
                selection
                + " and source.id='array2-pg1' and not(on.id='id-array1')"
                + left_out,
                group + " and not(source.id='array2-pg1')" + left_out,
                selection + " and source.name='pg2'" + left_out,
            )
        ]
        assert _names(result) == {"array1": ["array2:pg1.1"]}
        assert result["missing_protection_groups"] == ["pg2"]

    @pytest.mark.parametrize(
        "options, base",
        [
            pytest.param({}, 1, id="no other option"),
            pytest.param(
                dict(window=86400, incomplete_only=True),
                3,
                id="window and incomplete_only",
            ),
        ],
    )
    def test_name_of_many_groups_stays_within_the_filter_pure1_takes(
        self, pure1_client, options, base
    ):
        """A name on 20 source arrays, the group of the first on three arrays."""
        sources = ["array{0}".format(number) for number in range(10, 30)]
        snapshots = [
            _snapshot("9", CREATED - 700, "array3", source_array="array10"),
            _snapshot("9", CREATED - 800, "array4", source_array="array10"),
        ]
        for number, source in enumerate(sources):
            snapshots += [
                _snapshot("9", CREATED - number * 10, source_array=source),
                _snapshot("8", CREATED - 50000 - number, source_array=source),
            ]
        snapshots.sort(key=lambda snapshot: snapshot.created, reverse=True)
        counts = _answer_as_pure1_filters(pure1_client, snapshots)

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg1"],
                latest_only=True,
                transfer=False,
                **options
            )
        )

        assert _names(result) == {
            "array1": [source + ":pg1.9" for source in sources],
            "array3": ["array10:pg1.9"],
            "array4": ["array10:pg1.9"],
        }
        assert (result["total"], result["returned"]) == (22, 22)
        # one request for each of the 22 snapshots and of the 20 groups and
        # one for the name; the first has nothing to leave out yet
        assert counts == [base + 1] + [base + 2] * 42

    def test_comparisons_of_a_filter_are_counted_as_pure1_counts_them(self):
        assert (
            _comparisons(
                "on.name=('a=b','c') and contains(name,'d:') and created>=1 and "
                "created<2 and source.name='e' and progress<1.0 and "
                "not(source.id=('f','g')) and not(pod.name='h')"
            )
            == 8
        )

    def test_snapshots_of_one_time_are_in_the_order_of_their_ids(self, pure1_client):
        """As Pure1 sorts a listing: created-, id."""
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1", group="pg2")]),
            valid_response([]),
            valid_response([]),
            valid_response([_snapshot("1", group="pg1")]),
            valid_response([]),
            valid_response([]),
            valid_response([_snapshot("1", CREATED + 1, group="pg0")]),
            valid_response([]),
            valid_response([]),
        ]

        result = _result(
            run_main(
                pure1_pgroup_snapshots,
                protection_group=["pg2", "pg1", "pg0"],
                latest_only=True,
                transfer=False,
            )
        )

        assert _names(result) == {
            "array1": ["array2:pg0.1", "array2:pg1.1", "array2:pg2.1"]
        }


class TestTransfersById:
    """The ids of one request stay below what Pure1 takes."""

    def test_ids_are_split_by_their_length_in_the_request(self):
        # 'id', as the request carries it: 91 characters and 9 for the rest
        plain = ["{0:091d}".format(number) for number in range(81)]

        assert [len(chunk) for chunk in id_chunks(plain)] == [40, 40, 1]
        assert [item for chunk in id_chunks(plain) for item in chunk] == plain

    def test_colon_counts_as_the_three_bytes_it_is_sent_as(self):
        with_colon = ["{0:089d}:".format(number) for number in range(41)]

        assert [len(chunk) for chunk in id_chunks(with_colon)] == [39, 2]

    def test_id_longer_than_a_request_is_asked_for_alone(self):
        assert list(id_chunks(["a" * 5000, "b"])) == [["a" * 5000], ["b"]]

    def test_no_id_is_no_request(self):
        assert not list(id_chunks([]))

    def test_one_request_for_each_chunk(self, pure1_client):
        snapshots = [
            _snapshot(str(number), id="{0:091d}".format(number)) for number in range(41)
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([_transfer(snapshot) for snapshot in snapshots[:40]]),
            valid_response([_transfer(snapshots[40])]),
        ]

        transfers = read_transfers(ansible_module(), pure1_client, snapshots, [])

        requests = _requests(pure1_client.get_protection_group_snapshots_transfer)
        assert [request["filter"].count("','") + 1 for request in requests] == [40, 1]
        assert requests[1] == {"filter": "id='{0:091d}'".format(40)}
        assert sorted(transfers) == [snapshot.id for snapshot in snapshots]

    def test_id_is_escaped_and_asked_for_once(self, pure1_client):
        snapshots = [_snapshot("1", id="o'brien"), _snapshot("2", id="o'brien")]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([])
        ]

        transfers = read_transfers(ansible_module(), pure1_client, snapshots, [])

        assert _requests(pure1_client.get_protection_group_snapshots_transfer) == [
            {"filter": "id='o\\'brien'"}
        ]
        assert not transfers

    def _three(self):
        snapshots = [
            _snapshot(str(number), id="id-{0}".format(number)) for number in range(3)
        ]
        return snapshots, [_transfer(snapshot) for snapshot in snapshots]

    def test_transfers_of_two_pages_are_read_whole(self, pure1_client):
        snapshots, sent = self._three()
        answers = paged_answers(
            "ProtectionGroupSnapshotTransferGetResponse", sent, page_size=2
        )
        pure1_client.get_protection_group_snapshots_transfer.side_effect = answers
        module = ansible_module()

        transfers = read_transfers(module, pure1_client, snapshots, [])

        by_id = "id=('id-0','id-1','id-2')"
        assert answers.requests == [
            {"filter": by_id},
            {"filter": by_id, "continuation_token": "page-2"},
        ]
        assert sorted(transfers) == ["id-0", "id-1", "id-2"]
        module.warn.assert_not_called()

    def test_transfers_that_pure1_sends_short_give_a_warning(self, pure1_client):
        """Pure1 counts five and sends three: what is missing is asked for
        once more, from where the listing ended"""
        snapshots, sent = self._three()
        answers = paged_answers(
            "ProtectionGroupSnapshotTransferGetResponse",
            sent,
            page_size=2,
            total_item_count=5,
        )
        pure1_client.get_protection_group_snapshots_transfer.side_effect = answers
        module = ansible_module()

        transfers = read_transfers(module, pure1_client, snapshots, [])

        by_id = "id=('id-0','id-1','id-2')"
        assert answers.requests == [
            {"filter": by_id},
            {"filter": by_id, "continuation_token": "page-2"},
            {"filter": by_id, "offset": 3},
        ]
        assert sorted(transfers) == ["id-0", "id-1", "id-2"]
        module.warn.assert_called_once_with(
            "Pure1 returned 3 of 5 transfers of protection group snapshots: the "
            "result may be incomplete."
        )

    def test_short_requests_give_one_warning_for_all_of_them(self, pure1_client):
        """41 transfers in two requests; the first one counts 40 and sends 38"""
        snapshots = [
            _snapshot(str(number), id="{0:091d}".format(number)) for number in range(41)
        ]
        every = [_transfer(snapshot) for snapshot in snapshots]

        def answer(**kwargs):
            wanted = [
                transfer
                for transfer in every
                if "'{0}'".format(transfer.id) in kwargs["filter"]
            ]
            sent = wanted[:38][kwargs.get("offset", 0) :]
            return valid_response(sent, total_item_count=len(wanted))

        pure1_client.get_protection_group_snapshots_transfer.side_effect = answer
        module = ansible_module()

        transfers = read_transfers(module, pure1_client, snapshots, [])

        requests = _requests(pure1_client.get_protection_group_snapshots_transfer)
        assert [request.get("offset") for request in requests] == [None, 38, None]
        assert len(transfers) == 39
        module.warn.assert_called_once_with(
            "Pure1 returned 39 of 41 transfers of protection group snapshots: "
            "the result may be incomplete."
        )

    def test_short_request_without_a_total_gives_the_warning(self, pure1_client):
        """Pure1 counts nothing and gives the first page again for the next"""
        snapshots, sent = self._three()
        answers = paged_answers(
            "ProtectionGroupSnapshotTransferGetResponse", sent, total_item_count=None
        )

        def offset_ignored(**kwargs):
            kwargs.pop("offset", None)
            return answers(**kwargs)

        pure1_client.get_protection_group_snapshots_transfer.side_effect = (
            offset_ignored
        )
        module = ansible_module()

        transfers = read_transfers(module, pure1_client, snapshots, [])

        assert len(answers.requests) == 2
        assert sorted(transfers) == ["id-0", "id-1", "id-2"]
        module.warn.assert_called_once_with(
            "Pure1 returned 3 of an unknown number of transfers of protection "
            "group snapshots: the result may be incomplete."
        )

    def test_transfers_by_id_that_are_short_warn_a_task(self, pure1_client):
        """Through main(): the listing by filter misses the transfers, and
        the request by id is short"""
        snapshots, sent = self._three()
        by_id = paged_answers(
            "ProtectionGroupSnapshotTransferGetResponse", sent[:2], total_item_count=3
        )

        def answer(**kwargs):
            if "sort" in kwargs:
                return valid_response([])
            return by_id(**kwargs)

        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response(snapshots)
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = answer

        module = run_main(pure1_pgroup_snapshots)

        rows = _result(module)["arrays"]["array1"]
        assert [row["transfer"] is None for row in rows] == [False, False, True]
        module.warn.assert_called_once_with(
            "Pure1 returned 2 of 3 transfers of protection group snapshots: the "
            "result may be incomplete."
        )


class TestSplitName:
    @pytest.mark.parametrize(
        "name, expected",
        [
            pytest.param("array2:pg1.4721", ("array2", "4721"), id="replicated"),
            pytest.param("pod1:pg2.118", ("pod1", "118"), id="group in a pod"),
            pytest.param(
                "array2:pg1.SP-2-77", ("array2", "SP-2-77"), id="named suffix"
            ),
            pytest.param("array2:pg1.a.b", ("array2", "a.b"), id="dot in the suffix"),
            pytest.param(
                "array2:pod1::pg1.7", ("array2", "7"), id="colons after the source"
            ),
            pytest.param("pg1.4721", (None, "4721"), id="no source"),
            pytest.param("array2:pg1", ("array2", None), id="no suffix"),
            pytest.param("pg1", (None, None), id="neither"),
            pytest.param("", (None, None), id="empty"),
            pytest.param(None, (None, None), id="no name"),
        ],
    )
    def test_source_and_suffix_of_a_name(self, name, expected):
        assert split_name(name) == expected


class TestDocumentation:
    """What the documentation blocks say of the rules of the code."""

    @pytest.mark.parametrize(
        "option, sentence",
        [
            pytest.param(
                "latest_only",
                "Requires I(protection_group) with at least one name.",
                id="latest_only requires protection_group",
            ),
            pytest.param(
                "protection_group",
                "Required when I(latest_only) is set.",
                id="protection_group is required by latest_only",
            ),
            pytest.param("window", "Must be 1 or more.", id="least window"),
            pytest.param("limit", "Must be 0 or more.", id="least limit"),
        ],
    )
    def test_option_names_what_the_module_refuses(self, option, sentence):
        options = yaml.safe_load(pure1_pgroup_snapshots.DOCUMENTATION)["options"]

        assert sentence in " ".join(options[option]["description"])

    @pytest.mark.parametrize(
        "option, sentence",
        [
            pytest.param(
                "array",
                "Without it, or with an empty list, the snapshots stored on every "
                "array are returned.",
                id="array",
            ),
            pytest.param(
                "protection_group",
                "Without it, or with an empty list, the snapshots of every "
                "protection group are returned.",
                id="protection_group",
            ),
        ],
    )
    def test_list_option_says_what_it_returns_when_it_is_not_set(
        self, option, sentence
    ):
        """What test_empty_array_list_selects_every_array and
        test_empty_list_selects_every_group pin."""
        options = yaml.safe_load(pure1_pgroup_snapshots.DOCUMENTATION)["options"]

        assert sentence in " ".join(options[option]["description"])

    def test_total_is_null_when_pure1_reports_none(self, pure1_client):
        """And RETURN says so"""
        pure1_client.get_protection_group_snapshots.side_effect = paged_answers(
            "ProtectionGroupSnapshotGetResponse",
            [_snapshot("1"), _snapshot("2", CREATED - 1000)],
            total_item_count=None,
        )

        result = _result(run_main(pure1_pgroup_snapshots, transfer=False))

        assert result["total"] is None
        assert result["returned"] == 2
        assert result["truncated"] is False
        keys = yaml.safe_load(pure1_pgroup_snapshots.RETURN)
        total = keys["pure1_pgroup_snapshots"]["contains"]["total"]["description"]
        assert "null when Pure1 does not report it." in " ".join(total).replace(
            "\n", " "
        )

    def test_note_names_the_release_that_the_calls_came_with(self):
        """It is no requirement below the py-pure-client the collection requires"""
        notes = yaml.safe_load(pure1_pgroup_snapshots.DOCUMENTATION)["notes"]

        (note,) = [note for note in notes if "py-pure-client" in note]
        assert note.replace("\n", " ") == (
            "The Pure1 REST 1.5 calls this module makes, "
            "C(get_protection_group_snapshots) and "
            "C(get_protection_group_snapshots_transfer), came with py-pure-client "
            "1.78.0."
        )

    def test_sample_is_shown_whole_by_ansible_doc(self):
        """ansible-doc takes "key: word::" out of a text, as a directive of RST."""
        returned = yaml.safe_load(pure1_pgroup_snapshots.RETURN)
        sample = returned["pure1_pgroup_snapshots"]["sample"]

        shown = yaml.safe_dump(sample, default_flow_style=False)

        assert re.search(r".. \w+?::", shown) is None

    def test_example_looks_for_a_transfer_before_it_reads_one(self):
        """transfer is null for a snapshot that Pure1 has no transfer for."""
        tasks = yaml.safe_load(pure1_pgroup_snapshots.EXAMPLES)

        (looping,) = [task for task in tasks if "loop" in task]

        assert looping["ansible.builtin.assert"]["that"] == [
            "item['transfer'] is not none",
            "item['transfer']['complete']",
        ]


class TestFailures:
    """A failed request fails the task with one line."""

    @pytest.mark.parametrize(
        "attribute, answer, reason",
        [
            pytest.param(
                "return_value",
                error_response(403, "Access denied"),
                " (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                "return_value",
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                " (HTTP 400): Illegal filter format: Unexpected input at character 16",
                id="error answer with context",
            ),
            pytest.param(
                "return_value",
                ErrorResponse(503, [], {}),
                " (HTTP 503): Service Unavailable",
                id="error answer without message",
            ),
            pytest.param(
                "side_effect",
                PureError("token refused"),
                ": PureError: token refused",
                id="PureError",
            ),
            pytest.param(
                "side_effect",
                TimeoutError("The read operation timed out"),
                ": TimeoutError: The read operation timed out",
                id="transport error",
            ),
        ],
    )
    @pytest.mark.parametrize("method", ["snapshots", "transfers"])
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, reason
    ):
        if method == "snapshots":
            failing = pure1_client.get_protection_group_snapshots
            expected = SNAPSHOTS_FAILED + reason
        else:
            pure1_client.get_protection_group_snapshots.side_effect = [
                valid_response([_snapshot("1")])
            ]
            failing = pure1_client.get_protection_group_snapshots_transfer
            expected = TRANSFERS_FAILED + reason
        setattr(failing, attribute, answer)

        module = run_main(pure1_pgroup_snapshots)

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_failed_later_page_fails_the_task(self, pure1_client):
        answers = paged_answers(
            "ProtectionGroupSnapshotGetResponse",
            [_snapshot(str(number)) for number in range(5)],
            page_size=2,
        )

        def first_page_only(**kwargs):
            if "offset" in kwargs:
                return error_response(500, "Internal server error")
            return answers(**kwargs)

        pure1_client.get_protection_group_snapshots.side_effect = first_page_only

        module = run_main(pure1_pgroup_snapshots, limit=3)

        module.fail_json.assert_called_once_with(
            msg=SNAPSHOTS_FAILED + " (HTTP 500): Internal server error"
        )
        module.exit_json.assert_not_called()

    def test_failed_transfer_by_id_fails_the_task(self, pure1_client):
        pure1_client.get_protection_group_snapshots.side_effect = [
            valid_response([_snapshot("1")])
        ]
        pure1_client.get_protection_group_snapshots_transfer.side_effect = [
            valid_response([]),
            PureError("token refused"),
        ]

        module = run_main(pure1_pgroup_snapshots)

        module.fail_json.assert_called_once_with(
            msg=TRANSFERS_FAILED + ": PureError: token refused"
        )

    def test_failed_array_request_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(500, "boom"),
        ]

        module = run_main(pure1_pgroup_snapshots, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 500): boom"
        )
        pure1_client.get_protection_group_snapshots.assert_not_called()

    @pytest.mark.parametrize(
        "method",
        ["get_protection_group_snapshots", "get_protection_group_snapshots_transfer"],
    )
    def test_sdk_without_the_method_fails_the_task(
        self, pure1_client, monkeypatch, method
    ):
        """py-pure-client before 1.78.0 has neither method."""
        monkeypatch.setattr(pypureclient, "__version__", "1.77.0")
        setattr(pure1_client, method, None)

        module = run_main(pure1_pgroup_snapshots)

        module.fail_json.assert_called_once_with(
            msg="py-pure-client 1.78.0 or later is required: this module needs "
            "Client.{0}(), which the installed py-pure-client 1.77.0 does not "
            "have.".format(method)
        )
        module.exit_json.assert_not_called()


MODULE = "everpure.pure1.pure1_pgroup_snapshots"


def _template(text, variables):
    """Return what ansible-core makes of a template of a task"""
    return Templar(loader=None, variables=variables).template(trust_as_template(text))


def _met(condition, variables):
    """Tell whether a condition of a when or of an assert is met"""
    return bool(_template("{{ " + condition + " }}", variables))


def _serve_example(pure1_client, registers, served):
    """Give Pure1 what the task of EXAMPLES that registers this name asks for"""
    snapshots = pure1_client.get_protection_group_snapshots
    transfers = pure1_client.get_protection_group_snapshots_transfer
    if registers == "snapshots":
        _typical(pure1_client)
    elif registers == "latest":
        # the newest of each group, as the filter of each request selects it
        _answer_as_pure1_filters(pure1_client, served["latest"])

        def by_id(**kwargs):
            return valid_response(
                [
                    transfer
                    for transfer in served["transfers"]
                    if "'{0}'".format(transfer.id) in kwargs["filter"]
                ]
            )

        transfers.side_effect = by_id
    elif registers == "unfinished":
        # a count: no snapshot is asked for
        def count(**kwargs):
            return valid_response(
                served["unfinished"][: kwargs["limit"]],
                total_item_count=len(served["unfinished"]),
            )

        snapshots.side_effect = count
    else:
        _answer(pure1_client, served["sent"])


def _play_examples(pure1_client, **changed):
    """Run the tasks of EXAMPLES in their order and return what the others make

    A task of the module runs through main() with the options of the
    example and registers what main() returns. Pure1 has snapshots for it,
    which changed replaces under the name that the task registers
    (transfers: those of latest). Every other task is returned in the order
    of the examples, as a list with one entry for each item of its loop and
    one without a loop. An entry has the msg of the task and the conditions
    of its that up to the first one that is not met, where
    ansible.builtin.assert stops as well.
    """
    served = dict(
        latest=[
            _snapshot("7", group="pg1"),
            _snapshot("3", CREATED - 1000, group="pg2"),
        ],
        unfinished=[],
        sent=[_snapshot("1", NOW - 1000), _snapshot("2", NOW - 2000)],
    )
    served.update(changed)
    served.setdefault(
        "transfers", [_transfer(snapshot) for snapshot in served["latest"]]
    )
    registered = {}
    played = []
    for task in yaml.safe_load(pure1_pgroup_snapshots.EXAMPLES):
        if MODULE in task:
            options = dict(task[MODULE])
            options.pop("app_id", None)
            options.pop("key_file", None)
            _known(pure1_client, *options.get("array", []))
            _serve_example(pure1_client, task["register"], served)
            module = run_main(pure1_pgroup_snapshots, **options)
            module.fail_json.assert_not_called()
            module.warn.assert_not_called()
            registered[task["register"]] = dict(module.exit_json.call_args[1])
            continue
        (action,) = set(task) - {"name", "loop"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        entries = []
        for item in items:
            scope = dict(registered, item=item)
            entry = {}
            if "msg" in task[action]:
                entry["msg"] = _template(task[action]["msg"], scope)
            for condition in task[action].get("that", []):
                entry.setdefault("that", []).append(_met(condition, scope))
                if not entry["that"][-1]:
                    break
            entries.append(entry)
        played.append(entries)
    return played


class TestExamples:
    """The tasks of EXAMPLES, on what the module returns."""

    def test_examples_read_what_the_module_returns(self, pure1_client):
        shown, replicated, arrived, finished, counted = _play_examples(pure1_client)

        assert shown == [{"msg": TYPICAL["arrays"]}]
        assert replicated == [{"that": [True]}]
        # one item for the latest snapshot of each of the two groups
        assert arrived == [{"that": [True, True]}, {"that": [True, True]}]
        assert finished == [{"that": [True]}]
        # ansible-core before 2.19 gives the number as text
        assert [int(entry["msg"]) for entry in counted] == [2]

    def test_group_that_did_not_replicate_fails_the_check(self, pure1_client):
        played = _play_examples(pure1_client, latest=[_snapshot("7", group="pg1")])

        assert played[1] == [{"that": [False]}]
        assert played[2] == [{"that": [True, True]}]

    @pytest.mark.parametrize(
        "transfer, expected",
        [
            pytest.param(None, [False], id="no transfer"),
            pytest.param(
                dict(completed=None, progress=0.5), [True, False], id="not finished"
            ),
            pytest.param(dict(progress=0.99), [True, False], id="not all of it"),
            pytest.param({}, [True, True], id="finished"),
        ],
    )
    def test_check_of_the_latest_snapshot_reads_its_transfer(
        self, pure1_client, transfer, expected
    ):
        """transfer is null for a snapshot that Pure1 has no transfer for:
        the first condition keeps the second one from reading it"""
        latest = [
            _snapshot("7", group="pg1"),
            _snapshot("3", CREATED - 1000, group="pg2"),
        ]
        transfers = [_transfer(latest[0])]
        if transfer is not None:
            transfers.append(_transfer(latest[1], **transfer))

        played = _play_examples(pure1_client, latest=latest, transfers=transfers)

        assert played[2] == [{"that": [True, True]}, {"that": expected}]

    def test_unfinished_transfer_fails_the_check_of_the_count(self, pure1_client):
        unfinished = [_snapshot(str(number), CREATED - number) for number in range(3)]

        played = _play_examples(pure1_client, unfinished=unfinished)

        assert played[3] == [{"that": [False]}]

    def test_tasks_of_the_examples_ask_as_their_options_say(self, pure1_client):
        _play_examples(pure1_client)

        requests = _requests(pure1_client.get_protection_group_snapshots)
        # the first task has no option
        assert requests[0] == {"filter": BOUND, "sort": SORT, "limit": 100}
        # the count of the third one: array1 by its id, and no snapshot
        unfinished = (
            "on.id='id-array1' and created>=1611756245678 and "
            "created<1612361045678 and progress<1.0"
        )
        assert requests[-3:-1] == [
            {"filter": unfinished, "sort": SORT, "limit": 0},
            {"filter": unfinished, "sort": SORT, "limit": 1},
        ]
        # the last one: the source array in the name, the last hour
        assert requests[-1] == {
            "filter": "on.id='id-array1' and contains(name,'array2:') and "
            "created>=1612357445678 and created<1612361045678",
            "sort": SORT,
            "limit": 1000,
        }
        # the transfers of the first task and of the two latest snapshots
        assert len(_requests(pure1_client.get_protection_group_snapshots_transfer)) == 2
