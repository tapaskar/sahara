from sahara.models import Family, Parent
from sahara.summarize import offline_summary


def test_offline_summary_reads_observations_and_scam_words():
    p = Parent(family_id=1, name="Sushila", phone="+91", language="hi-IN")
    f = Family(child_name="Ravi", child_phone="+91")
    turns = [{"who": "sahara", "text": "नमस्ते"}, {"who": "parent", "text": "ठीक हूँ बेटा, घुटने में दर्द है"},
             {"who": "parent", "text": "कोई फोन आया था, ओटीपी मांग रहा था"}]
    obs = [{"kind": "medication", "detail": "Took morning tablets", "severity": "info"},
           {"kind": "health", "detail": "Knee pain since yesterday", "severity": "warn"}]
    s = offline_summary(turns, obs, p, f)
    assert s.medications_taken is True and s.health_concerns == ["Knee pain since yesterday"]
    assert s.scam_mentions and s.follow_up and "Sushila" in s.child_message
    assert 0 < s.engagement <= 1
