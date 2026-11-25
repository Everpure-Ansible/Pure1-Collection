# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_replica_links module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import call

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


from plugins.modules import pure1_replica_links
from tests.unit.conftest import (
    array_reference,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# The module runs at 2021-02-03 13:05:00.5 UTC
NOW = 1612357500.5
# 2021-02-03 13:04:05 UTC, 55.5 seconds before
AS_OF = 1612357445000

POD_ROW = {
    "id": "pod-link-1",
    "sources": [
        {
            "name": "pod1",
            "id": "pod1-on-array1",
            "arrays": [{"name": "array1", "fqdn": "array1.example.com"}],
        }
    ],
    "targets": [
        {
            "name": "pod1-dr",
            "id": "pod1-dr-on-array2",
            "arrays": [{"name": "array2", "fqdn": "array2.example.com"}],
        }
    ],
    "bidirectional": False,
    "status": "replicating",
    "status_details": None,
    "paused": False,
    "lag": 127537,
    "lag_seconds": 127,
    "recovery_point": "2021-02-03 13:01:57 UTC",
    "recovery_point_epoch": 1612357317463,
    "never_replicated": False,
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "as_of_age_seconds": 55,
}
FS_ROW = {
    "id": "fs-link-1",
    "sources": [
        {
            "name": "fs1",
            "id": "fs1-on-array3",
            "arrays": [{"name": "array3", "fqdn": "array3.example.com"}],
        }
    ],
    "targets": [
        {
            "name": "fs1",
            "id": "fs1-on-array4",
            "arrays": [{"name": "array4", "fqdn": "array4.example.com"}],
        }
    ],
    "bidirectional": False,
    "status": "idle",
    "status_details": None,
    "paused": False,
    "lag": 3964000,
    "lag_seconds": 3964,
    "recovery_point": "2021-02-03 11:36:11 UTC",
    "recovery_point_epoch": 1612352171000,
    "never_replicated": False,
    "as_of": "2021-02-03 12:42:15 UTC",
    "as_of_epoch": 1612356135000,
    "as_of_age_seconds": 1365,
    "policies": ["policy1"],
}
BUCKET_ROW = {
    "id": "bucket-link-1",
    "sources": [
        {
            "name": "bucket1",
            "id": "bucket1-on-array3",
            "arrays": [{"name": "array3", "fqdn": "array3.example.com"}],
        },
        {
            "name": "bucket1",
            "id": "bucket1-on-array4",
            "arrays": [{"name": "array4", "fqdn": "array4.example.com"}],
        },
    ],
    "targets": [
        {
            "name": "bucket1",
            "id": "bucket1-on-array4",
            "arrays": [{"name": "array4", "fqdn": "array4.example.com"}],
        },
        {
            "name": "bucket1",
            "id": "bucket1-on-array3",
            "arrays": [{"name": "array3", "fqdn": "array3.example.com"}],
        },
    ],
    "bidirectional": True,
    "status": "replicating",
    "status_details": None,
    "paused": False,
    "lag": 94208,
    "lag_seconds": 94,
    "recovery_point": "2021-02-03 12:04:05 UTC",
    "recovery_point_epoch": 1612353845000,
    "never_replicated": False,
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
    "as_of_age_seconds": 55,
}
TARGET_ROW = {
    "name": "target1",
    "id": "target1-id",
    "address": "192.0.2.10",
    "status": "connecting",
    "status_details": "Error in SSL Handshake.",
    "as_of": "2021-02-03 13:04:05 UTC",
    "as_of_epoch": 1612357445000,
}
NO_LINKS = {
    "total": 0,
    "by_status": {},
    "paused": 0,
    "unhealthy": 0,
    "never_replicated": 0,
    "max_lag_seconds": None,
    "max_as_of_age_seconds": None,
}
NO_TARGETS = {"total": 0, "not_connected": []}
EVERYTHING = {
    "pods": [POD_ROW],
    "file_systems": [FS_ROW],
    "buckets": [BUCKET_ROW],
    "replication_targets": {"array3": [TARGET_ROW]},
    "summary": {
        "pods": {
            "total": 1,
            "by_status": {"replicating": 1},
            "paused": 0,
            "unhealthy": 0,
            "never_replicated": 0,
            "max_lag_seconds": 127,
            "max_as_of_age_seconds": 55,
        },
        "file_systems": {
            "total": 1,
            "by_status": {"idle": 1},
            "paused": 0,
            "unhealthy": 0,
            "never_replicated": 0,
            "max_lag_seconds": 3964,
            "max_as_of_age_seconds": 1365,
        },
        "buckets": {
            "total": 1,
            "by_status": {"replicating": 1},
            "paused": 0,
            "unhealthy": 0,
            "never_replicated": 0,
            "max_lag_seconds": 94,
            "max_as_of_age_seconds": 55,
        },
        "replication_targets": {"total": 1, "not_connected": ["target1"]},
    },
}
NOTHING = {
    "pods": [],
    "file_systems": [],
    "buckets": [],
    "replication_targets": {},
    "summary": {
        "pods": NO_LINKS,
        "file_systems": NO_LINKS,
        "buckets": NO_LINKS,
        "replication_targets": NO_TARGETS,
    },
}
LISTINGS = (
    "get_pod_replica_links",
    "get_file_system_replica_links",
    "get_bucket_replica_links",
    "get_targets",
)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(pure1_replica_links.time, "time", lambda: NOW)


def _array(name, array_id=None):
    """A reference to an array; arrays of one name differ by their id"""
    return array_reference(
        name, id=array_id or "id-" + name, fqdn=name + ".example.com"
    )


def _pod(name, array, **overrides):
    """A pod of a pod replica link: it has a list of locations"""
    fields = dict(
        id="{0}-on-{1}".format(name, array),
        name=name,
        resource_type="pods",
        locations=[_array(array)],
    )
    fields.update(overrides)
    return sdk_model("ResourceWithLocations", **fields)


def _member(name, array, **overrides):
    """A file system or a bucket of a FlashBlade link: it has one location"""
    fields = dict(
        id="{0}-on-{1}".format(name, array),
        name=name,
        resource_type="file-systems",
        location=_array(array),
    )
    fields.update(overrides)
    return sdk_model("ResourceWithLocation", **fields)


def _pod_link(
    link_id="pod-link-1", source=("pod1", "array1"), target=("pod1-dr", "array2"), **o
):
    fields = dict(
        id=link_id,
        as_of=AS_OF,
        lag=127537,
        paused=False,
        recovery_point=1612357317463,
        status="replicating",
        sources=[_pod(*source)],
        targets=[_pod(*target)],
        members=[_pod(*source), _pod(*target)],
    )
    fields.update(o)
    return sdk_model("PodReplicaLink", **fields)


def _fs_link(
    link_id="fs-link-1", source=("fs1", "array3"), target=("fs1", "array4"), **o
):
    fields = dict(
        id=link_id,
        as_of=1612356135000,
        lag=3964000,
        paused=False,
        recovery_point=1612352171000,
        status="idle",
        sources=[_member(*source)],
        targets=[_member(*target)],
        members=[_member(*source), _member(*target)],
    )
    fields.update(o)
    return sdk_model("FileSystemReplicaLink", **fields)


def _bucket_link(link_id="bucket-link-1", **overrides):
    """A link that replicates in both directions, as Pure1 reports it"""
    fields = dict(
        id=link_id,
        as_of=AS_OF,
        lag=94208,
        paused=False,
        recovery_point=1612353845000,
        status="replicating",
        sources=[_member("bucket1", "array3"), _member("bucket1", "array4")],
        targets=[_member("bucket1", "array4"), _member("bucket1", "array3")],
        members=[_member("bucket1", "array3"), _member("bucket1", "array4")],
    )
    fields.update(overrides)
    return sdk_model("BucketReplicaLink", **fields)


def _target(name="target1", arrays=("array3",), **overrides):
    """A replication target on these arrays, each a name or a reference"""
    fields = dict(
        name=name,
        id=name + "-id",
        address="192.0.2.10",
        status="connecting",
        status_details="Error in SSL Handshake.",
        as_of=AS_OF,
        arrays=[_array(array) if isinstance(array, str) else array for array in arrays],
    )
    fields.update(overrides)
    return sdk_model("Target", **fields)


def _policy(link_id="fs-link-1", name="policy1"):
    return sdk_model(
        "PolicyMember",
        member=sdk_model(
            "FixedReference", id=link_id, resource_type="file-system-replica-links"
        ),
        policy=sdk_model(
            "FixedReference", id=name + "-id", name=name, resource_type="policies"
        ),
    )


def _answer(
    pure1_client, pods=(), file_systems=(), buckets=(), targets=(), policies=()
):
    """Give every call of the module its answer"""
    pure1_client.get_pod_replica_links.return_value = valid_response(pods)
    pure1_client.get_file_system_replica_links.return_value = valid_response(
        file_systems
    )
    pure1_client.get_bucket_replica_links.return_value = valid_response(buckets)
    pure1_client.get_targets.return_value = valid_response(targets)
    pure1_client.get_policies_file_system_replica_links.return_value = valid_response(
        policies
    )


def _answer_everything(pure1_client):
    _answer(
        pure1_client,
        pods=[_pod_link()],
        file_systems=[_fs_link()],
        buckets=[_bucket_link()],
        targets=[_target()],
        policies=[_policy()],
    )


def _arrays_known(pure1_client, *names):
    """Answer the check of get_pure1 and then the request that resolves array

    A name stands for the array that _array() refers to; anything else is
    the array itself.
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


def _result(module):
    module.fail_json.assert_not_called()
    return module.exit_json.call_args[1]["pure1_replica_links"]


def _ids(rows):
    return [row["id"] for row in rows]


class TestMain:
    """The whole result of main()."""

    def test_everything_is_returned(self, pure1_client):
        _answer_everything(pure1_client)

        # every option is left to the argument spec
        module = run_main(pure1_replica_links)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=EVERYTHING
        )
        module.warn.assert_not_called()
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_pod_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_file_system_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_bucket_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_policies_file_system_replica_links.assert_called_once_with()
        pure1_client.get_targets.assert_called_once_with(filter=None)

    def test_the_same_is_returned_in_check_mode(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=EVERYTHING
        )
        pure1_client.get_targets.assert_called_once_with(filter=None)

    def test_nothing_in_pure1_gives_every_key_empty(self, pure1_client):
        _answer(pure1_client)

        module = run_main(pure1_replica_links)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=NOTHING
        )
        module.warn.assert_not_called()
        # without a file-system link there is no policy to ask for
        pure1_client.get_policies_file_system_replica_links.assert_not_called()

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_times_are_utc_whatever_the_host(self, pure1_client):
        _answer_everything(pure1_client)

        result = _result(run_main(pure1_replica_links))

        assert result["pods"][0]["recovery_point"] == "2021-02-03 13:01:57 UTC"
        assert result["pods"][0]["as_of"] == "2021-02-03 13:04:05 UTC"
        assert result["file_systems"][0]["as_of"] == "2021-02-03 12:42:15 UTC"
        assert result["replication_targets"]["array3"][0]["as_of"] == (
            "2021-02-03 13:04:05 UTC"
        )


class TestRequests:
    """The requests each option costs."""

    @pytest.mark.parametrize(
        "params, expected",
        [
            pytest.param({}, [1, 1, 1, 1, 1, 1], id="default"),
            pytest.param({"link_type": ["pod"]}, [1, 1, 0, 0, 0, 1], id="pod"),
            pytest.param(
                {"link_type": ["file_system"]}, [1, 0, 1, 0, 1, 1], id="file_system"
            ),
            pytest.param({"link_type": ["bucket"]}, [1, 0, 0, 1, 0, 1], id="bucket"),
            pytest.param({"link_type": []}, [1, 1, 1, 1, 1, 1], id="no link type"),
            pytest.param(
                {"replication_targets": False}, [1, 1, 1, 1, 1, 0], id="no targets"
            ),
            # no file-system link has the name, so no policy is asked for
            pytest.param({"name": ["pod1"]}, [1, 1, 1, 1, 0, 1], id="name"),
            pytest.param({"name": ["fs1"]}, [1, 1, 1, 1, 1, 1], id="name of fs"),
            pytest.param({"array": ["array3"]}, [2, 1, 1, 1, 1, 1], id="array"),
            pytest.param(
                {"array": ["array3", "array4"], "array_role": "target"},
                [2, 1, 1, 1, 1, 1],
                id="two arrays with a role",
            ),
        ],
    )
    def test_requests_of_a_run(self, pure1_client, params, expected):
        _answer_everything(pure1_client)
        if "array" in params:
            _arrays_known(pure1_client, *params["array"])

        module = run_main(pure1_replica_links, **params)

        module.fail_json.assert_not_called()
        assert [
            pure1_client.get_arrays.call_count,
            pure1_client.get_pod_replica_links.call_count,
            pure1_client.get_file_system_replica_links.call_count,
            pure1_client.get_bucket_replica_links.call_count,
            pure1_client.get_policies_file_system_replica_links.call_count,
            pure1_client.get_targets.call_count,
        ] == expected

    def test_every_page_of_a_listing_is_read(self, pure1_client):
        _answer(pure1_client)
        links = [
            _pod_link("pod-link-{0}".format(number), source=("pod1", "array1"))
            for number in (1, 2, 3)
        ]
        answers = paged_answers("PodReplicaLinkGetResponse", links, page_size=2)
        pure1_client.get_pod_replica_links.side_effect = answers

        result = _result(run_main(pure1_replica_links))

        assert answers.requests == [
            {"filter": None},
            {"filter": None, "continuation_token": "page-2"},
        ]
        assert _ids(result["pods"]) == ["pod-link-1", "pod-link-2", "pod-link-3"]
        assert result["summary"]["pods"]["total"] == 3


def _three(listing):
    """Return three items of a listing, which all reach the result"""
    numbers = (1, 2, 3)
    if listing == "get_pod_replica_links":
        return [_pod_link("link-{0}".format(number)) for number in numbers]
    if listing == "get_file_system_replica_links":
        return [_fs_link("link-{0}".format(number)) for number in numbers]
    if listing == "get_bucket_replica_links":
        return [_bucket_link("link-{0}".format(number)) for number in numbers]
    if listing == "get_targets":
        return [_target("target{0}".format(number)) for number in numbers]
    return [_policy("fs-link-1", "policy{0}".format(number)) for number in numbers]


def _read(listing, result):
    """Return how many items of a listing the result holds"""
    if listing == "get_targets":
        return result["summary"]["replication_targets"]["total"]
    if listing == "get_policies_file_system_replica_links":
        return len(result["file_systems"][0]["policies"])
    return len(result[LISTED[listing][2]])


# The answer class of each listing, what its request carries and where its
# items are in the result
LISTED = {
    "get_pod_replica_links": ("PodReplicaLinkGetResponse", {"filter": None}, "pods"),
    "get_file_system_replica_links": (
        "FileSystemReplicaLinkGetResponse",
        {"filter": None},
        "file_systems",
    ),
    "get_bucket_replica_links": (
        "BucketReplicaLinkGetResponse",
        {"filter": None},
        "buckets",
    ),
    "get_targets": ("TargetGetResponse", {"filter": None}, None),
    "get_policies_file_system_replica_links": ("PolicyMembersGetResponse", {}, None),
}
KINDS = {
    "get_pod_replica_links": "pod replica links",
    "get_file_system_replica_links": "file-system replica links",
    "get_bucket_replica_links": "bucket replica links",
    "get_targets": "replication targets",
    "get_policies_file_system_replica_links": "policies of file-system replica links",
}


class TestListings:
    """Every listing is read page by page, and a short one gives a warning."""

    @pytest.mark.parametrize("listing", list(LISTED))
    def test_listing_of_two_pages_is_read_whole(self, pure1_client, listing):
        _answer_everything(pure1_client)
        response, request = LISTED[listing][:2]
        answers = paged_answers(response, _three(listing), page_size=2)
        getattr(pure1_client, listing).side_effect = answers

        module = run_main(pure1_replica_links)

        assert answers.requests == [
            request,
            dict(request, continuation_token="page-2"),
        ]
        assert _read(listing, _result(module)) == 3
        module.warn.assert_not_called()

    @pytest.mark.parametrize("listing", list(LISTED))
    def test_listing_that_pure1_sends_short_gives_a_warning(
        self, pure1_client, listing
    ):
        """Pure1 counts five and sends three: what is missing is asked for
        once more, from where the listing ended"""
        _answer_everything(pure1_client)
        response, request = LISTED[listing][:2]
        answers = paged_answers(
            response, _three(listing), page_size=2, total_item_count=5
        )
        getattr(pure1_client, listing).side_effect = answers

        module = run_main(pure1_replica_links)

        assert answers.requests == [
            request,
            dict(request, continuation_token="page-2"),
            dict(request, offset=3),
        ]
        assert _read(listing, _result(module)) == 3
        module.warn.assert_called_once_with(
            "Pure1 returned 3 of 5 {0}: the result may be "
            "incomplete.".format(KINDS[listing])
        )

    def test_every_short_listing_gives_its_own_warning(self, pure1_client):
        _answer_everything(pure1_client)
        for listing, (response, _request, _key) in LISTED.items():
            getattr(pure1_client, listing).side_effect = paged_answers(
                response, _three(listing), total_item_count=4
            )

        module = run_main(pure1_replica_links)

        assert [warning.args[0] for warning in module.warn.call_args_list] == [
            "Pure1 returned 3 of 4 {0}: the result may be incomplete.".format(kind)
            for kind in (
                "pod replica links",
                "file-system replica links",
                "bucket replica links",
                "policies of file-system replica links",
                "replication targets",
            )
        ]

    def test_short_listing_without_a_total_gives_the_warning(self, pure1_client):
        """Pure1 counts nothing and gives the first page again for the next"""
        _answer_everything(pure1_client)
        answers = paged_answers("TargetGetResponse", [_target()], total_item_count=None)

        def offset_ignored(**kwargs):
            kwargs.pop("offset", None)
            return answers(**kwargs)

        pure1_client.get_targets.side_effect = offset_ignored

        module = run_main(pure1_replica_links)

        assert answers.requests == [{"filter": None}, {"filter": None}]
        assert _result(module)["replication_targets"] == {"array3": [TARGET_ROW]}
        module.warn.assert_called_once_with(
            "Pure1 returned 1 of an unknown number of replication targets: the "
            "result may be incomplete."
        )

    def test_array_is_in_the_filter_of_every_page(self, pure1_client):
        _answer(pure1_client)
        _arrays_known(pure1_client, "array1")
        links = [
            _pod_link("link-{0}".format(number), ("pod1", "array1"))
            for number in (1, 2, 3)
        ]
        answers = paged_answers("PodReplicaLinkGetResponse", links, page_size=2)
        pure1_client.get_pod_replica_links.side_effect = answers

        result = _result(run_main(pure1_replica_links, array=["array1"]))

        selection = "members.locations.name='array1'"
        assert answers.requests == [
            {"filter": selection},
            {"filter": selection, "continuation_token": "page-2"},
        ]
        assert _ids(result["pods"]) == ["link-1", "link-2", "link-3"]


class TestLinkType:
    """The link_type option."""

    @pytest.mark.parametrize(
        "link_type, key, row",
        [
            pytest.param("pod", "pods", POD_ROW, id="pod"),
            pytest.param("file_system", "file_systems", FS_ROW, id="file_system"),
            pytest.param("bucket", "buckets", BUCKET_ROW, id="bucket"),
        ],
    )
    def test_one_type_alone(self, pure1_client, link_type, key, row):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, link_type=[link_type])

        expected = dict(NOTHING, replication_targets={"array3": [TARGET_ROW]})
        expected[key] = [row]
        expected["summary"] = dict(
            NOTHING["summary"],
            replication_targets=EVERYTHING["summary"]["replication_targets"],
        )
        expected["summary"][key] = EVERYTHING["summary"][key]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )

    def test_two_types_in_any_order_and_one_twice(self, pure1_client):
        _answer_everything(pure1_client)

        result = _result(
            run_main(pure1_replica_links, link_type=["bucket", "pod", "bucket"])
        )

        pure1_client.get_bucket_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_pod_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_file_system_replica_links.assert_not_called()
        assert result["pods"] == [POD_ROW]
        assert result["file_systems"] == []
        assert result["buckets"] == [BUCKET_ROW]

    def test_empty_list_of_types_counts_as_not_set(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, link_type=[])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=EVERYTHING
        )

    def test_unknown_type_is_refused(self, pure1_client, client_factory):
        refused = _refused_by_the_spec(
            {
                "link_type": {
                    "type": "list",
                    "elements": "str",
                    "choices": ["pod", "file_system", "bucket"],
                }
            },
            {"link_type": ["pod", "volume"]},
        )

        module = run_main(pure1_replica_links, link_type=["pod", "volume"])

        # the choices of the argument spec, as ansible-core words them
        assert "volume" in refused
        module.fail_json.assert_called_once_with(msg=refused)
        module.exit_json.assert_not_called()
        # before the client is made
        client_factory.assert_not_called()
        pure1_client.get_pod_replica_links.assert_not_called()


class TestArray:
    """The array and array_role options."""

    def test_array_goes_into_the_filter_of_every_listing(self, pure1_client):
        _answer_everything(pure1_client)
        # Pure1 knows the arrays as array3 and Array4
        _arrays_known(pure1_client, "array3", "Array4")

        module = run_main(pure1_replica_links, array=["ARRAY3", "array4"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('ARRAY3','array4')"),
        ]
        # the names Pure1 returned are the ones the listings are filtered with
        pure1_client.get_pod_replica_links.assert_called_once_with(
            filter="members.locations.name=('array3','Array4')"
        )
        pure1_client.get_file_system_replica_links.assert_called_once_with(
            filter="members.location.name=('array3','Array4')"
        )
        pure1_client.get_bucket_replica_links.assert_called_once_with(
            filter="members.location.name=('array3','Array4')"
        )
        pure1_client.get_targets.assert_called_once_with(
            filter="arrays.name=('array3','Array4')"
        )
        pure1_client.get_policies_file_system_replica_links.assert_called_once_with()
        # the pod link is on other arrays: the module leaves it out although
        # the answer holds it
        expected = dict(EVERYTHING, pods=[])
        expected["summary"] = dict(EVERYTHING["summary"], pods=NO_LINKS)
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )
        module.warn.assert_not_called()

    def test_one_array_is_one_value_in_the_filter(self, pure1_client):
        _answer_everything(pure1_client)
        _arrays_known(pure1_client, "array1")

        result = _result(run_main(pure1_replica_links, array=["array1"]))

        pure1_client.get_pod_replica_links.assert_called_once_with(
            filter="members.locations.name='array1'"
        )
        pure1_client.get_file_system_replica_links.assert_called_once_with(
            filter="members.location.name='array1'"
        )
        pure1_client.get_targets.assert_called_once_with(filter="arrays.name='array1'")
        assert _ids(result["pods"]) == ["pod-link-1"]
        assert result["file_systems"] == []
        assert result["buckets"] == []
        assert result["replication_targets"] == {}

    def test_array_that_pure1_does_not_know_fails_the_task(self, pure1_client):
        _answer_everything(pure1_client)
        _arrays_known(pure1_client, "array1")

        module = run_main(pure1_replica_links, array=["array1", "array9"])

        module.fail_json.assert_called_once_with(
            msg="Array array9 is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        for listing in LISTINGS:
            getattr(pure1_client, listing).assert_not_called()

    @pytest.mark.parametrize(
        "role, expected",
        [
            pytest.param(None, ["link-b", "link-a"], id="default"),
            pytest.param("any", ["link-b", "link-a"], id="any"),
            pytest.param("source", ["link-a"], id="source"),
            pytest.param("target", ["link-b"], id="target"),
        ],
    )
    def test_array_role_selects_a_side_of_the_link(self, pure1_client, role, expected):
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", ("pod1", "array2"), ("pod1-dr", "array1")),
                _pod_link("link-b", ("pod2", "array1"), ("pod2-dr", "array2")),
                _pod_link("link-c", ("pod3", "array1"), ("pod3-dr", "array3")),
            ],
            file_systems=[
                _fs_link("link-a", ("fs1", "array2"), ("fs1", "array1")),
                _fs_link("link-b", ("fs2", "array1"), ("fs2", "array2")),
                _fs_link("link-c", ("fs3", "array1"), ("fs3", "array3")),
            ],
        )
        _arrays_known(pure1_client, "array2")
        params = dict(array=["array2"])
        if role is not None:
            params["array_role"] = role

        result = _result(run_main(pure1_replica_links, **params))

        # in the order of the source array: link-b has its source on array1
        assert _ids(result["pods"]) == expected
        assert _ids(result["file_systems"]) == expected
        assert result["summary"]["pods"]["total"] == len(expected)
        # the role changes no request
        pure1_client.get_pod_replica_links.assert_called_once_with(
            filter="members.locations.name='array2'"
        )
        pure1_client.get_file_system_replica_links.assert_called_once_with(
            filter="members.location.name='array2'"
        )

    @pytest.mark.parametrize("role", ["any", "source", "target"])
    @pytest.mark.parametrize("array", ["array3", "array4"])
    def test_link_in_both_directions_matches_either_role(
        self, pure1_client, role, array
    ):
        _answer(pure1_client, buckets=[_bucket_link()])
        _arrays_known(pure1_client, array)

        result = _result(run_main(pure1_replica_links, array=[array], array_role=role))

        assert result["buckets"] == [BUCKET_ROW]

    @pytest.mark.parametrize(
        "role, expected",
        [
            # a link without a source comes first
            pytest.param("any", ["link-b", "link-a"], id="any"),
            pytest.param("source", ["link-a"], id="source"),
            pytest.param("target", [], id="target"),
        ],
    )
    def test_target_without_a_location_matches_no_array(
        self, pure1_client, role, expected
    ):
        """Pure1 has pod links whose target has neither a name nor a location"""
        bare = sdk_model("ResourceWithLocations", resource_type="pods", locations=[])
        link = _pod_link("link-a", ("pod1", "array1"), targets=[bare])
        # a member that is neither among the sources nor among the targets
        other = _pod_link("link-b", sources=None, targets=None)
        _answer(pure1_client, pods=[link, other])
        _arrays_known(pure1_client, "array1")

        result = _result(
            run_main(pure1_replica_links, array=["array1"], array_role=role)
        )

        assert _ids(result["pods"]) == expected

    def test_member_array_is_matched_by_its_id_not_by_its_name(self, pure1_client):
        """Pure1 compares names whatever their case: asked for array1, it
        also sends the links of ARRAY1, which is another array"""
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", ("pod1", "ARRAY1")),
                _pod_link("link-b", ("pod2", "array1")),
            ],
            file_systems=[
                _fs_link("link-a", ("fs1", "ARRAY1")),
                _fs_link("link-b", ("fs2", "array1")),
            ],
            buckets=[
                _bucket_link(
                    "link-a",
                    sources=[_member("bucket1", "ARRAY1")],
                    targets=[_member("bucket1", "array4")],
                ),
                _bucket_link(
                    "link-b",
                    sources=[_member("bucket2", "array1")],
                    targets=[_member("bucket2", "array4")],
                ),
            ],
        )
        _arrays_known(pure1_client, "array1")

        module = run_main(pure1_replica_links, array=["Array1"], array_role="source")
        result = _result(module)

        # Pure1 is asked by the name, as it spells it
        pure1_client.get_pod_replica_links.assert_called_once_with(
            filter="members.locations.name='array1'"
        )
        assert _ids(result["pods"]) == ["link-b"]
        assert _ids(result["file_systems"]) == ["link-b"]
        assert _ids(result["buckets"]) == ["link-b"]
        assert result["summary"]["pods"]["total"] == 1
        assert result["pods"][0]["sources"][0]["arrays"][0]["name"] == "array1"
        # a link of another array is not of the selection: no warning
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "role, expected",
        [
            pytest.param("any", ["link-b", "link-c"], id="any"),
            pytest.param("source", ["link-b"], id="source"),
            pytest.param("target", ["link-c"], id="target"),
        ],
    )
    def test_array_of_the_same_name_is_told_apart_on_either_side(
        self, pure1_client, role, expected
    ):
        """Two arrays are called array1; the task gets the one Pure1 resolves"""

        def on_the_other(pod):
            return _pod(pod, "array1", locations=[_array("array1", "id-other")])

        def link(link_id, source, target):
            return _pod_link(
                link_id, sources=[source], targets=[target], members=[source, target]
            )

        _answer(
            pure1_client,
            pods=[
                link("link-a", on_the_other("pod1"), _pod("pod1-dr", "array2")),
                _pod_link("link-b", ("pod2", "array1"), ("pod2-dr", "array2")),
                _pod_link("link-c", ("pod3", "array2"), ("pod3-dr", "array1")),
                link("link-d", _pod("pod4", "array2"), on_the_other("pod4-dr")),
            ],
        )
        _arrays_known(pure1_client, "array1")

        result = _result(
            run_main(pure1_replica_links, array=["array1"], array_role=role)
        )

        assert _ids(result["pods"]) == expected

    def test_reference_without_an_id_is_matched_by_the_name_as_pure1_spells_it(
        self, pure1_client
    ):
        def without_id(pod, array):
            return _pod(pod, array, locations=[array_reference(array)])

        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", sources=[without_id("pod1", "array1")]),
                _pod_link("link-b", sources=[without_id("pod2", "ARRAY1")]),
            ],
            targets=[
                sdk_model("Target", name="target1", arrays=[array_reference("array1")]),
                sdk_model("Target", name="target2", arrays=[array_reference("ARRAY1")]),
            ],
        )
        _arrays_known(pure1_client, "array1")

        module = run_main(pure1_replica_links, array=["array1"], array_role="source")
        result = _result(module)

        assert _ids(result["pods"]) == ["link-a"]
        assert list(result["replication_targets"]) == ["array1"]
        assert result["summary"]["replication_targets"]["total"] == 1
        module.warn.assert_not_called()

    def test_array_without_an_id_is_matched_by_its_name(self, pure1_client):
        """Pure1 sends every array with its id; without one only the name is left"""
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", ("pod1", "array1")),
                _pod_link("link-b", ("pod2", "ARRAY1")),
            ],
            targets=[
                _target("target1", arrays=("array1",)),
                _target("target2", arrays=("ARRAY1",)),
            ],
        )
        _arrays_known(pure1_client, sdk_model("Array", name="array1"))

        result = _result(run_main(pure1_replica_links, array=["array1"]))

        assert _ids(result["pods"]) == ["link-a"]
        assert list(result["replication_targets"]) == ["array1"]

    def test_links_of_an_array_that_pure1_spells_with_a_capital(self, pure1_client):
        """The only array of the task is Array1 in Pure1 and on the link"""
        _answer(pure1_client, pods=[_pod_link(source=("pod1", "Array1"))])
        _arrays_known(pure1_client, "Array1")

        result = _result(run_main(pure1_replica_links, array=["array1"]))

        pure1_client.get_pod_replica_links.assert_called_once_with(
            filter="members.locations.name='Array1'"
        )
        assert _ids(result["pods"]) == ["pod-link-1"]
        assert result["pods"][0]["sources"][0]["arrays"][0]["name"] == "Array1"

    def test_targets_of_an_array_that_pure1_spells_with_a_capital(self, pure1_client):
        _answer(pure1_client, targets=[_target(arrays=("Array1",))])
        _arrays_known(pure1_client, "Array1")

        result = _result(run_main(pure1_replica_links, array=["array1"]))

        pure1_client.get_targets.assert_called_once_with(filter="arrays.name='Array1'")
        assert result["replication_targets"] == {"Array1": [TARGET_ROW]}
        assert result["summary"]["replication_targets"] == {
            "total": 1,
            "not_connected": ["target1"],
        }

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"array": ["array1"]}, id="array of the source"),
            pytest.param({"array": ["array2"]}, id="array of the target"),
            pytest.param({"name": ["pod1"]}, id="name of the source"),
            pytest.param({"name": ["pod1-dr"]}, id="name of the target"),
        ],
    )
    def test_link_without_a_members_list_is_selected_by_either_side(
        self, pure1_client, params
    ):
        """The default role reads the sources and the targets themselves"""
        _answer(pure1_client, pods=[_pod_link(members=None)])
        if "array" in params:
            _arrays_known(pure1_client, *params["array"])

        module = run_main(pure1_replica_links, **params)

        assert _result(module)["pods"] == [POD_ROW]
        module.warn.assert_not_called()

    def test_unknown_role_is_refused(self, pure1_client):
        module = run_main(pure1_replica_links, array=["array1"], array_role="both")

        module.fail_json.assert_called_once_with(
            msg="value of array_role must be one of: any, source, target, got: both"
        )

    @pytest.mark.parametrize("role", ["source", "target"])
    def test_role_without_array_is_refused(self, pure1_client, client_factory, role):
        refused = _refused_by_the_spec(
            {"array_role": {"type": "str"}, "array": {"type": "list"}},
            {"array_role": role},
            required_if=[["array_role", role, ["array"]]],
        )

        module = run_main(pure1_replica_links, array_role=role)

        # the required_if of the argument spec, as ansible-core words it
        module.fail_json.assert_called_once_with(msg=refused)
        module.exit_json.assert_not_called()
        # before the client is made, where the module's own check comes after
        client_factory.assert_not_called()
        pure1_client.get_pod_replica_links.assert_not_called()

    @pytest.mark.parametrize("role", ["source", "target"])
    def test_role_with_an_empty_array_list_is_refused(self, pure1_client, role):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, array=[], array_role=role)

        module.fail_json.assert_called_once_with(
            msg="array_role is {0} but array names no array".format(role)
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        for listing in LISTINGS:
            getattr(pure1_client, listing).assert_not_called()

    def test_empty_array_list_is_no_selection(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, array=[])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=EVERYTHING
        )
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_targets.assert_called_once_with(filter=None)

    def test_targets_of_the_arrays_alone(self, pure1_client):
        _answer(
            pure1_client,
            targets=[
                _target("target1", arrays=("array3", "array4")),
                # not on the array: left out without a warning
                _target("target2", arrays=("array5",)),
                # ARRAY4 is another array, which Pure1 sends for the name too
                _target("target3", arrays=("ARRAY4",)),
                _target("target4", arrays=("ARRAY4", "array4"), status="connected"),
            ],
        )
        _arrays_known(pure1_client, "array4")

        module = run_main(pure1_replica_links, array=["array4"], array_role="source")
        result = _result(module)

        pure1_client.get_targets.assert_called_once_with(filter="arrays.name='array4'")
        assert result["replication_targets"] == {
            "array4": [
                TARGET_ROW,
                dict(TARGET_ROW, name="target4", id="target4-id", status="connected"),
            ]
        }
        assert result["summary"]["replication_targets"] == {
            "total": 2,
            "not_connected": ["target1"],
        }
        module.warn.assert_not_called()

    def test_targets_of_two_arrays_of_one_name(self, pure1_client):
        """The array that Pure1 lists first keeps the name in the result"""
        other = _array("array3", "id-other")
        _answer(
            pure1_client,
            targets=[
                _target("target1"),
                # on the other array3 alone, which Pure1 names twice
                _target("target2", arrays=(other, other), status="connected"),
                # on the other array3 and on array4
                _target("target3", arrays=(other, "array4")),
                _target("target4", status="connected"),
                # on the other array3 and on another array4
                _target("target5", arrays=(other, _array("array4", "id-other4"))),
            ],
        )

        module = run_main(pure1_replica_links)
        result = _result(module)

        connected = dict(TARGET_ROW, status="connected")
        assert result["replication_targets"] == {
            "array3": [TARGET_ROW, dict(connected, name="target4", id="target4-id")],
            "array4": [dict(TARGET_ROW, name="target3", id="target3-id")],
        }
        # target2 and target5 are nowhere in the result and in no count
        assert result["summary"]["replication_targets"] == {
            "total": 3,
            "not_connected": ["target1", "target3"],
        }
        # one for each array that a target is left out of: target2 once,
        # target3 once and target5 twice
        module.warn.assert_called_once_with(
            "Replication targets of an array that has the name of another "
            "array are left out of the result: 4."
        )

    def test_both_arrays_of_one_name_are_of_the_task(self, pure1_client):
        """Pure1 has two arrays for the name of the task: the first one
        listed keeps the name, the targets of the other are counted"""
        other = _target("target2", arrays=(_array("array3", "id-other"),))
        _answer(pure1_client, targets=[other, _target("target1"), _target("target3")])
        _arrays_known(
            pure1_client,
            sdk_model("Array", name="array3", id="id-array3"),
            sdk_model("Array", name="array3", id="id-other"),
        )

        module = run_main(pure1_replica_links, array=["array3"], link_type=["pod"])
        result = _result(module)

        assert result["replication_targets"] == {
            "array3": [dict(TARGET_ROW, name="target2", id="target2-id")]
        }
        assert result["summary"]["replication_targets"]["total"] == 1
        module.warn.assert_called_once_with(
            "Replication targets of an array that has the name of another "
            "array are left out of the result: 2."
        )

    def test_target_array_without_an_id_is_taken_for_the_array_of_the_name(
        self, pure1_client
    ):
        _answer(
            pure1_client,
            targets=[
                sdk_model("Target", name="target1", arrays=[array_reference("array3")]),
                _target("target2"),
                sdk_model("Target", name="target3", arrays=[array_reference("array3")]),
            ],
        )

        module = run_main(pure1_replica_links)
        result = _result(module)

        names = [row["name"] for row in result["replication_targets"]["array3"]]
        assert names == ["target1", "target2", "target3"]
        module.warn.assert_not_called()


class TestName:
    """The name option."""

    @pytest.mark.parametrize(
        "names, pods, file_systems, buckets",
        [
            pytest.param(["pod1"], ["pod-link-1"], [], [], id="source"),
            pytest.param(["POD1-DR"], ["pod-link-1"], [], [], id="target, other case"),
            pytest.param(["fs1"], [], ["fs-link-1"], [], id="file system"),
            pytest.param(["Bucket1"], [], [], ["bucket-link-1"], id="bucket"),
            pytest.param(
                ["bucket1", "pod1", "pod1"],
                ["pod-link-1"],
                [],
                ["bucket-link-1"],
                id="two types",
            ),
            pytest.param(
                [], ["pod-link-1"], ["fs-link-1"], ["bucket-link-1"], id="empty list"
            ),
        ],
    )
    def test_links_with_a_member_of_that_name(
        self, pure1_client, names, pods, file_systems, buckets
    ):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, name=names)
        result = _result(module)

        assert _ids(result["pods"]) == pods
        assert _ids(result["file_systems"]) == file_systems
        assert _ids(result["buckets"]) == buckets
        assert result["summary"]["pods"]["total"] == len(pods)
        assert result["summary"]["file_systems"]["total"] == len(file_systems)
        assert result["summary"]["buckets"]["total"] == len(buckets)
        # the names are compared in the module: no request carries them
        pure1_client.get_pod_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_file_system_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_bucket_replica_links.assert_called_once_with(filter=None)
        # and the targets are not narrowed
        pure1_client.get_targets.assert_called_once_with(filter=None)
        assert result["replication_targets"] == {"array3": [TARGET_ROW]}
        module.warn.assert_not_called()

    def test_name_of_a_member_that_is_no_source_and_no_target(self, pure1_client):
        link = _pod_link(sources=None, targets=None)
        _answer(pure1_client, pods=[link, _pod_link("pod-link-2", ("pod2", "array1"))])

        result = _result(run_main(pure1_replica_links, name=["pod1"]))

        assert _ids(result["pods"]) == ["pod-link-1"]

    def test_name_that_pure1_spells_in_another_case(self, pure1_client):
        _answer(
            pure1_client,
            pods=[
                _pod_link("pod-link-1", ("Pod1", "array1")),
                _pod_link("pod-link-2", ("pod2", "array1")),
            ],
        )

        module = run_main(pure1_replica_links, name=["pod1"])
        result = _result(module)

        assert _ids(result["pods"]) == ["pod-link-1"]
        # the result carries the name as Pure1 spells it
        assert result["pods"][0]["sources"][0]["name"] == "Pod1"
        module.warn.assert_not_called()

    def test_name_that_matches_nothing_gives_a_warning(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, name=["pod1", "Pod9"])
        result = _result(module)

        assert _ids(result["pods"]) == ["pod-link-1"]
        module.warn.assert_called_once_with(
            "The name Pod9 matches no member of the selected replica links: "
            "the result holds no link for it."
        )

    def test_names_that_match_nothing_give_one_warning(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(
            pure1_replica_links, name=["fs9", "pod1", "Pod9", "fs9"], link_type=["pod"]
        )

        module.warn.assert_called_once_with(
            "The names fs9, Pod9 match no member of the selected replica links: "
            "the result holds no link for them."
        )
        expected = dict(NOTHING, pods=[POD_ROW])
        expected["replication_targets"] = {"array3": [TARGET_ROW]}
        expected["summary"] = dict(
            NOTHING["summary"],
            pods=EVERYTHING["summary"]["pods"],
            replication_targets=EVERYTHING["summary"]["replication_targets"],
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )

    def test_name_and_array_must_both_match(self, pure1_client):
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", ("pod1", "array1"), ("pod1-dr", "array2")),
                _pod_link("link-b", ("pod1", "array3"), ("pod1-dr", "array4")),
                _pod_link("link-c", ("pod2", "array1"), ("pod2-dr", "array2")),
            ],
        )
        _arrays_known(pure1_client, "array1")

        module = run_main(
            pure1_replica_links, name=["pod1", "pod2-dr"], array=["array1"]
        )
        result = _result(module)

        assert _ids(result["pods"]) == ["link-a", "link-c"]
        module.warn.assert_not_called()

    def test_name_on_another_array_counts_as_not_matched(self, pure1_client):
        _answer(
            pure1_client,
            pods=[_pod_link("link-b", ("pod1", "array3"), ("pod1-dr", "array4"))],
        )
        _arrays_known(pure1_client, "array1")

        module = run_main(pure1_replica_links, name=["pod1"], array=["array1"])

        assert _result(module)["pods"] == []
        module.warn.assert_called_once_with(
            "The name pod1 matches no member of the selected replica links: "
            "the result holds no link for it."
        )


class TestLinks:
    """What a link is reported as."""

    def test_link_with_only_its_id(self, pure1_client):
        _answer(pure1_client, pods=[sdk_model("PodReplicaLink", id="pod-link-1")])

        module = run_main(pure1_replica_links, replication_targets=False)

        expected = dict(
            NOTHING,
            pods=[
                {
                    "id": "pod-link-1",
                    "sources": [],
                    "targets": [],
                    "bidirectional": False,
                    "status": None,
                    "status_details": None,
                    "paused": None,
                    "lag": None,
                    "lag_seconds": None,
                    "recovery_point": None,
                    "recovery_point_epoch": None,
                    "never_replicated": True,
                    "as_of": None,
                    "as_of_epoch": None,
                    "as_of_age_seconds": None,
                }
            ],
        )
        expected["summary"] = dict(
            NOTHING["summary"], pods=dict(NO_LINKS, total=1, never_replicated=1)
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )
        module.warn.assert_not_called()

    def test_link_without_any_field_is_still_reported(self, pure1_client):
        _answer(
            pure1_client,
            file_systems=[sdk_model("FileSystemReplicaLink"), _fs_link()],
            buckets=[sdk_model("BucketReplicaLink")],
            policies=[_policy()],
        )

        module = run_main(pure1_replica_links, replication_targets=False)
        result = _result(module)

        empty = {
            "id": None,
            "sources": [],
            "targets": [],
            "bidirectional": False,
            "status": None,
            "status_details": None,
            "paused": None,
            "lag": None,
            "lag_seconds": None,
            "recovery_point": None,
            "recovery_point_epoch": None,
            "never_replicated": True,
            "as_of": None,
            "as_of_epoch": None,
            "as_of_age_seconds": None,
        }
        # a link without a source comes first
        assert result["file_systems"] == [dict(empty, policies=[]), FS_ROW]
        assert result["buckets"] == [empty]
        assert result["summary"]["file_systems"] == dict(
            EVERYTHING["summary"]["file_systems"], total=2, never_replicated=1
        )
        assert result["summary"]["buckets"] == dict(
            NO_LINKS, total=1, never_replicated=1
        )
        module.warn.assert_not_called()

    def test_link_that_never_replicated(self, pure1_client):
        """As Pure1 has it: recovery point 0, the lag is the time since 1970,
        and the source has neither a name nor a location"""
        never = _fs_link(
            "fs-link-0",
            target=("fs0", "array4"),
            sources=[sdk_model("ResourceWithLocation", resource_type="file-systems")],
            recovery_point=0,
            lag=AS_OF,
            as_of=AS_OF,
        )
        _answer(pure1_client, file_systems=[_fs_link(), never], policies=[_policy()])

        result = _result(run_main(pure1_replica_links, replication_targets=False))

        assert result["file_systems"] == [
            {
                "id": "fs-link-0",
                "sources": [{"name": None, "id": None, "arrays": []}],
                "targets": [
                    {
                        "name": "fs0",
                        "id": "fs0-on-array4",
                        "arrays": [{"name": "array4", "fqdn": "array4.example.com"}],
                    }
                ],
                "bidirectional": False,
                "status": "idle",
                "status_details": None,
                "paused": False,
                "lag": 1612357445000,
                "lag_seconds": 1612357445,
                "recovery_point": None,
                "recovery_point_epoch": 0,
                "never_replicated": True,
                "as_of": "2021-02-03 13:04:05 UTC",
                "as_of_epoch": 1612357445000,
                "as_of_age_seconds": 55,
                "policies": [],
            },
            FS_ROW,
        ]
        # neither its lag nor its age is in a maximum
        assert result["summary"]["file_systems"] == {
            "total": 2,
            "by_status": {"idle": 2},
            "paused": 0,
            "unhealthy": 0,
            "never_replicated": 1,
            "max_lag_seconds": 3964,
            "max_as_of_age_seconds": 1365,
        }

    def test_only_links_that_never_replicated_give_no_maximum(self, pure1_client):
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-a", recovery_point=None),
                _pod_link("link-b", recovery_point=0),
            ],
        )

        result = _result(run_main(pure1_replica_links))

        assert [row["never_replicated"] for row in result["pods"]] == [True, True]
        assert [row["recovery_point_epoch"] for row in result["pods"]] == [None, 0]
        assert [row["lag_seconds"] for row in result["pods"]] == [127, 127]
        assert result["summary"]["pods"] == {
            "total": 2,
            "by_status": {"replicating": 2},
            "paused": 0,
            "unhealthy": 0,
            "never_replicated": 2,
            "max_lag_seconds": None,
            "max_as_of_age_seconds": None,
        }

    def test_summary_counts_and_maximum_values(self, pure1_client):
        _answer(
            pure1_client,
            buckets=[
                _bucket_link("link-a", lag=1999, as_of=AS_OF - 4000),
                _bucket_link(
                    "link-b",
                    lag=2000,
                    status="unhealthy",
                    status_details="The target is not reachable.",
                    paused=True,
                ),
                _bucket_link("link-c", lag=None, status=None, paused=None, as_of=None),
                _bucket_link("link-d", lag=0, status="unhealthy", paused=True),
                _bucket_link("link-e", lag=999, status="idle", paused=False),
            ],
        )

        result = _result(run_main(pure1_replica_links, link_type=["bucket"]))

        rows = result["buckets"]
        assert _ids(rows) == ["link-a", "link-b", "link-c", "link-d", "link-e"]
        # whole seconds, rounded down
        assert [row["lag_seconds"] for row in rows] == [1, 2, None, 0, 0]
        assert [row["as_of_age_seconds"] for row in rows] == [59, 55, None, 55, 55]
        assert [row["status_details"] for row in rows] == [
            None,
            "The target is not reachable.",
            None,
            None,
            None,
        ]
        assert [row["paused"] for row in rows] == [False, True, None, True, False]
        assert result["summary"]["buckets"] == {
            "total": 5,
            # the link without a status is in no count of a status
            "by_status": {"replicating": 1, "unhealthy": 2, "idle": 1},
            "paused": 2,
            "unhealthy": 2,
            "never_replicated": 0,
            "max_lag_seconds": 2,
            "max_as_of_age_seconds": 59,
        }

    def test_age_is_measured_by_the_clock_of_the_host(self, pure1_client, monkeypatch):
        """A host whose clock is behind Pure1 gives an age below zero"""
        monkeypatch.setattr(
            pure1_replica_links.time, "time", lambda: (AS_OF - 1500) / 1000
        )
        _answer(pure1_client, pods=[_pod_link()])

        result = _result(run_main(pure1_replica_links))

        assert result["pods"][0]["as_of_age_seconds"] == -2
        assert result["summary"]["pods"]["max_as_of_age_seconds"] == -2

    def test_age_counts_the_fraction_of_a_second_of_the_clock(self, pure1_client):
        """55.2 seconds before 13:05:00.5; from 13:05:00 it would be 54.7"""
        _answer(pure1_client, pods=[_pod_link(as_of=AS_OF + 300)])

        result = _result(run_main(pure1_replica_links))

        assert result["pods"][0]["as_of_epoch"] == 1612357445300
        assert result["pods"][0]["as_of_age_seconds"] == 55

    def test_clock_is_read_after_the_links_and_only_once(
        self, pure1_client, monkeypatch
    ):
        """A link that Pure1 refreshed while the listings were read has no
        age below zero"""
        _answer_everything(pure1_client)
        listings_read = []

        def clock():
            listings_read.append(
                [getattr(pure1_client, listing).call_count for listing in LISTINGS[:3]]
            )
            return NOW

        monkeypatch.setattr(pure1_replica_links.time, "time", clock)

        module = run_main(pure1_replica_links)

        assert listings_read == [[1, 1, 1]]
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=EVERYTHING
        )

    def test_links_are_ordered_by_source_and_target(self, pure1_client):
        _answer(
            pure1_client,
            pods=[
                _pod_link("link-7", ("pod2", "array2"), ("pod2-dr", "array1")),
                _pod_link("link-6", ("pod1", "array2"), ("pod1-dr", "array3")),
                _pod_link("link-5", ("pod1", "array2"), ("pod1-dr", "array1")),
                _pod_link("link-4", ("pod1", "array2"), ("pod1-a", "array3")),
                _pod_link("link-3", ("pod9", "array1"), ("pod9-dr", "array2")),
                _pod_link("link-2", ("pod1", "array2"), ("pod1-a", "array3")),
                _pod_link("link-1", sources=None, targets=None),
            ],
        )

        result = _result(run_main(pure1_replica_links))

        assert _ids(result["pods"]) == [
            "link-1",
            "link-3",
            "link-5",
            "link-2",
            "link-4",
            "link-6",
            "link-7",
        ]

    def test_members_as_pure1_can_send_them(self, pure1_client):
        """References without a field, null entries and a pod on two arrays"""
        stretched = _pod(
            "pod1", "array1", locations=[_array("array1"), _array("array2")]
        )
        no_fqdn = _pod("pod2", "array3", locations=[array_reference("array3")])
        only_fqdn = _pod(
            "pod3",
            "array4",
            id=None,
            locations=[sdk_model("FixedReferenceFqdn", fqdn="array4.example.com")],
        )
        no_locations = _pod("pod4", "array5", locations=None)
        empty_reference = _pod(
            "pod5", "array6", locations=[sdk_model("FixedReferenceFqdn")]
        )
        null_location = _pod("pod6", "array7")
        null_location.locations[0] = None
        link = _pod_link(
            sources=[stretched, no_fqdn, only_fqdn],
            targets=[no_locations, empty_reference, null_location],
        )
        link.sources.append(None)
        link.targets.insert(0, None)
        no_location = _member("fs1", "array3", location=None)
        empty_location = sdk_model(
            "ResourceWithLocation",
            id="fs2-on-array4",
            location=sdk_model("FixedReferenceFqdn"),
        )
        fs_link = _fs_link(sources=[no_location], targets=[empty_location])
        _answer(pure1_client, pods=[link], file_systems=[fs_link])

        result = _result(run_main(pure1_replica_links))

        assert result["pods"][0]["sources"] == [
            {
                "name": "pod1",
                "id": "pod1-on-array1",
                "arrays": [
                    {"name": "array1", "fqdn": "array1.example.com"},
                    {"name": "array2", "fqdn": "array2.example.com"},
                ],
            },
            {
                "name": "pod2",
                "id": "pod2-on-array3",
                "arrays": [{"name": "array3", "fqdn": None}],
            },
            {
                "name": "pod3",
                "id": None,
                "arrays": [{"name": None, "fqdn": "array4.example.com"}],
            },
        ]
        assert result["pods"][0]["targets"] == [
            {"name": "pod4", "id": "pod4-on-array5", "arrays": []},
            {"name": "pod5", "id": "pod5-on-array6", "arrays": []},
            {"name": "pod6", "id": "pod6-on-array7", "arrays": []},
        ]
        assert result["file_systems"][0]["sources"] == [
            {"name": "fs1", "id": "fs1-on-array3", "arrays": []}
        ]
        assert result["file_systems"][0]["targets"] == [
            {"name": None, "id": "fs2-on-array4", "arrays": []}
        ]

    @pytest.mark.parametrize(
        "sources, targets, expected",
        [
            pytest.param(
                [("bucket1", "array3")], [("bucket1", "array4")], False, id="one way"
            ),
            pytest.param(
                [("bucket1", "array3"), ("bucket1", "array4")],
                [("bucket1", "array4")],
                True,
                id="one of two",
            ),
            pytest.param(
                [("bucket1", "array3")], [("bucket1", "array3")], True, id="same"
            ),
        ],
    )
    def test_bidirectional_is_a_source_that_is_a_target(
        self, pure1_client, sources, targets, expected
    ):
        link = _bucket_link(
            sources=[_member(*source) for source in sources],
            targets=[_member(*target) for target in targets],
        )
        _answer(pure1_client, buckets=[link])

        result = _result(run_main(pure1_replica_links))

        assert result["buckets"][0]["bidirectional"] is expected

    def test_members_without_an_id_are_not_taken_for_the_same(self, pure1_client):
        link = _bucket_link(
            sources=[_member("bucket1", "array3", id=None)],
            targets=[_member("bucket1", "array3", id=None)],
        )
        _answer(pure1_client, buckets=[link])

        result = _result(run_main(pure1_replica_links))

        assert result["buckets"][0]["bidirectional"] is False


class TestPolicies:
    """The policies of the file-system links."""

    def test_policies_are_joined_by_the_id_of_the_link(self, pure1_client):
        _answer(
            pure1_client,
            file_systems=[
                _fs_link("fs-link-1", ("fs1", "array3")),
                _fs_link("fs-link-2", ("fs2", "array3")),
                _fs_link("fs-link-3", ("fs3", "array3")),
            ],
            policies=[
                _policy("fs-link-2", "policy2"),
                _policy("fs-link-1", "policy1"),
                _policy("fs-link-2", "policy1"),
                _policy("fs-link-2", "policy2"),
                # a link that is not in the result
                _policy("fs-link-9", "policy9"),
            ],
        )

        module = run_main(pure1_replica_links)
        result = _result(module)

        pure1_client.get_policies_file_system_replica_links.assert_called_once_with()
        assert [(row["id"], row["policies"]) for row in result["file_systems"]] == [
            ("fs-link-1", ["policy1"]),
            ("fs-link-2", ["policy1", "policy2"]),
            ("fs-link-3", []),
        ]
        module.warn.assert_not_called()

    def test_only_file_system_links_have_policies(self, pure1_client):
        _answer_everything(pure1_client)

        result = _result(run_main(pure1_replica_links))

        assert "policies" in result["file_systems"][0]
        assert "policies" not in result["pods"][0]
        assert "policies" not in result["buckets"][0]

    def test_policy_that_cannot_be_joined_is_left_out(self, pure1_client):
        no_link = sdk_model(
            "PolicyMember", policy=sdk_model("FixedReference", name="policy2")
        )
        empty_link = sdk_model(
            "PolicyMember",
            member=sdk_model("FixedReference"),
            policy=sdk_model("FixedReference", name="policy3"),
        )
        no_name = sdk_model(
            "PolicyMember",
            member=sdk_model("FixedReference", id="fs-link-1"),
            policy=sdk_model("FixedReference", id="policy4-id"),
        )
        _answer(
            pure1_client,
            file_systems=[_fs_link()],
            policies=[no_link, _policy(), empty_link, no_name],
        )

        module = run_main(pure1_replica_links)

        assert _result(module)["file_systems"] == [FS_ROW]
        module.warn.assert_called_once_with(
            "Policies of file-system replica links that Pure1 returned without "
            "a link or without a policy name are left out of the result: 3."
        )

    def test_no_selected_file_system_link_asks_for_no_policy(self, pure1_client):
        _answer_everything(pure1_client)

        result = _result(run_main(pure1_replica_links, name=["bucket1"]))

        assert result["file_systems"] == []
        pure1_client.get_file_system_replica_links.assert_called_once_with(filter=None)
        pure1_client.get_policies_file_system_replica_links.assert_not_called()


class TestReplicationTargets:
    """The replication_targets option and what a target is reported as."""

    def test_targets_are_not_read_when_the_option_is_false(self, pure1_client):
        _answer_everything(pure1_client)

        module = run_main(pure1_replica_links, replication_targets=False)

        pure1_client.get_targets.assert_not_called()
        expected = dict(EVERYTHING, replication_targets={})
        expected["summary"] = dict(
            EVERYTHING["summary"], replication_targets=NO_TARGETS
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )

    def test_target_with_only_a_name_and_an_array(self, pure1_client):
        target = sdk_model("Target", name="target1", arrays=[array_reference("array3")])
        _answer(pure1_client, targets=[target])

        module = run_main(pure1_replica_links, link_type=[])

        expected = dict(
            NOTHING,
            replication_targets={
                "array3": [
                    {
                        "name": "target1",
                        "id": None,
                        "address": None,
                        "status": None,
                        "status_details": None,
                        "as_of": None,
                        "as_of_epoch": None,
                    }
                ]
            },
        )
        # a target without a status is not known to be connected
        expected["summary"] = dict(
            NOTHING["summary"],
            replication_targets={"total": 1, "not_connected": ["target1"]},
        )
        module.exit_json.assert_called_once_with(
            changed=False, pure1_replica_links=expected
        )
        module.warn.assert_not_called()

    def test_targets_by_array_in_the_order_of_their_names(self, pure1_client):
        connected = dict(status="connected", status_details=None)
        _answer(
            pure1_client,
            targets=[
                _target("target3", arrays=("array3",)),
                _target("target1", arrays=("array4", "array3"), **connected),
                _target("target2", arrays=("array3",), **connected),
                _target("target1", arrays=("array5",), id="target1-other-id"),
            ],
        )

        result = _result(run_main(pure1_replica_links))

        target1 = dict(TARGET_ROW, status="connected", status_details=None)
        assert result["replication_targets"] == {
            "array3": [
                target1,
                dict(target1, name="target2", id="target2-id"),
                dict(TARGET_ROW, name="target3", id="target3-id"),
            ],
            # a target on two arrays is under both
            "array4": [target1],
            "array5": [dict(TARGET_ROW, id="target1-other-id")],
        }
        # and counted once
        assert result["summary"]["replication_targets"] == {
            "total": 4,
            "not_connected": ["target1", "target3"],
        }

    def test_target_on_two_arrays_that_is_not_connected_is_named_once(
        self, pure1_client
    ):
        _answer(pure1_client, targets=[_target(arrays=("array3", "array4"))])

        result = _result(run_main(pure1_replica_links))

        assert result["replication_targets"] == {
            "array3": [TARGET_ROW],
            "array4": [TARGET_ROW],
        }
        assert result["summary"]["replication_targets"] == {
            "total": 1,
            "not_connected": ["target1"],
        }

    def test_targets_of_one_name_on_one_array_are_ordered_by_id(self, pure1_client):
        _answer(
            pure1_client,
            targets=[
                _target(id="target1-b"),
                _target(id="target1-a"),
                sdk_model("Target", name="target1", arrays=[_array("array3")]),
            ],
        )

        result = _result(run_main(pure1_replica_links))

        # a target without an id comes first
        assert [row["id"] for row in result["replication_targets"]["array3"]] == [
            None,
            "target1-a",
            "target1-b",
        ]

    def test_array_that_pure1_names_twice_has_the_target_once(self, pure1_client):
        _answer(
            pure1_client,
            targets=[_target(arrays=("array3", "array4", "array3"))],
        )

        result = _result(run_main(pure1_replica_links))

        assert result["replication_targets"] == {
            "array3": [TARGET_ROW],
            "array4": [TARGET_ROW],
        }
        assert result["summary"]["replication_targets"] == {
            "total": 1,
            "not_connected": ["target1"],
        }

    def test_target_that_cannot_be_filed_is_left_out(self, pure1_client):
        no_name = sdk_model("Target", id="target0-id", arrays=[_array("array3")])
        no_arrays = sdk_model("Target", name="target2", status="connecting")
        empty_arrays = _target("target3")
        del empty_arrays.arrays[:]
        null_array = _target("target4")
        null_array.arrays[0] = None
        unnamed_array = sdk_model(
            "Target",
            name="target5",
            arrays=[sdk_model("FixedReferenceFqdn", fqdn="a.example.com")],
        )
        _answer(
            pure1_client,
            targets=[
                no_name,
                no_arrays,
                _target(),
                empty_arrays,
                null_array,
                unnamed_array,
            ],
        )

        module = run_main(pure1_replica_links)
        result = _result(module)

        assert result["replication_targets"] == {"array3": [TARGET_ROW]}
        assert result["summary"]["replication_targets"] == {
            "total": 1,
            "not_connected": ["target1"],
        }
        module.warn.assert_called_once_with(
            "Replication targets that Pure1 returned without a name or without "
            "an array are left out of the result: 5."
        )


class TestFailures:
    """A request that fails ends the task with one line."""

    @pytest.mark.parametrize(
        "method",
        LISTINGS + ("get_policies_file_system_replica_links",),
    )
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
                    400, "Unknown filter field", "Did not find property location"
                ),
                "Pure1 request {0} failed (HTTP 400): Unknown filter field: "
                "Did not find property location",
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
                "Pure1 request {0} failed: TimeoutError: The read operation "
                "timed out",
                id="transport error",
            ),
        ],
    )
    def test_failed_request_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, expected
    ):
        _answer_everything(pure1_client)
        failing = getattr(pure1_client, method)
        failing.return_value = None
        setattr(failing, attribute, answer)

        module = run_main(pure1_replica_links)

        module.fail_json.assert_called_once_with(msg=expected.format(method))
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_failed_array_request_fails_the_task(self, pure1_client):
        _answer_everything(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(500, "Internal server error"),
        ]

        module = run_main(pure1_replica_links, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 500): Internal server error"
        )
        module.exit_json.assert_not_called()
        for listing in LISTINGS:
            getattr(pure1_client, listing).assert_not_called()


MODULE = "everpure.pure1.pure1_replica_links"


def _template(text, variables):
    """Return what ansible-core makes of a template of a task"""
    return Templar(loader=None, variables=variables).template(trust_as_template(text))


def _met(condition, variables):
    """Tell whether a condition of a when or of an assert is met"""
    return bool(_template("{{ " + condition + " }}", variables))


def _play_examples(pure1_client, **changed):
    """Run the tasks of EXAMPLES in their order and return what the others make

    A task of the module runs through main() with the options of the
    example and registers what main() returns. Pure1 has links and targets
    for it, which changed replaces by the listings it gives under the name
    that the task registers. Every other task is returned in the order of
    the examples, as a list with one entry for each item of its loop and
    one without a loop. An entry has the when of the task, and unless that
    is false its msg and the conditions of its that up to the first one
    that is not met, where ansible.builtin.assert stops as well.
    """
    never = _fs_link(
        "fs-link-2", ("fs2", "array3"), ("fs2", "array4"), recovery_point=0, lag=AS_OF
    )
    served = dict(
        replication=dict(
            pods=[_pod_link()],
            file_systems=[_fs_link()],
            buckets=[_bucket_link()],
            targets=[_target()],
            policies=[_policy()],
        ),
        outbound=dict(pods=[_pod_link()]),
        fs_links=dict(file_systems=[_fs_link(), never], policies=[_policy()]),
    )
    registered = {}
    played = []
    for task in yaml.safe_load(pure1_replica_links.EXAMPLES):
        if MODULE in task:
            options = dict(task[MODULE])
            options.pop("app_id", None)
            options.pop("key_file", None)
            name = task["register"]
            _answer(pure1_client, **dict(served[name], **changed.get(name, {})))
            _arrays_known(pure1_client, *options.get("array", []))
            module = run_main(pure1_replica_links, **options)
            module.fail_json.assert_not_called()
            registered[name] = dict(module.exit_json.call_args[1])
            continue
        (action,) = set(task) - {"name", "when", "loop"}
        items = _template(task["loop"], registered) if "loop" in task else [None]
        entries = []
        for item in items:
            scope = dict(registered, item=item)
            entry = {}
            if "when" in task:
                entry["when"] = _met(task["when"], scope)
            if entry.get("when", True):
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
        summary, not_connected, check, never = _play_examples(pure1_client)

        assert summary == [{"msg": EVERYTHING["summary"]}]
        assert not_connected == [{"when": True, "msg": ["target1"]}]
        assert check == [{"that": [True, True, True, True, True]}]
        # one item, the link of fs2 that never replicated
        assert never == [
            {
                "msg": [
                    {
                        "name": "fs2",
                        "id": "fs2-on-array4",
                        "arrays": [{"name": "array4", "fqdn": "array4.example.com"}],
                    }
                ]
            }
        ]
        # only the first task reads the targets, and the second names array1
        pure1_client.get_targets.assert_called_once_with(filter=None)
        assert pure1_client.get_pod_replica_links.call_args_list == [
            call(filter=None),
            call(filter="members.locations.name='array1'"),
        ]
        assert pure1_client.get_file_system_replica_links.call_count == 2
        assert pure1_client.get_bucket_replica_links.call_count == 1

    def test_connected_targets_are_not_named(self, pure1_client):
        connected = [_target(status="connected", status_details=None)]

        played = _play_examples(pure1_client, replication=dict(targets=connected))

        assert played[0][0]["msg"]["replication_targets"] == {
            "total": 1,
            "not_connected": [],
        }
        assert played[1] == [{"when": False}]

    @pytest.mark.parametrize(
        "pods, expected",
        [
            pytest.param([], [False], id="no link of array1"),
            pytest.param(
                [_pod_link("link-a", ("pod1", "array2"), ("pod1-dr", "array1"))],
                [False],
                id="array1 is the target",
            ),
            pytest.param(
                [_pod_link(), _pod_link("link-b", paused=True)],
                [True, False],
                id="paused",
            ),
            pytest.param(
                [_pod_link(status="unhealthy")], [True, True, False], id="unhealthy"
            ),
            pytest.param(
                [_pod_link(recovery_point=0)],
                [True, True, True, False],
                id="never replicated",
            ),
            pytest.param(
                [_pod_link(as_of=AS_OF - 3545000)],
                [True, True, True, True, False],
                id="not refreshed for an hour",
            ),
            pytest.param(
                [_pod_link(as_of=AS_OF - 3544000)],
                [True, True, True, True, True],
                id="refreshed within the hour",
            ),
        ],
    )
    def test_check_before_a_recovery_test_fails_for_a_link_in_trouble(
        self, pure1_client, pods, expected
    ):
        played = _play_examples(pure1_client, outbound=dict(pods=pods))

        assert played[2] == [{"that": expected}]

    def test_no_link_that_never_replicated_shows_nothing(self, pure1_client):
        played = _play_examples(pure1_client, fs_links=dict(file_systems=[_fs_link()]))

        assert played[3] == []
