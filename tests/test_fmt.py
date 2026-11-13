import pytest

from fumes import fmt
from fumes.feed import Provider


@pytest.mark.parametrize("v,s", [(12.34, "$12.34"), (0, "$0.00"), (1234.5, "$1234.50"),
                                 (0.004, "<$0.01"), (0.001, "<$0.01"), (0.005, "$0.01"),
                                 (0.01, "$0.01"), (-1.2, "-$1.20"), (7.254112, "$7.25")])
def test_money(v, s):
    assert fmt.money(v) == s


def test_figure_rules():
    assert fmt.figure(Provider("a", "A", balance=2.86, mtd=9)) == ("$2.86", "balance")
    assert fmt.figure(Provider("a", "A", balance=None, mtd=5.436120)) == ("$5.44", "mtd")      # no " mo" suffix
    assert fmt.figure(Provider("a", "A", balance=0.0, mtd=1)) == ("$0.00", "balance")
    assert fmt.figure(Provider("a", "A")) == ("\u2013", "na") and fmt.DASH == "\u2013"      # an en dash, not n/a


@pytest.mark.parametrize("sec,s", [(5, "now"), (59, "now"), (60, "1m old"), (3599, "59m old"),
                                   (3600, "1h old"), (3 * 3600 + 5, "3h old"),
                                   (86400, "1d old"), (-5, "now")])
def test_age_text(sec, s):
    assert fmt.age_text(sec) == s


def test_tooltip_contents_and_wrap():
    p = Provider("a", "Acme", status="error", balance=1.5, mtd=None, d30=2.0,
                 checked="2026-11-12T11:30:56Z", note="word " * 40, error="boom")
    t = fmt.tooltip(p, width=30)
    assert "Acme" in t and "Balance: $1.50" in t and "Month to date: n/a" in t
    assert "Last 30 days: $2.00" in t and "Status: error" in t and "boom" in t
    assert max(len(line) for line in t.splitlines()) <= 40  # note lines wrapped


def test_recolor_svg():
    assert fmt.recolor_svg('<svg fill="currentColor"/>', "#f2f2f2") == '<svg fill="#f2f2f2"/>'


def test_tooltip_html_escapes_markup_from_the_feed():
    from types import SimpleNamespace
    p = SimpleNamespace(name="<b>x</b>", balance=1.0, mtd=None, d30=None, status="ok",
                        checked="2026-11-12T11:30:56Z", note='<img src="file:///c:/x">', error="a & b")
    t = fmt.tooltip_html(p)
    assert t.startswith("<p style='white-space:pre'>") and t.endswith("</p>")
    body = t[len("<p style='white-space:pre'>"):-len("</p>")]
    assert "<" not in body and "&lt;b&gt;x&lt;/b&gt;" in body and "a &amp; b" in body


@pytest.mark.parametrize("d,s", [("2026-11-06", "Nov 6"), ("2026-10-14", "Oct 14"), ("2026-01-01", "Jan 1"),
                                 ("2026-12-31", "Dec 31"), ("junk", "junk")])
def test_short_date(d, s):
    assert fmt.short_date(d) == s


@pytest.mark.parametrize("v,top", [(3.64, 5), (5, 5), (5.01, 10), (7.82, 10), (1.2, 2), (2.0, 2), (0.3, 0.5),
                                   (0.07, 0.1), (11, 20), (21, 50), (100, 100), (0, 1), (-3, 1)])
def test_nice_top(v, top):
    assert fmt.nice_top(v) == pytest.approx(top)


def rows(n, other=None):
    r = [(f"m{i}", float(n - i)) for i in range(n)]
    if other is not None:
        r.insert(other, ("Other", 0.5))
    return r


def test_fold_breakdown_keeps_eight_or_fewer():
    for n in (0, 1, 7, 8):
        assert fmt.fold_breakdown(rows(n)) == rows(n)
    assert fmt.fold_breakdown(rows(7, other=7)) == rows(7, other=7)


def test_fold_breakdown_over_eight_gives_seven_plus_other():
    out = fmt.fold_breakdown(rows(10))
    assert len(out) == 8 and out[:7] == rows(10)[:7]
    assert out[7] == ("Other", 3.0 + 2.0 + 1.0)


def test_fold_breakdown_merges_existing_other():
    out = fmt.fold_breakdown(rows(8, other=8))        # 8 rows + Other = 9
    assert [l for l, _ in out].count("Other") == 1 and len(out) == 8
    assert out[7] == ("Other", 1.0 + 0.5)
    out = fmt.fold_breakdown(rows(12, other=3))
    assert [l for l, _ in out].count("Other") == 1 and len(out) == 8
    assert out[7][1] == pytest.approx(sum(u for _, u in rows(12)[7:]) + 0.5)


def test_chart_caption_default_hover_empty():
    days = ["2026-11-05", "2026-11-06", "2026-11-07"]
    vals = [1.0, 3.641290, 0.0]
    assert fmt.chart_caption(days, vals) == "Peak: $3.64 on Nov 6"
    assert fmt.chart_caption(days, vals, hover=0) == "Nov 5: $1.00"
    assert fmt.chart_caption(days, vals, hover=2) == "Nov 7: $0.00"
    assert fmt.chart_caption(days, [0, 0, 0]) == "No spend in the last 30 days"
    assert fmt.chart_caption([], []) == "No spend in the last 30 days"
    assert fmt.chart_caption(days, vals, hover=9) == "Peak: $3.64 on Nov 6"


def test_break_long_words():
    fits = lambda w: len(w) <= 5
    assert fmt.break_long_words("ab cdefghijkl m", fits) == "ab cdefg hijkl m"
    assert fmt.break_long_words("short words", fits) == "short words"


def test_break_long_words_is_cheap_on_one_huge_run_and_always_advances():
    calls = []
    def fits(chunk):
        calls.append(1)
        return len(chunk) <= 7
    out = fmt.break_long_words("x" * 5000, fits)
    assert out.replace(" ", "") == "x" * 5000 and all(len(c) <= 7 for c in out.split(" "))
    assert len(calls) < 5000 * 16                      # bisection, not one test per character per chunk
    assert fmt.break_long_words("abc", lambda c: False) == "a b c"   # nothing fits: still terminates


# ---- Claude limits ----------------------------------------------------------------
from datetime import datetime, timedelta, timezone  # noqa: E402

from fumes import limits as lim  # noqa: E402

EST = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 12, 12, 20, tzinfo=timezone.utc)      # 07:20 in EST


@pytest.mark.parametrize("sec,s", [(0, "in under a minute"), (59, "in under a minute"), (60, "in 1m"),
                                   (44 * 60 + 59, "in 44m"), (3599, "in 59m"), (3600, "in 1h 0m"),
                                   (3 * 3600 + 24 * 60 + 30, "in 3h 24m"), (86399, "in 23h 59m"),
                                   (86400, "in 1d 0h"), (2 * 86400 + 5 * 3600 + 3000, "in 2d 5h"),
                                   (-5, "in under a minute")])
def test_countdown(sec, s):
    assert fmt.countdown(sec) == s


def v_at(delta_s):
    return lim.View(50.0, int(NOW.timestamp()) + delta_s, False)


def test_reset_line_same_day_vs_other_day_in_the_given_zone():
    assert fmt.reset_line(v_at(3 * 3600 + 24 * 60), NOW, EST) == "Resets 10:44 (in 3h 24m)"
    # 18h 44m later is tomorrow (Fri 2026-11-13) in EST
    assert fmt.reset_line(v_at(18 * 3600 + 44 * 60), NOW, EST) == "Resets Fri 02:04 (in 18h 44m)"
    # the same instant is the same day in UTC, so the zone really is injected
    assert fmt.reset_line(v_at(3 * 3600 + 24 * 60), NOW, timezone.utc) == "Resets 15:44 (in 3h 24m)"
    assert fmt.reset_line(v_at(12 * 3600), NOW, timezone.utc).startswith("Resets Fri 00:20")


def test_reset_line_midnight_boundary():
    late = datetime(2026, 11, 13, 4, 50, tzinfo=timezone.utc)       # 23:50 on Thu the 12th in EST
    t0 = int(late.timestamp())
    assert fmt.reset_line(lim.View(1.0, t0 + 5 * 60, False), late, EST) == "Resets 23:55 (in 5m)"
    assert fmt.reset_line(lim.View(1.0, t0 + 15 * 60, False), late, EST) == "Resets Fri 00:05 (in 15m)"


def test_reset_line_reset_and_absent():
    assert fmt.reset_line(lim.View(0.0, None, True), NOW, EST) == "Reset: a new window starts with your next message"
    assert fmt.reset_line(lim.ABSENT, NOW, EST) == "No reading yet"


def test_limits_tooltip_and_local_dt():
    views = [("5-hour", v_at(3600)), ("Weekly", lim.ABSENT)]
    t = fmt.limits_tooltip(views, NOW, NOW, EST)
    assert t.splitlines() == ["Claude subscription", "5-hour: 50% used, resets 08:20 (in 1h 0m)",
                              "Weekly: no reading yet", "Updated: 2026-11-12 07:20"]
    assert fmt.local_dt(NOW, EST) == "2026-11-12 07:20"


def test_escape_tooltip_escapes():
    assert "<b>" not in fmt.escape_tooltip("<b>x</b>") and "&lt;b&gt;" in fmt.escape_tooltip("<b>x</b>")


def test_fit_pixel_size_with_a_linear_metric():
    cap = lambda px: 0.72 * px          # noqa: E731  (fake, linear)
    px = fmt.fit_pixel_size(cap, 18)
    assert px == 25 and abs(cap(px) - 18) <= 0.5       # 18.0 exactly
    assert fmt.fit_pixel_size(lambda px: 0.7 * px, 18) == 26      # 18.2: within the tolerance, closest
    assert fmt.fit_pixel_size(lambda px: 0.7 * px, 18, tol=0) == 25  # never above the target when tol=0
    assert fmt.fit_pixel_size(lambda px: 10.0 * px, 18) == 6       # nothing fits: the smallest size
    assert fmt.fit_pixel_size(lambda px: 0.5 * px, 18, hi=20) == 20  # capped by the search range


def test_local_dt_falls_back_to_utc_when_the_platform_cannot_convert():
    from datetime import datetime, timezone

    class Unconvertible(datetime):
        def astimezone(self, tz=None):
            raise OSError("[Errno 22] Invalid argument")

    dt = Unconvertible(2026, 11, 12, 7, 16, tzinfo=timezone.utc)
    assert fmt.local_dt(dt) == "2026-11-12 07:16 UTC"
    assert fmt.local_time("1969-12-31T00:00:00Z") == "1969-12-31T00:00:00Z"   # unparsed: shown as given


def test_clean_text_strips_backticks():
    assert fmt.clean_text("Run `acme-tool anchor openai <usd>` now") == "Run acme-tool anchor openai <usd> now"
    assert fmt.clean_text("") == "" and fmt.clean_text(None) == ""
    p = Provider("a", "A", note="Run `x`", error="`y`")
    assert "`" not in fmt.tooltip(p)


@pytest.mark.parametrize("v,s", [(10.0, "$10"), (5, "$5"), (0.5, "$0.50"), (2.5, "$2.50"), (100.0, "$100"), (0.1, "$0.10")])
def test_axis_money_whole_dollars_without_cents(v, s):
    assert fmt.axis_money(v) == s


def test_checked_text_time_only_when_today_in_local_time():
    from datetime import datetime, timedelta, timezone
    est = timezone(timedelta(hours=-5))
    now = datetime(2026, 11, 12, 12, 20, tzinfo=timezone.utc)                  # 07:20 on Nov 12 in EST
    assert fmt.checked_text("2026-11-12T11:30:56Z", now, est) == "Checked 06:30"
    assert fmt.checked_text("2026-11-11T11:30:56Z", now, est) == "Checked Nov 11 06:30"
    # today by UTC but yesterday by the local clock, and the reverse: the zone decides
    assert fmt.checked_text("2026-11-12T03:00:00Z", now, est) == "Checked Nov 11 22:00"
    assert fmt.checked_text("2026-11-12T03:00:00Z", now, timezone.utc) == "Checked 03:00"
    assert fmt.checked_text("2026-10-01T09:05:00Z", now, timezone.utc) == "Checked Oct 1 09:05"
    assert fmt.checked_text("junk", now, est) == "Checked junk" and fmt.checked_text("", now, est) == "Checked unknown"


def test_no_spend_caption_only_for_a_connected_provider():
    days = ["2026-11-01", "2026-11-02"]
    assert fmt.chart_caption(days, [0.0, 0.0]) == "No spend in the last 30 days"
    assert fmt.chart_caption(days, [0.0, 0.0], connected=True) == "No spend in the last 30 days"
    assert fmt.chart_caption(days, [0.0, 0.0], connected=False) == ""
    assert fmt.chart_caption(days, [0.0, 2.0], connected=False) == "Peak: $2.00 on Nov 2"


def test_limits_tooltip_carries_the_numbers():
    from datetime import datetime, timedelta, timezone
    from fumes import limits as lim
    est = timezone(timedelta(hours=-5))
    now = datetime(2026, 11, 12, 12, 20, tzinfo=timezone.utc)
    ts = now.timestamp()
    views = [("5-hour limit", lim.view(lim.Window(26.0, 1794498000), ts)),
             ("Weekly limit", lim.view(lim.Window(94.0, 1794553200), ts))]
    t = fmt.limits_tooltip(views, now, now, est)
    assert t.splitlines()[1:3] == ["5-hour limit: 26% used, resets 10:40 (in 3h 20m)",
                                   "Weekly limit: 94% used, resets Fri 02:00 (in 18h 40m)"]
    absent = [("5-hour limit", lim.ABSENT), ("Weekly limit", lim.view(lim.Window(50, 5), ts))]
    t = fmt.limits_tooltip(absent, now, now, est)
    assert "5-hour limit: no reading yet" in t and "Weekly limit: 0% used (reset)" in t
