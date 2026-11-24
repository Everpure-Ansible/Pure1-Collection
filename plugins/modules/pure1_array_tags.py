#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2021, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_array_tags
version_added: '1.0.0'
short_description:  Manage array tags in Pure1
description:
- Manage tags for arrays on Pure1.
- Pure1 does not distinguish the case of a tag key, and neither does this
  module, so C(Owner) and C(owner) are the same tag.
- In check mode the module reports whether it would change a tag and changes
  nothing.
author:
- Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
options:
  name:
    description:
    - The name of the array.
    - The case of the name does not matter, unless Pure1 knows several arrays
      whose names differ only in case. The name is then needed as Pure1 spells
      it.
    - The task fails if Pure1 knows no array of this name.
    type: str
    required: true
  tag:
    description:
    - List of key value pairs to assign to the array.
    - Separate the key from the value using a colon (:) only.
    - Only the first colon separates the two, so a value can contain colons.
    - With I(state=present) every entry needs a key and a value. If a key is
      given more than once, the last value is used, and two keys that differ
      only in case fail the task.
    - With I(state=present) a tag stored under the same key in another case is
      left as it is when its value is the same. When the value differs, the
      tag is updated and its key is stored as the task spells it.
    - With I(state=absent) only the key counts. An entry can be the key alone,
      and a value after the key is ignored.
    - An empty list changes nothing.
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
notes:
- The module returns no value of its own. The common return value C(changed)
  tells whether a tag was added, updated or removed.
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
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem

- name: Remove existing tags for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'key1:value1'
      - key2
    state: absent

- name: Update an existing tag for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'key1:value2'

- name: Set a tag whose value contains colons for array foo
  everpure.pure1.pure1_array_tags:
    name: foo
    tag:
      - 'url:https://example.com:8443/status'
"""

RETURN = r"""
# The module returns nothing beyond the common return values: changed tells
# whether a tag was added, updated or removed.
"""

import inspect

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    Pure1RequestError,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_items,
    pure1_response,
)


def _add_tag(tags, key, value):
    """Add a tag to the list to write, the last value given for a key wins"""
    for item in tags:
        if item["key"] == key:
            item["value"] = value
            return
    tags.append({"key": key, "value": value})


def _same_key(stored, key):
    """Whether a key Pure1 holds is the key of the task, whatever their case"""
    return stored is not None and stored.lower() == key.lower()


def _wanted_tags(module):
    """Return the tags a task with state present asks for

    A tag is split at its first colon only, so that a value can hold colons.
    A tag without a key or without a value fails the task, and so do two keys
    that differ only in case, which Pure1 holds as one key.
    """
    tags = []
    for tag in module.params["tag"]:
        key, dummy, value = tag.partition(":")
        if not key or not value:
            module.fail_json(
                msg="Tag '{0}' is not valid: with state present a tag has the "
                "form key:value, and neither the key nor the value can be "
                "empty.".format(tag)
            )
        for item in tags:
            if item["key"] != key and _same_key(item["key"], key):
                module.fail_json(
                    msg="Tag keys '{0}' and '{1}' differ only in case, and "
                    "Pure1 holds them as one key. Give the key once.".format(
                        item["key"], key
                    )
                )
        _add_tag(tags, key, value)
    return tags


def _is_stored(tag, current_tags):
    """Whether Pure1 holds the key of a tag, whatever its case, with its value"""
    values = [
        getattr(current_tag, "value", None)
        for current_tag in current_tags
        if _same_key(getattr(current_tag, "key", None), tag["key"])
    ]
    return bool(values) and all(value == tag["value"] for value in values)


def _array_name(module, pure_1):
    """Return the name of the array as Pure1 spells it, or fail

    A filter finds the array whatever the case of its name. The tag calls
    take the name only as Pure1 spells it. Of what the filter finds, only the
    name of the task in whatever case is taken, so that no tag of another
    array is read or written.
    """
    name = module.params["name"]
    arrays = pure1_items(
        module,
        "get_arrays",
        lambda: pure_1.get_arrays(filter="name=" + filter_string(name)),
    )
    names = [getattr(array, "name", None) for array in arrays]
    names = [found for found in names if found and found.lower() == name.lower()]
    if not names:
        module.fail_json(msg="Array {0} is not known to Pure1.".format(name))
    if name in names:
        return name
    spellings = sorted(set(names))
    if len(spellings) > 1:
        module.fail_json(
            msg="Pure1 knows more than one array whose name is {0} in another "
            "case: {1}. Give the name as Pure1 spells it.".format(
                name, ", ".join(spellings)
            )
        )
    return spellings[0]


def _put_tags(module, pure_1, tags, array):
    """Write Array Tags"""
    # py-pure-client 1.70.0 and later take the tags as tag_put, not tag
    keyword = "tag_put"
    try:
        params = inspect.signature(pure_1.put_arrays_tags).parameters
        if "tag" in params and "tag_put" not in params:
            keyword = "tag"
    except (TypeError, ValueError):
        pass

    def put_tags():
        return pure_1.put_arrays_tags(resource_names=[array], **{keyword: tags})

    return pure1_response(module, "put_arrays_tags", put_tags, fail=False)


def _delete_key(module, pure_1, key, array):
    """Delete one Array Tag, by its key as Pure1 spells it"""
    return pure1_response(
        module,
        "delete_arrays_tags",
        lambda: pure_1.delete_arrays_tags(resource_names=[array], keys=[key]),
        fail=False,
    )


def create_tag(module, pure_1, array=None):
    """Create Array Tag

    array is the name of the array as Pure1 spells it, when it is not the
    name the task gives.
    """
    array = array or module.params["name"]
    tags = _wanted_tags(module)
    if tags and not module.check_mode:
        try:
            _put_tags(module, pure_1, tags, array)
        except Pure1RequestError as error:
            module.fail_json(
                msg="Failed to add tag KVP {0} to array {1}. Error: {2}".format(
                    ", ".join(module.params["tag"]), array, error.text
                )
            )
    module.exit_json(changed=bool(tags))


def update_tag(module, pure_1, current_tags, array=None):
    """Update Array Tag

    array is the name of the array as Pure1 spells it, when it is not the
    name the task gives.
    """
    array = array or module.params["name"]
    tags = [tag for tag in _wanted_tags(module) if not _is_stored(tag, current_tags)]
    if tags and not module.check_mode:
        try:
            _put_tags(module, pure_1, tags, array)
        except Pure1RequestError as error:
            module.fail_json(
                msg="Failed to update tags '{0}' on array {1}. Error: {2}".format(
                    ", ".join([item["key"] for item in tags]), array, error.text
                )
            )
    module.exit_json(changed=bool(tags))


def delete_tag(module, pure_1, current_tags, array=None):
    """Delete Array Tag

    array is the name of the array as Pure1 spells it, when it is not the
    name the task gives.
    """
    array = array or module.params["name"]
    changed = False
    for tag in module.params["tag"]:
        # Only the key counts, a tag can be the key alone
        key = tag.partition(":")[0]
        for current_tag in current_tags:
            stored = getattr(current_tag, "key", None)
            if not _same_key(stored, key):
                continue
            changed = True
            if module.check_mode:
                continue
            try:
                _delete_key(module, pure_1, stored, array)
            except Pure1RequestError as error:
                module.fail_json(
                    msg="Failed to remove tag KVP '{0}' from array {1}. Error: {2}".format(
                        tag, array, error.text
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
    if state == "present":
        # A tag that cannot be set fails the task before Pure1 is asked anything
        _wanted_tags(module)
    pure_1 = get_pure1(module)
    array = _array_name(module, pure_1)

    try:
        current_tags = pure1_items(
            module,
            "get_arrays_tags",
            lambda: pure_1.get_arrays_tags(resource_names=[array]),
            fail=False,
        )
    except Pure1RequestError as error:
        module.fail_json(
            msg="Failed to get tags of array {0}. Error: {1}".format(array, error.text)
        )

    if state == "present" and not current_tags:
        create_tag(module, pure_1, array)
    elif state == "absent" and not current_tags:
        module.exit_json(changed=False)
    elif state == "present" and current_tags:
        update_tag(module, pure_1, current_tags, array)
    elif state == "absent" and current_tags:
        delete_tag(module, pure_1, current_tags, array)

    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
