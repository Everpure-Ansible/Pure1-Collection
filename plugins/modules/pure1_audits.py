#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_audits
version_added: '1.6.0'
short_description: Collect audit records from Pure1
description:
  - Collect the audit records of the arrays of an organization from Pure1,
    newest first. A record tells who ran which Purity command on which array,
    and when.
  - A playbook can verify the changes of a maintenance window, export the
    destroy and eradicate commands of a day, or check that nobody but a
    service user changed anything, with I(limit) C(0) for a count alone.
  - Pure1 receives the records of an array with a delay of an hour or more. A
    I(window) of an hour or two can therefore find nothing, and the records
    of a past period can still arrive later, so C(total) can grow from one
    run to the next.
options:
  array:
    description:
      - Names of the arrays whose records are returned. The records of every
        array are returned when it is not set or empty.
      - A name is matched without regard to case, and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - Naming arrays costs one more request to Pure1.
    type: list
    elements: str
  user:
    description:
      - Name of one user, matched without regard to case. Only the records
        of this user are returned.
      - Mutually exclusive with I(exclude_users). A task that gives both
        options fails, also when I(exclude_users) is an empty list.
    type: str
  exclude_users:
    description:
      - Names of users whose records are left out, matched without regard to
        case. No user is left out when it is not set or empty.
      - A name with a single quote fails the task, because Pure1 cannot leave
        such a user out.
      - Mutually exclusive with I(user). A task that gives both options
        fails, also when I(exclude_users) is an empty list.
    type: list
    elements: str
  command:
    description:
      - One command, such as C(purevol) or C(purefs), matched without regard
        to case. Only the records of this command are returned.
    type: str
  subcommand:
    description:
      - Subcommands, such as C(destroy) and C(eradicate), matched without
        regard to case. Only the records with one of them are returned, and
        those with any subcommand when it is not set or empty.
      - A value must be the whole subcommand. I(subcommand_contains) also
        finds a subcommand of several words, such as C(snapshot destroy).
      - Several values of which one holds a single quote fail the task,
        because Pure1 cannot filter on them. One such value alone works.
      - Mutually exclusive with I(subcommand_contains). A task that gives
        both options fails, also when I(subcommand) is an empty list.
    type: list
    elements: str
  subcommand_contains:
    description:
      - One text, matched without regard to case. Only the records whose
        subcommand holds this text are returned.
      - A text with a single quote fails the task, because Pure1 cannot
        search for it.
      - Mutually exclusive with I(subcommand). A task that gives both
        options fails, also when I(subcommand) is an empty list.
    type: str
  arguments:
    description:
      - One text, such as the name of a volume, a host or a pod, matched
        without regard to case. Only the records whose arguments hold this
        text are returned.
      - A text with a single quote fails the task, because Pure1 cannot
        search for it.
    type: str
  window:
    description:
      - How far back from I(end_time) the records are collected, in seconds.
        Must be 1 or more.
      - Pure1 refuses a period longer than 30 days, which are 2592000
        seconds, and the task fails with the message of Pure1.
      - Mutually exclusive with I(start_time).
    type: int
    default: 86400
  start_time:
    description:
      - Start of the period, in milliseconds since the epoch. Only the
        records of this time and later are returned.
      - Must not be after I(end_time). Pure1 refuses a period longer than 30
        days, and the task fails with the message of Pure1.
      - Mutually exclusive with I(window).
    type: int
  end_time:
    description:
      - End of the period, in milliseconds since the epoch. Only the
        records before this time are returned.
      - Defaults to the time at which the module starts, by the clock of the
        host it runs on.
    type: int
  limit:
    description:
      - The most records to return, the newest first. Must be 0 or more.
        An organization can hold tens of thousands of records for one day.
      - With C(0) no record is returned, only C(total).
      - Pure1 gives up to 1,000 records with one request. A higher limit
        costs one request for every 1,000 records.
    type: int
    default: 1000
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the newest audit records of the last day
  everpure.pure1.pure1_audits:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: audits

- name: Show how many of the records returned each user caused
  ansible.builtin.debug:
    msg: "{{ audits['pure1_audits']['by_user'] }}"

- name: Count the commands of the last week that two service users did not run
  everpure.pure1.pure1_audits:
    exclude_users:
      - svc-automation
      - svc-backup
    window: 604800
    limit: 0
  register: manual

- name: Check that nobody but the service users ran a command
  ansible.builtin.assert:
    that:
      - manual['pure1_audits']['total'] == 0

- name: Collect what was destroyed or eradicated on two arrays in the last day
  everpure.pure1.pure1_audits:
    array:
      - array1
      - array2
    subcommand:
      - destroy
      - eradicate
  register: removed

- name: Show who destroyed or eradicated what
  ansible.builtin.debug:
    msg: >-
      {{ item['time'] }} {{ item['array']['name'] }} {{ item['user'] }}:
      {{ item['command'] }} {{ item['subcommand'] }} {{ item['arguments'] }}
  loop: "{{ removed['pure1_audits']['records'] }}"

- name: Collect the volume commands that named vol1 in two hours that ended three hours ago
  everpure.pure1.pure1_audits:
    command: purevol
    arguments: vol1
    start_time: "{{ (now().timestamp() | int - 18000) * 1000 }}"
    end_time: "{{ (now().timestamp() | int - 10800) * 1000 }}"
  register: maintenance

- name: Check that every record of those two hours was read
  ansible.builtin.assert:
    that:
      - not maintenance['pure1_audits']['truncated']

- name: Count the snapshot commands of one user in the last twelve hours
  everpure.pure1.pure1_audits:
    user: pureuser
    subcommand_contains: snap
    window: 43200
    limit: 0
  register: snapshots

- name: Show the number of snapshot commands
  ansible.builtin.debug:
    msg: "{{ snapshots['pure1_audits']['total'] }}"
"""

RETURN = r"""
pure1_audits:
  description: The audit records collected from Pure1.
  returned: always
  type: dict
  contains:
    total:
      description:
        - The number of records in Pure1 that the options select, null when
          Pure1 does not report it.
      type: int
    returned:
      description: The number of records under C(records).
      type: int
    truncated:
      description:
        - Whether fewer records are returned than the options select. Either
          I(limit) cut the result, or the records in Pure1 changed while the
          module read them page by page.
      type: bool
    start:
      description:
        - Start of the period the records are from, in UTC, as
          C(YYYY-MM-DD HH:MM:SS UTC).
      type: str
    start_epoch:
      description: Start of the period, in milliseconds since the epoch.
      type: int
    end:
      description:
        - End of the period, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC). A record
          of exactly this time is not part of the period.
      type: str
    end_epoch:
      description: End of the period, in milliseconds since the epoch.
      type: int
    by_array:
      description:
        - The number of records under C(records) of each array, as a
          dictionary keyed by array name.
        - Where two arrays carry one name, the records of the array that
          comes first under C(records) are counted, and the module gives a
          warning that counts the records of the other one. C(records) holds
          them all and tells the arrays apart by the ID of the array.
        - Empty when no record is returned.
      type: dict
    by_user:
      description:
        - The number of records under C(records) of each user, as a
          dictionary keyed by user name.
        - Empty when no record is returned.
      type: dict
    records:
      description:
        - The records, the newest first, each with the keys listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when no record is selected and when I(limit) is C(0).
      type: list
      elements: dict
      contains:
        id:
          description: The Pure1 ID of the record.
          type: str
        time:
          description: When the command was run, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        time_epoch:
          description: When the command was run, in milliseconds since the epoch.
          type: int
        array:
          description: The array the command was run on.
          type: dict
          contains:
            name:
              description: Name of the array.
              type: str
            id:
              description: The Pure1 ID of the array.
              type: str
            fqdn:
              description: Fully qualified domain name of the array, null when Pure1 has none.
              type: str
        user:
          description: Name of the user who ran the command.
          type: str
        command:
          description: The command, such as C(purevol).
          type: str
        subcommand:
          description: The subcommand, such as C(destroy).
          type: str
        arguments:
          description: The arguments of the command.
          type: str
        origin:
          description: Where the command was run, C(array) or C(Pure1).
          type: str
  sample:
    total: 2412
    returned: 2
    truncated: true
    start: 2021-02-02 13:04:05 UTC
    start_epoch: 1612271045000
    end: 2021-02-03 13:04:05 UTC
    end_epoch: 1612357445000
    by_array:
      array1: 1
      array2: 1
    by_user:
      pureuser: 2
    records:
      - id: 0f8fad5b-d9cb-469f-a165-70867728950e
        time: 2021-02-03 13:01:44 UTC
        time_epoch: 1612357304000
        array:
          name: array1
          id: 7c9e6679-7425-40de-944b-e07fc1f90ae7
          fqdn: array1.example.com
        user: pureuser
        command: purevol
        subcommand: destroy
        arguments: vol1
        origin: array
      - id: 6ec0bd7f-11c0-43da-975e-2a8ad9ebae0b
        time: 2021-02-03 12:58:10 UTC
        time_epoch: 1612357090000
        array:
          name: array2
          id: 3f2504e0-4f89-41d3-9a0c-0305e82c3301
          fqdn: null
        user: pureuser
        command: purefs
        subcommand: snapshot destroy
        arguments: fs1.daily
        origin: array
"""


import time

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_resolve_arrays,
    pure1_time,
)

# Newest first. Several records share one millisecond, and the id makes the
# order total, which the pages after the first need: Pure1 gives no
# continuation token for a sorted listing.
NEWEST_FIRST = ["time-", "id"]
QUOTE = "'"


def check_options(module):
    """Fail the task for a value that the argument spec cannot refuse

    Pure1 answers HTTP 500 to a filter on audit records that holds a single
    quote in a text to search for, in a value to leave out, or in one of
    several values of a field. No other form of such a filter is answered,
    so the task fails here, before any request.
    """
    params = module.params
    limit = params["limit"]
    # The argument spec refuses a limit of null. Should one get past it,
    # the listing would read every record of the period
    if limit is None or limit < 0:
        module.fail_json(msg="limit must be 0 or more, got {0}".format(limit))
    for option in ("user", "command", "subcommand_contains", "arguments"):
        if params[option] == "":
            module.fail_json(msg="{0} must not be empty.".format(option))
    for option in ("exclude_users", "subcommand"):
        if "" in (params[option] or []):
            module.fail_json(msg="{0} must not hold an empty value.".format(option))
    if any(QUOTE in user for user in params["exclude_users"] or []):
        module.fail_json(
            msg="Pure1 cannot leave a user whose name holds a single quote out "
            "of the audit records: exclude_users must not hold such a name."
        )
    subcommands = set(params["subcommand"] or [])
    if len(subcommands) > 1 and any(QUOTE in value for value in subcommands):
        module.fail_json(
            msg="Pure1 cannot filter audit records on several values when one "
            "of them holds a single quote: give subcommand one such value "
            "alone, which works."
        )
    for option in ("subcommand_contains", "arguments"):
        if QUOTE in (params[option] or ""):
            module.fail_json(
                msg="Pure1 cannot search audit records for a text that holds a "
                "single quote: {0} must not hold one.".format(option)
            )


def time_range(module):
    """Return the start and the end of the period of the task, in milliseconds

    The end is not part of the period. A value that the argument spec
    cannot refuse fails the task.
    """
    start = module.params["start_time"]
    end = module.params["end_time"]
    for option, value in (("start_time", start), ("end_time", end)):
        # Pure1 takes a time before 1970 for an illegal filter
        if value is not None and value < 0:
            module.fail_json(msg="{0} must be 0 or more, got {1}".format(option, value))
    if end is None:
        end = int(time.time() * 1000)
    if start is None:
        # The argument spec refuses a window beside a start_time, and a
        # window of null
        window = module.params["window"]
        if window is None or window < 1:
            module.fail_json(msg="window must be 1 or more, got {0}".format(window))
        start = max(end - window * 1000, 0)
    elif start > end:
        # Pure1 would answer with no record, which reads as nothing was done
        bound = "end_time"
        if module.params["end_time"] is None:
            bound = "the time the module started, by the clock of its host"
        module.fail_json(
            msg="start_time must not be after {0}: {1} is after "
            "{2}".format(bound, start, end)
        )
    return start, end


def contains(field, text):
    """Return the filter term for a field that holds a text, None for no text"""
    if text is None:
        return None
    return "contains({0},{1})".format(field, filter_string(text))


def audit_filter(module, arrays, start, end):
    """Return the filter of the records that the options select

    Every option is a term of the filter, so that Pure1 counts and limits
    the records that the task asks for.
    """
    params = module.params
    ids = [array["id"] for array in arrays or []]
    if all(ids):
        # Exact, where a name also selects an array named in another case
        selected = filter_in("arrays.id", ids)
    else:
        selected = filter_in("arrays.name", [array["name"] for array in arrays])
    excluded = filter_in("user", params["exclude_users"])
    return filter_and(
        selected,
        filter_in("user", params["user"]),
        "not({0})".format(excluded) if excluded else None,
        filter_in("command", params["command"]),
        filter_in("subcommand", params["subcommand"]),
        contains("subcommand", params["subcommand_contains"]),
        contains("arguments", params["arguments"]),
        "time>={0}".format(start),
        "time<{0}".format(end),
    )


def audit_record(audit):
    # Every record is of one array. An entry can be null: it holds nothing
    arrays = [array for array in pure1_attr(audit, "arrays", []) if array is not None]
    array = arrays[0] if arrays else None
    when = pure1_attr(audit, "time")
    return {
        "id": pure1_attr(audit, "id"),
        "time": pure1_time(when),
        "time_epoch": when,
        "array": {
            "name": pure1_attr(array, "name"),
            "id": pure1_attr(array, "id"),
            "fqdn": pure1_attr(array, "fqdn"),
        },
        "user": pure1_attr(audit, "user"),
        "command": pure1_attr(audit, "command"),
        "subcommand": pure1_attr(audit, "subcommand"),
        "arguments": pure1_attr(audit, "arguments"),
        "origin": pure1_attr(audit, "origin"),
    }


def count_records(module, records):
    """Return the number of records of each array and of each user

    by_array has one place for a name. Two arrays can carry one name: the
    array seen first keeps it, and the records of another array of that
    name are not counted. Every record stays in the result.
    """
    by_array = {}
    by_user = {}
    owners = {}
    without_array = 0
    of_namesake = 0
    without_user = 0
    for record in records:
        array = record["array"]["name"]
        array_id = record["array"]["id"]
        if array is None:
            without_array += 1
        elif array_id is not None and owners.setdefault(array, array_id) != array_id:
            of_namesake += 1
        else:
            by_array[array] = by_array.get(array, 0) + 1
        user = record["user"]
        if user is None:
            without_user += 1
        else:
            by_user[user] = by_user.get(user, 0) + 1
    if without_array:
        module.warn(
            "Audit records that Pure1 returned without an array are not "
            "counted in by_array: {0}.".format(without_array)
        )
    if of_namesake:
        module.warn(
            "Audit records of an array that carries the name of another "
            "array are not counted in by_array: {0}.".format(of_namesake)
        )
    if without_user:
        module.warn(
            "Audit records that Pure1 returned without a user are not "
            "counted in by_user: {0}.".format(without_user)
        )
    return by_array, by_user


def generate_audits_dict(module, pure_1, start, end):
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    selection = audit_filter(module, arrays, start, end)
    listing = pure1_list(
        module,
        "get_audits",
        lambda **page: pure_1.get_audits(filter=selection, sort=NEWEST_FIRST, **page),
        limit=module.params["limit"],
    )
    records = []
    seen = set()
    for audit in listing.items:
        audit_id = pure1_attr(audit, "id")
        if audit_id in seen:
            # A record that Pure1 received between two pages moved the
            # later page down by one
            continue
        if audit_id is not None:
            seen.add(audit_id)
        records.append(audit_record(audit))
    truncated = listing.truncated
    if listing.total is not None:
        truncated = len(records) < listing.total
    by_array, by_user = count_records(module, records)
    return {
        "total": listing.total,
        "returned": len(records),
        "truncated": truncated,
        "start": pure1_time(start),
        "start_epoch": start,
        "end": pure1_time(end),
        "end_epoch": end,
        "by_array": by_array,
        "by_user": by_user,
        "records": records,
    }


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            user=dict(type="str"),
            exclude_users=dict(type="list", elements="str"),
            command=dict(type="str"),
            subcommand=dict(type="list", elements="str"),
            subcommand_contains=dict(type="str"),
            arguments=dict(type="str"),
            window=dict(type="int", default=86400),
            start_time=dict(type="int"),
            end_time=dict(type="int"),
            limit=dict(type="int", default=1000),
        )
    )
    module = AnsibleModule(
        argument_spec,
        mutually_exclusive=[
            ["user", "exclude_users"],
            ["subcommand", "subcommand_contains"],
            ["window", "start_time"],
        ],
        supports_check_mode=True,
    )
    check_options(module)
    start, end = time_range(module)
    pure_1 = get_pure1(module)

    module.exit_json(
        changed=False, pure1_audits=generate_audits_dict(module, pure_1, start, end)
    )


if __name__ == "__main__":
    main()
