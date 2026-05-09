from ontology.version import KubeVersion, _parse_int


class TestParseInt:
    def test_plain(self):             assert _parse_int("28") == 28
    def test_plus_suffix(self):       assert _parse_int("28+") == 28
    def test_k3s_suffix(self):        assert _parse_int("28") == 28
    def test_none(self):              assert _parse_int(None) == 0
    def test_empty(self):             assert _parse_int("") == 0
    def test_non_numeric(self):       assert _parse_int("abc") == 0


class TestKubeVersion:
    def test_gte_same(self):
        v = KubeVersion(1, 28, "v1.28.0")
        assert v.gte(1, 28)

    def test_gte_higher_minor(self):
        v = KubeVersion(1, 28, "v1.28.0")
        assert v.gte(1, 20)

    def test_gte_false(self):
        v = KubeVersion(1, 18, "v1.18.0")
        assert not v.gte(1, 19)

    def test_lt(self):
        v = KubeVersion(1, 18, "v1.18.0")
        assert v.lt(1, 19)

    # Ingress API version
    def test_ingress_v1_from_119(self):
        assert KubeVersion(1, 19, "v1.19.0").ingress_api_version == "networking.k8s.io/v1"

    def test_ingress_v1_on_128(self):
        assert KubeVersion(1, 28, "v1.28.3+k3s1").ingress_api_version == "networking.k8s.io/v1"

    def test_ingress_v1beta1_before_119(self):
        assert KubeVersion(1, 18, "v1.18.20").ingress_api_version == "networking.k8s.io/v1beta1"

    # CronJob API
    def test_cronjob_batch_v1_from_121(self):
        assert KubeVersion(1, 21, "v1.21.0").cronjob_api_version == "batch/v1"

    def test_cronjob_beta_before_121(self):
        assert KubeVersion(1, 20, "v1.20.0").cronjob_api_version == "batch/v1beta1"

    # HPA API
    def test_hpa_v2_from_126(self):
        assert KubeVersion(1, 26, "v1.26.0").hpa_api_version == "autoscaling/v2"

    def test_hpa_v2beta2_before_126(self):
        assert KubeVersion(1, 25, "v1.25.0").hpa_api_version == "autoscaling/v2beta2"

    # PSP removed in 1.25
    def test_psp_before_125(self):
        assert KubeVersion(1, 24, "v1.24.0").has_pod_security_policy

    def test_psp_removed_125(self):
        assert not KubeVersion(1, 25, "v1.25.0").has_pod_security_policy

    # K3s suffix survives str()
    def test_str_k3s(self):
        v = KubeVersion(1, 28, "v1.28.3+k3s1")
        assert str(v) == "v1.28.3+k3s1"
