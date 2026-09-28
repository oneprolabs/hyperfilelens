package enroll

import (
	"fmt"
	"strconv"
	"strings"
)

func normalizeVersion(value string) string {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "\uFEFF")
	return strings.TrimSpace(value)
}

// versionGreater reports whether version a is newer than b (semver-like numeric segments).
func versionGreater(a, b string) bool {
	a = normalizeVersion(a)
	b = normalizeVersion(b)
	if a == "" || b == "" || a == "unknown" || b == "unknown" {
		return false
	}
	ap := strings.Split(a, ".")
	bp := strings.Split(b, ".")
	n := len(ap)
	if len(bp) > n {
		n = len(bp)
	}
	for i := 0; i < n; i++ {
		av, bv := 0, 0
		if i < len(ap) {
			av, _ = strconv.Atoi(strings.TrimLeft(ap[i], "vV"))
		}
		if i < len(bp) {
			bv, _ = strconv.Atoi(strings.TrimLeft(bp[i], "vV"))
		}
		if av > bv {
			return true
		}
		if av < bv {
			return false
		}
	}
	return false
}

func versionLabel(installed, release string) string {
	installed = normalizeVersion(installed)
	release = normalizeVersion(release)
	if installed != "" && release != "" {
		return fmt.Sprintf("v%s -> v%s", installed, release)
	}
	if release != "" {
		return "v" + release
	}
	return ""
}
