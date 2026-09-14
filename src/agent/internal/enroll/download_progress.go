package enroll

import (
	"context"
	"fmt"
	"math"
	"net/url"
	"os"
	"path"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/mattn/go-isatty"

	"hyperfilelens/agent/internal/model"
	platforminstall "hyperfilelens/agent/internal/platform/install"
)

const nonTerminalProgressInterval = 30 * time.Second

type downloadProgressDisplay struct {
	label          string
	terminal       bool
	mu             sync.Mutex
	lastLogged     time.Duration
	latest         platforminstall.DownloadProgress
	terminalActive bool
}

func newDownloadProgressDisplay(label string) *downloadProgressDisplay {
	initOutput()
	stdout := commandStdout()
	terminal := useColor && !jsonOutput() &&
		(isatty.IsTerminal(stdout.Fd()) || isatty.IsCygwinTerminal(stdout.Fd()))
	return &downloadProgressDisplay{
		label:    label,
		terminal: terminal,
	}
}

func (display *downloadProgressDisplay) report(progress platforminstall.DownloadProgress) {
	display.mu.Lock()
	defer display.mu.Unlock()
	display.latest = progress
	if !display.terminal && !progress.Completed {
		if progress.Elapsed-display.lastLogged < nonTerminalProgressInterval {
			return
		}
		display.lastLogged = progress.Elapsed
	}
	line := fmt.Sprintf("  [....] %s %s", display.label, formatDownloadProgress(progress))
	if columns, _ := strconv.Atoi(strings.TrimSpace(os.Getenv("COLUMNS"))); columns > 20 && len(line) > columns {
		line = fmt.Sprintf("  [....] %s %s", display.label, compactDownloadProgress(progress))
		if len(line) > columns {
			line = line[:columns]
		}
	}
	if display.terminal {
		if progress.Completed {
			return
		}
		fmt.Fprintf(os.Stdout, "\r%s\033[K", line)
		display.terminalActive = true
		return
	}
	if jsonOutput() {
		emitJSON(os.Stdout, map[string]any{
			"type":             "download_progress",
			"label":            display.label,
			"downloaded_bytes": progress.DownloadedBytes,
			"total_bytes":      progress.TotalBytes,
			"bytes_per_second": progress.BytesPerSecond,
			"elapsed_seconds":  progress.Elapsed.Seconds(),
			"completed":        progress.Completed,
		})
		return
	}
	if progress.Completed {
		return
	}
	fmt.Fprintf(os.Stdout, "  [INFO] Download progress: %s %s\n", display.label, compactDownloadProgress(progress))
}

func compactDownloadProgress(progress platforminstall.DownloadProgress) string {
	if progress.TotalBytes <= 0 {
		return formatByteCount(progress.DownloadedBytes)
	}
	percent := float64(progress.DownloadedBytes) / float64(progress.TotalBytes) * 100
	percent = math.Max(0, math.Min(100, percent))
	return fmt.Sprintf("%.0f%% %s/%s", percent, formatByteCount(progress.DownloadedBytes), formatByteCount(progress.TotalBytes))
}

func (display *downloadProgressDisplay) abort() {
	display.mu.Lock()
	defer display.mu.Unlock()
	if display.terminal && display.terminalActive {
		fmt.Fprintln(os.Stdout)
		display.terminalActive = false
	}
}

func (display *downloadProgressDisplay) success() {
	message := display.successMessage()
	display.mu.Lock()
	terminal := display.terminal
	if terminal {
		styled := colorize(ansiGreen, " OK ")
		fmt.Fprintf(os.Stdout, "\r  [%s] %s\033[K\n", styled, ensureSentence(message))
		display.terminalActive = false
	}
	display.mu.Unlock()
	if !terminal {
		logOK(message)
	}
}

func (display *downloadProgressDisplay) successMessage() string {
	display.mu.Lock()
	defer display.mu.Unlock()
	progress := display.latest
	average := float64(0)
	if progress.Elapsed > 0 {
		average = float64(progress.DownloadedBytes) / progress.Elapsed.Seconds()
	}
	return fmt.Sprintf(
		"%s downloaded (%s in %s, average %s)",
		display.label,
		formatByteCount(progress.DownloadedBytes),
		formatElapsed(progress.Elapsed),
		formatRate(average),
	)
}

func downloadWithProgress(
	ctx context.Context,
	rawURL string,
	destPath string,
	label string,
) error {
	display := newDownloadProgressDisplay(label)
	err := platforminstall.DownloadURLWithProgress(
		ctx,
		rawURL,
		destPath,
		display.report,
	)
	if err != nil {
		display.abort()
		return err
	}
	display.success()
	return nil
}

func downloadResumableWithProgress(
	ctx context.Context,
	rawURL string,
	destPath string,
	label string,
) error {
	display := newDownloadProgressDisplay(label)
	err := platforminstall.DownloadURLResumableWithProgress(
		ctx,
		rawURL,
		destPath,
		display.report,
		func(attempt, maxAttempts int, delay time.Duration, retryErr error, resumeBytes int64) {
			display.abort()
			logWarn(fmt.Sprintf("%s download interrupted: %v", label, retryErr))
			if resumeBytes > 0 {
				logInfo(fmt.Sprintf(
					"Retrying in %s (attempt %d/%d); resuming from %s",
					formatElapsed(delay), attempt+1, maxAttempts, formatByteCount(resumeBytes),
				))
				return
			}
			logInfo(fmt.Sprintf(
				"Retrying in %s (attempt %d/%d); restarting from byte zero",
				formatElapsed(delay), attempt+1, maxAttempts,
			))
		},
	)
	if err != nil {
		display.abort()
		return err
	}
	display.success()
	return nil
}

func formatDownloadProgress(progress platforminstall.DownloadProgress) string {
	downloaded := formatByteCount(progress.DownloadedBytes)
	elapsed := formatElapsed(progress.Elapsed)
	if progress.TotalBytes <= 0 {
		if progress.BytesPerSecond <= 0 && !progress.Completed {
			return fmt.Sprintf("%s downloaded | waiting for data | elapsed %s", downloaded, elapsed)
		}
		return fmt.Sprintf(
			"%s downloaded | %s | elapsed %s",
			downloaded,
			formatRate(progress.BytesPerSecond),
			elapsed,
		)
	}
	percent := float64(progress.DownloadedBytes) / float64(progress.TotalBytes) * 100
	percent = math.Max(0, math.Min(100, percent))
	filled := int(math.Round(percent / 5))
	filled = max(0, min(20, filled))
	bar := "[" + strings.Repeat("#", filled) + strings.Repeat("-", 20-filled) + "]"
	parts := []string{
		bar,
		fmt.Sprintf("%3.0f%%", percent),
		fmt.Sprintf("%s / %s", downloaded, formatByteCount(progress.TotalBytes)),
	}
	if progress.BytesPerSecond <= 0 && !progress.Completed {
		parts = append(parts, "waiting for data", "elapsed "+elapsed)
		return strings.Join(parts, " | ")
	}
	parts = append(parts, formatRate(progress.BytesPerSecond))
	remaining := progress.TotalBytes - progress.DownloadedBytes
	if remaining > 0 && progress.BytesPerSecond > 0 && !progress.Completed {
		eta := time.Duration(float64(remaining)/progress.BytesPerSecond) * time.Second
		parts = append(parts, "ETA "+formatElapsed(eta))
	}
	return strings.Join(parts, " | ")
}

func formatByteCount(bytes int64) string {
	if bytes < 0 {
		bytes = 0
	}
	const unit = int64(1024)
	if bytes < unit {
		return fmt.Sprintf("%d B", bytes)
	}
	units := []string{"KiB", "MiB", "GiB", "TiB"}
	value := float64(bytes)
	index := -1
	for value >= float64(unit) && index < len(units)-1 {
		value /= float64(unit)
		index++
	}
	return fmt.Sprintf("%.1f %s", value, units[index])
}

func formatRate(bytesPerSecond float64) string {
	if bytesPerSecond < 0 {
		bytesPerSecond = 0
	}
	return formatByteCount(int64(bytesPerSecond)) + "/s"
}

func formatElapsed(duration time.Duration) string {
	seconds := int64(math.Ceil(duration.Seconds()))
	if seconds < 1 {
		seconds = 1
	}
	if seconds < 60 {
		return fmt.Sprintf("%ds", seconds)
	}
	minutes := seconds / 60
	seconds %= 60
	if minutes < 60 {
		return fmt.Sprintf("%dm %02ds", minutes, seconds)
	}
	hours := minutes / 60
	minutes %= 60
	return fmt.Sprintf("%dh %02dm %02ds", hours, minutes, seconds)
}

func roleDisplayName(role model.Role, gatewayScope ...string) string {
	switch role {
	case model.RoleProxy:
		return "Proxy Host"
	case model.RoleGateway:
		scope := ""
		if len(gatewayScope) > 0 {
			scope = strings.ToLower(strings.TrimSpace(gatewayScope[0]))
		}
		if scope == "platform" {
			return "Platform Data Gateway"
		}
		if isPublicGatewayScope(scope) {
			return "Public Data Gateway"
		}
		return "Private Data Gateway"
	default:
		return "Source Host"
	}
}

func isPublicGatewayScope(scope string) bool {
	switch strings.ToLower(strings.TrimSpace(scope)) {
	case "public", "platform":
		return true
	default:
		return false
	}
}

func agentPackageLabel(role model.Role, gatewayScope ...string) string {
	return roleDisplayName(role, gatewayScope...) + " Agent package"
}

func safeDownloadFilename(rawURL string) string {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return ""
	}
	name := path.Base(parsed.Path)
	if name == "." || name == "/" || name == "" {
		return ""
	}
	return name
}
