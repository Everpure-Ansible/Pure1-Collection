# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_fleets module.

The typical answer has the shape of a reading of a real organisation, under
invented names: a fleet with four member rows, of which one array names no
fleet on its own record, and a fleet that only the record of one array names.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from unittest.mock import call

import pypureclient
import pytest
import yaml
from ansible.template import Templar
from pypureclient import ErrorResponse, PureError, ValidResponse
from pypureclient.responses import ItemIterator

try:
    from ansible.template import trust_as_template
except ImportError:
    # ansible-core before 2.19 templates every string
    def trust_as_template(value):
        return value


from plugins.modules import pure1_fleets
from plugins.modules.pure1_fleets import generate_fleets_dict
from tests.unit.conftest import (
    ansible_module,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

ARRAY_AS_OF = 1612357445000
# The member rows of the fleets and of the topology groups share one stamp
ROWS_AS_OF = 1612353784000
ROWS_TIME = "2021-02-03 12:03:04 UTC"
FLEET1_AS_OF = 1612447506000
FLEET2_AS_OF = 1612537567000
PRODUCTS = {
    "fa": ("Purity//FA", "FA-X70R4", "6.5.0"),
    "fb": ("Purity//FB", "FlashBlade//S200", "4.5.0"),
}
LISTINGS = [
    "get_arrays",
    "get_fleets",
    "get_fleets_members",
    "get_topology_groups",
    "get_topology_groups_members",
]
MISMATCHES = (
    "Differences between the member rows of Pure1 and its array records about "
    "the fleet or the topology group of an array are listed under mismatches "
    "in the result: {0}."
)


def _ref(name=None, kind=None, **fields):
    """Return a reference; without a name the empty object Pure1 sends for none."""
    if name is not None:
        fields = dict(dict(name=name, id="id-" + name, resource_type=kind), **fields)
    return sdk_model("FixedReference", **fields)


def _array(name, product="fa", fleet=None, group=None, **overrides):
    system, model, version = PRODUCTS[product]
    fields = dict(
        id="id-" + name,
        name=name,
        fqdn=name + ".example.com",
        os=system,
        model=model,
        version=version,
        as_of=ARRAY_AS_OF,
        fleet=_ref(fleet, "fleets"),
        topology_group=_ref(group, "topology-groups"),
    )
    fields.update(overrides)
    return sdk_model("Array", **fields)


def _fleet(name, as_of=FLEET1_AS_OF, **overrides):
    fields = dict(id="id-" + name, name=name, as_of=as_of)
    fields.update(overrides)
    return sdk_model("Fleet", **fields)


def _member(fleet_name, array_name, coordinator=False, **overrides):
    """Return the member row of an array in a fleet."""
    fields = dict(
        fleet=_ref(fleet_name, "fleets"),
        member=_ref(array_name, "arrays"),
        status="joined",
        status_details="",
        coordinator_of=[_ref(fleet_name, "fleets")] if coordinator else [],
        as_of=ROWS_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("FleetMember", **fields)


def _group(group_name, fleet="fleet1"):
    return sdk_model(
        "TopologyGroup",
        id="id-" + group_name,
        name=group_name,
        fleet=_ref(fleet, "fleets"),
        parent_topology_group=_ref(),
        as_of=ROWS_AS_OF,
    )


def _group_member(group, array_name, fleet="fleet1"):
    """Return the member row of an array in a topology group."""
    return sdk_model(
        "TopologyGroupMember",
        fleet=_ref(fleet, "fleets"),
        topology_group=_ref(group, "topology-groups"),
        member=_ref(array_name, "arrays"),
        status="joined",
        status_details="",
        as_of=ROWS_AS_OF,
    )


def _serve(
    client,
    arrays=(),
    fleets=(),
    members=(),
    groups=(),
    group_members=(),
    resolved=None,
):
    """Answer every request of one run of main() with one page.

    get_arrays is asked by get_pure1, then for the arrays that the array
    option names (resolved), then for every array.
    """
    answers = [valid_response()]
    if resolved is not None:
        answers.append(valid_response(resolved))
    answers.append(valid_response(arrays))
    client.get_arrays.side_effect = answers
    client.get_fleets.return_value = valid_response(fleets)
    client.get_fleets_members.return_value = valid_response(members)
    client.get_topology_groups.return_value = valid_response(groups)
    client.get_topology_groups_members.return_value = valid_response(group_members)


def _lab_arrays():
    return [
        _array("array1", fleet="fleet1"),
        _array("array2", fleet="fleet1"),
        # A joined member whose own record names no fleet, but a group of the fleet
        _array("array3", "fb", group="site1"),
        _array("array4", "fb", fleet="fleet1", group="site1"),
        # The only trace of fleet2 beside its own entry
        _array("array5", fleet="fleet2"),
        _array("array6", fqdn=None),
    ]


def _lab_fleets():
    return [_fleet("fleet2", FLEET2_AS_OF), _fleet("fleet1")]


def _lab_members():
    # The member id of a FlashArray is not its Pure1 id, that of a FlashBlade is
    return [
        _member("fleet1", "array4"),
        _member("fleet1", "array2", True, member=_ref("array2", "arrays", id="m-2")),
        _member("fleet1", "array1", member=_ref("array1", "arrays", id="m-1")),
        _member("fleet1", "array3"),
    ]


def _serve_lab(client, resolved=None, **overrides):
    listings = dict(
        arrays=_lab_arrays(),
        fleets=_lab_fleets(),
        members=_lab_members(),
        groups=[_group("site1")],
        group_members=[
            _group_member("site1", "array3"),
            _group_member("site1", "array4"),
        ],
    )
    listings.update(overrides)
    _serve(client, resolved=resolved, **listings)


def _result(module):
    module.fail_json.assert_not_called()
    module.exit_json.assert_called_once()
    assert list(module.exit_json.call_args[1]) == ["changed", "pure1_fleets"]
    assert module.exit_json.call_args[1]["changed"] is False
    return module.exit_json.call_args[1]["pure1_fleets"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _failure(module):
    module.exit_json.assert_not_called()
    module.fail_json.assert_called_once()
    assert list(module.fail_json.call_args[1]) == ["msg"]
    return module.fail_json.call_args[1]["msg"]


def _row_member(name, product="fa", **overrides):
    """Return what the module gives for a member that the builders made."""
    system, model, version = PRODUCTS[product]
    entry = dict(
        status="joined",
        status_details="",
        coordinator=False,
        source="both",
        as_of=ROWS_TIME,
        as_of_epoch=ROWS_AS_OF,
        member_id="id-" + name,
        array_id="id-" + name,
        fqdn=name + ".example.com",
        os=system,
        model=model,
        version=version,
        topology_group=None,
        reported_fleet="fleet1",
    )
    entry.update(overrides)
    return entry


ARRAY1 = {
    "status": "joined",
    "status_details": "",
    "coordinator": False,
    "source": "both",
    "as_of": "2021-02-03 12:03:04 UTC",
    "as_of_epoch": 1612353784000,
    "member_id": "m-1",
    "array_id": "id-array1",
    "fqdn": "array1.example.com",
    "os": "Purity//FA",
    "model": "FA-X70R4",
    "version": "6.5.0",
    "topology_group": None,
    "reported_fleet": "fleet1",
}
ARRAY2 = {
    "status": "joined",
    "status_details": "",
    "coordinator": True,
    "source": "both",
    "as_of": "2021-02-03 12:03:04 UTC",
    "as_of_epoch": 1612353784000,
    "member_id": "m-2",
    "array_id": "id-array2",
    "fqdn": "array2.example.com",
    "os": "Purity//FA",
    "model": "FA-X70R4",
    "version": "6.5.0",
    "topology_group": None,
    "reported_fleet": "fleet1",
}
ARRAY3 = {
    "status": "joined",
    "status_details": "",
    "coordinator": False,
    "source": "members",
    "as_of": "2021-02-03 12:03:04 UTC",
    "as_of_epoch": 1612353784000,
    "member_id": "id-array3",
    "array_id": "id-array3",
    "fqdn": "array3.example.com",
    "os": "Purity//FB",
    "model": "FlashBlade//S200",
    "version": "4.5.0",
    "topology_group": "site1",
    "reported_fleet": None,
}
ARRAY4 = {
    "status": "joined",
    "status_details": "",
    "coordinator": False,
    "source": "both",
    "as_of": "2021-02-03 12:03:04 UTC",
    "as_of_epoch": 1612353784000,
    "member_id": "id-array4",
    "array_id": "id-array4",
    "fqdn": "array4.example.com",
    "os": "Purity//FB",
    "model": "FlashBlade//S200",
    "version": "4.5.0",
    "topology_group": "site1",
    "reported_fleet": "fleet1",
}
ARRAY5 = {
    "status": None,
    "status_details": None,
    "coordinator": None,
    "source": "arrays",
    "as_of": None,
    "as_of_epoch": None,
    "member_id": None,
    "array_id": "id-array5",
    "fqdn": "array5.example.com",
    "os": "Purity//FA",
    "model": "FA-X70R4",
    "version": "6.5.0",
    "topology_group": None,
    "reported_fleet": "fleet2",
}
FLEET1 = {
    "id": "id-fleet1",
    "as_of": "2021-02-04 14:05:06 UTC",
    "as_of_epoch": 1612447506000,
    "member_count": 4,
    "coordinators": ["array2"],
    "members": {
        "array1": ARRAY1,
        "array2": ARRAY2,
        "array3": ARRAY3,
        "array4": ARRAY4,
    },
}
FLEET2 = {
    "id": "id-fleet2",
    "as_of": "2021-02-05 15:06:07 UTC",
    "as_of_epoch": 1612537567000,
    "member_count": 1,
    "coordinators": [],
    "members": {"array5": ARRAY5},
}
MISMATCH3 = {
    "array": "array3",
    "kind": "fleet",
    "rows": ["fleet1"],
    "array_record": None,
}
MISMATCH5 = {
    "array": "array5",
    "kind": "fleet",
    "rows": [],
    "array_record": "fleet2",
}
LAB_RESULT = {
    "fleets": {"fleet1": FLEET1, "fleet2": FLEET2},
    "mismatches": [MISMATCH3, MISMATCH5],
}


class TestPure1FleetsMain:
    """The whole result of main() for the typical answer."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_fleet_is_returned_with_its_members(self, pure1_client):
        _serve_lab(pure1_client)

        # name and array are left to the argument spec
        module = run_main(pure1_fleets)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_fleets={
                "fleets": {
                    "fleet1": {
                        "id": "id-fleet1",
                        "as_of": "2021-02-04 14:05:06 UTC",
                        "as_of_epoch": 1612447506000,
                        "member_count": 4,
                        "coordinators": ["array2"],
                        "members": {
                            "array1": ARRAY1,
                            "array2": ARRAY2,
                            "array3": ARRAY3,
                            "array4": ARRAY4,
                        },
                    },
                    "fleet2": {
                        "id": "id-fleet2",
                        "as_of": "2021-02-05 15:06:07 UTC",
                        "as_of_epoch": 1612537567000,
                        "member_count": 1,
                        "coordinators": [],
                        "members": {"array5": ARRAY5},
                    },
                },
                "mismatches": [
                    {
                        "array": "array3",
                        "kind": "fleet",
                        "rows": ["fleet1"],
                        "array_record": None,
                    },
                    {
                        "array": "array5",
                        "kind": "fleet",
                        "rows": [],
                        "array_record": "fleet2",
                    },
                ],
            },
        )
        module.fail_json.assert_not_called()
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_fleets_are_collected_in_check_mode(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, check_mode=True)

        module.exit_json.assert_called_once_with(changed=False, pure1_fleets=LAB_RESULT)
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_each_listing_is_requested_once_and_unfiltered(self, pure1_client):
        _serve_lab(pure1_client)

        run_main(pure1_fleets)

        # The first is the check get_pure1 makes of every new client
        assert pure1_client.get_arrays.call_args_list == [call(limit=0), call()]
        pure1_client.get_fleets.assert_called_once_with()
        pure1_client.get_fleets_members.assert_called_once_with()
        pure1_client.get_topology_groups.assert_called_once_with()
        pure1_client.get_topology_groups_members.assert_called_once_with()

    @pytest.mark.parametrize(
        "params",
        [{}, {"name": ["fleet1"]}, {"name": []}, {"array": []}],
        ids=["default", "name", "no name", "no array"],
    )
    def test_five_requests_whatever_is_selected(self, pure1_client, params):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, **params)

        _result(module)
        requests = [
            len(getattr(pure1_client, name).call_args_list) for name in LISTINGS
        ]
        # get_arrays: the check of get_pure1 and the listing
        assert requests == [2, 1, 1, 1, 1]

    @pytest.mark.parametrize(
        "params", [{"name": []}, {"array": []}], ids=["no name", "no array"]
    )
    def test_empty_list_selects_nothing_so_everything_is_returned(
        self, pure1_client, params
    ):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, **params)

        assert _result(module) == LAB_RESULT
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_fleets_and_members_are_in_the_order_of_their_names(self, pure1_client):
        _serve_lab(pure1_client)

        result = _result(run_main(pure1_fleets, name=["fleet2", "fleet1"]))

        assert list(result["fleets"]) == ["fleet1", "fleet2"]
        assert list(result["fleets"]["fleet1"]["members"]) == [
            "array1",
            "array2",
            "array3",
            "array4",
        ]
        assert [entry["array"] for entry in result["mismatches"]] == [
            "array3",
            "array5",
        ]

    def test_an_organisation_without_fleets(self, pure1_client):
        _serve(pure1_client, [_array("array1"), _array("array2", "fb")])

        module = run_main(pure1_fleets)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_fleets={"fleets": {}, "mismatches": []}
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_answers_without_items(self, pure1_client):
        _serve(pure1_client)

        module = run_main(pure1_fleets)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_fleets={"fleets": {}, "mismatches": []}
        )
        module.warn.assert_not_called()

    def test_name_and_array_exclude_each_other(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=["fleet1"], array=["array1"])

        assert _failure(module) == "parameters are mutually exclusive: name|array"
        pure1_client.get_fleets.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            {"name": [], "array": []},
            {"name": [], "array": ["array1"]},
            {"name": ["fleet1"], "array": []},
        ],
        ids=["both empty", "no name", "no array"],
    )
    def test_empty_list_beside_the_other_option_is_refused_as_well(
        self, pure1_client, params
    ):
        """Alone an empty list counts as not set; the exclusion sees it as given."""
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, **params)

        assert _failure(module) == "parameters are mutually exclusive: name|array"
        # The argument spec refuses the task before the client is made
        pure1_client.get_arrays.assert_not_called()

    def test_unknown_option_is_refused(self, pure1_client):
        module = run_main(pure1_fleets, fleet="fleet1")

        assert _failure(module).startswith(
            "Unsupported parameters for module: fleet. Supported parameters include: "
        )

    def test_names_given_as_one_string_are_split(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name="fleet2,fleet1")

        assert _result(module) == LAB_RESULT


class TestRequiredRelease:
    """A py-pure-client without the methods fails the task before any request."""

    @pytest.mark.parametrize(
        "missing, named, minimum",
        [
            pytest.param(
                ["get_fleets_members"], "get_fleets_members", "1.99.0", id="members"
            ),
            pytest.param(
                ["get_topology_groups"], "get_topology_groups", "1.99.0", id="groups"
            ),
            pytest.param(
                ["get_topology_groups_members"],
                "get_topology_groups_members",
                "1.99.0",
                id="group members",
            ),
            pytest.param(["get_fleets"], "get_fleets", "1.93.0", id="fleets"),
            pytest.param(
                [
                    "get_fleets",
                    "get_fleets_members",
                    "get_topology_groups",
                    "get_topology_groups_members",
                ],
                "get_fleets_members",
                "1.99.0",
                id="none of them: the newest release is named",
            ),
        ],
    )
    def test_client_without_a_method(
        self, pure1_client, monkeypatch, missing, named, minimum
    ):
        _serve_lab(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.92.0")
        for method in missing:
            # What a client of an older release has for the name: nothing to call
            setattr(pure1_client, method, None)

        module = run_main(pure1_fleets)

        assert _failure(module) == (
            "py-pure-client {0} or later is required: this module needs "
            "Client.{1}(), which the installed py-pure-client 1.92.0 does not "
            "have.".format(minimum, named)
        )
        # Only the check of get_pure1 was made
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_methods_are_looked_at_before_the_arrays_are_resolved(self, pure1_client):
        _serve_lab(pure1_client)
        pure1_client.get_topology_groups = None

        module = run_main(pure1_fleets, array=["array1"])

        assert "Client.get_topology_groups()" in _failure(module)
        pure1_client.get_arrays.assert_called_once_with(limit=0)


class TestNameOption:
    """name reports only the fleets it names."""

    def test_one_fleet(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=["fleet1"])

        assert _result(module) == {
            "fleets": {"fleet1": FLEET1},
            "mismatches": [MISMATCH3],
        }
        assert _warnings(module) == [MISMATCHES.format(1)]

    def test_fleet_that_only_an_array_record_places_an_array_in(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=["fleet2"])

        assert _result(module) == {
            "fleets": {"fleet2": FLEET2},
            "mismatches": [MISMATCH5],
        }
        assert _warnings(module) == [MISMATCHES.format(1)]

    def test_several_fleets(self, pure1_client):
        _serve_lab(pure1_client, fleets=_lab_fleets() + [_fleet("fleet3")])

        module = run_main(pure1_fleets, name=["fleet3", "fleet1"])

        assert _result(module) == {
            "fleets": {
                "fleet1": FLEET1,
                "fleet3": {
                    "id": "id-fleet3",
                    "as_of": "2021-02-04 14:05:06 UTC",
                    "as_of_epoch": 1612447506000,
                    "member_count": 0,
                    "coordinators": [],
                    "members": {},
                },
            },
            "mismatches": [MISMATCH3],
        }

    def test_fleet_without_mismatch_gives_no_warning(self, pure1_client):
        _serve_lab(pure1_client, fleets=_lab_fleets() + [_fleet("fleet3")])

        module = run_main(pure1_fleets, name=["fleet3"])

        assert _result(module)["mismatches"] == []
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "names",
        [["FLEET1"], ["Fleet1", "fleet1"], ["fleet1", "fleet1"], ["FLEET1", "fLEET1"]],
        ids=["other case", "two cases", "twice", "twice in other cases"],
    )
    def test_name_in_any_case_gives_the_fleet_once_as_pure1_spells_it(
        self, pure1_client, names
    ):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=names)

        assert _result(module) == {
            "fleets": {"fleet1": FLEET1},
            "mismatches": [MISMATCH3],
        }

    @pytest.mark.parametrize(
        "names, message",
        [
            pytest.param(["fleet9"], "Fleet fleet9 is not known to Pure1.", id="one"),
            pytest.param(
                # The order of the task is not the order of the names
                ["fleet1", "fleet9", "fleet2", "Fleet8", "fleet9"],
                "Fleets fleet9, Fleet8 are not known to Pure1.",
                id="two among known ones, as the task wrote them",
            ),
            pytest.param(
                ["array1"], "Fleet array1 is not known to Pure1.", id="name of an array"
            ),
            pytest.param(
                ["fleet"], "Fleet fleet is not known to Pure1.", id="part of a name"
            ),
        ],
    )
    def test_name_pure1_does_not_know_fails_the_task(
        self, pure1_client, names, message
    ):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=names)

        assert _failure(module) == message
        module.warn.assert_not_called()

    def test_no_fleet_at_all_makes_every_name_unknown(self, pure1_client):
        _serve(pure1_client, [_array("array1")])

        module = run_main(pure1_fleets, name=["fleet1"])

        assert _failure(module) == "Fleet fleet1 is not known to Pure1."

    @pytest.mark.parametrize("names", [[""], ["fleet1", "  "]], ids=["empty", "blank"])
    def test_empty_name_fails_the_task(self, pure1_client, names):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, name=names)

        assert _failure(module) == "The name of a fleet must not be empty."

    @pytest.mark.parametrize(
        "name, member, as_of",
        [("Fleet1", "array2", FLEET2_AS_OF), ("fleet1", "array1", FLEET1_AS_OF)],
        ids=["upper", "lower"],
    )
    def test_fleets_that_differ_in_case_only(self, pure1_client, name, member, as_of):
        fleets = [_fleet("fleet1"), _fleet("Fleet1", FLEET2_AS_OF)]
        members = [_member("fleet1", "array1"), _member("Fleet1", "array2")]
        arrays = [_array("array1", fleet="fleet1"), _array("array2", fleet="Fleet1")]
        _serve(pure1_client, arrays, fleets, members)

        module = run_main(pure1_fleets, name=[name])

        # The spelling of the task wins over the same name in another case
        fleets = _result(module)["fleets"]
        assert list(fleets) == [name]
        assert list(fleets[name]["members"]) == [member]
        assert fleets[name]["as_of_epoch"] == as_of

    def test_name_that_matches_two_fleets_in_another_case_fails(self, pure1_client):
        fleets = [_fleet("fleet1"), _fleet("Fleet1")]
        _serve(pure1_client, [], fleets)

        module = run_main(pure1_fleets, name=["FLEET1"])

        assert _failure(module) == (
            "Pure1 knows more than one fleet whose name is FLEET1 in another "
            "case: Fleet1, fleet1. Give the name as Pure1 spells it."
        )

    def test_fleet_that_pure1_does_not_list_can_be_named(self, pure1_client):
        """A fleet that only a member row names is in the default result too."""
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet7")],
            [],
            [_member("fleet7", "array1")],
        )

        module = run_main(pure1_fleets, name=["Fleet7"])

        assert _result(module) == {
            "fleets": {
                "fleet7": {
                    "id": "id-fleet7",
                    "as_of": None,
                    "as_of_epoch": None,
                    "member_count": 1,
                    "coordinators": [],
                    "members": {
                        "array1": _row_member("array1", reported_fleet="fleet7")
                    },
                }
            },
            "mismatches": [],
        }
        module.warn.assert_not_called()

    def test_mismatch_of_an_array_whose_record_names_the_fleet(self, pure1_client):
        """array7 has a row in fleet1 while its record names fleet2."""
        arrays = _lab_arrays() + [_array("array7", fleet="fleet2")]
        members = _lab_members() + [_member("fleet1", "array7")]
        _serve_lab(pure1_client, arrays=arrays, members=members)
        mismatch7 = {
            "array": "array7",
            "kind": "fleet",
            "rows": ["fleet1"],
            "array_record": "fleet2",
        }

        module = run_main(pure1_fleets, name=["fleet2"])

        # The row wins: array7 is no member of fleet2, and is listed for it
        assert _result(module) == {
            "fleets": {"fleet2": FLEET2},
            "mismatches": [MISMATCH5, mismatch7],
        }
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_mismatches_of_other_fleets_and_of_arrays_in_no_fleet_are_left_out(
        self, pure1_client
    ):
        # array6 is in no fleet, and its record names a group it has no row in
        arrays = _lab_arrays()[:5] + [_array("array6", group="site1")]
        _serve_lab(pure1_client, arrays=arrays)
        mismatch6 = {
            "array": "array6",
            "kind": "topology_group",
            "rows": [],
            "array_record": "site1",
        }

        every = _result(run_main(pure1_fleets))["mismatches"]
        _serve_lab(pure1_client, arrays=arrays)
        named = _result(run_main(pure1_fleets, name=["fleet2"]))["mismatches"]

        assert every == [MISMATCH3, MISMATCH5, mismatch6]
        assert named == [MISMATCH5]

    def test_mismatch_of_the_topology_group_of_a_member(self, pure1_client):
        # array1 has a row in site1 that its record does not name
        group_members = [
            _group_member("site1", "array1"),
            _group_member("site1", "array3"),
            _group_member("site1", "array4"),
        ]
        _serve_lab(pure1_client, group_members=group_members)

        module = run_main(pure1_fleets, name=["fleet1"])

        result = _result(module)
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": ["site1"],
                "array_record": None,
            },
            MISMATCH3,
        ]
        assert result["fleets"]["fleet1"]["members"]["array1"] == dict(
            ARRAY1, topology_group="site1"
        )
        assert _warnings(module) == [MISMATCHES.format(2)]

    @pytest.mark.parametrize(
        "array, group_members, rows, array_record",
        [
            pytest.param(
                _array("array1"),
                [_group_member("g1", "array1", "fleet7")],
                ["g1"],
                None,
                id="member row of the group",
            ),
            pytest.param(_array("array1", group="g1"), [], [], "g1", id="array record"),
        ],
    )
    def test_mismatch_of_an_array_that_only_a_topology_group_places_in_the_fleet(
        self, pure1_client, array, group_members, rows, array_record
    ):
        """array1 is a member of no fleet, but in a topology group of fleet7."""
        fleets = [_fleet("fleet7"), _fleet("fleet8")]
        groups = [_group("g1", "fleet7")]
        mismatch = {
            "array": "array1",
            "kind": "topology_group",
            "rows": rows,
            "array_record": array_record,
        }

        _serve(pure1_client, [array], fleets, [], groups, group_members)
        of_the_fleet = run_main(pure1_fleets, name=["fleet7"])
        _serve(pure1_client, [array], fleets, [], groups, group_members)
        of_another = run_main(pure1_fleets, name=["fleet8"])

        assert _result(of_the_fleet) == {
            "fleets": {
                "fleet7": {
                    "id": "id-fleet7",
                    "as_of": "2021-02-04 14:05:06 UTC",
                    "as_of_epoch": 1612447506000,
                    "member_count": 0,
                    "coordinators": [],
                    "members": {},
                }
            },
            "mismatches": [mismatch],
        }
        assert _warnings(of_the_fleet) == [MISMATCHES.format(1)]
        assert _result(of_another)["mismatches"] == []
        of_another.warn.assert_not_called()


class TestArrayOption:
    """array reports the fleets of the arrays it names, narrowed to them."""

    def test_one_array(self, pure1_client):
        _serve_lab(pure1_client, resolved=[_array("array1", fleet="fleet1")])

        module = run_main(pure1_fleets, array=["array1"])

        # member_count and coordinators still describe the whole fleet
        assert _result(module) == {
            "fleets": {
                "fleet1": {
                    "id": "id-fleet1",
                    "as_of": "2021-02-04 14:05:06 UTC",
                    "as_of_epoch": 1612447506000,
                    "member_count": 4,
                    "coordinators": ["array2"],
                    "members": {"array1": ARRAY1},
                }
            },
            "mismatches": [],
        }
        module.warn.assert_not_called()

    def test_the_arrays_are_resolved_first_with_one_more_request(self, pure1_client):
        _serve_lab(pure1_client, resolved=[_array("array1"), _array("array5")])

        run_main(pure1_fleets, array=["array5", "array1"])

        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('array5','array1')"),
            call(),
        ]
        pure1_client.get_fleets.assert_called_once_with()
        pure1_client.get_fleets_members.assert_called_once_with()
        pure1_client.get_topology_groups.assert_called_once_with()
        pure1_client.get_topology_groups_members.assert_called_once_with()

    def test_arrays_of_two_fleets(self, pure1_client):
        resolved = [_array("array5"), _array("array3"), _array("array2")]
        _serve_lab(pure1_client, resolved=resolved)

        module = run_main(pure1_fleets, array=["array5", "array3", "array2"])

        assert _result(module) == {
            "fleets": {
                "fleet1": dict(FLEET1, members={"array2": ARRAY2, "array3": ARRAY3}),
                "fleet2": FLEET2,
            },
            "mismatches": [MISMATCH3, MISMATCH5],
        }
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_name_in_another_case_gives_the_spelling_of_pure1(self, pure1_client):
        _serve_lab(pure1_client, resolved=[_array("array3", "fb")])

        module = run_main(pure1_fleets, array=["ARRAY3"])

        pure1_client.get_arrays.assert_any_call(filter="name='ARRAY3'")
        assert _result(module) == {
            "fleets": {"fleet1": dict(FLEET1, members={"array3": ARRAY3})},
            "mismatches": [MISMATCH3],
        }
        assert _warnings(module) == [MISMATCHES.format(1)]

    def test_array_in_no_fleet_gives_no_fleet(self, pure1_client):
        _serve_lab(pure1_client, resolved=[_array("array6")])

        module = run_main(pure1_fleets, array=["array6"])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_fleets={"fleets": {}, "mismatches": []}
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_array_in_no_fleet_beside_one_in_a_fleet(self, pure1_client):
        _serve_lab(pure1_client, resolved=[_array("array6"), _array("array2")])

        module = run_main(pure1_fleets, array=["array6", "array2"])

        assert _result(module) == {
            "fleets": {"fleet1": dict(FLEET1, members={"array2": ARRAY2})},
            "mismatches": [],
        }

    def test_array_with_rows_in_two_fleets_is_reported_in_both(self, pure1_client):
        members = _lab_members() + [_member("fleet2", "array1", True)]
        _serve_lab(pure1_client, members=members, resolved=[_array("array1")])

        module = run_main(pure1_fleets, array=["array1"])

        in_fleet2 = dict(
            ARRAY1, coordinator=True, source="members", member_id="id-array1"
        )
        assert _result(module) == {
            "fleets": {
                "fleet1": dict(FLEET1, members={"array1": ARRAY1}),
                "fleet2": dict(
                    FLEET2,
                    member_count=2,
                    coordinators=["array1"],
                    members={"array1": in_fleet2},
                ),
            },
            "mismatches": [
                {
                    "array": "array1",
                    "kind": "fleet",
                    "rows": ["fleet1", "fleet2"],
                    "array_record": "fleet1",
                }
            ],
        }
        assert _warnings(module) == [MISMATCHES.format(1)]

    def test_topology_group_mismatch_of_a_named_array_in_no_fleet(self, pure1_client):
        arrays = _lab_arrays()[:5] + [_array("array6", group="site1")]
        _serve_lab(pure1_client, arrays=arrays, resolved=[_array("array6")])

        module = run_main(pure1_fleets, array=["array6"])

        assert _result(module) == {
            "fleets": {},
            "mismatches": [
                {
                    "array": "array6",
                    "kind": "topology_group",
                    "rows": [],
                    "array_record": "site1",
                }
            ],
        }
        assert _warnings(module) == [MISMATCHES.format(1)]

    @pytest.mark.parametrize(
        "names, resolved, message",
        [
            pytest.param(
                ["array9"], [], "Array array9 is not known to Pure1.", id="unknown"
            ),
            pytest.param(
                ["array1", "array9", "array8"],
                ["array1"],
                "Arrays array9, array8 are not known to Pure1.",
                id="two unknown beside a known one",
            ),
            pytest.param(
                ["fleet1"], [], "Array fleet1 is not known to Pure1.", id="a fleet"
            ),
        ],
    )
    def test_array_pure1_does_not_know_fails_the_task(
        self, pure1_client, names, resolved, message
    ):
        _serve_lab(pure1_client, resolved=[_array(name) for name in resolved])

        module = run_main(pure1_fleets, array=names)

        assert _failure(module) == message
        # Nothing is listed for a task that fails on its names
        assert len(pure1_client.get_arrays.call_args_list) == 2
        pure1_client.get_fleets.assert_not_called()

    def test_empty_array_name_fails_before_any_request(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets, array=["array1", ""])

        assert _failure(module) == "The name of an array must not be empty."
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_failed_request_for_the_named_arrays(self, pure1_client):
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            error_response(400, "Illegal filter format", "Unexpected input"),
        ]

        module = run_main(pure1_fleets, array=["array1"])

        assert _failure(module) == (
            "Pure1 request get_arrays failed (HTTP 400): Illegal filter format: "
            "Unexpected input"
        )


class TestMembers:
    """What the typical answer does not show of a member and of a fleet."""

    def test_objects_with_only_their_names(self, pure1_client):
        member = sdk_model(
            "FleetMember",
            fleet=sdk_model("FixedReference", name="fleet1"),
            member=sdk_model("FixedReference", name="array1"),
        )
        _serve(
            pure1_client,
            [sdk_model("Array", name="array1")],
            [sdk_model("Fleet", name="fleet1")],
            [member],
        )

        module = run_main(pure1_fleets)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_fleets={
                "fleets": {
                    "fleet1": {
                        "id": None,
                        "as_of": None,
                        "as_of_epoch": None,
                        "member_count": 1,
                        "coordinators": [],
                        "members": {
                            "array1": {
                                "status": None,
                                "status_details": None,
                                "coordinator": False,
                                "source": "members",
                                "as_of": None,
                                "as_of_epoch": None,
                                "member_id": None,
                                "array_id": None,
                                "fqdn": None,
                                "os": None,
                                "model": None,
                                "version": None,
                                "topology_group": None,
                                "reported_fleet": None,
                            }
                        },
                    }
                },
                "mismatches": [
                    {
                        "array": "array1",
                        "kind": "fleet",
                        "rows": ["fleet1"],
                        "array_record": None,
                    }
                ],
            },
        )
        assert _warnings(module) == [MISMATCHES.format(1)]

    def test_array_with_only_its_name_and_its_fleet(self, pure1_client):
        array = sdk_model(
            "Array", name="array1", fleet=sdk_model("FixedReference", name="fleet1")
        )
        _serve(pure1_client, [array])

        module = run_main(pure1_fleets)

        assert _result(module) == {
            "fleets": {
                "fleet1": {
                    "id": None,
                    "as_of": None,
                    "as_of_epoch": None,
                    "member_count": 1,
                    "coordinators": [],
                    "members": {
                        "array1": {
                            "status": None,
                            "status_details": None,
                            "coordinator": None,
                            "source": "arrays",
                            "as_of": None,
                            "as_of_epoch": None,
                            "member_id": None,
                            "array_id": None,
                            "fqdn": None,
                            "os": None,
                            "model": None,
                            "version": None,
                            "topology_group": None,
                            "reported_fleet": "fleet1",
                        }
                    },
                }
            },
            "mismatches": [
                {
                    "array": "array1",
                    "kind": "fleet",
                    "rows": [],
                    "array_record": "fleet1",
                }
            ],
        }

    def test_member_whose_name_no_array_has(self, pure1_client):
        members = [_member("fleet1", "array1"), _member("fleet1", "array9", True)]
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1")],
            [_fleet("fleet1")],
            members,
        )

        module = run_main(pure1_fleets)

        # It counts as a member and can coordinate, but nothing is known of it
        assert _result(module) == {
            "fleets": {
                "fleet1": {
                    "id": "id-fleet1",
                    "as_of": "2021-02-04 14:05:06 UTC",
                    "as_of_epoch": 1612447506000,
                    "member_count": 2,
                    "coordinators": ["array9"],
                    "members": {
                        "array1": _row_member("array1"),
                        "array9": {
                            "status": "joined",
                            "status_details": "",
                            "coordinator": True,
                            "source": "members",
                            "as_of": "2021-02-03 12:03:04 UTC",
                            "as_of_epoch": 1612353784000,
                            "member_id": "id-array9",
                            "array_id": None,
                            "fqdn": None,
                            "os": None,
                            "model": None,
                            "version": None,
                            "topology_group": None,
                            "reported_fleet": None,
                        },
                    },
                }
            },
            "mismatches": [],
        }
        module.warn.assert_not_called()

    def test_member_that_is_not_joined(self, pure1_client):
        row = _member(
            "fleet1", "array1", status="joining", status_details="Waiting for array1"
        )
        _serve(
            pure1_client, [_array("array1", fleet="fleet1")], [_fleet("fleet1")], [row]
        )

        module = run_main(pure1_fleets)

        assert _result(module)["fleets"]["fleet1"]["members"] == {
            "array1": _row_member(
                "array1", status="joining", status_details="Waiting for array1"
            )
        }
        module.warn.assert_not_called()

    def test_member_is_joined_to_its_array_by_name_not_by_id(self, pure1_client):
        other = _ref("array2", "arrays", id="id-array1")
        _serve(
            pure1_client,
            [_array("array1", fqdn="one.example.com"), _array("array2", "fb")],
            [_fleet("fleet1")],
            [_member("fleet1", "array2", member=other)],
        )

        module = run_main(pure1_fleets)

        assert _result(module)["fleets"]["fleet1"]["members"] == {
            "array2": _row_member(
                "array2",
                "fb",
                source="members",
                member_id="id-array1",
                reported_fleet=None,
            )
        }

    def test_array_without_fqdn(self, pure1_client):
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1", fqdn=None)],
            [_fleet("fleet1")],
            [_member("fleet1", "array1")],
        )

        module = run_main(pure1_fleets)

        assert _result(module)["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1", fqdn=None)
        }

    def test_group_that_only_the_array_record_names(self, pure1_client):
        """Without a row in a group the group of the record is the group."""
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1", group="site1")],
            [_fleet("fleet1")],
            [_member("fleet1", "array1")],
            [_group("site1")],
        )

        module = run_main(pure1_fleets)

        result = _result(module)
        assert result["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1", topology_group="site1")
        }
        assert result["mismatches"] == [
            {
                "array": "array1",
                "kind": "topology_group",
                "rows": [],
                "array_record": "site1",
            }
        ]

    def test_fleet_without_members_is_reported(self, pure1_client):
        _serve(pure1_client, [_array("array1")], [_fleet("fleet1")])

        module = run_main(pure1_fleets)

        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_fleets={
                "fleets": {
                    "fleet1": {
                        "id": "id-fleet1",
                        "as_of": "2021-02-04 14:05:06 UTC",
                        "as_of_epoch": 1612447506000,
                        "member_count": 0,
                        "coordinators": [],
                        "members": {},
                    }
                },
                "mismatches": [],
            },
        )
        module.warn.assert_not_called()

    def test_two_coordinators_are_named_in_order(self, pure1_client):
        members = [_member("fleet1", "array2", True), _member("fleet1", "array1", True)]
        arrays = [_array("array2", fleet="fleet1"), _array("array1", fleet="fleet1")]
        _serve(pure1_client, arrays, [_fleet("fleet1")], members)

        module = run_main(pure1_fleets)

        assert _result(module)["fleets"]["fleet1"]["coordinators"] == [
            "array1",
            "array2",
        ]

    def test_function_returns_what_main_exits_with(self, pure1_client):
        module = ansible_module(name=None, array=None)
        _serve_lab(pure1_client)
        # No get_pure1 here: the first answer is the one to its check
        pure1_client.get_arrays(limit=0)

        result = generate_fleets_dict(module, pure1_client)

        assert result == LAB_RESULT
        module.exit_json.assert_not_called()


class TestWarnings:
    """Each warning of the module, and that those of the helper reach the task."""

    def test_arrays_that_cannot_be_filed_are_counted_once(self, pure1_client):
        arrays = [
            _array("array1", fleet="fleet1"),
            sdk_model("Array", id="id-nameless", fleet=_ref("fleet1", "fleets")),
            # The first of a name is the array: the second is left out
            _array("array1", "fb", fqdn="other.example.com"),
        ]
        _serve(pure1_client, arrays, [_fleet("fleet1")], [_member("fleet1", "array1")])

        module = run_main(pure1_fleets)

        assert _result(module) == {
            "fleets": {
                "fleet1": {
                    "id": "id-fleet1",
                    "as_of": "2021-02-04 14:05:06 UTC",
                    "as_of_epoch": 1612447506000,
                    "member_count": 1,
                    "coordinators": [],
                    "members": {"array1": _row_member("array1")},
                }
            },
            "mismatches": [],
        }
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 2."
        ]

    @pytest.mark.parametrize(
        "odd",
        [
            pytest.param(
                sdk_model("Array", id="id-nameless", fleet=_ref("fleet1", "fleets")),
                id="no name",
            ),
            pytest.param(
                _array("array1", "fb", fqdn="other.example.com"),
                id="name of another array",
            ),
        ],
    )
    def test_one_array_that_cannot_be_filed_is_counted(self, pure1_client, odd):
        arrays = [_array("array1", fleet="fleet1"), odd]
        _serve(pure1_client, arrays, [_fleet("fleet1")], [_member("fleet1", "array1")])

        module = run_main(pure1_fleets)

        assert _result(module)["fleets"]["fleet1"]["members"] == {
            "array1": _row_member("array1")
        }
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: 1."
        ]

    def test_fewer_arrays_than_pure1_counted(self, pure1_client):
        arrays = [_array("array1"), _array("array2"), _array("array3")]
        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = [
            valid_response(),
            valid_response(arrays, total_item_count=5),
            valid_response([], total_item_count=5),
        ]

        module = run_main(pure1_fleets)

        assert _result(module) == {"fleets": {}, "mismatches": []}
        assert _warnings(module) == [
            "Pure1 returned 3 of 5 arrays: the result may be incomplete."
        ]

    def test_arrays_of_a_listing_without_a_total_that_cannot_be_paged(
        self, pure1_client
    ):
        arrays = [_array("array1"), _array("array2")]
        answers = paged_answers("ArrayGetResponse", arrays, total_item_count=None)

        def ignore_offset(**kwargs):
            kwargs.pop("offset", None)
            return answers(**kwargs)

        _serve(pure1_client)
        pure1_client.get_arrays.side_effect = ignore_offset

        module = run_main(pure1_fleets)

        assert _result(module) == {"fleets": {}, "mismatches": []}
        assert _warnings(module) == [
            "Pure1 returned 2 of an unknown number of arrays: the result may be "
            "incomplete."
        ]

    def test_fleet_without_a_name_is_left_out_by_the_helper(self, pure1_client):
        fleets = [sdk_model("Fleet", id="id-nameless", as_of=FLEET1_AS_OF)]
        _serve(pure1_client, [_array("array1")], fleets)

        module = run_main(pure1_fleets)

        assert _result(module) == {"fleets": {}, "mismatches": []}
        assert _warnings(module) == [
            "Fleets that Pure1 returned without a name or with the name of "
            "another fleet are left out of the result: 1."
        ]

    def test_member_rows_that_cannot_be_filed_are_left_out_by_the_helper(
        self, pure1_client
    ):
        members = [
            _member("fleet1", "array1"),
            _member("fleet1", "array2", member=_ref()),
            _member("fleet1", "array3", fleet=_ref()),
            sdk_model("FleetMember", status="joined"),
        ]
        _serve(
            pure1_client,
            [_array("array1", fleet="fleet1")],
            [_fleet("fleet1")],
            members,
        )

        module = run_main(pure1_fleets)

        assert list(_result(module)["fleets"]["fleet1"]["members"]) == ["array1"]
        assert _warnings(module) == [
            "Fleet member rows that Pure1 returned without a fleet or a member "
            "name, for a member that is no array or a second time for one "
            "member are left out of the result: 3."
        ]

    def test_fewer_member_rows_than_pure1_counted(self, pure1_client):
        _serve_lab(pure1_client)
        pure1_client.get_fleets_members.side_effect = [
            valid_response(_lab_members(), total_item_count=6),
            valid_response([], total_item_count=6),
        ]

        module = run_main(pure1_fleets)

        assert _result(module) == LAB_RESULT
        assert _warnings(module) == [
            "Pure1 returned 4 of 6 fleet member rows: the result may be incomplete.",
            MISMATCHES.format(2),
        ]

    def test_mismatches_are_counted_in_one_warning_without_a_name(self, pure1_client):
        _serve_lab(pure1_client)

        module = run_main(pure1_fleets)

        module.warn.assert_called_once_with(
            "Differences between the member rows of Pure1 and its array records "
            "about the fleet or the topology group of an array are listed under "
            "mismatches in the result: 2."
        )

    def test_no_mismatch_gives_no_warning(self, pure1_client):
        arrays = [_array("array1", fleet="fleet1"), _array("array2")]
        _serve(pure1_client, arrays, [_fleet("fleet1")], [_member("fleet1", "array1")])

        module = run_main(pure1_fleets)

        assert _result(module)["mismatches"] == []
        module.warn.assert_not_called()


class TestListingsInPages:
    """Every listing is read to its end, however many pages Pure1 makes of it."""

    def test_arrays_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("ArrayGetResponse", _lab_arrays(), page_size=2)
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_fleets)

        assert _result(module) == LAB_RESULT
        assert answers.requests == [
            {"limit": 0},
            {},
            {"continuation_token": "page-2"},
            {"continuation_token": "page-4"},
        ]

    def test_arrays_without_a_total_are_asked_for_once_more(self, pure1_client):
        """The paging keywords of the shared helper reach the client."""
        _serve_lab(pure1_client)
        answers = paged_answers(
            "ArrayGetResponse", _lab_arrays(), total_item_count=None
        )
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_fleets)

        assert _result(module) == LAB_RESULT
        # Only an empty page after the last array tells that there are no more
        assert answers.requests == [{"limit": 0}, {}, {"offset": 6}]
        assert _warnings(module) == [MISMATCHES.format(2)]

    def test_member_rows_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("FleetMemberGetResponse", _lab_members(), page_size=3)
        pure1_client.get_fleets_members.side_effect = answers

        module = run_main(pure1_fleets)

        assert _result(module) == LAB_RESULT
        assert answers.requests == [{}, {"continuation_token": "page-3"}]

    def test_fleets_in_pages(self, pure1_client):
        _serve_lab(pure1_client)
        answers = paged_answers("FleetGetResponse", _lab_fleets(), page_size=1)
        pure1_client.get_fleets.side_effect = answers

        module = run_main(pure1_fleets)

        assert _result(module) == LAB_RESULT
        assert answers.requests == [{}, {"continuation_token": "page-1"}]


class TestFailedRequests:
    """A listing that fails, fails the task with the line of the shared helpers."""

    @pytest.mark.parametrize("method", LISTINGS)
    @pytest.mark.parametrize(
        "answer, reason",
        [
            pytest.param(
                error_response(403, "Access denied"),
                " (HTTP 403): Access denied",
                id="error answer",
            ),
            pytest.param(
                error_response(400, "Invalid value", "no match"),
                " (HTTP 400): Invalid value: no match",
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
        ],
    )
    def test_failed_listing_fails_the_task_with_one_line(
        self, pure1_client, method, answer, reason
    ):
        _serve_lab(pure1_client)
        answers = [answer]
        if method == "get_arrays":
            # The check of get_pure1 comes first and works
            answers.insert(0, valid_response())
        getattr(pure1_client, method).side_effect = answers

        module = run_main(pure1_fleets)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request {0} failed{1}".format(method, reason)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()
        # No listing after the one that failed is asked for
        for later in LISTINGS[LISTINGS.index(method) + 1 :]:
            getattr(pure1_client, later).assert_not_called()

    def test_error_while_a_later_page_is_read(self, pure1_client):
        """The SDK raises when it cannot fetch the next page."""

        def next_page(**kwargs):
            raise PureError("connection reset")

        _serve_lab(pure1_client)
        page = ItemIterator(
            next_page,
            {},
            "next-page",
            4,
            _lab_members()[:2],
            None,
            more_items_remaining=True,
        )
        pure1_client.get_fleets_members.return_value = ValidResponse(
            200, "next-page", 4, page, {}
        )

        module = run_main(pure1_fleets)

        assert _failure(module) == (
            "Pure1 request get_fleets_members failed: PureError: Failed to "
            "collect more items: PureError: connection reset"
        )
        pure1_client.get_topology_groups.assert_not_called()


class TestDocumentation:
    """What DOCUMENTATION says of the code is what the code does."""

    @staticmethod
    def _documentation():
        return yaml.safe_load(pure1_fleets.DOCUMENTATION)

    def test_every_required_method_is_in_the_note_that_names_no_release(self):
        """The collection requires the py-pure-client that has them all: a
        note with a release would read as a requirement of its own."""
        notes = self._documentation()["notes"]

        assert notes[0] == (
            "This module makes the Pure1 REST 1.6 call C(get_fleets) and the "
            "Pure1 REST 1.7 calls C(get_fleets_members), C(get_topology_groups) "
            "and C(get_topology_groups_members)."
        )
        for method, _minimum in pure1_fleets.REQUIRED_METHODS:
            named = [note for note in notes if "C({0})".format(method) in note]
            assert named == [notes[0]], method
        assert "py-pure-client" not in " ".join(notes)

    def test_listing_longer_than_a_page_costs_another_request_as_a_note_says(
        self, pure1_client
    ):
        arrays = [_array("array{0}".format(number)) for number in range(1001)]
        _serve(pure1_client)
        # Pure1 gives 1,000 objects for one request
        answers = paged_answers("ArrayGetResponse", arrays)
        pure1_client.get_arrays.side_effect = answers

        module = run_main(pure1_fleets)

        assert _result(module) == {"fleets": {}, "mismatches": []}
        assert answers.requests == [
            {"limit": 0},
            {},
            {"continuation_token": "page-1000"},
        ]
        notes = " ".join(self._documentation()["notes"])
        assert "each with one request for every 1,000 objects" in notes

    @pytest.mark.parametrize("option", ["name", "array"])
    def test_option_says_what_an_empty_list_does(self, option):
        text = " ".join(self._documentation()["options"][option]["description"])

        assert "An empty list counts as not set, and every fleet is reported" in text

    @pytest.mark.parametrize("option", ["name", "array"])
    def test_option_says_that_an_empty_list_still_excludes_the_other(self, option):
        text = " ".join(self._documentation()["options"][option]["description"])

        assert (
            "A task that gives both options fails, also when one of them is an "
            "empty list."
        ) in text

    def test_organization_is_spelled_as_in_the_other_modules(self):
        documentation = (
            pure1_fleets.DOCUMENTATION + pure1_fleets.EXAMPLES + pure1_fleets.RETURN
        )

        assert "organisation" not in documentation.lower()
        assert "organization" in self._documentation()["description"][0]


def _documented():
    return yaml.safe_load(pure1_fleets.RETURN)["pure1_fleets"]


def _assert_keys(result, documented):
    """Compare a result with what RETURN describes, key by key, both ways."""
    assert set(result) == set(documented)
    fleet_keys = documented["fleets"]["contains"]
    for fleet in result["fleets"].values():
        assert set(fleet) == set(fleet_keys)
        for member in fleet["members"].values():
            assert set(member) == set(fleet_keys["members"]["contains"])
    for entry in result["mismatches"]:
        assert set(entry) == set(documented["mismatches"]["contains"])


class TestReturn:
    """RETURN describes what the module returns, in both directions."""

    @pytest.mark.parametrize(
        "params, resolved",
        [
            pytest.param({}, None, id="default"),
            pytest.param({"name": ["fleet1", "fleet2"]}, None, id="name"),
            pytest.param(
                {"array": ["array3", "array5"]},
                [_array("array3", "fb"), _array("array5")],
                id="array",
            ),
        ],
    )
    def test_return_has_the_keys_of_a_result(self, pure1_client, params, resolved):
        _serve_lab(pure1_client, resolved=resolved)

        result = _result(run_main(pure1_fleets, **params))

        _assert_keys(result, _documented()["contains"])
        # The comparison saw a member with a row, one without, and a mismatch
        sources = {
            member["source"]
            for fleet in result["fleets"].values()
            for member in fleet["members"].values()
        }
        assert {"members", "arrays"} <= sources
        assert result["mismatches"]

    def test_sample_has_the_keys_of_a_result(self):
        sample = _documented()["sample"]

        _assert_keys(sample, _documented()["contains"])
        assert sample["fleets"]["fleet1"]["members"]
        assert sample["mismatches"]

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_sample_is_what_the_module_returns_for_such_an_organisation(
        self, pure1_client
    ):
        sample = _documented()["sample"]
        fleet = sample["fleets"]["fleet1"]
        members = fleet["members"]
        _serve(
            pure1_client,
            arrays=[
                _array("array1", fleet="fleet1", id=members["array1"]["array_id"]),
                # In the fleet by its member row alone, and in a group of it
                _array("array2", "fb", group="site1", id=members["array2"]["array_id"]),
            ],
            fleets=[_fleet("fleet1", fleet["as_of_epoch"], id=fleet["id"])],
            members=[
                _member(
                    "fleet1",
                    name,
                    name == "array1",
                    member=_ref(name, "arrays", id=members[name]["member_id"]),
                    as_of=members[name]["as_of_epoch"],
                )
                for name in ("array1", "array2")
            ],
            groups=[_group("site1")],
            group_members=[_group_member("site1", "array2")],
        )

        module = run_main(pure1_fleets)

        assert _result(module) == sample
        assert _warnings(module) == [MISMATCHES.format(1)]


MODULE = "everpure.pure1.pure1_fleets"
NOT_JOINED = "The fleet has no member, or a member is not joined or has no member row"


def _template(text, variables):
    """Return what ansible-core makes of a template of a task."""
    templar = Templar(loader=None, variables=variables)
    return templar.template(trust_as_template(text))


def _play_examples(client, **served):
    """Run the tasks of EXAMPLES in their order and return what they make

    A task of the module runs through main() with the options of the example
    and registers its result. Pure1 has the typical organisation for it, with
    the listings replaced that served gives under the name the task
    registers. Every other task is returned under the name that the task of
    the module before it registered, as a list with one entry for each item
    of its loop, one without a loop: its when, and unless that is false the
    templates of its arguments, as ansible-core evaluates them.
    """
    registered = {}
    made = {}
    reads = None
    for task in yaml.safe_load(pure1_fleets.EXAMPLES):
        options = task.get(MODULE)
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            reads = task["register"]
            resolved = [_array(name) for name in options.get("array", [])]
            _serve_lab(client, resolved=resolved or None, **served.get(reads, {}))
            module = run_main(pure1_fleets, **options)
            registered[reads] = dict(module.exit_json.call_args[1])
            made[reads] = []
            continue
        (action,) = set(task) - {"name", "when", "loop", "vars"}
        variables = dict(registered)
        for name, value in task.get("vars", {}).items():
            variables[name] = trust_as_template(value)
        items = _template(task["loop"], variables) if "loop" in task else [None]
        entries = []
        for item in items:
            scope = dict(variables, item=item)
            entry = {}
            if "when" in task:
                entry["when"] = _template("{{ " + task["when"] + " }}", scope)
            if entry.get("when", True):
                for key, value in task[action].items():
                    if key == "that":
                        entry[key] = [
                            _template("{{ " + each + " }}", scope) for each in value
                        ]
                    elif isinstance(value, str):
                        entry[key] = _template(value, scope)
            entries.append(entry)
        made[reads].append(entries)
    return made


class TestExamples:
    """The tasks of EXAMPLES, on what the module returns."""

    def test_examples_read_what_the_module_returns(self, pure1_client):
        made = _play_examples(pure1_client)

        assert list(made) == ["fleets", "fleet", "placed"]
        assert made["fleets"] == [
            [
                {"msg": "fleet1 is coordinated by array2"},
                {"msg": "fleet2 is coordinated by "},
            ],
            [{"when": True, "msg": [MISMATCH3, MISMATCH5]}],
        ]
        # The fleet has members, and each of them has a row that says joined
        assert made["fleet"] == [[{"that": [True, True], "fail_msg": NOT_JOINED}]]
        assert made["placed"] == [
            [{"msg": "array1, array2 in fleet1, which has 4 members"}]
        ]

    def test_two_coordinators_are_shown_in_one_line(self, pure1_client):
        rows = _lab_members()
        rows[0] = _member("fleet1", "array4", True)

        made = _play_examples(pure1_client, fleets=dict(members=rows))

        assert made["fleets"][0][0] == {
            "msg": "fleet1 is coordinated by array2, array4"
        }

    def test_mismatches_are_not_shown_when_there_are_none(self, pure1_client):
        listings = dict(
            arrays=[_array("array1", fleet="fleet1"), _array("array2")],
            members=[_member("fleet1", "array1", True)],
            groups=[],
            group_members=[],
        )

        made = _play_examples(pure1_client, fleets=listings)

        assert made["fleets"] == [
            [
                {"msg": "fleet1 is coordinated by array1"},
                {"msg": "fleet2 is coordinated by "},
            ],
            [{"when": False}],
        ]

    def test_check_of_the_fleet_fails_for_a_member_that_has_not_joined(
        self, pure1_client
    ):
        rows = _lab_members()
        rows[3] = _member("fleet1", "array3", status="joining")

        made = _play_examples(pure1_client, fleet=dict(members=rows))

        assert made["fleet"] == [[{"that": [True, False], "fail_msg": NOT_JOINED}]]

    def test_check_of_the_fleet_fails_for_a_member_without_a_member_row(
        self, pure1_client
    ):
        """Only the record of array7 names fleet1: Pure1 gives no status."""
        arrays = _lab_arrays() + [_array("array7", fleet="fleet1")]

        made = _play_examples(pure1_client, fleet=dict(arrays=arrays))

        assert made["fleet"] == [[{"that": [True, False], "fail_msg": NOT_JOINED}]]

    def test_check_of_the_fleet_fails_for_a_fleet_without_members(self, pure1_client):
        """No array to reject must not pass as a fleet whose members joined."""
        listings = dict(
            arrays=[_array("array1")], members=[], groups=[], group_members=[]
        )

        made = _play_examples(pure1_client, fleet=listings)

        assert made["fleet"] == [[{"that": [False, True], "fail_msg": NOT_JOINED}]]

    def test_arrays_of_two_fleets_are_shown_with_the_fleet_of_each(self, pure1_client):
        arrays = _lab_arrays()
        arrays[1] = _array("array2", fleet="fleet2")
        rows = [row for row in _lab_members() if row.member.name != "array2"]

        made = _play_examples(pure1_client, placed=dict(arrays=arrays, members=rows))

        assert made["placed"] == [
            [
                {"msg": "array1 in fleet1, which has 3 members"},
                {"msg": "array2 in fleet2, which has 2 members"},
            ]
        ]

    def test_arrays_in_no_fleet_leave_nothing_to_show(self, pure1_client):
        listings = dict(arrays=[_array("array1"), _array("array2")], members=[])

        made = _play_examples(pure1_client, placed=listings)

        assert made["placed"] == [[]]
