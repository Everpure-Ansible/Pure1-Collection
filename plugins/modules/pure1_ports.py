#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_ports
version_added: '1.3.0'
short_description: Collect FlashArray port information from Pure1
description:
  - Collect FlashArray port information from a Pure1
options:
  array:
    description:
      - Filter to provide only ports for a specifically named array
    type: str
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect all ports information
  everpure.pure1.pure1_ports:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: pure1_ports

- name: Collect only ports information for array X
  everpure.pure1.pure1_ports:
    array: X
  register: pure1_ports

- name: Show ports information
  ansible.builtin.debug:
    msg: "{{ pure1_ports['pure1_ports']['ports'] }}"
"""

RETURN = r"""
pure1_ports:
  description: Returns the ports information collected from Pure1
  returned: always
  type: dict
  contains:
    ports:
      description:
        - The ports of each array, as a dictionary keyed by array name.
        - The value for an array is a list with one dictionary per port. That
          dictionary has one key, the port name, and its value is a dictionary
          with the keys listed here.
        - Empty when Pure1 reports no ports.
      type: dict
      contains:
        iqn:
          description: iSCSI Qualified Name of an iSCSI port, null for any other port.
          type: str
        nqn:
          description: NVMe Qualified Name of an NVMe-oF port, null for any other port.
          type: str
        wwn:
          description: World Wide Name of a Fibre Channel port, null for any other port.
          type: str
        portal:
          description: IP address and port number of an iSCSI or NVMe-oF port on Ethernet, null for a Fibre Channel port.
          type: str
        failover:
          description: Name of the port that this port has failed over to, null when it has not failed over.
          type: str
  sample:
    ports:
      array1:
        - CT0.ETH4:
            iqn: "iqn.2010-06.com.purestorage:flasharray.1a2b3c4d5e6f7a8b"
            nqn: null
            wwn: null
            portal: "192.0.2.20:3260"
            failover: null
        - CT0.FC0:
            iqn: null
            nqn: null
            wwn: "52:4A:93:70:00:00:00:00"
            portal: null
            failover: null
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)


def generate_ports_dict(module, pure_1):
    ports_info = {}
    if module.params["array"]:
        array_filter = "arrays.name=" + filter_string(module.params["array"])
        ports = pure1_items(
            module, "get_ports", lambda: pure_1.get_ports(filter=array_filter)
        )
    else:

        def all_ports():
            return pure_1.get_ports()

        ports = pure1_items(module, "get_ports", all_ports)
    left_out = 0
    for port in range(0, len(ports)):
        arrays = getattr(ports[port], "arrays", None)
        array = getattr(arrays[0], "name", None) if arrays else None
        port_name = getattr(ports[port], "name", None)
        if array is None or port_name is None:
            left_out += 1
            continue
        port_details = {
            port_name: {
                "iqn": getattr(ports[port], "iqn", None),
                "nqn": getattr(ports[port], "nqn", None),
                "wwn": getattr(ports[port], "wwn", None),
                "portal": getattr(ports[port], "portal", None),
                "failover": getattr(ports[port], "failover", None),
            }
        }
        ports_info.setdefault(array, []).append(port_details)
    if left_out:
        module.warn(
            "Ports that Pure1 returned without a name or without an array "
            "are left out of the result: {0}".format(left_out)
        )
    return ports_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(array=dict(type="str")))
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    ports = {}

    ports["ports"] = generate_ports_dict(module, pure_1)

    module.exit_json(changed=False, pure1_ports=ports)


if __name__ == "__main__":
    main()
