# -*- coding: utf-8 -*-

# This code is part of Ansible, but is an independent component.
# This particular file snippet, and this file snippet only, is BSD licensed.
# Modules you write using this snippet, which is embedded dynamically by Ansible
# still belong to the author of the module, and may assign their own license
# to the complete work.
#
# Copyright (c), Simon Dodsley <simon@everpuredata.com>,2026
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Security insights of the arrays, as the security modules report them

Pure1 lists the security insights of every array, each with its type and
the time it was generated, and an insight that tells of CVEs carries their
ids. pure1_security_insights() reads the insights once and returns them both
ways: by array, and by CVE id with the arrays the CVE was found on.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

from ansible_collections.everpure.pure1.plugins.module_utils.pure1 import (
    filter_and,
    filter_in,
    pure1_attr,
    pure1_list,
    pure1_time,
)

# The insight types Pure1 documents, in the order of its documentation
INSIGHT_TYPES = (
    "appliance_not_phoning_home",
    "end_of_life_purity_version_in_use",
    "public_access_risk",
    "purity_optimizations_available",
    "unsafe_password_configuration",
    "remote_assist_longer_than_eight_hours",
    "remote_assist_longer_than_one_day",
    "open_critical_alerts_detected",
    "open_anomalies_detected",
    "data_protection_needs_review",
    "ntp_server_not_enabled",
    "remote_access_control_not_enabled",
    "critical_cve_detected",
    "high_cve_detected",
    "medium_cve_detected",
    "low_cve_detected",
)
# The severities of a CVE that Pure1 has an insight type for, the worst first
CVE_SEVERITIES = ("critical", "high", "medium", "low")
# The insight type that tells of the CVEs of a severity
CVE_INSIGHT_TYPES = {
    severity: severity + "_cve_detected" for severity in CVE_SEVERITIES
}
_CVE_TYPE_SEVERITY = {kind: severity for severity, kind in CVE_INSIGHT_TYPES.items()}


def _cve_order(cve_id):
    """Return what orders CVE ids by year and number, not letter by letter"""
    parts = re.split(r"([0-9]+)", cve_id)
    # Every second part is a number
    return [int(part) if index % 2 else part for index, part in enumerate(parts)]


def _insight_order(insight):
    """Return what orders the insights of an array: by type, the oldest first"""
    as_of = insight["as_of_epoch"]
    return (insight["type"], -1 if as_of is None else as_of)


def _entry(array_id, fqdn):
    """Return the entry of an array that has no insight yet"""
    return dict(
        id=array_id,
        fqdn=fqdn,
        insight_types=[],
        insights=[],
        cves={severity: [] for severity in CVE_SEVERITIES},
    )


def _given(selected, name, array_id):
    """Return the array given that an insight is of, or None

    selected has the records of the arrays given, by their names. The name
    must be that of a record and, where the insight and the record both
    have an id, the id as well. Of two records of one name the one with the
    id of the insight is returned.
    """
    by_name = None
    for array in selected.get(name, []):
        if array_id is not None and array["id"] == array_id:
            return array
        if by_name is None and None in (array_id, array["id"]):
            by_name = array
    return by_name


def _add_cves(cves, kind, data):
    """File the CVE ids an insight carries by severity

    cves are the lists of the array by severity, kind is the type of the
    insight and data its additional data. Returns the number of entries
    that could not be filed.
    """
    listed = data.get("cves") if isinstance(data, dict) else None
    if listed is None:
        return 0
    if not isinstance(listed, list):
        return 1
    left_out = 0
    for cve in listed:
        cve_id = None
        severity = None
        if isinstance(cve, dict):
            cve_id = cve.get("id")
            severity = cve.get("severity")
        if not isinstance(severity, str) or severity not in cves:
            # What the type of the insight says of every CVE it carries
            severity = _CVE_TYPE_SEVERITY.get(kind)
        if not isinstance(cve_id, str) or not cve_id or severity is None:
            left_out += 1
        elif cve_id not in cves[severity]:
            cves[severity].append(cve_id)
    return left_out


def _left_out(module, what, count):
    """Warn once of what could not be filed, with its number"""
    if count:
        module.warn("{0} are left out of the result: {1}.".format(what, count))


def pure1_security_insights(module, pure_1, arrays=None, types=None, keep_named=False):
    """Return the security insights of the arrays, by array and by CVE id

    One listing of get_assessment_security_insights_arrays through
    pure1_list(), unsorted, whatever is asked for. The method needs
    py-pure-client 1.91.0: the module checks for it with pure1_require()
    before its first request. A failed request fails the task with the line
    of the shared helpers.

    arrays is what pure1_resolve_arrays() returned: None for every array,
    else the arrays to report. Pure1 compares the name of an array exactly
    in this listing, also in its case, and answers a name it does not know
    with nothing. So the names of the records are sent, which are spelled
    as Pure1 spells them, never a name as the task wrote it. Of what comes
    back only the insights of the arrays given are kept: the name must be
    that of a record and, where the insight and the record both have an id,
    the id as well. An empty list stands for no array: nothing is requested
    and the result is empty.

    types is a list of insight types to return, one type as a string, or
    None or an empty list for every type. Pure1 compares a type exactly as
    well and answers a type it does not know with nothing, without an
    error: INSIGHT_TYPES has the types Pure1 documents, as the choices of a
    module option. For the insights that carry CVEs alone give the values
    of CVE_INSIGHT_TYPES, or those of the wanted severities. Types can only
    be selected, not left out: Pure1 does not apply not() to this listing
    and returns what the filter inside it selects.

    The result has two keys, both always there:

    arrays: {array name: {id, fqdn, insight_types, insights, cves}} in the
    order of the names, with the name as Pure1 spells it. An array is a key
    when at least one of its insights is returned: neither an array without
    insights nor, with types, one that has insights of other types only.
    With keep_named True every array of arrays is a key, one without a
    returned insight with empty lists and the id and the fqdn of its
    record. id and fqdn are otherwise those of the array references of the
    insights, and what the references lack is taken from the record of the
    array where arrays are given. Each is None where Pure1 sent none: an
    array without a fully qualified domain name has fqdn None.
    insight_types are the types of the returned insights of the array,
    each once, in order. insights: [{type,
    as_of, as_of_epoch}] in the order of type and time; as_of is the time
    Pure1 generated the insight ("YYYY-MM-DD HH:MM:SS UTC") and as_of_epoch
    the same in milliseconds since 1970, as Pure1 sent it, both None where
    it sent none. cves: {critical: [], high: [], medium: [], low: []},
    always these four keys (CVE_SEVERITIES): the ids of the CVEs that the
    returned insights of the array carry, each once, in the order of year
    and number. A CVE is under the severity its own entry names. An entry
    that names none of the four is under the severity of the type of its
    insight: every CVE of a high_cve_detected insight is high then. With
    types that leave the CVE insights out every list is empty.

    cves: {CVE id: [array names]} in the order of year and number: every
    CVE id under arrays with the names of the arrays it is listed for, in
    order. Its keys are the ids to look up with
    get_assessment_security_cves, which takes no filter on an array.

    Warnings, each given once and only when it applies:

    "Pure1 returned 31 of 32 security insights: the result may be
    incomplete." when the listing came short ("of an unknown number of"
    where Pure1 counted nothing).

    "Security insights that Pure1 returned without a type or without the
    name of their array are left out of the result: 2."

    "Security insights of an array that has the name of another array are
    left out of the result: 2." Two arrays can have one name. The array
    whose insight comes first keeps the name, and an insight under that
    name whose array reference has another id is left out. A reference
    without an id is to the array that has the name.

    "CVE entries of security insights that Pure1 returned without an id or
    without a severity are left out of the result: 2." An entry has no
    severity when it names none of the four and its insight is of no CVE
    type; a list of CVEs that is no list counts as one entry.
    """
    result = dict(arrays={}, cves={})
    selected = None
    if arrays is not None:
        if not arrays:
            return result
        selected = {}
        for array in arrays:
            selected.setdefault(array["name"], []).append(array)
    insight_filter = filter_and(
        filter_in("resource.name", None if selected is None else list(selected)),
        filter_in("type", types),
    )

    def list_insights(**page):
        return pure_1.get_assessment_security_insights_arrays(
            filter=insight_filter, **page
        )

    listing = pure1_list(
        module, "get_assessment_security_insights_arrays", list_insights
    )
    if listing.truncated:
        total = "an unknown number of" if listing.total is None else listing.total
        module.warn(
            "Pure1 returned {0} of {1} security insights: the result may be "
            "incomplete.".format(len(listing.items), total)
        )

    entries = {}
    unfiled = 0
    other_array = 0
    unread = 0
    for insight in listing.items:
        kind = pure1_attr(insight, "type")
        name = pure1_attr(insight, "resource.name")
        array_id = pure1_attr(insight, "resource.id")
        fqdn = pure1_attr(insight, "resource.fqdn")
        if kind is None or name is None:
            unfiled += 1
            continue
        if selected is not None:
            given = _given(selected, name, array_id)
            if given is None:
                continue
            # What the reference lacks is in the record of the array
            array_id = given["id"] if array_id is None else array_id
            fqdn = given["fqdn"] if fqdn is None else fqdn
        if name not in entries:
            entries[name] = _entry(array_id, fqdn)
        entry = entries[name]
        if None not in (entry["id"], array_id) and entry["id"] != array_id:
            other_array += 1
            continue
        # A reference can lack what another reference to the array has
        if entry["id"] is None:
            entry["id"] = array_id
        if entry["fqdn"] is None:
            entry["fqdn"] = fqdn
        as_of = pure1_attr(insight, "as_of")
        entry["insights"].append(
            dict(type=kind, as_of=pure1_time(as_of), as_of_epoch=as_of)
        )
        unread += _add_cves(entry["cves"], kind, pure1_attr(insight, "additional_data"))
    if keep_named:
        for array in arrays or []:
            if array["name"] not in entries:
                entries[array["name"]] = _entry(array["id"], array["fqdn"])

    found = {}
    for name in sorted(entries):
        entry = entries[name]
        entry["insights"].sort(key=_insight_order)
        entry["insight_types"] = sorted(
            {insight["type"] for insight in entry["insights"]}
        )
        for severity in CVE_SEVERITIES:
            entry["cves"][severity].sort(key=_cve_order)
            for cve_id in entry["cves"][severity]:
                on_arrays = found.setdefault(cve_id, [])
                if name not in on_arrays:
                    on_arrays.append(name)
        result["arrays"][name] = entry
    result["cves"] = {cve_id: found[cve_id] for cve_id in sorted(found, key=_cve_order)}

    _left_out(
        module,
        "Security insights that Pure1 returned without a type or without the "
        "name of their array",
        unfiled,
    )
    _left_out(
        module,
        "Security insights of an array that has the name of another array",
        other_array,
    )
    _left_out(
        module,
        "CVE entries of security insights that Pure1 returned without an id or "
        "without a severity",
        unread,
    )
    return result
