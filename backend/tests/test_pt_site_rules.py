from app.core.pt_site_rules import (
    AVISTAZ_DEFAULT_HNR_DAYS,
    effective_hnr_rule,
    hnr_acknowledgement_required,
)


def test_avistaz_always_uses_the_seven_day_site_default() -> None:
    for upstream_value in (None, False, True):
        rule = effective_hnr_rule("AvistaZ", upstream_value)
        assert rule.applies is True
        assert rule.known is True
        assert rule.source == "SITE_DEFAULT"
        assert rule.minimum_seeding_days == AVISTAZ_DEFAULT_HNR_DAYS == 7
    assert hnr_acknowledgement_required("avistaz") is False


def test_other_sites_keep_unknown_and_candidate_metadata_semantics() -> None:
    unknown = effective_hnr_rule("nexus-test", None)
    assert unknown.applies is None
    assert unknown.known is False
    assert unknown.source == "UNKNOWN"
    known = effective_hnr_rule("nexus-test", False)
    assert known.applies is False
    assert known.known is True
    assert known.source == "CANDIDATE_METADATA"
    assert hnr_acknowledgement_required("nexus-test") is True
