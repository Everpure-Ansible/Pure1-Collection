#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_pods
version_added: '1.3.0'
short_description: Collect FlashArray pod information from Pure1
description:
  - Collect FlashArray pod information from Pure1, listed by array.
  - A stretched pod is listed under each of the arrays it is on.
options:
  array:
    description:
      - Filter to provide only pods for a specifically named array
      - A stretched pod of that array is listed under that array only
    type: str
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect all pod information
  everpure.pure1.pure1_pods:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: pure1_pods

- name: Show pods information
  ansible.builtin.debug:
    msg: "{{ pure1_pods['pure1_pods']['pods'] }}"

- name: Collect only pod information for array X
  everpure.pure1.pure1_pods:
    array: X
  register: array_pods

- name: Show pods information for array X
  ansible.builtin.debug:
    msg: "{{ array_pods['pure1_pods']['pods'] }}"
"""

RETURN = r"""
pure1_pods:
  description: Returns the pod information collected from Pure1
  returned: always
  type: dict
  contains:
    pods:
      description:
        - Dictionary keyed on array name. Each array holds a list with one
          dictionary per pod, keyed on the pod name.
        - A stretched pod is listed under each of its arrays, or only under
          the array named in I(array) when that option is set.
        - A pod has the keys below.
      type: dict
      contains:
        mediator:
          description: The mediator of the pod
          type: str
        source:
          description:
            - The name of the pod this pod was cloned from
            - An empty list when the pod has no source
          type: str
        arrays:
          description:
            - The names of all arrays the pod is on, also when I(array) is set
            - Added in version 1.6.0.
          type: list
          elements: str
        stretched:
          description:
            - Whether the pod is on more than one array
            - Added in version 1.6.0.
          type: bool
  sample:
    pods:
      array1:
        - pod1:
            mediator: purestorage
            source: []
            arrays:
              - array1
            stretched: false
        - pod2:
            mediator: purestorage
            source: pod1
            arrays:
              - array1
              - array2
            stretched: true
      array2:
        - pod2:
            mediator: purestorage
            source: pod1
            arrays:
              - array1
              - array2
            stretched: true
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
)


def generate_pods_dict(module, pure_1):
    pods_info = {}
    if module.params["array"]:
        array_filter = "arrays.name=" + filter_string(module.params["array"])
        pods = pure1_items(
            module, "get_pods", lambda: pure_1.get_pods(filter=array_filter)
        )
    else:

        def all_pods():
            return pure_1.get_pods()

        pods = pure1_items(module, "get_pods", all_pods)
    no_name = 0
    no_array = 0
    for pod in range(0, len(pods)):
        # A stretched pod is on two arrays and is listed under each of them
        arrays = []
        for array in getattr(pods[pod], "arrays", None) or []:
            name = getattr(array, "name", None)
            if name and name not in arrays:
                arrays.append(name)
        pod_arrays = arrays
        if module.params["array"]:
            requested = [
                array
                for array in arrays
                if array.lower() == module.params["array"].lower()
            ]
            arrays = requested or arrays
        pod_name = getattr(pods[pod], "name", None)
        # A pod is listed by its name under the name of an array
        if pod_name is None:
            no_name += 1
            continue
        if not arrays:
            no_array += 1
            continue
        pod_details = {
            pod_name: {
                "mediator": getattr(pods[pod], "mediator", None),
                "source": [],
                "arrays": pod_arrays,
                "stretched": len(pod_arrays) > 1,
            }
        }
        source = getattr(pods[pod], "source", None)
        if getattr(source, "name", None) is not None:
            pod_details[pod_name]["source"] = source.name
        for array in arrays:
            pods_info.setdefault(array, []).append(pod_details)
    left_out = []
    if no_name:
        left_out.append("%d without a name" % no_name)
    if no_array:
        left_out.append("%d without an array" % no_array)
    if left_out:
        module.warn("Pods left out of the result: %s" % ", ".join(left_out))
    return pods_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(dict(array=dict(type="str")))
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    pods = {}

    pods["pods"] = generate_pods_dict(module, pure_1)

    module.exit_json(changed=False, pure1_pods=pods)


if __name__ == "__main__":
    main()
