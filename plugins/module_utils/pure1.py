# -*- coding: utf-8 -*-

# This code is part of Ansible, but is an independent component.
# This particular file snippet, and this file snippet only, is BSD licensed.
# Modules you write using this snippet, which is embedded dynamically by Ansible
# still belong to the author of the module, and may assign their own license
# to the complete work.
#
# Copyright (c), Simon Dodsley <simon@everpuredata.com>,2021
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

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import traceback

PYPURECLIENT_IMP_ERR = None
HAS_PYPURECLIENT = True
try:
    from pypureclient import pure1
except ImportError:
    HAS_PYPURECLIENT = False
    PYPURECLIENT_IMP_ERR = traceback.format_exc()

import collections
import functools
import inspect
import itertools
import json
import os
import platform
import re
import shutil
import tempfile
import time

from ansible.module_utils.basic import env_fallback, missing_required_lib
from ansible.module_utils.errors import AnsibleFallbackNotFound

try:
    from http.client import responses as HTTP_RESPONSES
except ImportError:
    HTTP_RESPONSES = {}

VERSION = 1.0
USER_AGENT_BASE = "Ansible"
ERROR_TEXT_LIMIT = 500
# A PEM block and a JSON web token (ID token, bearer token) are never reported
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN.*"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}[A-Za-z0-9_.=-]*"),
)
# What OpenSSL calls a certificate it could not verify. The words are in the
# error whatever urllib3 or py-pure-client wrap it in.
CERTIFICATE_FAILURE = "CERTIFICATE_VERIFY_FAILED"
# The reason given for a request whose target is too long for Pure1. Pure1
# takes a request line of up to 8192 bytes, and refuses a shorter one too
# when it is too long together with its headers. What makes a request long
# are the names, the IDs or the protection groups that a task gives.
REQUEST_TOO_LONG = (
    "The request is too long for Pure1: give fewer names or IDs in one task."
)
# Past 8192 bytes Pure1 answers HTTP 414, which py-pure-client raises as a
# PureError, also from the request for a later page
URI_TOO_LONG = re.compile(
    r"PureError: (Failed to collect more items: PureError: )?\(414\)"
)
# Below that the answer is HTTP 400 with a body that is no JSON, for which
# py-pure-client gives one error with this message. A request that Pure1
# refuses for what it asks has a JSON body, with a message of Pure1.
NOT_JSON = "Response is not a valid JSON"
# The same answer to the request for a later page is raised out of the
# iteration, as a PureError with the status, the headers and the body
LATER_PAGE_400 = "PureError: Failed to collect more items: (400)\n"


class Pure1RequestError(Exception):
    """A Pure1 request that failed

    status_code is the HTTP status of an error answer. It is None when the
    SDK raised an error instead of answering, except for a request that is
    too long, where it is the status Pure1 gave (414, or 400 for a later
    page). text is the reason as one bounded line with no secret in it, and
    str() the message of the failed task.
    """

    def __init__(self, action, status_code, text):
        super(Pure1RequestError, self).__init__(action, status_code, text)
        self.action = action
        self.status_code = status_code
        self.text = text

    def __str__(self):
        if self.status_code is None:
            return "Pure1 request %s failed: %s" % (self.action, self.text)
        return "Pure1 request %s failed (HTTP %s): %s" % (
            self.action,
            self.status_code,
            self.text,
        )


def _condense(module, text):
    """Return an error text as one bounded line with no secret in it"""
    # The SDK appends the response headers and body to a failed request
    text = str(text).split("HTTP response headers:", 1)[0]
    text = text.split("HTTP response body:", 1)[0]
    # and the whole body to a token response it cannot parse
    text = "".join(text.partition("Unable to parse response.")[:2])
    for secret in (
        module.params.get("password"),
        os.environ.get("PURE1_PRIVATE_PASSWORD"),
        os.environ.get("PURE1_PRIVATE_KEY_PASSWORD"),
    ):
        if secret:
            text = text.replace(secret, "********")
    text = " ".join(text.split())
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("********", text)
    if len(text) > ERROR_TEXT_LIMIT:
        text = text[:ERROR_TEXT_LIMIT] + "..."
    return text


def _too_long_status(error):
    """Return the HTTP status in a raised error that tells of a request too long

    That is HTTP 414, and HTTP 400 for a later page with a body that is no
    JSON, which is what py-pure-client goes by for the first page. None for
    any other error.
    """
    if type(error).__name__ != "PureError":
        return None
    text = str(error)
    if URI_TOO_LONG.match(text):
        return 414
    if text.startswith(LATER_PAGE_400):
        try:
            json.loads(text.partition("\nHTTP response body: ")[2])
        except ValueError:
            return 400
    return None


def pure1_error_text(module, error):
    """Return the type of an exception and its text as one bounded line

    The line holds no password, token or key. A Pure1RequestError gives its
    own message, and an error py-pure-client raises for a request that is
    too long (HTTP 414, or HTTP 400 with no JSON for a later page) gives
    REQUEST_TOO_LONG.
    """
    if isinstance(error, Pure1RequestError):
        return str(error)
    if _too_long_status(error) is not None:
        return REQUEST_TOO_LONG
    name = type(error).__name__
    text = _condense(module, error)
    # PureError already starts its text with its own name
    if not text.startswith(name + ":"):
        text = "%s: %s" % (name, text) if text else name
    return text


def _response_message(module, res):
    """Return the messages of an error response or its HTTP status text"""
    texts = []
    errors = getattr(res, "errors", None) or []
    if (
        res.status_code == 400
        and len(errors) == 1
        and getattr(errors[0], "message", None) == NOT_JSON
        and not getattr(errors[0], "context", None)
    ):
        return REQUEST_TOO_LONG
    for error in errors:
        # Pure1 puts what is specific to a 400 or a 404 in the context
        parts = [getattr(error, "message", None), getattr(error, "context", None)]
        parts = [_condense(module, part) for part in parts if part]
        text = ": ".join(part for part in parts if part)
        if text and text not in texts:
            texts.append(text)
    if texts:
        return _condense(module, "; ".join(texts))
    return HTTP_RESPONSES.get(res.status_code, "no message returned")


def _request(module, action, request, fail, read=None):
    """Run a client call and return its answer, or what read makes of the answer"""
    try:
        res = request()
        if res.status_code == 200:
            return res if read is None else read(res)
        error = Pure1RequestError(
            action, res.status_code, _response_message(module, res)
        )
    except Exception as raised:
        # Of the raised errors only a request too long is known by its status
        error = Pure1RequestError(
            action, _too_long_status(raised), pure1_error_text(module, raised)
        )
    if fail:
        module.fail_json(msg=str(error))
    raise error


def _rest_of_page(items):
    """Return what the SDK iterator left of the page it ended in

    The iterator ends where its place within a page reaches the total that
    Pure1 reported, so of a page that carries more items than that total it
    hands out only as many as the total. What is left of the page ends at
    the limit of the request, where the iterator ends as well. Nothing is
    left where it ended at the end of a page, and nothing is taken from
    what is no such iterator.
    """
    page = getattr(items, "_items", None)
    place = getattr(items, "_ItemIterator__index", None)
    if not isinstance(page, list) or not isinstance(place, int):
        return []
    keywords = getattr(items, "_kwargs", None)
    limit = keywords.get("limit") if isinstance(keywords, dict) else None
    if limit is None:
        return page[place:]
    return page[place : max(place, limit)]


def _items(wanted, res):
    """Return the items of an answer as a list, all of them or at most wanted

    The SDK fetches the pages after the first while the items are read, and
    fetches no page that the wanted items do not need. A page that carries
    more items than the total Pure1 reported is read whole.
    """
    if res.items is None:
        return []
    if wanted is None:
        found = list(res.items)
        return found + _rest_of_page(res.items)
    found = list(itertools.islice(res.items, wanted))
    return found + _rest_of_page(res.items)[: wanted - len(found)]


def _page(wanted, res):
    """Return the total an answer reports and its items, at most wanted"""
    return res.total_item_count, _items(wanted, res)


def pure1_items(module, action, request, fail=True):
    """Run a call of the Pure1 client and return the items of its answer as a list

    request is a function without arguments that makes the call, so that the
    call stays written out in the module, and action is the name of the client
    method for the message:

        pods = pure1_items(module, "get_pods", lambda: pure_1.get_pods(limit=5))

    pylint refuses a lambda around a call that has no argument or only
    **kwargs: give such a call as a local function. An error answer, an error
    raised by the SDK or by the transport, or an error while the items are
    read, fails the task with one line such as "Pure1 request get_pods failed
    (HTTP 400): Illegal filter format". With fail=False it raises
    Pure1RequestError instead, for a module that carries on or words the
    message itself.

    It is for a complete listing without sort. With sort the SDK stops after
    the first page of 1000 items and says nothing: use pure1_list() for a
    sorted listing and for a limit.
    """
    return _request(module, action, request, fail, functools.partial(_items, None))


def pure1_response(module, action, request, fail=True):
    """Run a call of the Pure1 client and return its answer when it is a success

    For a call whose items are not wanted: a count (total_item_count of a
    call made with limit=0) or a change. The items of the answer are not
    read, use pure1_items() for them. It fails as pure1_items() does.
    """
    return _request(module, action, request, fail)


Pure1Listing = collections.namedtuple("Pure1Listing", ["items", "total", "truncated"])


def _takes_paging(request):
    """Tell whether a request function can be called with the paging keywords"""
    try:
        inspect.signature(request).bind(limit=1, offset=1)
    except TypeError:
        return False
    except ValueError:
        # Nothing to look at: some callables have no signature
        pass
    return True


def _takes_no_offset(error):
    """Tell whether a call failed because it does not take the offset keyword"""
    return (
        error.status_code is None
        and error.text.startswith("TypeError:")
        and "unexpected keyword argument 'offset'" in error.text
    )


def pure1_list(module, action, request, limit=None, fail=True):
    """Run a listing call of the Pure1 client page by page and return a Pure1Listing

    items are the SDK models, total is the total_item_count Pure1 reported
    for the request (None when it reported none), and truncated tells that
    the items are not all of them: there are fewer than Pure1 reported, or
    Pure1 reported no total and the helper could not ask whether more exist.
    A listing is never returned short with truncated False. A page that
    carries more items than the total Pure1 reported is returned whole, and
    no page after it is asked for: the total is reached.

    request is a function that makes the call and hands the paging keywords
    it is given on to the client. It sets none of limit, offset and
    continuation_token itself, and action is the name of the client method
    for the message:

        audits = pure1_list(
            module,
            "get_audits",
            lambda **page: pure_1.get_audits(filter=flt, sort=["time-", "id"], **page),
            limit=1000,
        )

    pylint refuses a lambda that only hands its own arguments on: give a
    call with no argument of its own as a local function. A request function
    that does not take limit and offset raises TypeError here, before any
    request: it would work until a listing needs a second page.

    limit None returns every item, however many pages that takes. A limit
    above 0 returns at most that many and requests no page they do not
    need. limit 0 returns no item and the total alone. Where Pure1 then
    counts 0, one item is asked for and the total of that answer is taken:
    get_protection_group_snapshots counts nothing when no item is wanted.

    Without sort Pure1 gives a continuation token with each page of 1000
    items, and the SDK follows it. With sort Pure1 gives no token and the SDK
    stops after the first page, so the helper asks for the pages after it by
    offset. Those pages are disjoint only when the sort orders every item:
    the last sort key must be one that every item has and no two items
    share, as id is for most items. With any other sort the pages can
    repeat some items and leave out others. Items that are created or
    deleted while the pages are read move the others as well, and a listing
    that lost items after the first answer counted them is truncated: the
    helper cannot tell whether one of the others was skipped. Where the
    client method takes no offset, as the four get_recommendations methods,
    or Pure1 ignores it, a sorted listing ends after its first page with
    truncated True.

    An error answer, an error raised by the SDK or by the transport, and an
    error while a later page is read fail the task as pure1_items() does,
    and with fail=False raise Pure1RequestError.
    """
    if not _takes_paging(request):
        raise TypeError(
            "the request function of pure1_list() must take the paging "
            "keywords (**page) and hand them on"
        )
    if limit is not None and limit < 0:
        module.fail_json(msg="limit must be 0 or more, got {0}".format(limit))
    items = []
    total = None
    # Nothing is left to ask for: the total is reached, or a page came empty
    complete = False
    first = True

    def ask(page):
        return _request(
            module,
            action,
            functools.partial(request, **page),
            False,
            functools.partial(_page, page.get("limit")),
        )

    try:
        while True:
            page = {}
            if limit is not None:
                page["limit"] = limit - len(items)
            if not first:
                page["offset"] = len(items)
            try:
                reported, found = ask(page)
            except Pure1RequestError as error:
                if not first and _takes_no_offset(error):
                    break
                raise
            if first:
                total = reported
                first = False
            elif found and found == items[: len(found)]:
                # Pure1 ignored the offset and gave the first page again
                break
            items.extend(found)
            if limit is not None and len(items) >= limit:
                break
            if not found or (total is not None and len(items) >= total):
                complete = True
                break
        if limit == 0 and total == 0:
            # Counted with no item asked for, which some endpoints get wrong
            reported, found = ask(dict(limit=1))
            if reported or found:
                total = reported
    except Pure1RequestError as error:
        if fail:
            module.fail_json(msg=str(error))
        raise
    if total is None:
        truncated = not complete
    else:
        truncated = len(items) < total
    return Pure1Listing(items, total, truncated)


def filter_string(value):
    """Return a string as a quoted value of a Pure1 filter

    Pure1 filters escape a backslash or a single quote in a string with a
    backslash.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def filter_in(field, values):
    """Return the filter term for a field that equals one of the values

    Each value is quoted with filter_string() and a value given twice is
    sent once: "arrays.name='a'" for one value, "arrays.name=('a','b')" for
    several and None for none. Pure1 compares a name whatever its case and
    ignores a value that matches nothing.
    """
    if isinstance(values, str):
        values = [values]
    quoted = []
    for value in values or []:
        value = filter_string(value)
        if value not in quoted:
            quoted.append(value)
    if not quoted:
        return None
    if len(quoted) == 1:
        return field + "=" + quoted[0]
    return "{0}=({1})".format(field, ",".join(quoted))


def _top_level(term):
    """Return a filter term with its strings and parentheses blanked out

    What is left stands outside every string and every pair of parentheses.
    ValueError for a term that closes a parenthesis it did not open, or
    leaves a parenthesis or a string open: joined to other terms it would
    not stay one term.
    """
    outside = []
    depth = 0
    quoted = False
    escaped = False
    for character in term:
        if quoted:
            # A backslash in a string escapes the character after it
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "'":
                quoted = False
        elif character == "'":
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0:
            outside.append(character)
            continue
        outside.append(" ")
    if quoted or depth:
        raise ValueError(
            "The parentheses or the quotes of the filter term do not match: "
            "{0}".format(term)
        )
    return "".join(outside)


def filter_raw(text):
    """Return a filter that the task wrote as a term for filter_and()

    The filter is put in parentheses, so that an or in it stays inside the
    term. None for a filter that is None or empty, and ValueError for one
    whose parentheses or quotes do not match, which no parentheses around
    it would hold together: the module fails the task with str() of it.
    """
    text = (text or "").strip()
    if not text:
        return None
    _top_level(text)
    return "({0})".format(text)


def filter_and(*terms):
    """Return the filter that every one of the terms must match

    A term that is None or empty is left out, and None is returned when no
    term is left. Pure1 binds and before or, so a term that holds an or
    outside its strings and parentheses is put in parentheses:

        filter_and(filter_in("arrays.name", names), "time>=1612357445000")

    gives "arrays.name=('a','b') and time>=1612357445000". Give a filter
    that the task wrote through filter_raw(). A term whose parentheses or
    quotes do not match raises ValueError, as filter_raw() does.
    """
    kept = []
    for term in terms:
        term = (term or "").strip()
        if term:
            wrap = re.search(r"\bor\b", _top_level(term)) is not None
            kept.append("({0})".format(term) if wrap else term)
    return " and ".join(kept) or None


def pure1_time(milliseconds):
    """Return a Pure1 time, milliseconds since 1970, as "YYYY-MM-DD HH:MM:SS UTC"

    Always in UTC, whatever the time zone of the host. None for None and for
    a value that cannot be a time: one that is no number, a boolean, and a
    number that the platform cannot convert. It never raises.
    """
    if milliseconds is None or isinstance(milliseconds, bool):
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(milliseconds / 1000))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def pure1_attr(item, path, default=None):
    """Return a field of an SDK model by its dotted path, or the default

    An SDK model raises AttributeError for a field Pure1 did not send, a
    reference Pure1 left out arrives as an object with no field set, and an
    entry of a list can be None: pure1_attr(volume, "pod.name") gives the
    default for each of them, and a value of 0, "" or [] as it is. A path
    does not go through a list: pure1_attr(pod, "arrays.name") is the
    default, read the entries first. A method is no field either.
    """
    for name in path.split("."):
        if item is None:
            break
        item = getattr(item, name, None)
        if callable(item):
            # A method of the value, as count of a list
            item = None
    return default if item is None else item


def pure1_resolve_arrays(module, pure_1, names):
    """Return the arrays of the names a task gives, as Pure1 knows them

    names is the value of a module option of type list with elements str.
    None when it is None or empty, else a list with one dictionary per array
    (id, name, fqdn, os, model, version) in the order of the names, each
    array once. name is the name as Pure1 spells it, which the task may give
    in any case, and fqdn is None where Pure1 has none.

    A name Pure1 does not know fails the task ("Array foo is not known to
    Pure1."), and so does an empty name. A failed request fails it as
    pure1_items() does.
    """
    if not names:
        return None
    if not all(name.strip() for name in names):
        module.fail_json(msg="The name of an array must not be empty.")
    name_filter = filter_in("name", names)
    arrays = pure1_items(
        module, "get_arrays", lambda: pure_1.get_arrays(filter=name_filter)
    )
    known = {}
    for array in arrays:
        name = pure1_attr(array, "name")
        if name:
            known.setdefault(name.lower(), []).append(array)
    found = []
    unknown = []
    for name in names:
        matches = known.get(name.lower(), [])
        # The spelling of the task wins over the same name in another case
        matches = [array for array in matches if array.name == name] or matches
        spellings = sorted(set(array.name for array in matches))
        if len(spellings) > 1:
            module.fail_json(
                msg="Pure1 knows more than one array whose name is {0} in another "
                "case: {1}. Give the name as Pure1 spells it.".format(
                    name, ", ".join(spellings)
                )
            )
        if not matches and name not in unknown:
            unknown.append(name)
        for array in matches:
            if not any(array is earlier for earlier in found):
                found.append(array)
    if unknown:
        text = "Array {0} is" if len(unknown) == 1 else "Arrays {0} are"
        module.fail_json(msg=(text + " not known to Pure1.").format(", ".join(unknown)))
    return [
        dict(
            id=pure1_attr(array, "id"),
            name=array.name,
            fqdn=pure1_attr(array, "fqdn"),
            os=pure1_attr(array, "os"),
            model=pure1_attr(array, "model"),
            version=pure1_attr(array, "version"),
        )
        for array in found
    ]


def pure1_is_one_of(arrays, reference):
    """Tell whether a reference to an array is to one of the arrays of the task

    arrays are the arrays as pure1_resolve_arrays() returns them and
    reference is the array reference of a row, as the SDK model. The id
    tells an array from another one of the same name, or of that name in
    another case: Pure1 compares names whatever their case, so a filter on
    the names also returns the rows of such an array. Where Pure1 sent no
    id, for the reference or for the array, the name must be the same as
    Pure1 spells it.
    """
    reference_id = pure1_attr(reference, "id")
    name = pure1_attr(reference, "name")
    for array in arrays:
        if reference_id is not None and array["id"] is not None:
            if reference_id == array["id"]:
                return True
        elif name is not None and name == array["name"]:
            return True
    return False


def pure1_keeps_name(owners, name, array_id):
    """Tell whether an array is the one that has its name in the result

    A result that is keyed by array name has one array under a name: the
    array that is seen first keeps it. owners is a dictionary of the
    caller, empty at first, in which the id of that array is kept by the
    name. Arrays are told apart by their ids, so a reference without an id
    is taken for the array that has the name. The caller leaves out the rows
    of an array that does not keep its name and counts them in one warning.
    """
    owner = owners.setdefault(name, array_id)
    if owner is None:
        owners[name] = array_id
    return owner is None or array_id is None or owner == array_id


def _sdk_release():
    """Return the release of the installed py-pure-client, None if it is not known"""
    try:
        import pypureclient

        release = getattr(pypureclient, "__version__", None)
        if not release:
            from importlib.metadata import version

            release = version("py-pure-client")
        return str(release)
    except Exception:
        return None


def pure1_require(module, pure_1, method, minimum):
    """Fail the task when the Pure1 client has no such method

    method is the name of a method of the client that the module needs and
    minimum the first py-pure-client release that has it, such as "1.99.0".
    The message names both, and the release that is installed when it can
    be read.
    """
    if callable(getattr(pure_1, method, None)):
        return
    release = _sdk_release()
    module.fail_json(
        msg="py-pure-client {0} or later is required: this module needs "
        "Client.{1}(), which the installed py-pure-client{2} does not "
        "have.".format(minimum, method, " " + release if release else "")
    )


def _client_arguments(module, app_id, key_file):
    """Return the keyword arguments of pure1.Client() for this task"""
    # The client is created in another directory, and a relative path is
    # one below the directory the module was started in
    arguments = dict(
        app_id=app_id, private_key_file=os.path.join(os.getcwd(), key_file)
    )
    if module.params.get("password"):
        arguments["private_key_password"] = module.params["password"]
    # Only an explicit false switches the verification off
    validate_certs = module.params.get("validate_certs") is not False
    # The setting goes through a configuration object where the factory of
    # the installed py-pure-client takes one. A factory without that
    # parameter always verifies the certificate.
    try:
        configurable = "configuration" in inspect.signature(pure1.Client).parameters
    except (TypeError, ValueError):
        configurable = True
    if configurable:
        from pypureclient.configuration import Configuration

        configuration = Configuration()
        configuration.verify_ssl = validate_certs
        arguments["configuration"] = configuration
    elif not validate_certs:
        module.warn(
            "validate_certs is false, but the installed py-pure-client cannot "
            "switch the verification off, which py-pure-client 1.71.0 and later "
            "can: the certificate of Pure1 is still verified."
        )
    return arguments


def _create_client(arguments):
    """Return a new SDK client, created in a directory of its own

    While it creates the client py-pure-client writes the access token to a
    file in the working directory. The file goes with the directory.
    """
    workdir = os.getcwd()
    tempdir = tempfile.mkdtemp(prefix="ansible-pure1-")
    try:
        os.chdir(tempdir)
        try:
            return pure1.Client(**arguments)
        finally:
            os.chdir(workdir)
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _discard_token_file(pure_1):
    """Send the token file of a later authentication to the null device

    py-pure-client writes the file again each time it fetches an access
    token, and reads it only while it holds no token. A client that was
    created always holds one.
    """
    token_manager = getattr(pure_1, "_token_man", None)
    if hasattr(token_manager, "_access_token_file"):
        token_manager._access_token_file = os.devnull


def _fill_credentials(module):
    """Read a credential the task gives empty from the environment

    The argument spec does so for an option the task leaves out. An option
    given as an empty string or as null gets the same fallback here, and a
    secret it finds is made a no_log value of the module, as the spec makes
    it.
    """
    spec = pure1_argument_spec()
    for name in ("app_id", "key_file", "password"):
        if module.params.get(name):
            continue
        fallback, variables = spec[name]["fallback"]
        try:
            module.params[name] = fallback(*variables)
        except AnsibleFallbackNotFound:
            continue
        if spec[name]["no_log"] and module.params[name]:
            module.no_log_values.add(module.params[name])


def _certificate_hint(text):
    """Return the pointer to the remedy when the certificate was not verified

    text is the error as the message shows it: what the message does not
    show, such as the body of an HTTP answer, is no reason for the pointer.
    """
    if CERTIFICATE_FAILURE in text:
        return " See the validate_certs option."
    return ""


def get_pure1(module):
    """Return System Object or Fail"""
    if not HAS_PYPURECLIENT:
        module.fail_json(
            msg=missing_required_lib("py-pure-client"), exception=PYPURECLIENT_IMP_ERR
        )
    _fill_credentials(module)
    app_id = module.params["app_id"]
    key_file = module.params["key_file"]
    if not app_id or not key_file:
        module.fail_json(
            msg="You must set PURE1_APP_ID and PURE1_PRIVATE_KEY_FILE environment variables "
            "or the app_id and key_file module arguments"
        )
    user_agent = "%(base)s %(class)s/%(version)s (%(platform)s)" % {
        "base": USER_AGENT_BASE,
        "class": __name__,
        "version": VERSION,
        "platform": platform.platform(),
    }
    try:
        pure_1 = _create_client(_client_arguments(module, app_id, key_file))
        _discard_token_file(pure_1)
        pure_1._api_client.set_default_header("User-Agent", user_agent)
    except Exception as error:
        text = pure1_error_text(module, error)
        module.fail_json(
            msg="Failed to create the Pure1 client: %s%s"
            % (text, _certificate_hint(text))
        )
    try:
        pure1_response(
            module, "get_arrays", lambda: pure_1.get_arrays(limit=0), fail=False
        )
    except Pure1RequestError as error:
        # An error answer is no certificate failure, whatever its message says
        hint = _certificate_hint(error.text) if error.status_code is None else ""
        module.fail_json(msg=str(error) + hint)
    return pure_1


def pure1_argument_spec():
    """Return standard base dictionary used for the argument_spec argument in AnsibleModule"""

    return dict(
        app_id=dict(no_log=True, fallback=(env_fallback, ["PURE1_APP_ID"])),
        key_file=dict(
            no_log=False, fallback=(env_fallback, ["PURE1_PRIVATE_KEY_FILE"])
        ),
        password=dict(
            no_log=True,
            fallback=(
                env_fallback,
                ["PURE1_PRIVATE_KEY_PASSWORD", "PURE1_PRIVATE_PASSWORD"],
            ),
        ),
        validate_certs=dict(type="bool", default=True),
    )
