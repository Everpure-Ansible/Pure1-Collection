# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for pure1 module utilities."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import importlib.util
import sys
import types
import pytest
from unittest.mock import Mock, patch, MagicMock

# Mock external dependencies before importing pure1
sys.modules["pypureclient"] = MagicMock()
sys.modules["pypureclient.pure1"] = MagicMock()

from plugins.module_utils.pure1 import get_pure1, pure1_argument_spec

# Shaped like the JSON web tokens Pure1 uses as ID token and bearer token
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJpc3MiOiJwdXJlMSJ9.c2lnbmF0dXJl"


class PureError(Exception):
    """Stand-in for the py-pure-client exception, which names itself in its text."""

    def __init__(self, reason=None):
        self.reason = reason
        super(PureError, self).__init__()

    def __str__(self):
        return "PureError: {0}".format(self.reason)


@pytest.fixture(autouse=True)
def _no_password_environment(monkeypatch):
    """Keep exported key passwords out of the expected messages."""
    monkeypatch.delenv("PURE1_PRIVATE_PASSWORD", raising=False)
    monkeypatch.delenv("PURE1_PRIVATE_KEY_PASSWORD", raising=False)


def _module(password=None):
    """Return a module with credentials whose fail_json ends the call."""
    mock_module = Mock()
    mock_module.params = {
        "app_id": "pure1-app-id",
        "key_file": "/run/secrets/pure1.pem",
        "password": password,
    }
    mock_module.fail_json.side_effect = SystemExit("fail_json called")
    return mock_module


def _failure_message(mock_module):
    """Run get_pure1 and return the message it failed with."""
    try:
        get_pure1(mock_module)
    except SystemExit:
        pass
    mock_module.fail_json.assert_called_once()
    return mock_module.fail_json.call_args[1]["msg"]


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

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_with_module_params(self, mock_client_class):
        """Test get_pure1 with app_id/key_file module parameters."""
        mock_module = Mock()
        mock_module.params = {
            "app_id": "pure1-app-id",
            "key_file": "/run/secrets/pure1.pem",
            "password": None,
        }
        mock_client = Mock()
        mock_client.get_arrays.return_value = Mock(status_code=200)
        mock_client_class.return_value = mock_client

        result = get_pure1(mock_module)

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["app_id"] == "pure1-app-id"
        assert call_kwargs["private_key_file"] == "/run/secrets/pure1.pem"
        assert "private_key_password" not in call_kwargs
        mock_client.get_arrays.assert_called_once_with()
        assert result == mock_client

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_with_password(self, mock_client_class):
        """A supplied password is forwarded as private_key_password."""
        mock_module = Mock()
        mock_module.params = {
            "app_id": "pure1-app-id",
            "key_file": "/run/secrets/pure1.pem",
            "password": "s3cret",
        }
        mock_client = Mock()
        mock_client.get_arrays.return_value = Mock(status_code=200)
        mock_client_class.return_value = mock_client

        result = get_pure1(mock_module)

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["private_key_password"] == "s3cret"
        assert result == mock_client

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    @patch("plugins.module_utils.pure1.environ")
    def test_with_environment_vars(self, mock_environ, mock_client_class):
        """Test get_pure1 falls back to environment variables."""
        mock_module = Mock()
        mock_module.params = {
            "app_id": None,
            "key_file": None,
            "password": "envpass",
        }
        env_vars = {
            "PURE1_APP_ID": "env-app-id",
            "PURE1_PRIVATE_KEY_FILE": "/env/pure1.pem",
            "PURE1_PRIVATE_PASSWORD": "envpass",
        }
        mock_environ.get.side_effect = env_vars.get
        mock_client = Mock()
        mock_client.get_arrays.return_value = Mock(status_code=200)
        mock_client_class.return_value = mock_client

        result = get_pure1(mock_module)

        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["app_id"] == "env-app-id"
        assert call_kwargs["private_key_file"] == "/env/pure1.pem"
        assert call_kwargs["private_key_password"] == "envpass"
        assert result == mock_client

    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    @patch("plugins.module_utils.pure1.environ")
    def test_missing_credentials(self, mock_environ):
        """Test that missing credentials causes a clean failure."""
        mock_module = Mock()
        mock_module.params = {"app_id": None, "key_file": None, "password": None}
        mock_module.fail_json.side_effect = SystemExit("fail_json called")
        mock_environ.get.return_value = None

        try:
            get_pure1(mock_module)
        except SystemExit:
            pass

        mock_module.fail_json.assert_called_once()
        msg = mock_module.fail_json.call_args[1]["msg"]
        assert "PURE1_APP_ID" in msg
        assert "PURE1_PRIVATE_KEY_FILE" in msg

    @patch("plugins.module_utils.pure1.missing_required_lib")
    @patch("plugins.module_utils.pure1.PYPURECLIENT_IMP_ERR", "import traceback")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", False)
    def test_missing_pypureclient(self, mock_missing_lib):
        """Test that a missing py-pure-client causes failure."""
        mock_module = Mock()
        mock_module.params = {
            "app_id": "pure1-app-id",
            "key_file": "/run/secrets/pure1.pem",
            "password": None,
        }
        mock_module.fail_json.side_effect = SystemExit("fail_json called")
        mock_missing_lib.return_value = "py-pure-client is missing"

        try:
            get_pure1(mock_module)
        except SystemExit:
            pass

        mock_missing_lib.assert_called_once_with("py-pure-client")
        mock_module.fail_json.assert_called_once_with(
            msg="py-pure-client is missing", exception="import traceback"
        )

    def test_import_failure_keeps_traceback(self):
        """A failed import of py-pure-client is recorded with its traceback."""
        spec = importlib.util.spec_from_file_location(
            "pure1_without_sdk", sys.modules[get_pure1.__module__].__file__
        )
        module_copy = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"pypureclient": None}):
            spec.loader.exec_module(module_copy)

        assert module_copy.HAS_PYPURECLIENT is False
        assert "pypureclient" in module_copy.PYPURECLIENT_IMP_ERR

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_failure_status(self, mock_client_class):
        """A non-200 from get_arrays must surface with its status and message."""
        mock_module = Mock()
        mock_module.params = {
            "app_id": "pure1-app-id",
            "key_file": "/run/secrets/pure1.pem",
            "password": None,
        }
        mock_module.fail_json.side_effect = SystemExit("fail_json called")
        mock_client = Mock()
        mock_client.get_arrays.return_value = Mock(
            status_code=403,
            errors=[types.SimpleNamespace(context=None, message="Access denied")],
        )
        mock_client_class.return_value = mock_client

        try:
            get_pure1(mock_module)
        except SystemExit:
            pass

        mock_module.fail_json.assert_called_once()
        assert (
            mock_module.fail_json.call_args[1]["msg"]
            == "Pure1 request get_arrays failed (HTTP 403): Access denied"
        )


class TestGetPure1ErrorReporting:
    """Tests for the failure messages of get_pure1."""

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_names_type_and_text(self, mock_client_class):
        """Any exception from the client constructor is reported with its type."""
        mock_client_class.side_effect = AttributeError(
            "module 'jwt' has no attribute 'encode'"
        )

        msg = _failure_message(_module())

        assert msg == (
            "Failed to create the Pure1 client: "
            "AttributeError: module 'jwt' has no attribute 'encode'"
        )
        assert "Unknown failure" not in msg

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_pure_error_is_named_once(self, mock_client_class):
        """The SDK error keeps its text and its name is not repeated."""
        mock_client_class.side_effect = PureError(
            "Could not load PEM private key /run/secrets/pure1.pem: "
            "key is encrypted but no private_key_password was provided"
        )

        msg = _failure_message(_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not load PEM private key /run/secrets/pure1.pem: "
            "key is encrypted but no private_key_password was provided"
        )

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_drops_response_headers_and_body(
        self, mock_client_class
    ):
        """A rejected token exchange is condensed to its status and reason."""
        mock_client_class.side_effect = PureError(
            "Could not retrieve a new access token: (401)\n"
            "Reason: Unauthorized\n"
            "HTTP response headers: HTTPHeaderDict({'X-Request-ID': 'abc'})\n"
            'HTTP response body: b\'{"error": "invalid_client"}\'\n'
        )

        msg = _failure_message(_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: "
            "Could not retrieve a new access token: (401) Reason: Unauthorized"
        )

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_drops_unparsed_token_response(self, mock_client_class):
        """A token response the SDK cannot parse is not repeated."""
        mock_client_class.side_effect = PureError(
            "Unable to parse response. "
            'b\'{"token": "0a1b2c3d4e5f60718293a4b5c6d7e8f9"}\''
        )

        msg = _failure_message(_module())

        assert msg == (
            "Failed to create the Pure1 client: PureError: Unable to parse response."
        )

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_hides_secrets(self, mock_client_class):
        """No token, key material or password reaches the message."""
        mock_client_class.side_effect = ValueError(
            "password s3cret, Authorization: Bearer %s\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n" % JWT
        )

        msg = _failure_message(_module(password="s3cret"))

        assert msg.startswith("Failed to create the Pure1 client: ValueError: ")
        for secret in ("s3cret", JWT, "eyJ", "BEGIN", "MIIEowIBAAKCAQEA"):
            assert secret not in msg

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_text_is_bounded(self, mock_client_class):
        """A long error text is cut."""
        mock_client_class.side_effect = RuntimeError("x" * 5000)

        msg = _failure_message(_module())

        assert msg.endswith("...")
        assert len(msg) < 600

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_construction_error_hides_secret_at_the_cut(self, mock_client_class):
        """A token that crosses the length limit is hidden whole."""
        mock_client_class.side_effect = RuntimeError("x" * 490 + " " + JWT)

        msg = _failure_message(_module())

        assert msg.endswith("x ********")
        assert "eyJ" not in msg

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    @patch("plugins.module_utils.pure1.environ")
    def test_construction_error_with_environment_credentials(
        self, mock_environ, mock_client_class
    ):
        """The environment path reports the cause and hides its passwords."""
        mock_module = _module(password="optpass")
        mock_module.params.update(app_id=None, key_file=None)
        env_vars = {
            "PURE1_APP_ID": "env-app-id",
            "PURE1_PRIVATE_KEY_FILE": "/env/pure1.pem",
            "PURE1_PRIVATE_PASSWORD": "envpass",
            "PURE1_PRIVATE_KEY_PASSWORD": "sdkpass",
        }
        mock_environ.get.side_effect = env_vars.get
        mock_client_class.side_effect = OSError("bad optpass, envpass or sdkpass")

        msg = _failure_message(mock_module)

        assert msg == (
            "Failed to create the Pure1 client: "
            "OSError: bad ********, ******** or ********"
        )

    @pytest.mark.parametrize(
        "response, expected",
        [
            (
                Mock(
                    status_code=403,
                    errors=[types.SimpleNamespace(context=None, message=None)],
                ),
                "(HTTP 403): Forbidden",
            ),
            (
                Mock(
                    status_code=403,
                    errors=[types.SimpleNamespace(context=None, message=" \n")],
                ),
                "(HTTP 403): Forbidden",
            ),
            (
                Mock(
                    status_code=404,
                    errors=[types.SimpleNamespace(context="No such", message=None)],
                ),
                "(HTTP 404): No such",
            ),
            (Mock(status_code=503, errors=[]), "(HTTP 503): Service Unavailable"),
            (
                types.SimpleNamespace(status_code=429),
                "(HTTP 429): Too Many Requests",
            ),
            (Mock(status_code=599, errors=None), "(HTTP 599): no message returned"),
        ],
    )
    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_error_without_message(
        self, mock_client_class, response, expected
    ):
        """An error answer with no usable message falls back to the status."""
        mock_client_class.return_value.get_arrays.return_value = response

        msg = _failure_message(_module())

        assert msg == "Pure1 request get_arrays failed " + expected

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_error_message_is_condensed(self, mock_client_class):
        """The API message is put on one line and a token in it is hidden."""
        mock_client_class.return_value.get_arrays.return_value = Mock(
            status_code=401,
            errors=[
                types.SimpleNamespace(
                    context=None, message="bad token\n%s\nretry" % JWT
                )
            ],
        )

        msg = _failure_message(_module())

        assert msg == (
            "Pure1 request get_arrays failed (HTTP 401): bad token ******** retry"
        )

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_exception_names_type_and_text(self, mock_client_class):
        """An exception from get_arrays is not an authentication failure."""
        mock_client_class.return_value.get_arrays.side_effect = TimeoutError(
            "The read operation timed out"
        )

        msg = _failure_message(_module())

        assert msg == (
            "Pure1 request get_arrays failed: "
            "TimeoutError: The read operation timed out"
        )

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_exception_without_text(self, mock_client_class):
        """An exception with no text is reported by its type alone."""
        mock_client_class.return_value.get_arrays.side_effect = TimeoutError()

        msg = _failure_message(_module())

        assert msg == "Pure1 request get_arrays failed: TimeoutError"

    @patch("plugins.module_utils.pure1.pure1.Client")
    @patch("plugins.module_utils.pure1.HAS_PYPURECLIENT", True)
    def test_preflight_pure_error_drops_response_body(self, mock_client_class):
        """An SDK error raised by get_arrays is condensed to status and reason."""
        mock_client_class.return_value.get_arrays.side_effect = PureError(
            "(401)\nReason: Unauthorized\nHTTP response body: b'%s'\n" % JWT
        )

        msg = _failure_message(_module())

        assert msg == (
            "Pure1 request get_arrays failed: PureError: (401) Reason: Unauthorized"
        )
