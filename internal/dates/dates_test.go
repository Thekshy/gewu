package dates

import (
	"testing"
	"time"
)

// 2026-08-27 是周四（与 Python 版测试同基准日，保证行为对照）。
func base() time.Time {
	d, _ := time.ParseInLocation("2006-01-02", "2026-08-27", CNtz)
	return d
}

func p(t *testing.T, text string) (time.Time, bool) {
	t.Helper()
	return Parse(text, ptr(base()))
}

func ptr(t time.Time) *time.Time { return &t }

func TestFullDate(t *testing.T) {
	for _, c := range []struct{ in, want string }{
		{"2026-09-02", "2026-09-02"},
		{"2026/9/2", "2026-09-02"},
		{"2026年9月2日", "2026-09-02"},
	} {
		got, ok := p(t, c.in)
		if !ok || got.Format("2006-01-02") != c.want {
			t.Errorf("parse(%q) = %v,%v want %s", c.in, got, ok, c.want)
		}
	}
}

func TestMonthDayRollsToNextYear(t *testing.T) {
	if got, _ := p(t, "1月5日"); got.Format("2006-01-02") != "2027-01-05" {
		t.Errorf("1月5日 = %v, want 2027-01-05", got)
	}
	if got, _ := p(t, "9月2日"); got.Format("2006-01-02") != "2026-09-02" {
		t.Errorf("9月2日 = %v, want 2026-09-02", got)
	}
}

func TestInvalidCalendarDateRejected(t *testing.T) {
	if _, ok := p(t, "2月30日"); ok {
		t.Error("2月30日 不应解析成功")
	}
}

func TestRelativeWords(t *testing.T) {
	cases := []struct{ in, want string }{
		{"今天上课吗", "2026-08-27"},
		{"明天见", "2026-08-28"},
		{"后天交作业", "2026-08-29"},
	}
	for _, c := range cases {
		got, _ := p(t, c.in)
		if got.Format("2006-01-02") != c.want {
			t.Errorf("parse(%q) = %v, want %s", c.in, got, c.want)
		}
	}
}

func TestWeekday(t *testing.T) {
	cases := []struct{ in, want string }{
		{"周五", "2026-08-28"},
		{"周四", "2026-08-27"},  // 当天也算
		{"下周三", "2026-09-02"}, // 下一周的周三，不是下下周
		{"下周一", "2026-08-31"},
		{"星期五", "2026-08-28"},
		{"周日", "2026-08-30"},
	}
	for _, c := range cases {
		got, ok := p(t, c.in)
		if !ok || got.Format("2006-01-02") != c.want {
			t.Errorf("parse(%q) = %v,%v want %s", c.in, got, ok, c.want)
		}
	}
}

func TestParseAllKeepsOrder(t *testing.T) {
	got := ParseAll("下周一到下周二", ptr(base()))
	want := []string{"2026-08-31", "2026-09-01"}
	if len(got) != len(want) {
		t.Fatalf("len = %d, want %d", len(got), len(want))
	}
	for i, w := range want {
		if got[i].Format("2006-01-02") != w {
			t.Errorf("parse_all[%d] = %v, want %s", i, got[i], w)
		}
	}
}

func TestNoDateReturnsFalse(t *testing.T) {
	if _, ok := p(t, "这段话没有日期"); ok {
		t.Error("无日期文本不应解析成功")
	}
}

func TestMultipleOccurrencesOfSameWord(t *testing.T) {
	got := ParseAll("明天到后天", ptr(base()))
	if len(got) != 2 || got[0].Format("2006-01-02") != "2026-08-28" || got[1].Format("2006-01-02") != "2026-08-29" {
		t.Errorf("明天到后天 = %v", got)
	}
}
