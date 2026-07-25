"""GUID conversion and collision-analysis tests."""

from __future__ import annotations

import pytest

from palmigrate import guid, scan

HOST = "00000000000000000000000000000001"
# Synthetic player ids. Real ones are derived from a player's account, so none
# appear in this repository -- but the shape is faithful: Palworld populates
# only the first uint32 field, leaving twelve zero bytes.
FRIEND_A = "a1b2c3d4000000000000000000000000"
FRIEND_B = "e5f60718000000000000000000000000"


class TestGuidConversion:
    def test_host_guid_byte_layout(self):
        """Twelve zero bytes then int32 1 -- the reason byte matching fails."""
        assert guid.to_bytes(HOST) == b"\x00" * 12 + b"\x01\x00\x00\x00"

    def test_friend_guid_byte_layout(self):
        """First field little-endian, remaining three zero."""
        assert guid.to_bytes(FRIEND_A) == bytes.fromhex("d4c3b2a1") + b"\x00" * 12

    @pytest.mark.parametrize("text", [HOST, FRIEND_A, FRIEND_B])
    def test_round_trip(self, text):
        assert guid.from_bytes(guid.to_bytes(text)) == text

    def test_normalise_strips_dashes_and_case(self):
        assert guid.normalise("A1B2C3D4-0000-0000-0000-000000000000") == FRIEND_A
        assert FRIEND_A.islower()  # normalise() always returns lowercase

    def test_rejects_bad_input(self):
        with pytest.raises(ValueError, match="32 hex characters"):
            guid.normalise("nope")

    def test_from_bytes_requires_16(self):
        with pytest.raises(ValueError, match="16 bytes"):
            guid.from_bytes(b"\x00" * 8)

    def test_is_coop_host(self):
        assert guid.is_coop_host(HOST)
        assert not guid.is_coop_host(FRIEND_A)

    def test_entropy_warning_flags_host(self):
        assert guid.entropy_warning(HOST) is not None

    def test_entropy_warning_flags_trailing_zero_guids(self):
        # Palworld player ids are one populated field plus three zero fields,
        # so they also carry a long zero run and must not be byte-matched.
        assert guid.entropy_warning(FRIEND_A) is not None

    def test_entropy_warning_silent_for_dense_guid(self):
        assert guid.entropy_warning("0123456789abcdef0123456789abcdef") is None


class TestOccurrenceScanning:
    def test_counts_occurrences(self):
        payload = b"\xff" + guid.to_bytes(FRIEND_A) + b"\xff" + guid.to_bytes(FRIEND_A)
        found = scan.find_occurrences(payload, FRIEND_A)
        assert found.total == 2

    def test_tracks_alignment(self):
        payload = guid.to_bytes(FRIEND_A) + b"\xff" + guid.to_bytes(FRIEND_A)
        found = scan.find_occurrences(payload, FRIEND_A)
        assert found.total == 2
        assert found.aligned_4 == 1  # offset 0 aligned, offset 17 not

    def test_offsets_are_capped(self):
        payload = guid.to_bytes(FRIEND_A) * 100
        found = scan.find_occurrences(payload, FRIEND_A, keep_offsets=5)
        assert found.total == 100
        assert len(found.offsets) == 5

    def test_host_pattern_matches_incidental_padding(self):
        """
        A struct of zero padding followed by a count of 1 is byte-identical to
        the host GUID. This is the whole problem, expressed as one assertion.
        """
        innocent = b"\x00" * 12 + b"\x01\x00\x00\x00"
        assert scan.find_occurrences(innocent, HOST).total == 1


class TestCollisionReport:
    def _payload(self, host_hits: int, friend_hits: int) -> bytes:
        return guid.to_bytes(HOST) * host_hits + guid.to_bytes(FRIEND_A) * friend_hits

    def test_flags_host_as_unsafe(self):
        report = scan.build_report(self._payload(2904, 174), HOST, [FRIEND_A])
        assert report.is_safe_to_byte_replace is False

    def test_estimates_false_positives(self):
        report = scan.build_report(self._payload(2904, 174), HOST, [FRIEND_A])
        assert report.estimated_false_positives == 2904 - 174

    def test_summary_states_the_verdict(self):
        report = scan.build_report(self._payload(2904, 174), HOST, [FRIEND_A])
        assert "NOT SAFE" in report.summary()

    def test_unsafe_even_when_counts_are_close(self):
        """
        The host id must be rejected on its byte pattern alone. A low hit count
        in some particular file does not make blind replacement acceptable.
        """
        report = scan.build_report(self._payload(2, 2), HOST, [FRIEND_A])
        assert report.is_safe_to_byte_replace is False

    def test_no_references_is_not_safe(self):
        report = scan.build_report(self._payload(1, 0), HOST, [])
        assert report.is_safe_to_byte_replace is False
        assert report.reference_max == 0
