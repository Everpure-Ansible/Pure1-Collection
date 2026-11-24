#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_nics
version_added: '1.3.0'
short_description: Collect network interface information from Pure1
description:
  - Collect network interface information from a Pure1
options:
  array:
    description:
      - Filter to provide only network interfaces for a specifically named array or blade
    type: str
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect all network interface information
  everpure.pure1.pure1_nics:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: pure1_nics

- name: Collect only network interface information for array X
  everpure.pure1.pure1_nics:
    array: X
  register: pure1_nics

- name: Show network interface information
  ansible.builtin.debug:
    msg: "{{ pure1_nics['pure1_nics']['nics'] }}"
"""

RETURN = r"""
pure1_nics:
  description: Returns the network interface information collected from Pure1
  returned: always
  type: dict
  contains:
    nics:
      description:
        - The network interfaces of each array, as a dictionary keyed by array name.
        - The value for an array is a list with one dictionary per network
          interface. That dictionary has one key, the interface name, and its
          value is a dictionary with the keys listed here.
        - Empty when Pure1 reports no network interfaces.
      type: dict
      contains:
        address:
          description: IP address of the interface, null when Pure1 reports none.
          type: str
        gateway:
          description: Gateway of the interface, null when Pure1 reports none.
          type: str
        hwaddr:
          description: Hardware address of the interface, null when Pure1 reports none.
          type: str
        netmask:
          description: Netmask of the interface, null when Pure1 reports none.
          type: str
        mtu:
          description: Maximum transmission unit of the interface, null when Pure1 reports none.
          type: int
        speed:
          description:
            - Speed of the interface, rounded to whole gigabits per second.
            - It is 0 both for a reported speed of 0 and when Pure1 reports no speed,
              and also for a reported speed below half a gigabit per second.
              C(speed_bps) tells them apart.
          type: int
        speed_bps:
          description:
            - Speed of the interface in bits per second as Pure1 reports it, null
              when Pure1 reports no speed.
            - Added in version 1.6.0.
          type: int
        enabled:
          description: Whether the interface is enabled, null when Pure1 does not say.
          type: bool
        services:
          description:
            - Services that are enabled on the interface, for example
              C(management) or C(iscsi). Empty when Pure1 reports none.
          type: list
          elements: str
        subinterfaces:
          description: Names of the subinterfaces of the interface. Empty when Pure1 reports none.
          type: list
          elements: str
  sample:
    nics:
      array1:
        - ct0.eth0:
            address: 192.0.2.10
            gateway: 192.0.2.1
            hwaddr: "00:00:5e:00:53:10"
            netmask: 255.255.255.0
            mtu: 1500
            speed: 1
            speed_bps: 1000000000
            enabled: true
            services:
              - management
            subinterfaces: []
        - ct0.eth4:
            address: 192.0.2.20
            gateway: 192.0.2.1
            hwaddr: "00:00:5e:00:53:14"
            netmask: 255.255.255.0
            mtu: 9000
            speed: 25
            speed_bps: 25000000000
            enabled: true
            services:
              - iscsi
            subinterfaces: []
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)


def generate_nics_dict(module, pure_1):
    nics_info = {}
    if module.params["array"]:
        array_filter = "arrays.name=" + filter_string(module.params["array"])
        nics = pure1_items(
            module,
            "get_network_interfaces",
            lambda: pure_1.get_network_interfaces(filter=array_filter),
        )
    else:

        def all_nics():
            return pure_1.get_network_interfaces()

        nics = pure1_items(module, "get_network_interfaces", all_nics)
    left_out = 0
    for nic in range(0, len(nics)):
        arrays = getattr(nics[nic], "arrays", None)
        array = getattr(arrays[0], "name", None) if arrays else None
        nic_name = getattr(nics[nic], "name", None)
        if array is None or nic_name is None:
            left_out += 1
            continue
        nic_details = {
            nic_name: {
                "address": getattr(nics[nic], "address", None),
                "gateway": getattr(nics[nic], "gateway", None),
                "hwaddr": getattr(nics[nic], "hwaddr", None),
                "netmask": getattr(nics[nic], "netmask", None),
                "mtu": getattr(nics[nic], "mtu", None),
                "speed": round(getattr(nics[nic], "speed", 0) / 1000000000),
                "speed_bps": getattr(nics[nic], "speed", None),
                "enabled": getattr(nics[nic], "enabled", None),
                "services": [],
                "subinterfaces": [],
            }
        }
        if getattr(nics[nic], "services", None):
            nic_details[nic_name]["services"] = nics[nic].services
        if getattr(nics[nic], "subinterfaces", None):
            nic_details[nic_name]["subinterfaces"] = nics[nic].subinterfaces
        nics_info.setdefault(array, []).append(nic_details)
    if left_out:
        module.warn(
            "Network interfaces that Pure1 returned without a name or without "
            "an array are left out of the result: {0}".format(left_out)
        )
    return nics_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(array=dict(type="str")))
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    nics = {}

    nics["nics"] = generate_nics_dict(module, pure_1)

    module.exit_json(changed=False, pure1_nics=nics)


if __name__ == "__main__":
    main()
