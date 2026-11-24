# -*- coding: utf-8 -*-

# Copyright: (c) 2021, Simon Dodsley <simon@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function

__metaclass__ = type


class ModuleDocFragment(object):
    # Standard Everpure documentation fragment
    DOCUMENTATION = r"""
options:
  - See separate platform section for more details
requirements:
  - See separate platform section for more details
notes:
  - Ansible modules are available for the following Everpure products: FlashArray, FlashBlade, Pure1
"""

    # Documentation fragment for Pure1
    P1 = r"""
options:
  key_file:
    description:
      - Path to the private key file
      - Defaults to the set environment variable under PURE1_PRIVATE_KEY_FILE.
    type: str
  app_id:
    description:
      - Application ID from Pure1 Registration page
      - eg. pure1:apikey:dssf2331sd
      - Defaults to the set environment variable under PURE1_APP_ID
    type: str
  password:
    description:
      - The password of the private key, if encrypted.
      - Defaults to the set environment variable under PURE1_PRIVATE_KEY_PASSWORD,
        or if that is not set, under PURE1_PRIVATE_PASSWORD.
    type: str
  validate_certs:
    description:
      - Whether to verify the TLS certificate of Pure1, for the request of the
        access token as well as for the API calls.
      - The certificate is checked against the default CA certificates of OpenSSL
        on the host that runs the module. The C(SSL_CERT_FILE) and C(SSL_CERT_DIR)
        environment variables of OpenSSL apply when they hold absolute paths, so
        where outbound TLS is inspected, set C(SSL_CERT_FILE) for the task to the
        absolute path of a PEM file that holds the inspecting CA.
    type: bool
    default: true
    version_added: '1.6.0'
notes:
  - This module requires the C(py-pure-client) Python library
  - You must set C(PURE1_APP_ID) and C(PURE1_PRIVATE_KEY_FILE) environment variables
    if I(app_id) and I(key_file) arguments are not passed to the module directly
  - C(PURE1_PRIVATE_KEY_PASSWORD) or C(PURE1_PRIVATE_PASSWORD) environmental variable | I(password) is optional
  - When the C(PURE1_ID_TOKEN) environment variable is set, C(py-pure-client) authenticates
    with that ID token and ignores I(app_id), I(key_file) and I(password), although
    I(app_id) and I(key_file) must still be set
requirements:
  - python >= 3.9
  - py-pure-client >= 1.99.0
"""
