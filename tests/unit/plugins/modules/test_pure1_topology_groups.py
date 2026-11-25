# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_topology_groups module.

The organisation of most tests has what no reading of a real one showed so
far: arrays below a nested group, a group name that two fleets have, an
array that only its own record places in a group and one that only a member
row places.
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


from plugins.modules import pure1_topology_groups
from tests.unit.conftest import (
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

ROWS_AS_OF = 1612357445000
ROWS_TIME = "2021-02-03 13:04:05 UTC"
GROUP_AS_OF = 1612447506000
GROUP_TIME = "2021-02-04 14:05:06 UTC"
LISTINGS = [
    "get_fleets",
    "get_fleets_members",
    "get_topology_groups",
    "get_topology_groups_members",
]


def _ref(name=None, kind=None, ref_id=None):
    """Return a reference; without a name the empty object Pure1 sends for none."""
    if name is None:
        return sdk_model("FixedReference")
    return sdk_model(
        "FixedReference", name=name, id=ref_id or "id-" + name, resource_type=kind
    )


def _group_ref(name, fleet):
    """Return the reference to a group; groups of one name differ by their id."""
    if name is None:
        return _ref()
    return _ref(name, "topology-groups", "id-{0}-{1}".format(fleet, name))


def _array(name, fleet=None, group=None, **overrides):
    """Return an array whose own record names this fleet and this group of it."""
    fields = dict(
        id="id-" + name,
        name=name,
        fqdn=name + ".example.com",
        os="Purity//FA",
        model="FA-X70R4",
        version="6.5.0",
        as_of=ROWS_AS_OF,
        fleet=_ref(fleet, "fleets"),
        topology_group=_group_ref(group, fleet),
    )
    fields.update(overrides)
    return sdk_model("Array", **fields)


def _fleet(name):
    return sdk_model("Fleet", id="id-" + name, name=name, as_of=GROUP_AS_OF)


def _member(fleet, array):
    return sdk_model(
        "FleetMember",
        fleet=_ref(fleet, "fleets"),
        member=_ref(array, "arrays"),
        status="joined",
        status_details="",
        coordinator_of=[],
        as_of=ROWS_AS_OF,
    )


def _group(name, fleet="fleet1", parent=None, **overrides):
    fields = dict(
        id="id-{0}-{1}".format(fleet, name),
        name=name,
        fleet=_ref(fleet, "fleets"),
        parent_topology_group=_group_ref(parent, fleet),
        as_of=GROUP_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("TopologyGroup", **fields)


def _group_member(group, name, fleet="fleet1", child=False, **overrides):
    """Return the member row of an array, or of a child group, in a group."""
    fields = dict(
        fleet=_ref(fleet, "fleets"),
        topology_group=_group_ref(group, fleet),
        member=_group_ref(name, fleet) if child else _ref(name, "arrays"),
        status="joined",
        status_details="",
        as_of=ROWS_AS_OF,
    )
    fields.update(overrides)
    return sdk_model("TopologyGroupMember", **fields)


def _serve(
    client,
    arrays=(),
    fleets=(),
    members=(),
    groups=(),
    group_members=(),
    resolved=None,
):
    """Answer the check of get_pure1, the look-up of the array option, each listing."""
    answers = [valid_response()]
    if resolved is not None:
        answers.append(valid_response(resolved))
    answers.append(valid_response(arrays))
    client.get_arrays.side_effect = answers
    client.get_fleets.return_value = valid_response(fleets)
    client.get_fleets_members.return_value = valid_response(members)
    client.get_topology_groups.return_value = valid_response(groups)
    client.get_topology_groups_members.return_value = valid_response(group_members)


def _tree_arrays():
    return [
        _array("array1", "fleet1", "rack1"),
        # In its group by the member row alone
        _array("array2", "fleet1"),
        _array("array3", "fleet1", "region1"),
        # In its group by its own record alone
        _array("array4", "fleet1", "site1"),
        _array("array5", "fleet1"),
        _array("array6", "fleet2", "site1"),
        _array("array7"),
    ]


def _tree_fleets():
    return [_fleet("fleet1"), _fleet("fleet2"), _fleet("fleet3")]


def _tree_members():
    rows = [_member("fleet1", "array" + str(number)) for number in range(1, 6)]
    return rows + [_member("fleet2", "array6")]


def _tree_groups():
    return [
        _group("zone-b", parent="region1"),
        _group("site1", "fleet2"),
        _group("rack1", parent="zone-a"),
        _group("region1"),
        _group("zone-a", parent="region1"),
        _group("site1"),
    ]


def _tree_group_members():
    return [
        _group_member("site1", "array6", "fleet2"),
        _group_member("region1", "zone-a", child=True),
        # The id of the member row need not be the id of the array
        _group_member("rack1", "array1", member=_ref("array1", "arrays", "member-1")),
        _group_member("region1", "zone-b", child=True),
        _group_member("zone-a", "array2"),
        _group_member("zone-a", "rack1", child=True),
        _group_member("region1", "array3"),
    ]


def _serve_tree(client, resolved=None, **replaced):
    listings = dict(
        arrays=_tree_arrays(),
        fleets=_tree_fleets(),
        members=_tree_members(),
        groups=_tree_groups(),
        group_members=_tree_group_members(),
    )
    listings.update(replaced)
    _serve(client, resolved=resolved, **listings)


def _in_group(name, source="both", member_id=None):
    return {
        "array_id": "id-" + name,
        "member_id": member_id or "id-" + name,
        "status": "joined",
        "status_details": "",
        "source": source,
        "as_of": ROWS_TIME,
        "as_of_epoch": ROWS_AS_OF,
    }


def _expected_group(
    name, fleet="fleet1", path=None, children=(), arrays=None, below=()
):
    """Return a listed group; below are the arrays of the groups under it."""
    path = path or [name]
    arrays = arrays or {}
    return {
        "id": "id-{0}-{1}".format(fleet, name),
        "as_of": GROUP_TIME,
        "as_of_epoch": GROUP_AS_OF,
        "parent": path[-2] if len(path) > 1 else None,
        "path": path,
        "children": list(children),
        "arrays": arrays,
        "all_arrays": sorted(set(arrays) | set(below)),
    }


def _where(fleet, path, source="both"):
    return {
        "fleet": fleet,
        "topology_group": path[-1],
        "path": path,
        "source": source,
    }


REGION1 = _expected_group(
    "region1",
    children=["zone-a", "zone-b"],
    arrays={"array3": _in_group("array3")},
    below=["array1", "array2"],
)
ZONE_A = _expected_group(
    "zone-a",
    path=["region1", "zone-a"],
    children=["rack1"],
    arrays={"array2": _in_group("array2", source="members")},
    below=["array1"],
)
ZONE_B = _expected_group("zone-b", path=["region1", "zone-b"])
RACK1 = _expected_group(
    "rack1",
    path=["region1", "zone-a", "rack1"],
    arrays={"array1": _in_group("array1", member_id="member-1")},
)
SITE1 = _expected_group(
    "site1",
    arrays={
        "array4": {
            "array_id": "id-array4",
            "member_id": None,
            "status": None,
            "status_details": None,
            "source": "arrays",
            "as_of": None,
            "as_of_epoch": None,
        }
    },
)
SITE1_OF_FLEET2 = _expected_group(
    "site1", "fleet2", arrays={"array6": _in_group("array6")}
)
ARRAY1 = _where("fleet1", ["region1", "zone-a", "rack1"])
ARRAY2 = _where("fleet1", ["region1", "zone-a"], source="members")
ARRAY3 = _where("fleet1", ["region1"])
ARRAY4 = _where("fleet1", ["site1"], source="arrays")
ARRAY6 = _where("fleet2", ["site1"])
WHOLE = {
    "topology_groups": {
        "fleet1": {
            "rack1": RACK1,
            "region1": REGION1,
            "site1": SITE1,
            "zone-a": ZONE_A,
            "zone-b": ZONE_B,
        },
        "fleet2": {"site1": SITE1_OF_FLEET2},
    },
    "arrays": {
        "array1": ARRAY1,
        "array2": ARRAY2,
        "array3": ARRAY3,
        "array4": ARRAY4,
        "array6": ARRAY6,
    },
}
NOTHING = {"topology_groups": {}, "arrays": {}}


def _result(module):
    module.fail_json.assert_not_called()
    module.exit_json.assert_called_once()
    assert list(module.exit_json.call_args[1]) == ["changed", "pure1_topology_groups"]
    assert module.exit_json.call_args[1]["changed"] is False
    return module.exit_json.call_args[1]["pure1_topology_groups"]


def _warnings(module):
    return [warning[0][0] for warning in module.warn.call_args_list]


def _assert_requests(client, resolved=None):
    """One check, one look-up for the array option, one request per listing."""
    expected = [call(limit=0), call()]
    if resolved is not None:
        expected.insert(1, call(filter=resolved))
    assert client.get_arrays.call_args_list == expected
    for listing in LISTINGS:
        getattr(client, listing).assert_called_once_with()


class TestWholeResult:
    """A run without options returns every group of every fleet."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    def test_every_group_and_every_array_in_a_group(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=WHOLE
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()
        _assert_requests(pure1_client)

    def test_check_mode_returns_the_same(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=WHOLE
        )
        _assert_requests(pure1_client)

    def test_the_arrays_below_a_nested_group_are_counted_in_every_group_above(
        self, pure1_client
    ):
        _serve_tree(pure1_client)

        groups = _result(run_main(pure1_topology_groups))["topology_groups"]["fleet1"]

        assert groups["rack1"]["all_arrays"] == ["array1"]
        assert groups["zone-a"]["all_arrays"] == ["array1", "array2"]
        assert groups["region1"]["all_arrays"] == ["array1", "array2", "array3"]
        assert sorted(groups["region1"]["arrays"]) == ["array3"]
        assert groups["zone-b"]["all_arrays"] == []

    def test_groups_and_arrays_are_in_the_order_of_their_names(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups))

        assert list(result) == ["topology_groups", "arrays"]
        assert list(result["topology_groups"]) == ["fleet1", "fleet2"]
        assert list(result["topology_groups"]["fleet1"]) == [
            "rack1",
            "region1",
            "site1",
            "zone-a",
            "zone-b",
        ]
        assert list(result["arrays"]) == [
            "array1",
            "array2",
            "array3",
            "array4",
            "array6",
        ]

    def test_organisation_without_anything(self, pure1_client):
        _serve(pure1_client)

        module = run_main(pure1_topology_groups)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=NOTHING
        )
        module.warn.assert_not_called()
        _assert_requests(pure1_client)

    def test_fleets_and_arrays_without_a_group(self, pure1_client):
        _serve_tree(pure1_client, groups=[], group_members=[], arrays=[])

        module = run_main(pure1_topology_groups)

        assert _result(module) == NOTHING
        module.warn.assert_not_called()

    def test_objects_with_only_their_names(self, pure1_client):
        """Every value Pure1 can leave out is null, and nothing is left out."""
        named = sdk_model("FixedReference", name="fleet1")
        site = sdk_model("FixedReference", name="site1")
        _serve(
            pure1_client,
            arrays=[sdk_model("Array", name="array1")],
            groups=[sdk_model("TopologyGroup", name="site1", fleet=named)],
            group_members=[
                sdk_model(
                    "TopologyGroupMember",
                    fleet=named,
                    topology_group=site,
                    member=sdk_model("FixedReference", name="array1"),
                )
            ],
        )

        module = run_main(pure1_topology_groups)

        assert _result(module) == {
            "topology_groups": {
                "fleet1": {
                    "site1": {
                        "id": None,
                        "as_of": None,
                        "as_of_epoch": None,
                        "parent": None,
                        "path": ["site1"],
                        "children": [],
                        "arrays": {
                            "array1": {
                                "array_id": None,
                                "member_id": None,
                                "status": None,
                                "status_details": None,
                                "source": "members",
                                "as_of": None,
                                "as_of_epoch": None,
                            }
                        },
                        "all_arrays": ["array1"],
                    }
                }
            },
            "arrays": {"array1": _where("fleet1", ["site1"], source="members")},
        }
        module.warn.assert_not_called()

    def test_member_that_has_not_joined(self, pure1_client):
        rows = _tree_group_members()
        rows[4] = _group_member(
            "zone-a", "array2", status="joining", status_details="Waiting for array2"
        )
        _serve_tree(pure1_client, group_members=rows)

        result = _result(run_main(pure1_topology_groups))

        assert result["topology_groups"]["fleet1"]["zone-a"]["arrays"] == {
            "array2": dict(
                _in_group("array2", source="members"),
                status="joining",
                status_details="Waiting for array2",
            )
        }
        # It is reported with its status, not left out
        assert result["arrays"]["array2"] == ARRAY2

    def test_member_whose_name_no_array_has(self, pure1_client):
        rows = _tree_group_members() + [_group_member("zone-b", "array9")]
        _serve_tree(pure1_client, group_members=rows)

        module = run_main(pure1_topology_groups)

        result = _result(module)
        assert result["topology_groups"]["fleet1"]["zone-b"] == dict(
            ZONE_B,
            arrays={
                "array9": dict(_in_group("array9", source="members"), array_id=None)
            },
        )
        assert result["topology_groups"]["fleet1"]["region1"] == REGION1
        assert result["arrays"] == WHOLE["arrays"]
        module.warn.assert_not_called()

    def test_group_that_only_a_parent_reference_names(self, pure1_client):
        """No listing has region1, no member row and no array names it."""
        reference = _ref("region1", "topology-groups", "id-of-the-reference")
        groups = [
            _group("zone-b", parent_topology_group=reference),
            _group("site1", "fleet2"),
            _group("rack1", parent="zone-a"),
            _group("zone-a", parent_topology_group=reference),
            _group("site1"),
        ]
        rows = [
            row for row in _tree_group_members() if row.topology_group.name != "region1"
        ]
        arrays = [
            _array("array3", "fleet1") if array.name == "array3" else array
            for array in _tree_arrays()
        ]
        _serve_tree(pure1_client, arrays=arrays, groups=groups, group_members=rows)

        module = run_main(pure1_topology_groups)

        result = _result(module)
        assert result["topology_groups"]["fleet1"] == {
            "rack1": RACK1,
            "region1": dict(
                REGION1,
                id="id-of-the-reference",
                as_of=None,
                as_of_epoch=None,
                arrays={},
                all_arrays=["array1", "array2"],
            ),
            "site1": SITE1,
            "zone-a": ZONE_A,
            "zone-b": ZONE_B,
        }
        assert sorted(result["arrays"]) == ["array1", "array2", "array4", "array6"]
        module.warn.assert_not_called()

    def test_group_that_references_and_member_rows_name_and_no_listing_has(
        self, pure1_client
    ):
        groups = [group for group in _tree_groups() if group.name != "region1"]
        _serve_tree(pure1_client, groups=groups)

        result = _result(run_main(pure1_topology_groups))

        assert result["topology_groups"]["fleet1"]["region1"] == dict(
            REGION1, as_of=None, as_of_epoch=None
        )


class TestNameOption:
    """name selects groups by name, with every group below them."""

    def test_group_and_the_groups_below_it(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, name=["zone-a"])

        assert _result(module) == {
            "topology_groups": {"fleet1": {"rack1": RACK1, "zone-a": ZONE_A}},
            "arrays": {"array1": ARRAY1, "array2": ARRAY2},
        }
        module.warn.assert_not_called()
        # The selection is made in the module: no further and no other request
        _assert_requests(pure1_client)

    def test_root_group_selects_its_whole_tree(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["region1"]))

        assert result == {
            "topology_groups": {
                "fleet1": {
                    "rack1": RACK1,
                    "region1": REGION1,
                    "zone-a": ZONE_A,
                    "zone-b": ZONE_B,
                }
            },
            "arrays": {"array1": ARRAY1, "array2": ARRAY2, "array3": ARRAY3},
        }

    def test_group_without_groups_below_it(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["rack1"]))

        # The path still names the groups above, which are not in the result
        assert result == {
            "topology_groups": {"fleet1": {"rack1": RACK1}},
            "arrays": {"array1": ARRAY1},
        }
        assert result["topology_groups"]["fleet1"]["rack1"]["path"] == [
            "region1",
            "zone-a",
            "rack1",
        ]

    def test_several_names(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["zone-b", "rack1"]))

        assert result == {
            "topology_groups": {"fleet1": {"rack1": RACK1, "zone-b": ZONE_B}},
            "arrays": {"array1": ARRAY1},
        }

    def test_name_in_another_case(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["Zone-A"]))

        assert result == {
            "topology_groups": {"fleet1": {"rack1": RACK1, "zone-a": ZONE_A}},
            "arrays": {"array1": ARRAY1, "array2": ARRAY2},
        }

    @pytest.mark.parametrize("name", ["site9", "SITE9", "Site9"])
    def test_group_that_pure1_spells_with_capitals(self, pure1_client, name):
        groups = _tree_groups() + [_group("Site9", "fleet2")]
        _serve_tree(pure1_client, groups=groups)

        result = _result(run_main(pure1_topology_groups, name=[name]))

        assert result == {
            "topology_groups": {
                "fleet2": {"Site9": _expected_group("Site9", "fleet2")}
            },
            "arrays": {},
        }

    def test_name_that_groups_of_two_fleets_have(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["site1"]))

        assert result == {
            "topology_groups": {
                "fleet1": {"site1": SITE1},
                "fleet2": {"site1": SITE1_OF_FLEET2},
            },
            "arrays": {"array4": ARRAY4, "array6": ARRAY6},
        }

    def test_name_with_a_fleet_selects_the_group_of_that_fleet(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(
            run_main(pure1_topology_groups, name=["site1"], fleet="fleet2")
        )

        assert result == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }

    def test_name_of_a_group_of_another_fleet_selects_nothing(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, name=["rack1"], fleet="fleet2")

        assert _result(module) == NOTHING
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "names",
        [["zone-z"], ["zone"], ["zone-a "], [""], ["array1"], ["fleet1"]],
        ids=["unknown", "part", "blank at the end", "empty", "array", "fleet"],
    )
    def test_name_that_no_group_has_selects_nothing(self, pure1_client, names):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, name=names)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=NOTHING
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_known_and_unknown_name(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name=["zone-z", "zone-b"]))

        assert result == {
            "topology_groups": {"fleet1": {"zone-b": ZONE_B}},
            "arrays": {},
        }

    def test_arrays_names_only_groups_of_the_result(self, pure1_client):
        """With rows in two groups, name can select the one arrays does not name."""
        rows = _tree_group_members() + [
            _group_member("zone-b", "array3", status="joining")
        ]
        _serve_tree(pure1_client, group_members=rows)

        module = run_main(pure1_topology_groups, name=["zone-b"])

        zone_b = dict(
            ZONE_B,
            arrays={
                "array3": dict(_in_group("array3", source="members"), status="joining")
            },
            all_arrays=["array3"],
        )
        assert _result(module) == {
            "topology_groups": {"fleet1": {"zone-b": zone_b}},
            "arrays": {},
        }
        module.warn.assert_not_called()

    def test_no_names_select_every_group(self, pure1_client):
        _serve_tree(pure1_client)

        assert _result(run_main(pure1_topology_groups, name=[])) == WHOLE

    def test_one_name_as_text(self, pure1_client):
        """The argument spec makes the list, as for a task that gives a string."""
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, name="zone-b,rack1"))

        assert sorted(result["topology_groups"]["fleet1"]) == ["rack1", "zone-b"]


class TestFleetOption:
    """fleet selects the groups of one fleet, which Pure1 must know."""

    def test_groups_of_the_fleet(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, fleet="fleet1")

        assert _result(module) == {
            "topology_groups": {"fleet1": WHOLE["topology_groups"]["fleet1"]},
            "arrays": {
                "array1": ARRAY1,
                "array2": ARRAY2,
                "array3": ARRAY3,
                "array4": ARRAY4,
            },
        }
        module.warn.assert_not_called()
        _assert_requests(pure1_client)

    def test_the_other_fleet(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, fleet="fleet2"))

        assert result == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }

    def test_fleet_in_another_case_is_returned_as_pure1_spells_it(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups, fleet="FLEET2"))

        assert result == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }

    def test_fleet_without_groups_gives_an_empty_result(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, fleet="fleet3")

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=NOTHING
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_fleet_that_only_an_array_names_is_known(self, pure1_client):
        arrays = _tree_arrays() + [_array("array8", "fleet4")]
        _serve_tree(pure1_client, arrays=arrays)

        module = run_main(pure1_topology_groups, fleet="fleet4")

        assert _result(module) == NOTHING

    @pytest.mark.parametrize("fleet", ["fleet9", "fleet", "fleet1 ", "region1"])
    def test_fleet_that_pure1_does_not_know_fails_the_task(self, pure1_client, fleet):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, fleet=fleet)

        module.fail_json.assert_called_once_with(
            msg="Fleet {0} is not known to Pure1.".format(fleet)
        )
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    def test_unknown_fleet_fails_in_an_organisation_without_fleets(self, pure1_client):
        _serve(pure1_client)

        module = run_main(pure1_topology_groups, fleet="fleet1")

        module.fail_json.assert_called_once_with(
            msg="Fleet fleet1 is not known to Pure1."
        )

    @pytest.mark.parametrize("fleet", ["", "  "], ids=["empty", "blank"])
    def test_empty_fleet_name_fails_before_any_listing(self, pure1_client, fleet):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, fleet=fleet)

        module.fail_json.assert_called_once_with(
            msg="The name of a fleet must not be empty."
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        pure1_client.get_fleets.assert_not_called()

    def test_spelling_of_the_task_wins_over_the_name_in_another_case(
        self, pure1_client
    ):
        fleets = _tree_fleets() + [_fleet("Fleet2")]
        groups = _tree_groups() + [_group("site9", "Fleet2")]
        _serve_tree(pure1_client, fleets=fleets, groups=groups)

        result = _result(run_main(pure1_topology_groups, fleet="Fleet2"))

        assert result == {
            "topology_groups": {
                "Fleet2": {"site9": _expected_group("site9", "Fleet2")}
            },
            "arrays": {},
        }

    def test_name_that_two_fleets_have_in_another_case_fails_the_task(
        self, pure1_client
    ):
        _serve_tree(pure1_client, fleets=_tree_fleets() + [_fleet("Fleet2")])

        module = run_main(pure1_topology_groups, fleet="FLEET2")

        module.fail_json.assert_called_once_with(
            msg="Pure1 knows more than one fleet whose name is FLEET2 in another "
            "case: Fleet2, fleet2. Give the name as Pure1 spells it."
        )
        module.exit_json.assert_not_called()


class TestArrayOption:
    """array selects the group of each array, with every group above it."""

    def test_group_of_the_array_and_the_groups_above_it(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array1", "fleet1", "rack1")])

        module = run_main(pure1_topology_groups, array=["array1"])

        # The groups are whole; arrays has the named array only
        assert _result(module) == {
            "topology_groups": {
                "fleet1": {"rack1": RACK1, "region1": REGION1, "zone-a": ZONE_A}
            },
            "arrays": {"array1": ARRAY1},
        }
        module.warn.assert_not_called()
        # One request more than without the option
        _assert_requests(pure1_client, resolved="name='array1'")

    def test_array_in_a_root_group(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array3")])

        result = _result(run_main(pure1_topology_groups, array=["array3"]))

        assert result == {
            "topology_groups": {"fleet1": {"region1": REGION1}},
            "arrays": {"array3": ARRAY3},
        }

    def test_several_arrays_in_several_fleets(self, pure1_client):
        resolved = [_array("array6"), _array("array2"), _array("array4")]
        _serve_tree(pure1_client, resolved=resolved)

        module = run_main(pure1_topology_groups, array=["array6", "array2", "array4"])

        assert _result(module) == {
            "topology_groups": {
                "fleet1": {"region1": REGION1, "site1": SITE1, "zone-a": ZONE_A},
                "fleet2": {"site1": SITE1_OF_FLEET2},
            },
            "arrays": {"array2": ARRAY2, "array4": ARRAY4, "array6": ARRAY6},
        }
        _assert_requests(pure1_client, resolved="name=('array6','array2','array4')")

    def test_name_in_another_case_is_returned_as_pure1_spells_it(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array3")])

        result = _result(run_main(pure1_topology_groups, array=["ARRAY3"]))

        assert result == {
            "topology_groups": {"fleet1": {"region1": REGION1}},
            "arrays": {"array3": ARRAY3},
        }
        _assert_requests(pure1_client, resolved="name='ARRAY3'")

    @pytest.mark.parametrize("array", ["array5", "array7"], ids=["in a fleet", "alone"])
    def test_array_in_no_group_gives_an_empty_result(self, pure1_client, array):
        _serve_tree(pure1_client, resolved=[_array(array)])

        module = run_main(pure1_topology_groups, array=[array])

        module.exit_json.assert_called_once_with(
            changed=False, pure1_topology_groups=NOTHING
        )
        module.fail_json.assert_not_called()
        module.warn.assert_not_called()

    def test_array_in_a_group_and_array_in_none(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array7"), _array("array6")])

        result = _result(run_main(pure1_topology_groups, array=["array7", "array6"]))

        assert result == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }

    def test_array_that_pure1_does_not_know_fails_before_any_listing(
        self, pure1_client
    ):
        _serve_tree(pure1_client, resolved=[_array("array1")])

        module = run_main(pure1_topology_groups, array=["array1", "array9"])

        module.fail_json.assert_called_once_with(
            msg="Array array9 is not known to Pure1."
        )
        module.exit_json.assert_not_called()
        assert pure1_client.get_arrays.call_args_list == [
            call(limit=0),
            call(filter="name=('array1','array9')"),
        ]
        pure1_client.get_fleets.assert_not_called()

    def test_empty_array_name_fails_the_task(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, array=["array1", ""])

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_no_arrays_select_every_group_without_a_look_up(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, array=[])

        assert _result(module) == WHOLE
        _assert_requests(pure1_client)

    def test_array_with_the_fleet_of_its_group(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array6")])

        result = _result(
            run_main(pure1_topology_groups, array=["array6"], fleet="fleet2")
        )

        assert result == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }

    def test_array_with_another_fleet_gives_an_empty_result(self, pure1_client):
        _serve_tree(pure1_client, resolved=[_array("array6")])

        module = run_main(pure1_topology_groups, array=["array6"], fleet="fleet1")

        assert _result(module) == NOTHING
        module.warn.assert_not_called()

    def test_array_with_member_rows_in_two_groups(self, pure1_client):
        """Both groups hold it; arrays names the one its own record names too."""
        rows = _tree_group_members() + [_group_member("site1", "array1")]
        _serve_tree(pure1_client, resolved=[_array("array1")], group_members=rows)

        result = _result(run_main(pure1_topology_groups, array=["array1"]))

        site1 = dict(
            SITE1,
            arrays=dict(SITE1["arrays"], array1=_in_group("array1", source="members")),
            all_arrays=["array1", "array4"],
        )
        assert result == {
            "topology_groups": {
                "fleet1": {
                    "rack1": RACK1,
                    "region1": REGION1,
                    "site1": site1,
                    "zone-a": ZONE_A,
                }
            },
            "arrays": {"array1": ARRAY1},
        }

    def test_arrays_names_only_groups_of_the_result(self, pure1_client):
        """With rows in two fleets, the fleet option can leave the group out."""
        rows = _tree_group_members() + [_group_member("site1", "array1", "fleet2")]
        _serve_tree(pure1_client, resolved=[_array("array1")], group_members=rows)

        result = _result(
            run_main(pure1_topology_groups, array=["array1"], fleet="fleet2")
        )

        site1 = dict(
            SITE1_OF_FLEET2,
            arrays=dict(
                SITE1_OF_FLEET2["arrays"], array1=_in_group("array1", source="members")
            ),
            all_arrays=["array1", "array6"],
        )
        assert result == {"topology_groups": {"fleet2": {"site1": site1}}, "arrays": {}}

    def test_name_and_array_exclude_each_other(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, name=["site1"], array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="parameters are mutually exclusive: name|array"
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_not_called()

    @pytest.mark.parametrize(
        "params",
        [
            {"name": [], "array": []},
            {"name": [], "array": ["array1"]},
            {"name": ["site1"], "array": []},
        ],
        ids=["both empty", "no names", "no arrays"],
    )
    def test_empty_list_beside_the_other_option_is_refused_as_well(
        self, pure1_client, params
    ):
        """Alone an empty list counts as not set; the exclusion sees it as given."""
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, **params)

        module.fail_json.assert_called_once_with(
            msg="parameters are mutually exclusive: name|array"
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_not_called()


class TestLeftOut:
    """What cannot be filed is left out, with one warning that counts it."""

    def _unplaced(self):
        """Return two arrays whose own record names a group no listing has."""
        gone = _ref("gone", "topology-groups", "id-gone")
        return [
            _array("array8", "fleet1", topology_group=gone),
            _array("array9", topology_group=gone),
        ]

    def test_array_whose_group_pure1_does_not_list(self, pure1_client):
        _serve_tree(pure1_client, arrays=_tree_arrays() + self._unplaced())

        module = run_main(pure1_topology_groups)

        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Arrays that name a topology group which Pure1 does not list are left "
            "out of the result: 2."
        ]

    def test_named_array_whose_group_pure1_does_not_list(self, pure1_client):
        _serve_tree(
            pure1_client,
            arrays=_tree_arrays() + self._unplaced(),
            resolved=[_array("array9"), _array("array3")],
        )

        module = run_main(pure1_topology_groups, array=["array9", "array3"])

        assert _result(module) == {
            "topology_groups": {"fleet1": {"region1": REGION1}},
            "arrays": {"array3": ARRAY3},
        }
        assert _warnings(module) == [
            "Arrays that name a topology group which Pure1 does not list are left "
            "out of the result: 1."
        ]

    def test_no_warning_for_an_array_the_task_does_not_name(self, pure1_client):
        _serve_tree(
            pure1_client,
            arrays=_tree_arrays() + self._unplaced(),
            resolved=[_array("array3")],
        )

        module = run_main(pure1_topology_groups, array=["array3"])

        assert _result(module)["arrays"] == {"array3": ARRAY3}
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "fleet, count",
        [
            # array8 is in fleet1 and array9 in no fleet
            pytest.param("fleet1", 2, id="fleet of one of the arrays"),
            pytest.param("fleet2", 1, id="another fleet"),
            pytest.param("FLEET2", 1, id="another fleet in another case"),
        ],
    )
    def test_fleet_counts_only_arrays_that_can_be_of_the_fleet(
        self, pure1_client, fleet, count
    ):
        _serve_tree(pure1_client, arrays=_tree_arrays() + self._unplaced())

        module = run_main(pure1_topology_groups, fleet=fleet)

        name = fleet.lower()
        assert _result(module)["topology_groups"] == {
            name: WHOLE["topology_groups"][name]
        }
        assert _warnings(module) == [
            "Arrays that name a topology group which Pure1 does not list are left "
            "out of the result: {0}.".format(count)
        ]

    def test_fleet_gives_no_warning_for_arrays_of_other_fleets(self, pure1_client):
        _serve_tree(pure1_client, arrays=_tree_arrays() + self._unplaced()[:1])

        module = run_main(pure1_topology_groups, fleet="fleet2")

        assert _result(module) == {
            "topology_groups": {"fleet2": {"site1": SITE1_OF_FLEET2}},
            "arrays": {"array6": ARRAY6},
        }
        module.warn.assert_not_called()

    def test_name_counts_the_arrays_of_every_fleet(self, pure1_client):
        """The fleet of a group that Pure1 does not list is not known."""
        _serve_tree(pure1_client, arrays=_tree_arrays() + self._unplaced())

        module = run_main(pure1_topology_groups, name=["site1"])

        assert _warnings(module) == [
            "Arrays that name a topology group which Pure1 does not list are left "
            "out of the result: 2."
        ]

    def test_objects_without_a_name_are_left_out(self, pure1_client):
        fleet1 = _ref("fleet1", "fleets")
        _serve_tree(
            pure1_client,
            arrays=_tree_arrays() + [sdk_model("Array", id="id-array8")],
            fleets=_tree_fleets() + [sdk_model("Fleet", id="id-fleet8")],
            members=_tree_members() + [sdk_model("FleetMember", fleet=fleet1)],
            groups=_tree_groups()
            + [sdk_model("TopologyGroup", id="id-fleet1-site8", fleet=fleet1)],
            group_members=_tree_group_members()
            + [
                sdk_model(
                    "TopologyGroupMember",
                    fleet=fleet1,
                    topology_group=_group_ref("site1", "fleet1"),
                )
            ],
        )

        module = run_main(pure1_topology_groups)

        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of another "
            "array are left out of the result: 1.",
            "Fleets that Pure1 returned without a name or with the name of another "
            "fleet are left out of the result: 1.",
            "Fleet member rows that Pure1 returned without a fleet or a member "
            "name, for a member that is no array or a second time for one member "
            "are left out of the result: 1.",
            "Topology groups that Pure1 returned without a name or a fleet, or "
            "with the name of another group of the fleet, are left out of the "
            "result: 1.",
            "Topology group member rows that Pure1 returned without a fleet, a "
            "group or a member name, for a member that is neither an array nor a "
            "group or a second time for one array are left out of the result: 1.",
        ]

    def test_second_array_of_one_name_is_left_out(self, pure1_client):
        """The first of the name is the array; one warning counts both kinds."""
        arrays = _tree_arrays() + [
            _array("array1", "fleet2", "site1", id="id-other"),
            sdk_model("Array", id="id-array8"),
        ]
        _serve_tree(pure1_client, arrays=arrays)

        module = run_main(pure1_topology_groups)

        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Arrays that Pure1 returned without a name or with the name of another "
            "array are left out of the result: 2."
        ]

    def test_cycle_of_parents_is_cut(self, pure1_client):
        groups = [_group("site1", parent="site2"), _group("site2", parent="site1")]
        _serve(pure1_client, fleets=[_fleet("fleet1")], groups=groups)

        module = run_main(pure1_topology_groups)

        assert _result(module) == {
            "topology_groups": {
                "fleet1": {
                    "site1": _expected_group(
                        "site1", path=["site2", "site1"], children=[]
                    ),
                    "site2": _expected_group("site2", children=["site1"]),
                }
            },
            "arrays": {},
        }
        assert _warnings(module) == [
            "Parents of topology groups that form a cycle are left out of the "
            "result: 1."
        ]


FAILURES = [
    pytest.param(
        "return_value",
        error_response(403, "Access denied"),
        "Pure1 request {0} failed (HTTP 403): Access denied",
        id="error answer",
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
]


# The listings of the fleet map: method, answer, items, noun, number of items
PAGED = [
    ("get_fleets", "FleetGetResponse", _tree_fleets, "fleets", 3),
    (
        "get_fleets_members",
        "FleetMemberGetResponse",
        _tree_members,
        "fleet member rows",
        6,
    ),
    (
        "get_topology_groups",
        "TopologyGroupGetResponse",
        _tree_groups,
        "topology groups",
        6,
    ),
    (
        "get_topology_groups_members",
        "TopologyGroupMemberGetResponse",
        _tree_group_members,
        "topology group member rows",
        7,
    ),
]


def _arrays_in_pages(client, arrays, **paging):
    """Answer the check of get_pure1, then the listing of the arrays in pages."""
    answers = paged_answers("ArrayGetResponse", arrays, **paging)

    def answer(**kwargs):
        if kwargs == {"limit": 0}:
            return valid_response()
        return answers(**kwargs)

    client.get_arrays.side_effect = answer
    return answers


class TestRequests:
    """Pages, short listings, failed requests and an SDK that is too old."""

    @pytest.mark.parametrize("method, answer, items, what, count", PAGED)
    def test_listing_is_read_across_pages(
        self, pure1_client, method, answer, items, what, count
    ):
        _serve_tree(pure1_client)
        answers = paged_answers(answer, items(), page_size=2)
        getattr(pure1_client, method).side_effect = answers

        module = run_main(pure1_topology_groups)

        assert answers.requests == [{}] + [
            {"continuation_token": "page-{0}".format(start)}
            for start in range(2, count, 2)
        ]
        assert _result(module) == WHOLE
        module.warn.assert_not_called()

    @pytest.mark.parametrize("method, answer, items, what, count", PAGED)
    def test_listing_that_comes_short_gives_a_warning(
        self, pure1_client, method, answer, items, what, count
    ):
        _serve_tree(pure1_client)
        answers = paged_answers(answer, items(), total_item_count=9)
        getattr(pure1_client, method).side_effect = answers

        module = run_main(pure1_topology_groups)

        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Pure1 returned {0} of 9 {1}: the result may be incomplete.".format(
                count, what
            )
        ]

    def test_arrays_are_read_across_pages(self, pure1_client):
        _serve_tree(pure1_client)
        answers = _arrays_in_pages(pure1_client, _tree_arrays(), page_size=3)

        module = run_main(pure1_topology_groups)

        assert answers.requests == [
            {},
            {"continuation_token": "page-3"},
            {"continuation_token": "page-6"},
        ]
        assert _result(module) == WHOLE
        module.warn.assert_not_called()

    def test_arrays_that_come_short_give_a_warning(self, pure1_client):
        _serve_tree(pure1_client)
        answers = _arrays_in_pages(pure1_client, _tree_arrays(), total_item_count=9)

        module = run_main(pure1_topology_groups)

        # pure1_list asks once more, for what would follow the seven arrays
        assert answers.requests == [{}, {"offset": 7}]
        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Pure1 returned 7 of 9 arrays: the result may be incomplete."
        ]

    def test_arrays_that_come_short_without_a_total(self, pure1_client):
        """No total, and the request for what follows gives the same arrays again."""
        _serve_tree(pure1_client)
        requests = []

        def answer(**page):
            if page == {"limit": 0}:
                return valid_response()
            requests.append(page)
            items = ItemIterator(
                None, {}, None, None, _tree_arrays(), None, more_items_remaining=False
            )
            return ValidResponse(200, None, None, items, {})

        pure1_client.get_arrays.side_effect = answer

        module = run_main(pure1_topology_groups)

        assert requests == [{}, {"offset": 7}]
        assert _result(module) == WHOLE
        assert _warnings(module) == [
            "Pure1 returned 7 of an unknown number of arrays: the result may be "
            "incomplete."
        ]

    @pytest.mark.parametrize("method", ["get_arrays"] + LISTINGS)
    @pytest.mark.parametrize("attribute, answer, expected", FAILURES)
    def test_failed_listing_fails_the_task_with_one_line(
        self, pure1_client, method, attribute, answer, expected
    ):
        _serve_tree(pure1_client)
        if method == "get_arrays":
            # The first answer is for the check of get_pure1
            answer = [valid_response(), answer]
            attribute = "side_effect"
        else:
            getattr(pure1_client, method).return_value = None
        setattr(getattr(pure1_client, method), attribute, answer)

        module = run_main(pure1_topology_groups)

        module.fail_json.assert_called_once_with(msg=expected.format(method))
        module.exit_json.assert_not_called()
        module.warn.assert_not_called()

    @pytest.mark.parametrize(
        "answer, expected",
        [
            (
                error_response(400, "Illegal filter format", "Unexpected input"),
                "Pure1 request get_arrays failed (HTTP 400): Illegal filter format: "
                "Unexpected input",
            ),
            (
                PureError("token refused"),
                "Pure1 request get_arrays failed: PureError: token refused",
            ),
        ],
        ids=["error answer", "PureError"],
    )
    def test_failed_look_up_of_the_arrays_fails_the_task(
        self, pure1_client, answer, expected
    ):
        _serve_tree(pure1_client)
        pure1_client.get_arrays.side_effect = [valid_response(), answer]

        module = run_main(pure1_topology_groups, array=["array1"])

        module.fail_json.assert_called_once_with(msg=expected)
        module.exit_json.assert_not_called()
        pure1_client.get_fleets.assert_not_called()

    @pytest.mark.parametrize(
        "method, minimum",
        [
            ("get_topology_groups", "1.99.0"),
            ("get_topology_groups_members", "1.99.0"),
            ("get_fleets_members", "1.99.0"),
            ("get_fleets", "1.93.0"),
        ],
    )
    def test_sdk_without_a_method_fails_before_any_listing(
        self, pure1_client, monkeypatch, method, minimum
    ):
        _serve_tree(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.92.0")
        setattr(pure1_client, method, None)

        module = run_main(pure1_topology_groups, array=["array1"])

        module.fail_json.assert_called_once_with(
            msg="py-pure-client {0} or later is required: this module needs "
            "Client.{1}(), which the installed py-pure-client 1.92.0 does not "
            "have.".format(minimum, method)
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_called_once_with(limit=0)

    def test_sdk_of_pure1_rest_1_6_is_told_of_the_method_of_the_module(
        self, pure1_client, monkeypatch
    ):
        """py-pure-client 1.93.0 to 1.98.0 has get_fleets and none of the others."""
        _serve_tree(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.98.0")
        for method in LISTINGS[1:]:
            setattr(pure1_client, method, None)

        module = run_main(pure1_topology_groups)

        module.fail_json.assert_called_once_with(
            msg="py-pure-client 1.99.0 or later is required: this module needs "
            "Client.get_topology_groups(), which the installed py-pure-client "
            "1.98.0 does not have."
        )

    def test_sdk_without_any_of_the_methods_is_told_of_the_method_of_the_module(
        self, pure1_client, monkeypatch
    ):
        """py-pure-client before 1.93.0 has none of the four, get_fleets neither."""
        _serve_tree(pure1_client)
        monkeypatch.setattr(pypureclient, "__version__", "1.92.0")
        for method in LISTINGS:
            setattr(pure1_client, method, None)

        module = run_main(pure1_topology_groups)

        # Not 1.93.0 for get_fleets: an upgrade to that release would fail again
        module.fail_json.assert_called_once_with(
            msg="py-pure-client 1.99.0 or later is required: this module needs "
            "Client.get_topology_groups(), which the installed py-pure-client "
            "1.92.0 does not have."
        )
        module.exit_json.assert_not_called()
        pure1_client.get_arrays.assert_called_once_with(limit=0)


class TestArguments:
    """What the argument spec takes and refuses."""

    def test_option_the_module_does_not_have(self, pure1_client):
        _serve_tree(pure1_client)

        module = run_main(pure1_topology_groups, filter="name='site1'")

        module.exit_json.assert_not_called()
        message = module.fail_json.call_args[1]["msg"]
        assert message.startswith("Unsupported parameters for module: filter.")
        pure1_client.get_arrays.assert_not_called()


REQUIRED_CALLS_NOTE = (
    "This module makes the Pure1 REST 1.6 call C(get_fleets) and the Pure1 REST "
    "1.7 calls C(get_fleets_members), C(get_topology_groups) and "
    "C(get_topology_groups_members)."
)


def _documented():
    return yaml.safe_load(pure1_topology_groups.RETURN)["pure1_topology_groups"]


def _assert_keys(result, documented):
    """Compare a result with what RETURN describes, key by key, both ways."""
    assert set(result) == set(documented)
    group_keys = documented["topology_groups"]["contains"]
    for groups in result["topology_groups"].values():
        for group in groups.values():
            assert set(group) == set(group_keys)
            for array in group["arrays"].values():
                assert set(array) == set(group_keys["arrays"]["contains"])
    for array in result["arrays"].values():
        assert set(array) == set(documented["arrays"]["contains"])


class TestDocumentation:
    """RETURN describes what the module returns, in both directions."""

    def test_return_has_the_keys_of_a_result(self, pure1_client):
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups))

        _assert_keys(result, _documented()["contains"])
        # The comparison saw a group with arrays and an array of the flat map
        assert result["topology_groups"]["fleet1"]["rack1"]["arrays"]
        assert result["arrays"]

    def test_sample_has_the_keys_of_a_result(self):
        sample = _documented()["sample"]

        _assert_keys(sample, _documented()["contains"])
        assert sample["topology_groups"]["fleet1"]["site1"]["arrays"]
        assert sample["arrays"]

    def test_sample_is_what_the_module_returns_for_such_an_organisation(
        self, pure1_client
    ):
        sample = _documented()["sample"]
        groups = sample["topology_groups"]["fleet1"]
        arrays = groups["site1"]["arrays"]
        _serve(
            pure1_client,
            arrays=[
                _array(
                    "array1",
                    "fleet1",
                    id=arrays["array1"]["array_id"],
                    topology_group=_ref(
                        "site1", "topology-groups", groups["site1"]["id"]
                    ),
                ),
                _array("array2", "fleet1", id=arrays["array2"]["array_id"]),
            ],
            fleets=[_fleet("fleet1")],
            groups=[
                _group("region1", id=groups["region1"]["id"], as_of=ROWS_AS_OF),
                _group(
                    "site1",
                    parent="region1",
                    id=groups["site1"]["id"],
                    as_of=ROWS_AS_OF,
                ),
            ],
            group_members=[
                _group_member(
                    "site1",
                    name,
                    member=_ref(name, "arrays", arrays[name]["member_id"]),
                )
                for name in ("array1", "array2")
            ],
        )

        assert _result(run_main(pure1_topology_groups)) == sample

    def test_every_required_method_is_in_the_note_that_names_no_release(self):
        """The collection requires the py-pure-client that has them all: a
        note with a release would read as a requirement of its own."""
        notes = yaml.safe_load(pure1_topology_groups.DOCUMENTATION)["notes"]

        assert notes[0] == REQUIRED_CALLS_NOTE
        for method, _minimum in pure1_topology_groups.REQUIRED_METHODS:
            named = [note for note in notes if "C({0})".format(method) in note]
            assert named == [notes[0]], method
        assert "py-pure-client" not in " ".join(notes)

    def test_an_array_of_a_group_has_the_keys_of_a_fleet_member(self, pure1_client):
        """The ID of the array in Pure1 is array_id, as in pure1_fleets."""
        _serve_tree(pure1_client)

        result = _result(run_main(pure1_topology_groups))

        rack1 = result["topology_groups"]["fleet1"]["rack1"]
        assert rack1["id"] == "id-fleet1-rack1"
        assert "id" not in rack1["arrays"]["array1"]
        assert rack1["arrays"]["array1"]["array_id"] == "id-array1"
        assert rack1["arrays"]["array1"]["member_id"] == "member-1"

    @pytest.mark.parametrize(
        "block, path, says",
        [
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "An empty list counts as not set, and every group is reported.",
                id="no names",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "An empty list counts as not set, and every group is reported.",
                id="no arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.name",
                "A task that gives both options fails, also when one of them is "
                "an empty list.",
                id="no names beside arrays",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.array",
                "A task that gives both options fails, also when one of them is "
                "an empty list.",
                id="no arrays beside names",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.fleet",
                "the result has the name as Pure1 spells it.",
                id="fleet in another case",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.fleet",
                "It takes one name.",
                id="one fleet",
            ),
            pytest.param(
                "DOCUMENTATION",
                "options.fleet",
                "or knows more than one fleet of the name in another case and "
                "none as the task spells it.",
                id="two fleets in another case",
            ),
            pytest.param(
                "RETURN",
                "pure1_topology_groups.contains.arrays",
                "It is not listed when that group is not in the result, also "
                "when another group of the result holds it.",
                id="array in two groups",
            ),
            pytest.param(
                "RETURN",
                "pure1_topology_groups.contains.arrays",
                "With I(fleet) the warning counts the arrays of that fleet and "
                "the arrays in no fleet.",
                id="arrays left out with a fleet",
            ),
            pytest.param(
                "RETURN",
                "pure1_topology_groups.contains.topology_groups.contains.arrays"
                ".contains.as_of",
                "The status is as of this time, which is not the time the array "
                "joined the group.",
                id="time of a member row",
            ),
        ],
    )
    def test_documentation_says_what_the_tests_of_the_module_pin(
        self, block, path, says
    ):
        entry = yaml.safe_load(getattr(pure1_topology_groups, block))
        for key in path.split("."):
            entry = entry[key]

        assert says in " ".join(entry["description"])


MODULE = "everpure.pure1.pure1_topology_groups"


def _template(text, variables):
    """Return what ansible-core makes of a template of a task."""
    templar = Templar(loader=None, variables=variables)
    return templar.template(trust_as_template(text))


def _play_examples(client, **served):
    """Run the tasks of EXAMPLES in their order and return what they make

    A task of the module runs through main() with the options of the example
    and registers its result. Pure1 has the tree for it, with the listings
    replaced that served gives under the name the task registers. Every other
    task is returned under the name that the task of the module before it
    registered, as a list with one entry for each item of its loop, one
    without a loop: its when, and unless that is false the templates of its
    arguments, as ansible-core evaluates them.
    """
    registered = {}
    made = {}
    reads = None
    for task in yaml.safe_load(pure1_topology_groups.EXAMPLES):
        options = task.get(MODULE)
        if options is not None:
            options = {
                name: value
                for name, value in options.items()
                if name not in ("app_id", "key_file")
            }
            reads = task["register"]
            resolved = [_array(name) for name in options.get("array", [])]
            _serve_tree(client, resolved=resolved or None, **served.get(reads, {}))
            module = run_main(pure1_topology_groups, **options)
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


def _checks(tasks):
    """Return the outcome of every assertion that the tasks made, in order."""
    return [
        outcome for task in tasks for entry in task for outcome in entry.get("that", [])
    ]


class TestExamples:
    """The tasks of EXAMPLES, on what the module returns."""

    def test_examples_read_what_the_module_returns(self, pure1_client):
        made = _play_examples(pure1_client)

        assert list(made) == ["topology", "region", "placement"]
        assert made["topology"] == [
            [
                {"msg": "array1 is in region1 / zone-a / rack1 of fleet1"},
                {"msg": "array2 is in region1 / zone-a of fleet1"},
                {"msg": "array3 is in region1 of fleet1"},
                {"msg": "array4 is in site1 of fleet1"},
                {"msg": "array6 is in site1 of fleet2"},
            ]
        ]
        # The group is there, and each of its four groups has only joined arrays
        assert [len(task) for task in made["region"]] == [1, 4]
        assert _checks(made["region"]) == [True] * 5
        assert made["placement"] == [
            [{"that": [True]}],
            [{"when": True, "msg": ["array1"]}],
        ]

    @pytest.mark.parametrize(
        "listings",
        [
            pytest.param(dict(groups=[], group_members=[], arrays=[]), id="no groups"),
            pytest.param(
                dict(
                    groups=[_group("site1"), _group("site1", "fleet2")],
                    group_members=[_group_member("site1", "array6", "fleet2")],
                    arrays=[
                        _array("array4", "fleet1", "site1"),
                        _array("array6", "fleet2", "site1"),
                    ],
                ),
                id="no group of the name",
            ),
        ],
    )
    def test_check_of_the_region_fails_when_the_task_selected_no_group(
        self, pure1_client, listings
    ):
        """A group name with a typing error must not pass as a joined region."""
        made = _play_examples(pure1_client, region=listings)

        assert False in _checks(made["region"])

    def test_check_of_the_region_fails_for_an_array_that_has_not_joined(
        self, pure1_client
    ):
        rows = _tree_group_members()
        rows[4] = _group_member("zone-a", "array2", status="joining")

        made = _play_examples(pure1_client, region=dict(group_members=rows))

        # One assertion for the group, then one for each of its four groups
        assert _checks(made["region"]) == [True, True, True, False, True]

    def test_check_of_the_region_fails_for_an_array_without_a_member_row(
        self, pure1_client
    ):
        """Only the record of array3 names region1: Pure1 gives no status."""
        rows = [row for row in _tree_group_members() if row.member.name != "array3"]

        made = _play_examples(pure1_client, region=dict(group_members=rows))

        assert _checks(made["region"]) == [True, True, False, True, True]

    def test_arrays_in_no_group_fail_the_check_of_the_placement(self, pure1_client):
        made = _play_examples(
            pure1_client, placement=dict(groups=[], group_members=[], arrays=[])
        )

        assert made["placement"] == [[{"that": [False]}], [{"when": False}]]
