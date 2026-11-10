"""The naming rule of the S2 and S2-GitOps guards (tools/veracity_replay.py, cause_named)."""
from tools.veracity_replay import cause_named


def test_whole_token_and_kind():
    assert cause_named("Application/argocd/billing", "Application/argocd/billing self-heals the limit")
    assert not cause_named("Application/argocd/billing", "billing-worker of billing-worker-664b exceeds its limit")
    assert not cause_named("Application/argocd/checkout", "ConfigMap/shop/checkout-config: last written")
    assert not cause_named("Application/argocd/billing", "the billing namespace")          # name without the kind
    assert cause_named("HelmRelease/shop/pricing", "HelmRelease.helm.toolkit.fluxcd.io/shop/pricing: Released=False")
    assert cause_named("ConfigMap/shop/shop-env", "ConfigMap/shop/shop-env: last written")
