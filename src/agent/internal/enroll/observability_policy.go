package enroll

import (
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var (
	observabilityEnvironmentPattern = regexp.MustCompile(`^hfl-(test|community|preprod|production)$`)
	observabilityReleasePattern     = regexp.MustCompile(`^[A-Za-z0-9._@+-]+$`)
)

// ObservabilityPolicy is the bounded control-plane policy for a Data Gateway.
// Private Gateways receive only Enabled=false; DSNs are distributed exclusively
// to server-verified platform Gateways.
type ObservabilityPolicy struct {
	Enabled          bool
	BackendDSN       string
	Environment      string
	AgentRelease     string
	LensnodeRelease  string
	TracesSampleRate float64
}

func validPublicSentryDSN(value string) bool {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return false
	}
	if parsed.User == nil || parsed.User.Username() == "" {
		return false
	}
	if _, hasPassword := parsed.User.Password(); hasPassword {
		return false
	}
	if parsed.RawQuery != "" || parsed.Fragment != "" {
		return false
	}
	projectID := strings.TrimSpace(strings.TrimSuffix(parsed.Path, "/"))
	projectID = projectID[strings.LastIndex(projectID, "/")+1:]
	_, err = strconv.ParseUint(projectID, 10, 64)
	return err == nil
}

// Normalized returns a fail-closed policy safe to persist and apply locally.
func (p ObservabilityPolicy) Normalized() ObservabilityPolicy {
	if !p.Enabled {
		return ObservabilityPolicy{}
	}
	p.BackendDSN = strings.TrimSpace(p.BackendDSN)
	p.Environment = strings.TrimSpace(p.Environment)
	p.AgentRelease = strings.TrimSpace(p.AgentRelease)
	p.LensnodeRelease = strings.TrimSpace(p.LensnodeRelease)
	if !validPublicSentryDSN(p.BackendDSN) ||
		!observabilityEnvironmentPattern.MatchString(p.Environment) ||
		!observabilityReleasePattern.MatchString(p.AgentRelease) ||
		!observabilityReleasePattern.MatchString(p.LensnodeRelease) ||
		p.TracesSampleRate < 0 || p.TracesSampleRate > 1 {
		return ObservabilityPolicy{}
	}
	return p
}

func (p ObservabilityPolicy) agentEnvValues() map[string]string {
	p = p.Normalized()
	if !p.Enabled {
		return map[string]string{
			"HFL_SENTRY_POLICY_MANAGED": "true",
			"SENTRY_ENABLED":            "false",
		}
	}
	return map[string]string{
		"HFL_SENTRY_POLICY_MANAGED":   "true",
		"SENTRY_ENABLED":              "true",
		"SENTRY_BACKEND_DSN":          p.BackendDSN,
		"SENTRY_ENVIRONMENT":          p.Environment,
		"SENTRY_RELEASE":              p.AgentRelease,
		"SENTRY_TRACES_SAMPLE_RATE":   strconv.FormatFloat(p.TracesSampleRate, 'g', -1, 64),
		"HFL_SENTRY_LENSNODE_RELEASE": p.LensnodeRelease,
	}
}

func (p ObservabilityPolicy) lensnodeEnvValues() map[string]string {
	p = p.Normalized()
	if !p.Enabled {
		return map[string]string{"SENTRY_ENABLED": "false"}
	}
	return map[string]string{
		"SENTRY_ENABLED":              "true",
		"SENTRY_BACKEND_DSN":          p.BackendDSN,
		"SENTRY_ENVIRONMENT":          p.Environment,
		"SENTRY_TRACES_SAMPLE_RATE":   strconv.FormatFloat(p.TracesSampleRate, 'g', -1, 64),
		"HFL_SENTRY_LENSNODE_RELEASE": p.LensnodeRelease,
	}
}
