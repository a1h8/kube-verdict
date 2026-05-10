"""
Unit tests for KubeVersion — detect_version and changelog_notes.
"""
from unittest.mock import MagicMock, patch

import pytest

from ontology.version import KubeVersion, detect_version, _parse_int


class TestDetectVersion:
    def _make_client(self):
        return MagicMock()

    def test_parses_version_from_api(self):
        info = MagicMock()
        info.major = "1"
        info.minor = "28"
        info.git_version = "v1.28.3"
        info.platform = "linux/amd64"

        mock_api = MagicMock()
        mock_api.get_code.return_value = info

        with patch("ontology.version.k8s_client.VersionApi", return_value=mock_api):
            v = detect_version(self._make_client())

        assert v.major == 1
        assert v.minor == 28
        assert v.git_version == "v1.28.3"
        assert v.platform == "linux/amd64"

    def test_k3s_suffix_parsed(self):
        info = MagicMock()
        info.major = "1"
        info.minor = "28+"   # K3s adds +
        info.git_version = "v1.28.3+k3s1"
        info.platform = ""

        mock_api = MagicMock()
        mock_api.get_code.return_value = info

        with patch("ontology.version.k8s_client.VersionApi", return_value=mock_api):
            v = detect_version(self._make_client())

        assert v.minor == 28

    def test_fallback_on_api_exception(self):
        from kubernetes.client.exceptions import ApiException
        mock_api = MagicMock()
        mock_api.get_code.side_effect = ApiException(status=403)

        with patch("ontology.version.k8s_client.VersionApi", return_value=mock_api):
            v = detect_version(self._make_client())

        assert v.major == 1
        assert v.minor == 19

    def test_fallback_on_generic_exception(self):
        mock_api = MagicMock()
        mock_api.get_code.side_effect = RuntimeError("network error")

        with patch("ontology.version.k8s_client.VersionApi", return_value=mock_api):
            v = detect_version(self._make_client())

        assert v.major == 1
        assert v.minor == 19


class TestChangelogNotes:
    def test_returns_notes_for_version(self):
        v = KubeVersion(1, 28, "v1.28.0")
        notes = v.changelog_notes()
        assert isinstance(notes, list)
        assert len(notes) > 0

    def test_older_version_fewer_notes(self):
        old = KubeVersion(1, 16, "v1.16.0")
        new = KubeVersion(1, 28, "v1.28.0")
        assert len(old.changelog_notes()) <= len(new.changelog_notes())

    def test_notes_contain_version_prefix(self):
        v = KubeVersion(1, 28, "v1.28.0")
        for note in v.changelog_notes():
            assert ">=" in note


class TestParseInt:
    def test_plain_int(self):
        assert _parse_int("28") == 28

    def test_with_plus_suffix(self):
        assert _parse_int("28+") == 28

    def test_none_returns_zero(self):
        assert _parse_int(None) == 0

    def test_empty_returns_zero(self):
        assert _parse_int("") == 0

    def test_non_numeric_returns_zero(self):
        assert _parse_int("abc") == 0
