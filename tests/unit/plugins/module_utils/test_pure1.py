# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for pure1 module utilities."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.metadata
import importlib.util
import os
import ssl
import stat
import time
import types
import pytest
from unittest.mock import patch

import pypureclient
import urllib3
from ansible.module_utils.basic import AnsibleModule, env_fallback
from pypureclient import ApiError, ErrorResponse, PureError, ValidResponse
from pypureclient._transport.api_response import ApiResponse
from pypureclient._transport.exceptions import ApiException
from pypureclient.configuration import Configuration
from pypureclient.pure1.client import Client as sdk_client_factory
from pypureclient.responses import ItemIterator
from pypureclient.token_manager import TokenManager

from plugins.module_utils import pure1 as pure1_utils
from plugins.module_utils.pure1 import (
    REQUEST_TOO_LONG,
    Pure1Listing,
    Pure1RequestError,
    filter_and,
    filter_in,
    filter_raw,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_attr,
    pure1_error_text,
    pure1_is_one_of,
    pure1_items,
    pure1_keeps_name,
    pure1_list,
    pure1_require,
    pure1_resolve_arrays,
    pure1_response,
    pure1_time,
)
from tests.unit.conftest import (
    CREDENTIALS,
    NEWEST,
    SDK,
    ansible_module,
    error_response,
    paged_answers,
    run_main,
    sdk_model,
    valid_response,
)

# Shaped like the JSON web tokens Pure1 uses as ID token and bearer token
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJwdXJlMSJ9.c2lnbmF0dXJl"
# The file py-pure-client keeps its access token in
TOKEN_FILE = "oauth21.0token.access_token"
MISSING_CREDENTIALS = (
    "You must set PURE1_APP_ID and PURE1_PRIVATE_KEY_FILE environment variables "
    "or the app_id and key_file module arguments"
)
# The variables the key password is read from, and the password that results
PASSWORD_VARIABLES = [
    pytest.param({"PURE1_PRIVATE_KEY_PASSWORD": "sdkpass"}, "sdkpass", id="SDK name"),
    pytest.param(
        {"PURE1_PRIVATE_PASSWORD": "oldpass"}, "oldpass", id="collection name"
    ),
    pytest.param(
        {
            "PURE1_PRIVATE_KEY_PASSWORD": "sdkpass",
            "PURE1_PRIVATE_PASSWORD": "oldpass",
        },
        "sdkpass",
        id="SDK name first",
    ),
]


@pytest.fixture
def no_task_credentials(monkeypatch):
    """Leave app_id, key_file and password out of the task run_main builds."""
    for name in ("app_id", "key_file", "password"):
        monkeypatch.delitem(CREDENTIALS, name)


@pytest.fixture
def sdk_client(client_factory, monkeypatch):
    """Make pure1.Client() the factory of the SDK, with nothing sent.

    The client is the SDK's own. PURE1_ID_TOKEN stands for the key, which the
    SDK then does not open, the request for an access token returns a fixed
    one that does not expire, and get_arrays answers with no items. Returns
    what the working directory was at each token request and what it held
    once the client was created.
    """
    seen = {"token_requests": []}

    def request_access_token(self):
        seen["token_requests"].append(os.getcwd())
        return JWT

    def create(**kwargs):
        client = sdk_client_factory(**kwargs)
        seen["files"] = os.listdir(os.getcwd())
        client.get_arrays = lambda **_kwargs: valid_response()
        return client

    monkeypatch.setenv("PURE1_ID_TOKEN", JWT)
    monkeypatch.setattr(TokenManager, "_request_access_token", request_access_token)
    monkeypatch.setattr(TokenManager, "_is_token_expired", lambda self: False)
    client_factory.side_effect = create
    return seen


def _plugin():
    """Return a module whose main() does what main() of every module starts with."""
    plugin = types.ModuleType("pure1_example")
    plugin.AnsibleModule = AnsibleModule

    def main():
        module = plugin.AnsibleModule(pure1_argument_spec(), supports_check_mode=True)
        get_pure1(module)
        module.exit_json(changed=False)

    plugin.main = main
    return plugin


def _failure_message(module):
    """Run get_pure1 and return the message it failed with."""
    with pytest.raises(SystemExit):
        get_pure1(module)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


def _credentials_given(client_factory):
    """Return what pure1.Client() was last given beside the configuration."""
    arguments = dict(client_factory.call_args[1])
    del arguments["configuration"]
    return arguments


def _request_failure(helper, module, request):
    """Run a request helper and return the message it failed with."""
    with pytest.raises(SystemExit):
        helper(module, "get_pods", request)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


def _pool():
    """Return the urllib3 pool of the Pure1 API; it connects when it is used."""
    return urllib3.HTTPSConnectionPool("api.pure1.purestorage.com", port=443)


def _read_timeout():
    return urllib3.exceptions.ReadTimeoutError(
        _pool(), "/api/1.7/pods", "Read timed out. (read timeout=15.0)"
    )


def _certificate_error(url):
    """Return what urllib3 raises for a certificate it cannot verify."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLCertVerificationError(
            1,
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1007)",
        )
    )
    return urllib3.exceptions.MaxRetryError(_pool(), url, reason)


def _certificate_error_of_the_sdk():
    """Return what py-pure-client makes of that failure when urllib3 does not retry.

    urllib3 then raises its SSLError itself. The transport of the SDK turns
    it into an ApiException, and the token request that one into a PureError.
    """
    reason = _certificate_error("/oauth2/1.0/token").reason
    api_error = ApiException(
        status=0, reason="{0}\n{1}".format(type(reason).__name__, reason)
    )
    return PureError("Could not retrieve a new access token: {0}".format(api_error))


def _tls_error(url):
    """Return what urllib3 raises for a TLS failure that is not the certificate."""
    reason = urllib3.exceptions.SSLError(
        ssl.SSLError(
            1, "[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1007)"
        )
    )
    return urllib3.exceptions.MaxRetryError(_pool(), url, reason)


def _connection_refused(url):
    """Return what urllib3 raises when nothing listens on the port."""
    connection = urllib3.connection.HTTPSConnection("api.pure1.purestorage.com", 443)
    reason = urllib3.exceptions.NewConnectionError(
        connection,
        "Failed to establish a new connection: [Errno 111] Connection refused",
    )
    return urllib3.exceptions.MaxRetryError(_pool(), url, reason)


def _paged_response(items, next_page):
    """Return a valid answer whose items go on with a page that next_page gives."""
    total = len(items) + 1
    page = ItemIterator(
        next_page, {}, "next-page", total, items, None, more_items_remaining=True
    )
    return ValidResponse(200, "next-page", total, page, {})


class TestPure1ArgumentSpec:
    """Tests for pure1_argument_spec function."""

    def test_returns_dict(self):
        """Test that the function returns a dictionary."""
        assert isinstance(pure1_argument_spec(), dict)

    def test_all_expected_keys(self):
        """Test that spec contains exactly the expected keys."""
        result = pure1_argument_spec()
        assert sorted(result) == ["app_id", "key_file", "password", "validate_certs"]

    def test_app_id_is_secret_and_falls_back_to_the_environment(self):
        """app_id is not required: it has PURE1_APP_ID to fall back to."""
        result = pure1_argument_spec()
        assert "required" not in result["app_id"]
        assert result["app_id"]["no_log"] is True
        assert result["app_id"]["fallback"] == (env_fallback, ["PURE1_APP_ID"])

    def test_key_file_is_a_path_and_falls_back_to_the_environment(self):
        """key_file is not required, and is a path, not a secret."""
        result = pure1_argument_spec()
        assert "required" not in result["key_file"]
        assert result["key_file"]["no_log"] is False
        assert result["key_file"]["fallback"] == (
            env_fallback,
            ["PURE1_PRIVATE_KEY_FILE"],
        )

    def test_password_is_secret_and_reads_the_sdk_variable_first(self):
        """The variable py-pure-client reads comes before the collection's own."""
        result = pure1_argument_spec()
        assert result["password"]["no_log"] is True
        assert result["password"]["fallback"] == (
            env_fallback,
            ["PURE1_PRIVATE_KEY_PASSWORD", "PURE1_PRIVATE_PASSWORD"],
        )

    def test_validate_certs_is_on_by_default(self):
        """Certificate verification is a boolean that defaults to true."""
        option = pure1_argument_spec()["validate_certs"]
        assert option == dict(type="bool", default=True)


class TestCredentials:
    """Where get_pure1 takes the credentials from, through the argument spec."""

    def test_task_options(self, client_factory, pure1_client):
        """Options of the task reach pure1.Client(), which is called once."""
        module = run_main(
            _plugin(),
            app_id="task-app-id",
            key_file="/task/pure1.pem",
            password="taskpass",
        )

        module.exit_json.assert_called_once_with(changed=False)
        client_factory.assert_called_once()
        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["app_id"] == "task-app-id"
        assert call_kwargs["private_key_file"] == "/task/pure1.pem"
        assert call_kwargs["private_key_password"] == "taskpass"

    def test_environment(
        self, client_factory, pure1_client, monkeypatch, no_task_credentials
    ):
        """A task with no credentials takes app ID and key file from the environment."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")

        module = run_main(_plugin())

        module.exit_json.assert_called_once_with(changed=False)
        client_factory.assert_called_once()
        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["app_id"] == "env-app-id"
        assert call_kwargs["private_key_file"] == "/env/pure1.pem"
        assert "private_key_password" not in call_kwargs

    @pytest.mark.parametrize("variables, expected", PASSWORD_VARIABLES)
    def test_password_from_the_environment(
        self,
        client_factory,
        pure1_client,
        monkeypatch,
        no_task_credentials,
        variables,
        expected,
    ):
        """The key password is read from either variable, the SDK's first."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

        run_main(_plugin())

        client_factory.assert_called_once()
        assert client_factory.call_args[1]["private_key_password"] == expected

    def test_task_options_win_over_the_environment(
        self, client_factory, pure1_client, monkeypatch
    ):
        """An exported variable does not replace an option the task gives."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "oldpass")

        module = run_main(
            _plugin(),
            app_id="task-app-id",
            key_file="/task/pure1.pem",
            password="taskpass",
        )

        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["app_id"] == "task-app-id"
        assert call_kwargs["private_key_file"] == "/task/pure1.pem"
        assert call_kwargs["private_key_password"] == "taskpass"
        # nothing was read from the environment: no secret but those of the task
        assert module.no_log_values == {"task-app-id", "taskpass"}

    @pytest.mark.parametrize(
        "variables",
        [
            pytest.param({}, id="nothing set"),
            pytest.param({"PURE1_APP_ID": "env-app-id"}, id="no key file"),
            pytest.param({"PURE1_PRIVATE_KEY_FILE": "/env/pure1.pem"}, id="no app ID"),
        ],
    )
    def test_missing_credentials(
        self, client_factory, monkeypatch, no_task_credentials, variables
    ):
        """Without an app ID or a key file the task fails before any client."""
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

        module = run_main(_plugin())

        module.fail_json.assert_called_once()
        assert module.fail_json.call_args[1]["msg"] == MISSING_CREDENTIALS
        client_factory.assert_not_called()

    @pytest.mark.parametrize("empty", ["", None], ids=["empty string", "null"])
    @pytest.mark.parametrize(
        "option, argument, value",
        [
            ("app_id", "app_id", "env-app-id"),
            ("key_file", "private_key_file", "/env/pure1.pem"),
            ("password", "private_key_password", "sdkpass"),
        ],
    )
    def test_empty_option_is_read_from_the_environment(
        self, client_factory, pure1_client, monkeypatch, option, argument, value, empty
    ):
        """An option the task gives empty counts as left out; the others stay."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        options = dict(
            app_id="task-app-id", key_file="/task/pure1.pem", password="taskpass"
        )
        options[option] = empty

        module = run_main(_plugin(), **options)

        module.exit_json.assert_called_once_with(changed=False)
        client_factory.assert_called_once()
        expected = dict(
            app_id="task-app-id",
            private_key_file="/task/pure1.pem",
            private_key_password="taskpass",
        )
        expected[argument] = value
        assert _credentials_given(client_factory) == expected

    @pytest.mark.parametrize("variables, expected", PASSWORD_VARIABLES)
    def test_empty_password_is_read_from_either_variable(
        self, client_factory, pure1_client, monkeypatch, variables, expected
    ):
        """The variables of the argument spec, in its order."""
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

        run_main(_plugin(), password="")

        client_factory.assert_called_once()
        assert client_factory.call_args[1]["private_key_password"] == expected

    @pytest.mark.parametrize("empty", ["", None], ids=["empty string", "null"])
    def test_empty_options_are_the_task_without_credentials(
        self, client_factory, pure1_client, monkeypatch, empty
    ):
        """Given empty, the options end as the argument spec leaves absent ones."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "oldpass")

        module = run_main(_plugin(), app_id=empty, key_file=empty, password=empty)

        module.exit_json.assert_called_once_with(changed=False)
        given_empty = (
            _credentials_given(client_factory),
            module.params,
            module.no_log_values,
        )
        assert given_empty == (
            dict(
                app_id="env-app-id",
                private_key_file="/env/pure1.pem",
                private_key_password="oldpass",
            ),
            dict(
                app_id="env-app-id",
                key_file="/env/pure1.pem",
                password="oldpass",
                validate_certs=True,
            ),
            # what Ansible hides in the result; the path of the key file is no secret
            {"env-app-id", "oldpass"},
        )
        for name in ("app_id", "key_file", "password"):
            monkeypatch.delitem(CREDENTIALS, name)

        module = run_main(_plugin())

        left_out = (
            _credentials_given(client_factory),
            module.params,
            module.no_log_values,
        )
        assert left_out == given_empty
        assert client_factory.call_count == 2

    def test_secret_read_for_an_empty_option_is_a_no_log_value(
        self, pure1_client, monkeypatch
    ):
        """Ansible has not seen it: get_pure1 has it hidden in what the task returns."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        module = ansible_module(app_id="", key_file="", password="")

        get_pure1(module)

        assert module.no_log_values == {"env-app-id", "sdkpass"}

    def test_empty_variable_is_no_secret_to_hide(
        self, client_factory, pure1_client, monkeypatch
    ):
        """An empty no_log value would have Ansible mask every string of the result.

        A variable that is set is the value, also when it is empty: as for the
        argument spec, the variable after it is not read.
        """
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "oldpass")
        module = ansible_module(password="")

        get_pure1(module)

        assert module.no_log_values == set()
        assert "private_key_password" not in client_factory.call_args[1]

    @pytest.mark.parametrize(
        "options, variables",
        [
            pytest.param({"app_id": "", "key_file": ""}, {}, id="nothing set"),
            pytest.param(
                {"app_id": ""},
                {"PURE1_PRIVATE_KEY_FILE": "/env/pure1.pem"},
                id="no app ID",
            ),
            pytest.param(
                {"key_file": None}, {"PURE1_APP_ID": "env-app-id"}, id="no key file"
            ),
            pytest.param({"app_id": ""}, {"PURE1_APP_ID": ""}, id="empty variable"),
        ],
    )
    def test_empty_option_without_its_variable_is_missing(
        self, client_factory, monkeypatch, options, variables
    ):
        """With nothing to read for it an empty option fails the task as before."""
        for name, value in variables.items():
            monkeypatch.setenv(name, value)

        module = run_main(_plugin(), **options)

        module.fail_json.assert_called_once_with(msg=MISSING_CREDENTIALS)
        client_factory.assert_not_called()


class TestGetPure1:
    """Tests for get_pure1 function."""

    def test_with_module_params(self, client_factory, pure1_client):
        """Test get_pure1 with app_id/key_file module parameters."""
        module = ansible_module(
            app_id="pure1-app-id", key_file="/run/secrets/pure1.pem", password=None
        )

        result = get_pure1(module)

        client_factory.assert_called_once()
        call_kwargs = client_factory.call_args[1]
        assert set(call_kwargs) == {"app_id", "private_key_file", "configuration"}
        assert call_kwargs["app_id"] == "pure1-app-id"
        assert call_kwargs["private_key_file"] == "/run/secrets/pure1.pem"
        header, value = pure1_client._api_client.set_default_header.call_args[0]
        assert header == "User-Agent"
        assert value.startswith("Ansible ")
        # the check of the new client asks for the count alone
        pure1_client.get_arrays.assert_called_once_with(limit=0)
        assert result == pure1_client

    def test_with_password(self, client_factory, pure1_client):
        """A supplied password is forwarded as private_key_password."""
        module = ansible_module(password="s3cret")

        result = get_pure1(module)

        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["private_key_password"] == "s3cret"
        assert result == pure1_client

    def test_empty_password_is_not_forwarded(self, client_factory, pure1_client):
        """An empty password is none: the key password is left to py-pure-client."""
        get_pure1(ansible_module(password=""))

        assert "private_key_password" not in client_factory.call_args[1]

    @pytest.mark.parametrize(
        "credentials",
        [
            dict(app_id=None, key_file=None),
            dict(app_id=""),
            dict(key_file=""),
        ],
    )
    def test_missing_credentials(self, client_factory, credentials):
        """Test that missing credentials causes a clean failure."""
        msg = _failure_message(ansible_module(**credentials))

        assert msg == MISSING_CREDENTIALS
        client_factory.assert_not_called()

    @patch("plugins.module_utils.pure1.missing_required_lib")
    @patch("plugins.module_utils.pure1.PYPURECLIENT_IMP_ERR", "import traceback")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", False)
    def test_missing_pypureclient(self, mock_missing_lib):
        """Test that a missing py-pure-client causes failure."""
        module = ansible_module()
        mock_missing_lib.return_value = "py-pure-client is missing"

        with pytest.raises(SystemExit):
            get_pure1(module)

        mock_missing_lib.assert_called_once_with("py-pure-client")
        module.fail_json.assert_called_once_with(
            msg="py-pure-client is missing", exception="import traceback"
        )

    def test_import_failure_keeps_traceback(self, no_sdk):
        """A failed import of py-pure-client is recorded with its traceback."""
        spec = importlib.util.spec_from_file_location(
            "pure1_without_sdk", pure1_utils.__file__
        )
        module_copy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module_copy)

        assert module_copy.HAS_PYPURECLIENT is False
        assert "pypureclient" in module_copy.PYPURECLIENT_IMP_ERR

    def test_preflight_failure_status(self, pure1_client):
        """A non-200 from get_arrays must surface with its status and message."""
        module = ansible_module()
        pure1_client.get_arrays.return_value = error_response(403, "Access denied")

        with pytest.raises(SystemExit):
            get_pure1(module)

        module.fail_json.assert_called_once()
        assert (
            module.fail_json.call_args[1]["msg"]
            == "Pure1 request get_arrays failed (HTTP 403): Access denied"
        )

    def test_unused_constant_is_gone(self):
        """Nothing read the token exchange URL."""
        assert not hasattr(pure1_utils, "TOKEN_EXCHANGE_URL")


class TestCertificateVerification:
    """The configuration get_pure1 gives pure1.Client()."""

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({}, id="default"),
            pytest.param({"validate_certs": True}, id="true"),
            pytest.param({"validate_certs": None}, id="null"),
        ],
    )
    def test_verification_is_on(self, client_factory, pure1_client, params):
        """py-pure-client verifies no certificate unless a configuration says so."""
        module = ansible_module(**params)

        get_pure1(module)

        configuration = client_factory.call_args[1]["configuration"]
        assert isinstance(configuration, Configuration)
        assert configuration.verify_ssl is True
        # no CA file: the default CA certificates of OpenSSL are used
        assert configuration.ssl_ca_cert is None
        module.warn.assert_not_called()

    def test_verification_is_on_without_the_option(self, client_factory, pure1_client):
        """A module that does not have the option verifies as well."""
        module = ansible_module()
        del module.params["validate_certs"]

        get_pure1(module)

        assert client_factory.call_args[1]["configuration"].verify_ssl is True

    def test_verification_can_be_switched_off(self, client_factory, pure1_client):
        """validate_certs=false is the one way to an unverified connection."""
        module = ansible_module(validate_certs=False)

        get_pure1(module)

        configuration = client_factory.call_args[1]["configuration"]
        assert isinstance(configuration, Configuration)
        assert configuration.verify_ssl is False
        module.warn.assert_not_called()

    def test_option_goes_through_the_argument_spec(
        self, client_factory, pure1_client, monkeypatch
    ):
        """The spec fills in the default and converts what a task gives."""
        monkeypatch.delitem(CREDENTIALS, "validate_certs")

        module = run_main(_plugin())

        assert module.params["validate_certs"] is True
        assert client_factory.call_args[1]["configuration"].verify_ssl is True

        module = run_main(_plugin(), validate_certs="no")

        assert module.params["validate_certs"] is False
        assert client_factory.call_args[1]["configuration"].verify_ssl is False

    @pytest.mark.parametrize(
        "validate_certs, cert_reqs",
        [(True, ssl.CERT_REQUIRED), (False, ssl.CERT_NONE)],
    )
    def test_sdk_client_verifies_as_asked(self, sdk_client, validate_certs, cert_reqs):
        """The token request and the API calls of the SDK client follow the option."""
        client = get_pure1(ansible_module(validate_certs=validate_certs))

        assert isinstance(client, NEWEST.Client)
        # the configuration the token request makes its connection from
        assert client._token_man._configuration.verify_ssl is validate_certs
        # the connections of the API calls
        pool_options = client._api_client.rest_client.pool_manager.connection_pool_kw
        assert pool_options["cert_reqs"] == cert_reqs
        assert pool_options["ca_certs"] is None

    def test_sdk_without_configuration_is_given_none(self, pure1_client, monkeypatch):
        """py-pure-client before 1.71.0 takes no configuration and always verifies."""
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return pure1_client

        monkeypatch.setattr(SDK, "Client", factory)
        module = ansible_module(password="s3cret")

        assert get_pure1(module) == pure1_client

        assert calls == [
            {
                "app_id": "pure1-app-id",
                "private_key_file": "/run/secrets/pure1.pem",
                "private_key_password": "s3cret",
            }
        ]
        module.warn.assert_not_called()

    def test_sdk_without_configuration_cannot_switch_verification_off(
        self, pure1_client, monkeypatch
    ):
        """validate_certs=false on such an SDK is answered with a warning."""
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return pure1_client

        monkeypatch.setattr(SDK, "Client", factory)
        module = ansible_module(validate_certs=False)

        assert get_pure1(module) == pure1_client

        assert calls == [
            {"app_id": "pure1-app-id", "private_key_file": "/run/secrets/pure1.pem"}
        ]
        module.warn.assert_called_once_with(
            "validate_certs is false, but the installed py-pure-client cannot "
            "switch the verification off, which py-pure-client 1.71.0 and later "
            "can: the certificate of Pure1 is still verified."
        )

    @pytest.mark.parametrize("error", [ValueError, TypeError])
    def test_unreadable_signature_counts_as_configurable(
        self, client_factory, pure1_client, error
    ):
        """When the factory cannot be inspected the configuration is still given."""
        module = ansible_module()
        # the stand-ins read their signatures before inspect.signature is replaced
        pure1_client._api_client.set_default_header.return_value = None

        with patch("inspect.signature", side_effect=error("no signature")):
            get_pure1(module)

        assert client_factory.call_args[1]["configuration"].verify_ssl is True


class TestWorkingDirectory:
    """get_pure1 keeps the token file of py-pure-client out of the working directory."""

    def test_client_is_created_in_a_private_directory(
        self, client_factory, pure1_client
    ):
        """The directory is new, private, and gone when the client is there."""
        seen = {}

        def create(**kwargs):
            seen["cwd"] = os.getcwd()
            seen["mode"] = stat.S_IMODE(os.stat(seen["cwd"]).st_mode)
            seen["files"] = os.listdir(seen["cwd"])
            return pure1_client

        client_factory.side_effect = create
        start = os.getcwd()

        assert get_pure1(ansible_module()) == pure1_client

        assert seen["cwd"] != start
        assert seen["mode"] == 0o700
        assert seen["files"] == []
        assert os.getcwd() == start
        assert not os.path.exists(seen["cwd"])

    def test_failed_construction_restores_the_working_directory(self, client_factory):
        """The directory goes, with what the SDK wrote, when the client fails."""
        seen = {}

        def create(**kwargs):
            seen["cwd"] = os.getcwd()
            with open(TOKEN_FILE, "w") as token_file:
                token_file.write(JWT)
            raise PureError("Could not retrieve a new access token")

        client_factory.side_effect = create
        start = os.getcwd()

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: "
            "PureError: Could not retrieve a new access token"
        )
        assert seen["cwd"] != start
        assert os.getcwd() == start
        assert not os.path.exists(seen["cwd"])

    def test_relative_key_file_is_resolved_first(
        self, client_factory, pure1_client, tmp_path, monkeypatch
    ):
        """A relative key_file stays the file below the working directory."""
        (tmp_path / "keys").mkdir()
        (tmp_path / "keys" / "pure1.pem").write_text("the key")
        monkeypatch.chdir(tmp_path)
        seen = {}

        def create(**kwargs):
            # the SDK opens the file in the directory the client is created in
            with open(kwargs["private_key_file"]) as key_file:
                seen["key"] = key_file.read()
            return pure1_client

        client_factory.side_effect = create

        get_pure1(ansible_module(key_file="keys/pure1.pem"))

        assert client_factory.call_args[1]["private_key_file"] == os.path.join(
            os.getcwd(), "keys/pure1.pem"
        )
        assert seen["key"] == "the key"

    def test_relative_key_file_is_not_normalised(
        self, client_factory, pure1_client, tmp_path, monkeypatch
    ):
        """A ".." behind a symbolic link stays what the system makes of it."""
        (tmp_path / "real" / "keys").mkdir(parents=True)
        (tmp_path / "real" / "pure1.pem").write_text("the key")
        (tmp_path / "link").symlink_to(tmp_path / "real" / "keys")
        monkeypatch.chdir(tmp_path)
        seen = {}

        def create(**kwargs):
            with open(kwargs["private_key_file"]) as key_file:
                seen["key"] = key_file.read()
            return pure1_client

        client_factory.side_effect = create

        get_pure1(ansible_module(key_file="link/../pure1.pem"))

        assert client_factory.call_args[1]["private_key_file"] == os.path.join(
            os.getcwd(), "link/../pure1.pem"
        )
        assert seen["key"] == "the key"

    def test_token_file_of_the_sdk_goes_with_the_directory(
        self, sdk_client, tmp_path, monkeypatch
    ):
        """What py-pure-client writes while it creates the client is not left behind."""
        monkeypatch.chdir(tmp_path)
        start = os.getcwd()

        client = get_pure1(ansible_module())

        assert isinstance(client, NEWEST.Client)
        (created_in,) = sdk_client["token_requests"]
        assert created_in != start
        # the SDK did write its file, into the directory of the client
        assert sdk_client["files"] == [TOKEN_FILE]
        assert not os.path.exists(created_in)
        assert os.getcwd() == start
        assert os.listdir(start) == []

    def test_later_authentication_writes_no_token_file(
        self, sdk_client, tmp_path, monkeypatch
    ):
        """The SDK fetches a new token after a 403: it is kept in memory only."""
        monkeypatch.chdir(tmp_path)
        client = get_pure1(ansible_module())

        assert client._token_man._access_token_file == os.devnull
        assert client.get_access_token(refresh=True) == JWT

        assert len(sdk_client["token_requests"]) == 2
        assert os.listdir(os.getcwd()) == []

    def test_client_without_a_token_file_is_left_alone(self, pure1_client):
        """An SDK that keeps no token file gets no attribute added."""
        pure1_client._token_man = types.SimpleNamespace()

        assert get_pure1(ansible_module()) == pure1_client

        assert not hasattr(pure1_client._token_man, "_access_token_file")


class TestGetPure1ErrorReporting:
    """Tests for the failure messages of get_pure1."""

    def test_construction_error_names_type_and_text(self, client_factory):
        """Any exception from the client constructor is reported with its type."""
        client_factory.side_effect = AttributeError(
            "module 'jwt' has no attribute 'encode'"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: "
            "AttributeError: module 'jwt' has no attribute 'encode'"
        )
        assert "Unknown failure" not in msg

    def test_construction_pure_error_is_named_once(self, client_factory):
        """The SDK error keeps its text and its name is not repeated."""
        client_factory.side_effect = PureError(
            "Could not load PEM private key /run/secrets/pure1.pem: "
            "key is encrypted but no private_key_password was provided"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not load PEM private key /run/secrets/pure1.pem: "
            "key is encrypted but no private_key_password was provided"
        )

    def test_construction_error_drops_response_headers_and_body(self, client_factory):
        """A rejected token exchange is condensed to its status and reason."""
        client_factory.side_effect = PureError(
            "Could not retrieve a new access token: (401)\n"
            "Reason: Unauthorized\n"
            "HTTP response headers: HTTPHeaderDict({'X-Request-ID': 'abc'})\n"
            'HTTP response body: b\'{"error": "invalid_client"}\'\n'
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not retrieve a new access token: (401) Reason: Unauthorized"
        )

    def test_construction_error_drops_unparsed_token_response(self, client_factory):
        """A token response the SDK cannot parse is not repeated."""
        client_factory.side_effect = PureError(
            "Unable to parse response. "
            'b\'{"token": "0a1b2c3d4e5f60718293a4b5c6d7e8f9"}\''
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: Unable to parse response."
        )

    def test_construction_error_hides_secrets(self, client_factory):
        """No token, key material or password reaches the message."""
        client_factory.side_effect = ValueError(
            "password s3cret, Authorization: Bearer %s\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n" % JWT
        )

        msg = _failure_message(ansible_module(password="s3cret"))

        assert msg.startswith("Failed to create the Pure1 client: ValueError: ")
        for secret in ("s3cret", JWT, "eyJ", "BEGIN", "MIIEowIBAAKCAQEA"):
            assert secret not in msg

    def test_construction_error_text_is_bounded(self, client_factory):
        """A long error text is cut."""
        client_factory.side_effect = RuntimeError("x" * 5000)

        msg = _failure_message(ansible_module())

        assert msg.endswith("...")
        assert len(msg) < 600

    def test_construction_error_hides_secret_at_the_cut(self, client_factory):
        """A token that crosses the length limit is hidden whole."""
        client_factory.side_effect = RuntimeError("x" * 490 + " " + JWT)

        msg = _failure_message(ansible_module())

        assert msg.endswith("x ********")
        assert "eyJ" not in msg

    def test_construction_error_with_environment_credentials(
        self, client_factory, monkeypatch, no_task_credentials
    ):
        """Credentials from the environment report the cause and stay hidden."""
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "envpass")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        client_factory.side_effect = OSError("bad optpass, envpass or sdkpass")

        module = run_main(_plugin(), password="optpass")

        client_factory.assert_called_once()
        assert client_factory.call_args[1]["app_id"] == "env-app-id"
        assert module.fail_json.call_args[1]["msg"] == (
            "Failed to create the Pure1 client: "
            "OSError: bad ********, ******** or ********"
        )

    def test_construction_certificate_error_is_one_line(self, client_factory):
        """A certificate that does not verify fails the token request, readably."""
        client_factory.side_effect = _certificate_error("/oauth2/1.0/token")

        msg = _failure_message(ansible_module())

        assert msg.startswith(
            "Failed to create the Pure1 client: MaxRetryError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Max retries exceeded with url: /oauth2/1.0/token "
        )
        assert "certificate verify failed" in msg
        assert "\n" not in msg

    def test_construction_certificate_error_points_to_the_option(self, client_factory):
        """Where outbound TLS is inspected the token request is what fails."""
        client_factory.side_effect = _certificate_error("/oauth2/1.0/token")

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: MaxRetryError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Max retries exceeded with url: /oauth2/1.0/token (Caused by "
            "SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] "
            "certificate verify failed: unable to get local issuer certificate "
            "(_ssl.c:1007)'))) See the validate_certs option."
        )

    def test_construction_certificate_error_of_the_sdk_points_to_the_option(
        self, client_factory
    ):
        """The pointer follows the failure, not the exception that carries it."""
        client_factory.side_effect = _certificate_error_of_the_sdk()

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not retrieve a new access token: (0) Reason: SSLError "
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1007) "
            "See the validate_certs option."
        )

    def test_preflight_certificate_error_points_to_the_option(self, pure1_client):
        """The first API call makes its own connection, and verifies it as well."""
        pure1_client.get_arrays.side_effect = _certificate_error(
            "/api/1.7/arrays?limit=0"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Pure1 request get_arrays failed: MaxRetryError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Max retries exceeded with url: /api/1.7/arrays?limit=0 (Caused by "
            "SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] "
            "certificate verify failed: unable to get local issuer certificate "
            "(_ssl.c:1007)'))) See the validate_certs option."
        )

    def test_certificate_named_in_a_response_body_has_no_pointer(self, client_factory):
        """An HTTP answer to the token request is no certificate this client refused.

        A proxy that cannot verify the server it forwards to says so in the
        body of its answer. The message leaves the body out, and
        validate_certs would not help.
        """
        client_factory.side_effect = PureError(
            "Could not retrieve a new access token: (503)\n"
            "Reason: Service Unavailable\n"
            "HTTP response headers: HTTPHeaderDict({'server': 'envoy'})\n"
            "HTTP response body: upstream connect error or disconnect/reset "
            "before headers. reset reason: connection failure, transport failure "
            "reason: TLS error: 268435581:SSL routines:OPENSSL_internal:"
            "CERTIFICATE_VERIFY_FAILED\n"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not retrieve a new access token: (503) Reason: Service Unavailable"
        )

    def test_certificate_named_in_an_error_answer_has_no_pointer(self, pure1_client):
        """An error answer is an answer: the connection it came over was made."""
        pure1_client.get_arrays.return_value = error_response(
            400, "upstream connect error: TLS error: CERTIFICATE_VERIFY_FAILED"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Pure1 request get_arrays failed (HTTP 400): "
            "upstream connect error: TLS error: CERTIFICATE_VERIFY_FAILED"
        )

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(_tls_error("/oauth2/1.0/token"), id="other TLS failure"),
            pytest.param(
                _connection_refused("/oauth2/1.0/token"), id="connection refused"
            ),
            pytest.param(_read_timeout(), id="timeout"),
            pytest.param(
                PureError(
                    "Could not retrieve a new access token: (401)\n"
                    "Reason: Unauthorized\n"
                ),
                id="rejected key",
            ),
        ],
    )
    def test_other_failure_has_no_pointer(self, client_factory, pure1_client, error):
        """Only a certificate that was not verified has validate_certs as a remedy."""
        text = pure1_error_text(ansible_module(), error)
        assert "validate_certs" not in text
        client_factory.side_effect = error

        msg = _failure_message(ansible_module())

        assert msg == "Failed to create the Pure1 client: " + text

        client_factory.side_effect = None
        pure1_client.get_arrays.side_effect = error

        msg = _failure_message(ansible_module())

        assert msg == "Pure1 request get_arrays failed: " + text

    @pytest.mark.parametrize(
        "response, expected",
        [
            (error_response(403), "(HTTP 403): Forbidden"),
            (error_response(403, message=" \n"), "(HTTP 403): Forbidden"),
            (error_response(404, context="No such"), "(HTTP 404): No such"),
            (
                error_response(400, "Invalid value", "No arrays with name: foo"),
                "(HTTP 400): Invalid value: No arrays with name: foo",
            ),
            (ErrorResponse(503, [], {}), "(HTTP 503): Service Unavailable"),
            # a ValidResponse is the SDK answer that has no errors attribute
            (
                ValidResponse(429, None, None, None, {}),
                "(HTTP 429): Too Many Requests",
            ),
            (ErrorResponse(599, None, {}), "(HTTP 599): no message returned"),
        ],
    )
    def test_preflight_error_without_message(self, pure1_client, response, expected):
        """An error answer with no usable message falls back to the status."""
        pure1_client.get_arrays.return_value = response

        msg = _failure_message(ansible_module())

        assert msg == "Pure1 request get_arrays failed " + expected

    def test_preflight_error_message_is_condensed(self, pure1_client):
        """The API message is put on one line and a token in it is hidden."""
        pure1_client.get_arrays.return_value = error_response(
            401, "bad token\n%s\nretry" % JWT
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Pure1 request get_arrays failed (HTTP 401): bad token ******** retry"
        )

    def test_preflight_exception_names_type_and_text(self, pure1_client):
        """An exception from get_arrays is not an authentication failure."""
        pure1_client.get_arrays.side_effect = TimeoutError(
            "The read operation timed out"
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Pure1 request get_arrays failed: "
            "TimeoutError: The read operation timed out"
        )

    def test_preflight_exception_without_text(self, pure1_client):
        """An exception with no text is reported by its type alone."""
        pure1_client.get_arrays.side_effect = TimeoutError()

        msg = _failure_message(ansible_module())

        assert msg == "Pure1 request get_arrays failed: TimeoutError"

    def test_preflight_pure_error_drops_response_body(self, pure1_client):
        """An SDK error raised by get_arrays is condensed to status and reason."""
        pure1_client.get_arrays.side_effect = PureError(
            "(401)\nReason: Unauthorized\nHTTP response body: b'%s'\n" % JWT
        )

        msg = _failure_message(ansible_module())

        assert msg == (
            "Pure1 request get_arrays failed: PureError: (401) Reason: Unauthorized"
        )


class TestPure1Items:
    """pure1_items runs a client call and returns the items of its answer."""

    def test_valid_answer_gives_the_items_as_a_list(self, pure1_client):
        """The call is made once, as the module wrote it."""
        pods = [sdk_model("Pod", name="pod-a"), sdk_model("Pod", name="pod-b")]
        pure1_client.get_pods.return_value = valid_response(pods)
        module = ansible_module()

        items = pure1_items(
            module,
            "get_pods",
            lambda: pure1_client.get_pods(filter="arrays.name='foo'"),
        )

        assert items == pods
        assert isinstance(items, list)
        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='foo'")
        module.fail_json.assert_not_called()

    def test_call_without_arguments_is_given_as_a_function(self, pure1_client):
        """pylint refuses a lambda around a call that takes no argument."""
        pods = [sdk_model("Pod", name="pod-a")]
        pure1_client.get_pods.return_value = valid_response(pods)

        def all_pods():
            return pure1_client.get_pods()

        assert pure1_items(ansible_module(), "get_pods", all_pods) == pods
        pure1_client.get_pods.assert_called_once_with()

    @pytest.mark.parametrize(
        "response",
        [
            pytest.param(valid_response(), id="no items"),
            pytest.param(valid_response(total_item_count=5), id="count only"),
            pytest.param(ValidResponse(200, None, None, None, {}), id="no body"),
        ],
    )
    def test_answer_without_items_gives_an_empty_list(self, response):
        assert pure1_items(ansible_module(), "get_pods", lambda: response) == []

    def test_later_pages_are_read(self, pure1_client):
        """The SDK fetches the next page while the items are read."""
        pods = [sdk_model("Pod", name=name) for name in ("pod-a", "pod-b", "pod-c")]

        def next_page(**kwargs):
            # what the SDK gets from Pure1 for the page after the first
            return ApiResponse(200, {}, sdk_model("PodGetResponse", items=pods[2:]))

        response = _paged_response(pods[:2], next_page)

        assert pure1_items(ansible_module(), "get_pods", lambda: response) == pods

    def test_page_with_more_items_than_its_total_is_returned_whole(self):
        """The SDK iterator ends where its place in a page reaches the total."""
        pods = [sdk_model("Pod", name=name) for name in ("pod-a", "pod-b", "pod-c")]
        # what the SDK hands out of such a page
        assert list(valid_response(pods, total_item_count=2).items) == pods[:2]

        items = pure1_items(
            ansible_module(),
            "get_pods",
            lambda: valid_response(pods, total_item_count=2),
        )

        assert items == pods

    def test_later_page_with_more_items_than_the_total_is_returned_whole(self):
        pods = [sdk_model("Pod", name="pod-{0}".format(number)) for number in range(4)]

        def next_page(**kwargs):
            return ApiResponse(200, {}, sdk_model("PodGetResponse", items=pods[1:]))

        # the first page has one pod and a total of two
        response = _paged_response(pods[:1], next_page)

        assert pure1_items(ansible_module(), "get_pods", lambda: response) == pods

    @pytest.mark.parametrize(
        "total",
        [
            pytest.param(5, id="total above the limit"),
            pytest.param(1, id="total below the limit"),
            pytest.param(None, id="no total"),
        ],
    )
    def test_limit_of_the_call_ends_a_page_that_pure1_sent_whole(self, total):
        """get_arrays_tags sends every tag, whatever limit the call gives."""
        tags = [sdk_model("Tag", key="key-{0}".format(number)) for number in range(5)]
        page = ItemIterator(
            None, {"limit": 2}, None, total, tags, None, more_items_remaining=False
        )
        response = ValidResponse(200, None, total, page, {})

        items = pure1_items(ansible_module(), "get_arrays_tags", lambda: response)

        assert items == tags[:2]

    def test_items_that_are_no_sdk_iterator_are_read_as_they_are(self):
        """Nothing is looked for in what is not the iterator of the SDK."""
        pods = [sdk_model("Pod", name="pod-a"), sdk_model("Pod", name="pod-b")]
        response = ValidResponse(200, None, 1, iter(pods), {})

        assert pure1_items(ansible_module(), "get_pods", lambda: response) == pods

    @pytest.mark.parametrize(
        "response, expected",
        [
            pytest.param(
                error_response(
                    400, "Illegal filter format", "Unexpected input at character 16"
                ),
                "(HTTP 400): Illegal filter format: Unexpected input at character 16",
                id="message and context",
            ),
            pytest.param(
                error_response(404, context="No resources with name: foo"),
                "(HTTP 404): No resources with name: foo",
                id="context alone",
            ),
            pytest.param(error_response(403), "(HTTP 403): Forbidden", id="no message"),
            pytest.param(
                ErrorResponse(
                    400,
                    [
                        ApiError("No arrays: a", "Invalid value"),
                        ApiError("No arrays: b", "Invalid value"),
                        ApiError("No arrays: a", "Invalid value"),
                    ],
                    {},
                ),
                "(HTTP 400): Invalid value: No arrays: a; Invalid value: No arrays: b",
                id="every error once",
            ),
            pytest.param(
                ValidResponse(429, None, None, None, {}),
                "(HTTP 429): Too Many Requests",
                id="status alone",
            ),
        ],
    )
    def test_error_answer_fails_the_task(self, response, expected):
        """An error answer has no items: its status and messages are reported."""
        msg = _request_failure(pure1_items, ansible_module(), lambda: response)

        assert msg == "Pure1 request get_pods failed " + expected

    @pytest.mark.parametrize(
        "error, expected",
        [
            pytest.param(
                PureError("(0)\nReason: SSLError\nbad handshake\n"),
                "PureError: (0) Reason: SSLError bad handshake",
                id="PureError",
            ),
            pytest.param(
                _read_timeout(),
                "ReadTimeoutError: HTTPSConnectionPool(host="
                "'api.pure1.purestorage.com', port=443): "
                "Read timed out. (read timeout=15.0)",
                id="urllib3 timeout",
            ),
            pytest.param(
                TypeError("get_pods() got an unexpected keyword argument 'filters'"),
                "TypeError: get_pods() got an unexpected keyword argument 'filters'",
                id="wrong call",
            ),
        ],
    )
    def test_raised_error_fails_the_task(self, pure1_client, error, expected):
        """What the SDK or the transport raises is reported with its type."""
        pure1_client.get_pods.side_effect = error

        msg = _request_failure(
            pure1_items, ansible_module(), lambda: pure1_client.get_pods(limit=5)
        )

        assert msg == "Pure1 request get_pods failed: " + expected

    def test_certificate_error_is_one_line(self, pure1_client):
        pure1_client.get_pods.side_effect = _certificate_error("/api/1.7/pods")

        msg = _request_failure(
            pure1_items, ansible_module(), lambda: pure1_client.get_pods(limit=5)
        )

        assert msg.startswith(
            "Pure1 request get_pods failed: MaxRetryError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Max retries exceeded with url: /api/1.7/pods "
        )
        assert "certificate verify failed" in msg
        assert "\n" not in msg

    def test_error_while_the_items_are_read_fails_the_task(self):
        """The SDK raises when it cannot fetch the next page."""

        def next_page(**kwargs):
            raise _read_timeout()

        response = _paged_response([sdk_model("Pod", name="pod-a")], next_page)

        msg = _request_failure(pure1_items, ansible_module(), lambda: response)

        assert msg == (
            "Pure1 request get_pods failed: PureError: Failed to collect more items: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Read timed out. (read timeout=15.0)"
        )

    def test_secrets_are_hidden_in_an_error_answer(self):
        response = error_response(401, "bad token\n%s" % JWT, "password s3cret")

        msg = _request_failure(
            pure1_items, ansible_module(password="s3cret"), lambda: response
        )

        assert msg == (
            "Pure1 request get_pods failed (HTTP 401): "
            "bad token ********: password ********"
        )

    def test_secrets_are_hidden_in_a_raised_error(self, pure1_client, monkeypatch):
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        pure1_client.get_pods.side_effect = PureError(
            "(403)\nReason: Forbidden with s3cret and sdkpass\n"
            "HTTP response headers: HTTPHeaderDict({'Authorization': 'Bearer %s'})\n"
            "HTTP response body: b'%s'\n" % (JWT, JWT)
        )

        msg = _request_failure(
            pure1_items,
            ansible_module(password="s3cret"),
            lambda: pure1_client.get_pods(limit=5),
        )

        assert msg == (
            "Pure1 request get_pods failed: PureError: (403) Reason: "
            "Forbidden with ******** and ********"
        )

    def test_error_text_is_bounded(self):
        response = error_response(400, "x" * 5000)

        msg = _request_failure(pure1_items, ansible_module(), lambda: response)

        assert msg.endswith("...")
        assert len(msg) < 600

    def test_error_text_of_several_errors_is_bounded(self):
        """The errors of an answer are cut as a whole, not only each of them."""
        response = ErrorResponse(
            400, [ApiError(None, "x" * 800), ApiError(None, "y" * 800)], {}
        )

        msg = _request_failure(pure1_items, ansible_module(), lambda: response)

        assert msg.endswith("...")
        assert len(msg) < 600

    def test_error_answer_can_be_raised_to_the_module(self):
        """With fail=False the module gets the status and the text to act on."""
        module = ansible_module()
        response = error_response(404, "Not found", "No resources with name: v1")

        with pytest.raises(Pure1RequestError) as raised:
            pure1_items(module, "get_volumes", lambda: response, fail=False)

        error = raised.value
        assert error.action == "get_volumes"
        assert error.status_code == 404
        assert error.text == "Not found: No resources with name: v1"
        assert str(error) == (
            "Pure1 request get_volumes failed (HTTP 404): "
            "Not found: No resources with name: v1"
        )
        module.fail_json.assert_not_called()

    def test_raised_error_can_be_raised_to_the_module(self, pure1_client):
        """An error the SDK raised has no status."""
        module = ansible_module()
        pure1_client.get_volumes.side_effect = _read_timeout()

        with pytest.raises(Pure1RequestError) as raised:
            pure1_items(
                module,
                "get_volumes",
                lambda: pure1_client.get_volumes(limit=5),
                fail=False,
            )

        error = raised.value
        assert error.status_code is None
        assert error.text.startswith("ReadTimeoutError: HTTPSConnectionPool(")
        assert str(error) == "Pure1 request get_volumes failed: " + error.text
        module.fail_json.assert_not_called()


class TestPure1Response:
    """pure1_response runs a client call and returns its answer."""

    def test_valid_answer_is_returned_with_its_items_unread(self, pure1_client):
        """What a count or a change needs: the answer itself."""
        pods = [sdk_model("Pod", name="pod-a"), sdk_model("Pod", name="pod-b")]
        response = valid_response(pods, total_item_count=7)
        pure1_client.get_pods.return_value = response
        module = ansible_module()

        result = pure1_response(
            module, "get_pods", lambda: pure1_client.get_pods(limit=0)
        )

        assert result is response
        assert result.total_item_count == 7
        pure1_client.get_pods.assert_called_once_with(limit=0)
        # the items are still there to read
        assert list(result.items) == pods
        module.fail_json.assert_not_called()

    def test_later_pages_are_not_fetched(self):
        def next_page(**kwargs):
            pytest.fail("pure1_response read the items")

        response = _paged_response([sdk_model("Pod", name="pod-a")], next_page)

        result = pure1_response(ansible_module(), "get_pods", lambda: response)

        assert result is response

    def test_error_answer_fails_the_task(self, pure1_client):
        pure1_client.delete_arrays_tags.return_value = error_response(
            400, "Invalid value", "No arrays with name: foo"
        )
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_response(
                module,
                "delete_arrays_tags",
                lambda: pure1_client.delete_arrays_tags(
                    resource_names=["foo"], keys=["site"]
                ),
            )

        assert module.fail_json.call_args[1]["msg"] == (
            "Pure1 request delete_arrays_tags failed (HTTP 400): "
            "Invalid value: No arrays with name: foo"
        )

    def test_raised_error_fails_the_task(self, pure1_client):
        pure1_client.get_pods.side_effect = PureError("no route to Pure1")

        msg = _request_failure(
            pure1_response, ansible_module(), lambda: pure1_client.get_pods(limit=0)
        )

        assert msg == "Pure1 request get_pods failed: PureError: no route to Pure1"

    def test_success_is_status_200_alone(self):
        """As in the modules: Pure1 answers every call that worked with 200."""
        msg = _request_failure(
            pure1_response,
            ansible_module(),
            lambda: ValidResponse(204, None, None, None, {}),
        )

        assert msg == "Pure1 request get_pods failed (HTTP 204): No Content"

    def test_error_can_be_raised_to_the_module(self):
        module = ansible_module()

        with pytest.raises(Pure1RequestError) as raised:
            pure1_response(
                module, "get_invoices", lambda: error_response(403), fail=False
            )

        assert raised.value.status_code == 403
        assert raised.value.text == "Forbidden"
        module.fail_json.assert_not_called()


class TestPure1ErrorText:
    """pure1_error_text gives a module the text of an exception for its own message."""

    def test_names_the_type(self):
        error = TimeoutError("The read operation timed out")

        assert pure1_error_text(ansible_module(), error) == (
            "TimeoutError: The read operation timed out"
        )

    def test_type_alone_when_there_is_no_text(self):
        assert pure1_error_text(ansible_module(), TimeoutError()) == "TimeoutError"

    def test_pure_error_is_named_once(self):
        error = PureError("Failed to collect more items")

        assert pure1_error_text(ansible_module(), error) == (
            "PureError: Failed to collect more items"
        )

    def test_secrets_are_hidden_and_the_text_is_one_line(self, monkeypatch):
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "envpass")
        error = PureError(
            "(401)\nReason: Unauthorized for s3cret or envpass\n"
            "HTTP response headers: HTTPHeaderDict({'X-Request-ID': 'abc'})\n"
            "HTTP response body: b'%s'\n" % JWT
        )

        text = pure1_error_text(ansible_module(password="s3cret"), error)

        assert text == "PureError: (401) Reason: Unauthorized for ******** or ********"

    def test_token_and_key_are_hidden(self):
        error = ValueError("Bearer %s\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\n" % JWT)

        text = pure1_error_text(ansible_module(), error)

        assert text == "ValueError: Bearer ******** ********"

    def test_text_is_bounded(self):
        text = pure1_error_text(ansible_module(), RuntimeError("x" * 5000))

        assert text.endswith("...")
        assert len(text) < 550

    def test_request_error_gives_its_own_message(self):
        error = Pure1RequestError("get_pods", 403, "Forbidden")

        assert pure1_error_text(ansible_module(), error) == (
            "Pure1 request get_pods failed (HTTP 403): Forbidden"
        )


class TestFilterString:
    """filter_string quotes a value for a Pure1 filter."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            pytest.param("array-1", "'array-1'", id="plain"),
            pytest.param("", "''", id="empty"),
            pytest.param("o'brien", "'o\\'brien'", id="single quote"),
            pytest.param("a\\b", "'a\\\\b'", id="backslash"),
            # the backslash of the value is escaped before the quote gets its own
            pytest.param("a\\'b", "'a\\\\\\'b'", id="backslash and quote"),
            pytest.param('say "x"', "'say \"x\"'", id="double quote is left"),
        ],
    )
    def test_value_is_quoted_and_escaped(self, value, expected):
        assert filter_string(value) == expected

    def test_filter_is_built_from_it(self):
        assert "arrays.name=" + filter_string("o'brien") == "arrays.name='o\\'brien'"


TOO_LONG = "The request is too long for Pure1: give fewer names or IDs in one task."
# The helpers that run a request; each takes a request function
REQUEST_HELPERS = [
    pytest.param(pure1_items, id="pure1_items"),
    pytest.param(pure1_response, id="pure1_response"),
    pytest.param(pure1_list, id="pure1_list"),
]


def _hardware(count):
    """Return that many hardware items, as get_hardware lists them."""
    return [
        sdk_model("Hardware", name="hw-{0}".format(number)) for number in range(count)
    ]


def _serve(pure1_client, count=8, **options):
    """Make get_hardware list that many items, three a page; return items and answers."""
    items = _hardware(count)
    answers = paged_answers("HardwareGetResponse", items, page_size=3, **options)
    pure1_client.get_hardware.side_effect = answers
    return items, answers


def _paging(answers):
    """Return limit, offset and continuation token of every request, in order."""
    return [
        (request.get("limit"), request.get("offset"), request.get("continuation_token"))
        for request in answers.requests
    ]


def _list_failure(module, request, **options):
    """Run pure1_list and return the message it failed with."""
    with pytest.raises(SystemExit):
        pure1_list(module, "get_hardware", request, **options)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


def _gateway_refusal():
    """Return the SDK's answer to a request that is too long, up to 8192 bytes.

    Pure1 answers HTTP 400 with an HTML page, and py-pure-client makes an
    error answer with one message of its own of a body that is no JSON.
    """
    error = ApiException(status=400, reason="Bad Request")
    error.body = (
        '<!doctype html><html lang="en"><head><title>HTTP Status 400 '
        "– Bad Request</title></head><body></body></html>"
    )
    error.headers = {"Content-Type": "text/html;charset=utf-8"}
    return NEWEST.Client._create_error_response(None, error)


def _refusal_of_pure1(body):
    """Return the SDK's answer to a request Pure1 refuses with a JSON body."""
    error = ApiException(status=400, reason="Bad Request")
    error.body = body
    error.headers = {"Content-Type": "application/json"}
    return NEWEST.Client._create_error_response(None, error)


def _uri_too_long():
    """Return what py-pure-client raises for a request line above 8192 bytes.

    Pure1 answers HTTP 414, a status py-pure-client has no error answer for.
    """
    error = ApiException(status=414, reason="Request-URI Too Large")
    error.body = "Request-URI Too Large"
    error.headers = {"Content-Type": "text/plain; charset=UTF-8"}
    return PureError(error)


# The page Pure1 answers a request that is too long with, up to 8192 bytes
GATEWAY_PAGE = (
    '<!doctype html><html lang="en"><head><title>HTTP Status 400 '
    "– Bad Request</title></head><body></body></html>"
)
# What Pure1 answers a request that it refuses for what it asks
ILLEGAL_FILTER = '{"errors":[{"message":"Illegal filter format","context":"x"}]}'


def _refused_later_page(body=GATEWAY_PAGE):
    """Return the HTTP 400 with this body as the transport raises it.

    py-pure-client makes an error answer of it for the first request only.
    From the request for a later page it comes out of the iteration, with
    its headers and its body in the text of a PureError.
    """
    error = ApiException(status=400, reason="Bad Request")
    error.body = body
    error.headers = {"Content-Type": "text/html;charset=utf-8"}
    return error


def _answer_with_a_refused_later_page(body=GATEWAY_PAGE):
    """Return a valid answer whose next page Pure1 refuses with HTTP 400."""

    def next_page(**kwargs):
        raise _refused_later_page(body)

    return _paged_response([sdk_model("Pod", name="pod-a")], next_page)


def _array(name, **fields):
    return sdk_model("Array", name=name, **fields)


class TestPure1List:
    """pure1_list reads a listing page by page: tokens by the SDK, offsets itself."""

    def test_unsorted_listing_is_read_through_its_tokens(self, pure1_client):
        """The module makes one call, the SDK fetches the pages of the tokens."""
        items, answers = _serve(pure1_client)
        module = ansible_module()

        listing = pure1_list(
            module,
            "get_hardware",
            lambda **page: pure1_client.get_hardware(filter="type='fan'", **page),
        )

        assert listing == Pure1Listing(items=items, total=8, truncated=False)
        assert isinstance(listing.items, list)
        assert answers.requests == [
            {"filter": "type='fan'"},
            {"filter": "type='fan'", "continuation_token": "page-3"},
            {"filter": "type='fan'", "continuation_token": "page-6"},
        ]
        pure1_client.get_hardware.assert_called_once_with(filter="type='fan'")
        module.fail_json.assert_not_called()

    def test_sorted_listing_is_read_by_offset(self, pure1_client):
        """Pure1 gives no token with a sort, and the SDK stops after one page."""
        items, answers = _serve(pure1_client)

        def request(**page):
            return pure1_client.get_hardware(sort=["name", "id"], **page)

        assert pure1_items(ansible_module(), "get_hardware", request) == items[:3]
        del answers.requests[:]

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, 8, False)
        assert answers.requests == [
            {"sort": ["name", "id"]},
            {"sort": ["name", "id"], "offset": 3},
            {"sort": ["name", "id"], "offset": 6},
        ]

    @pytest.mark.parametrize(
        "limit, paging",
        [
            pytest.param(1, [(1, None, None)], id="one"),
            pytest.param(2, [(2, None, None)], id="below a page"),
            pytest.param(3, [(3, None, None)], id="a page"),
            pytest.param(4, [(4, None, None), (4, None, "page-3")], id="above a page"),
            pytest.param(6, [(6, None, None), (6, None, "page-3")], id="two pages"),
            pytest.param(
                7,
                [(7, None, None), (7, None, "page-3"), (7, None, "page-6")],
                id="into the last page",
            ),
            pytest.param(
                8,
                [(8, None, None), (8, None, "page-3"), (8, None, "page-6")],
                id="all",
            ),
            pytest.param(
                20,
                [(20, None, None), (20, None, "page-3"), (20, None, "page-6")],
                id="above all",
            ),
        ],
    )
    def test_limit_on_an_unsorted_listing(self, pure1_client, limit, paging):
        """At most limit items, and no page that they do not need."""
        items, answers = _serve(pure1_client)

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(filter="type='fan'", **page),
            limit=limit,
        )

        assert listing == Pure1Listing(items[:limit], 8, limit < 8)
        assert _paging(answers) == paging

    @pytest.mark.parametrize(
        "limit, paging",
        [
            pytest.param(1, [(1, None, None)], id="one"),
            pytest.param(2, [(2, None, None)], id="below a page"),
            pytest.param(3, [(3, None, None)], id="a page"),
            pytest.param(4, [(4, None, None), (1, 3, None)], id="above a page"),
            pytest.param(6, [(6, None, None), (3, 3, None)], id="two pages"),
            pytest.param(
                7,
                [(7, None, None), (4, 3, None), (1, 6, None)],
                id="into the last page",
            ),
            pytest.param(8, [(8, None, None), (5, 3, None), (2, 6, None)], id="all"),
            pytest.param(
                20, [(20, None, None), (17, 3, None), (14, 6, None)], id="above all"
            ),
        ],
    )
    def test_limit_on_a_sorted_listing(self, pure1_client, limit, paging):
        """Each page is asked for with what is still wanted, from where it starts."""
        items, answers = _serve(pure1_client)

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(sort=["name", "id"], **page),
            limit=limit,
        )

        assert listing == Pure1Listing(items[:limit], 8, limit < 8)
        assert _paging(answers) == paging

    def test_limit_0_gives_the_total_alone(self, pure1_client):
        dummy, answers = _serve(pure1_client)

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(filter="type='fan'", **page),
            limit=0,
        )

        assert listing == Pure1Listing([], 8, True)
        assert answers.requests == [{"filter": "type='fan'", "limit": 0}]

    def test_limit_0_reads_no_item_that_pure1_sends_all_the_same(self, pure1_client):
        """get_arrays_tags ignores the limit and sends every tag."""
        tags = [sdk_model("Tag", key="site"), sdk_model("Tag", key="rack")]
        pure1_client.get_arrays_tags.return_value = valid_response(tags)

        listing = pure1_list(
            ansible_module(),
            "get_arrays_tags",
            lambda **page: pure1_client.get_arrays_tags(namespaces=["pure1"], **page),
            limit=0,
        )

        assert listing == Pure1Listing([], 2, True)
        pure1_client.get_arrays_tags.assert_called_once_with(
            namespaces=["pure1"], limit=0
        )

    def test_limit_0_asks_for_one_item_where_pure1_counts_nothing(self, pure1_client):
        """get_protection_group_snapshots counts 0 when no item is asked for."""
        snapshots = [
            sdk_model("ProtectionGroupSnapshot", name="pg.{0}".format(number))
            for number in range(5)
        ]
        response = "ProtectionGroupSnapshotGetResponse"
        counted = paged_answers(response, snapshots, page_size=3)
        uncounted = paged_answers(response, snapshots, 3, total_item_count=0)

        def answer(**kwargs):
            return (uncounted if kwargs["limit"] == 0 else counted)(**kwargs)

        pure1_client.get_protection_group_snapshots.side_effect = answer

        listing = pure1_list(
            ansible_module(),
            "get_protection_group_snapshots",
            lambda **page: pure1_client.get_protection_group_snapshots(
                sort=["created-", "id"], **page
            ),
            limit=0,
        )

        assert listing == Pure1Listing([], 5, True)
        assert uncounted.requests == [{"sort": ["created-", "id"], "limit": 0}]
        assert counted.requests == [{"sort": ["created-", "id"], "limit": 1}]

    def test_limit_0_asks_again_where_nothing_matches(self, pure1_client):
        """A total of 0 is believed when one item is asked for and none comes."""
        dummy, answers = _serve(pure1_client, count=0)

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(filter="type='fan'", **page),
            limit=0,
        )

        assert listing == Pure1Listing([], 0, False)
        assert answers.requests == [
            {"filter": "type='fan'", "limit": 0},
            {"filter": "type='fan'", "limit": 1},
        ]

    def test_limit_0_with_an_item_that_pure1_did_not_count(self, pure1_client):
        """The item is not returned, and how many there are is not known."""
        items = _hardware(2)
        uncounted = paged_answers("HardwareGetResponse", items, total_item_count=0)
        without = paged_answers("HardwareGetResponse", items, total_item_count=None)

        def answer(**kwargs):
            return (uncounted if kwargs["limit"] == 0 else without)(**kwargs)

        pure1_client.get_hardware.side_effect = answer

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request, limit=0)

        assert listing == Pure1Listing([], None, True)
        assert without.requests == [{"limit": 1}]

    def test_error_while_pure1_is_asked_for_one_item_fails_the_task(self, pure1_client):
        pure1_client.get_hardware.side_effect = [
            valid_response(),
            error_response(503, "Service unavailable"),
        ]

        def request(**page):
            return pure1_client.get_hardware(**page)

        msg = _list_failure(ansible_module(), request, limit=0)

        assert (
            msg == "Pure1 request get_hardware failed (HTTP 503): Service unavailable"
        )

    @pytest.mark.parametrize(
        "limit, calls",
        [
            pytest.param(None, 1, id="None"),
            pytest.param(0, 2, id="0"),
            pytest.param(5, 1, id="5"),
        ],
    )
    def test_listing_without_items(self, pure1_client, limit, calls):
        pure1_client.get_hardware.side_effect = lambda **kwargs: valid_response()

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request, limit=limit)

        assert listing == Pure1Listing([], 0, False)
        assert pure1_client.get_hardware.call_count == calls

    @pytest.mark.parametrize("limit", [None, 5])
    def test_request_that_takes_no_paging_keyword_is_refused(self, pure1_client, limit):
        """It would work until a listing needs a second page."""
        _serve(pure1_client)
        module = ansible_module()

        with pytest.raises(TypeError) as refused:
            pure1_list(
                module,
                "get_hardware",
                lambda: pure1_client.get_hardware(sort=["name"]),
                limit=limit,
            )

        assert str(refused.value) == (
            "the request function of pure1_list() must take the paging "
            "keywords (**page) and hand them on"
        )
        pure1_client.get_hardware.assert_not_called()
        module.fail_json.assert_not_called()

    def test_request_that_takes_no_offset_is_refused(self, pure1_client):
        """A sorted listing would end after its first page."""
        _serve(pure1_client)

        def request(limit=None):
            return pure1_client.get_hardware(sort=["name"], limit=limit)

        with pytest.raises(TypeError, match="must take the paging keywords"):
            pure1_list(ansible_module(), "get_hardware", request)

        pure1_client.get_hardware.assert_not_called()

    def test_request_that_names_the_paging_keywords_is_accepted(self, pure1_client):
        items, answers = _serve(pure1_client)

        def request(limit=None, offset=None):
            return pure1_client.get_hardware(
                sort=["name", "id"], limit=limit, offset=offset
            )

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, 8, False)
        assert _paging(answers) == [
            (None, None, None),
            (None, 3, None),
            (None, 6, None),
        ]

    def test_request_without_a_signature_is_called(self, pure1_client, monkeypatch):
        """Some callables have none to look at."""
        items, dummy = _serve(pure1_client, count=2)

        def no_signature(function):
            raise ValueError("no signature found for {0}".format(function))

        def request(**page):
            return pure1_client.get_hardware(**page)

        monkeypatch.setattr(
            pure1_utils, "inspect", types.SimpleNamespace(signature=no_signature)
        )

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, 2, False)

    def test_answer_without_a_body_is_a_listing_without_items(self):
        """It has no items and no total, and is asked for once."""
        answers = iter([ValidResponse(200, None, None, None, {})])

        listing = pure1_list(
            ansible_module(), "get_hardware", lambda **page: next(answers)
        )

        assert listing == Pure1Listing([], None, False)

    def test_result_unpacks_into_items_total_and_truncated(self, pure1_client):
        items, dummy = _serve(pure1_client, count=2)

        def request(**page):
            return pure1_client.get_hardware(**page)

        found, total, truncated = pure1_list(ansible_module(), "get_hardware", request)

        assert (found, total, truncated) == (items, 2, False)

    @pytest.mark.parametrize(
        "sort, paging",
        [
            pytest.param(
                None,
                [
                    (None, None, None),
                    (None, None, "page-3"),
                    (None, None, "page-6"),
                    (None, 8, None),
                ],
                id="unsorted",
            ),
            pytest.param(
                ["name", "id"],
                [(None, None, None), (None, 3, None), (None, 6, None), (None, 8, None)],
                id="sorted",
            ),
        ],
    )
    def test_listing_without_a_total_is_read_until_a_page_is_empty(
        self, pure1_client, sort, paging
    ):
        """Without a total only an empty page tells that nothing is left."""
        items, answers = _serve(pure1_client, total_item_count=None)

        def request(**page):
            if sort:
                page["sort"] = sort
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, None, False)
        assert _paging(answers) == paging

    @pytest.mark.parametrize(
        "limit, paging, truncated",
        [
            pytest.param(3, [(3, None, None)], True, id="limit reached"),
            pytest.param(
                8,
                [(8, None, None), (5, 3, None), (2, 6, None)],
                True,
                id="limit reached with the last item",
            ),
            pytest.param(
                20,
                [(20, None, None), (17, 3, None), (14, 6, None), (12, 8, None)],
                False,
                id="limit above all",
            ),
            pytest.param(0, [(0, None, None)], True, id="limit 0"),
        ],
    )
    def test_limit_without_a_total(self, pure1_client, limit, paging, truncated):
        """A limit that was reached may have cut the listing: nothing tells."""
        items, answers = _serve(pure1_client, total_item_count=None)

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(sort=["name", "id"], **page),
            limit=limit,
        )

        assert listing == Pure1Listing(items[:limit], None, truncated)
        assert _paging(answers) == paging

    @pytest.mark.parametrize(
        "options, total",
        [
            pytest.param({}, 5, id="total"),
            pytest.param({"total_item_count": None}, None, id="no total"),
        ],
    )
    def test_sorted_listing_of_a_method_without_offset_ends_truncated(
        self, pure1_client, options, total
    ):
        """The four get_recommendations methods take no offset."""
        recommendations = [
            sdk_model("Recommendation", id="r-{0}".format(number))
            for number in range(5)
        ]
        answers = paged_answers(
            "RecommendationGetResponse", recommendations, page_size=3, **options
        )
        pure1_client.get_recommendations.side_effect = answers
        module = ansible_module()

        listing = pure1_list(
            module,
            "get_recommendations",
            lambda **page: pure1_client.get_recommendations(sort=["id"], **page),
        )

        assert listing == Pure1Listing(recommendations[:3], total, True)
        assert answers.requests == [{"sort": ["id"]}]
        module.fail_json.assert_not_called()

    def test_unsorted_listing_of_a_method_without_offset_is_complete(
        self, pure1_client
    ):
        recommendations = [
            sdk_model("Recommendation", id="r-{0}".format(number))
            for number in range(5)
        ]
        answers = paged_answers("RecommendationGetResponse", recommendations, 3)
        pure1_client.get_recommendations.side_effect = answers

        def request(**page):
            return pure1_client.get_recommendations(**page)

        listing = pure1_list(ansible_module(), "get_recommendations", request)

        assert listing == Pure1Listing(recommendations, 5, False)
        assert _paging(answers) == [(None, None, None), (None, None, "page-3")]

    def test_another_type_error_for_a_later_page_fails_the_task(self, pure1_client):
        """Only a call that does not take offset ends the listing."""
        _serve(pure1_client)

        def request(**page):
            if "offset" in page:
                raise TypeError("'NoneType' object is not iterable")
            return pure1_client.get_hardware(sort=["name"], **page)

        msg = _list_failure(ansible_module(), request)

        assert msg == (
            "Pure1 request get_hardware failed: "
            "TypeError: 'NoneType' object is not iterable"
        )

    @pytest.mark.parametrize(
        "error, expected",
        [
            pytest.param(
                TypeError("'offset' must be an integer"),
                "TypeError: 'offset' must be an integer",
                id="another TypeError about offset",
            ),
            pytest.param(
                PureError("got an unexpected keyword argument 'offset'"),
                "PureError: got an unexpected keyword argument 'offset'",
                id="another exception with the words",
            ),
        ],
    )
    def test_another_error_that_names_offset_fails_the_task(
        self, pure1_client, error, expected
    ):
        """Only the TypeError of a call that does not take offset ends the listing."""
        _serve(pure1_client)

        def request(**page):
            if "offset" in page:
                raise error
            return pure1_client.get_hardware(sort=["name"], **page)

        msg = _list_failure(ansible_module(), request)

        assert msg == "Pure1 request get_hardware failed: " + expected

    def test_later_page_that_starts_as_the_listing_does_is_read(self, pure1_client):
        """Only a whole page that comes again tells that Pure1 ignored the offset."""
        items = _hardware(8)
        items[3] = sdk_model("Hardware", name="hw-0")
        assert items[3] == items[0]
        answers = paged_answers("HardwareGetResponse", items, page_size=3)
        pure1_client.get_hardware.side_effect = answers

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(sort=["name"], **page),
        )

        assert listing == Pure1Listing(items, 8, False)
        assert _paging(answers) == [
            (None, None, None),
            (None, 3, None),
            (None, 6, None),
        ]

    def test_listing_that_ignores_the_offset_ends_truncated(self, pure1_client):
        """Pure1 gives the first page again: its items are not added twice."""
        tags = [sdk_model("Tag", key="key-{0}".format(number)) for number in range(5)]
        pure1_client.get_arrays_tags.side_effect = lambda **kwargs: valid_response(
            tags[:3], total_item_count=5
        )

        listing = pure1_list(
            ansible_module(),
            "get_arrays_tags",
            lambda **page: pure1_client.get_arrays_tags(namespaces=["pure1"], **page),
        )

        assert listing == Pure1Listing(tags[:3], 5, True)
        assert pure1_client.get_arrays_tags.call_count == 2
        pure1_client.get_arrays_tags.assert_called_with(namespaces=["pure1"], offset=3)

    def test_fewer_items_than_pure1_reported_is_truncated(self, pure1_client):
        """The helper asks once for what is missing and gets nothing."""
        items, answers = _serve(pure1_client, count=5, total_item_count=7)

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, 7, True)
        assert _paging(answers) == [
            (None, None, None),
            (None, None, "page-3"),
            (None, 5, None),
        ]

    def test_total_is_what_the_first_answer_reports(self, pure1_client):
        """Pure1 counts again for every page, and items come and go."""
        items = _hardware(9)
        first = paged_answers("HardwareGetResponse", items, 3, total_item_count=8)
        later = paged_answers("HardwareGetResponse", items, 3, total_item_count=9)

        def answer(**kwargs):
            return (later if "offset" in kwargs else first)(**kwargs)

        pure1_client.get_hardware.side_effect = answer

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(sort=["name", "id"], **page),
        )

        assert listing == Pure1Listing(items, 8, False)
        assert _paging(later) == [(None, 3, None), (None, 6, None)]

    def test_page_with_more_items_than_its_total_is_returned_whole(self, pure1_client):
        """The SDK iterator ends where its place in a page reaches the total."""
        items, answers = _serve(pure1_client, count=3, total_item_count=2)
        # what the SDK hands out of such a page
        assert list(answers().items) == items[:2]
        del answers.requests[:]

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items, 2, False)
        assert _paging(answers) == [(None, None, None)]

    @pytest.mark.parametrize(
        "limit, returned, truncated",
        [
            pytest.param(1, 1, True, id="below the total"),
            pytest.param(2, 2, False, id="the total"),
            pytest.param(3, 3, False, id="the page"),
            pytest.param(4, 3, False, id="above the page"),
        ],
    )
    def test_limit_on_a_page_with_more_items_than_its_total(
        self, pure1_client, limit, returned, truncated
    ):
        """Of what the page carries beyond its total at most the limit is returned."""
        items, answers = _serve(pure1_client, count=3, total_item_count=2)

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request, limit=limit)

        assert listing == Pure1Listing(items[:returned], 2, truncated)
        assert _paging(answers) == [(limit, None, None)]

    def test_sorted_page_with_more_items_than_its_total_is_returned_whole(
        self, pure1_client
    ):
        """The total is reached with the first page, so no other page is asked for."""
        items = _hardware(12)
        answers = paged_answers(
            "HardwareGetResponse", items, page_size=5, total_item_count=4
        )
        pure1_client.get_hardware.side_effect = answers

        listing = pure1_list(
            ansible_module(),
            "get_hardware",
            lambda **page: pure1_client.get_hardware(sort=["name", "id"], **page),
        )

        assert listing == Pure1Listing(items[:5], 4, False)
        assert _paging(answers) == [(None, None, None)]

    def test_page_of_a_token_after_the_total_is_reached_is_not_read(self, pure1_client):
        """The SDK does not follow the token of a page that reached the total."""
        items, answers = _serve(pure1_client, total_item_count=2)

        def request(**page):
            return pure1_client.get_hardware(**page)

        listing = pure1_list(ansible_module(), "get_hardware", request)

        assert listing == Pure1Listing(items[:3], 2, False)
        assert _paging(answers) == [(None, None, None)]

    @pytest.mark.parametrize("limit", [None, 4, 3])
    def test_later_page_with_more_items_than_the_total_is_returned_whole(self, limit):
        hardware = _hardware(4)

        def next_page(**kwargs):
            return ApiResponse(
                200, {}, sdk_model("HardwareGetResponse", items=hardware[1:])
            )

        # the first page has one item and a total of two
        response = _paged_response(hardware[:1], next_page)

        listing = pure1_list(
            ansible_module(), "get_hardware", lambda **page: response, limit=limit
        )

        assert listing == Pure1Listing(hardware[:limit], 2, False)

    def test_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_hardware.return_value = error_response(
            400, "Illegal filter format", "Unexpected input at character 9"
        )

        msg = _list_failure(
            ansible_module(),
            lambda **page: pure1_client.get_hardware(filter="name='o'brien'", **page),
        )

        assert msg == (
            "Pure1 request get_hardware failed (HTTP 400): "
            "Illegal filter format: Unexpected input at character 9"
        )

    def test_raised_error_fails_the_task(self, pure1_client):
        pure1_client.get_hardware.side_effect = _read_timeout()

        msg = _list_failure(
            ansible_module(),
            lambda **page: pure1_client.get_hardware(sort=["name"], **page),
            limit=5,
        )

        assert msg == (
            "Pure1 request get_hardware failed: ReadTimeoutError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Read timed out. (read timeout=15.0)"
        )

    @pytest.mark.parametrize("limit", [None, 2])
    def test_error_while_the_page_of_a_token_is_read_fails_the_task(self, limit):
        """The SDK raises out of the iteration when it cannot fetch the page."""

        def next_page(**kwargs):
            raise _read_timeout()

        response = _paged_response([sdk_model("Hardware", name="hw-0")], next_page)

        msg = _list_failure(ansible_module(), lambda **page: response, limit=limit)

        assert msg == (
            "Pure1 request get_hardware failed: PureError: Failed to collect more "
            "items: HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Read timed out. (read timeout=15.0)"
        )

    def test_page_of_a_token_that_the_limit_does_not_need_is_not_read(self):
        def next_page(**kwargs):
            pytest.fail("pure1_list fetched a page the limit does not need")

        hardware = sdk_model("Hardware", name="hw-0")
        response = _paged_response([hardware], next_page)

        listing = pure1_list(
            ansible_module(), "get_hardware", lambda **page: response, limit=1
        )

        assert listing == Pure1Listing([hardware], 2, True)

    @pytest.mark.parametrize(
        "failure, expected",
        [
            pytest.param(
                lambda: error_response(503, "Service unavailable"),
                "Pure1 request get_hardware failed (HTTP 503): Service unavailable",
                id="error answer",
            ),
            pytest.param(
                PureError("no route to Pure1"),
                "Pure1 request get_hardware failed: PureError: no route to Pure1",
                id="raised error",
            ),
        ],
    )
    def test_error_for_a_page_by_offset_fails_the_task(
        self, pure1_client, failure, expected
    ):
        """No listing is returned short because a later page failed."""
        _serve(pure1_client)

        def request(**page):
            if page.get("offset") == 6:
                if isinstance(failure, Exception):
                    raise failure
                return failure()
            return pure1_client.get_hardware(sort=["name", "id"], **page)

        assert _list_failure(ansible_module(), request) == expected

    def test_error_answer_can_be_raised_to_the_module(self, pure1_client):
        module = ansible_module()
        pure1_client.get_hardware.return_value = error_response(
            404, "Not found", "No resources with name: hw-9"
        )

        with pytest.raises(Pure1RequestError) as raised:
            pure1_list(
                module,
                "get_hardware",
                lambda **page: pure1_client.get_hardware(names=["hw-9"], **page),
                fail=False,
            )

        error = raised.value
        assert error.action == "get_hardware"
        assert error.status_code == 404
        assert error.text == "Not found: No resources with name: hw-9"
        module.fail_json.assert_not_called()

    def test_error_for_a_later_page_can_be_raised_to_the_module(self, pure1_client):
        module = ansible_module()
        _serve(pure1_client)

        def request(**page):
            if "offset" in page:
                raise _read_timeout()
            return pure1_client.get_hardware(sort=["name", "id"], **page)

        with pytest.raises(Pure1RequestError) as raised:
            pure1_list(module, "get_hardware", request, fail=False)

        assert raised.value.status_code is None
        assert str(raised.value) == (
            "Pure1 request get_hardware failed: ReadTimeoutError: "
            "HTTPSConnectionPool(host='api.pure1.purestorage.com', port=443): "
            "Read timed out. (read timeout=15.0)"
        )
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize("fail", [True, False])
    def test_limit_below_0_fails_the_task(self, pure1_client, fail):
        module = ansible_module()

        def request(**page):
            return pure1_client.get_hardware(**page)

        with pytest.raises(SystemExit):
            pure1_list(module, "get_hardware", request, limit=-1, fail=fail)

        module.fail_json.assert_called_once_with(msg="limit must be 0 or more, got -1")
        pure1_client.get_hardware.assert_not_called()


class TestRequestTooLong:
    """A request that is too long for Pure1 fails with a line that says so."""

    def test_text(self):
        assert REQUEST_TOO_LONG == TOO_LONG

    @pytest.mark.parametrize("helper", REQUEST_HELPERS)
    def test_answer_that_is_no_json_is_a_request_too_long(self, helper):
        """Up to 8192 bytes Pure1 answers HTTP 400 with an HTML page."""
        module = ansible_module()
        answer = _gateway_refusal()
        assert answer.errors[0].message == "Response is not a valid JSON"

        msg = _request_failure(helper, module, lambda **page: answer)

        assert msg == "Pure1 request get_pods failed (HTTP 400): " + TOO_LONG

    @pytest.mark.parametrize("helper", REQUEST_HELPERS)
    def test_http_414_is_a_request_too_long(self, pure1_client, helper):
        """Above 8192 bytes py-pure-client raises, with the headers in the text."""
        pure1_client.get_pods.side_effect = _uri_too_long()
        assert "HTTP response headers" in str(_uri_too_long())

        msg = _request_failure(
            helper,
            ansible_module(),
            lambda **page: pure1_client.get_pods(filter="name=('a','b')", **page),
        )

        assert msg == "Pure1 request get_pods failed (HTTP 414): " + TOO_LONG

    @pytest.mark.parametrize("helper", [pure1_items, pure1_list])
    def test_http_414_for_a_later_page_is_a_request_too_long(self, helper):
        """The token of the next page makes the request longer."""

        def next_page(**kwargs):
            raise _uri_too_long()

        response = _paged_response([sdk_model("Pod", name="pod-a")], next_page)

        msg = _request_failure(helper, ansible_module(), lambda **page: response)

        assert msg == "Pure1 request get_pods failed (HTTP 414): " + TOO_LONG

    @pytest.mark.parametrize("helper", [pure1_items, pure1_list])
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(GATEWAY_PAGE, id="HTML page"),
            pytest.param("Bad Request", id="text"),
            pytest.param(None, id="no body"),
        ],
    )
    def test_http_400_without_json_for_a_later_page_is_a_request_too_long(
        self, helper, body
    ):
        """What py-pure-client goes by for the first page: a body that is no JSON."""
        response = _answer_with_a_refused_later_page(body)

        msg = _request_failure(helper, ansible_module(), lambda **page: response)

        assert msg == "Pure1 request get_pods failed (HTTP 400): " + TOO_LONG

    @pytest.mark.parametrize("helper", [pure1_items, pure1_list])
    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(ILLEGAL_FILTER, id="error of Pure1"),
            pytest.param("[]", id="JSON that is no error"),
        ],
    )
    def test_http_400_with_json_for_a_later_page_keeps_its_text(self, helper, body):
        """A request that Pure1 refuses for what it asks is not too long."""
        response = _answer_with_a_refused_later_page(body)

        msg = _request_failure(helper, ansible_module(), lambda **page: response)

        assert msg == (
            "Pure1 request get_pods failed: PureError: Failed to collect more "
            "items: (400) Reason: Bad Request"
        )

    @pytest.mark.parametrize("helper", [pure1_items, pure1_list])
    def test_refused_later_page_can_be_raised_to_the_module(self, helper):
        module = ansible_module()
        response = _answer_with_a_refused_later_page()

        with pytest.raises(Pure1RequestError) as raised:
            helper(module, "get_pods", lambda **page: response, fail=False)

        assert raised.value.status_code == 400
        assert raised.value.text == TOO_LONG
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize("helper", REQUEST_HELPERS)
    @pytest.mark.parametrize(
        "failure, status_code",
        [
            pytest.param(_gateway_refusal, 400, id="HTTP 400"),
            pytest.param(_uri_too_long, 414, id="HTTP 414"),
        ],
    )
    def test_can_be_raised_to_the_module(self, helper, failure, status_code):
        module = ansible_module()

        def request(**page):
            failed = failure()
            if isinstance(failed, Exception):
                raise failed
            return failed

        with pytest.raises(Pure1RequestError) as raised:
            helper(module, "get_pods", request, fail=False)

        assert raised.value.status_code == status_code
        assert raised.value.text == TOO_LONG
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize(
        "response, expected",
        [
            pytest.param(
                _refusal_of_pure1(
                    '{"errors":[{"message":"Illegal filter format","context":'
                    "\"Unexpected input at character 9: name='o'brien'\"}]}"
                ),
                "(HTTP 400): Illegal filter format: "
                "Unexpected input at character 9: name='o'brien'",
                id="HTTP 400 of Pure1",
            ),
            pytest.param(
                _refusal_of_pure1("[]"),
                "(HTTP 400): Response is not an Error object",
                id="JSON that is no error",
            ),
            pytest.param(
                ErrorResponse(
                    404, [ApiError(None, "Response is not a valid JSON")], {}
                ),
                "(HTTP 404): Response is not a valid JSON",
                id="another status",
            ),
            pytest.param(
                ErrorResponse(
                    400,
                    [ApiError("at character 9", "Response is not a valid JSON")],
                    {},
                ),
                "(HTTP 400): Response is not a valid JSON: at character 9",
                id="message with a context",
            ),
            pytest.param(
                ErrorResponse(
                    400,
                    [
                        ApiError(None, "Response is not a valid JSON"),
                        ApiError(None, "Invalid value"),
                    ],
                    {},
                ),
                "(HTTP 400): Response is not a valid JSON; Invalid value",
                id="one of two errors",
            ),
            pytest.param(
                ErrorResponse(400, [], {}), "(HTTP 400): Bad Request", id="none"
            ),
        ],
    )
    def test_another_error_answer_keeps_its_message(self, response, expected):
        msg = _request_failure(pure1_items, ansible_module(), lambda: response)

        assert msg == "Pure1 request get_pods failed " + expected

    @pytest.mark.parametrize(
        "error, expected",
        [
            pytest.param(
                PureError(ApiException(status=413, reason="Payload Too Large")),
                "PureError: (413) Reason: Payload Too Large",
                id="another status",
            ),
            pytest.param(
                PureError("Could not read page (414)"),
                "PureError: Could not read page (414)",
                id="414 elsewhere in the text",
            ),
            pytest.param(
                RuntimeError("PureError: (414)"),
                "RuntimeError: PureError: (414)",
                id="another exception",
            ),
            pytest.param(
                PureError(
                    "Could not retrieve a new access token: {0}".format(_uri_too_long())
                ),
                "PureError: Could not retrieve a new access token: "
                "PureError: (414) Reason: Request-URI Too Large",
                id="414 of another request",
            ),
            pytest.param(
                PureError("Failed to collect more items: (404)\nReason: Not Found\n"),
                "PureError: Failed to collect more items: (404) Reason: Not Found",
                id="another status of a later page",
            ),
            pytest.param(
                PureError(
                    "Retry failed: PureError: Failed to collect more items: "
                    "{0}".format(_refused_later_page())
                ),
                "PureError: Retry failed: PureError: Failed to collect more "
                "items: (400) Reason: Bad Request",
                id="400 of a later page elsewhere in the text",
            ),
            pytest.param(
                RuntimeError(
                    "PureError: Failed to collect more items: "
                    "{0}".format(_refused_later_page())
                ),
                "RuntimeError: PureError: Failed to collect more items: "
                "(400) Reason: Bad Request",
                id="another exception for a later page",
            ),
        ],
    )
    def test_another_raised_error_keeps_its_text(self, pure1_client, error, expected):
        pure1_client.get_pods.side_effect = error
        module = ansible_module()

        with pytest.raises(Pure1RequestError) as raised:
            pure1_items(
                module, "get_pods", lambda: pure1_client.get_pods(limit=5), fail=False
            )

        assert raised.value.status_code is None
        assert str(raised.value) == "Pure1 request get_pods failed: " + expected

    def test_error_text_of_http_414(self):
        """For a module that words the message of a raised error itself."""
        module = ansible_module()
        later_page = PureError(
            "Failed to collect more items: {0}".format(_uri_too_long())
        )

        assert pure1_error_text(module, _uri_too_long()) == TOO_LONG
        assert pure1_error_text(module, later_page) == TOO_LONG

    def test_error_text_of_http_400_for_a_later_page(self):
        module = ansible_module()
        text = "Failed to collect more items: {0}"
        gateway = PureError(text.format(_refused_later_page()))
        pure1 = PureError(text.format(_refused_later_page(ILLEGAL_FILTER)))

        assert pure1_error_text(module, gateway) == TOO_LONG
        assert pure1_error_text(module, pure1) == (
            "PureError: Failed to collect more items: (400) Reason: Bad Request"
        )

    @pytest.mark.parametrize(
        "failure, status_code",
        [
            pytest.param(_gateway_refusal, 400, id="HTTP 400"),
            pytest.param(_uri_too_long, 414, id="HTTP 414"),
        ],
    )
    def test_too_many_array_names_fail_the_task(
        self, pure1_client, failure, status_code
    ):
        """pure1_resolve_arrays sends every name in one request."""
        failed = failure()
        if isinstance(failed, Exception):
            pure1_client.get_arrays.side_effect = failed
        else:
            pure1_client.get_arrays.return_value = failed
        module = ansible_module()
        names = ["array-{0:03d}".format(number) for number in range(300)]

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, names)

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP {0}): {1}".format(
                status_code, TOO_LONG
            )
        )
        assert pure1_client.get_arrays.call_count == 1


class TestFilterIn:
    """filter_in gives the term for a field that equals one of the values."""

    @pytest.mark.parametrize(
        "values, expected",
        [
            pytest.param(["a"], "arrays.name='a'", id="one value"),
            pytest.param(["a", "b"], "arrays.name=('a','b')", id="two values"),
            pytest.param(
                ["c", "a", "b"], "arrays.name=('c','a','b')", id="order is kept"
            ),
            pytest.param([], None, id="no value"),
            pytest.param(None, None, id="None"),
            pytest.param(["a", "b", "a"], "arrays.name=('a','b')", id="value twice"),
            pytest.param(["a", "a"], "arrays.name='a'", id="one value twice"),
            pytest.param(["a", "A"], "arrays.name=('a','A')", id="case is kept"),
            pytest.param(
                ["o'brien", "a\\b"],
                "arrays.name=('o\\'brien','a\\\\b')",
                id="values are escaped",
            ),
            pytest.param(("a", "b"), "arrays.name=('a','b')", id="tuple"),
            pytest.param("ab", "arrays.name='ab'", id="a string is one value"),
            pytest.param([""], "arrays.name=''", id="empty value"),
        ],
    )
    def test_term(self, values, expected):
        assert filter_in("arrays.name", values) == expected

    def test_field_is_used_as_given(self):
        assert filter_in("on.name", ["a", "b"]) == "on.name=('a','b')"


UNMATCHED = "The parentheses or the quotes of the filter term do not match: "
# Terms that, joined to others with and, would not stay one term
UNMATCHED_TERMS = [
    pytest.param("a='x') or (b='y'", id="closed before it is opened"),
    pytest.param("a='x')) or ((b='y'", id="closed twice before it is opened"),
    pytest.param("(a='x' or b='y'", id="left open"),
    pytest.param("a='x' or b='y')", id="closed once too often"),
    pytest.param("a='x' or b='y", id="string left open"),
    pytest.param("a='x\\'", id="string that ends in an escaped quote"),
]


class TestFilterRaw:
    """filter_raw makes one term of a filter the task wrote."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            pytest.param(None, None, id="None"),
            pytest.param("", None, id="empty"),
            pytest.param("  \n", None, id="blank"),
            pytest.param("a='x'", "(a='x')", id="one comparison"),
            pytest.param(" a='x' or b='y' ", "(a='x' or b='y')", id="stripped"),
            pytest.param("(a='x')", "((a='x'))", id="always in parentheses"),
        ],
    )
    def test_term(self, text, expected):
        assert filter_raw(text) == expected

    @pytest.mark.parametrize("text", UNMATCHED_TERMS)
    def test_filter_that_would_not_stay_one_term_is_refused(self, text):
        """Parentheses around it would not hold it together."""
        with pytest.raises(ValueError) as refused:
            filter_raw(text)

        assert str(refused.value) == UNMATCHED + text

    def test_refused_filter_is_reported_as_the_task_wrote_it(self):
        with pytest.raises(ValueError) as refused:
            filter_raw("  a='x') or (b='y' ")

        assert str(refused.value) == UNMATCHED + "a='x') or (b='y'"


class TestFilterAnd:
    """filter_and joins the terms that every item must match."""

    @pytest.mark.parametrize(
        "terms, expected",
        [
            pytest.param((), None, id="no term"),
            pytest.param((None, "", "  "), None, id="only empty terms"),
            pytest.param(("a='x'",), "a='x'", id="one term"),
            pytest.param(("a='x'", "b>=5"), "a='x' and b>=5", id="two terms"),
            pytest.param(
                (None, "a='x'", "", "b>=5", None, "c<7"),
                "a='x' and b>=5 and c<7",
                id="empty terms are left out",
            ),
            pytest.param((" a='x' ", "b>=5\n"), "a='x' and b>=5", id="stripped"),
            pytest.param(
                ("a='x' or b='y'", "c='z'"),
                "(a='x' or b='y') and c='z'",
                id="or at the top level",
            ),
            pytest.param(
                ("c='z'", "a='x' or b='y'"),
                "c='z' and (a='x' or b='y')",
                id="or in the last term",
            ),
            pytest.param(("a='x' or b='y'",), "(a='x' or b='y')", id="or alone"),
            pytest.param(
                ("a='x'or b='y'", "c='z'"),
                "(a='x'or b='y') and c='z'",
                id="or straight after a string",
            ),
            pytest.param(
                ("(a='x') or (b='y')", "c='z'"),
                "((a='x') or (b='y')) and c='z'",
                id="or between parentheses",
            ),
            pytest.param(
                ("contains(a,'x')or contains(b,'y')", "c='z'"),
                "(contains(a,'x')or contains(b,'y')) and c='z'",
                id="or straight after a parenthesis",
            ),
            pytest.param(
                ("(a='x' or b='y')", "c='z'"),
                "(a='x' or b='y') and c='z'",
                id="or in parentheses",
            ),
            pytest.param(
                ("a='this or that'", "c='z'"),
                "a='this or that' and c='z'",
                id="or in a string",
            ),
            pytest.param(
                ("a='it\\'s or not'", "c='z'"),
                "a='it\\'s or not' and c='z'",
                id="or in a string after an escaped quote",
            ),
            pytest.param(
                ("a='x\\\\' or b='y'", "c='z'"),
                "(a='x\\\\' or b='y') and c='z'",
                id="or after a string that ends in a backslash",
            ),
            pytest.param(
                ("a='(' or b=')'", "c='z'"),
                "(a='(' or b=')') and c='z'",
                id="parentheses in strings",
            ),
            pytest.param(
                ("contains(model,'or')", "tags('or','or')"),
                "contains(model,'or') and tags('or','or')",
                id="or in a function",
            ),
            pytest.param(
                ("color='red'", "orbit>1", "floor<3"),
                "color='red' and orbit>1 and floor<3",
                id="or in a word",
            ),
            pytest.param(
                ("a='x' and b='y'", "c='z'"),
                "a='x' and b='y' and c='z'",
                id="and needs no parentheses",
            ),
        ],
    )
    def test_filter(self, terms, expected):
        assert filter_and(*terms) == expected

    @pytest.mark.parametrize("term", UNMATCHED_TERMS)
    def test_term_that_would_not_stay_one_term_is_refused(self, term):
        """It would take the terms beside it apart."""
        with pytest.raises(ValueError) as refused:
            filter_and("c='z'", term, "d>=5")

        assert str(refused.value) == UNMATCHED + term

    def test_values_of_filter_in_are_terms_whatever_they_hold(self):
        """A quoted value never leaves a string or a parenthesis open."""
        names = filter_in("name", ["a)", "(b", "o'brien", "c\\", " or "])

        assert filter_and(names, "d>=5") == (
            "name=('a)','(b','o\\'brien','c\\\\',' or ') and d>=5"
        )

    def test_terms_of_the_helpers_and_of_the_task(self):
        """What a module joins: arrays, a time bound and a filter of the task."""
        result = filter_and(
            filter_in("arrays.name", ["a", "b"]),
            "time>=1612357445000",
            filter_raw("command='purevol' or command='purehost'"),
            filter_in("user", None),
            filter_raw(None),
        )

        assert result == (
            "arrays.name=('a','b') and time>=1612357445000 "
            "and (command='purevol' or command='purehost')"
        )


class TestPure1Time:
    """pure1_time gives the milliseconds of Pure1 as a time in UTC."""

    @pytest.mark.usefixtures("localtime_ahead_of_utc")
    @pytest.mark.parametrize(
        "milliseconds, expected",
        [
            pytest.param(1612357445000, "2021-02-03 13:04:05 UTC", id="time"),
            pytest.param(1612357445999, "2021-02-03 13:04:05 UTC", id="milliseconds"),
            pytest.param(0, "1970-01-01 00:00:00 UTC", id="0 is a time"),
            pytest.param(None, None, id="None"),
            pytest.param(1612357445000.5, "2021-02-03 13:04:05 UTC", id="float"),
        ],
    )
    def test_time(self, milliseconds, expected):
        assert pure1_time(milliseconds) == expected

    @pytest.mark.usefixtures("host_west_of_utc")
    def test_time_zone_of_the_host_does_not_count(self):
        # the host is five hours behind UTC
        assert time.localtime(1612357445)[3] == 8
        assert pure1_time(1612357445000) == "2021-02-03 13:04:05 UTC"

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(True, id="True"),
            pytest.param(False, id="False"),
            pytest.param("1612357445000", id="number as text"),
            pytest.param("soon", id="text"),
            pytest.param(b"1612357445000", id="bytes"),
            pytest.param([1612357445000], id="list"),
            pytest.param({"time": 1612357445000}, id="dictionary"),
            pytest.param(float("nan"), id="not a number"),
            pytest.param(float("inf"), id="infinity"),
            pytest.param(10**20, id="year beyond the platform"),
            pytest.param(-(10**20), id="year before the platform"),
            pytest.param(10**400, id="beyond a float"),
        ],
    )
    def test_value_that_cannot_be_a_time_is_none(self, value):
        """A result has null for it: no value of Pure1 fails the task here."""
        assert pure1_time(value) is None

    def test_time_that_the_platform_refuses_is_none(self, monkeypatch):
        """Some platforms convert no time before 1970."""

        def refused(seconds):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(
            pure1_utils,
            "time",
            types.SimpleNamespace(gmtime=refused, strftime=time.strftime),
        )

        assert pure1_time(-1000) is None


class TestPure1Attr:
    """pure1_attr reads a field of an SDK model that may not be set."""

    def test_field_of_a_reference(self):
        pod = sdk_model("FixedReference", name="pod1", id="p-1")
        volume = sdk_model("Volume", name="pod1::v1", pod=pod)

        assert pure1_attr(volume, "pod.name") == "pod1"
        assert pure1_attr(volume, "name") == "pod1::v1"
        assert pure1_attr(volume, "pod") is volume.pod

    @pytest.mark.parametrize("default", [None, "-"])
    def test_default_for_what_is_not_set(self, default):
        """A field Pure1 did not send, and a reference with no field set."""
        volume = sdk_model("Volume", name="v1", source=sdk_model("FixedReference"))
        options = {} if default is None else {"default": default}

        assert pure1_attr(volume, "pod.name", **options) == default
        assert pure1_attr(volume, "pod", **options) == default
        assert pure1_attr(volume, "serial", **options) == default
        assert pure1_attr(volume, "source.name", **options) == default
        assert pure1_attr(volume, "source.name.first", **options) == default
        assert pure1_attr(volume, "no_such_field.name", **options) == default

    def test_default_for_none(self):
        """An entry of a list that Pure1 sends as null."""
        pod = sdk_model("Pod", name="pod1", arrays=[])
        pod.arrays.append(None)

        assert pure1_attr(pod.arrays[0], "name") is None
        assert pure1_attr(pod.arrays[0], "name", "-") == "-"
        assert pure1_attr(None, "fleet.name", default="none") == "none"

    def test_default_is_the_third_argument(self):
        assert pure1_attr(sdk_model("Volume", name="v1"), "pod.name", "-") == "-"

    def test_value_that_is_set_is_not_replaced(self):
        hardware = sdk_model(
            "Hardware", name="", slot=0, identify_enabled=False, arrays=[]
        )

        assert pure1_attr(hardware, "slot", -1) == 0
        assert pure1_attr(hardware, "name", "-") == ""
        assert pure1_attr(hardware, "identify_enabled", True) is False
        assert pure1_attr(hardware, "arrays", "-") == []

    def test_field_of_a_list_is_not_set(self):
        """A path does not go through the entries of a list."""
        pod = sdk_model("Pod", name="pod1", arrays=[sdk_model("FixedReferenceFqdn")])

        assert pure1_attr(pod, "arrays.name", "-") == "-"

    def test_method_is_no_field(self):
        """A path that names a method of a value, as count of a list."""
        pod = sdk_model("Pod", name="pod1", arrays=[])

        assert pure1_attr(pod, "arrays.count", "-") == "-"
        assert pure1_attr(pod, "name.upper", "-") == "-"
        assert pure1_attr(pod, "to_dict") is None

    def test_empty_reference_of_an_array(self):
        """An array in no fleet has a fleet reference with no field set."""
        array = _array("array1", fleet=sdk_model("FixedReference"))

        assert pure1_attr(array, "fleet.name") is None
        assert pure1_attr(array, "topology_group.name") is None


class TestPure1ResolveArrays:
    """pure1_resolve_arrays finds the arrays of the names a task gives."""

    @pytest.mark.parametrize("names", [None, []])
    def test_no_names_is_no_selection(self, pure1_client, names):
        module = ansible_module()

        assert pure1_resolve_arrays(module, pure1_client, names) is None
        pure1_client.get_arrays.assert_not_called()
        module.fail_json.assert_not_called()

    def test_one_name_is_looked_up_with_an_equality(self, pure1_client):
        """In the case of the task: Pure1 compares a name whatever its case."""
        pure1_client.get_arrays.return_value = valid_response(
            [
                _array(
                    "array1",
                    id="id-1",
                    fqdn="array1.example.com",
                    os="Purity//FA",
                    model="FA-X70R4",
                    version="6.5.0",
                    fleet=sdk_model("FixedReference", name="fleet1"),
                )
            ]
        )
        module = ansible_module()

        arrays = pure1_resolve_arrays(module, pure1_client, ["ARRAY1"])

        assert arrays == [
            {
                "id": "id-1",
                "name": "array1",
                "fqdn": "array1.example.com",
                "os": "Purity//FA",
                "model": "FA-X70R4",
                "version": "6.5.0",
            }
        ]
        pure1_client.get_arrays.assert_called_once_with(filter="name='ARRAY1'")
        module.fail_json.assert_not_called()

    def test_name_comes_back_as_pure1_spells_it(self, pure1_client):
        """Also where Pure1 writes capitals and the task does not."""
        pure1_client.get_arrays.return_value = valid_response(
            [_array("Array1", id="1")]
        )

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["array1"])

        assert [array["name"] for array in arrays] == ["Array1"]

    @pytest.mark.parametrize(
        "names",
        [
            pytest.param([""], id="the only"),
            pytest.param(["a", ""], id="one of two"),
            pytest.param(["  "], id="blank"),
        ],
    )
    def test_empty_name_fails_the_task(self, pure1_client, names):
        """Pure1 answers it with no array, and the message would name nothing."""
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, names)

        module.fail_json.assert_called_once_with(
            msg="The name of an array must not be empty."
        )
        pure1_client.get_arrays.assert_not_called()

    def test_several_names_come_back_in_the_order_of_the_task(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [_array("a", id="1"), _array("b", id="2"), _array("c", id="3")]
        )

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["c", "A", "b"])

        assert [(array["name"], array["id"]) for array in arrays] == [
            ("c", "3"),
            ("a", "1"),
            ("b", "2"),
        ]
        pure1_client.get_arrays.assert_called_once_with(filter="name=('c','A','b')")

    def test_field_pure1_did_not_send_is_none(self, pure1_client):
        """A Cloud Block Store has no fqdn."""
        pure1_client.get_arrays.return_value = valid_response([_array("cbs1")])

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["cbs1"])

        assert arrays == [
            {
                "id": None,
                "name": "cbs1",
                "fqdn": None,
                "os": None,
                "model": None,
                "version": None,
            }
        ]

    def test_name_given_twice_is_one_array(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [_array("a", id="1"), _array("b", id="2")]
        )

        arrays = pure1_resolve_arrays(
            ansible_module(), pure1_client, ["a", "B", "A", "a", "b"]
        )

        assert [array["name"] for array in arrays] == ["a", "b"]
        pure1_client.get_arrays.assert_called_once_with(filter="name=('a','B','A','b')")

    def test_name_is_escaped(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response([_array("o'brien")])

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["o'brien"])

        assert [array["name"] for array in arrays] == ["o'brien"]
        pure1_client.get_arrays.assert_called_once_with(filter="name='o\\'brien'")

    def test_arrays_of_every_page_are_found(self, pure1_client):
        found = [_array("array{0}".format(number)) for number in range(5)]
        answers = paged_answers("ArrayGetResponse", found, page_size=2)
        pure1_client.get_arrays.side_effect = answers
        names = [array.name for array in found]

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, names)

        assert [array["name"] for array in arrays] == names
        assert len(answers.requests) == 3

    def test_array_of_a_page_with_more_arrays_than_its_total_is_found(
        self, pure1_client
    ):
        """An array that the page carries is known, whatever Pure1 counted."""
        pure1_client.get_arrays.return_value = valid_response(
            [_array("array1", id="1"), _array("array2", id="2")], total_item_count=1
        )
        module = ansible_module()

        arrays = pure1_resolve_arrays(module, pure1_client, ["array1", "array2"])

        assert [(array["name"], array["id"]) for array in arrays] == [
            ("array1", "1"),
            ("array2", "2"),
        ]
        module.fail_json.assert_not_called()

    @pytest.mark.parametrize(
        "names, expected",
        [
            pytest.param(["foo"], "Array foo is not known to Pure1.", id="the only"),
            pytest.param(
                ["a", "Foo"], "Array Foo is not known to Pure1.", id="one of two"
            ),
            pytest.param(
                ["foo", "a", "Bar"],
                "Arrays foo, Bar are not known to Pure1.",
                id="two of three",
            ),
            pytest.param(
                ["foo", "foo"], "Array foo is not known to Pure1.", id="given twice"
            ),
        ],
    )
    def test_unknown_name_fails_the_task(self, pure1_client, names, expected):
        """Pure1 ignores a name it does not know: the names are as the task wrote them."""
        pure1_client.get_arrays.return_value = valid_response([_array("a", id="1")])
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, names)

        module.fail_json.assert_called_once_with(msg=expected)

    def test_array_that_was_not_asked_for_is_left_out(self, pure1_client):
        """Also one that Pure1 sends without a name."""
        pure1_client.get_arrays.return_value = valid_response(
            [_array("a", id="1"), _array("other", id="2"), sdk_model("Array", id="3")]
        )

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["a"])

        assert [array["id"] for array in arrays] == ["1"]

    def test_spelling_of_the_task_wins_over_another_case(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [_array("Array1", id="1"), _array("array1", id="2")]
        )

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["array1"])

        assert [(array["name"], array["id"]) for array in arrays] == [("array1", "2")]

    def test_name_in_two_other_cases_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [_array("array1", id="2"), _array("Array1", id="1")]
        )
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, ["ARRAY1"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 knows more than one array whose name is ARRAY1 in another "
            "case: Array1, array1. Give the name as Pure1 spells it."
        )

    def test_two_arrays_of_one_name_are_both_returned(self, pure1_client):
        pure1_client.get_arrays.return_value = valid_response(
            [_array("a", id="1"), _array("a", id="2")]
        )

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["A", "a"])

        assert [(array["name"], array["id"]) for array in arrays] == [
            ("a", "1"),
            ("a", "2"),
        ]

    def test_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.return_value = error_response(403)
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, ["a"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed (HTTP 403): Forbidden"
        )

    def test_raised_error_fails_the_task(self, pure1_client):
        pure1_client.get_arrays.side_effect = PureError("no route to Pure1")
        module = ansible_module()

        with pytest.raises(SystemExit):
            pure1_resolve_arrays(module, pure1_client, ["a", "b"])

        module.fail_json.assert_called_once_with(
            msg="Pure1 request get_arrays failed: PureError: no route to Pure1"
        )


def _resolved(name, array_id):
    """Return an array as pure1_resolve_arrays returns it."""
    return dict(id=array_id, name=name, fqdn=None, os=None, model=None, version=None)


def _array_reference(**fields):
    """Return the reference a row has to its array."""
    return sdk_model("FixedReferenceFqdn", **fields)


class TestPure1IsOneOf:
    """pure1_is_one_of tells whether a row is of one of the arrays of the task."""

    ARRAYS = [_resolved("array1", "id-1"), _resolved("array2", "id-2")]

    @pytest.mark.parametrize(
        "reference, expected",
        [
            pytest.param(dict(id="id-1", name="array1"), True, id="the first"),
            pytest.param(dict(id="id-2", name="array2"), True, id="the second"),
            pytest.param(dict(id="id-2"), True, id="id alone"),
            pytest.param(dict(id="id-2", name="other"), True, id="id under a name"),
            pytest.param(dict(id="id-3", name="array1"), False, id="same name"),
            pytest.param(dict(id="id-3", name="ARRAY1"), False, id="other case"),
            pytest.param(dict(id="id-3", name="array3"), False, id="another array"),
            pytest.param(dict(name="array2"), True, id="name alone"),
            pytest.param(dict(name="ARRAY2"), False, id="name alone in other case"),
            pytest.param(dict(name="array3"), False, id="other name alone"),
            pytest.param(dict(fqdn="array1.example.com"), False, id="no id, no name"),
            pytest.param(dict(), False, id="no field set"),
        ],
    )
    def test_reference_is_told_by_its_id(self, reference, expected):
        """Pure1 compares names whatever their case: the id tells the arrays apart."""
        found = pure1_is_one_of(self.ARRAYS, _array_reference(**reference))

        assert found is expected

    def test_reference_that_pure1_left_out(self):
        """An entry of a list can be null, and a row can have no reference."""
        assert pure1_is_one_of(self.ARRAYS, None) is False

    @pytest.mark.parametrize(
        "reference, expected",
        [
            pytest.param(dict(id="id-9", name="array1"), True, id="name"),
            pytest.param(dict(name="array1"), True, id="name alone"),
            pytest.param(dict(id="id-9", name="Array1"), False, id="other case"),
            pytest.param(dict(id="id-9"), False, id="id alone"),
            pytest.param(dict(id="id-2", name="array1"), True, id="id of the other"),
        ],
    )
    def test_array_without_an_id_is_told_by_its_name(self, reference, expected):
        """As Pure1 spells it: the name of the record is the spelling of Pure1."""
        arrays = [_resolved("array1", None), _resolved("array2", "id-2")]

        assert pure1_is_one_of(arrays, _array_reference(**reference)) is expected

    def test_no_arrays_have_no_row(self):
        assert pure1_is_one_of([], _array_reference(id="id-1", name="array1")) is False

    def test_arrays_are_those_pure1_resolve_arrays_returned(self, pure1_client):
        """Of two arrays whose names differ in case only the one named is kept."""
        named = _array("array1", id="id-1")
        other = _array("Array1", id="id-2")
        pure1_client.get_arrays.return_value = valid_response([other, named])

        arrays = pure1_resolve_arrays(ansible_module(), pure1_client, ["array1"])

        assert pure1_is_one_of(arrays, _array_reference(id="id-1", name="array1"))
        assert not pure1_is_one_of(arrays, _array_reference(id="id-2", name="Array1"))


class TestPure1KeepsName:
    """pure1_keeps_name tells the array that has its name in a result keyed by name."""

    def test_array_seen_first_keeps_the_name(self):
        owners = {}

        assert pure1_keeps_name(owners, "array1", "id-1") is True
        assert pure1_keeps_name(owners, "array1", "id-2") is False
        assert pure1_keeps_name(owners, "array1", "id-1") is True
        assert owners == {"array1": "id-1"}

    def test_each_name_has_its_own_array(self):
        owners = {}

        assert pure1_keeps_name(owners, "array1", "id-1") is True
        assert pure1_keeps_name(owners, "array2", "id-2") is True
        assert pure1_keeps_name(owners, "Array1", "id-3") is True
        assert pure1_keeps_name(owners, "array2", "id-1") is False
        assert owners == {"array1": "id-1", "array2": "id-2", "Array1": "id-3"}

    def test_row_without_an_id_is_of_the_array_that_has_the_name(self):
        owners = {}

        assert pure1_keeps_name(owners, "array1", "id-1") is True
        assert pure1_keeps_name(owners, "array1", None) is True
        assert owners == {"array1": "id-1"}

    def test_first_array_with_an_id_takes_a_name_seen_without_one(self):
        owners = {}

        assert pure1_keeps_name(owners, "array1", None) is True
        assert owners == {"array1": None}
        assert pure1_keeps_name(owners, "array1", "id-2") is True
        assert pure1_keeps_name(owners, "array1", "id-1") is False
        assert pure1_keeps_name(owners, "array1", None) is True
        assert owners == {"array1": "id-2"}

    def test_rows_of_two_arrays_of_one_name_are_not_merged(self):
        """As a module uses it: the rows of the second array are counted, not filed."""
        rows = [("array1", "id-1"), ("array1", "id-2"), ("array2", "id-3")]
        rows += [("array1", "id-1"), ("array1", "id-2")]
        owners = {}
        kept = []
        left_out = 0
        for name, array_id in rows:
            if pure1_keeps_name(owners, name, array_id):
                kept.append((name, array_id))
            else:
                left_out += 1

        assert kept == [("array1", "id-1"), ("array2", "id-3"), ("array1", "id-1")]
        assert left_out == 2


class TestPure1Require:
    """pure1_require fails the task on a py-pure-client without the method."""

    MISSING = "get_things_of_a_later_release"

    def _message(self, pure1_client):
        module = ansible_module()
        with pytest.raises(SystemExit):
            pure1_require(module, pure1_client, self.MISSING, "1.101.0")
        module.fail_json.assert_called_once()
        return module.fail_json.call_args[1]["msg"]

    def test_method_of_the_client_is_enough(self, pure1_client):
        module = ansible_module()

        assert pure1_require(module, pure1_client, "get_fleets", "1.99.0") is None
        module.fail_json.assert_not_called()
        # the method is looked at, not called
        pure1_client.get_fleets.assert_not_called()

    def test_missing_method_names_what_is_needed_and_what_is_installed(
        self, pure1_client, monkeypatch
    ):
        monkeypatch.setattr(pypureclient, "__version__", "1.98.0")

        assert self._message(pure1_client) == (
            "py-pure-client 1.101.0 or later is required: this module needs "
            "Client.get_things_of_a_later_release(), which the installed "
            "py-pure-client 1.98.0 does not have."
        )

    def test_installed_release_is_the_one_of_the_sdk(self, pure1_client):
        release = importlib.metadata.version("py-pure-client")

        assert "installed py-pure-client {0} does".format(release) in self._message(
            pure1_client
        )

    def test_release_comes_from_the_package_data_of_an_sdk_without_version(
        self, pure1_client, monkeypatch
    ):
        monkeypatch.delattr(pypureclient, "__version__")
        monkeypatch.setattr(
            importlib.metadata, "version", {"py-pure-client": "1.97.0"}.__getitem__
        )

        assert self._message(pure1_client) == (
            "py-pure-client 1.101.0 or later is required: this module needs "
            "Client.get_things_of_a_later_release(), which the installed "
            "py-pure-client 1.97.0 does not have."
        )

    def test_release_that_cannot_be_read_is_left_out(self, pure1_client, monkeypatch):
        def not_found(name):
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.delattr(pypureclient, "__version__")
        monkeypatch.setattr(importlib.metadata, "version", not_found)

        assert self._message(pure1_client) == (
            "py-pure-client 1.101.0 or later is required: this module needs "
            "Client.get_things_of_a_later_release(), which the installed "
            "py-pure-client does not have."
        )

    @pytest.mark.parametrize(
        "error", [RuntimeError("broken package data"), ValueError("no version")]
    )
    def test_no_error_while_the_release_is_read_is_raised(
        self, pure1_client, monkeypatch, error
    ):
        """Whatever the package data raises: the message is what counts."""

        def broken(name):
            raise error

        monkeypatch.delattr(pypureclient, "__version__")
        monkeypatch.setattr(importlib.metadata, "version", broken)

        assert self._message(pure1_client) == (
            "py-pure-client 1.101.0 or later is required: this module needs "
            "Client.get_things_of_a_later_release(), which the installed "
            "py-pure-client does not have."
        )

    def test_sdk_that_cannot_be_imported_does_not_raise(self, pure1_client, no_sdk):
        assert self._message(pure1_client).endswith(
            "which the installed py-pure-client does not have."
        )

    def test_attribute_that_is_no_method_is_not_enough(self, pure1_client):
        setattr(pure1_client, self.MISSING, "1.101.0")

        assert "Client.get_things_of_a_later_release()" in self._message(pure1_client)
