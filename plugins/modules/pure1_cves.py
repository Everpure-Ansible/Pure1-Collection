#!/usr/bin/python
# -*- coding: utf-8 -*-

# (c) 2026, Simon Dodsley (simon@everpuredata.com)
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r"""
---
module: pure1_cves
version_added: '1.6.0'
short_description: Collect CVEs and the arrays they affect from Pure1
description:
  - Returns the common vulnerabilities and exposures (CVEs) that Pure1 keeps
    a record of, each with its severity, its CVSS score, the link to its
    knowledge base article, the Purity releases and patches that fix it, and
    the arrays of the organization it affects with the listed fix releases
    above the release an array runs.
  - Use it to check that no array is affected by a CVE of a given severity,
    to plan the upgrade of an array, and to learn of the CVEs that Pure1
    recorded lately.
  - By default only the CVEs are returned that the security insights of
    Pure1 report for the arrays of the organization. Pure1 works these
    insights out periodically, so they can be some hours old. With
    I(affected_only) set to C(false) the catalogue of CVEs is read, which
    Pure1 keeps for every organization and which holds about ten thousand
    records.
options:
  array:
    description:
      - Names of the arrays to return the CVEs of, which are the CVEs that
        the security insights report for these arrays. All arrays when not
        set or empty.
      - A name is matched without regard to case and the result carries the
        name as Pure1 spells it. A name that Pure1 does not know fails the
        task.
      - The arrays that a returned CVE affects are then listed for these
        arrays only.
      - It cannot be given when I(affected_only) is C(false). Naming arrays
        costs one more request to Pure1.
    type: list
    elements: str
  name:
    description:
      - Ids of the CVEs to return, each in the form C(CVE-<year>-<number>),
        in upper or lower case. All CVEs that the other options select when
        not set or empty.
      - An id that is not of this form fails the task. An id that Pure1 has
        no record of, or that no security insight reports while
        I(affected_only) is C(true), is not an error and is not returned.
      - With I(affected_only) set to C(false), Pure1 is asked once for every
        100 ids.
    type: list
    elements: str
  severity:
    description:
      - Only CVEs of these severities. Every severity when not set or
        empty.
      - The severity is that of the record Pure1 keeps of the CVE, also for
        the CVEs that the security insights report.
      - C(none) is the severity of a CVE that was not rated. It can be
        given only when I(affected_only) is C(false), because the security
        insights report no CVE of that severity.
    type: list
    elements: str
    choices: [ critical, high, medium, low, none ]
  min_cvss:
    description:
      - Only CVEs with a CVSS score of this value or more, from C(0) to
        C(10).
      - A CVE without a score is then not returned, whatever the value.
        About one CVE in five has no score, the newest among them. Select
        with I(severity) to be told of new CVEs.
    type: float
  affected_only:
    description:
      - With C(true) only the CVEs are returned that the security insights
        of Pure1 report for the arrays of the organization.
      - With C(false) the CVEs come from the catalogue that Pure1 keeps for
        every organization, whether they affect an array or not. At least
        one of I(name), I(severity), I(min_cvss), I(window) and I(limit)
        must then be given.
    type: bool
    default: true
  window:
    description:
      - Only CVEs that Pure1 created a record of in the last I(window)
        seconds, by the clock of the host the module runs on. Must be 1 or
        more.
      - Pure1 dates the record of a CVE to the day, at midnight UTC. This is
        the date of the record, not the time an array became affected.
    type: int
  include_affected_versions:
    description:
      - Whether to return the Purity releases that each CVE affects as
        C(affected_versions). The list can hold hundreds of releases for one
        CVE.
    type: bool
    default: false
  limit:
    description:
      - The most CVEs to return, the worst first. Must be 0 or more. Every
        selected CVE when not set.
      - With C(0) no CVE is returned, only C(total).
      - The CVEs are put in their order by the module, so Pure1 is asked for
        every selected CVE unless the limit is C(0). A listing of the
        catalogue without I(name) costs one request for every 1000 selected
        CVEs.
    type: int
notes:
  - This module requires py-pure-client 1.91.0 or later, the first release
    with the C(get_assessment_security_cves) and
    C(get_assessment_security_insights_arrays) methods of Pure1 REST 1.5.
  - Besides the CVEs the module reads the security insights of the arrays,
    with one request. The CVEs that the insights report are looked up with
    one request for every 100 CVEs.
  - With I(affected_only) set to C(true), a CVE that a security insight
    reports and that Pure1 has no record of is left out of the result and
    is not counted. A warning tells of it, also when I(severity) alone
    selects and an insight reports the CVE with one of the severities. With
    I(min_cvss) or I(window), and with a I(limit) of C(0), such a CVE cannot
    be told from one that the options leave out, and no warning is given.
author:
  - Everpure Ansible Team (@sdodsley) <pure-ansible-team@everpuredata.com>
extends_documentation_fragment:
  - everpure.pure1.everpure.p1
"""

EXAMPLES = r"""
- name: Collect the CVEs that Pure1 reports for the arrays
  everpure.pure1.pure1_cves:
    app_id: pure1:apikey:dssf2331sd
    key_file: /home/user/private.pem
  register: affected

- name: Show each CVE with the arrays it affects
  ansible.builtin.debug:
    msg: "{{ item.key }} ({{ item.value.severity }}): {{ item.value.affected_arrays | map(attribute='name') | join(', ') }}"
  loop: "{{ affected['pure1_cves']['cves'] | dict2items }}"

- name: Collect the high and critical CVEs of two arrays
  everpure.pure1.pure1_cves:
    array:
      - array1
      - array2
    severity:
      - critical
      - high
  register: serious

- name: Check that neither array is affected by a high or critical CVE
  ansible.builtin.assert:
    that:
      - serious['pure1_cves']['total'] == 0
    fail_msg: "The arrays are affected by {{ serious['pure1_cves']['cves'] | list | join(', ') }}"

- name: Collect the CVEs of one array with a score of 7 or more and the releases they affect
  everpure.pure1.pure1_cves:
    array:
      - array1
    min_cvss: 7.0
    include_affected_versions: true
  register: planned

- name: Check that the release to upgrade to is affected by none of them
  ansible.builtin.assert:
    that:
      - "'6.7.4' not in item.value.affected_versions['Purity//FA']"
    fail_msg: "Purity//FA 6.7.4 is affected by {{ item.key }}"
  loop: "{{ planned['pure1_cves']['cves'] | dict2items }}"

- name: Collect the ten worst CVEs that Pure1 recorded in the last seven days
  everpure.pure1.pure1_cves:
    affected_only: false
    window: 604800
    limit: 10
  register: recent

- name: Show the ids of the new CVEs
  ansible.builtin.debug:
    msg: "{{ recent['pure1_cves']['cves'] | list }}"
  when: recent['pure1_cves']['returned'] > 0

- name: Look two CVEs up in the catalogue of Pure1
  everpure.pure1.pure1_cves:
    affected_only: false
    name:
      - CVE-2021-44228
      - CVE-2024-3094
  register: looked_up

- name: Show the releases that fix each of them
  ansible.builtin.debug:
    msg: "{{ item.key }}: {{ item.value.fixed_in_versions }}"
  loop: "{{ looked_up['pure1_cves']['cves'] | dict2items }}"
"""

RETURN = r"""
pure1_cves:
  description: The CVEs that the options select, with what Pure1 records of each.
  returned: always
  type: dict
  contains:
    total:
      description:
        - The number of CVEs in Pure1 that the options select.
      type: int
    returned:
      description: The number of CVEs under C(cves).
      type: int
    truncated:
      description:
        - Whether fewer CVEs are returned than the options select. That is
          so because of I(limit), and when CVEs that Pure1 counted are left
          out of the result or did not arrive, which a warning then says.
      type: bool
    cves:
      description:
        - The CVEs, as a dictionary keyed by the id of the CVE. The value
          for a CVE is a dictionary with the keys listed here.
        - The CVEs are in the order of their severity, the worst first, and
          within one severity in the order of their score, the highest
          first and the CVEs without a score last.
        - A key is null when Pure1 reports no value for it.
        - Empty when no CVE is selected.
      type: dict
      contains:
        severity:
          description:
            - The severity of the CVE, one of C(critical), C(high),
              C(medium), C(low) and C(none).
          type: str
        cvss_score:
          description:
            - The CVSS score of the CVE, from 0 to 10. Null for a CVE that
              has no score.
          type: float
        description:
          description: What the CVE is about.
          type: str
        kb_article:
          description: The link to the knowledge base article of the CVE.
          type: str
        detected:
          description:
            - The day Pure1 created its record of the CVE, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        detected_epoch:
          description:
            - The day Pure1 created its record of the CVE, in milliseconds
              since the epoch.
          type: int
        updated:
          description:
            - The time Pure1 last changed its record of the CVE, as
              C(YYYY-MM-DD HH:MM:SS UTC).
          type: str
        updated_epoch:
          description:
            - The time Pure1 last changed its record of the CVE, in
              milliseconds since the epoch.
          type: int
        fixed_in_versions:
          description:
            - The Purity releases that Pure1 lists as fixing the CVE, as a
              dictionary keyed by operating system. The value for an
              operating system is the list of its releases, the lowest
              first.
            - The keys C(Purity//FA) and C(Purity//FB) are always there,
              each with an empty list when Pure1 lists no release.
            - Pure1 names a few releases only and not the releases that
              follow them, so a release that is not in the list can have the
              fix as well.
          type: dict
        fixed_in_patches:
          description:
            - The names of the patches that fix the CVE, as a dictionary
              keyed by operating system, as C(fixed_in_versions) is.
          type: dict
        affected_arrays:
          description:
            - The arrays of the organization that the CVE affects, in the
              order of their names. These are the arrays that the record of
              the CVE lists and the arrays that a security insight reports
              the CVE for.
            - With I(array) only the arrays named there are listed.
            - Empty when the CVE affects no array.
          type: list
          elements: dict
          contains:
            name:
              description: The name of the array.
              type: str
            id:
              description: The id of the array in Pure1.
              type: str
            fqdn:
              description:
                - The fully qualified domain name of the array. Null when
                  Pure1 has none.
              type: str
            os:
              description:
                - The operating system of the array, C(Purity//FA) or
                  C(Purity//FB). Null for an array that only a security
                  insight reports the CVE for.
              type: str
            version:
              description:
                - The Purity release the array runs. Null for an array that
                  only a security insight reports the CVE for.
              type: str
            fixed_in_later_versions:
              description:
                - The releases of C(fixed_in_versions) for the operating
                  system of the array that are above the release the array
                  runs, the lowest first.
                - Empty when there is none, and when the operating system
                  or the release of the array is not known.
              type: list
              elements: str
            in_insights:
              description:
                - Whether a security insight of the array reports the CVE.
                  C(false) for an array that only the record of the CVE
                  lists.
              type: bool
        affected_versions:
          description:
            - The Purity releases that the CVE affects, as a dictionary
              keyed by operating system, as C(fixed_in_versions) is.
          returned: when I(include_affected_versions) is C(true)
          type: dict
  sample:
    total: 2
    returned: 1
    truncated: true
    cves:
      CVE-2023-52001:
        severity: medium
        cvss_score: 4.5
        description: A use-after-free in a file system driver allows a local denial of service.
        kb_article: https://support.purestorage.com/bundle/z-kb-articles-cve/page/CVE-2023-52001.html
        detected: 2024-06-13 00:00:00 UTC
        detected_epoch: 1718236800000
        updated: 2026-11-23 13:34:57 UTC
        updated_epoch: 1795440897000
        fixed_in_versions:
          Purity//FA:
            - 6.5.14
            - 6.7.9
            - 6.9.8
            - 6.10.7
            - 6.12.0
          Purity//FB: []
        fixed_in_patches:
          Purity//FA: []
          Purity//FB: []
        affected_arrays:
          - name: array1
            id: 0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d
            fqdn: array1.example.com
            os: Purity//FA
            version: 6.10.6
            fixed_in_later_versions:
              - 6.10.7
              - 6.12.0
            in_insights: true
"""


import re
import time
from decimal import ROUND_CEILING, Decimal

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
    pure1_time,
)
from ansible_collections.everpure.pure1.plugins.module_utils.security import (
    CVE_INSIGHT_TYPES,
    CVE_SEVERITIES,
    pure1_security_insights,
)

# The severities of a CVE, the worst first. A CVE that was not rated is none.
SEVERITIES = ("critical", "high", "medium", "low", "none")
# The form of the id of a CVE: its year and a number of four digits or more
CVE_ID = re.compile(r"CVE-[0-9]{4}-[0-9]{4,}")
# Pure1 refuses a request with a few hundred ids in its filter
IDS_PER_REQUEST = 100
# The operating systems Pure1 lists releases and patches for
OPERATING_SYSTEMS = ("Purity//FA", "Purity//FB")
# The client methods the module needs, with the py-pure-client release that
# has them first
REQUIRED_METHODS = (
    ("get_assessment_security_cves", "1.91.0"),
    ("get_assessment_security_insights_arrays", "1.91.0"),
)


def check_options(module):
    """Fail the task for options that cannot be met, before any request

    Returns the ids of I(name) in upper case, each once, or None when the
    option is not set or empty.
    """
    params = module.params
    limit = params["limit"]
    window = params["window"]
    min_cvss = params["min_cvss"]
    if limit is not None and limit < 0:
        module.fail_json(msg="limit must be 0 or more, got {0}".format(limit))
    if window is not None and window < 1:
        module.fail_json(msg="window must be 1 or more, got {0}".format(window))
    if min_cvss is not None and not 0 <= min_cvss <= 10:
        module.fail_json(
            msg="min_cvss must be between 0 and 10, got {0}".format(min_cvss)
        )
    names = None
    if params["name"]:
        names = []
        refused = []
        for name in params["name"]:
            cve_id = name.strip().upper()
            if CVE_ID.fullmatch(cve_id) is None:
                refused.append(name)
            elif cve_id not in names:
                names.append(cve_id)
        if refused:
            module.fail_json(
                msg="name must give the ids of CVEs as CVE-<year>-<number>, "
                "got: {0}".format(", ".join(refused))
            )
    if params["affected_only"]:
        if "none" in (params["severity"] or []):
            module.fail_json(
                msg="severity none can be given only with affected_only false: "
                "the security insights of the arrays report no CVE of that "
                "severity."
            )
    elif params["array"]:
        module.fail_json(
            msg="array cannot be given with affected_only false: the CVE "
            "catalogue of Pure1 is not kept by array."
        )
    elif not (
        names
        or params["severity"]
        or min_cvss is not None
        or window is not None
        or limit is not None
    ):
        module.fail_json(
            msg="affected_only false reads the CVE catalogue that Pure1 keeps "
            "for every organization: give at least one of name, severity, "
            "min_cvss, window and limit with it."
        )
    return names


def natural_order(text):
    """Return what orders releases and ids by their numbers: 6.9.8 before 6.10.7"""
    parts = re.split(r"([0-9]+)", text)
    # Every second part is a number
    return [int(part) if index % 2 else part for index, part in enumerate(parts)]


def score_bound(min_cvss):
    """Return the number to send to Pure1 for the lowest score wanted

    Pure1 compares a score with the number of a filter as if it read the
    number in single precision. There 42 of the scores with one decimal,
    9.8 and 8.8 among them, are a little above themselves, and
    cvss_score>=9.8 leaves out the CVEs whose score is 9.8. A CVSS score
    has one decimal, so the number sent lies half a step below the lowest
    such score that meets min_cvss, 9.75 for 9.8: no rounding moves it
    across a score. It is worked out in decimals, from min_cvss as it is
    written, so that it is exact. Pure1 refuses a negative number in a
    filter: 0 stays 0, which every score meets.
    """
    tenths = (Decimal(repr(min_cvss)) * 10).to_integral_value(rounding=ROUND_CEILING)
    hundredths = max(0, int(tenths) * 10 - 5)
    return "{0}.{1:02d}".format(*divmod(hundredths, 100))


def worst_first(cve):
    """Return what orders CVEs: by severity, then score, those without one last"""
    severity = pure1_attr(cve, "severity")
    score = pure1_attr(cve, "cvss_score")
    rank = SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)
    return (
        rank,
        score is None,
        -float(score or 0),
        natural_order(pure1_attr(cve, "id")),
    )


def list_cves(module, pure_1, cve_filter, limit):
    """List the CVEs a filter selects, in the order of their ids

    The listing is sorted, so that pure1_list() reads its pages by offset.
    Read by continuation token, the pages after the first repeat CVEs of
    the pages before them and leave as many out: 4075 CVEs came as 2868
    different ones. Every CVE has an id and no two share one, which makes
    the pages of the sorted listing disjoint.
    """

    def request(**page):
        return pure_1.get_assessment_security_cves(
            filter=cve_filter, sort=["id"], **page
        )

    return pure1_list(module, "get_assessment_security_cves", request, limit=limit)


def read_cves(module, pure_1, terms, ids):
    """Return the CVEs that the filter terms select, and how many Pure1 counted

    ids are the ids to look up, in requests of at most IDS_PER_REQUEST ids,
    or None for one listing of every CVE that the terms select. With a limit
    of 0 Pure1 is asked for the number alone.
    """
    count_only = module.params["limit"] == 0
    found = []
    counted = 0
    short = False
    if ids is None:
        chunks = [None]
    else:
        chunks = [
            ids[start : start + IDS_PER_REQUEST]
            for start in range(0, len(ids), IDS_PER_REQUEST)
        ]
    for chunk in chunks:
        listing = list_cves(
            module,
            pure_1,
            filter_and(filter_in("id", chunk), *terms),
            0 if count_only else None,
        )
        found.extend(listing.items)
        short = short or listing.truncated
        if counted is not None:
            counted = None if listing.total is None else counted + listing.total
    if short and not count_only:
        module.warn(
            "Pure1 returned {0} of {1} CVEs: the result may be incomplete.".format(
                len(found), "an unknown number of" if counted is None else counted
            )
        )
    return found, max(counted or 0, len(found))


def by_os(entries, field):
    """Return the releases or the patches of a CVE by operating system

    entries are the entries of a list of the CVE and field the field that
    holds the release or the name of the patch. Returns the dictionary and
    the number of entries that could not be filed.
    """
    found = {os_name: [] for os_name in OPERATING_SYSTEMS}
    left_out = 0
    for entry in entries:
        os_name = pure1_attr(entry, "os")
        value = pure1_attr(entry, field)
        if os_name is None or value is None:
            left_out += 1
        elif value not in found.setdefault(os_name, []):
            found[os_name].append(value)
    for values in found.values():
        values.sort(key=natural_order)
    return found, left_out


def is_selected(arrays, name, array_id):
    """Tell whether an array that a CVE lists is one of the arrays of the task

    The name must be that of one of them and, where both have an id, the id
    as well: two arrays can have one name.
    """
    return any(
        array["name"] == name
        and (array_id is None or array["id"] is None or array["id"] == array_id)
        for array in arrays
    )


def affected_arrays(cve_id, cve, fixed, insights, arrays):
    """Return the arrays a CVE affects, and how many could not be filed

    These are the arrays the record of the CVE lists and the arrays that the
    security insights report the CVE for. fixed are the releases that fix
    the CVE by operating system, and arrays the arrays of the task, or None
    for every array.
    """
    reported = insights["cves"].get(cve_id, [])
    on_insight = set(reported)
    rows = []
    listed = set()
    in_both = set()
    # What orders the fix releases of an operating system, worked out once
    # for all the arrays of the CVE
    fix_orders = {}
    left_out = 0
    for resource in pure1_attr(cve, "affected_resources", []):
        name = pure1_attr(resource, "name")
        array_id = pure1_attr(resource, "id")
        if name is None:
            left_out += 1
            continue
        if (name, array_id) in listed:
            # Pure1 can list one array twice
            continue
        if arrays is not None and not is_selected(arrays, name, array_id):
            continue
        listed.add((name, array_id))
        os_name = pure1_attr(resource, "os")
        version = pure1_attr(resource, "version")
        later = []
        if os_name is not None and version is not None:
            if os_name not in fix_orders:
                fix_orders[os_name] = [
                    (natural_order(release), release)
                    for release in fixed.get(os_name, [])
                ]
            running = natural_order(version)
            later = [
                release for order, release in fix_orders[os_name] if order > running
            ]
        # The array of an insight is told by its name and, where the
        # insight and the record both have an id, by its id as well
        insight_id = insights["arrays"][name]["id"] if name in on_insight else None
        in_insights = name in on_insight and (
            array_id is None or insight_id is None or insight_id == array_id
        )
        if in_insights:
            in_both.add(name)
        rows.append(
            {
                "name": name,
                "id": array_id,
                "fqdn": pure1_attr(resource, "fqdn"),
                "os": os_name,
                "version": version,
                "fixed_in_later_versions": later,
                "in_insights": in_insights,
            }
        )
    for name in reported:
        if name not in in_both:
            # Only an insight tells of this array
            rows.append(
                {
                    "name": name,
                    "id": insights["arrays"][name]["id"],
                    "fqdn": insights["arrays"][name]["fqdn"],
                    "os": None,
                    "version": None,
                    "fixed_in_later_versions": [],
                    "in_insights": True,
                }
            )
    rows.sort(key=lambda row: (row["name"], row["id"] or ""))
    return rows, left_out


def cve_row(cve_id, cve, insights, arrays, include_affected_versions):
    """Return the result of a CVE and what of it could not be filed

    The second value is the number of releases and patches without an
    operating system or without a name, the third the number of affected
    arrays without a name.
    """
    detected = pure1_attr(cve, "detected_at")
    updated = pure1_attr(cve, "updated_at")
    fixed, unfiled = by_os(pure1_attr(cve, "fixed_in_versions", []), "version")
    patches, left_out = by_os(pure1_attr(cve, "fixed_in_patches", []), "name")
    unfiled += left_out
    on_arrays, unnamed = affected_arrays(cve_id, cve, fixed, insights, arrays)
    row = {
        "severity": pure1_attr(cve, "severity"),
        "cvss_score": pure1_attr(cve, "cvss_score"),
        "description": pure1_attr(cve, "description"),
        "kb_article": pure1_attr(cve, "kb_article"),
        "detected": pure1_time(detected),
        "detected_epoch": detected,
        "updated": pure1_time(updated),
        "updated_epoch": updated,
        "fixed_in_versions": fixed,
        "fixed_in_patches": patches,
        "affected_arrays": on_arrays,
    }
    if include_affected_versions:
        row["affected_versions"], left_out = by_os(
            pure1_attr(cve, "affected_versions", []), "version"
        )
        unfiled += left_out
    return row, unfiled, unnamed


def left_out_warning(module, what, count):
    if count:
        module.warn("{0} are left out of the result: {1}.".format(what, count))


def reported_with(insights, severities):
    """Return the ids of the CVEs that the insights report with one of the severities"""
    return {
        cve_id
        for entry in insights["arrays"].values()
        for severity in severities
        for cve_id in entry["cves"].get(severity, [])
    }


def generate_cves_dict(module, pure_1, names):
    params = module.params
    limit = params["limit"]
    cve_types = [CVE_INSIGHT_TYPES[severity] for severity in CVE_SEVERITIES]
    result = {"total": 0, "returned": 0, "truncated": False, "cves": {}}
    terms = [filter_in("severity", params["severity"])]
    if params["min_cvss"] is not None:
        terms.append("cvss_score>={0}".format(score_bound(params["min_cvss"])))
    if params["window"] is not None:
        now = int(time.time() * 1000)
        # Pure1 refuses a negative number in a filter: a window that reaches
        # back before 1970 selects every CVE
        since = max(0, now - params["window"] * 1000)
        terms.append("detected_at>={0}".format(since))

    arrays = None
    insights = None
    ids = names
    if params["affected_only"]:
        arrays = pure1_resolve_arrays(module, pure_1, params["array"])
        insights = pure1_security_insights(
            module, pure_1, arrays=arrays, types=cve_types
        )
        # Without an id nothing is asked for
        ids = [
            cve_id
            for cve_id in insights["cves"]
            if names is None or cve_id.upper() in names
        ]
    found, total = read_cves(module, pure_1, terms, ids)

    cves = {}
    no_id = 0
    same_id = 0
    for cve in found:
        cve_id = pure1_attr(cve, "id")
        if cve_id is None:
            no_id += 1
        elif cve_id in cves:
            same_id += 1
        else:
            cves[cve_id] = cve
    left_out_warning(module, "CVEs that Pure1 returned without an id", no_id)
    if same_id:
        module.warn(
            "Pure1 returned {0} CVEs under the id of another CVE: of each such "
            "id only the CVE returned first is in the result.".format(same_id)
        )
    if params["affected_only"] and limit != 0:
        missing = [cve_id for cve_id in ids if cve_id not in cves]
        if not any(terms):
            # Nothing but the ids selects, so that every id should have its
            # record
            left_out_warning(
                module,
                "CVEs that the security insights report and Pure1 has no record of",
                len(missing),
            )
        elif params["min_cvss"] is None and params["window"] is None:
            # Only the severity selects besides the ids: a CVE that an
            # insight reports with one of the severities should have come
            expected = reported_with(insights, params["severity"])
            left_out_warning(
                module,
                "CVEs that the security insights report with a severity that is "
                "asked for and Pure1 has no record of with such a severity",
                len([cve_id for cve_id in missing if cve_id in expected]),
            )

    ordered = sorted(cves, key=lambda cve_id: worst_first(cves[cve_id]))
    if limit is not None:
        ordered = ordered[:limit]
    if ordered and insights is None:
        insights = pure1_security_insights(module, pure_1, types=cve_types)
    unfiled = 0
    unnamed = 0
    for cve_id in ordered:
        result["cves"][cve_id], left_out, without_name = cve_row(
            cve_id, cves[cve_id], insights, arrays, params["include_affected_versions"]
        )
        unfiled += left_out
        unnamed += without_name
    left_out_warning(
        module,
        "Releases and patches of CVEs that Pure1 returned without an operating "
        "system or without a name",
        unfiled,
    )
    left_out_warning(
        module, "Affected arrays that Pure1 returned without a name", unnamed
    )
    result["total"] = total
    result["returned"] = len(ordered)
    result["truncated"] = len(ordered) < total
    return result


def main():
    argument_spec = pure1_argument_spec()
    argument_spec.update(
        dict(
            array=dict(type="list", elements="str"),
            name=dict(type="list", elements="str"),
            severity=dict(type="list", elements="str", choices=list(SEVERITIES)),
            min_cvss=dict(type="float"),
            affected_only=dict(type="bool", default=True),
            window=dict(type="int"),
            include_affected_versions=dict(type="bool", default=False),
            limit=dict(type="int"),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    names = check_options(module)
    pure_1 = get_pure1(module)
    for method, minimum in REQUIRED_METHODS:
        pure1_require(module, pure_1, method, minimum)

    module.exit_json(
        changed=False, pure1_cves=generate_cves_dict(module, pure_1, names)
    )


if __name__ == "__main__":
    main()
