#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_pgroup_snapshots
version_added: '1.6.0'
short_description: Collect replicated protection group snapshots from Pure1
description:
  - Collect the protection group snapshots that replication stored on a target
    array, newest first, each with the statistics of its transfer.
  - A playbook can check that the latest snapshot of a protection group
    arrived on every target, how long the transfer took and how much data it
    moved, and find transfers that never finished.
  - Pure1 learns of a snapshot from the reports of the array that stores it,
    a few minutes after the snapshot is created. C(as_of) tells when Pure1
    last refreshed a snapshot.
options:
  array:
    description:
      - Names of the arrays the snapshots are stored on, the targets of the
        replication. Only snapshots stored on one of them are returned.
      - Without it, or with an empty list, the snapshots stored on every
        array are returned.
      - A name is matched without regard to case, and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - Only the arrays that the names stand for are selected, which are
        told from every other array by their ID in Pure1. Another array
        whose name differs from one of them in case only is not selected.
      - Naming arrays costs one more request to Pure1.
    type: list
    elements: str
  source_array:
    description:
      - Name of the array the snapshots were replicated from, matched
        without regard to case. It takes one name.
      - It is read from the name of a snapshot, which is
        C(source_array:group.suffix). The snapshot of a protection group in a
        pod carries the name of the pod there and is not matched; select it
        with the C(pod::group) form of I(protection_group).
      - Pure1 can only look for the name inside the snapshot name, so the
        module itself leaves out the snapshots of an array or a pod whose
        name ends in the same characters, which costs further requests when
        there are any and fails the task when there are more than about a
        dozen. C(total) can still count such snapshots when none of them is
        among the snapshots read.
    type: str
  protection_group:
    description:
      - Names of protection groups as they are on the source array, C(group),
        or C(pod::group) for a group in a pod, matched without regard to
        case.
      - Without it, or with an empty list, the snapshots of every protection
        group are returned.
      - A name on several source arrays selects the group of each of them.
      - Required when I(latest_only) is set.
      - A name without a snapshot among those that I(array), I(source_array)
        and I(window) select does not fail the task. It is returned in
        C(missing_protection_groups).
      - Costs one more request for each name that is not among the snapshots
        returned, when I(incomplete_only) is set or, without I(latest_only),
        the result is truncated.
    type: list
    elements: str
  window:
    description:
      - Only snapshots created in the last I(window) seconds, by the clock of
        the host the module runs on. Must be 1 or more.
    type: int
  latest_only:
    description:
      - Return only the newest snapshot of each protection group on each
        array that stores it. Groups of one name on different source arrays
        count as different groups.
      - Requires I(protection_group) with at least one name.
      - With other options set it is the newest of the snapshots they select.
      - Costs one request for each snapshot found, one more for each group
        and one more for each name of I(protection_group). I(limit) is
        applied after that.
      - A name with more than about 40 groups can make a request too long
        for Pure1, which fails the task. Select fewer with I(source_array)
        or I(array).
    type: bool
    default: false
  incomplete_only:
    description:
      - Only snapshots whose transfer has not finished, which are those with
        a progress below 1.0.
    type: bool
    default: false
  transfer:
    description:
      - Return the statistics of the transfer of each snapshot.
      - Costs one more request for every 1,000 snapshots returned. With
        I(latest_only) it is one for about every 40 snapshots.
    type: bool
    default: true
  limit:
    description:
      - The most snapshots to return, the newest first. Must be 0 or more.
        Pure1 gives up to 1,000 with one request, and an organization can
        hold tens of thousands.
      - With C(0) no snapshot is returned, only C(total) and
        C(missing_protection_groups).
      - Above 1,000 the snapshots are read with several requests, and a
        snapshot that Pure1 removes between two of them can leave another
        one out of the result.
    type: int
    default: 100
notes:
  - The Pure1 REST 1.5 calls this module makes,
    C(get_protection_group_snapshots) and
    C(get_protection_group_snapshots_transfer), came with py-pure-client
    1.78.0.
  - A snapshot created after the module started is not returned. That start
    time, I(window) and C(age_seconds) use the clock of the host the module
    runs on.
seealso:
  - module: everpure.pure1.pure1_replica_links
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the newest replicated protection group snapshots
  everpure.pure1.pure1_pgroup_snapshots:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: snapshots

- name: Show the snapshots stored on each array
  ansible.builtin.debug:
    msg: "{{ snapshots['pure1_pgroup_snapshots']['arrays'] }}"

- name: Collect the latest snapshot of two protection groups from the last day
  everpure.pure1.pure1_pgroup_snapshots:
    protection_group:
      - pg1
      - pg2
    window: 86400
    latest_only: true
  register: latest

- name: Check that both groups replicated in the last day
  ansible.builtin.assert:
    that:
      - latest['pure1_pgroup_snapshots']['missing_protection_groups'] | length == 0

- name: Check that the latest snapshot of each group arrived completely
  ansible.builtin.assert:
    that:
      - item['transfer'] is not none
      - item['transfer']['complete']
    quiet: true
  loop: "{{ latest['pure1_pgroup_snapshots']['arrays'] | dict2items | map(attribute='value') | flatten }}"

- name: Count the transfers to array1 of the last week that have not finished
  everpure.pure1.pure1_pgroup_snapshots:
    array:
      - array1
    window: 604800
    incomplete_only: true
    limit: 0
  register: unfinished

- name: Check that every transfer to array1 finished
  ansible.builtin.assert:
    that:
      - unfinished['pure1_pgroup_snapshots']['total'] == 0

- name: Collect the snapshots that array2 sent to array1 in the last hour, without transfers
  everpure.pure1.pure1_pgroup_snapshots:
    array:
      - array1
    source_array: array2
    window: 3600
    transfer: false
    limit: 1000
  register: sent

- name: Show how many snapshots array2 sent
  ansible.builtin.debug:
    msg: "{{ sent['pure1_pgroup_snapshots']['total'] }}"
"""

RETURN = r"""
pure1_pgroup_snapshots:
  description: The protection group snapshots collected from Pure1.
  returned: always
  type: dict
  contains:
    total:
      description:
        - The number of snapshots in Pure1 that the options select, null
          when Pure1 does not report it.
        - With I(latest_only) the number of newest snapshots found.
      type: int
    returned:
      description: The number of snapshots under C(arrays).
      type: int
    truncated:
      description:
        - Whether the module read fewer snapshots than the options select.
          Either I(limit) cut the result, or the snapshots in Pure1 changed
          while the module read them page by page.
        - A snapshot that is left out of the result with a warning does not
          make it true.
      type: bool
    missing_protection_groups:
      description:
        - The names of I(protection_group), as the task wrote them, without a
          snapshot among those that I(array), I(source_array) and I(window)
          select.
        - Empty when I(protection_group) is not set.
      type: list
      elements: str
    arrays:
      description:
        - The snapshots, as a dictionary keyed by the name of the array that
          stores them.
        - The value for an array is a list with one dictionary per snapshot,
          the newest first, with the keys listed here.
        - A key is null when Pure1 reports no value for it.
        - Empty when no snapshot is returned.
        - Where two arrays carry one name, the array of the newest snapshot
          keeps the name. The snapshots of the other one are left out, and
          the module gives a warning that counts them.
      type: dict
      contains:
        name:
          description:
            - Name of the snapshot on the array that stores it,
              C(source_array:group.suffix), or C(pod:group.suffix) for a
              protection group in a pod.
          type: str
        id:
          description: The Pure1 ID of the snapshot.
          type: str
        protection_group:
          description:
            - Name of the protection group on the source array, C(pod::group)
              for a group in a pod.
          type: str
        source_array:
          description:
            - Name of the array the snapshot was replicated from, taken from
              the name of the snapshot.
            - Null for a protection group in a pod.
          type: str
        pod:
          description: Name of the pod of the protection group, null when it is in no pod.
          type: str
        suffix:
          description: The part of the snapshot name after the protection group.
          type: str
        created:
          description: When the snapshot was created, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        created_epoch:
          description: When the snapshot was created, in milliseconds since the epoch.
          type: int
        age_seconds:
          description: Whole seconds between the creation of the snapshot and the start of the module.
          type: int
        as_of:
          description: When Pure1 last refreshed the snapshot, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        as_of_epoch:
          description: When Pure1 last refreshed the snapshot, in milliseconds since the epoch.
          type: int
        transfer:
          description:
            - The transfer of the snapshot from the source array.
            - Null when I(transfer) is false and when Pure1 has no transfer
              for the snapshot.
          type: dict
          contains:
            started:
              description: When the transfer started, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
              type: str
            started_epoch:
              description: When the transfer started, in milliseconds since the epoch.
              type: int
            completed:
              description:
                - When the transfer completed, in UTC, as C(YYYY-MM-DD HH:MM:SS UTC).
                - Null for a transfer that has not completed.
              type: str
            completed_epoch:
              description: When the transfer completed, in milliseconds since the epoch.
              type: int
            duration_seconds:
              description:
                - Whole seconds between the start and the completion of the
                  transfer, null when it has not completed.
              type: int
            data_transferred:
              description: The data sent from the source array to the target, in bytes.
              type: int
            progress:
              description: The part of the transfer that is done, from 0.0 to 1.0.
              type: float
            complete:
              description: Whether the transfer has a completion time and a progress of 1.0.
              type: bool
  sample:
    total: 2412
    returned: 2
    truncated: true
    missing_protection_groups: []
    arrays:
      array1:
        - name: array2:pg1.4721
          id: 6f2b7a1e-9c1d-4a53-8a0e-3d2c5b7f9e10:array2:pg1.4721:1612357445000
          protection_group: pg1
          source_array: array2
          pod: null
          suffix: '4721'
          created: 2021-02-03 13:04:05 UTC
          created_epoch: 1612357445000
          age_seconds: 610
          as_of: 2021-02-03 13:06:12 UTC
          as_of_epoch: 1612357572000
          transfer:
            started: 2021-02-03 13:04:06 UTC
            started_epoch: 1612357446000
            completed: 2021-02-03 13:04:48 UTC
            completed_epoch: 1612357488000
            duration_seconds: 42
            data_transferred: 73400320
            progress: 1.0
            complete: true
        - name: array3:pg2.118
          id: 6f2b7a1e-9c1d-4a53-8a0e-3d2c5b7f9e10:array3:pg2.118:1612353845000
          protection_group: pg2
          source_array: array3
          pod: null
          suffix: '118'
          created: 2021-02-03 12:04:05 UTC
          created_epoch: 1612353845000
          age_seconds: 4210
          as_of: 2021-02-03 13:06:12 UTC
          as_of_epoch: 1612357572000
          transfer:
            started: 2021-02-03 12:04:06 UTC
            started_epoch: 1612353846000
            completed: null
            completed_epoch: null
            duration_seconds: null
            data_transferred: 0
            progress: 0.0
            complete: false
"""


import time
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_is_one_of,
    pure1_keeps_name,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
    pure1_time,
)

# Newest first. The id makes the order total, which the pages after the first
# need: Pure1 gives no continuation token for a sorted listing.
NEWEST_FIRST = ["created-", "id"]
# The most bytes the ids take in the filter of one request, as they are sent.
# Pure1 refuses a request of more than about 6,300 bytes.
ID_FILTER_BYTES = 4000


def split_name(name):
    """Return the source and the suffix of the name of a snapshot

    A replicated snapshot is named source:group.suffix, where the source is
    the source array or, for a protection group in a pod, the pod. Each is
    None when the name does not have it.
    """
    if name is None:
        return None, None
    source, colon, rest = name.partition(":")
    if not colon:
        source, rest = None, name
    return source, rest.partition(".")[2] or None


def from_source_array(snapshot, source_array):
    """Tell whether a snapshot was replicated from the source array of the task"""
    source = split_name(pure1_attr(snapshot, "name"))[0]
    return (
        source is not None
        and source.lower() == source_array.lower()
        and pure1_attr(snapshot, "pod.name") is None
    )


def exclusion(snapshot):
    """Return the filter term that leaves out every snapshot of the source of this one"""
    pod = pure1_attr(snapshot, "pod.name")
    if pod is not None:
        return "not(pod.name={0})".format(filter_string(pod))
    source = split_name(pure1_attr(snapshot, "name"))[0]
    if source is None:
        return None
    return "not(contains(name,{0}))".format(filter_string(source + ":"))


def list_snapshots(module, pure_1, snapshot_filter, limit):
    return pure1_list(
        module,
        "get_protection_group_snapshots",
        lambda **page: pure_1.get_protection_group_snapshots(
            filter=snapshot_filter, sort=NEWEST_FIRST, **page
        ),
        limit=limit,
    )


def list_transfers(module, pure_1, snapshot_filter, limit):
    """Return the transfers of the snapshots that a filter selects, newest first

    Pure1 takes the filter and the sort of the snapshots for their transfers,
    and gives them in the same order.
    """
    return pure1_list(
        module,
        "get_protection_group_snapshots_transfer",
        lambda **page: pure_1.get_protection_group_snapshots_transfer(
            filter=snapshot_filter, sort=NEWEST_FIRST, **page
        ),
        limit=limit,
    ).items


def transfers_by_id(module, pure_1, ids):
    """Return the listing of the transfers of the snapshots with these ids"""
    id_filter = filter_in("id", ids)
    return pure1_list(
        module,
        "get_protection_group_snapshots_transfer",
        lambda **page: pure_1.get_protection_group_snapshots_transfer(
            filter=id_filter, **page
        ),
    )


def array_term(arrays):
    """Return the filter term of the arrays that the snapshots are stored on

    arrays are the arrays of the task as pure1_resolve_arrays() returns
    them, or None. Pure1 compares a name whatever its case, so the term is
    on the ids, which leave out another array whose name differs in case
    only. It is on the names only where Pure1 gave an array no id.
    """
    if not arrays:
        return None
    ids = [array["id"] for array in arrays]
    if all(ids):
        return filter_in("on.id", ids)
    return filter_in("on.name", [array["name"] for array in arrays])


def id_chunks(ids):
    """Split ids into lists that each fit in the filter of one request"""
    chunk = []
    size = 0
    for item_id in ids:
        # quoted, with a comma, as the request carries it
        cost = len(quote(filter_string(item_id), safe="")) + 3
        if chunk and size + cost > ID_FILTER_BYTES:
            yield chunk
            chunk = []
            size = 0
        chunk.append(item_id)
        size += cost
    if chunk:
        yield chunk


def leaving_out(field, values):
    """Return the filter term that leaves out the values of a field, None for no value

    Pure1 takes 20 comparisons in one filter and counts this term as one of
    them, however many values it has.
    """
    term = filter_in(field, values)
    return None if term is None else "not({0})".format(term)


def select_snapshots(module, pure_1, terms, limit, left_out=None):
    """Return the snapshots that the filter terms select, their listing and the filter

    The snapshots are the newest first, at most limit of them. Pure1 cannot
    match the start of a name, so the term of source_array also finds the
    snapshots of an array or a pod whose name ends as the source array does.
    Each such source is left out with a term of its own and the listing is
    read again, until all its snapshots are from the source array. Those
    terms are added to left_out, for a caller that asks again.
    """
    source_array = module.params["source_array"]
    if left_out is None:
        left_out = []
    while True:
        snapshot_filter = filter_and(*(terms + left_out))
        listing = list_snapshots(module, pure_1, snapshot_filter, limit)
        snapshots = []
        added = []
        seen = set()
        for snapshot in listing.items:
            snapshot_id = pure1_attr(snapshot, "id")
            if snapshot_id in seen:
                # Pure1 learned of a snapshot between two pages, which moved
                # the later page down by one
                continue
            if snapshot_id is not None:
                seen.add(snapshot_id)
            if (
                not source_array
                or pure1_attr(snapshot, "name") is None
                or from_source_array(snapshot, source_array)
            ):
                snapshots.append(snapshot)
                continue
            term = exclusion(snapshot)
            if term is not None and term not in left_out and term not in added:
                added.append(term)
        if not added:
            return snapshots, listing, snapshot_filter
        left_out.extend(added)


def newest_snapshot(module, pure_1, terms, left_out):
    """Return the newest snapshot that the filter terms select, None when there is none"""
    newest = select_snapshots(module, pure_1, terms, 1, left_out)[0]
    return newest[0] if newest else None


def latest_snapshots(module, pure_1, selection, unfinished, groups):
    """Return the newest snapshot of each protection group on each array

    A name can be the group of several source arrays, and one group can be
    replicated to several arrays. Pure1 has no listing of the newest of
    each, so the newest snapshot of a name is asked for again and again,
    each time without the source groups that are already found, and the
    newest snapshot of each source group without the arrays that are
    already found. What is found is left out with leaving_out(), which is
    one comparison for Pure1 however much is found. Groups and arrays are
    left out by their ids: a name can be the name of two of them.
    """
    snapshots = []
    # What source_array leaves out, kept for every request that follows
    left_out = []
    for group in groups:
        sources = []
        while True:
            newest = newest_snapshot(
                module,
                pure_1,
                selection
                + [
                    filter_in("source.name", group),
                    unfinished,
                    leaving_out("source.id", sources),
                ],
                left_out,
            )
            source = pure1_attr(newest, "source.id")
            if newest is None or source in sources:
                break
            if source is None:
                # Nothing to leave it out by: the next answer would be the same
                snapshots.append(newest)
                break
            sources.append(source)
            arrays = []
            while newest is not None:
                array = pure1_attr(newest, "on.id")
                if array in arrays:
                    break
                snapshots.append(newest)
                if array is None:
                    # Nothing to leave it out by
                    break
                arrays.append(array)
                newest = newest_snapshot(
                    module,
                    pure_1,
                    selection
                    + [
                        filter_in("source.id", source),
                        unfinished,
                        leaving_out("on.id", arrays),
                    ],
                    left_out,
                )
    return snapshots


def read_transfers(module, pure_1, snapshots, listed):
    """Return the transfer of each snapshot that has one, by the id of the snapshot

    listed are the transfers that were read with the listing of the
    snapshots. A snapshot that is not among them is asked for by its id.
    """
    transfers = {}
    for transfer in listed:
        transfers[pure1_attr(transfer, "id")] = transfer
    wanted = []
    for snapshot in snapshots:
        snapshot_id = pure1_attr(snapshot, "id")
        if snapshot_id is not None and snapshot_id not in transfers:
            wanted.append(snapshot_id)
    returned = 0
    total = 0
    truncated = False
    for chunk in id_chunks(wanted):
        listing = transfers_by_id(module, pure_1, chunk)
        for transfer in listing.items:
            transfers[pure1_attr(transfer, "id")] = transfer
        returned += len(listing.items)
        if total is not None:
            total = None if listing.total is None else total + listing.total
        truncated = truncated or listing.truncated
    if truncated:
        module.warn(
            "Pure1 returned {0} of {1} transfers of protection group snapshots: "
            "the result may be incomplete.".format(
                returned, "an unknown number of" if total is None else total
            )
        )
    transfers.pop(None, None)
    return transfers


def transfer_details(transfer):
    if transfer is None:
        return None
    started = pure1_attr(transfer, "started")
    completed = pure1_attr(transfer, "completed")
    progress = pure1_attr(transfer, "progress")
    duration = None
    if started is not None and completed is not None:
        duration = (completed - started) // 1000
    return {
        "started": pure1_time(started),
        "started_epoch": started,
        "completed": pure1_time(completed),
        "completed_epoch": completed,
        "duration_seconds": duration,
        "data_transferred": pure1_attr(transfer, "data_transferred"),
        "progress": progress,
        "complete": completed is not None and progress is not None and progress >= 1.0,
    }


def snapshot_details(snapshot, transfer, now):
    name = pure1_attr(snapshot, "name")
    source, suffix = split_name(name)
    pod = pure1_attr(snapshot, "pod.name")
    created = pure1_attr(snapshot, "created")
    as_of = pure1_attr(snapshot, "as_of")
    return {
        "name": name,
        "id": pure1_attr(snapshot, "id"),
        "protection_group": pure1_attr(snapshot, "source.name"),
        "source_array": source if pod is None else None,
        "pod": pod,
        "suffix": suffix,
        "created": pure1_time(created),
        "created_epoch": created,
        "age_seconds": None if created is None else (now - created) // 1000,
        "as_of": pure1_time(as_of),
        "as_of_epoch": as_of,
        "transfer": transfer_details(transfer),
    }


def file_snapshots(module, snapshots, transfers, now, arrays):
    """Return the snapshots as a dictionary keyed by the array that stores them

    arrays are the arrays of the task as pure1_resolve_arrays() returns
    them, or None: a snapshot that is stored on another array is not of the
    selection. Under a name are the snapshots of one array, the one of the
    newest snapshot of that name.
    """
    filed = {}
    owners = {}
    left_out = 0
    other_array = 0
    for snapshot in snapshots:
        array = pure1_attr(snapshot, "on.name")
        if array is None or pure1_attr(snapshot, "name") is None:
            left_out += 1
            continue
        if arrays is not None and not pure1_is_one_of(
            arrays, pure1_attr(snapshot, "on")
        ):
            continue
        if not pure1_keeps_name(owners, array, pure1_attr(snapshot, "on.id")):
            other_array += 1
            continue
        transfer = transfers.get(pure1_attr(snapshot, "id"))
        filed.setdefault(array, []).append(snapshot_details(snapshot, transfer, now))
    if left_out:
        module.warn(
            "Protection group snapshots that Pure1 returned without a name or "
            "without the array they are stored on are left out of the result: "
            "{0}.".format(left_out)
        )
    if other_array:
        module.warn(
            "Protection group snapshots that are stored on an array with the "
            "name of another array are left out of the result: "
            "{0}.".format(other_array)
        )
    return filed


def check_options(module):
    """Fail the task for a value that the argument spec cannot refuse"""
    limit = module.params["limit"]
    window = module.params["window"]
    source_array = module.params["source_array"]
    # The argument spec refuses a limit of null. Should one get past it, the
    # listing would have no end
    if limit is None or limit < 0:
        module.fail_json(msg="limit must be 0 or more, got {0}".format(limit))
    if window is not None and window < 1:
        module.fail_json(msg="window must be 1 or more, got {0}".format(window))
    if source_array is not None and not source_array.strip():
        module.fail_json(msg="The name of a source array must not be empty.")
    if module.params["latest_only"] and not module.params["protection_group"]:
        # as the argument spec words it for an option that is not set at all
        module.fail_json(
            msg="latest_only is True but all of the following are missing: "
            "protection_group"
        )


def generate_snapshots_dict(module, pure_1):
    check_options(module)
    limit = module.params["limit"]
    window = module.params["window"]
    source_array = module.params["source_array"]
    groups = []
    for group in module.params["protection_group"] or []:
        if group.lower() not in [known.lower() for known in groups]:
            groups.append(group)
    # Snapshots keep arriving, and none that is created from now on is read.
    # One that Pure1 learns of late still moves the pages and the transfers:
    # select_snapshots() and read_transfers() allow for that
    now = int(time.time() * 1000)
    arrays = pure1_resolve_arrays(module, pure_1, module.params["array"])
    selection = [
        array_term(arrays),
        (
            "contains(name,{0})".format(filter_string(source_array + ":"))
            if source_array
            else None
        ),
        # Pure1 refuses a time before 1970
        (
            "created>={0}".format(max(now - window * 1000, 0))
            if window is not None
            else None
        ),
        "created<{0}".format(now),
    ]
    unfinished = "progress<1.0" if module.params["incomplete_only"] else None
    listed = []
    if module.params["latest_only"]:
        read = latest_snapshots(module, pure_1, selection, unfinished, groups)
        # The newest first, and by id among those of one time, as Pure1 sorts
        read.sort(key=lambda snapshot: pure1_attr(snapshot, "id", ""))
        read.sort(key=lambda snapshot: pure1_attr(snapshot, "created", 0), reverse=True)
        snapshots = read[:limit]
        total = len(read)
        truncated = len(snapshots) < total
        # Every name was asked for
        complete = unfinished is None
    else:
        snapshots, listing, snapshot_filter = select_snapshots(
            module,
            pure_1,
            selection + [filter_in("source.name", groups), unfinished],
            limit,
        )
        read = snapshots
        total = listing.total
        truncated = listing.truncated
        # Every snapshot of the selection was read
        complete = unfinished is None and not truncated
        if module.params["transfer"] and listing.items:
            listed = list_transfers(module, pure_1, snapshot_filter, len(listing.items))
    known = set(pure1_attr(snapshot, "source.name", "").lower() for snapshot in read)
    missing = []
    for group in groups:
        if group.lower() in known:
            continue
        if (
            complete
            or not select_snapshots(
                module, pure_1, selection + [filter_in("source.name", group)], 1
            )[0]
        ):
            missing.append(group)
    transfers = {}
    if module.params["transfer"]:
        transfers = read_transfers(module, pure_1, snapshots, listed)
    filed = file_snapshots(module, snapshots, transfers, now, arrays)
    return {
        "total": total,
        "returned": sum(len(rows) for rows in filed.values()),
        "truncated": truncated,
        "missing_protection_groups": missing,
        "arrays": filed,
    }


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            source_array=dict(type="str"),
            protection_group=dict(type="list", elements="str"),
            window=dict(type="int"),
            latest_only=dict(type="bool", default=False),
            incomplete_only=dict(type="bool", default=False),
            transfer=dict(type="bool", default=True),
            limit=dict(type="int", default=100),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[["latest_only", True, ["protection_group"]]],
        supports_check_mode=True,
    )
    pure_1 = get_pure1(module)
    pure1_require(module, pure_1, "get_protection_group_snapshots", "1.78.0")
    pure1_require(module, pure_1, "get_protection_group_snapshots_transfer", "1.78.0")

    module.exit_json(
        changed=False, pure1_pgroup_snapshots=generate_snapshots_dict(module, pure_1)
    )


if __name__ == "__main__":
    main()
