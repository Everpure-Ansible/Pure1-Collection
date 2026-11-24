# Copyright: (c) 2026, Everpure Ansible Team <pure-ansible-team@everpuredata.com>
# GNU General Public License v3.0+ (see COPYING.GPLv3 or https://www.gnu.org/licenses/gpl-3.0.txt)

"""Unit tests for the pure1_pods module."""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import pytest

from plugins.modules import pure1_pods
from plugins.modules.pure1_pods import generate_pods_dict
from tests.unit.conftest import (
    ansible_module,
    array_reference,
    error_response,
    run_main,
    sdk_model,
    valid_response,
)


def _make_pod(array_name="array1", **overrides):
    fields = dict(
        name="pod1",
        mediator="purestorage",
        arrays=[array_reference(array_name)],
    )
    fields.update(overrides)
    return sdk_model("Pod", **fields)


def _make_stretched_pod(name="stretched", array_names=("array1", "array2")):
    return _make_pod(
        name=name, arrays=[array_reference(array) for array in array_names]
    )


POD1 = {"pod1": {"mediator": "purestorage", "source": []}}
STRETCHED = {"stretched": {"mediator": "purestorage", "source": []}}


class TestGeneratePodsDict:
    """Tests for generate_pods_dict."""

    def test_basic_mapping_no_source(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod()])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        pure1_client.get_pods.assert_called_once_with()
        assert result["array1"] == [{"pod1": {"mediator": "purestorage", "source": []}}]

    def test_source_is_unwrapped_to_name(self, pure1_client):
        pod = _make_pod(source=sdk_model("FixedReference", name="sourcepod"))
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["pod1"]["source"] == "sourcepod"

    def test_array_filter_applied(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod("myarray")])

        generate_pods_dict(ansible_module(array="myarray"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='myarray'")

    def test_grouping_across_arrays(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_pod("array2")]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert set(result.keys()) == {"array1", "array2"}

    def test_missing_mediator_defaults_to_none(self, pure1_client):
        bare = sdk_model("Pod", name="pod1", arrays=[array_reference("array1")])
        pure1_client.get_pods.return_value = valid_response([bare])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result["array1"][0]["pod1"]["mediator"] is None

    def test_stretched_pod_listed_under_each_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_stretched_pod_array_filter_first_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        result = generate_pods_dict(ansible_module(array="array1"), pure1_client)

        assert result == {"array1": [STRETCHED]}

    def test_stretched_pod_array_filter_second_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_stretched_pod(), _make_pod("array2")]
        )

        result = generate_pods_dict(ansible_module(array="array2"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='array2'")
        assert result == {"array2": [STRETCHED, POD1]}

    def test_array_filter_match_ignores_case(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        result = generate_pods_dict(ansible_module(array="ARRAY2"), pure1_client)

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='ARRAY2'")
        assert result == {"array2": [STRETCHED]}

    def test_array_filter_unmatched_name_keeps_pod(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        result = generate_pods_dict(ansible_module(array="other"), pure1_client)

        assert result == {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}

    def test_array_reference_without_name_is_skipped(self, pure1_client):
        pod = _make_pod(
            arrays=[array_reference("array1"), sdk_model("FixedReferenceFqdn")]
        )
        # The SDK reads a response without validating it, so a null entry of
        # the list arrives as None; the constructor of the model refuses one
        pod.arrays.append(None)
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}

    def test_repeated_array_name_lists_pod_once(self, pure1_client):
        pod = _make_pod(arrays=[array_reference("array1"), array_reference("array1")])
        pure1_client.get_pods.return_value = valid_response([pod])

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}

    def test_pod_without_arrays_is_skipped(self, pure1_client):
        no_arrays = sdk_model("Pod", name="orphan", mediator="purestorage")
        pure1_client.get_pods.return_value = valid_response(
            [no_arrays, _make_pod(name="empty", arrays=[]), _make_pod()]
        )

        result = generate_pods_dict(ansible_module(array=None), pure1_client)

        assert result == {"array1": [POD1]}


class TestPure1PodsMain:
    """Tests for the pure1_pods main() flow."""

    def test_pods_are_returned_by_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response(
            [_make_pod("array1"), _make_stretched_pod()]
        )

        # array is left to the argument spec
        module = run_main(pure1_pods)

        pure1_client.get_pods.assert_called_once_with()
        module.exit_json.assert_called_once_with(
            changed=False,
            pure1_pods={"pods": {"array1": [POD1, STRETCHED], "array2": [STRETCHED]}},
        )

    def test_array_option_selects_the_pods_of_one_array(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_stretched_pod()])

        module = run_main(pure1_pods, array="array2")

        pure1_client.get_pods.assert_called_once_with(filter="arrays.name='array2'")
        module.exit_json.assert_called_once_with(
            changed=False, pure1_pods={"pods": {"array2": [STRETCHED]}}
        )

    def test_pods_are_collected_in_check_mode(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([_make_pod()])

        module = run_main(pure1_pods, check_mode=True)

        module.exit_json.assert_called_once_with(
            changed=False, pure1_pods={"pods": {"array1": [POD1]}}
        )

    def test_no_pods_gives_an_empty_result(self, pure1_client):
        pure1_client.get_pods.return_value = valid_response([])

        module = run_main(pure1_pods, array="array1")

        module.fail_json.assert_not_called()
        module.exit_json.assert_called_once_with(changed=False, pure1_pods={"pods": {}})

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason="D011 pure1_pods reads .items of an error answer, which has none",
    )
    def test_pure1_error_answer_fails_the_task(self, pure1_client):
        pure1_client.get_pods.return_value = error_response(403, "Access denied")

        module = run_main(pure1_pods)

        module.exit_json.assert_not_called()
        module.fail_json.assert_called_once()
        message = module.fail_json.call_args[1]["msg"]
        assert "403" in message
        assert "Access denied" in message
