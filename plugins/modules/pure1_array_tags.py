#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

ANSIBLE_METADATA = {
    "metadata_version": "1.1",
    "status": ["preview"],
    "supported_by": "community",
}

DOCUMENTATION = r"""
---
module: pure1_array_tags
version_added: '1.0.0'
short_description:  Manage array tags in Pure1
description:
- Manage tags for arrays on Pure1.
author:
- Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
options:
  name:
    description:
    - The name of the array.
    type: str
    required: true
  tag:
    description:
    - List of key value pairs to assign to the array.
    - Seperate the key from the value using a colon (:) only.
    - See examples for exact formatting requirements
    type: list
    elements: str
    required: true
  state:
    description:
    - Define whether the array tag(s) should exist or not.
    default: present
    choices: [ absent, present ]
    type: str
extends_documentation_fragment:
- everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Create new tags for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'key1:value1'
      - 'key2:value2'
    app_id: 'pure1:apikey:P3nkAt46lmXMBHLV'
    key_file: '/home/private.pem'
    password: PassW0rd!

- name: Remove an existing tag for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'key1:value1'
    app_id: 'pure1:apikey:P3nkAt46lmXMBHLV'
    key_file: '/home/private.pem'
    password: PassW0rd!
    state: absent

- name: Update an existing tag for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'key1:value2'
    app_id: 'pure1:apikey:P3nkAt46lmXMBHLV'
    key_file: '/home/private.pem'
    password: PassW0rd!
"""

RETURN = r"""
"""

import inspect

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    get_pure1,
    pure1_argument_spec,
)


def _error_text(res):
    """Return the message and context of a failed API answer"""
    errors = getattr(res, "errors", None)
    if errors:
        parts = [
            getattr(errors[0], "message", None),
            getattr(errors[0], "context", None),
        ]
        text = ": ".join([str(part) for part in parts if part])
        if text:
            return text
    return "HTTP status {0}".format(getattr(res, "status_code", None))


def _add_tag(tags, key, value):
    """Add a tag to the list to write, the last value given for a key wins"""
    for item in tags:
        if item["key"] == key:
            item["value"] = value
            return
    tags.append({"key": key, "value": value})


def _put_tags(module, pure_1, tags):
    """Write Array Tags"""
    # py-pure-client 1.70.0 and later take the tags as tag_put, not tag
    keyword = "tag_put"
    try:
        params = inspect.signature(pure_1.put_arrays_tags).parameters
        if "tag" in params and "tag_put" not in params:
            keyword = "tag"
    except (TypeError, ValueError):
        pass
    return pure_1.put_arrays_tags(
        resource_names=[module.params["name"]], **{keyword: tags}
    )


def create_tag(module, pure_1):
    """Create Array Tag"""
    changed = True
    if not module.check_mode:
        tags = []
        for tag in range(0, len(module.params["tag"])):
            key = module.params["tag"][tag].split(":")[0]
            value = module.params["tag"][tag].split(":")[1]
            _add_tag(tags, key, value)
        if tags:
            res = _put_tags(module, pure_1, tags)
            if res.status_code != 200:
                module.fail_json(
                    msg="Failed to add tag KVP {0} to array {1}. Error: {2}".format(
                        ", ".join(module.params["tag"]),
                        module.params["name"],
                        _error_text(res),
                    )
                )
    module.exit_json(changed=changed)


def update_tag(module, pure_1, current_tags):
    """Update Array Tag"""
    changed = False
    if not module.check_mode:
        tags = []
        for tag in range(0, len(module.params["tag"])):
            tag_exists = False
            for current_tag in range(0, len(current_tags)):
                if (
                    module.params["tag"][tag].split(":")[0]
                    == current_tags[current_tag].key
                ):
                    tag_exists = True
                    if (
                        module.params["tag"][tag].split(":")[1]
                        != current_tags[current_tag].value
                    ):
                        key = module.params["tag"][tag].split(":")[0]
                        value = module.params["tag"][tag].split(":")[1]
                        _add_tag(tags, key, value)
            if not tag_exists:
                key = module.params["tag"][tag].split(":")[0]
                value = module.params["tag"][tag].split(":")[1]
                _add_tag(tags, key, value)
        if tags:
            res = _put_tags(module, pure_1, tags)
            if res.status_code == 200:
                changed = True
            else:
                module.fail_json(
                    msg="Failed to update tags '{0}' on array {1}. Error: {2}".format(
                        ", ".join([item["key"] for item in tags]),
                        module.params["name"],
                        _error_text(res),
                    )
                )
    module.exit_json(changed=changed)


def delete_tag(module, pure_1, current_tags):
    """Delete Array Tag"""
    changed = False
    if not module.check_mode:
        for tag in range(0, len(module.params["tag"])):
            for current_tag in range(0, len(current_tags)):
                if (
                    module.params["tag"][tag].split(":")[0]
                    == current_tags[current_tag].key
                ):
                    res = pure_1.delete_arrays_tags(
                        resource_names=[module.params["name"]],
                        keys=[module.params["tag"][tag].split(":")[0]],
                    )
                    if res.status_code == 200:
                        changed = True
                    else:
                        module.fail_json(
                            msg="Failed to remove tag KVP '{0}' from volume {1}. Error: {2}".format(
                                module.params["tag"][tag],
                                module.params["name"],
                                _error_text(res),
                            )
                        )
    module.exit_json(changed=changed)


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            state=dict(type="str", default="present", choices=["absent", "present"]),
            tag=dict(type="list", elements="str", required=True),
        )
    )

    module = AnsibleModule(argument_spec, supports_check_mode=True)

    state = module.params["state"]
    pure_1 = get_pure1(module)

    res = pure_1.get_arrays_tags(resource_names=[module.params["name"]])

    if res.status_code != 200:
        module.fail_json(
            msg="Failed to get tags of array {0}. Error: {1}".format(
                module.params["name"], _error_text(res)
            )
        )
    current_tags = list(res.items)

    if state == "present" and not current_tags:
        create_tag(module, pure_1)
    elif state == "absent" and not current_tags:
        module.exit_json(changed=False)
    elif state == "present" and current_tags:
        update_tag(module, pure_1, current_tags)
    elif state == "absent" and current_tags:
        delete_tag(module, pure_1, current_tags)

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
