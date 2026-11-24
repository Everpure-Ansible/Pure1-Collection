#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2024, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_drives
version_added: '1.5.0'
short_description: Collect array drives information from Pure1
description:
  - Collect array drives information from a Pure1
options:
  array:
    description:
      - Filter to provide only drives for a specifically named FlashArray or
        Cloud Block Store array.
      - Pure1 reports no drives for a FlashBlade, and none for a name it does
        not know. The result is then empty and the module gives a warning.
    type: str
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect all drives information
  everpure.pure1.pure1_drives:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: pure1_drives

- name: Collect only drives information for array X
  everpure.pure1.pure1_drives:
    array: X
  register: pure1_drives

- name: Show drives information
  ansible.builtin.debug:
    msg: "{{ pure1_drives['pure1_drives']['drives'] }}"
"""

RETURN = r"""
pure1_drives:
  description: Returns array drives information collected from Pure1
  returned: always
  type: dict
  contains:
    drives:
      description:
        - The drives of each array, as a dictionary keyed by array name.
        - The value for an array is a list with one dictionary per drive. That
          dictionary has one key, the drive name, and its value is a dictionary
          with the keys listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when Pure1 reports no drives.
      type: dict
      contains:
        capacity:
          description: Physical storage capacity of the drive in bytes.
          type: int
        protocol:
          description: Storage protocol of the drive, for example C(NVMe) or C(SAS).
          type: str
        status:
          description: Current status of the drive, for example C(healthy) or C(failed).
          type: str
        type:
          description: Type of the drive, for example C(SSD), C(NVRAM) or C(cache).
          type: str
  sample:
    drives:
      array1:
        - CH0.BAY1:
            capacity: 1099511627776
            protocol: NVMe
            status: healthy
            type: SSD
        - CH0.NVB1:
            capacity: 4294967296
            protocol: NVMe
            status: healthy
            type: NVRAM
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)


def generate_drives_dict(module, pure_1):
    drives_info = {}
    if module.params["array"]:
        array_filter = "arrays.name=" + filter_string(module.params["array"])
        drives = pure1_items(
            module, "get_drives", lambda: pure_1.get_drives(filter=array_filter)
        )
        if not drives:
            module.warn(
                "No drives information available for array {0}".format(
                    module.params["array"]
                )
            )
    else:

        def all_drives():
            return pure_1.get_drives()

        drives = pure1_items(module, "get_drives", all_drives)
    left_out = 0
    for drive in range(0, len(drives)):
        arrays = getattr(drives[drive], "arrays", None)
        array = getattr(arrays[0], "name", None) if arrays else None
        drive_name = getattr(drives[drive], "name", None)
        if array is None or drive_name is None:
            left_out += 1
            continue
        drive_details = {
            drive_name: {
                "capacity": getattr(drives[drive], "capacity", None),
                "protocol": getattr(drives[drive], "protocol", None),
                "status": getattr(drives[drive], "status", None),
                "type": getattr(drives[drive], "type", None),
            }
        }
        drives_info.setdefault(array, []).append(drive_details)
    if left_out:
        module.warn(
            "Drives that Pure1 returned without a name or without an array "
            "are left out of the result: {0}".format(left_out)
        )
    return drives_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(array=dict(type="str")))
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    drives = {}

    drives["drives"] = generate_drives_dict(module, pure_1)

    module.exit_json(changed=False, pure1_drives=drives)


if __name__ == "__main__":
    main()
