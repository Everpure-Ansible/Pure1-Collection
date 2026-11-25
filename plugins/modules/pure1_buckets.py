#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_buckets
version_added: '1.6.0'
short_description: Collect object store accounts and their buckets from Pure1
description:
  - Collect the object store accounts of the FlashBlade and FlashArray arrays
    that Pure1 knows, each with its buckets, their versioning state, their
    number of objects and whether a bucket is destroyed and not yet
    eradicated.
  - Use it for an inventory of the object storage of the organization, to
    check buckets across arrays, and to get the IDs by which Pure1 keys the
    metrics of buckets and accounts.
  - Pure1 holds what an array reported last, which is usually less than an
    hour old. The C(as_of) of each account and bucket gives the time of its
    data.
options:
  array:
    description:
      - Names of the arrays to return the accounts and buckets of. All
        arrays when not set or empty.
      - A name is matched without regard to case and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - Naming arrays costs one more request to Pure1.
    type: list
    elements: str
  name:
    description:
      - Names of the buckets to return. All buckets when not set or empty.
      - A name is matched without regard to case, and a name that no bucket
        has is not an error.
      - The name of a bucket is unique within its account only, so one name
        can return a bucket of several accounts and arrays.
      - An account without a bucket of these names is left out of the
        result.
    type: list
    elements: str
  account:
    description:
      - Name of the object store account to return, with its buckets. All
        accounts when not set or empty.
      - This is a single name, not a list of names. It is matched without
        regard to case. An account of this name is returned for every array
        that has one, and a name that no account has is not an error.
    type: str
  destroyed:
    description:
      - With C(true) only buckets that are destroyed and not yet eradicated
        are returned, with C(false) only buckets that are not destroyed.
        Both when not set.
      - When it is set, an account without such a bucket is left out of the
        result.
    type: bool
seealso:
  - module: everpure.pure1.pure1_metrics
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the object store accounts and buckets of all arrays
  everpure.pure1.pure1_buckets:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: object_store

- name: Show the accounts of each array with their buckets
  ansible.builtin.debug:
    msg: "{{ object_store['pure1_buckets']['accounts'] }}"

- name: Collect the buckets of one account on two arrays
  everpure.pure1.pure1_buckets:
    array:
      - array1
      - array2
    account: account1
  register: account_buckets

- name: Show the names of the buckets of the account on the first array
  ansible.builtin.debug:
    msg: "{{ account_buckets['pure1_buckets']['accounts']['array1']['account1']['buckets'] | list }}"
  when: "'array1' in account_buckets['pure1_buckets']['accounts']"

- name: Collect the buckets that are destroyed and not yet eradicated
  everpure.pure1.pure1_buckets:
    destroyed: true
  register: destroyed_buckets

- name: Check that no bucket waits to be eradicated
  ansible.builtin.assert:
    that:
      - destroyed_buckets['pure1_buckets']['accounts'] | length == 0
    fail_msg: Some arrays hold destroyed buckets

- name: Collect the buckets named bucket1 and bucket2 on one array
  everpure.pure1.pure1_buckets:
    array:
      - array1
    name:
      - bucket1
      - bucket2
    destroyed: false
  register: named_buckets

- name: Check that every one of these buckets keeps versions of its objects
  ansible.builtin.assert:
    that:
      - item.value.buckets.values() | rejectattr('versioning', 'equalto', 'enabled') | list | length == 0
    fail_msg: "Account {{ item.key }} has a bucket without versioning"
  loop: "{{ named_buckets['pure1_buckets']['accounts']['array1'] | default({}) | dict2items }}"
"""

RETURN = r"""
pure1_buckets:
  description: The object store accounts and buckets that Pure1 reports.
  returned: always
  type: dict
  contains:
    accounts:
      description:
        - The object store accounts of each array, as a dictionary keyed by
          array name. The value for an array is a dictionary keyed by account
          name, and the value for an account is a dictionary with the keys
          listed here.
        - An account without a returned bucket is listed only when neither
          I(name) nor I(destroyed) is set, and an array is listed only when it
          has an account to list.
        - A key is null when Pure1 reports no value for it. An account that
          Pure1 names in a bucket and does not list itself has only its C(id)
          and its C(buckets) set, its other keys are null.
        - Where Pure1 reports two accounts of one name on an array, or two
          buckets of one name in an account, the one created last is returned
          and the module gives a warning.
        - Where two arrays carry one name, the array that Pure1 returns
          first keeps the name. The accounts and buckets of the other one
          are left out, and the module gives a warning that counts them.
        - Empty when there is nothing to return.
      type: dict
      contains:
        id:
          description:
            - The ID of the account in Pure1, which the metrics of the
              account are keyed by.
          type: str
        created:
          description: The time the account was created, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: The time the account was created, in milliseconds since the epoch.
          type: int
        object_count:
          description:
            - The number of objects in the account, as Pure1 reports it. It
              can differ from the sum of the C(object_count) of the buckets
              of the account.
          type: int
        as_of:
          description:
            - The time of the data that Pure1 holds of the account, in UTC, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description:
            - The time of the data that Pure1 holds of the account, in
              milliseconds since the epoch.
          type: int
        buckets:
          description:
            - The returned buckets of the account, as a dictionary keyed by
              bucket name. The value for a bucket is a dictionary with the
              keys listed here.
            - Empty when the account has no bucket.
          type: dict
          contains:
            id:
              description:
                - The ID of the bucket in Pure1, which the metrics of the
                  bucket are keyed by.
              type: str
            created:
              description: The time the bucket was created, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            created_epoch:
              description: The time the bucket was created, in milliseconds since the epoch.
              type: int
            destroyed:
              description:
                - Whether the bucket is destroyed and not yet eradicated.
              type: bool
            object_count:
              description: The number of objects in the bucket.
              type: int
            versioning:
              description:
                - The versioning state of the bucket, one of C(none),
                  C(enabled) and C(suspended).
              type: str
            as_of:
              description:
                - The time of the data that Pure1 holds of the bucket, in UTC,
                  as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            as_of_epoch:
              description:
                - The time of the data that Pure1 holds of the bucket, in
                  milliseconds since the epoch.
              type: int
  sample:
    accounts:
      array1:
        account1:
          id: 0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d
          created: 2021-02-03 13:04:05 UTC
          created_epoch: 1612357445000
          object_count: 1500
          as_of: 2026-11-24 10:15:30 UTC
          as_of_epoch: 1795515330000
          buckets:
            bucket1:
              id: 1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e
              created: 2021-02-04 08:30:00 UTC
              created_epoch: 1612427400000
              destroyed: false
              object_count: 1500
              versioning: enabled
              as_of: 2026-11-24 10:15:30 UTC
              as_of_epoch: 1795515330000
"""


from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_is_one_of,
    pure1_keeps_name,
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


def array_names(item, arrays, owners):
    """Return the names of the arrays to file an account or a bucket under, each once

    arrays are the arrays of the task as pure1_resolve_arrays() returns
    them, or None. Pure1 matches the name of an array without regard to
    case, and an item can be on more arrays than the task names: an array
    that is not one of them is passed over. Of two arrays that carry one
    name the one seen first keeps it, which owners tells. The second value
    is whether the item is on an array that does not keep its name. The
    names are None for an item that names no array at all.
    """
    references = [
        reference
        for reference in pure1_attr(item, "arrays", [])
        if pure1_attr(reference, "name") is not None
    ]
    if not references:
        return None, False
    names = []
    other_array = False
    for reference in references:
        name = pure1_attr(reference, "name")
        if arrays is not None and not pure1_is_one_of(arrays, reference):
            continue
        if not pure1_keeps_name(owners, name, pure1_attr(reference, "id")):
            other_array = True
        elif name not in names:
            names.append(name)
    return names, other_array


def is_another(earlier, row):
    """Tell whether a name is taken by another object than the one of a row

    The id tells the objects apart: Pure1 can list one object twice.
    """
    return earlier is not None and (earlier["id"] is None or earlier["id"] != row["id"])


def created_last(row):
    """Return what orders the objects of one name: the one created last is last"""
    created = row["created_epoch"]
    return (-1 if created is None else created, row["id"] or "")


def account_row(account):
    """Return the result of an account, or of the reference a bucket has to one"""
    created = pure1_attr(account, "created")
    as_of = pure1_attr(account, "as_of")
    return {
        "id": pure1_attr(account, "id"),
        "created": pure1_time(created),
        "created_epoch": created,
        "object_count": pure1_attr(account, "object_count"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "buckets": {},
    }


def bucket_row(bucket):
    created = pure1_attr(bucket, "created")
    as_of = pure1_attr(bucket, "as_of")
    return {
        "id": pure1_attr(bucket, "id"),
        "created": pure1_time(created),
        "created_epoch": created,
        "destroyed": pure1_attr(bucket, "destroyed"),
        "object_count": pure1_attr(bucket, "object_count"),
        "versioning": pure1_attr(bucket, "versioning"),
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
    }


def generate_accounts_dict(module, pure_1):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    wanted = [array["name"] for array in arrays] if arrays else None
    names = module.params["name"]
    account = module.params["account"] or None
    destroyed = module.params["destroyed"]
    # With a selection of buckets only the accounts that have one are returned
    selected = bool(names) or destroyed is not None

    array_filter = filter_in("arrays.name", wanted)
    bucket_filter = filter_and(
        array_filter,
        filter_in("account.name", account),
        filter_in("name", names),
        None if destroyed is None else "destroyed=" + str(destroyed).lower(),
    )
    account_filter = filter_and(array_filter, filter_in("name", account))

    def list_buckets(**page):
        return pure_1.get_buckets(filter=bucket_filter, **page)

    def list_accounts(**page):
        return pure_1.get_object_store_accounts(filter=account_filter, **page)

    listing = pure1_list(module, "get_buckets", list_buckets)
    warn_if_truncated(module, listing, "buckets")
    buckets = listing.items
    accounts = []
    if buckets or not selected:
        listing = pure1_list(module, "get_object_store_accounts", list_accounts)
        warn_if_truncated(module, listing, "object store accounts")
        accounts = listing.items

    accounts_info = {}
    by_id = {}
    # The accounts and the buckets of an array go under one name
    owners = {}
    left_out = 0
    other_array = 0
    same_name = 0
    for item in accounts:
        name = pure1_attr(item, "name")
        if name is None:
            left_out += 1
            continue
        on_arrays, on_other_array = array_names(item, arrays, owners)
        if on_arrays is None:
            left_out += 1
            continue
        if on_other_array:
            other_array += 1
        for array in on_arrays:
            row = account_row(item)
            if row["id"] is not None:
                # Also when another account takes its place in the result:
                # its buckets then go with it
                by_id[(array, row["id"])] = row
            of_array = accounts_info.setdefault(array, {})
            if is_another(of_array.get(name), row):
                same_name += 1
                if created_last(of_array[name]) >= created_last(row):
                    continue
            of_array[name] = row
    if left_out:
        module.warn(
            "Object store accounts that Pure1 returned without a name or "
            "without an array are left out of the result: {0}.".format(left_out)
        )
    if other_array:
        module.warn(
            "Object store accounts of an array that has the name of another "
            "array are left out of the result: {0}.".format(other_array)
        )
    if same_name:
        module.warn(
            "Object store accounts that Pure1 returned under the name of "
            "another account of the same array are left out of the result, "
            "which keeps the account created last with its buckets: "
            "{0}.".format(same_name)
        )

    left_out = 0
    other_array = 0
    same_name = 0
    for item in buckets:
        name = pure1_attr(item, "name")
        account_id = pure1_attr(item, "account.id")
        account_name = pure1_attr(item, "account.name")
        if name is None:
            left_out += 1
            continue
        on_arrays, on_other_array = array_names(item, arrays, owners)
        if on_arrays is None:
            left_out += 1
            continue
        if on_other_array:
            other_array += 1
        without_account = False
        for array in on_arrays:
            # The id tells the account, whatever name the bucket has for it
            row = by_id.get((array, account_id))
            if row is None and account_name is not None:
                # Pure1 did not list the account: its reference stands for it
                row = accounts_info.setdefault(array, {}).setdefault(
                    account_name, account_row(pure1_attr(item, "account"))
                )
            if row is None:
                without_account = True
                continue
            bucket = bucket_row(item)
            if is_another(row["buckets"].get(name), bucket):
                same_name += 1
                if created_last(row["buckets"][name]) >= created_last(bucket):
                    continue
            row["buckets"][name] = bucket
        if without_account:
            left_out += 1
    if left_out:
        module.warn(
            "Buckets that Pure1 returned without a name, without an account or "
            "without an array are left out of the result: {0}.".format(left_out)
        )
    if other_array:
        module.warn(
            "Buckets of an array that has the name of another array are left "
            "out of the result: {0}.".format(other_array)
        )
    if same_name:
        module.warn(
            "Buckets that Pure1 returned under the name of another bucket of "
            "the same account are left out of the result, which keeps the "
            "bucket created last: {0}.".format(same_name)
        )

    if selected:
        for array in list(accounts_info):
            accounts_info[array] = {
                name: row
                for name, row in accounts_info[array].items()
                if row["buckets"]
            }
            if not accounts_info[array]:
                del accounts_info[array]
    return accounts_info


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            name=dict(type="list", elements="str"),
            account=dict(type="str"),
            destroyed=dict(type="bool"),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    pure_1 = get_pure1(module)

    module.exit_json(
        changed=False,
        pure1_buckets={"accounts": generate_accounts_dict(module, pure_1)},
    )


if __name__ == "__main__":
    main()
