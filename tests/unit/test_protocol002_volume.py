"""Protocol 002, amendment A3: the exact McNemar power behind the volume of S2 and S3."""
from tools.protocol002_volume import DELTA, exact_p, power


def test_the_exact_test_is_two_sided_and_symmetric():
    assert exact_p(9, 0) == exact_p(0, 9) < 0.01
    assert exact_p(3, 3) == 1.0


def test_280_cases_reach_the_power_and_s2_does_not():
    assert power(280, 0.474, DELTA, 0.05 / 8) >= 0.8                 # D vs L0, A3's number
    assert power(14, 0.474, DELTA, 0.05) < 0.1                       # S2's cases with a cause: exploratory


def test_no_difference_rejects_at_most_alpha():                     # control
    assert power(120, 0.21, 0.0, 0.05) <= 0.05
