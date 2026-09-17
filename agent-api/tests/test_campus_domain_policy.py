from app.services.chat.builtin_assistants.campus_services.domain_policy import (
    DomainPolicyError,
    filter_results_by_official_domains,
    host_allowed,
    normalize_official_domain,
    scope_query_to_official_domains,
    url_allowed,
)
import pytest


def test_normalize_and_match_official_domains():
    rule = normalize_official_domain({"host": "EXAMPLE.EDU.CN.", "include_subdomains": True})
    assert rule == {"host": "example.edu.cn", "include_subdomains": True}
    assert host_allowed("example.edu.cn", [rule])
    assert host_allowed("jwc.example.edu.cn", [rule])
    assert not host_allowed("evil-example.edu.cn", [rule])
    assert not host_allowed("example.edu.cn.evil.com", [rule])


def test_reject_invalid_admin_hosts():
    with pytest.raises(DomainPolicyError):
        normalize_official_domain("https://example.edu.cn/path")
    with pytest.raises(DomainPolicyError):
        normalize_official_domain("user@example.edu.cn")
    with pytest.raises(DomainPolicyError):
        normalize_official_domain("127.0.0.1")
    with pytest.raises(DomainPolicyError):
        normalize_official_domain("localhost")
    with pytest.raises(DomainPolicyError):
        normalize_official_domain("edu.cn")


def test_filter_results_keeps_only_allowlisted_urls():
    rules = [normalize_official_domain({"host": "example.edu.cn", "include_subdomains": True})]
    kept = filter_results_by_official_domains(
        [
            {"url": "https://jwc.example.edu.cn/notice", "title": "ok"},
            {"url": "https://evil-example.edu.cn/phish", "title": "bad"},
            {"url": "https://example.edu.cn.evil.com/", "title": "bad2"},
        ],
        rules,
    )
    assert [item["url"] for item in kept] == ["https://jwc.example.edu.cn/notice"]
    assert not url_allowed("https://evil-example.edu.cn/phish", rules)


def test_search_query_is_scoped_to_each_official_domain():
    rules = [
        normalize_official_domain("school.edu.cn"),
        normalize_official_domain("school.gov.cn"),
    ]
    assert scope_query_to_official_domains("新生报到 校园风光", rules) == (
        "新生报到 校园风光 (site:school.edu.cn OR site:school.gov.cn)"
    )
    assert scope_query_to_official_domains("新生报到", []) == "新生报到"
