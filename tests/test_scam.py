from sahara.scam import heuristic_risk, verdict


def test_digital_arrest_script_is_blocked():
    risk, labels = heuristic_risk("This is CBI. A parcel with drugs is in your name. You are under digital arrest. "
                                  "Do not tell anyone. Share the OTP now.")
    assert risk >= 0.9 and "digital arrest" in labels and verdict(risk) == "block"


def test_hindi_otp_request_is_flagged():
    risk, labels = heuristic_risk("बैंक से बोल रहा हूँ, आपका खाता बंद हो जाएगा, ओटीपी बताइए")
    assert risk >= 0.6 and "asks for OTP" in labels


def test_neighbour_is_fine():
    risk, labels = heuristic_risk("Hello, this is Meena from next door, is the plumber coming today?")
    assert risk == 0.0 and labels == [] and verdict(risk) == "connect"


def test_single_weak_signal_is_message_not_block():
    risk, _ = heuristic_risk("Your courier parcel could not be delivered")
    assert 0.25 <= risk < 0.6 and verdict(risk) == "message"
