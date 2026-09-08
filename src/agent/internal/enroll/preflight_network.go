package enroll

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"hyperfilelens/agent/internal/platform/tlsclient"
)

func resolveWSSURL(cfg Config) string {
	if wss := strings.TrimSpace(cfg.WSSURL); wss != "" {
		return wss
	}
	base := strings.TrimRight(strings.TrimSpace(cfg.APIBase), "/")
	if base == "" {
		return ""
	}
	parsed, err := url.Parse(base)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return ""
	}
	scheme := "wss"
	if parsed.Scheme == "http" {
		scheme = "ws"
	}
	return scheme + "://" + parsed.Host + "/ws/node/agent/"
}

type reachResult struct {
	OK         bool
	Warning    bool
	Title      string
	Detail     string
	FailDetail string
}

func checkConsoleReachable(ctx context.Context, apiBase string) reachResult {
	base := strings.TrimRight(strings.TrimSpace(apiBase), "/")
	healthURL := base + "/api/v1/node/health"
	if base == "" {
		return reachResult{
			Title:  "Console API URL is not configured",
			Detail: "no console URL configured",
		}
	}

	reqCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, healthURL, nil)
	if err != nil {
		return reachResult{
			Title:  "Console API URL is invalid",
			Detail: healthURL,
		}
	}

	client := &http.Client{Timeout: 8 * time.Second}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}

	resp, err := client.Do(req)
	if err != nil {
		return reachResult{
			Title:  "Console API is unreachable",
			Detail: fmt.Sprintf("GET %s - %s", healthURL, shortenErr(err)),
		}
	}
	resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 500 {
		return reachResult{
			OK:     true,
			Title:  "Console API is reachable",
			Detail: fmt.Sprintf("GET %s returned %d", healthURL, resp.StatusCode),
		}
	}
	return reachResult{
		Title:  "Console API is unreachable",
		Detail: fmt.Sprintf("GET %s returned %d", healthURL, resp.StatusCode),
	}
}

func checkWSSReachable(ctx context.Context, wssURL string) reachResult {
	return checkWSSReachableWithRetry(
		ctx,
		wssURL,
		30*time.Second,
		[]time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second, 8 * time.Second},
	)
}

func checkWSSReachableWithRetry(
	ctx context.Context,
	wssURL string,
	retryWindow time.Duration,
	retryDelays []time.Duration,
) reachResult {
	wssURL = strings.TrimSpace(wssURL)
	if wssURL == "" {
		return reachResult{
			Title:  "Control-plane WebSocket URL is not configured",
			Detail: "control plane URL not configured",
		}
	}

	parsed, err := url.Parse(wssURL)
	if err != nil || parsed.Host == "" {
		return reachResult{
			Title:  "Control-plane WebSocket URL is invalid",
			Detail: wssURL,
		}
	}

	endpoint := parsed.Scheme + "://" + parsed.Host
	if parsed.Scheme != "ws" && parsed.Scheme != "wss" {
		return reachResult{
			Title:  "Control-plane WebSocket scheme is unsupported",
			Detail: wssURL,
		}
	}

	retryCtx, cancel := context.WithTimeout(ctx, retryWindow)
	defer cancel()
	for attempt := 0; ; attempt++ {
		result, retry := checkWSSReachableOnce(retryCtx, parsed, endpoint)
		if result.OK || !retry || attempt >= len(retryDelays) || retryCtx.Err() != nil {
			return result
		}

		delay := retryDelays[attempt]
		timer := time.NewTimer(delay)
		select {
		case <-retryCtx.Done():
			timer.Stop()
			return result
		case <-timer.C:
		}
	}
}

func checkWSSReachableOnce(
	ctx context.Context,
	parsed *url.URL,
	endpoint string,
) (reachResult, bool) {
	dialCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	dialer := websocket.Dialer{
		HandshakeTimeout: 8 * time.Second,
		Proxy:            http.ProxyFromEnvironment,
		TLSClientConfig:  tlsclient.Config(),
	}
	conn, response, err := dialer.DialContext(dialCtx, parsed.String(), nil)
	if response != nil && response.Body != nil {
		_ = response.Body.Close()
	}
	if conn != nil {
		_ = conn.Close()
	}
	if err == nil {
		return reachResult{
			OK:     true,
			Title:  "Control plane WebSocket endpoint reachable",
			Detail: endpoint + " accepted the WebSocket handshake",
		}, false
	}
	if response != nil &&
		(response.StatusCode == http.StatusBadRequest ||
			response.StatusCode == http.StatusUnauthorized ||
			response.StatusCode == http.StatusForbidden) {
		return reachResult{
			OK:    true,
			Title: "Control plane WebSocket endpoint reachable",
			Detail: fmt.Sprintf(
				"%s returned %d before node authentication",
				endpoint,
				response.StatusCode,
			),
		}, false
	}
	detail := fmt.Sprintf("%s - %s", endpoint, shortenErr(err))
	if response != nil {
		detail = fmt.Sprintf("%s returned %d", endpoint, response.StatusCode)
	}
	return reachResult{
		Title:  "WebSocket endpoint unreachable",
		Detail: detail,
	}, transientWSSFailure(response, err)
}

func transientWSSFailure(response *http.Response, err error) bool {
	if response != nil {
		switch response.StatusCode {
		case http.StatusBadGateway,
			http.StatusServiceUnavailable,
			http.StatusGatewayTimeout:
			return true
		default:
			return false
		}
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return dnsErr.IsTimeout || dnsErr.IsTemporary
	}
	var networkErr net.Error
	return errors.As(err, &networkErr) ||
		errors.Is(err, io.EOF) ||
		errors.Is(err, io.ErrUnexpectedEOF)
}

func websocketDialAddress(parsed *url.URL) string {
	if parsed == nil || parsed.Port() != "" {
		if parsed == nil {
			return ""
		}
		return parsed.Host
	}
	switch parsed.Scheme {
	case "wss":
		return net.JoinHostPort(parsed.Hostname(), "443")
	case "ws":
		return net.JoinHostPort(parsed.Hostname(), "80")
	default:
		return parsed.Host
	}
}

const maxClockSkew = 5 * time.Minute

type clockCheckResult struct {
	OK      bool
	Warning bool
	Title   string
	Detail  string
}

func checkClockSync(ctx context.Context, apiBase string) clockCheckResult {
	base := strings.TrimRight(strings.TrimSpace(apiBase), "/")
	if base == "" {
		return clockCheckResult{
			Warning: true,
			Title:   "System clock could not be verified",
			Detail:  "no console URL configured",
		}
	}

	reqCtx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	healthURL := base + "/api/v1/node/health"
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, healthURL, nil)
	if err != nil {
		return clockCheckResult{
			Warning: true,
			Title:   "System clock could not be verified",
			Detail:  healthURL,
		}
	}

	client := &http.Client{Timeout: 8 * time.Second}
	if tlsclient.InsecureTLSEnabled() {
		client.Transport = tlsclient.Transport()
	}

	resp, err := client.Do(req)
	if err != nil {
		return clockCheckResult{
			Warning: true,
			Title:   "System clock could not be verified",
			Detail:  "console unreachable",
		}
	}
	defer resp.Body.Close()

	dateHeader := resp.Header.Get("Date")
	if dateHeader == "" {
		return clockCheckResult{
			OK:     true,
			Title:  "System clock assumed correct",
			Detail: "console did not return Date header",
		}
	}
	serverTime, err := http.ParseTime(dateHeader)
	if err != nil {
		return clockCheckResult{
			Warning: true,
			Title:   "System clock could not be verified",
			Detail:  "invalid Date header from console",
		}
	}

	skew := time.Since(serverTime)
	if skew < 0 {
		skew = -skew
	}
	skewLabel := skew.Round(time.Second).String()
	if skew > maxClockSkew {
		return clockCheckResult{
			Warning: true,
			Title:   "System clock may be out of sync",
			Detail:  "skew " + skewLabel,
		}
	}
	return clockCheckResult{
		OK:     true,
		Title:  "System clock synchronized",
		Detail: "skew " + skewLabel,
	}
}

func shortenErr(err error) string {
	if err == nil {
		return "unknown error"
	}
	msg := strings.TrimSpace(err.Error())
	if len(msg) > 120 {
		return msg[:117] + "..."
	}
	return msg
}

func logReachResult(r reachResult, failures *preflightFailures) {
	switch {
	case r.OK:
		logOKDetail(r.Title, r.Detail)
	case r.Warning:
		logWarnDetail(r.Title, r.Detail)
	default:
		failures.add(r.Title, r.Detail, 2)
	}
}

func logClockResult(r clockCheckResult) {
	switch {
	case r.OK:
		logOKDetail(r.Title, r.Detail)
	case r.Warning:
		logWarnDetail(r.Title, r.Detail)
	}
}
