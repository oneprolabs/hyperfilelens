package connection

import (
	"errors"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// patchLensCompose edits only the managed LensNode address and, when needed,
// its host-gateway mapping. Images, credentials and volume bindings stay intact.
func patchLensCompose(raw []byte, hostURL string) ([]byte, error) {
	u, err := url.Parse(hostURL)
	if err != nil || u.Hostname() == "" {
		return nil, errors.New("invalid AI engine address")
	}
	host := u.Hostname()
	if ip := net.ParseIP(host); strings.EqualFold(host, "localhost") || ip != nil && ip.IsLoopback() {
		port := u.Port()
		u.Host = "host.docker.internal"
		if port != "" {
			u.Host = net.JoinHostPort(u.Host, port)
		}
	}
	lines := strings.Split(string(raw), "\n")
	start, end := -1, len(lines)
	service := regexp.MustCompile(`^  [^ \t#][^:]*:`)
	for i, line := range lines {
		if strings.TrimRight(line, " \t\r") == "  lensnode:" {
			if start >= 0 {
				return nil, errors.New("ambiguous AI engine Compose service")
			}
			start = i
		} else if start >= 0 && end == len(lines) && service.MatchString(line) {
			end = i
		}
	}
	if start < 0 {
		return nil, errors.New("unsupported AI engine Compose service")
	}
	address := regexp.MustCompile(`^      LENSNODE_SERVER_URL:[^\r\n]*`)
	addressLine, hostsLine := -1, -1
	for i := start + 1; i < end; i++ {
		if address.MatchString(lines[i]) {
			if addressLine >= 0 {
				return nil, errors.New("ambiguous AI engine Compose address")
			}
			addressLine = i
		}
		if strings.HasPrefix(lines[i], "    extra_hosts:") {
			if hostsLine >= 0 || strings.TrimSpace(strings.TrimPrefix(lines[i], "    extra_hosts:")) != "" {
				return nil, errors.New("unsupported AI engine host mapping")
			}
			hostsLine = i
		}
	}
	if addressLine < 0 {
		return nil, errors.New("unsupported AI engine Compose configuration")
	}
	lines[addressLine] = "      LENSNODE_SERVER_URL: " + strconv.Quote(u.String())
	if strings.EqualFold(u.Hostname(), "host.docker.internal") {
		mapped := false
		if hostsLine >= 0 {
			for i := hostsLine + 1; i < end; i++ {
				line := strings.TrimSpace(lines[i])
				if line == "" || strings.HasPrefix(line, "#") {
					continue
				}
				if !strings.HasPrefix(lines[i], "      ") {
					break
				}
				if !strings.HasPrefix(line, "- ") {
					return nil, errors.New("unsupported AI engine host mapping")
				}
				entry := strings.Trim(strings.TrimSpace(strings.TrimPrefix(line, "- ")), "\"'")
				if strings.HasPrefix(entry, "host.docker.internal:") {
					if entry != "host.docker.internal:host-gateway" {
						return nil, errors.New("existing AI engine host mapping conflicts with host-gateway")
					}
					mapped = true
				}
			}
		}
		if !mapped {
			index := start + 1
			addition := []string{"    extra_hosts:", `      - "host.docker.internal:host-gateway"`}
			if hostsLine >= 0 {
				index = hostsLine + 1
				addition = addition[1:]
			}
			lines = append(lines[:index], append(addition, lines[index:]...)...)
		}
	}
	return []byte(strings.Join(lines, "\n")), nil
}
