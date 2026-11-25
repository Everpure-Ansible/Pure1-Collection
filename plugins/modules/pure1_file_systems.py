#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_file_systems
version_added: '1.6.0'
short_description: Collect file systems information from Pure1
description:
  - Collect the file systems of FlashBlade and FlashArray arrays from Pure1,
    by array and by file system name, and on request the managed directories
    of each file system.
  - It is an inventory, with the protocol settings and the NFS export rules
    of the FlashBlade file systems and the Pure1 ID of every object. Pure1
    has no protocol or export data for a FlashArray file system or its
    managed directories, so the module cannot tell what a FlashArray exports.
  - Pure1 holds what each array last reported, so the data is not live.
    C(as_of) of each file system is the time of its data.
options:
  array:
    description:
      - Names of the arrays to return the file systems of. The file systems
        of all arrays are returned when it is not set or empty.
      - A name is matched without regard to case, and the result has the
        name as Pure1 spells it.
      - The task fails when Pure1 does not know one of the names.
    type: list
    elements: str
  name:
    description:
      - Names of the file systems to return, on whichever array they are.
        All file systems are returned when it is not set or empty.
      - A name is matched without regard to case. A name that matches no
        file system is not an error.
      - Give the whole name as Pure1 has it. A FlashArray file system in a
        pod is named C(pod-1::fs1).
    type: list
    elements: str
  destroyed:
    description:
      - With C(true) only destroyed file systems are returned, which wait to
        be eradicated, and with C(false) only file systems that are not
        destroyed. Both are returned when it is not set.
      - It selects file systems, not managed directories. With I(directories)
        every managed directory of a returned file system is listed, each
        with its own C(destroyed).
    type: bool
  directories:
    description:
      - Also return the managed directories of each file system, as the list
        C(directories) of the file system.
      - Pure1 has managed directories for FlashArray file systems only. The
        list of a FlashBlade file system is empty.
      - This costs one more request to Pure1, which is not made when no file
        system is returned.
    type: bool
    default: false
notes:
  - The module makes one request to Pure1 for every 1,000 file systems, one
    more when I(array) is set, and with I(directories) one more for every
    1,000 managed directories unless no file system is returned.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the file systems of all arrays
  everpure.pure1.pure1_file_systems:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: all_file_systems

- name: Show the names of the file systems of each array
  ansible.builtin.debug:
    msg: "{{ item.key }} has {{ item.value | list }}"
  loop: "{{ all_file_systems['pure1_file_systems']['file_systems'] | dict2items }}"

# NFS rules can be set while NFS is disabled, so an export check reads both
- name: Show the names of the file systems that are exported over NFS
  ansible.builtin.debug:
    msg: >-
      {{ all_file_systems['pure1_file_systems']['file_systems'].values()
         | map('dict2items') | flatten | selectattr('value.nfs.enabled')
         | selectattr('value.nfs.rules') | map(attribute='key') | list }}

- name: Collect the file systems of one array with their managed directories
  everpure.pure1.pure1_file_systems:
    array:
      - array1
    directories: true
  register: with_directories

- name: Show the paths of the managed directories of each file system
  ansible.builtin.debug:
    msg: "{{ item.key }} has {{ item.value.directories | map(attribute='path') | list }}"
  # an array without a file system is not in the result
  loop: "{{ with_directories['pure1_file_systems']['file_systems']['array1'] | default({}) | dict2items }}"

- name: Collect two file systems by name, unless they are destroyed
  everpure.pure1.pure1_file_systems:
    name:
      - fs1
      - pod-1::fs2
    destroyed: false
  register: named_file_systems

- name: Show the arrays that have one of the two file systems
  ansible.builtin.debug:
    msg: "{{ named_file_systems['pure1_file_systems']['file_systems'] | list }}"

- name: Collect the destroyed file systems of one array
  everpure.pure1.pure1_file_systems:
    array:
      - array1
    destroyed: true
  register: destroyed_file_systems

- name: Check that no file system of the array waits to be eradicated
  ansible.builtin.assert:
    that:
      - destroyed_file_systems['pure1_file_systems']['file_systems'] | length == 0
"""

RETURN = r"""
pure1_file_systems:
  description: Returns the file systems information collected from Pure1
  returned: always
  type: dict
  contains:
    file_systems:
      description:
        - The file systems of each array, as a dictionary keyed by array name.
          The value for an array is a dictionary keyed by file system name,
          and the value for a file system is a dictionary with the keys
          listed here.
        - An array is present when it has a file system that matches the
          options, so an array named in I(array) can be missing. Empty when
          no file system matches.
        - A file system that Pure1 reports on several arrays is listed under
          each of them, or with I(array) under each of them that is named
          there.
        - A key is null when Pure1 reports no value for it. Pure1 reports no
          C(provisioned), no C(hard_limit_enabled), no
          C(fast_remove_directory_enabled), no C(snapshot_directory_enabled)
          and no protocol data for a FlashArray file system.
        - Pure1 can list two file systems of one array under the same name.
          The one created last is then the value for the name, and the
          others are in its C(same_name).
      type: dict
      contains:
        id:
          description: The globally unique Pure1 ID of the file system.
          type: str
        created:
          description: Time the file system was created, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: Time the file system was created, in milliseconds since the epoch.
          type: int
        destroyed:
          description: Whether the file system is destroyed and waits to be eradicated.
          type: bool
        as_of:
          description: Time of the data Pure1 holds for the file system, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: Time of the data Pure1 holds for the file system, in milliseconds since the epoch.
          type: int
        provisioned:
          description:
            - Provisioned size of a FlashBlade file system in bytes. C(0) means unlimited.
          type: int
        hard_limit_enabled:
          description: Whether the provisioned size of a FlashBlade file system is a hard limit.
          type: bool
        fast_remove_directory_enabled:
          description: Whether fast remove directory is enabled on a FlashBlade file system.
          type: bool
        snapshot_directory_enabled:
          description: Whether the snapshot directory is enabled on a FlashBlade file system.
          type: bool
        nfs:
          description: NFS settings of a FlashBlade file system. Both keys are null for a FlashArray file system.
          type: dict
          contains:
            enabled:
              description: Whether NFS is enabled.
              type: bool
            rules:
              description:
                - The NFS export rules, as the text set on the FlashBlade. Can be an empty string.
                - Rules can be set while C(enabled) is false, so a check of what is exported reads both keys.
              type: str
        smb:
          description: SMB settings of a FlashBlade file system. The key is null for a FlashArray file system.
          type: dict
          contains:
            enabled:
              description: Whether SMB is enabled.
              type: bool
        http:
          description: HTTP settings of a FlashBlade file system. The key is null for a FlashArray file system.
          type: dict
          contains:
            enabled:
              description: Whether HTTP is enabled.
              type: bool
        directories:
          description:
            - The managed directories of the file system, sorted by name. Empty for a file system without one.
          returned: when I(directories) is true
          type: list
          elements: dict
          contains:
            id:
              description: The globally unique Pure1 ID of the managed directory.
              type: str
            name:
              description: Full name of the managed directory, the file system name and C(directory_name) joined by a colon.
              type: str
            directory_name:
              description: Name of the managed directory without the file system name.
              type: str
            path:
              description: Absolute path of the managed directory in the file system.
              type: str
            created:
              description: Time the managed directory was created, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            created_epoch:
              description: Time the managed directory was created, in milliseconds since the epoch.
              type: int
            destroyed:
              description: Whether the managed directory is destroyed and waits to be eradicated.
              type: bool
        same_name:
          description:
            - The other file systems that Pure1 lists for the array under the same name, the one created last
              first. Each is a dictionary with the keys listed here except C(same_name), with its own C(directories).
            - Empty when Pure1 lists one file system of the array under the name, which is the usual case.
          type: list
          elements: dict
  sample:
    file_systems:
      array1:
        fs1:
          id: 2a7f5c1e-8d3b-4f6a-9c0e-1b2d3f4a5b6c
          created: "2021-02-03 13:04:05 UTC"
          created_epoch: 1612357445000
          destroyed: false
          as_of: "2026-01-02 03:04:05 UTC"
          as_of_epoch: 1767323045000
          provisioned: 1099511627776
          hard_limit_enabled: true
          fast_remove_directory_enabled: false
          snapshot_directory_enabled: true
          nfs:
            enabled: true
            rules: "*(rw,no_root_squash)"
          smb:
            enabled: false
          http:
            enabled: false
          directories: []
          same_name: []
      array2:
        "pod-1::fs2":
          id: 7c9e6679-7425-40de-944b-e07fc1f90ae7
          created: "2021-02-03 13:04:05 UTC"
          created_epoch: 1612357445000
          destroyed: false
          as_of: "2026-01-02 03:04:05 UTC"
          as_of_epoch: 1767323045000
          provisioned: null
          hard_limit_enabled: null
          fast_remove_directory_enabled: null
          snapshot_directory_enabled: null
          nfs:
            enabled: null
            rules: null
          smb:
            enabled: null
          http:
            enabled: null
          directories:
            - id: 0f8fad5b-d9cb-469f-a165-70867728950e
              name: "pod-1::fs2:root"
              directory_name: root
              path: /
              created: "2021-02-03 13:04:05 UTC"
              created_epoch: 1612357445000
              destroyed: false
          same_name: []
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)


def warn_if_truncated(module, listing, kind):
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} {2}: the result may be incomplete.".format(
                len(listing.items), total, kind
            )
        )


def file_system_row(file_system):
    created = pure1_attr(file_system, "created")
    as_of = pure1_attr(file_system, "as_of")
    return {
        "id": pure1_attr(file_system, "id"),
        "created": pure1_time(created),
        "created_epoch": created,
        "destroyed": pure1_attr(file_system, "destroyed"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "provisioned": pure1_attr(file_system, "provisioned"),
        "hard_limit_enabled": pure1_attr(file_system, "hard_limit_enabled"),
        "fast_remove_directory_enabled": pure1_attr(
            file_system, "fast_remove_directory_enabled"
        ),
        "snapshot_directory_enabled": pure1_attr(
            file_system, "snapshot_directory_enabled"
        ),
        "nfs": {
            "enabled": pure1_attr(file_system, "nfs.enabled"),
            "rules": pure1_attr(file_system, "nfs.rules"),
        },
        "smb": {"enabled": pure1_attr(file_system, "smb.enabled")},
        "http": {"enabled": pure1_attr(file_system, "http.enabled")},
    }


def directory_row(directory):
    created = pure1_attr(directory, "created")
    return {
        "id": pure1_attr(directory, "id"),
        "name": pure1_attr(directory, "name"),
        "directory_name": pure1_attr(directory, "directory_name"),
        "path": pure1_attr(directory, "path"),
        "created": pure1_time(created),
        "created_epoch": created,
        "destroyed": pure1_attr(directory, "destroyed"),
    }


def array_names(item, selected):
    """Return the names of the arrays an item is on, those of the task if it names any"""
    names = []
    for array in pure1_attr(item, "arrays", []):
        name = pure1_attr(array, "name")
        if name is None or name in names:
            continue
        if selected is None or name.lower() in selected:
            names.append(name)
    return names


def created_last(row):
    """Return the sort key of a file system row: the one created last is the highest"""
    created = row["created_epoch"]
    return (-1 if created is None else created, row["id"] or "")


def add_directories(module, pure_1, array_filter, rows, listed):
    """Give each file system row the list of its managed directories

    rows are the rows of the result by file system ID, those under same_name
    as well, and listed the IDs of all file systems Pure1 listed.
    """
    directory_filter = filter_and(
        array_filter, filter_in("file_system.name", module.params["name"])
    )

    def list_directories(**page):
        return pure_1.get_directories(filter=directory_filter, **page)

    listing = pure1_list(module, "get_directories", list_directories)
    warn_if_truncated(module, listing, "managed directories")
    left_out = 0
    joined = set()
    for directory in listing.items:
        file_system_id = pure1_attr(directory, "file_system.id")
        if file_system_id in rows:
            row = directory_row(directory)
            # A listing that moved while it was read can give a directory twice
            if row["id"] is None or (file_system_id, row["id"]) not in joined:
                joined.add((file_system_id, row["id"]))
                rows[file_system_id]["directories"].append(row)
        elif file_system_id is None or (
            # A selection of destroyed leaves out file systems that have directories
            module.params["destroyed"] is None
            and file_system_id not in listed
        ):
            left_out += 1
    for row in rows.values():
        row["directories"].sort(
            key=lambda directory: (directory["name"] or "", directory["id"] or "")
        )
    if left_out:
        module.warn(
            "Pure1 returned {0} managed directories whose file system it did "
            "not return: they are left out of the result.".format(left_out)
        )


def generate_file_systems_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    selected = None
    if arrays:
        selected = set(array["name"].lower() for array in arrays)
    array_filter = filter_in(
        "arrays.name", [array["name"] for array in arrays] if arrays else None
    )
    destroyed = module.params["destroyed"]
    file_system_filter = filter_and(
        array_filter,
        filter_in("name", module.params["name"]),
        None if destroyed is None else "destroyed={0}".format(str(destroyed).lower()),
    )

    def list_file_systems(**page):
        return pure_1.get_file_systems(filter=file_system_filter, **page)

    listing = pure1_list(module, "get_file_systems", list_file_systems)
    warn_if_truncated(module, listing, "file systems")

    # The rows of each array by name: Pure1 can list two file systems of one
    # array under the same name
    found = {}
    listed = set()
    left_out = 0
    for file_system in listing.items:
        row = file_system_row(file_system)
        if row["id"] is not None:
            listed.add(row["id"])
        name = pure1_attr(file_system, "name")
        on_arrays = array_names(file_system, selected)
        if name is None or not on_arrays:
            left_out += 1
            continue
        if module.params["directories"]:
            row["directories"] = []
        for array in on_arrays:
            under_name = found.setdefault(array, {}).setdefault(name, [])
            # A listing that moved while it was read can give a file system twice
            if row["id"] is None or all(
                row["id"] != other["id"] for other in under_name
            ):
                under_name.append(row)
    if left_out:
        module.warn(
            "Pure1 returned {0} file systems without a name or without an "
            "array: they are left out of the result.".format(left_out)
        )
    file_systems = {}
    rows = {}
    for array, names in found.items():
        file_systems[array] = {}
        for name, under_name in names.items():
            under_name.sort(key=created_last, reverse=True)
            # A copy: a file system on two arrays has one row, and its
            # same_name is that of one array
            file_systems[array][name] = dict(under_name[0], same_name=under_name[1:])
            for row in under_name:
                if row["id"] is not None:
                    rows[row["id"]] = row
    # Without a file system in the result no row can take a directory
    if module.params["directories"] and file_systems:
        add_directories(module, pure_1, array_filter, rows, listed)
    return file_systems


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            name=dict(type="list", elements="str"),
            destroyed=dict(type="bool"),
            directories=dict(type="bool", default=False),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    file_systems = {"file_systems": generate_file_systems_dict(module, pure_1)}

    module.exit_json(changed=False, pure1_file_systems=file_systems)


if __name__ == "__main__":
    main()
