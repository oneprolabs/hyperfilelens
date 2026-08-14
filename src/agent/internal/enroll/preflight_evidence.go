package enroll

import (
	"fmt"
	"os"
	"strings"
)

type preflightFailures struct {
	first *InstallFailure
	count int
}

func logOKDetail(title, detail string) {
	emitDetailLine(" OK ", title, detail, os.Stdout)
}

func logWarnDetail(title, detail string) {
	emitDetailLine("WARN", title, detail, os.Stderr)
}

func (failures *preflightFailures) add(title, detail string, code int) {
	message := joinDetail(title, detail)
	emitDetailLine("FAIL", title, detail, os.Stderr)
	failures.count++
	if failures.first == nil {
		failure := InstallFailure{
			Stage:   "Preflight checks",
			Reason:  message,
			Code:    code,
			CodeKey: fmt.Sprintf("HFL-PREFLIGHT-%03d", code),
		}
		failures.first = &failure
	}
}

func (failures *preflightFailures) err() error {
	if failures == nil || failures.count == 0 || failures.first == nil {
		return nil
	}
	failure := *failures.first
	if failures.count > 1 {
		failure.Reason = fmt.Sprintf(
			"%d preflight checks failed; review the FAIL entries above. First failure: %s",
			failures.count,
			failure.Reason,
		)
	}
	return failure
}

func joinDetail(title, detail string) string {
	title = strings.TrimSpace(title)
	detail = strings.TrimSpace(detail)
	if title == "" {
		return detail
	}
	if detail == "" {
		return title
	}
	return title + " (" + detail + ")"
}
