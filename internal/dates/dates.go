// Package dates 提供确定性中文日期解析：「明天 / 下周三 / 9月2日 / 2026-09-02」统一成 date。
//
// 设计动机：LLM 做日期换算容易错（"下周三"到底是哪天），因此策略是 LLM 只负责
// 从句子里「找出」日期表述，统一交给本模块做确定性换算。时区固定中国标准时间
// （无夏令时，固定偏移即可，且免 tzdata 依赖）。
package dates

import (
	"regexp"
	"strconv"
	"strings"
	"time"
)

// CNtz 中国标准时间 UTC+8。
var CNtz = time.FixedZone("UTC+8", 8*3600)

var (
	fullRe = regexp.MustCompile(`(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})[日号]?`)
	mdRe   = regexp.MustCompile(`(\d{1,2})月(\d{1,2})[日号]?`)
	weekRe = regexp.MustCompile(`(下?)(?:周|星期)([一二三四五六日天])`)
)

var daysWords = []struct {
	word   string
	offset int
}{
	{"今天", 0}, {"今日", 0}, {"明天", 1}, {"明日", 1}, {"后天", 2},
}

const weekDays = "一二三四五六日"

// weekdayIndex 返回周几在「一二三四五六日」中的序号（一=0 … 日=6）。
var weekdayIndex = map[rune]int{'一': 0, '二': 1, '三': 2, '四': 3, '五': 4, '六': 5, '日': 6, '天': 6}

// Today 返回业务与解析统一使用的「今天」（中国时区）。
func Today() time.Time {
	return time.Now().In(CNtz)
}

// TodayISO 中国时区今天的 YYYY-MM-DD。
func TodayISO() string { return Today().Format("2006-01-02") }

// NowCN 中国时区当前时间。
func NowCN() time.Time { return time.Now().In(CNtz) }

// nowFn 可在测试中替换的「现在」。
var nowFn = Today

type hit struct {
	pos  int
	date time.Time
}

// safeDate 构造 date，非法（如 2 月 30 日）返回零值与 false。
func safeDate(y, m, d int) (time.Time, bool) {
	t := time.Date(y, time.Month(m), d, 0, 0, 0, 0, CNtz)
	if t.Year() != y || int(t.Month()) != m || t.Day() != d {
		return time.Time{}, false
	}
	return t, true
}

// matches 按出现位置收集全部可识别的日期。
func matches(text string, today time.Time) []hit {
	var found []hit
	for _, loc := range fullRe.FindAllStringSubmatchIndex(text, -1) {
		y, _ := strconv.Atoi(text[loc[2]:loc[3]])
		m, _ := strconv.Atoi(text[loc[4]:loc[5]])
		d, _ := strconv.Atoi(text[loc[6]:loc[7]])
		if dt, ok := safeDate(y, m, d); ok {
			found = append(found, hit{loc[0], dt})
		}
	}
	for _, loc := range mdRe.FindAllStringSubmatchIndex(text, -1) {
		m, _ := strconv.Atoi(text[loc[2]:loc[3]])
		d, _ := strconv.Atoi(text[loc[4]:loc[5]])
		dt, ok := safeDate(today.Year(), m, d)
		if ok && dt.Before(dayStart(today)) { // 已过去的「N月N日」顺延到明年
			dt, ok = safeDate(today.Year()+1, m, d)
		}
		if ok {
			found = append(found, hit{loc[0], dt})
		}
	}
	curWeekday := int(today.Weekday()) // 周日=0，与 Python 的 Monday=0 不同，统一换算
	pyWeekday := (curWeekday + 6) % 7  // Monday=0 … Sunday=6
	for _, loc := range weekRe.FindAllStringSubmatchIndex(text, -1) {
		target := weekdayIndex[[]rune(text[loc[4]:loc[5]])[0]]
		var delta int
		if text[loc[2]:loc[3]] == "下" { // 「下周X」= 下一周的周X（下周一为基准）
			delta = (7-pyWeekday)%7 + target
		} else { // 「周X」= 最近将来的周X（当天也算）
			delta = (target - pyWeekday + 7) % 7
		}
		found = append(found, hit{loc[0], today.AddDate(0, 0, delta)})
	}
	for _, w := range daysWords {
		start := 0
		for {
			pos := strings.Index(text[start:], w.word)
			if pos == -1 {
				break
			}
			found = append(found, hit{start + pos, today.AddDate(0, 0, w.offset)})
			start += pos + len(w.word)
		}
	}
	// 按出现位置稳定排序（同位置保持收集顺序）
	for i := 1; i < len(found); i++ {
		for j := i; j > 0 && found[j].pos < found[j-1].pos; j-- {
			found[j], found[j-1] = found[j-1], found[j]
		}
	}
	return found
}

func dayStart(t time.Time) time.Time {
	y, m, d := t.Date()
	return time.Date(y, m, d, 0, 0, 0, 0, CNtz)
}

// ParseAll 按出现顺序返回文本中的全部日期（中国时区当天为基准）。
// today 为 nil 时取中国时区现在。
func ParseAll(text string, today *time.Time) []time.Time {
	base := nowFn()
	if today != nil {
		base = *today
	}
	base = dayStart(base)
	var out []time.Time
	for _, h := range matches(text, base) {
		out = append(out, dayStart(h.date))
	}
	return out
}

// Parse 返回文本中第一个可识别的日期；识别不到返回零值与 false。
func Parse(text string, today *time.Time) (time.Time, bool) {
	all := ParseAll(text, today)
	if len(all) == 0 {
		return time.Time{}, false
	}
	return all[0], true
}

// ParseISO 便捷封装：解析并返回 YYYY-MM-DD，失败返回空串。
func ParseISO(text string, today *time.Time) string {
	d, ok := Parse(text, today)
	if !ok {
		return ""
	}
	return d.Format("2006-01-02")
}
