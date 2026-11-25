# -*- coding: utf-8 -*-

# This code is part of Ansible, but is an independent component.
# This particular file snippet, and this file snippet only, is BSD licensed.
# Modules you write using this snippet, which is embedded dynamically by Ansible
# still belong to the author of the module, and may assign their own license
# to the complete work.
#
# Copyright (c), Simon Dodsley <simon@everpuredata.com>,2026
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Arrays, their fleets, topology groups and tags, as every module reports them

Pure1 tells in two places which fleet an array is in, the member rows of the
fleet and the fleet reference on the array, and the two can differ. The same
holds for the topology group of an array. pure1_fleet_map() is the one rule
for both, pure1_tag_map() lists the tags of every array and
pure1_array_record() describes one array with what the two found.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import time

from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    pure1_attr,
    pure1_list,
    pure1_time,
)

# The product an array is, by the operating system Pure1 reports for it
ARRAY_TYPES = {
    "Purity//FA": "flasharray",
    "Purity": "flasharray",
    "Purity//FB": "flashblade",
    "Elasticity": "objectengine",
}


def _listing(module, action, request, what):
    """Return every item of a listing and warn when Pure1 gave fewer than it has"""
    listing = pure1_list(module, action, request)
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items), total, what
            )
        )
    return listing.items


def _left_out(module, what, count):
    """Warn once of the objects of a kind that could not be filed"""
    if count:
        module.warn("{0} are left out of the result: {1}.".format(what, count))


def _stamp(item):
    """Return the freshness stamp of a row as the two keys every result has"""
    as_of = pure1_attr(item, "as_of")
    return dict(as_of=pure1_time(as_of), as_of_epoch=as_of)


def _arrays_by_name(arrays):
    """Return the arrays by name and the number that have none of their own"""
    by_name = {}
    left_out = 0
    for array in arrays:
        if isinstance(array, dict):
            # As pure1_resolve_arrays() returns them: no field would be read
            raise TypeError(
                "the arrays of pure1_fleet_map() must be SDK models, as "
                "get_arrays() returns them, not dictionaries"
            )
        name = pure1_attr(array, "name")
        if name is None or name in by_name:
            left_out += 1
        else:
            by_name[name] = array
    return by_name, left_out


def _fleet(fleets, name, fleet_id):
    """Return the entry of a fleet, made of a reference when no listing had it"""
    if name not in fleets:
        fleets[name] = dict(
            id=fleet_id, as_of=None, as_of_epoch=None, coordinators=[], members={}
        )
    return fleets[name]


def _add_fleets(fleets, listed):
    """File the fleets of the listing and return the number left out"""
    left_out = 0
    for fleet in listed:
        name = pure1_attr(fleet, "name")
        if name is None or name in fleets:
            left_out += 1
            continue
        fleets[name] = dict(
            id=pure1_attr(fleet, "id"), coordinators=[], members={}, **_stamp(fleet)
        )
    return left_out


def _add_fleet_members(fleets, by_name, rows):
    """File the member rows, then the arrays that only their own record places

    Returns the fleets that the rows name for each member and the number of
    rows left out.
    """
    placed = {}
    left_out = 0
    for row in rows:
        fleet = pure1_attr(row, "fleet.name")
        name = pure1_attr(row, "member.name")
        kind = pure1_attr(row, "member.resource_type")
        if fleet is None or name is None or kind not in (None, "arrays"):
            left_out += 1
            continue
        members = _fleet(fleets, fleet, pure1_attr(row, "fleet.id"))["members"]
        if name in members:
            left_out += 1
            continue
        # The id of a FlashArray member is no Pure1 array id: only the name joins
        array = by_name.get(name)
        reported = pure1_attr(array, "fleet.name")
        led = [pure1_attr(ref, "name") for ref in pure1_attr(row, "coordinator_of", [])]
        members[name] = dict(
            status=pure1_attr(row, "status"),
            status_details=pure1_attr(row, "status_details"),
            coordinator=fleet in led,
            source="both" if reported == fleet else "members",
            member_id=pure1_attr(row, "member.id"),
            array_id=pure1_attr(array, "id"),
            reported_fleet=reported,
            **_stamp(row)
        )
        placed.setdefault(name, []).append(fleet)
    for name, array in by_name.items():
        reported = pure1_attr(array, "fleet.name")
        if reported is None or name in placed:
            continue
        members = _fleet(fleets, reported, pure1_attr(array, "fleet.id"))["members"]
        members[name] = dict(
            status=None,
            status_details=None,
            coordinator=None,
            source="arrays",
            member_id=None,
            array_id=pure1_attr(array, "id"),
            reported_fleet=reported,
            as_of=None,
            as_of_epoch=None,
        )
    return placed, left_out


def _mismatch(mismatches, array, kind, rows, reported):
    """List an array unless one row names what its own record names, or nothing does"""
    rows = sorted(rows)
    if rows != ([] if reported is None else [reported]):
        mismatches.append(
            dict(array=array, kind=kind, rows=rows, array_record=reported)
        )


def _place_in_fleets(fleets, by_name, placed, mismatches):
    """Return the fleet of every array and list the arrays whose sources differ"""
    arrays = {}
    for name, array in by_name.items():
        reported = pure1_attr(array, "fleet.name")
        rows = placed.get(name, [])
        # What the array record names too, then what is joined, then by name
        ranked = sorted(
            (
                fleet != reported,
                fleets[fleet]["members"][name]["status"] != "joined",
                fleet,
            )
            for fleet in rows
        )
        fleet = ranked[0][2] if ranked else reported
        member = {} if fleet is None else fleets[fleet]["members"][name]
        arrays[name] = dict(
            fleet=fleet,
            fleet_status=member.get("status"),
            fleet_source=member.get("source"),
            topology_group=None,
            topology_group_source=None,
            topology_fleet=None,
            topology_path=None,
        )
        _mismatch(mismatches, name, "fleet", rows, reported)
    for fleet in fleets.values():
        fleet["coordinators"] = sorted(
            name for name, member in fleet["members"].items() if member["coordinator"]
        )
    return arrays


def _group(groups, fleet, name, group_id):
    """Return the entry of a group, made of a reference when no listing had it"""
    entries = groups.setdefault(fleet, {})
    if name not in entries:
        entries[name] = dict(
            id=group_id,
            as_of=None,
            as_of_epoch=None,
            parent=None,
            path=[],
            children=[],
            arrays={},
            all_arrays=[],
        )
    return entries[name]


def _add_groups(fleets, groups, listed):
    """File the topology groups of the listing and return the number left out"""
    left_out = 0
    parents = []
    for group in listed:
        name = pure1_attr(group, "name")
        fleet = pure1_attr(group, "fleet.name")
        if name is None or fleet is None or name in groups.get(fleet, {}):
            left_out += 1
            continue
        _fleet(fleets, fleet, pure1_attr(group, "fleet.id"))
        entry = _group(groups, fleet, name, pure1_attr(group, "id"))
        entry.update(_stamp(group))
        entry["parent"] = pure1_attr(group, "parent_topology_group.name")
        parents.append(
            (fleet, entry["parent"], pure1_attr(group, "parent_topology_group.id"))
        )
    # A parent that the listing does not have is known by the reference alone
    for fleet, parent, parent_id in parents:
        if parent is not None:
            _group(groups, fleet, parent, parent_id)
    return left_out


def _add_group_members(fleets, groups, by_name, rows):
    """File the member rows of the topology groups: arrays and child groups

    Returns the groups that the rows name for each array, as (fleet, group),
    and the number of rows left out.
    """
    placed = {}
    left_out = 0
    for row in rows:
        fleet = pure1_attr(row, "fleet.name")
        group = pure1_attr(row, "topology_group.name")
        name = pure1_attr(row, "member.name")
        kind = pure1_attr(row, "member.resource_type")
        if None in (fleet, group, name) or kind not in (
            None,
            "arrays",
            "topology-groups",
        ):
            left_out += 1
            continue
        _fleet(fleets, fleet, pure1_attr(row, "fleet.id"))
        entry = _group(groups, fleet, group, pure1_attr(row, "topology_group.id"))
        if kind == "topology-groups":
            # The parent on the record of the group wins over the row
            child = _group(groups, fleet, name, pure1_attr(row, "member.id"))
            if child["parent"] is None:
                child["parent"] = group
            continue
        if name in entry["arrays"]:
            left_out += 1
            continue
        array = by_name.get(name)
        reported = pure1_attr(array, "topology_group.name")
        entry["arrays"][name] = dict(
            status=pure1_attr(row, "status"),
            status_details=pure1_attr(row, "status_details"),
            source="both" if reported == group else "members",
            member_id=pure1_attr(row, "member.id"),
            array_id=pure1_attr(array, "id"),
            **_stamp(row)
        )
        placed.setdefault(name, []).append((fleet, group))
    return placed, left_out


def _find_group(groups, name, group_id, fleet):
    """Return (fleet, group) of the group an array record names, None if unknown

    The record has no fleet for the group: the id finds it, else the name in
    the fleet of the array, else the name when one fleet alone has it.
    """
    every = [(key, group) for key in sorted(groups) for group in sorted(groups[key])]
    if group_id is not None:
        for key, group in every:
            if groups[key][group]["id"] == group_id:
                return key, group
    named = [place for place in every if place[1] == name]
    if (fleet, name) in named:
        return fleet, name
    return named[0] if len(named) == 1 else None


def _place_in_groups(groups, by_name, placed, arrays, mismatches):
    """Give every array its topology group and list the arrays whose sources differ"""
    for name, array in by_name.items():
        reported = pure1_attr(array, "topology_group.name")
        rows = placed.get(name, [])
        _mismatch(
            mismatches, name, "topology_group", [place[1] for place in rows], reported
        )
        if rows:
            # What the array record names too, then what is joined, then by name
            place = sorted(
                (
                    row[1] != reported,
                    groups[row[0]][row[1]]["arrays"][name]["status"] != "joined",
                    row,
                )
                for row in rows
            )[0][2]
        elif reported is None:
            continue
        else:
            place = _find_group(
                groups,
                reported,
                pure1_attr(array, "topology_group.id"),
                arrays[name]["fleet"],
            )
            if place is None:
                # Named on the array alone: there is no group to file it under
                arrays[name].update(
                    topology_group=reported, topology_group_source="arrays"
                )
                continue
            groups[place[0]][place[1]]["arrays"][name] = dict(
                status=None,
                status_details=None,
                source="arrays",
                member_id=None,
                array_id=pure1_attr(array, "id"),
                as_of=None,
                as_of_epoch=None,
            )
        arrays[name].update(
            topology_group=place[1],
            topology_group_source=groups[place[0]][place[1]]["arrays"][name]["source"],
            topology_fleet=place[0],
        )


def _cut_cycles(entries):
    """Take one parent out of every cycle of parents and return how many

    Followed upwards from each group in the order of the names, the parent
    that leads back to a group already passed is the one taken out.
    """
    cut = 0
    for name in sorted(entries):
        passed = [name]
        while entries[passed[-1]]["parent"] is not None:
            parent = entries[passed[-1]]["parent"]
            if parent in passed:
                entries[passed[-1]]["parent"] = None
                cut += 1
                break
            passed.append(parent)
    return cut


def _build_trees(groups, by_name, arrays):
    """Fill in path, children and all_arrays; return the number of parents cut"""
    cut = 0
    for entries in groups.values():
        cut += _cut_cycles(entries)
        for name, entry in entries.items():
            path = [name]
            while entries[path[0]]["parent"] is not None:
                path.insert(0, entries[path[0]]["parent"])
            entry["path"] = path
        for name, entry in entries.items():
            if entry["parent"] is not None:
                entries[entry["parent"]]["children"].append(name)
            # A member that no Pure1 array has the name of is in no selection
            known = [array for array in entry["arrays"] if array in by_name]
            for above in entry["path"]:
                entries[above]["all_arrays"].extend(known)
        for entry in entries.values():
            entry["children"].sort()
            entry["all_arrays"] = sorted(set(entry["all_arrays"]))
    for placement in arrays.values():
        if placement["topology_fleet"] is not None:
            group = groups[placement["topology_fleet"]][placement["topology_group"]]
            placement["topology_path"] = list(group["path"])
    return cut


def _sorted(mapping):
    """Return a dictionary with its keys in order"""
    return {key: mapping[key] for key in sorted(mapping)}


def pure1_fleet_map(module, pure_1, arrays=None, topology=True):
    """Return the fleet and the topology group of every array, by one rule

    Pure1 has two views of both and they can differ: a joined member whose
    array record names no fleet, and a fleet named on an array record that
    has no member row for it. The rule: the member rows first, the reference
    on the array record where no row names the array, both joined on the
    array NAME (the id of a FlashArray member is not a Pure1 array id). Every
    answer says which source it has, and an array whose sources differ is
    listed in mismatches. The caller decides what to make of a mismatch and
    of a member that is not joined: nothing is warned of or left out for it.

    arrays is the complete listing of get_arrays() as SDK models when the
    caller has it already, None to list them here. A member whose array is
    not in it counts as an array Pure1 does not know, so never give a
    listing that a filter narrowed, and the dictionaries that
    pure1_resolve_arrays() returns raise TypeError. With topology False the
    two topology listings are not requested.

    Requests, all unfiltered and through pure1_list(): get_arrays (unless
    arrays is given), get_fleets, get_fleets_members and, with topology,
    get_topology_groups and get_topology_groups_members. get_fleets needs
    py-pure-client 1.93.0 and the three after it 1.99.0: the module checks
    for them with pure1_require() before it calls this. A failed request
    fails the task with the line of the shared helpers.

    The result has four keys, each always there, every dictionary in the
    order of its keys:

    fleets: {fleet name: {id, as_of, as_of_epoch, coordinators, members}}.
    Every fleet of get_fleets, also one without members, and every fleet
    that only a row or an array record names (id of the reference, as_of
    None). coordinators are the names of the members that coordinate the
    fleet, in order. members: {array name: {status, status_details,
    coordinator, source, as_of, as_of_epoch, member_id, array_id,
    reported_fleet}}. source is "both" (a row, and the array record names
    this fleet), "members" (a row only) or "arrays" (the array record only:
    status, status_details, coordinator, as_of, as_of_epoch and member_id
    are None then). as_of is the freshness of the member row, a periodic
    snapshot that can be about an hour old, not that of the fleet. array_id
    is the id of the Pure1 array of that name, None when there is none: such
    a member is in no selection and not under arrays. reported_fleet is the
    fleet the array record names, None when it names none. Where a row and
    the array record name different fleets, the row wins and the array is no
    member of the fleet of its record.

    arrays: {array name: {fleet, fleet_status, fleet_source, topology_group,
    topology_group_source, topology_fleet, topology_path}} for every array
    Pure1 knows, all None for an array in no fleet and no group. fleet is a
    key of fleets, with the array among its members; fleet_status and
    fleet_source are status and source of that member. Rows in more than one
    fleet: the fleet of the array record, else a joined one, else the first
    by name. topology_group is the group the array is directly in, with the
    same sources and the same choice among several; topology_fleet and
    topology_group are its keys in topology_groups and topology_path the
    names from the root group down to it. With topology False, and for a
    group that only the array record names and no listing has,
    topology_group is the name on the array record, the source "arrays",
    and topology_fleet and topology_path are None.

    topology_groups: {fleet name: {group name: {id, as_of, as_of_epoch,
    parent, path, children, arrays, all_arrays}}}, empty with topology
    False. Every fleet name is a key of fleets. The tree is built here from
    the two listings: parent is the parent on the record of the group, else
    the group that a member row names it a child of; path the names from the
    root down to the group itself; children the names of its direct child
    groups; all_arrays the names of the arrays Pure1 knows in the group and
    in every group below it. arrays: {array name: {status, status_details,
    source, as_of, as_of_epoch, member_id, array_id}}, the arrays directly
    in the group, as the members of a fleet. A group that only a reference
    names has the id of the reference and as_of None. Where the parents of
    groups form a cycle, one parent of the cycle is given as None, with a
    warning.

    mismatches: [{array, kind, rows, array_record}] in the order of array
    and kind. kind is "fleet" or "topology_group", rows the names the member
    rows give for the array, in order (none: []), and array_record the name
    on the array record (None: none). An array is listed for a kind unless
    one row alone names what its record names, or neither names anything.
    Only arrays Pure1 knows are listed, and no topology group with topology
    False.

    Warnings: one for a listing of which Pure1 gave fewer items than it
    counted, and one per kind for what cannot be filed, with the number: an
    array or a fleet without a name or with the name of another one (arrays
    only when they are listed here), a group without a name or a fleet, a
    member row without its names, for a member of another kind or repeated.
    """

    def all_arrays(**page):
        return pure_1.get_arrays(**page)

    def all_fleets(**page):
        return pure_1.get_fleets(**page)

    def all_fleet_members(**page):
        return pure_1.get_fleets_members(**page)

    def all_groups(**page):
        return pure_1.get_topology_groups(**page)

    def all_group_members(**page):
        return pure_1.get_topology_groups_members(**page)

    listed = arrays is None
    if listed:
        arrays = _listing(module, "get_arrays", all_arrays, "arrays")
    by_name, left_out = _arrays_by_name(arrays)
    if listed:
        _left_out(
            module,
            "Arrays that Pure1 returned without a name or with the name of "
            "another array",
            left_out,
        )
    fleets = {}
    _left_out(
        module,
        "Fleets that Pure1 returned without a name or with the name of another fleet",
        _add_fleets(fleets, _listing(module, "get_fleets", all_fleets, "fleets")),
    )
    placed, left_out = _add_fleet_members(
        fleets,
        by_name,
        _listing(module, "get_fleets_members", all_fleet_members, "fleet member rows"),
    )
    _left_out(
        module,
        "Fleet member rows that Pure1 returned without a fleet or a member "
        "name, for a member that is no array or a second time for one member",
        left_out,
    )
    mismatches = []
    placements = _place_in_fleets(fleets, by_name, placed, mismatches)
    groups = {}
    if topology:
        _left_out(
            module,
            "Topology groups that Pure1 returned without a name or a fleet, or "
            "with the name of another group of the fleet,",
            _add_groups(
                fleets,
                groups,
                _listing(module, "get_topology_groups", all_groups, "topology groups"),
            ),
        )
        placed, left_out = _add_group_members(
            fleets,
            groups,
            by_name,
            _listing(
                module,
                "get_topology_groups_members",
                all_group_members,
                "topology group member rows",
            ),
        )
        _left_out(
            module,
            "Topology group member rows that Pure1 returned without a fleet, a "
            "group or a member name, for a member that is neither an array nor "
            "a group or a second time for one array",
            left_out,
        )
        _place_in_groups(groups, by_name, placed, placements, mismatches)
        _left_out(
            module,
            "Parents of topology groups that form a cycle",
            _build_trees(groups, by_name, placements),
        )
    else:
        for name, array in by_name.items():
            reported = pure1_attr(array, "topology_group.name")
            if reported is not None:
                placements[name].update(
                    topology_group=reported, topology_group_source="arrays"
                )
    for fleet in fleets.values():
        fleet["members"] = _sorted(fleet["members"])
    for entries in groups.values():
        for entry in entries.values():
            entry["arrays"] = _sorted(entry["arrays"])
    return dict(
        fleets=_sorted(fleets),
        arrays=_sorted(placements),
        topology_groups={fleet: _sorted(groups[fleet]) for fleet in sorted(groups)},
        mismatches=sorted(
            mismatches, key=lambda entry: (entry["array"], entry["kind"])
        ),
    )


def pure1_tag_map(module, pure_1):
    """Return the tags of every array as {array id: [{key, value, namespace}]}

    One unfiltered get_arrays_tags listing through pure1_list(), which Pure1
    answers with every tag at once: it takes neither a limit nor an offset
    on this listing. An array without tags has no key. The tags of
    an array are in the order of key, namespace and value; value and
    namespace are None where Pure1 sent none. A tag without a key or without
    the id of its array is left out, with one warning that counts them, and
    a listing of which Pure1 gave fewer tags than it counted gives one
    warning. A failed request fails the task with the line of the shared
    helpers.
    """

    def all_tags(**page):
        return pure_1.get_arrays_tags(**page)

    tags = {}
    left_out = 0
    for tag in _listing(module, "get_arrays_tags", all_tags, "array tags"):
        array_id = pure1_attr(tag, "resource.id")
        key = pure1_attr(tag, "key")
        if array_id is None or key is None:
            left_out += 1
            continue
        tags.setdefault(array_id, []).append(
            dict(
                key=key,
                value=pure1_attr(tag, "value"),
                namespace=pure1_attr(tag, "namespace"),
            )
        )
    for entries in tags.values():
        entries.sort(
            key=lambda tag: (tag["key"], tag["namespace"] or "", tag["value"] or "")
        )
    _left_out(
        module,
        "Array tags that Pure1 returned without a key or without the id of "
        "their array",
        left_out,
    )
    return tags


def pure1_array_record(array, tags=None, membership=None, now_ms=None):
    """Return an SDK Array as the dictionary every module returns for an array

    The keys, each always there and None where Pure1 sent no value: id,
    name, fqdn, os, type, model, version, fleet, fleet_source,
    topology_group, tags, as_of, as_of_epoch and age_seconds.

    type is "flasharray", "flashblade" or "objectengine", from os as
    ARRAY_TYPES has it, and None for any other os.

    tags is what pure1_tag_map() returned, the tags of every array by array
    id: the record gets a copy of the list of this array, [] when it has
    none or tags is None.

    membership is what pure1_fleet_map() returned. fleet, fleet_source and
    topology_group are then those of the array in it. Without it, and for
    an array it does not have, they are what the array record itself names:
    fleet_source is "arrays" where it names a fleet and None where not.

    as_of is the time of the data Pure1 has of the array ("YYYY-MM-DD
    HH:MM:SS UTC"), as_of_epoch the same in milliseconds since 1970, as
    Pure1 sent it, and age_seconds the whole seconds from then to now_ms,
    never below 0. now_ms is the time the module ran, in milliseconds since
    1970: give every array of one result the same. None takes the time of
    the call.
    """
    name = pure1_attr(array, "name")
    array_id = pure1_attr(array, "id")
    system = pure1_attr(array, "os")
    fleet = pure1_attr(array, "fleet.name")
    fleet_source = None if fleet is None else "arrays"
    topology_group = pure1_attr(array, "topology_group.name")
    placement = ((membership or {}).get("arrays") or {}).get(name)
    if placement is not None:
        fleet = placement["fleet"]
        fleet_source = placement["fleet_source"]
        topology_group = placement["topology_group"]
    as_of = pure1_attr(array, "as_of")
    age = None
    if as_of is not None:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        age = max(0, (now_ms - as_of) // 1000)
    return dict(
        id=array_id,
        name=name,
        fqdn=pure1_attr(array, "fqdn"),
        os=system,
        type=ARRAY_TYPES.get(system),
        model=pure1_attr(array, "model"),
        version=pure1_attr(array, "version"),
        fleet=fleet,
        fleet_source=fleet_source,
        topology_group=topology_group,
        tags=[dict(tag) for tag in (tags or {}).get(array_id, [])],
        as_of=pure1_time(as_of),
        as_of_epoch=as_of,
        age_seconds=age,
    )
