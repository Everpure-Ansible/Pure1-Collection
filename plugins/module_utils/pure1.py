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

import inspect
import os
import platform
import re
import shutil
import tempfile

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


class Pure1RequestError(Exception):
    """A Pure1 request that failed

    status_code is the HTTP status of an error answer, None when the SDK
    raised an error instead of answering. text is the reason as one bounded
    line with no secret in it, and str() the message of the failed task.
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


def pure1_error_text(module, error):
    """Return the type of an exception and its text as one bounded line

    The line holds no password, token or key. A Pure1RequestError gives its
    own message.
    """
    if isinstance(error, Pure1RequestError):
        return str(error)
    name = type(error).__name__
    text = _condense(module, error)
    # PureError already starts its text with its own name
    if not text.startswith(name + ":"):
        text = "%s: %s" % (name, text) if text else name
    return text


def _response_message(module, res):
    """Return the messages of an error response or its HTTP status text"""
    texts = []
    for error in getattr(res, "errors", None) or []:
        # Pure1 puts what is specific to a 400 or a 404 in the context
        parts = [getattr(error, "message", None), getattr(error, "context", None)]
        parts = [_condense(module, part) for part in parts if part]
        text = ": ".join(part for part in parts if part)
        if text and text not in texts:
            texts.append(text)
    if texts:
        return _condense(module, "; ".join(texts))
    return HTTP_RESPONSES.get(res.status_code, "no message returned")


def _request(module, action, request, fail, read_items):
    """Run a client call and return its answer, or the items of the answer"""
    try:
        res = request()
        if res.status_code == 200:
            if not read_items:
                return res
            # The SDK fetches the pages after the first while the items are read
            return [] if res.items is None else list(res.items)
        error = Pure1RequestError(
            action, res.status_code, _response_message(module, res)
        )
    except Exception as raised:
        error = Pure1RequestError(action, None, pure1_error_text(module, raised))
    if fail:
        module.fail_json(msg=str(error))
    raise error


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
    """
    return _request(module, action, request, fail, True)


def pure1_response(module, action, request, fail=True):
    """Run a call of the Pure1 client and return its answer when it is a success

    For a call whose items are not wanted: a count (total_item_count of a
    call made with limit=0) or a change. The items of the answer are not
    read, use pure1_items() for them. It fails as pure1_items() does.
    """
    return _request(module, action, request, fail, False)


def filter_string(value):
    """Return a string as a quoted value of a Pure1 filter

    Pure1 filters escape a backslash or a single quote in a string with a
    backslash.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


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
            "switch the verification off: the certificate of Pure1 is still "
            "verified. py-pure-client 1.71.0 and later can."
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
