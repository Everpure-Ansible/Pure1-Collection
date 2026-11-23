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

from os import environ
import platform
import re

from ansible.module_utils.basic import missing_required_lib

try:
    from http.client import responses as HTTP_RESPONSES
except ImportError:
    HTTP_RESPONSES = {}

TOKEN_EXCHANGE_URL = "https://api.pure1.purestorage.com/oauth2/1.0/token"
VERSION = 1.0
USER_AGENT_BASE = "Ansible"
ERROR_TEXT_LIMIT = 500
# A PEM block and a JSON web token (ID token, bearer token) are never reported
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN.*"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}[A-Za-z0-9_.=-]*"),
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
        environ.get("PURE1_PRIVATE_PASSWORD"),
        environ.get("PURE1_PRIVATE_KEY_PASSWORD"),
    ):
        if secret:
            text = text.replace(secret, "********")
    text = " ".join(text.split())
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("********", text)
    if len(text) > ERROR_TEXT_LIMIT:
        text = text[:ERROR_TEXT_LIMIT] + "..."
    return text


def _error_text(module, error):
    """Return the type of an exception and its condensed text"""
    name = type(error).__name__
    text = _condense(module, error)
    # PureError already starts its text with its own name
    if not text.startswith(name + ":"):
        text = "%s: %s" % (name, text) if text else name
    return text


def _response_message(module, res):
    """Return the message of an error response or its HTTP status text"""
    for error in getattr(res, "errors", None) or []:
        message = getattr(error, "message", None) or getattr(error, "context", None)
        text = _condense(module, message or "")
        if text:
            return text
    return HTTP_RESPONSES.get(res.status_code, "no message returned")


def get_pure1(module):
    """Return System Object or Fail"""
    user_agent = "%(base)s %(class)s/%(version)s (%(platform)s)" % {
        "base": USER_AGENT_BASE,
        "class": __name__,
        "version": VERSION,
        "platform": platform.platform(),
    }
    app_id = module.params["app_id"]
    key_file = module.params["key_file"]
    if HAS_PYPURECLIENT:
        if app_id and key_file:
            try:
                if module.params["password"]:
                    pure_1 = pure1.Client(
                        app_id=app_id,
                        private_key_file=key_file,
                        private_key_password=module.params["password"],
                    )
                else:
                    pure_1 = pure1.Client(
                        app_id=app_id,
                        private_key_file=key_file,
                    )
                pure_1._api_client.set_default_header("User-Agent", user_agent)
            except Exception as error:
                module.fail_json(
                    msg="Failed to create the Pure1 client: %s"
                    % _error_text(module, error)
                )
        elif environ.get("PURE1_APP_ID") and environ.get("PURE1_PRIVATE_KEY_FILE"):
            try:
                if module.params["password"]:
                    pure_1 = pure1.Client(
                        app_id=environ.get("PURE1_APP_ID"),
                        private_key_file=environ.get("PURE1_PRIVATE_KEY_FILE"),
                        private_key_password=environ.get("PURE1_PRIVATE_PASSWORD"),
                    )
                else:
                    pure_1 = pure1.Client(
                        app_id=app_id,
                        private_key_file=key_file,
                    )
                pure_1._api_client.set_default_header("User-Agent", user_agent)
            except Exception as error:
                module.fail_json(
                    msg="Failed to create the Pure1 client: %s"
                    % _error_text(module, error)
                )
        else:
            module.fail_json(
                msg="You must set PURE1_APP_ID and PURE1_PRIVATE_KEY_FILE environment variables "
                "or the app_id and key_file module arguments"
            )
        try:
            res = pure_1.get_arrays()
            if res.status_code != 200:
                module.fail_json(
                    msg="Pure1 request get_arrays failed (HTTP %s): %s"
                    % (res.status_code, _response_message(module, res))
                )
        except Exception as error:
            module.fail_json(
                msg="Pure1 request get_arrays failed: %s" % _error_text(module, error)
            )
    else:
        module.fail_json(
            msg=missing_required_lib("py-pure-client"), exception=PYPURECLIENT_IMP_ERR
        )
    return pure_1


def pure1_argument_spec():
    """Return standard base dictionary used for the argument_spec argument in AnsibleModule"""

    return dict(
        app_id=dict(no_log=True, required=True),
        key_file=dict(no_log=False, required=True),
        password=dict(no_log=True),
    )
