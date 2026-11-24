# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for pure1 module utilities."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
import os
import ssl
import stat
import types
import pytest
from unittest.mock import patch

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
    Pure1RequestError,
    filter_string,
    get_pure1,
    pure1_argument_spec,
    pure1_error_text,
    pure1_items,
    pure1_response,
)
from tests.unit.conftest import (
    CREDENTIALS,
    NEWEST,
    SDK,
    ansible_module,
    error_response,
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
            "switch the verification off: the certificate of Pure1 is still "
            "verified. py-pure-client 1.71.0 and later can."
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
