from app.services.chat.turn_finalizer import scrub_false_search_hedge


def test_scrub_false_search_hedge_replaces_empty_refuse():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "点此复制今天漳州天气:晴,气温26℃~35℃, 3级.明天漳州天气:多云,气温27℃~36℃, 3级",
    }]
    bad = (
        "查了两次，今天（8月6日）漳州的具体天气数值没有拿到可核验的实时数据。"
        "需要说明：以上不能等同于今日实况，不编造具体数字。"
    )
    out = scrub_false_search_hedge(bad, trace=trace)
    assert "26" in out and "℃" in out
    assert "没能查到" not in out and "不编造" not in out


def test_scrub_false_search_hedge_noop_without_facts():
    text = "没能查到实时数据。"
    assert scrub_false_search_hedge(text, trace=[]) == text


def test_scrub_false_search_hedge_keeps_good_answer():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州晴 26℃~35℃",
    }]
    good = "今天漳州晴，气温 26℃～35℃，风力 3 级 [1]。"
    assert scrub_false_search_hedge(good, trace=trace) == good


def test_scrub_does_not_collapse_long_research_report_with_percentages():
    """非天气调研里的“仅供参考 + 百分比”不能触发天气数值回填并覆盖全文。"""
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": (
            "研究显示，只要实现并维持 5%–10% 的体重减轻，健康指标即可改善；"
            "睡眠不足可能影响食欲激素 18%/28%。"
        ),
    }]
    raw = (
        "研究完成。以下是整合后的结论。\n\n"
        "结论\n\n"
        "减肥没有捷径。所有有效方法的底层原理都是制造并维持合理热量缺口。\n\n"
        "核心发现\n\n"
        "**唯一不变的原理：热量缺口**\n"
        "每日 300–500 大卡的温和缺口更容易长期坚持；每周约减 0.5–1 斤脂肪，"
        "减重 5%–10% 就足以显著改善健康指标。\n\n"
        + ("均衡饮食、有氧运动、力量训练、充足睡眠和日常活动需要共同配合，不能依赖极端节食。" * 24)
        + "\n\n证据与局限\n\n"
        "力量训练提升静息代谢 7%、睡眠影响食欲激素 18%/28% 这两组数字来自单个公开来源，"
        "方向可靠但数值仅供参考。检索未覆盖处方减重药与手术，这部分需要线下医生评估。"
    )
    assert len(raw) > 1000
    assert scrub_false_search_hedge(raw, trace=trace) == raw


def test_scrub_false_search_hedge_no_result_claim_v258():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "今天漳州天气:晴,气温26℃~35℃, 3级",
    }]
    raw = "首次检索没有返回结果。两次尝试均无数据。不能凭空给出温度。"
    out = scrub_false_search_hedge(raw, trace=trace)
    assert "26" in out and "℃" in out
    assert "没有返回结果" not in out


def test_scrub_false_search_hedge_double_network_claim_v271():
    """v2.71: model still claims empty after successful search."""
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "今天漳州天气:小雨转晴,气温26℃~36℃, 3级",
    }]
    raw = (
        "管理员，很抱歉，刚才两次联网查询都没有返回有效的天气数据，"
        "所以我无法给出漳州今天的气温、降水等具体数值——这类信息我不会凭空编造。"
        "建议你直接查看官方一手渠道获取实时数据。"
    )
    out = scrub_false_search_hedge(raw, trace=trace)
    assert "26" in out and "℃" in out
    assert "两次联网" not in out
    assert "建议你直接查看" not in out


def test_scrub_false_search_hedge_short_preview_counts_v271():
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州 28℃",
    }]
    raw = "没有返回有效的天气数据，无法给出具体数值。"
    out = scrub_false_search_hedge(raw, trace=trace)
    assert "28" in out and "℃" in out


def test_scrub_false_search_hedge_no_date_only_backfill_v272():
    """v2.72: 只有标题/日期的检索结果，不能伪装成气温实答覆盖诚实局限说明。"""
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "[1] 漳州-天气预报（发布于 2026-07-30T12:00:00） 2026年5月26日 - 天气公报每日天气提示春运。",
    }]
    raw = (
        "目前检索只拿到天气预报服务入口的页面框架，没有抓到可核验的具体气温数值，"
        "我不能凭空编报数据。建议查看中央气象台或当地气象台发布。"
    )
    out = scrub_false_search_hedge(raw, trace=trace)
    # 不应出现「根据刚才的检索结果：…日期…」垃圾回填
    assert "根据刚才的检索结果" not in out
    assert "春运" not in out
    # 保留诚实局限（去掉过程腔后仍可保留）
    assert "气温" in out or "气象" in out or "不能" in out


def test_scrub_rejects_photo_caption_fake_temp():
    """图注/域名里的 37℃ 不得回填成可核验气温（真机 deep bug）。"""
    from app.services.chat.turn_finalizer import scrub_false_search_hedge, extract_search_concrete_snippets
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州港:37℃高温下的美丽港湾 mbd.baidu.com 家乡地方特色照之漳州 www.yooc.me",
    }]
    facts = extract_search_concrete_snippets(trace)
    assert not any("港湾" in f or "baidu" in f or "yooc" in f for f in facts)
    ans = (
        "很抱歉，本轮联网检索已被限制，两次尝试都没能取到漳州今日带数值的气温数据。"
        "我不编造数字，所以无法给出具体温度。[图1]"
    )
    out = scrub_false_search_hedge(ans, trace=trace)
    assert "mbd.baidu" not in out
    assert "yooc" not in out
    assert "lub.vmall" not in out
    # 不应把图注 37℃ 伪装成实况回填主句
    assert "美丽港湾" not in out
    assert "[图1]" in out or "照片" in out or "不编造" in out or "未能" in out or "没能" in out or "无法" in out


def test_scrub_keeps_real_weather_units():
    from app.services.chat.turn_finalizer import scrub_false_search_hedge
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "漳州今天多云 气温 26℃~35℃ 风力3级",
    }]
    ans = "检索次数已用尽，没能拿到可核验气温。[图1]"
    out = scrub_false_search_hedge(ans, trace=trace)
    assert "℃" in out or "°C" in out
    assert "26" in out or "35" in out


def test_scrub_rejects_news_digest_garbage():
    from app.services.chat.turn_finalizer import scrub_false_search_hedge, extract_search_concrete_snippets
    trace = [{
        "name": "search_web",
        "status": "completed",
        "preview": "州天气查询 12:天气资讯87:今天北京晴最高温6℃ 明后天气温持续走低跨年夜最低温仅零下8℃36:中国天气网",
    }]
    facts = extract_search_concrete_snippets(trace)
    assert not facts
    ans = "今天漳州炎热，最高约 37℃。[图1]"
    out = scrub_false_search_hedge(ans, trace=trace)
    assert "北京" not in out
    assert "跨年夜" not in out
    assert "37" in out
