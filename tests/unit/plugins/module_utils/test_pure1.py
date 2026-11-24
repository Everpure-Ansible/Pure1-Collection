# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for pure1 module utilities."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
import pytest
from unittest.mock import patch

from pypureclient import ErrorResponse, PureError, ValidResponse

from plugins.module_utils import pure1 as pure1_utils
from plugins.module_utils.pure1 import get_pure1, pure1_argument_spec
from tests.unit.conftest import ansible_module, error_response

# Shaped like the JSON web tokens Pure1 uses as ID token and bearer token
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJwdXJlMSJ9.c2lnbmF0dXJl"


@pytest.fixture(autouse=True)
def _no_pure1_environment(monkeypatch):
    """Keep exported credentials and key passwords out of the tests."""
    for name in (
        "PURE1_APP_ID",
        "PURE1_PRIVATE_KEY_FILE",
        "PURE1_PRIVATE_PASSWORD",
        "PURE1_PRIVATE_KEY_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def _failure_message(module):
    """Run get_pure1 and return the message it failed with."""
    with pytest.raises(SystemExit):
        get_pure1(module)
    module.fail_json.assert_called_once()
    return module.fail_json.call_args[1]["msg"]


class TestPure1ArgumentSpec:
    """Tests for pure1_argument_spec function."""

    def test_returns_dict(self):
        """Test that the function returns a dictionary."""
        assert isinstance(pure1_argument_spec(), dict)

    def test_all_expected_keys(self):
        """Test that spec contains exactly the expected keys."""
        result = pure1_argument_spec()
        assert set(result.keys()) == {"app_id", "key_file", "password"}

    def test_app_id_required_and_no_log(self):
        """app_id must be required and marked no_log."""
        result = pure1_argument_spec()
        assert result["app_id"]["required"] is True
        assert result["app_id"]["no_log"] is True

    def test_key_file_required_and_not_no_log(self):
        """key_file is required but is a path, not a secret."""
        result = pure1_argument_spec()
        assert result["key_file"]["required"] is True
        assert result["key_file"]["no_log"] is False

    def test_password_is_secret(self):
        """password must be marked no_log."""
        result = pure1_argument_spec()
        assert result["password"]["no_log"] is True


class TestGetPure1:
    """Tests for get_pure1 function."""

    def test_with_module_params(self, client_factory, pure1_client):
        """Test get_pure1 with app_id/key_file module parameters."""
        module = ansible_module(
            app_id="pure1-app-id", key_file="/run/secrets/pure1.pem", password=None
        )

        result = get_pure1(module)

        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["app_id"] == "pure1-app-id"
        assert call_kwargs["private_key_file"] == "/run/secrets/pure1.pem"
        assert "private_key_password" not in call_kwargs
        header, value = pure1_client._api_client.set_default_header.call_args[0]
        assert header == "User-Agent"
        assert value.startswith("Ansible ")
        pure1_client.get_arrays.assert_called_once_with()
        assert result == pure1_client

    def test_with_password(self, client_factory, pure1_client):
        """A supplied password is forwarded as private_key_password."""
        module = ansible_module(password="s3cret")

        result = get_pure1(module)

        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["private_key_password"] == "s3cret"
        assert result == pure1_client

    def test_with_environment_vars(self, client_factory, pure1_client, monkeypatch):
        """Test get_pure1 falls back to environment variables."""
        module = ansible_module(app_id=None, key_file=None, password="envpass")
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "envpass")

        result = get_pure1(module)

        call_kwargs = client_factory.call_args[1]
        assert call_kwargs["app_id"] == "env-app-id"
        assert call_kwargs["private_key_file"] == "/env/pure1.pem"
        assert call_kwargs["private_key_password"] == "envpass"
        assert result == pure1_client

    def test_missing_credentials(self):
        """Test that missing credentials causes a clean failure."""
        module = ansible_module(app_id=None, key_file=None, password=None)

        with pytest.raises(SystemExit):
            get_pure1(module)

        module.fail_json.assert_called_once()
        msg = module.fail_json.call_args[1]["msg"]
        assert "PURE1_APP_ID" in msg
        assert "PURE1_PRIVATE_KEY_FILE" in msg

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
        self, client_factory, monkeypatch
    ):
        """The environment path reports the cause and hides its passwords."""
        module = ansible_module(app_id=None, key_file=None, password="optpass")
        monkeypatch.setenv("PURE1_APP_ID", "env-app-id")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_FILE", "/env/pure1.pem")
        monkeypatch.setenv("PURE1_PRIVATE_PASSWORD", "envpass")
        monkeypatch.setenv("PURE1_PRIVATE_KEY_PASSWORD", "sdkpass")
        client_factory.side_effect = OSError("bad optpass, envpass or sdkpass")

        msg = _failure_message(module)

        assert msg == (
            "Failed to create the Pure1 client: "
            "OSError: bad ********, ******** or ********"
        )

    @pytest.mark.parametrize(
        "response, expected",
        [
            (error_response(403), "(HTTP 403): Forbidden"),
            (error_response(403, message=" \n"), "(HTTP 403): Forbidden"),
            (error_response(404, context="No such"), "(HTTP 404): No such"),
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
