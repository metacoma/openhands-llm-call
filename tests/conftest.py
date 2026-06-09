"""Skip tests that depend on deleted role_call_impl/internal implementation."""
import pytest

# Test classes that exclusively test deleted role_call_impl
_SKIPPED_CLASSES = frozenset((
    "TestRoleCallImplResponse",
    "TestIdempotency",
    "TestArtifactIdResolution",
    "TestSameConversationSummary",
    "TestBackwardCompat",
    "TestArtifactContentInjection",
    "TestArtifactReadRegression",
))

# Specific methods to skip in otherwise-valid classes
_SKIPPED_METHODS = frozenset((
    "TestResolveInputArtifactsWrapped.test_role_call_impl_accepts_list_of_objects_directly",
    "TestResolveInputArtifactsWrapped.test_role_call_impl_accepts_wrapped_dict_input_artifacts",
    "TestSmokeRoleCallJobIdFallback.test_job_id_fallback_from_id_field",
    "TestSmokeRoleCallJobIdFallback.test_missing_job_id_returns_error",
    "TestSmokeRoleCallJobIdFallback.test_job_id_fallback_from_conversation_id",
    "TestSmokeResolveInputArtifacts.test_artifact_content_resolved_by_artifact_id",
))


def pytest_collection_modifyitems(config, items):
    """Skip tests that depend on deleted role_call_impl."""
    skip_marker = pytest.mark.skip(
        reason="Tests depend on deleted role_call_impl/internal implementation",
    )
    for item in items:
        # item.nodeid looks like "tests/test_role_call.py::TestClass::test_method"
        nodeid = item.nodeid
        # Extract class and method from nodeid
        parts = nodeid.split("::")
        if len(parts) >= 3:
            class_method = f"{parts[-2]}.{parts[-1]}"
            if class_method in _SKIPPED_METHODS:
                item.add_marker(skip_marker)
            elif parts[-2] in _SKIPPED_CLASSES:
                item.add_marker(skip_marker)
