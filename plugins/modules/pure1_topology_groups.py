#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_topology_groups
version_added: '1.6.0'
short_description: Collect the topology groups of fleets from Pure1
description:
  - Collect the topology groups of every fleet from Pure1 as a tree. Each
    group is returned with its parent, the path from its root group, its
    child groups, the arrays directly in it and the arrays in it and in every
    group below it. Each array that is in a group is returned with its fleet,
    its group and the path of that group.
  - Use it to run tasks on the arrays of a site or a zone, or to find where
    an array sits, without walking the tree in a playbook.
  - Pure1 refreshes the groups and their members from time to time and not
    when they change, so a change made on the arrays can take up to about an
    hour to show. The C(as_of) of a group and of an array in it is the time
    of the data.
options:
  name:
    description:
      - Report only the groups of these names and every group below them.
      - Names are matched without regard to case. A name that no group has
        selects nothing and is not an error.
      - A name that groups of several fleets have selects each of them. Add
        I(fleet) to select the group of one fleet.
      - An empty list counts as not set, and every group is reported.
      - Mutually exclusive with I(array). A task that gives both options
        fails, also when one of them is an empty list.
    type: list
    elements: str
  fleet:
    description:
      - Report only the groups of this fleet.
      - The name is matched without regard to case and the result has the
        name as Pure1 spells it.
      - The task fails when the name is empty, and when Pure1 does not know
        the fleet or knows more than one fleet of the name in another case
        and none as the task spells it.
      - A fleet without topology groups gives an empty result.
    type: str
  array:
    description:
      - Report only the group that each of these arrays is directly in, and
        the groups above that group.
      - The groups are reported whole, with all of their arrays, and
        C(arrays) holds the named arrays only. An array that is in no group
        gives an empty result.
      - Names are matched without regard to case and the result has the
        names as Pure1 spells them. The task fails when Pure1 does not know
        one of the arrays.
      - An empty list counts as not set, and every group is reported.
      - This option costs one more request to Pure1.
      - Mutually exclusive with I(name). A task that gives both options
        fails, also when one of them is an empty list.
    type: list
    elements: str
notes:
  - This module needs py-pure-client 1.99.0 or later. It is the first release
    with the Pure1 REST 1.7 methods C(get_topology_groups),
    C(get_topology_groups_members) and C(get_fleets_members). The module also
    uses C(get_fleets) of Pure1 REST 1.6, which py-pure-client 1.93.0 and
    later have.
  - Pure1 tells in two places which group an array is in, the member rows of
    the group and the group named on the array itself. The member rows count
    first and the array itself where no row names the array. The C(source) of
    an array says which of the two the result has.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the topology groups of every fleet
  everpure.pure1.pure1_topology_groups:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: topology

- name: Show the fleet and the path of every array that is in a group
  ansible.builtin.debug:
    msg: "{{ item.key }} is in {{ item.value.path | join(' / ') }} of {{ item.value.fleet }}"
  loop: "{{ topology.pure1_topology_groups.arrays | dict2items }}"

- name: Collect one group of one fleet and the groups below it
  everpure.pure1.pure1_topology_groups:
    fleet: fleet1
    name:
      - region1
  register: region

- name: Check that Pure1 has the group
  ansible.builtin.assert:
    that:
      - region.pure1_topology_groups.topology_groups | length > 0
    fail_msg: Pure1 has no topology group region1 in fleet1

- name: Check that every array of the region has joined its group
  ansible.builtin.assert:
    that:
      - item.value.arrays | dict2items | rejectattr('value.status', 'equalto', 'joined') | list | length == 0
    quiet: true
  loop: "{{ region.pure1_topology_groups.topology_groups['fleet1'] | default({}) | dict2items }}"

- name: Find the topology groups of two arrays
  everpure.pure1.pure1_topology_groups:
    array:
      - array1
      - array2
  register: placement

- name: Check that both arrays are in a topology group
  ansible.builtin.assert:
    that:
      - placement.pure1_topology_groups.arrays | length == 2

- name: Show every array of the group that array1 is in
  ansible.builtin.debug:
    msg: "{{ placement.pure1_topology_groups.topology_groups[where.fleet][where.topology_group].all_arrays }}"
  vars:
    where: "{{ placement.pure1_topology_groups.arrays['array1'] }}"
  when: "'array1' in placement.pure1_topology_groups.arrays"
"""

RETURN = r"""
pure1_topology_groups:
  description: The topology groups of the fleets and the group of each array.
  returned: always
  type: dict
  contains:
    topology_groups:
      description:
        - The topology groups, as a dictionary keyed by fleet name. The value
          for a fleet is a dictionary keyed by group name, and the value for
          a group is a dictionary with the keys listed here.
        - A fleet without a selected group has no key.
        - A key of a group or of an array is null when Pure1 reports no value
          for it.
        - Empty when Pure1 has no topology groups or the options select none.
      type: dict
      contains:
        id:
          description: The Pure1 id of the group.
          type: str
        as_of:
          description:
            - The time of the data Pure1 has of the group, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
            - Null for a group that Pure1 names only as the parent of another
              group or as the group of an array.
          type: str
        as_of_epoch:
          description: The same time in milliseconds since the epoch.
          type: int
        parent:
          description: The name of the parent group, null for a root group.
          type: str
        path:
          description:
            - The names of the groups from the root group down to the group
              itself.
            - It names the groups above also when I(name) left them out of
              the result.
          type: list
          elements: str
        children:
          description: The names of the groups directly below the group, in order.
          type: list
          elements: str
        arrays:
          description:
            - The arrays directly in the group, as a dictionary keyed by
              array name. The value for an array is a dictionary with the
              keys listed here.
            - Empty when no array is directly in the group.
          type: dict
          contains:
            id:
              description:
                - The Pure1 id of the array.
                - Null when Pure1 has no array of the name of the member.
              type: str
            member_id:
              description:
                - The id that the member row gives the array, which need not
                  be the Pure1 id of the array.
                - Null when C(source) is C(arrays).
              type: str
            status:
              description:
                - The status of the membership, one of C(joining), C(joined)
                  and C(removing).
                - Null when C(source) is C(arrays).
              type: str
            status_details:
              description:
                - More about the status, an empty text when Pure1 has nothing
                  to add.
                - Null when C(source) is C(arrays).
              type: str
            source:
              description:
                - Where Pure1 names the group for the array.
                - C(both) is a member row of the group and the array itself,
                  C(members) a member row only and C(arrays) the array
                  itself only.
              type: str
            as_of:
              description:
                - The time of the data Pure1 has of the member row, in UTC,
                  as C(YYYY-MM-DD HH:MM:SS UTC). The status is as of this
                  time, which is not the time the array joined the group.
                - Null when C(source) is C(arrays).
              type: str
            as_of_epoch:
              description: The same time in milliseconds since the epoch.
              type: int
        all_arrays:
          description:
            - The names of the arrays in the group and in every group below
              it, in order.
            - Only arrays that Pure1 knows by that name are listed.
          type: list
          elements: str
    arrays:
      description:
        - The group of each array, as a dictionary keyed by array name. The
          value for an array is a dictionary with the keys listed here.
        - It holds the arrays Pure1 knows that are directly in a group of the
          result, and with I(array) only the named ones.
        - An array that member rows place in more than one group is listed
          with one of them. That is the group the array itself names, else
          one it has joined, else the first by name. It is not listed when
          that group is not in the result, also when another group of the
          result holds it.
        - An array that names a group which Pure1 does not list is left out,
          with a warning that counts such arrays. With I(fleet) the warning
          counts the arrays of that fleet and the arrays in no fleet.
        - Empty when no such array is in a group.
      type: dict
      contains:
        fleet:
          description: The name of the fleet of the group, a key of C(topology_groups).
          type: str
        topology_group:
          description: The name of the group the array is directly in.
          type: str
        path:
          description: The names of the groups from the root group down to that group.
          type: list
          elements: str
        source:
          description:
            - Where Pure1 names the group for the array, as for the arrays
              of a group. One of C(both), C(members) and C(arrays).
          type: str
  sample:
    topology_groups:
      fleet1:
        region1:
          id: 5b0b4b6c-7f0e-4e0a-9d59-3c6a3d1f2e10
          as_of: "2021-02-03 13:04:05 UTC"
          as_of_epoch: 1612357445000
          parent: null
          path:
            - region1
          children:
            - site1
          arrays: {}
          all_arrays:
            - array1
            - array2
        site1:
          id: 9a1c2d3e-4f50-4a6b-8c7d-0e1f2a3b4c5d
          as_of: "2021-02-03 13:04:05 UTC"
          as_of_epoch: 1612357445000
          parent: region1
          path:
            - region1
            - site1
          children: []
          arrays:
            array1:
              id: 1d2e3f40-5a6b-4c7d-8e9f-a0b1c2d3e4f5
              member_id: 1d2e3f40-5a6b-4c7d-8e9f-a0b1c2d3e4f5
              status: joined
              status_details: ""
              source: both
              as_of: "2021-02-03 13:04:05 UTC"
              as_of_epoch: 1612357445000
            array2:
              id: 2e3f4051-6b7c-4d8e-9fa0-b1c2d3e4f506
              member_id: 2e3f4051-6b7c-4d8e-9fa0-b1c2d3e4f506
              status: joined
              status_details: ""
              source: members
              as_of: "2021-02-03 13:04:05 UTC"
              as_of_epoch: 1612357445000
          all_arrays:
            - array1
            - array2
    arrays:
      array1:
        fleet: fleet1
        topology_group: site1
        path:
          - region1
          - site1
        source: both
      array2:
        fleet: fleet1
        topology_group: site1
        path:
          - region1
          - site1
        source: members
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.fleets import (
    pure1_fleet_map,
)
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
)

# The client methods behind the result and the first py-pure-client with each.
# The one the module is named after comes first, for the message of an old SDK.
REQUIRED_METHODS = (
    ("get_topology_groups", "1.99.0"),
    ("get_topology_groups_members", "1.99.0"),
    ("get_fleets_members", "1.99.0"),
    ("get_fleets", "1.93.0"),
)


def list_arrays(module, pure_1):
    """Return every array of Pure1, which the fleet map joins the members to"""

    def all_arrays(**page):
        return pure_1.get_arrays(**page)

    listing = pure1_list(module, "get_arrays", all_arrays)
    if listing.truncated:
        module.warn(
            "Pure1 returned {0} of {1} arrays: the result may be incomplete.".format(
                len(listing.items),
                "an unknown number of" if listing.total is None else listing.total,
            )
        )
    names = [pure1_attr(array, "name") for array in listing.items]
    left_out = len(names) - len(set(names).difference([None]))
    if left_out:
        module.warn(
            "Arrays that Pure1 returned without a name or with the name of "
            "another array are left out of the result: {0}.".format(left_out)
        )
    return listing.items


def resolve_fleet(module, fleets, name):
    """Return the fleet of the task as Pure1 spells it, None when it names none"""
    if name is None:
        return None
    # The spelling of the task wins over the same name in another case
    found = [fleet for fleet in fleets if fleet == name] or [
        fleet for fleet in fleets if fleet.lower() == name.lower()
    ]
    if len(found) > 1:
        module.fail_json(
            msg="Pure1 knows more than one fleet whose name is {0} in another "
            "case: {1}. Give the name as Pure1 spells it.".format(
                name, ", ".join(found)
            )
        )
    if not found:
        module.fail_json(msg="Fleet {0} is not known to Pure1.".format(name))
    return found[0]


def select_groups(groups, names, arrays):
    """Return the selected groups as a set of (fleet name, group name)

    names selects the groups of these names and every group below them,
    arrays the groups that hold one of these arrays and every group above
    them, and neither of them every group.
    """
    wanted = set(name.lower() for name in names or [])
    selected = set()
    for fleet, entries in groups.items():
        for group, entry in entries.items():
            if arrays is not None:
                if any(array in entry["arrays"] for array in arrays):
                    selected.update((fleet, above) for above in entry["path"])
            elif not wanted or wanted.intersection(
                above.lower() for above in entry["path"]
            ):
                selected.add((fleet, group))
    return selected


def group_record(entry):
    """Return a group of the fleet map as the module reports it"""
    return dict(
        id=entry["id"],
        as_of=entry["as_of"],
        as_of_epoch=entry["as_of_epoch"],
        parent=entry["parent"],
        path=entry["path"],
        children=entry["children"],
        arrays={
            name: dict(
                id=member["array_id"],
                member_id=member["member_id"],
                status=member["status"],
                status_details=member["status_details"],
                source=member["source"],
                as_of=member["as_of"],
                as_of_epoch=member["as_of_epoch"],
            )
            for name, member in entry["arrays"].items()
        },
        all_arrays=entry["all_arrays"],
    )


def generate_topology_groups_dict(module, pure_1):
    fleet = module.params["fleet"]
    if fleet is not None and not fleet.strip():
        module.fail_json(msg="The name of a fleet must not be empty.")
    resolved = pure1_resolve_arrays(module, pure_1, module.params["array"])
    arrays = None if resolved is None else [array["name"] for array in resolved]
    # Every listing whole: Pure1 fails a request that names an array or a
    # group it has no row for, so the selection is made here
    membership = pure1_fleet_map(module, pure_1, arrays=list_arrays(module, pure_1))
    fleet = resolve_fleet(module, membership["fleets"], fleet)
    groups = membership["topology_groups"]
    if fleet is not None:
        groups = {fleet: groups[fleet]} if fleet in groups else {}
    selected = select_groups(groups, module.params["name"], arrays)
    result = dict(topology_groups={}, arrays={})
    for fleet_name, group in sorted(selected):
        result["topology_groups"].setdefault(fleet_name, {})[group] = group_record(
            groups[fleet_name][group]
        )
    left_out = 0
    for name, placement in membership["arrays"].items():
        if placement["topology_group"] is None:
            continue
        if arrays is not None and name not in arrays:
            continue
        place = (placement["topology_fleet"], placement["topology_group"])
        if place[0] is None:
            # Named on the array alone: no listing has a group to file it
            # under. An array of another fleet is no loss to the fleet asked for
            if fleet is None or placement["fleet"] in (None, fleet):
                left_out += 1
        elif place in selected:
            result["arrays"][name] = dict(
                fleet=place[0],
                topology_group=place[1],
                path=placement["topology_path"],
                source=placement["topology_group_source"],
            )
    if left_out:
        module.warn(
            "Arrays that name a topology group which Pure1 does not list "
            "are left out of the result: {0}.".format(left_out)
        )
    return result


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="list", elements="str"),
            fleet=dict(type="str"),
            array=dict(type="list", elements="str"),
        )
    )
    module = AnsibleModule(
        argument_spec,
        mutually_exclusive=[["name", "array"]],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)
    for method, minimum in REQUIRED_METHODS:
        pure1_require(module, pure_1, method, minimum)

    module.exit_json(
        changed=False,
        pure1_topology_groups=generate_topology_groups_dict(module, pure_1),
    )


if __name__ == "__main__":
    main()
